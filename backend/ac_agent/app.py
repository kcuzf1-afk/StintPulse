from __future__ import annotations
import asyncio
import csv
import io
import ipaddress
import json
import secrets
import hashlib
import logging
import tempfile
import time
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
import psutil
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import ValidationError
from .analysis import (
    compare,
    corner_sections,
    summary_statistics,
    detect_events,
    coaching,
)
from .database import Database
from .access import AccessGuard, code_matches, is_loopback
from .engine import Engine
from .models import Settings, new_access_code
from .recordings import RecordingError, Recordings
from .relay import OnboardRelay
from . import APP_NAME, __version__, setup_api, updates

MAX_IMPORT = 64 * 1024**2
MAX_VIDEO_CHUNK = 32 * 1024**2
log = logging.getLogger(__name__)


def portable_data_dir():
    """Packaged app with a "Daten" folder next to the EXE: all data stays in
    that folder (portable - the whole folder can be moved or backed up)."""
    import sys

    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent / "Daten"
    return folder if folder.is_dir() else None


def default_data_dir():
    import sys

    override = os.environ.get("AC_AGENT_DATA_DIR")
    if override:
        return Path(override)
    portable = portable_data_dir()
    if portable:
        return portable
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        current = base / APP_NAME
        legacy = base / "AC Engineering Data Agent"  # before the rename to StintPulse
        return legacy if legacy.is_dir() and not current.exists() else current
    return Path.home() / ".local" / "share" / "stintpulse"


def public_settings(settings):
    return {
        **settings.model_dump(exclude={"access_token"}),
        "token_set": bool(settings.access_token),
    }


def allowed_host(host, lan):
    if host in ("localhost", "127.0.0.1", "::1", "testserver"):
        return True
    try:
        addr = ipaddress.ip_address(host or "")
        return lan and addr.is_private and not addr.is_unspecified
    except ValueError:
        return False


def origin_allowed(origin, host, port):
    if not origin:
        return True  # CLI/non-browser clients still require LAN token
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    expected = urlparse("http://" + host)
    # Same origin, or documented local Vite development port.
    return (
        parsed.hostname == expected.hostname
        and (parsed.port or (443 if parsed.scheme == "https" else 80)) == port
    ) or (parsed.hostname in ("127.0.0.1", "localhost") and parsed.port == 5173)


def lap_csv(lap):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    keys = sorted({k for s in lap.samples for k in s.channels})
    writer.writerow(
        [
            "source",
            "lap",
            "lap_ms",
            "lap_pos",
            "x_m",
            "y_m",
            "z_m",
            "synthetic_boundary",
            *keys,
        ]
    )
    for s in lap.samples:
        writer.writerow(
            [
                s.source,
                lap.number,
                s.lap_ms,
                s.lap_pos,
                *s.coords,
                int(s.synthetic_boundary),
                *[s.channels.get(k) for k in keys],
            ]
        )
    return buffer.getvalue()


def create_app(
    data_dir=None,
    source=None,
    start_engine=True,
    web_dir=None,
    port=None,
    audio_factory=None,
    setup_provider=None,
):
    data_root = Path(data_dir or default_data_dir())
    db = Database(data_root / "telemetry.sqlite")
    cfg = db.get_settings()
    if source:
        cfg = cfg.model_copy(update={"source": source})
    if port:
        cfg = Settings.model_validate({**cfg.model_dump(), "port": port})
    engine = Engine(db, cfg)
    # Some container runners virtualize getpid but expose host /proc IDs.
    pid = (
        int(Path("/proc/self/stat").read_text().split()[0])
        if Path("/proc/self/stat").exists()
        else os.getpid()
    )
    process = psutil.Process(pid)
    process.cpu_percent()
    network_bytes = 0
    guard = AccessGuard()
    relay = OnboardRelay()
    recordings = Recordings(db, data_root, lambda: engine.settings, audio_factory)
    engine.lap_hooks.append(recordings.link_lap)
    # Segments left open by a crash or a closed browser stay usable
    # (indexing runs in the video worker thread).
    recordings.recover()

    @asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()

        def quiet_resets(loop, context):
            # Windows Proactor reports every abruptly closed browser connection
            # (phone screen off, reload) as WinError 10054. Harmless: ignore.
            if isinstance(context.get("exception"), ConnectionResetError):
                return
            loop.default_exception_handler(context)

        loop.set_exception_handler(quiet_resets)

        async def video_watchdog():
            while True:
                await asyncio.sleep(5)
                try:
                    await asyncio.to_thread(recordings.idle)
                except Exception as exc:  # keep watching
                    log.warning("Video watchdog: %s", str(exc)[:160])

        watchdog = asyncio.create_task(video_watchdog())
        if start_engine:
            engine.start()
        yield
        watchdog.cancel()
        if start_engine:
            await asyncio.to_thread(engine.stop)
        else:
            engine.analyzer.shutdown(wait=True)
            engine.writer.shutdown(wait=True)
        await asyncio.to_thread(recordings.close)
        db.close()

    app = FastAPI(
        title=APP_NAME,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.engine, app.state.db = engine, db
    app.state.relay, app.state.guard = relay, guard
    app.state.recordings = recordings

    @app.middleware("http")
    async def secure(request: Request, call_next):
        settings = engine.settings
        host = request.url.hostname
        if not allowed_host(host, settings.lan):
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        if request.url.path.startswith("/api"):
            if not origin_allowed(
                request.headers.get("origin"),
                request.headers.get("host", ""),
                request.url.port or settings.port,
            ):
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            if (
                settings.lan
                and request.url.path != "/api/health"
                and not is_loopback(request)
            ):
                provided = request.headers.get("authorization", "").removeprefix(
                    "Bearer "
                )
                if not provided and request.url.path.startswith(("/api/videos/", "/api/audio/")):
                    # A <video src> cannot send headers (access logging is off).
                    provided = request.query_params.get("token", "")
                ip = request.client.host if request.client else ""
                result = guard.check(ip, provided, settings.access_token)
                if result == "locked":
                    return JSONResponse(
                        {
                            "detail": "Too many wrong access codes. Try again in "
                            f"{guard.locked_for(ip):.0f} s"
                        },
                        status_code=429,
                    )
                if result != "ok":
                    return JSONResponse(
                        {"detail": "Access code required"}, status_code=401
                    )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def body_json(request):
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_IMPORT:
                raise HTTPException(413, "Import is limited to 64 MiB")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON")

    def get_lap(lap_id):
        lap = db.lap(lap_id)
        if not lap:
            raise HTTPException(404, "Lap not found")
        return lap

    def get_session(sid):
        session = db.session(sid)
        if not session:
            raise HTTPException(404, "Session not found")
        return session

    @app.get("/api/health")
    async def health():
        return {"ok": True, "name": APP_NAME, "version": __version__, "lan": engine.settings.lan}

    @app.get("/api/version")
    async def version_info():
        return await asyncio.to_thread(updates.check, engine.settings.update_check)

    @app.get("/api/live")
    async def live():
        return engine.payload()

    @app.get("/api/diagnostics")
    async def diagnostics():
        # No token, no memory contents, no personal data beyond what the
        # dashboard already shows.
        return await asyncio.to_thread(engine.diagnostics)

    @app.post("/api/diagnostics/recheck")
    async def diagnostics_recheck():
        engine.recheck()
        # Give the capture loop a moment to run one connection attempt.
        await asyncio.sleep(1.2)
        return await asyncio.to_thread(engine.diagnostics)

    @app.get("/api/performance")
    async def performance():
        return {
            **engine.payload()["performance"],
            "cpu_percent": process.cpu_percent(),
            "ram_mb": process.memory_info().rss / 1024**2,
            "websocket_bytes": network_bytes,
            "database_mb": db.size_mb(),
        }

    @app.get("/api/settings")
    async def settings_get():
        return public_settings(engine.settings)

    def lan_urls():
        urls = []
        for addresses in psutil.net_if_addrs().values():
            for a in addresses:
                try:
                    ip = ipaddress.ip_address(a.address)
                except ValueError:
                    continue
                if ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local:
                    urls.append(f"http://{ip}:{engine.settings.port}")
        return sorted(set(urls))

    @app.get("/api/access-code")
    async def access_code(request: Request):
        # Only shown on the PC itself, never to LAN devices.
        if not is_loopback(request):
            raise HTTPException(403, "The access code is only shown on the PC")
        return {
            "code": engine.settings.access_token,
            "lan": engine.settings.lan,
            "urls": lan_urls(),
        }

    @app.post("/api/access-code")
    async def access_code_reset(request: Request):
        if not is_loopback(request):
            raise HTTPException(403, "The access code can only be changed on the PC")
        settings = engine.settings.model_copy(update={"access_token": new_access_code()})
        db.save_settings(settings)
        engine.settings = settings
        await relay.close_all()  # existing LAN viewers must use the new code
        return {"code": settings.access_token, "lan": settings.lan, "urls": lan_urls()}

    @app.patch("/api/settings")
    async def settings_patch(request: Request):
        payload = await body_json(request)
        try:
            settings = Settings.model_validate(
                {**engine.settings.model_dump(), **payload}
            )
        except ValidationError as exc:
            raise HTTPException(
                422,
                [
                    {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                    for e in exc.errors(include_input=False)
                ],
            )
        except TypeError:
            raise HTTPException(422, "Settings must be a JSON object")
        if settings.video_url:
            parsed = urlparse(settings.video_url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise HTTPException(
                    422, "Video URL must be http(s) without embedded credentials"
                )
        pc_only = ("ai_provider", "ai_model", "ai_consent", "ac_install_dir", "ac_setups_dir")
        if any(getattr(settings, f) != getattr(engine.settings, f) for f in pc_only):
            if not is_loopback(request):
                raise HTTPException(
                    403, "KI-Anbieter und AC-Ordner können nur am PC selbst geändert werden"
                )
            for folder in (settings.ac_install_dir, settings.ac_setups_dir):
                if folder and not Path(folder).is_absolute():
                    raise HTTPException(422, "AC-Ordner müssen absolute Pfade sein")
        if settings.record_dir != engine.settings.record_dir:
            if not is_loopback(request):
                raise HTTPException(
                    403, "The video folder can only be changed on the PC itself"
                )
            if settings.record_dir:
                folder = Path(settings.record_dir)
                if not folder.is_absolute():
                    raise HTTPException(422, "Video folder must be an absolute path")
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    probe = folder / ".ac-agent-write-test"
                    probe.write_bytes(b"ok")
                    probe.unlink()
                except OSError as exc:
                    raise HTTPException(422, f"Video folder is not writable: {exc}")
        changed_network = (settings.lan, settings.port) != (
            engine.settings.lan,
            engine.settings.port,
        )
        if settings.start_with_windows != engine.settings.start_with_windows:
            from .windows import set_startup

            try:
                set_startup(settings.start_with_windows)
            except (OSError, ValueError) as exc:
                raise HTTPException(422, str(exc))
        db.save_settings(settings)
        engine.settings = settings
        engine.update_reference()
        return {
            "settings": public_settings(settings),
            "restart_required": changed_network,
        }

    @app.get("/api/map")
    async def map_get():
        return engine.map_payload()

    @app.get("/api/sessions")
    async def sessions(q: str = "", source: str | None = None):
        return await asyncio.to_thread(db.list_sessions, q[:200], source)

    @app.get("/api/sessions/{sid}")
    async def session(sid: str):
        s = await asyncio.to_thread(get_session, sid)
        stats = summary_statistics(s["laps"])
        video = await asyncio.to_thread(recordings.lap_status, sid)
        recs = await asyncio.to_thread(recordings.for_session, sid)
        for lap in s["laps"]:
            lap["video"] = video.get(
                lap["id"], {"coverage": "none", "recording_id": None, "covered_ratio": 0}
            )
        return {
            **s,
            "statistics": stats,
            "recordings": [recordings.public(r) for r in recs],
        }

    @app.patch("/api/sessions/{sid}")
    async def favorite(sid: str, request: Request):
        get_session(sid)
        value = await body_json(request)
        if set(value) != {"favorite"} or not isinstance(value["favorite"], bool):
            raise HTTPException(422, "Expected {favorite: boolean}")
        db.favorite(sid, value["favorite"])
        return {"ok": True}

    @app.delete("/api/sessions/{sid}")
    async def delete(sid: str):
        if sid == engine.session_id:
            raise HTTPException(409, "Cannot delete the active session")
        if sid in engine.inflight_sessions:
            raise HTTPException(
                409, "Session is still being analyzed/saved; try again shortly"
            )
        s = get_session(sid)
        if any(l["id"] == engine.settings.reference_lap_id for l in s["laps"]):
            raise HTTPException(
                409, "Select another reference before deleting this session"
            )
        try:
            # Deleting a session explicitly also deletes its onboard videos.
            await asyncio.to_thread(recordings.delete_session, sid)
        except RecordingError as exc:
            raise HTTPException(exc.status, str(exc))
        db.delete_session(sid)
        engine.update_reference()
        return {"ok": True}

    setup_api.register(app, db, engine, data_root, setup_provider)

    # ---------- onboard recording ----------

    def require_pc(request, what="Recording"):
        if not is_loopback(request):
            raise HTTPException(403, f"{what} is only possible on the PC itself")

    async def recording_call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except RecordingError as exc:
            raise HTTPException(exc.status, str(exc))

    @app.get("/api/recordings/storage")
    async def recordings_storage():
        return await asyncio.to_thread(recordings.usage)

    @app.get("/api/recordings")
    async def recordings_list(session_id: str = ""):
        rows = await asyncio.to_thread(
            recordings.for_session if session_id else (lambda _: recordings._rows()),
            session_id,
        )
        return [recordings.public(r) for r in rows]

    @app.post("/api/recordings")
    async def recording_create(request: Request):
        require_pc(request)
        payload = await body_json(request)
        if not isinstance(payload, dict):
            raise HTTPException(422, "Expected a JSON object")
        sid = payload.get("session_id")
        if not sid or sid != engine.session_id:
            raise HTTPException(409, "This session is not running (anymore)")
        info = {}
        for key in ("bitrate", "width", "height", "fps", "clock_offset_ms"):
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                info[key] = value
        if payload.get("quality") in ("saver", "standard", "high"):
            info["quality"] = payload["quality"]
        return await recording_call(
            recordings.create,
            sid,
            str(payload.get("client_id", ""))[:64],
            str(payload.get("mime", ""))[:100],
            info,
        )

    @app.post("/api/recordings/{rid}/chunks/{seq}")
    async def recording_chunk(
        rid: str,
        seq: int,
        request: Request,
        started_at: float | None = None,
        clock_offset_ms: float | None = None,
    ):
        require_pc(request)
        parts, size = [], 0
        async for part in request.stream():
            size += len(part)
            if size > MAX_VIDEO_CHUNK:
                raise HTTPException(413, "Video chunk too large")
            parts.append(part)
        return await recording_call(
            recordings.append, rid, seq, b"".join(parts), started_at, clock_offset_ms
        )

    @app.post("/api/recordings/{rid}/finish")
    async def recording_finish(rid: str, request: Request):
        require_pc(request)
        raw = await request.body()  # also sent by navigator.sendBeacon
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        duration = payload.get("duration_ms")
        return await recording_call(
            recordings.finish,
            rid,
            str(payload.get("reason") or "stopped"),
            duration if isinstance(duration, (int, float)) else None,
        )

    @app.get("/api/recordings/{rid}")
    async def recording_get(rid: str):
        r = await asyncio.to_thread(recordings.get, rid)
        if not r:
            raise HTTPException(404, "Recording not found")
        return recordings.public(r)

    @app.delete("/api/recordings/{rid}")
    async def recording_delete(rid: str):
        if not await asyncio.to_thread(recordings.get, rid):
            raise HTTPException(404, "Recording not found")
        await recording_call(recordings.delete, rid)
        return {"ok": True}

    @app.delete("/api/sessions/{sid}/videos")
    async def session_videos_delete(sid: str):
        get_session(sid)
        if sid == engine.session_id:
            raise HTTPException(409, "Cannot delete videos of the running session")
        await recording_call(recordings.delete_session, sid)
        return {"ok": True}

    @app.get("/api/videos/{rid}")
    async def video_file(rid: str):
        r = await asyncio.to_thread(recordings.get, rid)
        if not r or r["status"] != "ready":
            raise HTTPException(404, "Video not available")
        path = Path(r["path"])
        if not path.is_file():
            raise HTTPException(404, "Video file is missing")
        # Starlette answers Range requests (206) for seeking in long files.
        return FileResponse(path, media_type=r["mime"].split(";")[0])

    @app.get("/api/audio/{rid}")
    async def audio_file(rid: str):
        r = await asyncio.to_thread(recordings.get, rid)
        sound = (r or {}).get("info", {}).get("audio") or {}
        if not r or r["status"] != "ready" or sound.get("status") != "ready":
            raise HTTPException(404, "No sound for this video")
        path = Path(r["path"]).with_suffix(".wav")
        if not path.is_file():
            raise HTTPException(404, "Sound file is missing")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/laps/{lap_id}/video")
    async def lap_video(lap_id: str):
        lap = await asyncio.to_thread(get_lap, lap_id)
        return await asyncio.to_thread(recordings.playback, lap)

    @app.get("/api/laps/{lap_id}")
    async def lap_detail(lap_id: str):
        data = await asyncio.to_thread(lambda: get_lap(lap_id).model_dump_json())
        return Response(data, media_type="application/json")

    @app.get("/api/compare")
    async def comparison(lap_ids: str, reference: str):
        ids = list(dict.fromkeys(lap_ids.split(",")))
        if not 1 <= len(ids) <= 4:
            raise HTTPException(422, "Compare one to four laps")
        ref = await asyncio.to_thread(get_lap, reference)
        sm = (await asyncio.to_thread(get_session, ref.session_id))["meta"]
        if sm["track_length"] <= 0:
            raise HTTPException(409, "Track spline length is not available")
        output = []
        for lap_id in ids:
            lap = await asyncio.to_thread(get_lap, lap_id)
            lm = (await asyncio.to_thread(get_session, lap.session_id))["meta"]
            if any(lm[k] != sm[k] for k in ("source", "car", "track", "layout")):
                raise HTTPException(
                    422, "Select laps from the same source, car, track and layout"
                )
            try:
                if not lap.events:
                    lap.events = await asyncio.to_thread(
                        detect_events, lap, sm["track_length"], engine.settings
                    )
                result = await asyncio.to_thread(compare, lap, ref, sm["track_length"])
                result["corners"] = await asyncio.to_thread(
                    corner_sections, lap, ref, sm["track_length"], engine.settings
                )
                result["events"] = lap.events
                result["tips"] = await asyncio.to_thread(
                    coaching, lap, ref, sm["track_length"], engine.settings
                )
                output.append(result)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        data = await asyncio.to_thread(
            json.dumps, output, separators=(",", ":"), allow_nan=False
        )
        return Response(data, media_type="application/json")

    @app.get("/api/laps/{lap_id}/export")
    async def export_lap(lap_id: str, format: str = "csv"):
        lap = await asyncio.to_thread(get_lap, lap_id)
        filename = "lap-" + str(lap.number)
        if format == "csv":
            return Response(
                await asyncio.to_thread(lap_csv, lap),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}.csv"'
                },
            )
        if format == "json":
            return Response(
                await asyncio.to_thread(lap.model_dump_json),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}.json"'
                },
            )
        if format == "parquet":
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError:
                raise HTTPException(
                    501, "Install optional dependency: pip install .[parquet]"
                )
            rows = [
                {
                    "source": s.source,
                    "lap_ms": s.lap_ms,
                    "lap_pos": s.lap_pos,
                    **s.channels,
                }
                for s in lap.samples
            ]
            buffer = io.BytesIO()
            pq.write_table(pa.Table.from_pylist(rows), buffer)
            return Response(
                buffer.getvalue(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}.parquet"'
                },
            )
        raise HTTPException(422, "Choose csv, json or parquet")

    @app.get("/api/sessions/{sid}/export")
    async def export_session(sid: str):
        get_session(sid)
        return JSONResponse(
            await asyncio.to_thread(db.export_session, sid),
            headers={"Content-Disposition": 'attachment; filename="ac-session.json"'},
        )

    @app.post("/api/import")
    async def import_session(request: Request):
        payload = await body_json(request)
        try:
            sid = await asyncio.to_thread(db.import_session, payload)
            return {"id": sid}
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)[:300])

    @app.get("/api/backup")
    async def backup():
        handle, path = tempfile.mkstemp(suffix=".sqlite", prefix="ac-agent-backup-")
        os.close(handle)
        try:
            await asyncio.to_thread(db.backup_to, path)
            return FileResponse(
                path,
                media_type="application/octet-stream",
                filename="stintpulse-backup.sqlite",
                background=BackgroundTask(os.unlink, path),
            )
        except Exception:
            os.unlink(path)
            raise

    @app.post("/api/restore")
    async def restore(request: Request):
        if request.headers.get("content-type", "").startswith(
            "application/octet-stream"
        ):
            handle, path = tempfile.mkstemp(
                suffix=".sqlite", prefix="ac-agent-restore-"
            )
            try:
                with os.fdopen(handle, "wb") as file:
                    size = 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > 2 * 1024**3:
                            raise HTTPException(
                                413, "SQLite restore is limited to 2 GiB"
                            )
                        file.write(chunk)
                ids = await asyncio.to_thread(db.restore_sqlite, path)
                engine.update_reference()
                return {"ids": ids}
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, str(exc)[:300])
            finally:
                os.unlink(path)
        payload = await body_json(request)
        try:
            if (
                payload["format"] != "ac-agent-backup"
                or payload["version"] != 1
                or len(payload["sessions"]) > 500
            ):
                raise ValueError("Invalid backup")
            for session in payload["sessions"]:
                db.validate_archive(session)
            ids = [db.import_session(s) for s in payload["sessions"]]
            return {"ids": ids}
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)[:300])

    async def ws_accept(ws: WebSocket):
        """Host/origin/access-code check; returns the code used or None (closed)."""
        cfg = engine.settings
        if not allowed_host(ws.url.hostname, cfg.lan) or not origin_allowed(
            ws.headers.get("origin"),
            ws.headers.get("host", ""),
            ws.url.port or cfg.port,
        ):
            await ws.close(code=1008)
            return None
        protocols = [
            v.strip() for v in ws.headers.get("sec-websocket-protocol", "").split(",")
        ]
        token = protocols[1] if len(protocols) > 1 else ""
        if cfg.lan and not is_loopback(ws):
            ip = ws.client.host if ws.client else ""
            if guard.check(ip, token, cfg.access_token) != "ok":
                await ws.close(code=1008)
                return None
        await ws.accept(subprotocol="ac-agent" if "ac-agent" in protocols else None)
        return token

    def still_authorized(ws, token):
        cfg = engine.settings
        return not cfg.lan or is_loopback(ws) or code_matches(token, cfg.access_token)

    @app.websocket("/ws/onboard")
    async def onboard_signaling(ws: WebSocket):
        role = "sender" if ws.query_params.get("role") == "sender" else "viewer"
        # Only the PC itself may publish its camera; LAN devices only watch.
        if role == "sender" and not is_loopback(ws):
            await ws.close(code=1008)
            return
        token = await ws_accept(ws)
        if token is None:
            return
        client_id = await relay.join(ws, role)
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if not still_authorized(ws, token):
                    await ws.close(code=1008)
                    return
                if message.get("bytes") is not None:
                    if role == "sender":  # JPEG fallback frame
                        await relay.frame(client_id, message["bytes"])
                elif message.get("text") is not None:
                    await relay.handle(client_id, role, message["text"])
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            await relay.leave(client_id, role)

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        nonlocal network_bytes
        token = await ws_accept(ws)
        if token is None:
            return
        engine.ws_clients += 1
        try:
            while True:
                if not still_authorized(ws, token):
                    await ws.close(code=1008)
                    return
                data = json.dumps(
                    {**engine.payload(), "build": build_id},
                    separators=(",", ":"),
                    allow_nan=False,
                )
                await asyncio.wait_for(ws.send_text(data), timeout=2)
                network_bytes += len(data.encode())
                engine.ws_last_send = time.monotonic()
                await asyncio.sleep(1 / engine.settings.broadcast_hz)
        except (WebSocketDisconnect, RuntimeError, TimeoutError):
            return
        finally:
            engine.ws_clients -= 1

    directory = (
        Path(web_dir) if web_dir else Path(__file__).parents[2] / "frontend" / "dist"
    )
    index = directory / "index.html"
    # Identifies the dashboard bundle; open tabs reload once when it changes.
    build_id = (
        hashlib.sha256(index.read_bytes()).hexdigest()[:12] if index.is_file() else ""
    )
    if directory.is_dir():
        app.mount("/", StaticFiles(directory=directory, html=True), name="dashboard")
    else:

        @app.get("/")
        async def missing_frontend():
            return {
                "message": "Run npm ci && npm run build in frontend, or use Vite at http://localhost:5173"
            }

    return app
