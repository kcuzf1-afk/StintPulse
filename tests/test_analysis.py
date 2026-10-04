import numpy as np
import pytest
from ac_agent.analysis import (
    normalize,
    compare,
    at,
    detect_events,
    coaching,
    track_map,
    lap_statistics,
    tyre_warnings,
)
from ac_agent.models import Settings, Tyre


def test_distance_alignment_different_capture_rates(recorded):
    meta, laps = recorded
    ref = laps[0]
    sparse = ref.model_copy(
        update={"samples": ref.samples[::3] + [ref.samples[-1]], "id": "sparse"}
    )
    result = compare(sparse, ref, meta.track_length)
    finite = [abs(v) for v in result["delta"] if v is not None]
    assert max(finite) < 0.08
    assert result["distance"][0] == 0 and result["distance"][-1] == 4200


def test_delta_endpoint_and_self_comparison(recorded):
    meta, laps = recorded
    assert compare(laps[0], laps[0], meta.track_length)["delta"][-1] == 0
    result = compare(laps[1], laps[0], meta.track_length)
    assert result["delta"][-1] == pytest.approx(
        (laps[1].duration_ms - laps[0].duration_ms) / 1000
    )
    assert result["delta"][-1] > 0


def test_missing_channel_stays_unavailable(recorded):
    meta, laps = recorded
    n = normalize(laps[0], meta.track_length)
    assert np.isnan(n["channels"]["engine_temp"]).all()
    assert at(n, "engine_temp", 100) is None


def test_capture_gap_not_interpolated(recorded):
    meta, laps = recorded
    l = laps[0]
    gap = l.model_copy(
        update={"samples": [s for s in l.samples if s.lap_pos < 0.3 or s.lap_pos > 0.5]}
    )
    n = normalize(gap, meta.track_length)
    assert at(n, "speed", 1680) is None


def test_braking_apex_full_throttle_events(recorded):
    meta, laps = recorded
    ev = detect_events(laps[0], meta.track_length, Settings())
    braking = [e for e in ev if e["type"] == "braking_zone"]
    apex = [e for e in ev if e["type"] == "apex"]
    assert len(braking) >= 6 and len(apex) == 8
    assert apex[0]["distance_m"] == pytest.approx(4200 * 0.095, abs=15)
    assert all(e["estimated"] for e in apex)
    assert any(e["type"] == "trail_braking" for e in ev)
    assert not any(e["type"] == "understeer_est" for e in ev)  # no calibration


def test_rule_coach_reports_measured_differences(recorded):
    meta, laps = recorded
    cfg = Settings()
    for l in laps:
        l.events = detect_events(l, meta.track_length, cfg)
    tips = coaching(laps[1], laps[0], meta.track_length, cfg)
    assert 1 <= len(tips) <= 5
    assert any(t["measurement"]["kind"] == "minimum_speed" for t in tips)
    for tip in tips:
        assert tip["potential_s"] is None or tip["potential_s"] >= 0
        assert tip["reference_id"] == laps[0].id
        assert tip["confidence"] in ("high", "medium", "low")
    assert coaching(laps[0], None, 4200, cfg) == []


def test_theoretical_lap_and_statistics(recorded):
    meta, laps = recorded
    stats = lap_statistics(laps, meta.track_length)
    expected = sum(min(l.sectors_ms[i] for l in laps) for i in range(3)) / 1000
    assert stats["theoretical_s"] == pytest.approx(expected)
    assert stats["combined_mini_s"] <= stats["best_s"] + 0.05
    assert stats["std_s"] > 0
    assert stats["fuel_effect"] is None  # insufficient laps


def test_track_map_from_coordinates(recorded):
    meta, laps = recorded
    track = track_map(laps[0], meta.track_length)
    assert track["complete"] and len(track["points"]) >= 600
    assert track["source"] == "demo" and len(track["sector_positions"]) == 2


def test_tyre_thresholds_configurable_and_unknown():
    wheels = [
        Tyre(corner="FL", core=120, pressure=18),
        Tyre(corner="FR", core=None),
        Tyre(corner="RL", core=60),
        Tyre(corner="RR", core=90),
    ]
    warnings = tyre_warnings(wheels, Settings())
    assert [w["temperature"] for w in warnings] == [
        "hot",
        "unavailable",
        "cold",
        "in_range",
    ]
    assert warnings[0]["pressure"] == "low"
    assert tyre_warnings(wheels, Settings(temp_hot=130))[0]["temperature"] == "in_range"


def test_mixed_demo_real_comparison_rejected(recorded):
    meta, laps = recorded
    other = laps[1].model_copy(deep=True)
    for s in other.samples:
        s.source = "ac"
    with pytest.raises(ValueError):
        compare(laps[0], other, meta.track_length)
