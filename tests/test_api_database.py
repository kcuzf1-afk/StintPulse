import time
from fastapi.testclient import TestClient
import pytest
from ac_agent.app import create_app
from ac_agent.database import Database


@pytest.fixture
def client(tmp_path, recorded):
    app = create_app(tmp_path, start_engine=False)
    meta, laps = recorded
    db = app.state.db
    sid = db.new_session(meta)
    for lap in laps:
        db.save_lap(lap.model_copy(update={"session_id": sid}))
    with TestClient(app) as client:
        yield client, app, sid


def test_persistence_survives_reopen(tmp_path, recorded):
    meta, laps = recorded
    path = tmp_path / "test.sqlite"
    db = Database(path)
    sid = db.new_session(meta)
    db.save_lap(laps[0].model_copy(update={"session_id": sid}))
    db.close()
    db = Database(path)
    assert len(db.lap(laps[0].id).samples) == len(laps[0].samples)
    assert db.best(meta).duration_ms == laps[0].duration_ms
    db.close()


def test_storage_limit_stops_after_freeing_enough_pages(tmp_path, recorded):
    meta, laps = recorded
    db = Database(tmp_path / "limited.sqlite")
    ids = []
    for index in range(3):
        sid = db.new_session(meta, created_at=f"2026-10-0{index + 1}T00:00:00+00:00")
        ids.append(sid)
        db.save_lap(
            laps[0].model_copy(update={"id": f"lap-{index}", "session_id": sid})
        )
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result = db.enforce_limit(db.size_mb() * 0.75)
    assert result["removed"] == 1
    assert db.session(ids[0]) is None
    assert db.session(ids[1]) is not None and db.session(ids[2]) is not None
    assert not result["limit_exceeded"]
    db.close()


def test_csv_json_and_session_roundtrip(client):
    c, app, sid = client
    csv = c.get("/api/laps/demo-fixture-0/export?format=csv")
    assert csv.status_code == 200 and csv.text.startswith("source,lap,lap_ms")
    assert "engine_temp" in csv.text and "demo," in csv.text
    lap = c.get("/api/laps/demo-fixture-0/export?format=json").json()
    assert lap["samples"][0]["source"] == "demo"
    archive = c.get("/api/sessions/" + sid + "/export").json()
    restored = c.post("/api/import", json=archive)
    assert restored.status_code == 200
    other = c.get("/api/sessions/" + restored.json()["id"]).json()
    assert len(other["laps"]) == 2 and other["laps"][0]["id"] != "demo-fixture-0"


def test_compare_endpoint_and_cross_session(client):
    c, app, sid = client
    result = c.get("/api/compare?lap_ids=demo-fixture-1&reference=demo-fixture-0")
    assert result.status_code == 200 and result.json()[0]["delta"][-1] > 0
    assert (
        c.get("/api/compare?lap_ids=missing&reference=demo-fixture-0").status_code
        == 404
    )
    assert (
        c.get("/api/compare?lap_ids=a,b,c,d,e&reference=demo-fixture-0").status_code
        == 422
    )


def test_invalid_import_is_atomic(client):
    c, app, sid = client
    data = c.get("/api/sessions/" + sid + "/export").json()
    before = len(c.get("/api/sessions").json())
    data["laps"][1]["samples"][1]["source"] = "ac"
    assert c.post("/api/import", json=data).status_code == 422
    assert len(c.get("/api/sessions").json()) == before


def test_backup_restore_does_not_expose_token(client):
    c, app, sid = client
    token = "73915824"
    assert c.patch("/api/settings", json={"access_token": token}).status_code == 200
    data = c.get("/api/backup").content
    assert data.startswith(b"SQLite format 3")
    assert b"access_token" not in data
    assert token.encode() not in data
    assert (
        c.post(
            "/api/restore",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    assert len(c.get("/api/sessions").json()) == 2


def test_settings_validation_and_origin_security(client):
    c, app, sid = client
    assert c.patch("/api/settings", json={"capture_hz": 200}).status_code == 422
    for bad in ("1234567", "123456789", "abcdefgh", "a-secure-local-token-12345"):
        assert c.patch("/api/settings", json={"lan": True, "access_token": bad}).status_code == 422
    assert (
        c.patch("/api/settings", json={"temp_cold": 110, "temp_hot": 90}).status_code
        == 422
    )
    assert (
        c.patch(
            "/api/settings", json={"visible_widgets": ["tyres", "tyres"]}
        ).status_code
        == 422
    )
    assert (
        c.patch(
            "/api/settings",
            json={"source": "demo"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert c.get("/api/live", headers={"Host": "evil.example"}).status_code == 403
    assert (
        c.patch("/api/settings", json={"source": "demo", "language": "en"}).status_code
        == 200
    )
    assert c.get("/api/settings").json()["language"] == "en"
    assert "access_token" not in c.get("/api/settings").json()


def test_lan_bearer_required_and_ws_token(client):
    c, app, sid = client
    token = "04815162"
    assert (
        c.patch("/api/settings", json={"lan": True, "access_token": token}).status_code
        == 200
    )
    assert c.get("/api/settings").status_code == 401
    assert (
        c.get("/api/settings", headers={"Authorization": "Bearer " + token}).status_code
        == 200
    )
    assert c.get("/api/health").status_code == 200
    with c.websocket_connect("/ws", subprotocols=["ac-agent", token]) as ws:
        assert ws.receive_json()["type"] == "telemetry"


def test_websocket_live_updates_without_refresh(client, recorded):
    c, app, sid = client
    meta, laps = recorded
    with c.websocket_connect("/ws", subprotocols=["ac-agent"]) as ws:
        assert ws.receive_json()["sample"] is None
        app.state.engine.meta = meta
        app.state.engine.latest = laps[0].samples[10]
        packet = ws.receive_json()
        assert packet["sample"]["channels"]["speed"] > 0
        assert packet["sample"]["source"] == "demo"


def test_delete_active_session_guard_and_favorites(client):
    c, app, sid = client
    app.state.engine.session_id = sid
    assert c.delete("/api/sessions/" + sid).status_code == 409
    assert c.patch("/api/sessions/" + sid, json={"favorite": True}).status_code == 200
    assert app.state.db.session(sid)["favorite"] == 1
    app.state.engine.session_id = None
    assert c.delete("/api/sessions/" + sid).status_code == 200
    assert c.get("/api/laps/demo-fixture-0").status_code == 404


def test_demo_engine_starts_and_generates_measured_coach(tmp_path):
    app = create_app(tmp_path, source="demo")
    with TestClient(app) as c:
        deadline = time.monotonic() + 8
        data = {}
        while time.monotonic() < deadline:
            data = c.get("/api/live").json()
            if data.get("sample"):
                break
            time.sleep(0.03)
        assert data["sample"]["source"] == "demo" and data["connected"]
        assert not data["error"]
        sessions = c.get("/api/sessions").json()
        assert sessions[0]["lap_count"] >= 2
        details = c.get("/api/sessions/" + sessions[0]["id"]).json()
        assert details["laps"][1]["tips"]
        a = app.state.db.lap(details["laps"][0]["id"])
        b = app.state.db.lap(details["laps"][1]["id"])
        assert a.samples[-1].captured_at - a.samples[0].captured_at == pytest.approx(
            a.duration_ms / 1000
        )
        assert b.samples[0].captured_at >= a.samples[-1].captured_at
        assert data["sample"]["last_lap_ms"] == b.duration_ms
        assert len(c.get("/api/map").json()["points"]) > 500
