"""Onboard video recordings: storage, limits and lap linkage.

The PC browser that shows the OBS camera records it with MediaRecorder and
sends the WebM in small chunks. Each chunk is appended to a ``.part`` file at
once, so a long session never sits in memory. When a segment ends, the file
is indexed (``webm.finalize``) so it can be seeked; frames are not re-encoded.

Time base (one clock for video and telemetry): every recording stores
``started_at`` = server wall clock (``time.time()``) of video time 0. The
browser measures the recorder start on its own clock and converts it with an
explicitly measured offset (``clock_offset_ms``). Telemetry samples carry
``captured_at`` on the same server clock, therefore

    video_s = captured_at - started_at + video_offset_s

``video_offset_s`` (setting, > 0) = the picture lags behind telemetry, e.g.
OBS/virtual-camera latency. It is applied at playback with its current value.

Sound (audio.py) is captured by this app from Windows during each segment and
stored as a WAV next to the video, on the same server clock: the sound that
belongs to video position v is at  started_at + v - offset - audio.started_at.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio as audio_capture
from . import webm
from .laps import lap_wall_window, utc_now

log = logging.getLogger(__name__)

MB = 1024**2
DISK_RESERVE = 512 * MB  # always leave this much free space on the drive
IDLE_TIMEOUT_S = 45  # no chunk for this long: the browser is gone
SINGLE_WRITER_S = 20  # another tab's recording counts as active this long
FULL_TOLERANCE_S = 0.05
ACTIVE = ("recording", "finalizing")
PUBLIC = (
    "id",
    "session_id",
    "segment",
    "status",
    "mime",
    "created_at",
    "started_at",
    "ended_at",
    "duration_ms",
    "bytes",
    "chunks",
    "indexed",
    "end_reason",
    "error",
)


class RecordingError(Exception):
    status = 409

    def __init__(self, message, status=None):
        super().__init__(message)
        if status:
            self.status = status


class StorageFull(RecordingError):
    status = 507


def coverage(window, recs, offset_s, now=None):
    """Which recording shows a lap, and how much of it.

    ``window`` = (start, end) of the lap on the server wall clock. Returns a
    dict with coverage full | partial | none | pending and the lap's position
    on that recording's video timeline. Gaps are never reported as full.
    """
    now = time.time() if now is None else now
    start, end = window
    length = max(end - start, 1e-3)
    best, best_overlap, pending = None, 0.0, None
    for r in recs:
        if r["status"] == "failed":
            continue
        if r["status"] in ACTIVE:
            origin = (r["started_at"] or r["created_at_ts"]) - offset_s
            if end > origin and start < now + 1:
                pending = pending or r
            continue
        if r["started_at"] is None or not r["duration_ms"]:
            continue
        v0 = r["started_at"] - offset_s  # telemetry clock of video time 0
        v1 = v0 + r["duration_ms"] / 1000
        overlap = max(0.0, min(end, v1) - max(start, v0))
        if overlap > best_overlap:
            best, best_overlap = r, overlap
    if pending:
        return {"coverage": "pending", "recording_id": pending["id"], "covered_ratio": None}
    if not best:
        return {"coverage": "none", "recording_id": None, "covered_ratio": 0.0}
    v_from = start - best["started_at"] + offset_s
    v_to = end - best["started_at"] + offset_s
    full = (
        v_from >= -FULL_TOLERANCE_S
        and v_to <= best["duration_ms"] / 1000 + FULL_TOLERANCE_S
    )
    return {
        "coverage": "full" if full else "partial",
        "recording_id": best["id"],
        "covered_ratio": 1.0 if full else round(min(1.0, best_overlap / length), 4),
        "video_from_s": v_from,
        "video_to_s": v_to,
    }


def free_bytes(folder: Path):
    """Free space on the drive of ``folder`` (or its nearest existing parent)."""
    for candidate in (folder, *folder.parents):
        try:
            if candidate.exists():
                return shutil.disk_usage(candidate).free
        except OSError:
            return None
    return None


def audio_path(r) -> Path:
    return Path(r["path"]).with_suffix(".wav")


AUDIO_PUBLIC = ("status", "mode", "started_at", "duration_ms", "sample_rate", "channels", "reason", "error", "interrupted")


class Recordings:
    def __init__(self, db, data_dir, settings, audio_factory=None):
        self.db = db
        # Running sound captures per recording id (see audio.py).
        self.audio: dict[str, object] = {}
        self.audio_factory = audio_factory or audio_capture.open_capture
        self.default_dir = Path(data_dir) / "videos"
        self.settings = settings  # callable -> current Settings
        self.lock = threading.RLock()
        self.file_locks: dict[str, threading.Lock] = {}
        # Indexing copies the whole file: one segment at a time, off the
        # request and capture threads.
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ac-video")

    def flush(self):
        """Wait until every scheduled finalization has run (tests, shutdown)."""
        self.worker.submit(lambda: None).result()

    def close(self):
        # Sound files must get a valid header even when the app quits mid-drive.
        for rid in list(self.audio):
            with self._file_lock(rid):
                r = self.get(rid)
                if r:
                    info = r["info"]
                    info["audio"] = self._stop_audio(rid, info.get("audio"), r)
                    self._update(rid, info=info)
        self.worker.shutdown(wait=True)

    # ---------- paths and rows ----------

    def video_dir(self) -> Path:
        custom = self.settings().record_dir
        return Path(custom) if custom else self.default_dir

    def _rows(self, where="1=1", args=()):
        with self.db.lock:
            rows = self.db.conn.execute(
                f"SELECT * FROM recordings WHERE {where} ORDER BY created_at, segment", args
            ).fetchall()
        out = []
        for row in rows:
            r = dict(row)
            r["info"] = json.loads(r.pop("info_json") or "{}")
            r["indexed"] = bool(r["indexed"])
            r["created_at_ts"] = r["info"].get("created_ts") or time.time()
            self._relocate(r)
            out.append(r)
        return out

    def _relocate(self, r):
        """Find videos again after the data folder was moved (portable
        folder, another drive): same file below the current video folder."""
        stored = Path(r["path"])
        if stored.exists() or Path(r["path"] + ".part").exists():
            return
        for base in dict.fromkeys((self.video_dir(), self.default_dir)):
            candidate = base / stored.parent.name / stored.name
            if candidate.exists() or Path(str(candidate) + ".part").exists():
                r["path"] = str(candidate)
                with self.db.lock, self.db.conn:
                    self.db.conn.execute(
                        "UPDATE recordings SET path=? WHERE id=?", (r["path"], r["id"])
                    )
                log.info("Video found in the moved data folder")
                return

    def get(self, rid):
        rows = self._rows("id=?", (rid,))
        return rows[0] if rows else None

    def public(self, r):
        out = {k: r[k] for k in PUBLIC}
        info = r["info"]
        out.update(
            {
                "width": info.get("width"),
                "height": info.get("height"),
                "codec": info.get("codec"),
                "quality": info.get("quality"),
                "truncated": info.get("truncated", False),
                "clock_offset_ms": info.get("clock_offset_ms"),
                "offset_s_at_recording": info.get("offset_s"),
                "audio": self._audio_public(r),
            }
        )
        return out

    def _audio_public(self, r):
        cap = self.audio.get(r["id"])
        if cap:
            return {**(r["info"].get("audio") or {}), **cap.status()}
        data = r["info"].get("audio") or {"status": "off"}
        return {k: data[k] for k in AUDIO_PUBLIC if k in data}

    def for_session(self, sid):
        return self._rows("session_id=?", (sid,))

    def _update(self, rid, **fields):
        if "info" in fields:
            fields["info_json"] = json.dumps(fields.pop("info"))
        if "indexed" in fields:
            fields["indexed"] = int(fields["indexed"])
        keys = ", ".join(f"{k}=?" for k in fields)
        with self.db.lock, self.db.conn:
            self.db.conn.execute(
                f"UPDATE recordings SET {keys} WHERE id=?", (*fields.values(), rid)
            )

    def _file_lock(self, rid):
        with self.lock:
            return self.file_locks.setdefault(rid, threading.Lock())

    # ---------- storage limit ----------

    def used_bytes(self):
        with self.db.lock:
            stored = self.db.conn.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM recordings"
            ).fetchone()[0]
        # Sound of running segments grows outside the database.
        return stored + sum(cap.bytes for cap in list(self.audio.values()))

    def usage(self):
        cfg = self.settings()
        folder = self.video_dir()
        free = free_bytes(folder)
        with self.db.lock:
            count = self.db.conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
        return {
            "dir": str(folder),
            "default_dir": str(self.default_dir),
            "used_mb": round(self.used_bytes() / MB, 1),
            "limit_mb": cfg.record_storage_mb,
            "free_mb": None if free is None else round(free / MB),
            "count": count,
            "delete_oldest": cfg.record_delete_oldest,
        }

    def _ensure_space(self, incoming, active_session):
        cfg = self.settings()
        limit = cfg.record_storage_mb * MB
        used = self.used_bytes()
        if used + incoming > limit:
            if not cfg.record_delete_oldest:
                raise StorageFull(
                    "Video storage limit reached ("
                    f"{cfg.record_storage_mb} MB). Raise the limit or delete videos.",
                )
            # Explicitly enabled retention rule: oldest finished videos first,
            # never the running session or favourite sessions.
            with self.db.lock:
                candidates = self.db.conn.execute(
                    "SELECT r.id,r.bytes FROM recordings r JOIN sessions s ON s.id=r.session_id "
                    "WHERE r.status NOT IN ('recording','finalizing') AND r.session_id!=? "
                    "AND s.favorite=0 ORDER BY r.created_at",
                    (active_session,),
                ).fetchall()
            for rid, size in candidates:
                if used + incoming <= limit:
                    break
                self.delete(rid)
                used -= size
                log.info("Deleted an old video (retention rule)")
            if used + incoming > limit:
                raise StorageFull("Video storage limit reached; nothing left to delete.")
        free = free_bytes(self.video_dir())
        if free is None:
            raise RecordingError("Video folder not available", 500)
        if free - incoming < DISK_RESERVE:
            raise StorageFull("Not enough free disk space for the video.")

    # ---------- recording lifecycle ----------

    def create(self, session_id, client_id, mime, info):
        if not mime.startswith(("video/webm", "video/mp4")):
            raise RecordingError("Unsupported recording format", 422)
        now = time.time()
        with self.lock:
            for r in self._rows("status='recording'"):
                last = r["info"].get("last_chunk_ts") or r["created_at_ts"]
                if now - last > SINGLE_WRITER_S:
                    continue  # stale: the watchdog finishes it
                if r["info"].get("client_id") != client_id:
                    raise RecordingError(
                        "Another browser tab is already recording the onboard video."
                    )
                self.finish(r["id"], "superseded")  # same tab, new segment
            self._ensure_space(0, session_id)
            folder = self.video_dir() / session_id[:8]
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RecordingError(f"Video folder not available: {exc}", 500)
            with self.db.lock:
                segment = (
                    self.db.conn.execute(
                        "SELECT COALESCE(MAX(segment),0) FROM recordings WHERE session_id=?",
                        (session_id,),
                    ).fetchone()[0]
                    + 1
                )
            rid = str(uuid.uuid4())
            ext = ".mp4" if mime.startswith("video/mp4") else ".webm"
            path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{segment:02d}-{rid[:8]}{ext}"
            path.with_name(path.name + ".part").touch()
            meta = {
                **info,
                "client_id": client_id,
                "created_ts": now,
                "offset_s": self.settings().video_offset_s,
            }
            with self.db.lock, self.db.conn:
                self.db.conn.execute(
                    "INSERT INTO recordings(id,session_id,segment,status,mime,path,created_at,info_json) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (rid, session_id, segment, "recording", mime, str(path), utc_now(), json.dumps(meta)),
                )
            meta["audio"] = self._start_audio(rid, path)
            self._update(rid, info=meta)
        return {
            "id": rid,
            "segment": segment,
            "server_time": time.time(),
            "audio": {k: v for k, v in meta["audio"].items() if k in AUDIO_PUBLIC},
        }

    # ---------- sound ----------

    def _start_audio(self, rid, path):
        mode = self.settings().record_audio
        if mode == "off":
            return {"status": "off", "mode": "off"}
        part = Path(str(path.with_suffix(".wav")) + ".part")
        try:
            cap = self.audio_factory(part, mode)
            cap.start()
        except audio_capture.AudioUnavailable as exc:
            return {"status": "none", "mode": mode, "reason": str(exc)[:200]}
        except audio_capture.AudioError as exc:
            log.warning("Sound recording not started: %s", str(exc)[:160])
            return {"status": "failed", "mode": mode, "error": str(exc)[:200]}
        self.audio[rid] = cap
        return {"status": "recording", "mode": cap.mode, "started_at": cap.timeline.started_at}

    def _stop_audio(self, rid, previous, r):
        """Close the sound of a segment; repairs a file left by a crash."""
        previous = previous or {"status": "off"}
        final = audio_path(r)
        part = Path(str(final) + ".part")
        cap = self.audio.pop(rid, None)
        if cap:
            summary = cap.stop(until=time.time())
            if summary["status"] == "ready" and part.exists():
                part.replace(final)
            else:
                part.unlink(missing_ok=True)
            summary.pop("frames", None)
            return {**previous, **summary, "mode": previous.get("mode", summary["mode"])}
        if previous.get("status") == "recording" and part.exists():
            # The app ended during the drive: keep what was written.
            try:
                rate, channels, frames = audio_capture.repair_wav(part)
                part.replace(final)
                return {
                    **previous,
                    "status": "ready" if frames else "failed",
                    "sample_rate": rate,
                    "channels": channels,
                    "duration_ms": round(frames / rate * 1000),
                    "bytes": final.stat().st_size,
                    "interrupted": True,
                }
            except (OSError, audio_capture.AudioError) as exc:
                return {**previous, "status": "failed", "error": str(exc)[:200]}
        if previous.get("status") == "recording":
            return {**previous, "status": "failed", "error": "sound file missing"}
        return previous

    def append(self, rid, seq, data: bytes, started_at=None, clock_offset_ms=None):
        with self._file_lock(rid):
            r = self.get(rid)
            if not r:
                raise RecordingError("Recording not found", 404)
            if r["status"] != "recording":
                raise RecordingError("Recording is already finished")
            if seq < r["chunks"]:
                return {"chunks": r["chunks"], "bytes": r["bytes"], "duplicate": True}
            if seq > r["chunks"]:
                raise RecordingError(
                    f"Missing video chunk {r['chunks']} (got {seq}); start a new segment"
                )
            self._ensure_space(len(data), r["session_id"])
            part = Path(r["path"] + ".part")
            try:
                with open(part, "ab") as f:
                    f.write(data)
                    f.flush()
            except OSError as exc:
                if getattr(exc, "errno", None) == 28:
                    raise StorageFull("Disk full while saving the video.")
                raise RecordingError(f"Video could not be written: {exc}", 500)
            info = r["info"]
            now = time.time()
            info["last_chunk_ts"] = now
            fields = {"bytes": r["bytes"] + len(data), "chunks": seq + 1, "info": info}
            if seq == 0:
                # Video time 0 on the server clock; implausible values are
                # replaced by the time the first chunk arrived (documented).
                plausible = started_at is not None and abs(started_at - now) < 600
                fields["started_at"] = started_at if plausible else now
                if not plausible:
                    info["start_estimated"] = True
                if clock_offset_ms is not None and abs(clock_offset_ms) < 600_000:
                    info["clock_offset_ms"] = round(clock_offset_ms, 2)
            self._update(rid, **fields)
            return {"chunks": seq + 1, "bytes": fields["bytes"], "audio": self._audio_public({**r, "info": info})}

    def finish(self, rid, reason, duration_ms=None):
        with self._file_lock(rid):
            r = self.get(rid)
            if not r:
                raise RecordingError("Recording not found", 404)
            if r["status"] != "recording":
                return self.public(r)
            info = r["info"]
            if duration_ms:
                info["client_duration_ms"] = int(duration_ms)
            info["audio"] = self._stop_audio(rid, info.get("audio"), r)
            self._update(
                rid,
                status="finalizing",
                ended_at=time.time(),
                end_reason=str(reason)[:40],
                info=info,
            )
        self.worker.submit(self.finalize, rid)
        return self.public(self.get(rid))

    def finalize(self, rid):
        """Index the finished segment; runs in a worker thread."""
        r = self.get(rid)
        if not r or r["status"] != "finalizing":
            return
        final = Path(r["path"])
        part = Path(r["path"] + ".part")
        info = r["info"]
        try:
            if not part.exists() or part.stat().st_size == 0:
                raise webm.WebmError("No video data received")
            if r["mime"].startswith("video/webm"):
                size = part.stat().st_size
                free = free_bytes(final.parent) or 0
                if free - size > DISK_RESERVE:
                    tmp = final.with_name(final.name + ".tmp")
                    summary = webm.finalize(part, tmp)
                    webm.verify(tmp, summary)
                    tmp.replace(final)
                    part.unlink()
                    indexed = True
                else:
                    # Too little space for an indexed copy: keep the raw file.
                    # It plays from the start; seeking is slower.
                    part.replace(final)
                    summary = webm.inspect(final)
                    indexed = False
                    info["not_indexed_reason"] = "disk_space"
                info.update({k: summary[k] for k in ("codec", "width", "height", "keyframes", "cues", "frames", "truncated")})
                duration = summary["duration_ms"]
            else:
                part.replace(final)
                duration = info.get("client_duration_ms") or 0
                indexed = False
            sound = audio_path(r)
            self._update(
                rid,
                status="ready",
                duration_ms=duration,
                bytes=final.stat().st_size + (sound.stat().st_size if sound.exists() else 0),
                indexed=indexed,
                info=info,
            )
        except (webm.WebmError, OSError) as exc:
            log.warning("Video segment could not be finalized: %s", str(exc)[:160])
            self._update(rid, status="failed", error=str(exc)[:300])
        self.link_session(r["session_id"])

    def recover(self):
        """After a restart: segments still open were interrupted."""
        for r in self._rows("status IN ('recording','finalizing')"):
            if r["status"] == "recording":
                self.finish(r["id"], "interrupted")
            else:
                self.worker.submit(self.finalize, r["id"])

    def idle(self, now=None):
        """Recording segments whose browser stopped sending (tab closed)."""
        now = time.time() if now is None else now
        out = []
        for r in self._rows("status='recording'"):
            last = r["info"].get("last_chunk_ts") or r["created_at_ts"]
            if now - last > IDLE_TIMEOUT_S:
                self.finish(r["id"], "interrupted")
                out.append(r["id"])
        return out

    def delete(self, rid):
        r = self.get(rid)
        if not r:
            return
        if r["status"] in ACTIVE:
            raise RecordingError("The video is still being recorded or saved")
        sound = audio_path(r)
        for p in (
            Path(r["path"]),
            Path(r["path"] + ".part"),
            Path(r["path"] + ".tmp"),
            sound,
            Path(str(sound) + ".part"),
        ):
            try:
                p.unlink(missing_ok=True)
            except OSError as exc:
                raise RecordingError(f"Video file could not be deleted: {exc}", 500)
        with self.db.lock, self.db.conn:
            self.db.conn.execute("DELETE FROM recordings WHERE id=?", (rid,))
            self.db.conn.execute(
                "UPDATE lap_videos SET recording_id=NULL, coverage='none' WHERE recording_id=?",
                (rid,),
            )
        self.link_session(r["session_id"])

    def delete_session(self, sid):
        for r in self.for_session(sid):
            self.delete(r["id"])
        with self.db.lock, self.db.conn:
            self.db.conn.execute("DELETE FROM lap_videos WHERE session_id=?", (sid,))

    # ---------- lap linkage ----------

    def _store_link(self, lap_id, sid, window, cov, offset):
        with self.db.lock, self.db.conn:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO lap_videos(lap_id,session_id,recording_id,coverage,lap_start,lap_end,"
                "video_start_ms,video_end_ms,covered_ratio,offset_s,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    lap_id,
                    sid,
                    cov["recording_id"],
                    cov["coverage"],
                    window[0],
                    window[1],
                    None if "video_from_s" not in cov else round(cov["video_from_s"] * 1000),
                    None if "video_to_s" not in cov else round(cov["video_to_s"] * 1000),
                    cov["covered_ratio"],
                    offset,
                    utc_now(),
                ),
            )

    def link_lap(self, lap):
        """Called after a lap was saved (writer thread)."""
        try:
            recs = self.for_session(lap.session_id)
            window = lap.analysis.get("wall_window") or lap_wall_window(lap)
            if not recs or not window:
                return
            offset = self.settings().video_offset_s
            self._store_link(lap.id, lap.session_id, window, coverage(window, recs, offset), offset)
        except Exception as exc:  # never break lap saving
            log.warning("Lap/video link failed: %s", str(exc)[:160])

    def link_session(self, sid):
        recs = self.for_session(sid)
        if not recs:
            return
        offset = self.settings().video_offset_s
        for lap in self.db.list_laps(sid):
            window = (lap.get("analysis") or {}).get("wall_window")
            if window:
                self._store_link(lap["id"], sid, window, coverage(window, recs, offset), offset)

    def lap_status(self, sid):
        """Per lap: coverage for the session view (current offset applied)."""
        recs = self.for_session(sid)
        if not recs:
            return {}
        offset = self.settings().video_offset_s
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT * FROM lap_videos WHERE session_id=?", (sid,)
            ).fetchall()
        out = {}
        for row in rows:
            cov = coverage((row["lap_start"], row["lap_end"]), recs, offset)
            if cov["coverage"] != row["coverage"] or row["offset_s"] != offset:
                self._store_link(row["lap_id"], sid, (row["lap_start"], row["lap_end"]), cov, offset)
            rec = next((r for r in recs if r["id"] == cov["recording_id"]), None)
            out[row["lap_id"]] = {
                "coverage": cov["coverage"],
                "recording_id": cov["recording_id"],
                "covered_ratio": cov["covered_ratio"],
                "audio": bool(rec and (rec["info"].get("audio") or {}).get("status") == "ready"),
            }
        return out

    def playback(self, lap):
        """Everything the player needs for one lap (no live data)."""
        recs = self.for_session(lap.session_id)
        window = lap.analysis.get("wall_window") or lap_wall_window(lap)
        offset = self.settings().video_offset_s
        base = {
            "lap_id": lap.id,
            "session_id": lap.session_id,
            "number": lap.number,
            "duration_ms": lap.duration_ms,
            "complete": lap.complete,
            "valid": lap.valid,
            "reasons": lap.reasons,
            "offset_s": offset,
            "lap_start": window[0] if window else None,
            "lap_end": window[1] if window else None,
            "segments": [self.public(r) for r in recs],
        }
        if not window or not recs:
            return {**base, "coverage": "none", "recording": None}
        cov = coverage(window, recs, offset)
        rec = next((r for r in recs if r["id"] == cov["recording_id"]), None)
        out = {**base, **cov, "recording": self.public(rec) if rec else None}
        if rec and cov["coverage"] in ("full", "partial"):
            duration = rec["duration_ms"] / 1000
            out["available_from_s"] = max(0.0, cov["video_from_s"])
            out["available_to_s"] = min(duration, cov["video_to_s"])
        return out
