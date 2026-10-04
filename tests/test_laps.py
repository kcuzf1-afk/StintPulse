from ac_agent.laps import LapRecorder
from ac_agent.demo import DemoSource


def feed(recorded, mutate=None, start=0):
    meta, laps = recorded
    r = LapRecorder("s", meta)
    for i, s in enumerate(laps[0].samples[start:-1]):
        if mutate:
            s = mutate(s, i)
        assert r.process(s) is None
    end = DemoSource().make_sample(0.0001, 1, 5)
    end = end.model_copy(update={"last_lap_ms": laps[0].duration_ms})
    return r, r.process(end)


def test_start_finish_and_official_sector_times(recorded):
    r, l = feed(recorded)
    assert l and l.complete and l.valid and l.number == 1
    assert len(l.sectors_ms) == 3 and all(v > 0 for v in l.sectors_ms)
    assert sum(l.sectors_ms) == l.duration_ms
    assert l.samples[-1].synthetic_boundary
    assert r.samples[0].lap_pos == 0


def test_off_track_invalid(recorded):
    _, lap = feed(
        recorded, lambda s, i: s.model_copy(update={"tyres_out": 3 if i == 40 else 0})
    )
    assert not lap.valid and "off_track_inferred" in lap.reasons


def test_pit_and_penalty_invalid(recorded):
    _, lap = feed(
        recorded,
        lambda s, i: s.model_copy(
            update={"in_pit": i == 30, "penalty_s": 1 if i == 20 else 0}
        ),
    )
    assert not lap.valid and {"pit_lap", "penalty"} <= set(lap.reasons)


def test_join_midlap_is_partial(recorded):
    _, lap = feed(recorded, start=80)
    assert not lap.valid and not lap.complete and "partial_start" in lap.reasons


def test_pause_replay_not_recorded(recorded):
    meta, laps = recorded
    r = LapRecorder("s", meta)
    r.process(laps[0].samples[0])
    before = len(r.samples)
    for status in (0, 1, 3):
        r.process(laps[0].samples[1].model_copy(update={"status": status}))
    assert len(r.samples) == before


def test_teleport_and_shutdown(recorded):
    meta, laps = recorded
    r = LapRecorder("s", meta)
    for s in laps[0].samples[:100]:
        r.process(s)
    lap = r.process(laps[0].samples[0])
    assert lap and not lap.valid and "teleport_or_restart" in lap.reasons
    assert not r.abort("application_closed").valid


def line_sample(lap, pos, ms, completed, **extra):
    """A frame at the start/finish line, derived from the lap's last real sample.
    Once the counter has changed, AC reports the first sector again."""
    sector = lap.samples[-2].sector_index if completed == lap.samples[-2].completed_laps else 0
    return lap.samples[-2].model_copy(
        update={
            "lap_pos": pos,
            "lap_ms": ms,
            "completed_laps": completed,
            "sector_index": sector,
            **extra,
        }
    )


def test_spline_wraps_before_lap_counter(recorded):
    """Real AC (Sepang, 1.16): position 0.999 -> 0.000 a few ms before the counter."""
    meta, laps = recorded
    lap = laps[0]
    r = LapRecorder("s", meta)
    for s in lap.samples[:-1]:
        assert r.process(s) is None
    end_ms = lap.samples[-2].lap_ms
    for dt in (16, 32):
        assert r.process(line_sample(lap, 0.0002, end_ms + dt, 0)) is None
        assert not r.accepted
    done = r.process(
        line_sample(lap, 0.0004, 20, 1, last_lap_ms=lap.duration_ms)
    )
    assert done and done.complete and done.valid, done and done.reasons
    assert done.duration_ms == lap.duration_ms
    assert done.samples[-1].lap_pos == 1.0 and done.samples[-1].synthetic_boundary
    # The next lap starts cleanly at the line, so it can become a full lap.
    assert r.complete and r.samples[0].lap_pos == 0 and not r.reasons


def test_lap_counter_before_spline_wrap(recorded):
    meta, laps = recorded
    lap = laps[0]
    r = LapRecorder("s", meta)
    for s in lap.samples[:-1]:
        r.process(s)
    done = r.process(line_sample(lap, 0.9997, 12, 1, last_lap_ms=lap.duration_ms))
    assert done and done.complete and done.valid
    # Last metres before the wrap belong to no lap and are not recorded.
    assert r.process(line_sample(lap, 0.9999, 28, 1)) is None and not r.accepted
    r.process(line_sample(lap, 0.0003, 44, 1))
    assert r.accepted and [s.lap_pos for s in r.samples] == [0.0, 0.0003]
    # Driving on: the following lap completes normally.
    for s in laps[1].samples[1:-1]:
        assert r.process(s) is None
    nxt = r.process(line_sample(laps[1], 0.0002, 16, 2, last_lap_ms=laps[1].duration_ms))
    assert nxt and nxt.complete and nxt.valid, nxt and nxt.reasons


def test_spline_wrap_without_lap_counter_is_not_a_lap(recorded):
    meta, laps = recorded
    lap = laps[0]
    r = LapRecorder("s", meta)
    for s in lap.samples[:-1]:
        r.process(s)
    end_ms = lap.samples[-2].lap_ms
    assert r.process(line_sample(lap, 0.001, end_ms + 16, 0)) is None
    aborted = r.process(line_sample(lap, 0.02, end_ms + 4000, 0))
    assert aborted and not aborted.complete
    assert "spline_wrap_without_lap_counter" in aborted.reasons


def test_clock_and_spline_reset_one_frame_before_counter(recorded):
    """Exact order measured in AC 1.16 at Sepang: position, iCurrentTime and
    iLastTime reset together, completedLaps follows one frame later."""
    meta, laps = recorded
    lap = laps[0]
    r = LapRecorder("s", meta)
    for s in lap.samples[:-1]:
        r.process(s)
    assert r.process(line_sample(lap, 0.00042, 8, 0, last_lap_ms=lap.duration_ms)) is None
    assert r.process(line_sample(lap, 0.00042, 8, 0, last_lap_ms=lap.duration_ms)) is None
    done = r.process(line_sample(lap, 0.00073, 32, 1, last_lap_ms=lap.duration_ms))
    assert done and done.complete and done.valid, done and done.reasons
    assert done.duration_ms == lap.duration_ms
    assert r.complete and not r.reasons
