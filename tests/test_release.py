"""Public release behaviour: name/version, update notice, data folder, start."""

import socket
import sys

import pytest
from fastapi.testclient import TestClient

from ac_agent import APP_NAME, __version__, updates
from ac_agent import __main__ as entry
from ac_agent import app as app_module
from ac_agent.app import create_app


def test_name_and_version_are_reported(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path, start_engine=False))
    health = client.get("/api/health").json()
    assert health["name"] == "StintPulse" == APP_NAME and health["version"] == __version__
    assert updates.UPDATE_REPO == "kcuzf1-afk/StintPulse"
    updates._cache.clear()
    asked = []
    monkeypatch.setattr(updates, "_latest", lambda repo: asked.append(repo) or {"version": "9.0.0", "url": "u", "published_at": None})
    info = client.get("/api/version").json()  # tests never contact GitHub
    assert asked == ["kcuzf1-afk/StintPulse"] and info["update"]["version"] == "9.0.0"
    assert info["download_url"] == "https://github.com/kcuzf1-afk/StintPulse/releases/latest"
    client.patch("/api/settings", json={"update_check": False})
    updates._cache.clear()
    assert client.get("/api/version").json()["update"] is None and asked == ["kcuzf1-afk/StintPulse"]
    monkeypatch.setattr(updates, "UPDATE_REPO", "")
    assert client.get("/api/version").json()["update_check"] is False


def test_update_notice_only_for_newer_releases():
    updates._cache.clear()
    calls = []

    def fetch(repo):
        calls.append(repo)
        return {"version": "99.0.0", "url": "https://github.com/x/y/releases/tag/v99.0.0", "published_at": None}

    found = updates.check(True, "x/y", fetch, now=1000)
    assert found["update"]["version"] == "99.0.0" and found["download_url"].endswith("/releases/latest")
    updates.check(True, "x/y", fetch, now=2000)
    assert calls == ["x/y"]  # cached, at most every 6 h
    assert updates.check(False, "x/y", fetch)["update"] is None  # switched off: no request
    assert calls == ["x/y"]
    updates._cache.clear()
    same = updates.check(True, "x/y", lambda r: {"version": __version__, "url": "", "published_at": None})
    assert same["update"] is None
    assert updates.newer("v0.10.0", "0.9.9") and not updates.newer("0.1.0", "0.1.0")


def test_offline_update_check_is_quiet():
    updates._cache.clear()

    def offline(repo):
        raise OSError("no network")

    info = updates.check(True, "x/y", offline, now=5000)
    assert info["update"] is None and info["error"] == "OSError"


def test_installed_data_folder_and_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("AC_AGENT_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert app_module.default_data_dir() == tmp_path / "StintPulse"
    (tmp_path / "AC Engineering Data Agent").mkdir()  # data of the version before the rename
    assert app_module.default_data_dir() == tmp_path / "AC Engineering Data Agent"
    (tmp_path / "StintPulse").mkdir()
    assert app_module.default_data_dir() == tmp_path / "StintPulse"


def test_start_detects_running_instance_and_busy_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert not entry.port_free("127.0.0.1", port)
        assert entry.running_instance(port) is None  # something else, not StintPulse
    assert entry.port_free("127.0.0.1", port)


def test_second_start_does_not_touch_the_running_app(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(entry, "running_instance", lambda port: "0.1.0")
    monkeypatch.setattr(entry.webbrowser, "open", opened.append)
    monkeypatch.setattr(entry, "create_app", lambda *a, **k: pytest.fail("must not open the data"))
    monkeypatch.setattr(sys, "argv", ["StintPulse", "--data-dir", str(tmp_path)])
    entry.main()
    assert "läuft bereits" in capsys.readouterr().out and opened == ["http://localhost:8765"]
