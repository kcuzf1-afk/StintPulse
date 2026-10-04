"""Onboard recording: WebM indexing, storage, lap linkage, API (no browser).

The WebM fixture is a real Chromium MediaRecorder recording (fake camera,
VP8, 8 s, keyframe every 2 s) as the dashboard produces it.
"""

import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ac_agent import recordings as rec_module
from ac_agent import webm
from ac_agent.app import create_app
from ac_agent.laps import lap_wall_window

FIXTURES = Path(__file__).parent / "fixtures"
CLIP = FIXTURES / "mediarecorder-vp8.webm"
# MediaRecorder delivered the fixture in these chunk sizes (timeslice 1 s).
CHUNKS = [4322, 14957, 18442, 15738, 17733, 15189, 16643, 14120, 839]
PC, PHONE = ("127.0.0.1", 50000), ("192.168.178.49", 50000)


def cluster_bytes(path):
    layout = webm.scan(path)
    with open(path, "rb") as f:
        out = []
        for c in layout.clusters:
            f.seek(c.data_start)
            out.append(f.read(c.data_len))
    return out


# ---------- WebM indexing ----------


def test_finalize_adds_duration_and_cues_without_reencoding(tmp_path):
    raw = webm.scan(CLIP)
    assert not raw.indexed  # live MediaRecorder output: no Duration, no Cues
    out = tmp_path / "clip.webm"
    summary = webm.finalize(CLIP, out)
    assert 7500 < summary["duration_ms"] < 8800
    assert summary["cues"] == summary["keyframes"] == 4
    assert summary["codec"] == "V_VP8" and not summary["truncated"]
    checked = webm.verify(out, summary)
    assert checked["frames"] == summary["frames"]
    # Every frame is copied byte for byte.
    assert cluster_bytes(out) == cluster_bytes(CLIP)


def test_truncated_recording_keeps_every_complete_frame(tmp_path):
    cut = tmp_path / "cut.webm.part"
    data = CLIP.read_bytes()
    cut.write_bytes(data[: int(len(data) * 0.7)])
    summary = webm.finalize(cut, tmp_path / "cut.webm")
    full = webm.inspect(CLIP)
    assert summary["truncated"] and 0 < summary["frames"] < full["frames"]
    assert summary["duration_ms"] < full["duration_ms"]
    webm.verify(tmp_path / "cut.webm", summary)


def test_non_webm_is_rejected(tmp_path):
    bad = tmp_path / "x.webm"
    bad.write_bytes(b"\x00" * 64)
    with pytest.raises(webm.WebmError):
        webm.finalize(bad, tmp_path / "y.webm")


# ---------- helpers ----------


def timed_lap(lap, sid, start, end, number=None, complete=True):
    """A stored lap re-timed onto the server clock between start and end."""
    n = len(lap.samples)
    samples = [
        s.model_copy(
            update={
                "captured_at": start + (end - start) * i / (n - 1),
                "source": "ac",
                "synthetic_boundary": complete and i in (0, n - 1),
            }
        )
        for i, s in enumerate(lap.samples)
    ]
    return lap.model_copy(
        update={
            "id": str(uuid.uuid4()),
            "session_id": sid,
            "number": number or lap.number,
            "samples": samples,
            "complete": complete,
            "valid": complete,
            "reasons": [] if complete else ["partial_start"],
            "analysis": {},
        }
    )


@pytest.fixture
def app_env(tmp_path, recorded):
    meta, laps = recorded
    meta = meta.model_copy(update={"source": "ac"})
    app = create_app(tmp_path, start_engine=False)
    engine = app.state.engine
    # Video-only here (sound has its own tests); never a real capture in tests.
    engine.settings = engine.settings.model_copy(update={"record_audio": "off"})
    with engine.lock:
        engine._new_session(meta)
    yield app, engine, laps, tmp_path
    app.state.recordings.close()
    engine.analyzer.shutdown(wait=True)
    engine.writer.shutdown(wait=True)


def upload(client, rid, t0, chunks=CHUNKS):
    data = CLIP.read_bytes()
    pos = 0
    for seq, size in enumerate(chunks):
        params = {"started_at": t0} if seq == 0 else {}
        r = client.post(
            f"/api/recordings/{rid}/chunks/{seq}",
            content=data[pos : pos + size],
            params=params,
        )
        assert r.status_code == 200, r.text
        pos += size


def start(client, engine, client_id="tab-1"):
    r = client.post(
        "/api/recordings",
        json={
            "session_id": engine.session_id,
            "client_id": client_id,
            "mime": "video/webm;codecs=vp8",
            "quality": "standard",
            "bitrate": 6_000_000,
            "clock_offset_ms": 1.5,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def record_clip(client, engine, t0):
    rid = start(client, engine)
    upload(client, rid, t0)
    assert client.post(f"/api/recordings/{rid}/finish", json={"reason": "session_end"}).status_code == 200
    engine_recordings(client).flush()
    return rid


def engine_recordings(client):
    return client.app.state.recordings


# ---------- API and storage ----------


def test_recording_is_stored_indexed_and_streamed_with_ranges(app_env):
    app, engine, _, data_dir = app_env
    client = TestClient(app, client=PC)
    t0 = time.time()
    rid = record_clip(client, engine, t0)
    info = client.get(f"/api/recordings/{rid}").json()
    assert info["status"] == "ready" and info["indexed"] and info["segment"] == 1
    assert 7500 < info["duration_ms"] < 8800 and info["started_at"] == pytest.approx(t0)
    assert info["clock_offset_ms"] == 1.5
    files = list((data_dir / "videos").rglob("*"))
    assert [f.suffix for f in files if f.is_file()] == [".webm"]  # no .part left
    full = client.get(f"/api/videos/{rid}")
    assert full.status_code == 200 and full.headers["content-type"] == "video/webm"
    part = client.get(f"/api/videos/{rid}", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"].startswith("bytes 100-199/")
    assert part.content == full.content[100:200]


def test_chunks_are_ordered_and_only_the_pc_records(app_env):
    app, engine, _, _ = app_env
    client = TestClient(app, client=PC)
    rid = start(client, engine)
    data = CLIP.read_bytes()
    assert client.post(f"/api/recordings/{rid}/chunks/0", content=data[:100]).status_code == 200
    # Retry of an already stored chunk is harmless, a gap is refused.
    again = client.post(f"/api/recordings/{rid}/chunks/0", content=data[:100])
    assert again.json()["duplicate"]
    assert client.post(f"/api/recordings/{rid}/chunks/5", content=b"x").status_code == 409
    # A second tab must not record the same session in parallel.
    other = client.post(
        "/api/recordings",
        json={"session_id": engine.session_id, "client_id": "tab-2", "mime": "video/webm"},
    )
    assert other.status_code == 409
    phone = TestClient(app, client=PHONE)
    assert phone.post("/api/recordings", json={}).status_code == 403
    assert phone.post(f"/api/recordings/{rid}/chunks/1", content=b"x").status_code == 403
    stale = client.post(
        "/api/recordings",
        json={"session_id": "old-session", "client_id": "tab-1", "mime": "video/webm"},
    )
    assert stale.status_code == 409


def test_lan_video_needs_the_access_code(app_env):
    app, engine, _, _ = app_env
    pc = TestClient(app, client=PC)
    rid = record_clip(pc, engine, time.time())
    pc.patch("/api/settings", json={"lan": True})
    code = pc.get("/api/access-code").json()["code"]
    phone = TestClient(app, client=PHONE)
    assert phone.get(f"/api/videos/{rid}").status_code == 401
    assert phone.get(f"/api/videos/{rid}", params={"token": code}).status_code == 200


def test_two_consecutive_laps_land_on_the_right_video_sections(app_env):
    app, engine, laps, _ = app_env
    client = TestClient(app, client=PC)
    t0 = time.time() - 60
    rid = start(client, engine)
    upload(client, rid, t0)
    # Engine path: wall window stored with the lap, link hook runs on save.
    a = timed_lap(laps[0], engine.session_id, t0 + 1.0, t0 + 4.0, number=1)
    b = timed_lap(laps[1], engine.session_id, t0 + 4.0, t0 + 7.5, number=2)
    for lap in (a, b):
        engine._complete(lap, synchronous=True)
    pending = client.get(f"/api/sessions/{engine.session_id}").json()["laps"]
    assert {l["video"]["coverage"] for l in pending} == {"pending"}  # still recording
    client.post(f"/api/recordings/{rid}/finish", json={"reason": "session_end"})
    app.state.recordings.flush()
    session = client.get(f"/api/sessions/{engine.session_id}").json()
    assert [l["video"]["coverage"] for l in session["laps"]] == ["full", "full"]
    va = client.get(f"/api/laps/{a.id}/video").json()
    vb = client.get(f"/api/laps/{b.id}/video").json()
    assert va["recording"]["id"] == vb["recording"]["id"] == rid
    assert va["video_from_s"] == pytest.approx(1.0, abs=1e-3)
    assert va["video_to_s"] == pytest.approx(4.0, abs=1e-3)
    assert vb["video_from_s"] == pytest.approx(4.0, abs=1e-3)
    assert vb["video_to_s"] == pytest.approx(7.5, abs=1e-3)
    assert va["available_from_s"] == pytest.approx(1.0, abs=1e-3)
    # Offset: the picture lags 0.25 s behind telemetry -> later in the video.
    client.patch("/api/settings", json={"video_offset_s": 0.25})
    assert client.get(f"/api/laps/{a.id}/video").json()["video_from_s"] == pytest.approx(1.25, abs=1e-3)


def test_video_gap_is_partial_never_full(app_env):
    app, engine, laps, _ = app_env
    client = TestClient(app, client=PC)
    t0 = time.time() - 120
    record_clip(client, engine, t0)  # covers t0 .. t0+8
    second = start(client, engine)  # source came back at t0+10
    upload(client, second, t0 + 10)
    client.post(f"/api/recordings/{second}/finish", json={"reason": "stopped"})
    app.state.recordings.flush()
    spanning = timed_lap(laps[0], engine.session_id, t0 + 7.0, t0 + 13.0)
    outside = timed_lap(laps[1], engine.session_id, t0 + 40.0, t0 + 45.0)
    for lap in (spanning, outside):
        engine._complete(lap, synchronous=True)
    info = client.get(f"/api/laps/{spanning.id}/video").json()
    assert info["coverage"] == "partial" and 0 < info["covered_ratio"] < 1
    assert info["recording"]["segment"] == 2  # 3 of 6 s are in segment 2, ~1 s in 1
    assert info["available_from_s"] == 0 and info["video_from_s"] < 0
    assert client.get(f"/api/laps/{outside.id}/video").json()["coverage"] == "none"


def test_recordings_survive_restart_and_open_segments_are_recovered(app_env):
    app, engine, _, data_dir = app_env
    client = TestClient(app, client=PC)
    done = record_clip(client, engine, time.time())
    open_rid = start(client, engine)
    upload(client, open_rid, time.time(), CHUNKS[:5])  # browser closed mid-recording
    app.state.recordings.close()
    restarted = create_app(data_dir, start_engine=False)
    restarted.state.recordings.flush()
    c2 = TestClient(restarted, client=PC)
    assert c2.get(f"/api/recordings/{done}").json()["status"] == "ready"
    assert c2.get(f"/api/videos/{done}").status_code == 200
    recovered = c2.get(f"/api/recordings/{open_rid}").json()
    assert recovered["status"] == "ready" and recovered["end_reason"] == "interrupted"
    assert recovered["indexed"] and recovered["duration_ms"] > 0
    restarted.state.recordings.close()


def test_idle_segment_is_closed_by_the_watchdog(app_env):
    app, engine, _, _ = app_env
    client = TestClient(app, client=PC)
    rid = start(client, engine)
    upload(client, rid, time.time(), CHUNKS[:3])
    store = app.state.recordings
    assert store.idle(time.time() + rec_module.IDLE_TIMEOUT_S + 1) == [rid]
    store.flush()
    assert client.get(f"/api/recordings/{rid}").json()["end_reason"] == "interrupted"


def test_storage_limit_is_never_exceeded_silently(app_env, monkeypatch):
    app, engine, _, _ = app_env
    client = TestClient(app, client=PC)
    client.patch("/api/settings", json={"record_storage_mb": 1024})
    monkeypatch.setattr(rec_module, "MB", 100)  # limit 1024 "MB" = 102 400 bytes
    rid = start(client, engine)
    data = CLIP.read_bytes()
    r = client.post(f"/api/recordings/{rid}/chunks/0", content=data, params={"started_at": time.time()})
    assert r.status_code == 507 and "limit" in r.json()["detail"]
    # Free disk space is checked as well.
    monkeypatch.setattr(rec_module, "MB", 1024**2)
    monkeypatch.setattr(
        rec_module.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(1, 1, 10)
    )
    r = client.post(f"/api/recordings/{rid}/chunks/0", content=data[:10])
    assert r.status_code == 507 and "disk" in r.json()["detail"]


def test_oldest_video_is_deleted_only_with_the_retention_rule(app_env, recorded, monkeypatch):
    app, engine, _, _ = app_env
    client = TestClient(app, client=PC)
    old = record_clip(client, engine, time.time() - 600)
    old_file = Path(app.state.recordings.get(old)["path"])
    meta = engine.meta.model_copy(update={"track": "other"})
    with engine.lock:
        engine._new_session(meta)
    rid = start(client, engine)
    client.patch("/api/settings", json={"record_storage_mb": 1024})
    monkeypatch.setattr(rec_module, "MB", 100)
    data = CLIP.read_bytes()
    first = client.post(f"/api/recordings/{rid}/chunks/0", content=data[:20000], params={"started_at": time.time()})
    assert first.status_code == 507 and old_file.exists()  # rule off: nothing deleted
    client.patch("/api/settings", json={"record_delete_oldest": True})
    ok = client.post(f"/api/recordings/{rid}/chunks/0", content=data[:20000], params={"started_at": time.time()})
    assert ok.status_code == 200 and not old_file.exists()
    assert client.get(f"/api/recordings/{old}").status_code == 404


def test_deleting_a_session_deletes_its_videos_and_size_limit_keeps_them(app_env):
    app, engine, laps, _ = app_env
    client = TestClient(app, client=PC)
    sid = engine.session_id
    rid = record_clip(client, engine, time.time())
    path = Path(app.state.recordings.get(rid)["path"])
    with engine.lock:
        engine._new_session(engine.meta.model_copy(update={"track": "next"}))
    # Telemetry size limit: sessions with videos are never removed implicitly.
    app.state.db.enforce_limit(0, engine.session_id)
    assert client.get(f"/api/sessions/{sid}").status_code == 200 and path.exists()
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert not path.exists() and client.get(f"/api/recordings/{rid}").status_code == 404


def test_old_sessions_without_video_still_work(app_env):
    app, engine, laps, _ = app_env
    client = TestClient(app, client=PC)
    lap = timed_lap(laps[0], engine.session_id, 1000.0, 1090.0)
    engine._complete(lap, synchronous=True)
    session = client.get(f"/api/sessions/{engine.session_id}").json()
    assert session["recordings"] == []
    assert session["laps"][0]["video"]["coverage"] == "none"
    info = client.get(f"/api/laps/{lap.id}/video").json()
    assert info["coverage"] == "none" and info["recording"] is None
    listed = client.get("/api/sessions").json()
    assert listed[0]["video_count"] == 0


def test_video_folder_only_changes_on_the_pc_and_must_be_writable(app_env, tmp_path):
    app, _, _, _ = app_env
    pc = TestClient(app, client=PC)
    target = tmp_path / "my videos"
    assert pc.patch("/api/settings", json={"record_dir": "relative/videos"}).status_code == 422
    r = pc.patch("/api/settings", json={"record_dir": str(target)})
    assert r.status_code == 200 and target.is_dir()
    assert pc.get("/api/recordings/storage").json()["dir"] == str(target)
    pc.patch("/api/settings", json={"lan": True})
    code = pc.get("/api/access-code").json()["code"]
    phone = TestClient(app, client=PHONE, headers={"Authorization": "Bearer " + code})
    assert phone.patch("/api/settings", json={"record_dir": str(tmp_path)}).status_code == 403


def test_wall_window_uses_line_frames_even_after_a_pause(recorded):
    _, laps = recorded
    lap = timed_lap(laps[0], "s", 500.0, 700.0)  # lap clock 91.8 s, wall 200 s (pause)
    assert lap_wall_window(lap) == [500.0, 700.0]
    partial = timed_lap(laps[0], "s", 500.0, 600.0, complete=False)
    assert lap_wall_window(partial) == [500.0, 600.0]


def test_portable_data_folder_next_to_the_exe(tmp_path, monkeypatch):
    import sys

    from ac_agent import app as app_module

    exe = tmp_path / "AC Engineering 0.1" / "AC Engineering Data Agent.exe"
    exe.parent.mkdir()
    monkeypatch.delenv("AC_AGENT_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert app_module.portable_data_dir() is None  # no "Daten" folder: AppData
    (exe.parent / "Daten").mkdir()
    assert app_module.default_data_dir() == (exe.parent / "Daten").resolve()


def test_videos_are_found_after_the_data_folder_was_moved(app_env, tmp_path):
    app, engine, _, data_dir = app_env
    client = TestClient(app, client=PC)
    rid = record_clip(client, engine, time.time())
    app.state.recordings.close()
    app.state.db.close()
    moved = tmp_path / "moved" / "Daten"
    moved.mkdir(parents=True)
    shutil.move(str(data_dir / "videos"), str(moved / "videos"))
    for name in ("telemetry.sqlite", "telemetry.sqlite-wal", "telemetry.sqlite-shm"):
        if (data_dir / name).exists():
            shutil.move(str(data_dir / name), str(moved / name))
    restarted = create_app(moved, start_engine=False)
    c2 = TestClient(restarted, client=PC)
    assert c2.get(f"/api/videos/{rid}").status_code == 200
    path = Path(restarted.state.recordings.get(rid)["path"])
    assert moved in path.parents  # stored path updated to the new folder
    restarted.state.recordings.close()
