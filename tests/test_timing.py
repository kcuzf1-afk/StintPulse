"""Timing block for the lap-timing HUD (no second timing engine)."""

import time
import uuid

import pytest

from ac_agent.database import Database
from ac_agent.engine import Engine
from ac_agent.laps import LapRecorder
from ac_agent.models import Settings
from ac_agent.timing import (
    invalid_reasons,
    lap_snapshot,
    merge_best_sectors,
    sectors_known,
    timing_payload,
)


def lap_with(lap, **update):
    return lap.model_copy(update=update)


def test_invalid_reasons_only_proven_ones():
    assert invalid_reasons(["off_track_inferred", "partial_start", "pit_lap"]) == [
        "off_track_inferred"
    ]
    assert invalid_reasons(["penalty"]) == ["penalty"]
    assert invalid_reasons(["partial_start", "incomplete_distance"]) == []


def test_best_sectors_only_from_valid_complete_laps(recorded):
    _, laps = recorded
    a = lap_with(laps[0], sectors_ms=[30000, 40000, 50000], valid=True, complete=True)
    b = lap_with(laps[0], sectors_ms=[29000, 41000, None], valid=True, complete=True)
    bad = lap_with(laps[0], sectors_ms=[1, 1, 1], valid=False, complete=True)
    best = merge_best_sectors([], a)
    best = merge_best_sectors(best, b)
    best = merge_best_sectors(best, bad)
    assert best == [29000, 40000, 50000]


def test_snapshot_compares_with_values_before_the_lap(recorded):
    _, laps = recorded
    lap = lap_with(laps[0], sectors_ms=[29000, 41000, 50000], valid=True, complete=True)
    before = [30000, 40000, 50500]
    snap = lap_snapshot(lap, {"id": "r", "kind": "personal_best"}, before, 100.0)
    assert snap["best_sectors_ms"] == before  # not yet including this lap
    assert snap["sectors_ms"] == [29000, 41000, 50000]
    assert snap["valid"] is True and snap["invalid_reasons"] == []


def test_payload_uses_game_times_and_reports_age(recorded):
    meta, laps = recorded
    sample = laps[0].samples[60]
    recorder = LapRecorder("s", meta)
    recorder.sectors = {0: 31234}
    recorder.reasons = {"off_track_inferred"}
    last = lap_snapshot(lap_with(laps[0], sectors_ms=[1, 2, 3]), None, [], 100.0)
    t = timing_payload(sample, meta, recorder, last, None, [30000, None, None], 5, 102.5)
    assert t["lap_ms"] == sample.lap_ms and t["lap_number"] == sample.completed_laps + 1
    assert t["sectors_ms"] == [31234, None, None]
    assert t["invalid_reasons"] == ["off_track_inferred"]
    assert t["last_lap"]["age_ms"] == 2500 and "_completed_at" not in t["last_lap"]
    assert t["hold_ms"] == 5000 and t["sectors_known"] is True
    unknown = meta.model_copy(update={"unknown_fields": ["static.sectorCount=0 outside 1..20"]})
    assert sectors_known(unknown) is False
    assert timing_payload(None, meta, recorder, None, None, [], 5, 0) is None


def test_db_best_sectors_respect_comparison_group(tmp_path, recorded):
    meta, laps = recorded
    db = Database(tmp_path / "t.sqlite")
    sid = db.new_session(meta)
    other_sid = db.new_session(meta.model_copy(update={"car": "Other car"}))
    base = laps[0]
    db.save_lap(lap_with(base, id="a", session_id=sid, sectors_ms=[30000, 40000, 50000], valid=True, complete=True))
    db.save_lap(lap_with(base, id="b", session_id=sid, sectors_ms=[31000, 39000, 51000], valid=True, complete=True))
    db.save_lap(lap_with(base, id="c", session_id=sid, sectors_ms=[1000, 1000, 1000], valid=False, complete=True))
    db.save_lap(lap_with(base, id="d", session_id=other_sid, sectors_ms=[2000, 2000, 2000], valid=True, complete=True))
    assert db.best_sectors(meta) == [30000, 39000, 50000]
    db.close()


def test_engine_hud_state_resets_on_session_and_source_change(tmp_path, recorded):
    meta, laps = recorded
    db = Database(tmp_path / "e.sqlite")
    engine = Engine(db, Settings(source="demo", hud_hold_s=3))
    try:
        with engine.lock:
            engine.active_source = "demo"
            engine._new_session(meta)
            engine.latest = laps[0].samples[40]
            lap = lap_with(laps[0], sectors_ms=[30000, 40000, 50000], valid=True, complete=True)
            before = list(engine.best_sectors)
            engine._lap_done(lap)
        timing = engine.payload()["timing"]
        assert timing["hold_ms"] == 3000
        assert timing["last_lap"]["best_sectors_ms"] == before
        assert timing["last_lap"]["age_ms"] < 1000
        assert engine.best_sectors[0] is not None and engine.best_sectors[0] <= 30000
        # Session restart (new session): no stale completed lap shown.
        with engine.lock:
            engine._new_session(meta.model_copy(update={"track": "Other"}))
        assert engine.last_lap is None
        # Source switch: live view (and timing) is removed completely.
        engine._switch_source("ac")
        assert engine.payload()["timing"] is None
    finally:
        engine.analyzer.shutdown(wait=True)
        engine.writer.shutdown(wait=True)
        db.close()


def test_settings_hold_and_manual_compound_map():
    assert Settings().hud_hold_s == 5 and Settings().compound_map == {}
    Settings(compound_map={"Semislick (SM)": "M"})
    with pytest.raises(ValueError):
        Settings(compound_map={"Soft": "Super soft label"})
    with pytest.raises(ValueError):
        Settings(hud_hold_s=0)


def test_provisional_map_grows_over_laps_without_stacking(tmp_path, recorded):
    """Joining mid-lap: the map keeps growing over the line instead of starting
    again, and a later lap replaces points instead of drawing a second trace."""
    import queue
    from ac_agent.engine import MAP_BINS

    meta, laps = recorded
    db = Database(tmp_path / "m.sqlite")
    engine = Engine(db, Settings(source="demo", auto_save=False))
    engine.queue = queue.Queue()  # unbounded: the test feeds before processing
    try:
        engine.active_source = "demo"
        half = len(laps[0].samples) // 2
        # Second half of lap 1 (partial), then the first 70 % of lap 2.
        cut = int(len(laps[1].samples) * 0.7)
        for s in laps[0].samples[half::2] + laps[1].samples[:cut:2]:
            engine.queue.put((meta, s))
        engine.stop_event.set()  # _process drains the queue, then returns
        engine._process()
        assert len(engine.partial_map) <= MAP_BINS
        engine.map = None  # demo seeding stores a map; look at the live one
        points = engine.map_payload()["points"]
        positions = [p[0] for p in points]
        # Sorted along the lap, one point per position, whole lap covered.
        assert positions == sorted(positions) and len(set(positions)) == len(positions)
        assert positions[0] < 0.01 and positions[-1] > 0.99
        assert engine.map_payload()["coverage"] > 0.95
    finally:
        engine.analyzer.shutdown(wait=True)
        engine.writer.shutdown(wait=True)
        db.close()


def stored_lap(db, meta, lap, **update):
    """Store a lap the way older versions did (aborted at the line)."""
    sid = db.new_session(meta)
    samples = [s.model_copy(update={"source": meta.source}) for s in lap.samples[:-1]]
    db.save_lap(
        lap.model_copy(
            update={
                "id": str(uuid.uuid4()),
                "session_id": sid,
                "samples": samples,
                "complete": False,
                "valid": False,
                "reasons": ["incomplete_distance", "teleport_or_restart"],
                **update,
            }
        )
    )


def wait_for_map(engine, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        engine.analyzer.submit(lambda: None).result()
        if engine.map:
            return engine.map
        time.sleep(0.05)
    return None


def test_map_recovered_from_stored_full_lap(tmp_path, recorded):
    meta, laps = recorded
    meta = meta.model_copy(update={"source": "ac", "car": "other_car"})
    db = Database(tmp_path / "r.sqlite")
    stored_lap(db, meta, laps[0])
    engine = Engine(db, Settings(source="ac"))
    try:
        with engine.lock:
            engine._new_session(meta.model_copy(update={"car": "ks_car"}))
        recovered = wait_for_map(engine)
        assert recovered and recovered["complete"] and recovered["recovered"]
        assert recovered["source"] == "ac" and len(recovered["points"]) > 100
        assert db.get_map(meta) == recovered  # stored for the next start
    finally:
        engine.analyzer.shutdown(wait=True)
        engine.writer.shutdown(wait=True)
        db.close()


def test_no_map_recovered_from_lap_with_hole_or_partial_start(tmp_path, recorded):
    meta, laps = recorded
    meta = meta.model_copy(update={"source": "ac"})
    db = Database(tmp_path / "h.sqlite")
    n = len(laps[0].samples)
    holed = laps[0].samples[: n // 3] + laps[0].samples[n // 2 :]
    stored_lap(db, meta, laps[0], samples=holed)
    stored_lap(db, meta, laps[0], samples=laps[0].samples[n // 4 :])
    engine = Engine(db, Settings(source="ac"))
    try:
        with engine.lock:
            engine._new_session(meta)
        assert wait_for_map(engine, timeout=1) is None
        assert db.get_map(meta) is None
    finally:
        engine.analyzer.shutdown(wait=True)
        engine.writer.shutdown(wait=True)
        db.close()
