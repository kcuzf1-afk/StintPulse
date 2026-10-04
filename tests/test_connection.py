"""Connection state machine of the original-AC reader (platform independent)."""

import pytest

from ac_agent.models import Settings
from ac_agent.shared_memory import ACSource
from ac_agent.diagnostics import hints
from ac_fake import Clock, FakeGame, make_source

CFG = Settings()


def test_page_names_match_original_ac():
    # Kunos/mdjarv/Rombik all use the plural "acpmf_graphics". The old name
    # "acpmf_graphic" never exists, so the reader could never connect.
    assert [n for n, _ in ACSource.names] == [
        "acpmf_physics",
        "acpmf_graphics",
        "acpmf_static",
    ]


def test_present_pages_deliver_real_samples():
    game = FakeGame().start()
    source = make_source(game)
    meta, sample = source.read(CFG)
    assert meta.source == "ac" and sample.source == "ac"
    assert sample.channels["speed"] == pytest.approx(183.4)
    assert source.state == "connected"
    diag = source.diagnostics()
    assert [p["name"] for p in diag["pages"]] == [n for n, _ in ACSource.names]
    for page in diag["pages"]:
        assert page["present"] and page["readable"] and page["initialized"]
        assert page["read_bytes"] == page["expected_bytes"]
        assert page["read_only"] is True
    assert diag["physics_packet_id"] == sample.packet_id
    assert diag["game_status"]["name"] == "AC_LIVE"


def test_missing_pages_wait_without_fake_data():
    game = FakeGame()
    game.process_status = "not_found"
    game.launcher = ["content manager.exe"]
    source = make_source(game)
    assert source.read(CFG) is None
    assert source.state == "waiting_game" and not source.maps
    assert not any(p["present"] for p in source.diagnostics()["pages"])
    # Only one page missing (e.g. a wrong name) is reported per page.
    game.start()
    del game.pages["acpmf_graphics"]
    game.process_status = "found"
    source.clock.advance(1.1)
    assert source.read(CFG) is None
    pages = {p["name"]: p for p in source.diagnostics()["pages"]}
    assert pages["acpmf_physics"]["present"] and not pages["acpmf_graphics"]["present"]
    assert pages["acpmf_graphics"]["error_code"] == 2
    assert source.state == "waiting_session"
    assert not game.open_pages  # partially opened pages are released again


def test_launcher_alone_is_not_a_session():
    game = FakeGame()
    game.process_status = "not_found"
    game.launcher = ["assettocorsa.exe"]
    source = make_source(game)
    source.read(CFG)
    report = _report(source)
    assert any("Launcher" in h["de"] for h in hints(report))


def test_failed_process_check_does_not_block_readable_pages():
    for status in ("unavailable", "not_found"):
        game = FakeGame().start()
        game.process_status = status
        source = make_source(game)
        frame = source.read(CFG)
        assert frame is not None and source.state == "connected"
        assert source.diagnostics()["process"]["status"] == status


def test_access_denied_is_diagnosed_and_only_then_admin_is_suggested():
    game = FakeGame().start()
    game.denied.add("acpmf_static")
    source = make_source(game)
    assert source.read(CFG) is None
    assert source.state == "access_denied"
    text = " ".join(h["de"] for h in hints(_report(source, "access_denied")))
    assert "Administrator" in text
    # Missing game: no admin advice.
    other = make_source(FakeGame())
    other.read(CFG)
    text = " ".join(h["de"] for h in hints(_report(other)))
    assert "Administrator" not in text


def test_uninitialized_static_page_waits_then_connects():
    game = FakeGame().start(with_static=False)
    clock = Clock()
    source = make_source(game, clock)
    assert source.read(CFG) is None
    assert source.state == "not_initialized"
    pages = {p["name"]: p for p in source.diagnostics()["pages"]}
    assert pages["acpmf_static"]["present"] and not pages["acpmf_static"]["initialized"]
    game.start()  # session finished loading
    game.set_packets(10, 11)
    clock.advance(0.02)
    assert source.read(CFG) is not None and source.state == "connected"


def test_unchanged_packet_ids_are_not_recorded_twice_and_become_stale():
    game = FakeGame().start()
    clock = Clock()
    source = make_source(game, clock)
    assert source.read(CFG) is not None
    clock.advance(0.5)
    assert source.read(CFG) is None and source.state == "connected"
    clock.advance(2.0)
    assert source.read(CFG) is None and source.state == "stale"
    assert source.diagnostics()["last_update_age_s"] >= 2.0
    # Game still running: handles are kept open while stale.
    clock.advance(10)
    source.read(CFG)
    assert source.maps


def test_pause_then_resume():
    game = FakeGame().start()
    clock = Clock()
    source = make_source(game, clock)
    game.process_status = "unavailable"  # must not trigger a release while paused
    assert source.read(CFG)[1].status == 2
    game.set_status(3)
    game.set_packets(501, 502)
    clock.advance(0.1)
    assert source.read(CFG)[1].status == 3
    for _ in range(5):
        clock.advance(3)
        assert source.read(CFG) is None
        assert source.state == "paused" and source.maps
    game.set_status(2)
    game.set_packets(503, 504)
    clock.advance(0.1)
    frame = source.read(CFG)
    assert frame[1].status == 2 and source.state == "connected"


def test_game_restart_reconnects_automatically():
    game = FakeGame().start()
    clock = Clock()
    source = make_source(game, clock)
    assert source.read(CFG) is not None
    first_connects = source.connects
    game.quit()  # acs.exe ends; our view still shows the last values
    for _ in range(4):
        clock.advance(2)
        source.read(CFG)
    assert source.state == "waiting_game" and not source.maps
    assert not game.open_pages
    clock.advance(2)
    assert source.read(CFG) is None and source.state == "waiting_game"
    game.start()
    game.process_status = "found"
    game.set_packets(1, 1)
    clock.advance(1.1)
    frame = source.read(CFG)
    assert frame is not None and source.state == "connected"
    assert source.connects == first_connects + 1


def test_decode_error_is_reported_and_reading_continues():
    game = FakeGame().start()
    clock = Clock()
    source = make_source(game, clock)
    game.set_status(9)  # not an AC_STATUS value
    assert source.read(CFG) is None
    assert source.state == "decode_error"
    assert "graphics.status=9" in source.diagnostics()["last_decode_error"]
    assert source.decode_errors == 1 and source.maps
    game.set_status(2)
    game.set_packets(800, 801)
    clock.advance(0.1)
    assert source.read(CFG) is not None and source.state == "connected"


def test_unusual_mod_metadata_is_marked_unknown_not_blocking():
    game = FakeGame().start()
    game.set_static_int("sectorCount", 0)
    game.set_static_int("maxRpm", 90000)
    source = make_source(game)
    meta, sample = source.read(CFG)
    assert meta.sector_count == 1 and meta.max_rpm == 0
    assert any("sectorCount=0" in u for u in meta.unknown_fields)
    assert any("maxRpm=90000" in u for u in meta.unknown_fields)
    assert sample.channels["speed"] == pytest.approx(183.4)
    assert source.diagnostics()["unknown_fields"] == meta.unknown_fields


def test_request_reconnect_reopens_immediately():
    game = FakeGame().start()
    clock = Clock()
    source = make_source(game, clock)
    source.read(CFG)
    opens = game.opens
    source.request_reconnect()
    assert not source.maps
    game.set_packets(900, 901)
    assert source.read(CFG) is not None  # no 1 s back-off after a manual re-check
    assert game.opens == opens + 3


def test_missing_game_reconnects_at_most_once_per_second():
    game = FakeGame()
    game.process_status = "not_found"
    clock = Clock()
    source = make_source(game, clock)
    probes = []
    source.process_probe = lambda: probes.append(1) or game.probe()
    for step in (0, 0.5, 0.6):
        clock.advance(step)
        assert source.read(CFG) is None
    assert len(probes) == 2


def _report(source, status=None):
    return {
        "status": status or source.state,
        "system": {"windows": True},
        "game": source.diagnostics(),
        "websocket": {"clients": 1},
    }
