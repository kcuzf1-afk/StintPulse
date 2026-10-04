import threading
import time

from ac_agent.database import Database
from ac_agent.engine import Engine
from ac_agent.models import Settings
import ac_agent.engine as engine_module


def test_slow_lap_analysis_keeps_live_frames_available_and_session_snapshot(
    tmp_path, recorded, monkeypatch
):
    meta, laps = recorded
    db = Database(tmp_path / "worker.sqlite")
    engine = Engine(db, Settings(source="demo"))
    old_sid = db.new_session(meta)
    engine.meta, engine.session_id = meta, old_sid
    entered, release = threading.Event(), threading.Event()
    original = engine_module.normalize

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine_module, "normalize", delayed)
    try:
        engine._schedule_complete(
            laps[0].model_copy(update={"id": "worker-lap", "session_id": old_sid})
        )
        assert entered.wait(1)
        # A new session arrives while the previous lap is still being analyzed.
        new_meta = meta.model_copy(
            update={"track": "Another circuit", "track_length": 1200}
        )
        new_sid = db.new_session(new_meta)
        start = time.monotonic()
        with engine.lock:
            engine.meta, engine.session_id = new_meta, new_sid
            engine.latest = laps[0].samples[60]
        payload = engine.payload()
        assert time.monotonic() - start < 0.4
        assert payload["meta"]["track"] == "Another circuit"
        assert payload["sample"]["packet_id"] == laps[0].samples[60].packet_id
        release.set()
        engine.analyzer.shutdown(wait=True)
        saved = db.lap("worker-lap")
        assert saved.session_id == old_sid and saved.events
        assert len(db.list_laps(new_sid)) == 0
        assert db.get_map(meta) and db.get_map(new_meta) is None
        assert engine.reference is None and engine.map is None and engine.stats == {}
    finally:
        release.set()
        engine.analyzer.shutdown(wait=True)
        engine.writer.shutdown(wait=True)
        db.close()
