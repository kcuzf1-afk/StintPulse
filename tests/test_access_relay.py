"""8-digit LAN access code, brute-force lock and WebRTC signaling relay."""

import json
import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ac_agent.access import AccessGuard, MAX_FAILURES
from ac_agent.app import create_app
from ac_agent.database import Database
from ac_agent.models import Settings

PC = ("127.0.0.1", 50000)
PHONE = ("192.168.178.77", 50000)


def lan_app(tmp_path):
    # Without "with": no lifespan, so the database stays open across clients.
    app = create_app(tmp_path, start_engine=False)
    pc = TestClient(app, client=PC)
    assert pc.patch("/api/settings", json={"lan": True}).status_code == 200
    code = pc.get("/api/access-code").json()["code"]
    return app, code


def test_old_token_is_reset_to_8_digit_code(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    old = Settings().model_dump()
    old.update(lan=True, access_token="old-long-token-abcdefghijklmnop")
    with db.lock, db.conn:
        db.conn.execute(
            "INSERT OR REPLACE INTO settings VALUES(1,?)", (json.dumps(old),)
        )
    settings = db.get_settings()
    assert re.fullmatch(r"\d{8}", settings.access_token)
    assert settings.lan is True
    assert db.get_settings().access_token == settings.access_token  # persisted
    db.close()


def test_code_created_when_missing_and_only_shown_on_pc(tmp_path):
    app = create_app(tmp_path, start_engine=False)
    with TestClient(app, client=PC) as pc:
        data = pc.get("/api/access-code").json()
        assert re.fullmatch(r"\d{8}", data["code"])
        assert "access_token" not in pc.get("/api/settings").json()
    with TestClient(app, client=PHONE) as phone:
        assert phone.get("/api/access-code").status_code == 403
        assert phone.post("/api/access-code").status_code == 403


def test_lan_phone_needs_code_pc_does_not(tmp_path):
    app, code = lan_app(tmp_path)
    with TestClient(app, client=PC) as pc:
        assert pc.get("/api/settings").status_code == 200
        with pc.websocket_connect("/ws", subprotocols=["ac-agent"]) as ws:
            assert ws.receive_json()["type"] == "telemetry"
    with TestClient(app, client=PHONE) as phone:
        r = phone.get("/api/settings")
        assert r.status_code == 401 and r.json()["detail"] == "Access code required"
        ok = phone.get("/api/settings", headers={"Authorization": "Bearer " + code})
        assert ok.status_code == 200
        with phone.websocket_connect("/ws", subprotocols=["ac-agent", code]) as ws:
            assert ws.receive_json()["type"] == "telemetry"
        with pytest.raises(WebSocketDisconnect):
            with phone.websocket_connect("/ws", subprotocols=["ac-agent", "00000000"]) as ws:
                ws.receive_json()


def test_dashboard_polling_without_code_never_locks_the_phone(tmp_path):
    """Regression: the phone was locked before the user could type the code."""
    app, code = lan_app(tmp_path)
    wrong = "11111111" if code != "11111111" else "22222222"
    phone = TestClient(app, client=PHONE)
    for _ in range(50):  # dashboard polls /map, /performance … without a code
        assert phone.get("/api/map").status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with phone.websocket_connect("/ws/onboard?role=viewer", subprotocols=["ac-agent"]) as ws:
                ws.receive_json()
    for _ in range(50):  # one mistyped code repeated by every poll is one guess
        assert phone.get("/api/map", headers={"Authorization": "Bearer " + wrong}).status_code == 401
    assert phone.get("/api/map", headers={"Authorization": "Bearer " + code}).status_code == 200


def test_distinct_wrong_codes_lock_the_device(tmp_path):
    app, code = lan_app(tmp_path)
    wrongs = [f"{i:08d}" for i in range(1, 30) if f"{i:08d}" != code][:MAX_FAILURES]
    with TestClient(app, client=PHONE) as phone:
        for wrong in wrongs:
            assert phone.get("/api/live", headers={"Authorization": "Bearer " + wrong}).status_code == 401
        locked = phone.get("/api/live", headers={"Authorization": "Bearer " + code})
        assert locked.status_code == 429  # even the right code waits out the lock
    with TestClient(app, client=("192.168.178.78", 50000)) as other:
        assert other.get("/api/live", headers={"Authorization": "Bearer " + code}).status_code == 200


def test_guard_unlocks_after_timeout():
    now = [0.0]
    guard = AccessGuard(clock=lambda: now[0])
    for i in range(MAX_FAILURES):
        assert guard.check("ip", f"9999999{i}", "12345678") == "denied"
    assert guard.check("ip", "12345678", "12345678") == "locked"
    now[0] += 61
    assert guard.check("ip", "12345678", "12345678") == "ok"
    assert guard.check("ip", "", "") == "denied"  # empty code never matches


def test_new_code_invalidates_old_one(tmp_path):
    app, code = lan_app(tmp_path)
    new = TestClient(app, client=PC).post("/api/access-code").json()["code"]
    assert new != code and re.fullmatch(r"\d{8}", new)
    with TestClient(app, client=PHONE) as phone:
        assert phone.get("/api/live", headers={"Authorization": "Bearer " + code}).status_code == 401
        assert phone.get("/api/live", headers={"Authorization": "Bearer " + new}).status_code == 200


def next_of(ws, kind):
    """Next message of a type, skipping viewer-count updates."""
    while True:
        m = ws.receive_json()
        if m["type"] == kind:
            return m
        assert m["type"] == "viewers", m


def test_signaling_relay_routes_between_pc_sender_and_viewer(tmp_path):
    app = create_app(tmp_path, start_engine=False)
    with TestClient(app, client=PC) as pc:
        with pc.websocket_connect("/ws/onboard?role=viewer", subprotocols=["ac-agent"]) as viewer:
            welcome = viewer.receive_json()
            assert welcome["type"] == "welcome" and welcome["sender"] is False
            with pc.websocket_connect("/ws/onboard?role=sender", subprotocols=["ac-agent"]) as sender:
                assert sender.receive_json()["type"] == "welcome"
                counts = next_of(sender, "viewers")
                assert counts["count"] == 1 and counts["frames"] == 0
                sender.send_text(json.dumps({"type": "online"}))
                assert viewer.receive_json() == {"type": "sender-online"}
                assert next_of(sender, "viewers")["active"] is True
                viewer.send_text(json.dumps({"type": "request"}))
                request = next_of(sender, "request")
                assert request["type"] == "request" and request["from"] == welcome["id"]
                sender.send_text(json.dumps({"type": "offer", "to": welcome["id"], "sdp": {"type": "offer", "sdp": "v=0"}}))
                offer = viewer.receive_json()
                assert offer["type"] == "offer" and offer["sdp"]["sdp"] == "v=0"
                viewer.send_text(json.dumps({"type": "answer", "sdp": {"type": "answer", "sdp": "v=0"}}))
                assert next_of(sender, "answer")["from"] == welcome["id"]
                viewer.send_text(json.dumps({"type": "ice", "candidate": {"candidate": "c"}}))
                assert next_of(sender, "ice")["candidate"] == {"candidate": "c"}
                # Viewers cannot impersonate the sender.
                viewer.send_text(json.dumps({"type": "online"}))
                viewer.send_text("not json")
            assert viewer.receive_json() == {"type": "sender-offline"}


def test_only_pc_may_send_and_viewer_needs_code(tmp_path):
    app, code = lan_app(tmp_path)
    with TestClient(app, client=PHONE) as phone:
        with pytest.raises(WebSocketDisconnect):
            with phone.websocket_connect("/ws/onboard?role=sender", subprotocols=["ac-agent", code]) as ws:
                ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with phone.websocket_connect("/ws/onboard?role=viewer", subprotocols=["ac-agent"]) as ws:
                ws.receive_json()
        with phone.websocket_connect("/ws/onboard?role=viewer", subprotocols=["ac-agent", code]) as ws:
            assert ws.receive_json()["type"] == "welcome"


def test_second_pc_tab_without_picture_does_not_push_out_the_camera(tmp_path):
    """Regression: a newer dashboard tab without camera made the phone wait forever."""
    app = create_app(tmp_path, start_engine=False)
    with TestClient(app, client=PC) as pc:
        with pc.websocket_connect("/ws/onboard?role=sender", subprotocols=["ac-agent"]) as camera_tab:
            camera_tab.receive_json()
            camera_tab.send_text(json.dumps({"type": "online"}))
            with pc.websocket_connect("/ws/onboard?role=sender", subprotocols=["ac-agent"]) as empty_tab:
                empty_tab.receive_json()
                with pc.websocket_connect("/ws/onboard?role=viewer", subprotocols=["ac-agent"]) as phone:
                    assert phone.receive_json()["sender"] is True
                    phone.send_text(json.dumps({"type": "request"}))
                    # The request reaches the tab that has the camera.
                    assert next_of(camera_tab, "request")["type"] == "request"
                    empty_tab.send_text(json.dumps({"type": "offline"}))
                    phone.send_text(json.dumps({"type": "request"}))
                    assert next_of(camera_tab, "request")["type"] == "request"
            # Camera tab goes away: phone is told, then a remaining live tab takes over.
        assert app.state.relay.status()["sender_live"] is False


def test_frame_fallback_forwards_only_active_sender_frames(tmp_path):
    app = create_app(tmp_path, start_engine=False)
    jpeg = b"\xff\xd8" + b"x" * 1000 + b"\xff\xd9"
    with TestClient(app, client=PC) as pc:
        with pc.websocket_connect("/ws/onboard?role=sender", subprotocols=["ac-agent"]) as sender:
            sender.receive_json()
            with pc.websocket_connect("/ws/onboard?role=sender", subprotocols=["ac-agent"]) as other:
                other.receive_json()
                sender.send_text(json.dumps({"type": "online"}))
                with pc.websocket_connect("/ws/onboard?role=viewer", subprotocols=["ac-agent"]) as phone:
                    phone.receive_json()
                    phone.send_text(json.dumps({"type": "frames", "on": True}))
                    counts = next_of(sender, "viewers")
                    while counts["frames"] != 1:
                        counts = next_of(sender, "viewers")
                    assert counts["active"] is True
                    other.send_bytes(b"not the active tab")  # ignored
                    sender.send_bytes(jpeg)
                    assert phone.receive_bytes() == jpeg
                    assert app.state.relay.status()["frame_viewers"] == 1
