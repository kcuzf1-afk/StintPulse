"""Sound for onboard recordings: WAV on the server clock, API, recovery.

The real Windows capture (WASAPI process/system loopback) plays a short quiet
test tone and is therefore opt-in: AC_AGENT_AUDIO_TEST=1.
"""

import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ac_agent import audio
from ac_agent.app import create_app

from test_recordings import CHUNKS, CLIP, PC, PHONE, timed_lap


def read_wav(path):
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels())
        return w.getframerate(), data


# ---------- file and timeline ----------


def test_wav_header_and_repair_after_crash(tmp_path):
    path = tmp_path / "a.wav"
    w = audio.WavWriter(path)
    w.write(np.full(960 * 2, 1000, dtype="<i2").tobytes())
    w.silence(480)
    w.close()
    rate, data = read_wav(path)
    assert rate == 48000 and data.shape == (1440, 2)
    # Crash: data written, header never finalized.
    crashed = tmp_path / "b.wav.part"
    w = audio.WavWriter(crashed)
    w.write(np.ones(4800 * 2, dtype="<i2").tobytes())
    w.file.flush()
    w.file.close()
    assert audio.repair_wav(crashed) == (48000, 2, 4800)
    assert read_wav(crashed)[1].shape == (4800, 2)


def test_timeline_keeps_samples_on_the_server_clock(tmp_path):
    w = audio.WavWriter(tmp_path / "t.wav")
    t = audio.Timeline(w)
    t.begin(1000.0)
    packet = lambda ms: np.full(48 * ms * 2, 7, dtype="<i2").tobytes()
    t.add(1000.0, packet(100))  # 0.0 .. 0.1 s
    t.add(1000.5, packet(100))  # game silent for 0.4 s: no packets
    assert t.padded == 19200 and w.frames == 28800
    t.add(1000.59, packet(100))  # 10 ms early: within tolerance, kept
    t.add(1000.66, packet(100))  # 40 ms overlap: dropped, timeline stays true
    assert t.dropped == 1920
    t.pad_until(1002.0)
    assert t.position() == pytest.approx(1002.0, abs=1 / 48000)
    w.close()
    rate, data = read_wav(tmp_path / "t.wav")
    # Sample index <-> server time: the second packet starts exactly at +0.5 s.
    first_after_gap = np.argmax(data[4800:, 0] != 0) + 4800
    assert first_after_gap / rate == pytest.approx(0.5, abs=1e-4)


# ---------- recordings with sound ----------


@pytest.fixture
def sound_app(tmp_path, recorded):
    meta, laps = recorded
    app = create_app(tmp_path, start_engine=False, audio_factory=lambda path, mode: audio.SyntheticCapture(path))
    engine = app.state.engine
    with engine.lock:
        engine._new_session(meta.model_copy(update={"source": "ac"}))
    yield app, engine, laps, tmp_path
    app.state.recordings.close()
    engine.analyzer.shutdown(wait=True)
    engine.writer.shutdown(wait=True)


def start_recording(client, engine):
    r = client.post(
        "/api/recordings",
        json={"session_id": engine.session_id, "client_id": "tab", "mime": "video/webm;codecs=vp8"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def upload_clip(client, rid, t0, chunks=CHUNKS):
    data, pos = CLIP.read_bytes(), 0
    last = None
    for seq, size in enumerate(chunks):
        params = {"started_at": t0} if seq == 0 else {}
        last = client.post(f"/api/recordings/{rid}/chunks/{seq}", content=data[pos : pos + size], params=params)
        assert last.status_code == 200, last.text
        pos += size
    return last.json()


def test_sound_is_recorded_with_the_segment_and_served(sound_app):
    app, engine, laps, _ = sound_app
    client = TestClient(app, client=PC)
    created = start_recording(client, engine)
    assert created["audio"]["status"] == "recording" and created["audio"]["mode"] == "test"
    t0 = time.time()
    last = upload_clip(client, created["id"], t0)
    assert last["audio"]["status"] == "recording"
    # Running sound counts towards the storage limit.
    assert app.state.recordings.used_bytes() > sum(CHUNKS)
    time.sleep(0.3)
    client.post(f"/api/recordings/{created['id']}/finish", json={"reason": "session_end"})
    app.state.recordings.flush()
    info = client.get(f"/api/recordings/{created['id']}").json()
    sound = info["audio"]
    assert sound["status"] == "ready" and sound["sample_rate"] == 48000 and sound["channels"] == 2
    # One clock: sound starts with (before) the video and covers it.
    assert sound["started_at"] <= info["started_at"] + 0.05
    assert sound["duration_ms"] >= (time.time() - t0 - 1) * 1000
    wav = client.get(f"/api/audio/{created['id']}")
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav"
    part = client.get(f"/api/audio/{created['id']}", headers={"Range": "bytes=44-1043"})
    assert part.status_code == 206 and len(part.content) == 1000
    rate, data = read_wav(Path(app.state.recordings.get(created["id"])["path"]).with_suffix(".wav"))
    tone = data[:rate, 0].astype(float)
    spectrum = np.abs(np.fft.rfft(tone))
    assert np.fft.rfftfreq(len(tone), 1 / rate)[np.argmax(spectrum)] == pytest.approx(440, abs=5)
    # The lap list knows which laps have sound.
    lap = timed_lap(laps[0], engine.session_id, t0 + 1, t0 + 4)
    engine._complete(lap, synchronous=True)
    status = client.get(f"/api/sessions/{engine.session_id}").json()["laps"][0]["video"]
    assert status["audio"] is True
    # Deleting the video deletes its sound.
    wav_file = Path(app.state.recordings.get(created["id"])["path"]).with_suffix(".wav")
    assert client.delete(f"/api/recordings/{created['id']}").status_code == 200
    assert not wav_file.exists()


def test_sound_off_and_game_not_running(sound_app, monkeypatch):
    app, engine, _, tmp = sound_app
    client = TestClient(app, client=PC)
    client.patch("/api/settings", json={"record_audio": "off"})
    off = start_recording(client, engine)
    assert off["audio"]["status"] == "off"
    client.post(f"/api/recordings/{off['id']}/finish", json={"reason": "x"})
    app.state.recordings.flush()
    # "Only game sound" while Assetto Corsa is not running: video without sound.
    client.patch("/api/settings", json={"record_audio": "game"})
    app.state.recordings.audio_factory = audio.open_capture
    monkeypatch.delenv("AC_AGENT_TEST_AUDIO", raising=False)
    monkeypatch.setattr(audio, "find_game_pid", lambda: None)
    monkeypatch.setattr(audio.sys, "platform", "win32")
    created = start_recording(client, engine)
    assert created["audio"]["status"] == "none" and "not running" in created["audio"]["reason"]
    assert client.get(f"/api/audio/{created['id']}").status_code == 404


def test_sound_survives_a_crash_and_needs_the_code_on_lan(sound_app, tmp_path):
    app, engine, _, data_dir = sound_app
    client = TestClient(app, client=PC)
    created = start_recording(client, engine)
    upload_clip(client, created["id"], time.time(), CHUNKS[:4])
    time.sleep(0.3)
    store = app.state.recordings
    cap = store.audio.pop(created["id"])  # the process dies: header never written
    cap.stop_event.set()
    cap.thread.join()
    cap.writer.file.flush()
    cap.writer.file.close()
    store.close()
    restarted = create_app(data_dir, start_engine=False, audio_factory=lambda p, m: audio.SyntheticCapture(p))
    restarted.state.recordings.flush()
    pc = TestClient(restarted, client=PC)
    sound = pc.get(f"/api/recordings/{created['id']}").json()["audio"]
    assert sound["status"] == "ready" and sound["interrupted"] and sound["duration_ms"] > 200
    assert pc.get(f"/api/audio/{created['id']}").status_code == 200
    pc.patch("/api/settings", json={"lan": True})
    code = pc.get("/api/access-code").json()["code"]
    phone = TestClient(restarted, client=PHONE)
    assert phone.get(f"/api/audio/{created['id']}").status_code == 401
    assert phone.get(f"/api/audio/{created['id']}", params={"token": code}).status_code == 200
    restarted.state.recordings.close()


# ---------- real Windows capture (opt-in, plays a quiet test tone) ----------

TONE = """
import io, math, struct, time, wave, winsound
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(b"".join(struct.pack("<hh", v, v) for v in (int(600 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000))))
time.sleep(0.8)
print(time.time(), flush=True)
winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)
"""


@pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("AC_AGENT_AUDIO_TEST"),
    reason="plays a test tone; set AC_AGENT_AUDIO_TEST=1 on Windows",
)
@pytest.mark.parametrize("mode", ["game", "system"])
def test_windows_loopback_captures_the_program_sound_on_time(tmp_path, mode):
    player = subprocess.Popen([sys.executable, "-c", TONE], stdout=subprocess.PIPE, text=True)
    cap = audio.WasapiCapture(tmp_path / "x.wav", mode, player.pid if mode == "game" else None).start()
    played_at = float(player.communicate(timeout=20)[0].split()[0])
    time.sleep(0.3)
    summary = cap.stop(until=time.time())
    rate, data = read_wav(tmp_path / "x.wav")
    loud = np.flatnonzero(np.abs(data[:, 0]) > 200)
    assert loud.size, "tone not captured"
    onset = summary["started_at"] + loud[0] / rate
    # Same clock as telemetry: the tone starts right after it was played.
    assert 0 <= onset - played_at < 0.25
    tone = data[loud[0] : loud[0] + rate // 2, 0].astype(float)
    spectrum = np.abs(np.fft.rfft(tone))
    assert np.fft.rfftfreq(len(tone), 1 / rate)[np.argmax(spectrum)] == pytest.approx(440, abs=4)
