"""Timing view for the lap-timing HUD.

No second timing engine: every time comes from the game (iCurrentTime,
iLastTime, lastSectorTime) via the existing LapRecorder. This module only
packages that data plus the comparison values (reference lap, personal sector
bests) into one JSON block that the dashboard renders.
"""

# Reasons that make a lap provably invalid in the HUD (red). Both are derived
# from game fields (numberOfTyresOut >= 3, penaltyTime > 0); original AC has no
# official isValidLap flag. Other reasons (joined mid-lap, pit, capture gap)
# make a lap unusable as a best, but are not shown as "invalid" (red).
INVALID_REASONS = {"off_track_inferred", "penalty"}


def invalid_reasons(reasons):
    return sorted(r for r in reasons if r in INVALID_REASONS)


def sectors_known(meta):
    """False when the game reported no plausible sector count (see unknown_fields)."""
    return not any(u.startswith("static.sectorCount") for u in meta.unknown_fields)


def merge_best_sectors(best, lap):
    """Per-sector minimum over valid, complete laps (ms, None = unknown)."""
    if not lap.valid or not lap.complete:
        return best
    sectors = lap.sectors_ms
    size = max(len(best), len(sectors))
    merged = []
    for i in range(size):
        a = best[i] if i < len(best) else None
        b = sectors[i] if i < len(sectors) else None
        merged.append(b if a is None else a if b is None else min(a, b))
    return merged


def reference_info(lap, kind):
    if lap is None:
        return None
    return {
        "id": lap.id,
        "kind": kind,  # "selected" | "personal_best"
        "number": lap.number,
        "duration_ms": lap.duration_ms,
        "sectors_ms": list(lap.sectors_ms),
    }


def lap_snapshot(lap, reference, best_before, completed_at):
    """Frozen view of a just-completed lap, compared with the values BEFORE it."""
    return {
        "number": lap.number,
        "duration_ms": lap.duration_ms,
        "sectors_ms": list(lap.sectors_ms),
        "valid": lap.valid,
        "complete": lap.complete,
        "reasons": list(lap.reasons),
        "invalid_reasons": invalid_reasons(lap.reasons),
        "reference": reference,
        "best_sectors_ms": list(best_before),
        "_completed_at": completed_at,  # monotonic seconds, converted to age
    }


def timing_payload(sample, meta, recorder, last_lap, reference, best, hold_s, now):
    if sample is None or meta is None:
        return None
    count = meta.sector_count
    current = [None] * count
    reasons = []
    started_at_line = False
    if recorder is not None:
        for i, value in recorder.sectors.items():
            if 0 <= i < count:
                current[i] = value
        reasons = sorted(recorder.reasons)
        started_at_line = bool(recorder.complete)
    last = None
    if last_lap is not None:
        last = {k: v for k, v in last_lap.items() if not k.startswith("_")}
        last["age_ms"] = max(0, round((now - last_lap["_completed_at"]) * 1000))
    return {
        "lap_number": sample.completed_laps + 1,
        "lap_ms": sample.lap_ms,
        "game_status": sample.status,
        "sector_index": sample.sector_index,
        "sector_count": count,
        "sectors_known": sectors_known(meta),
        "sectors_ms": current,
        "started_at_line": started_at_line,
        "reasons": reasons,
        "invalid_reasons": invalid_reasons(reasons),
        "last_lap": last,
        "reference": reference,
        "best_sectors_ms": list(best),
        "hold_ms": round(hold_s * 1000),
        "resolution_ms": 1,
    }
