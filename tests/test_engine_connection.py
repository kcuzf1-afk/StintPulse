"""Engine-level source switching, capture robustness and status separation."""

import time

import pytest
from fastapi.testclient import TestClient

import ac_agent.engine as engine_module
from ac_agent.app import create_app
from ac_agent.database import Database
from ac_agent.engine import Engine
from ac_agent.models import Settings
from ac_fake import FakeGame, make_source


def wait_for(predicate, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def game(monkeypatch):
    game = FakeGame()
    game.process_status = "not_found"
    # The engine creates ACSource() itself; give it the in-process fake game.
    monkeypatch.setattr(engine_module, "ACSource", lambda: make_source(game, time.monotonic))
    return game


@pytest.fixture
def engine(tmp_path):
    db = Database(tmp_path / "engine.sqlite")
    engines = []

    def make(source):
        e = Engine(db, Settings(source=source))
        engines.append(e)
        e.start()
        return e

    yield make
    for e in engines:
        e.stop()
    db.close()


def test_demo_to_ac_clears_live_view_keeps_archive_and_waits(engine, game):
    e = engine("demo")
    assert wait_for(lambda: e.payload()["sample"] is not None)
    demo = e.payload()
    assert demo["status"] == "demo" and demo["source"] == "demo"
    assert demo["connected"] is True and demo["game_connected"] is False
    demo_session = demo["session_id"]

    e.settings = e.settings.model_copy(update={"source": "ac"})
    assert wait_for(lambda: e.payload()["status"] == "waiting_game")
    time.sleep(0.2)  # queued demo frames must not reappear
    live = e.payload()
    assert live["source"] == "ac"
    assert live["sample"] is None and live["meta"] is None and live["session_id"] is None
    assert live["connected"] is False and live["game_connected"] is False
    assert live["delta_s"] is None and live["tips"] == [] and live["statistics"] == {}
    assert e.map_payload()["points"] == []
    # Demo sessions stay in the archive.
    archived = e.db.list_sessions(source="demo")
    assert any(s["id"] == demo_session for s in archived)

    # First real sample switches to live AC data.
    game.start()
    game.process_status = "found"
    assert wait_for(lambda: e.payload()["status"] == "live")
    assert wait_for(lambda: e.payload()["sample"] is not None)
    real = e.payload()
    assert real["sample"]["source"] == "ac" and real["meta"]["source"] == "ac"
    assert real["game_connected"] is True

    # Switching back to demo never shows the game as connected.
    e.settings = e.settings.model_copy(update={"source": "demo"})
    assert wait_for(lambda: e.payload()["status"] == "demo")
    assert e.payload()["game_connected"] is False


def test_capture_errors_do_not_end_the_thread(engine, game, monkeypatch):
    calls = {"n": 0}
    original = make_source

    class Exploding:
        def __init__(self):
            self.inner = original(game, time.monotonic)
            self.state, self.error = "waiting_game", ""

        def read(self, settings):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RuntimeError("simulated decoder crash")
            frame = self.inner.read(settings)
            self.state, self.error = self.inner.state, self.inner.error
            return frame

        def close(self):
            self.inner.close()

        def diagnostics(self):
            return self.inner.diagnostics()

    monkeypatch.setattr(engine_module, "ACSource", Exploding)
    game.start()
    game.process_status = "found"
    e = engine("ac")
    assert wait_for(lambda: e.payload()["status"] == "live")
    assert e.capture_alive()
    diag = e.diagnostics()
    assert diag["capture_thread"]["errors"] == 3
    assert "simulated decoder crash" in diag["capture_thread"]["last_error"]


def test_dead_capture_thread_is_reported_and_recheck_restarts_it(engine, game):
    e = engine("ac")
    assert wait_for(lambda: e.payload()["status"] == "waiting_game")
    # Simulate an unexpected thread death (e.g. a BaseException).
    e.stop_event.set()
    e.threads[0].join(2)
    e.stop_event.clear()
    assert not e.capture_alive()
    assert e.payload()["status"] == "capture_failed"
    assert e.diagnostics()["capture_thread"]["alive"] is False
    e.recheck()
    assert wait_for(e.capture_alive)
    assert wait_for(lambda: e.payload()["status"] == "waiting_game")
    assert e.diagnostics()["capture_thread"]["restarts"] == 1


def test_diagnostics_api_and_recheck(tmp_path, game):
    game.launcher = ["content manager.exe"]
    app = create_app(tmp_path, source="ac")
    with TestClient(app) as client:
        assert wait_for(lambda: client.get("/api/live").json()["status"] == "waiting_game")
        data = client.get("/api/diagnostics").json()
        assert data["status"] == "waiting_game"
        assert data["source"] == {"selected": "ac", "active": "ac"}
        assert data["capture_thread"]["alive"] is True
        assert data["system"]["python_bits"] in (32, 64)
        assert {p["name"] for p in data["game"]["pages"]} == {
            "acpmf_physics",
            "acpmf_graphics",
            "acpmf_static",
        }
        assert data["game"]["process"]["status"] == "not_found"
        assert data["websocket"]["clients"] == 0
        text = " ".join(h["de"] for h in data["hints"])
        assert "Launcher" in text and "Administrator" not in text
        assert "token" not in str(data).lower()
        game.start()
        game.process_status = "found"
        data = client.post("/api/diagnostics/recheck").json()
        assert data["game"]["state"] in ("connected", "not_initialized")
        assert wait_for(lambda: client.get("/api/live").json()["game_connected"] is True)
        with client.websocket_connect("/ws", subprotocols=["ac-agent"]) as ws:
            frame = ws.receive_json()
            assert frame["status"] == "live" and frame["game_connected"] is True
            assert client.get("/api/diagnostics").json()["websocket"]["clients"] == 1


def test_camera_video_source_settings_roundtrip(tmp_path):
    app = create_app(tmp_path, start_engine=False)
    with TestClient(app) as client:
        r = client.patch(
            "/api/settings",
            json={"video_mode": "camera", "video_device": "OBS Virtual Camera"},
        )
        assert r.status_code == 200, r.text
        s = client.get("/api/settings").json()
        assert s["video_mode"] == "camera" and s["video_device"] == "OBS Virtual Camera"
        assert client.patch("/api/settings", json={"video_device": "x" * 201}).status_code == 422
