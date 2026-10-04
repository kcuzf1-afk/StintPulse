"""Distance-aligned analysis with explicit missing data and heuristic labels."""

from __future__ import annotations
import math
import numpy as np
from .models import Lap, Settings, json_safe


def normalize(lap: Lap, length: float, step=5.0):
    if length <= 0 or len(lap.samples) < 2:
        raise ValueError(
            "At least two samples and a measured track length are required"
        )
    dist = np.array([s.lap_pos * length for s in lap.samples], dtype=float)
    elapsed = np.array([s.lap_ms / 1000 for s in lap.samples], dtype=float)
    # Stable chronological filter: do not sort reversing/teleporting samples.
    keep = []
    furthest = -1.0
    last_time = -1.0
    for i, (d, t) in enumerate(zip(dist, elapsed)):
        if d > furthest + 1e-5 and t >= last_time:
            keep.append(i)
            furthest, last_time = d, t
    if len(keep) < 2:
        raise ValueError("No increasing lap-distance samples")
    dist, elapsed = dist[keep], elapsed[keep]
    grid = np.unique(np.r_[np.arange(0, length, step), length])

    def interpolate(values, discrete=False):
        vals = np.array([np.nan if v is None else v for v in values], dtype=float)[keep]
        usable = np.isfinite(vals)
        if usable.sum() < 2:
            return np.full(len(grid), np.nan)
        dx, dy = dist[usable], vals[usable]
        result = np.interp(grid, dx, dy, left=np.nan, right=np.nan)
        if discrete:
            idx = np.searchsorted(dx, grid, side="right") - 1
            result = dy[np.clip(idx, 0, len(dy) - 1)]
            result[(grid < dx[0]) | (grid > dx[-1])] = np.nan
        # Never bridge large recording holes or missing sensor spans.
        for a, b in zip(dx[:-1], dx[1:]):
            if b - a > 100:
                result[(grid > a) & (grid < b)] = np.nan
        return result

    keys = sorted({k for s in lap.samples for k in s.channels})
    channels = {
        k: interpolate([s.channels.get(k) for s in lap.samples], k == "gear")
        for k in keys
    }
    time_values = interpolate([s.lap_ms / 1000 for s in lap.samples])
    if "gas" in channels:
        dt = np.gradient(time_values)
        with np.errstate(divide="ignore", invalid="ignore"):
            channels["gas_rate"] = np.gradient(channels["gas"]) / np.where(
                dt > 0.001, dt, np.nan
            )
    if "fuel" in channels:
        channels["fuel_used"] = channels["fuel"][0] - channels["fuel"]
    # Suspension displacement derivative, never claimed as measured damper speed.
    for corner in ("FL", "FR", "RL", "RR"):
        key = f"travel_{corner}"
        if key in channels:
            with np.errstate(divide="ignore", invalid="ignore"):
                channels[f"travel_rate_est_{corner}"] = np.gradient(
                    channels[key]
                ) / np.where(
                    np.gradient(time_values) > 0.001, np.gradient(time_values), np.nan
                )
    return {
        "distance": grid,
        "time": time_values,
        "channels": channels,
        "x": interpolate([s.coords[0] for s in lap.samples]),
        "z": interpolate([s.coords[2] for s in lap.samples]),
        "length": length,
    }


def at(normalized, key, distance):
    values = normalized.get(key)
    if values is None:
        values = normalized["channels"].get(key)
    if values is None:
        return None
    value = float(
        np.interp(distance, normalized["distance"], values, left=np.nan, right=np.nan)
    )
    return value if math.isfinite(value) else None


def compare(lap, reference, length):
    if lap.samples[0].source != reference.samples[0].source:
        raise ValueError("Demo and measured laps must never be mixed")
    a, b = normalize(lap, length), normalize(reference, length)
    return json_safe(
        {
            "distance": a["distance"].tolist(),
            "delta": (a["time"] - b["time"]).tolist(),
            "line_difference_m": np.hypot(a["x"] - b["x"], a["z"] - b["z"]).tolist(),
            "lap": {
                "id": lap.id,
                "time": a["time"].tolist(),
                "channels": {k: v.tolist() for k, v in a["channels"].items()},
            },
            "reference": {
                "id": reference.id,
                "time": b["time"].tolist(),
                "channels": {k: v.tolist() for k, v in b["channels"].items()},
            },
            "map": {"x": a["x"].tolist(), "z": a["z"].tolist()},
            "reference_map": {"x": b["x"].tolist(), "z": b["z"].tolist()},
            "summary": {
                "lap_s": lap.duration_ms / 1000,
                "reference_s": reference.duration_ms / 1000,
                "delta_s": (lap.duration_ms - reference.duration_ms) / 1000,
            },
        }
    )


def _runs(mask, times, min_s=0.12):
    padded = np.r_[False, mask, False]
    starts = np.flatnonzero(np.diff(padded.astype(int)) == 1)
    ends = np.flatnonzero(np.diff(padded.astype(int)) == -1) - 1
    return [(a, b) for a, b in zip(starts, ends) if times[b] - times[a] >= min_s]


def detect_events(lap: Lap, length: float, cfg: Settings):
    n = normalize(lap, length)
    d, t, ch = n["distance"], n["time"], n["channels"]
    speed = ch.get("speed", np.zeros(len(d)))
    brake = ch.get("brake", np.zeros(len(d)))
    gas = ch.get("gas", np.zeros(len(d)))
    steer = ch.get("steer", np.zeros(len(d)))
    events = []

    def add(kind, a, b=None, **extra):
        b = a if b is None else b
        events.append(
            {
                "type": kind,
                "distance_m": round(float(d[a]), 1),
                "end_m": round(float(d[b]), 1),
                "time_s": float(t[a]),
                "estimated": False,
                **extra,
            }
        )

    for a, b in _runs(brake > cfg.brake_threshold, t):
        add("braking_zone", a, b, peak=float(np.nanmax(brake[a : b + 1])))
        add("brake_release", b)
        section = brake[a : b + 1]
        delta = np.diff(section)
        # Significant pedal reversals, rather than derivative noise at 60 Hz.
        movements = delta[np.abs(delta) > 0.035]
        reversals = (
            int(np.count_nonzero(movements[1:] * movements[:-1] < 0))
            if len(movements) > 1
            else 0
        )
        if reversals >= 4:
            add("unstable_braking", a, b, reversals=reversals)
    for a, b in _runs((brake > cfg.brake_threshold) & (np.abs(steer) > 0.08), t, 0.2):
        add("trail_braking", a, b, estimated=True)
    for a, b in _runs(np.abs(steer) > 0.1, t, 0.3):
        add("turn_in", a, b, estimated=True)
    for a, b in _runs(gas >= cfg.full_throttle, t, 0.3):
        add("full_throttle", a, b)
    for a, b in _runs(
        (gas < 0.8) & (brake < 0.03) & (np.abs(steer) < 0.07) & (speed > 50), t, 0.2
    ):
        add("lift_segment", a, b, estimated=True)
    gear = ch.get("gear")
    if gear is not None:
        for i in np.flatnonzero(np.diff(gear) != 0) + 1:
            if np.isfinite(gear[i]) and np.isfinite(gear[i - 1]):
                add("shift", i, gear=int(gear[i]))
    # Minimum-speed points: prominence >=15 km/h within a 200 m neighborhood.
    candidates = []
    for i in range(2, len(d) - 2):
        if (
            np.isfinite(speed[i])
            and speed[i] <= speed[i - 1]
            and speed[i] < speed[i + 1]
        ):
            radius = max(3, int(200 / (d[1] - d[0])))
            before = np.nanmax(speed[max(0, i - radius) : i + 1])
            after = np.nanmax(speed[i : min(len(d), i + radius)])
            if min(before, after) - speed[i] >= 15:
                if candidates and d[i] - d[candidates[-1]] < 80:
                    if speed[i] < speed[candidates[-1]]:
                        candidates[-1] = i
                else:
                    candidates.append(i)
    for num, i in enumerate(candidates, 1):
        add(
            "apex",
            i,
            corner=num,
            speed_kmh=float(speed[i]),
            estimated=True,
            basis="minimum-speed proxy; auto-numbered, not official corner labels",
        )
    for corner in ("FL", "FR", "RL", "RR"):
        for kind, attr in (("wheel_lock", "locked"), ("wheelspin", "spinning")):
            values = [
                next((getattr(w, attr) for w in s.tyres if w.corner == corner), None)
                for s in lap.samples
            ]
            times = np.array([s.lap_ms / 1000 for s in lap.samples])
            for a, b in _runs(np.array([v is True for v in values]), times, 0.12):
                events.append(
                    {
                        "type": kind,
                        "distance_m": lap.samples[a].lap_pos * length,
                        "end_m": lap.samples[b].lap_pos * length,
                        "time_s": float(times[a]),
                        "wheel": corner,
                        "estimated": True,
                    }
                )
        temp = ch.get(f"core_{corner}")
        if temp is not None:
            for a, b in _runs(temp > cfg.temp_hot, t, 2.0):
                add(
                    "tyre_overheat",
                    a,
                    b,
                    wheel=corner,
                    peak_c=float(np.nanmax(temp[a : b + 1])),
                )
    for s in lap.samples:
        if s.tyres_out >= 3 and (not events or events[-1]["type"] != "off_track"):
            events.append(
                {
                    "type": "off_track",
                    "distance_m": s.lap_pos * length,
                    "end_m": s.lap_pos * length,
                    "time_s": s.lap_ms / 1000,
                    "estimated": True,
                }
            )
    vx, vz = ch.get("local_vx"), ch.get("local_vz")
    if vx is not None and vz is not None:
        slip = np.degrees(np.arctan2(np.abs(vx), np.maximum(np.abs(vz), 0.01)))
        for a, b in _runs((slip > cfg.slip_angle_deg) & (speed > 40), t, 0.2):
            add(
                "body_slip",
                a,
                b,
                estimated=True,
                peak_deg=float(np.nanmax(slip[a : b + 1])),
            )
    if cfg.steering_lock_deg and cfg.wheelbase_m and cfg.steering_ratio:
        road = np.radians(steer * cfg.steering_lock_deg / cfg.steering_ratio)
        desired = np.abs(speed / 3.6 / cfg.wheelbase_m * np.tan(road))
        yaw = ch.get("yaw_rate", np.full(len(d), np.nan))
        ratio = np.abs(yaw) / np.maximum(desired, 0.01)
        turning = (np.abs(road) > 0.025) & (speed > 40) & (desired > 0.1)
        for kind, mask in (
            ("understeer_est", turning & (ratio < 0.6)),
            ("oversteer_est", turning & (ratio > 1.4)),
        ):
            for a, b in _runs(mask, t, 0.3):
                add(
                    kind,
                    a,
                    b,
                    estimated=True,
                    ratio=float(np.nanmedian(ratio[a : b + 1])),
                )
        if cfg.steering_yaw_sign:
            for a, b in _runs(
                turning & (yaw * steer * cfg.steering_yaw_sign < 0), t, 0.2
            ):
                add("countersteer_est", a, b, estimated=True)
    # Measured pedal correction; the early-throttle cause remains a hypothesis.
    for a, b in _runs((np.abs(steer) > 0.1) & (brake < 0.05), t, 0.5):
        segment = gas[a : b + 1]
        if (
            len(segment) > 4
            and np.nanmax(segment) > 0.7
            and np.any(np.diff(segment) < -0.08)
        ):
            add("throttle_correction", a, b, estimated=True)
    return sorted(json_safe(events), key=lambda e: e["distance_m"])


def track_map(lap, length):
    n = normalize(lap, length, step=max(5, length / 700))
    usable = np.isfinite(n["x"]) & np.isfinite(n["z"])
    if usable.sum() < 20:
        return None
    return json_safe(
        {
            "points": [
                [float(d / length), float(x), float(z)]
                for d, x, z in zip(
                    n["distance"][usable], n["x"][usable], n["z"][usable]
                )
            ],
            "sector_positions": lap.sector_positions,
            "events": lap.events,
            "generated_from": lap.id,
            "source": lap.samples[0].source,
            "complete": lap.complete,
            "length_m": length,
        }
    )


def covers_full_lap(lap, length, max_gap_m=100):
    """The car really drove line to line: no teleport, no hole in the trace.

    Older versions aborted laps at the line although they were driven in full;
    such laps still carry the whole track geometry."""
    pos = [s.lap_pos for s in lap.samples if not s.in_pit]
    if length <= 0 or len(pos) < 20 or pos[0] > 0.02 or pos[-1] < 0.98:
        return False
    for a, b in zip(pos[:-1], pos[1:]):
        if b + 0.08 < a or (b - a) * length > max_gap_m:
            return False
    return True


def corner_sections(lap, reference, length, cfg):
    a, b = normalize(lap, length), normalize(reference, length)
    ref_events = reference.events or detect_events(reference, length, cfg)
    apexes = [e for e in ref_events if e["type"] == "apex"]
    output = []
    for i, apex in enumerate(apexes):
        center = apex["distance_m"]
        start = (
            max(0, center - 180)
            if i == 0
            else (apexes[i - 1]["distance_m"] + center) / 2
        )
        end = (
            min(length, center + 220)
            if i == len(apexes) - 1
            else (center + apexes[i + 1]["distance_m"]) / 2
        )
        mask = (a["distance"] >= start) & (a["distance"] <= end)

        def metrics(n, events):
            braking = [
                e
                for e in events
                if e["type"] == "braking_zone" and start <= e["distance_m"] <= center
            ]
            full = [
                e
                for e in events
                if e["type"] == "full_throttle" and center <= e["distance_m"] <= end
            ]
            turn = [
                e
                for e in events
                if e["type"] == "turn_in" and start <= e["distance_m"] <= center
            ]
            release = [
                e
                for e in events
                if e["type"] == "brake_release" and start <= e["distance_m"] <= end
            ]
            vals = n["channels"].get("speed", np.full(len(mask), np.nan))[mask]
            st, en = at(n, "time", start), at(n, "time", end)
            br = n["channels"].get("brake", np.full(len(mask), np.nan))[mask]
            steering = n["channels"].get("steer", np.full(len(mask), np.nan))[mask]
            full_time = at(n, "time", full[0]["distance_m"]) if full else None
            apex_time = at(n, "time", center)
            return {
                "time_s": en - st if st is not None and en is not None else None,
                "entry_kmh": at(n, "speed", start),
                "minimum_kmh": float(np.nanmin(vals))
                if np.isfinite(vals).any()
                else None,
                "apex_kmh": at(n, "speed", center),
                "exit_kmh": at(n, "speed", end),
                "brake_start_m": braking[-1]["distance_m"] if braking else None,
                "brake_peak": float(np.nanmax(br)) if np.isfinite(br).any() else None,
                "brake_release_m": release[-1]["distance_m"] if release else None,
                "turn_in_m": turn[-1]["distance_m"] if turn else None,
                "full_throttle_m": full[0]["distance_m"] if full else None,
                "full_throttle_s": full_time - apex_time
                if full_time is not None and apex_time is not None
                else None,
                "steer_peak": float(np.nanmax(np.abs(steering)))
                if np.isfinite(steering).any()
                else None,
                "shifts": [
                    e
                    for e in events
                    if e["type"] == "shift" and start <= e["distance_m"] <= end
                ],
            }

        ma, mb = metrics(a, lap.events), metrics(b, ref_events)
        loss = (
            ma["time_s"] - mb["time_s"]
            if ma["time_s"] is not None and mb["time_s"] is not None
            else None
        )
        output.append(
            {
                "corner": i + 1,
                "start_m": start,
                "apex_m": center,
                "end_m": end,
                "estimated": True,
                "lap": ma,
                "reference": mb,
                "loss_s": loss,
            }
        )
    return json_safe(output)


def coaching(lap, reference, length, cfg):
    if not lap.complete or (reference and lap.id == reference.id):
        return []
    sections = (
        corner_sections(lap, reference, length, cfg)
        if reference and reference.complete
        else []
    )
    tips = []
    sample_rate = len(lap.samples) / max(1, lap.duration_ms / 1000)
    confidence = (
        "high"
        if sample_rate >= 30 and lap.valid and reference and reference.valid
        else "medium"
    )
    for c in sections:
        loss = c["loss_s"]
        if loss is None or loss < 0.05:
            continue
        a, b = c["lap"], c["reference"]
        measurement, cause, action = None, None, None
        if (
            a["brake_start_m"] is not None
            and b["brake_start_m"] is not None
            and b["brake_start_m"] - a["brake_start_m"] > 6
        ):
            measurement = {
                "kind": "brake_early",
                "difference_m": round(b["brake_start_m"] - a["brake_start_m"], 1),
            }
            cause = "Ein früherer Bremsbeginn könnte den gemessenen Abschnittsverlust erklären."
            action = "Bremsbeginn schrittweise an die Referenz annähern; Bremsweg und Stabilität prüfen."
        elif (
            a["minimum_kmh"] is not None
            and b["minimum_kmh"] is not None
            and b["minimum_kmh"] - a["minimum_kmh"] > 3
        ):
            measurement = {
                "kind": "minimum_speed",
                "difference_kmh": round(b["minimum_kmh"] - a["minimum_kmh"], 1),
            }
            cause = "Die niedrigere Mindestgeschwindigkeit könnte zum Abschnittsverlust beitragen."
            action = "Bremslösung und Fahrlinie vergleichen; die Kurvengeschwindigkeit schrittweise erhöhen."
        elif (
            a["full_throttle_s"] is not None
            and b["full_throttle_s"] is not None
            and a["full_throttle_s"] - b["full_throttle_s"] > 0.12
        ):
            measurement = {
                "kind": "throttle_late",
                "difference_s": round(a["full_throttle_s"] - b["full_throttle_s"], 3),
            }
            cause = (
                "Vollgas wird relativ zum Mindestgeschwindigkeitspunkt später erreicht."
            )
            action = "Fahrzeug früher für den Ausgang ausrichten; den Gasaufbau mit der Referenz vergleichen."
        else:
            measurement = {"kind": "section_loss", "difference_s": round(loss, 3)}
            cause = "Die gemessenen Daten zeigen einen Abschnittsverlust; eine eindeutige Ursache ist nicht belegt."
            action = "Geschwindigkeit, Pedale und Linie im markierten Abschnitt gemeinsam prüfen."
        tips.append(
            {
                "corner": c["corner"],
                "position_m": round(c["apex_m"], 1),
                "measurement": measurement,
                "cause": cause,
                "action": action,
                "observed_loss_s": round(loss, 3),
                "potential_s": round(max(0, loss), 3),
                "potential_basis": "upper bound: observed section loss, not a promised gain",
                "confidence": confidence,
                "corner_estimated": True,
                "reference_id": reference.id,
            }
        )
    for e in lap.events:
        if e["type"] == "tyre_overheat":
            tips.append(
                {
                    "corner": None,
                    "position_m": round(e["distance_m"], 1),
                    "measurement": {
                        "kind": "tyre_hot",
                        "wheel": e["wheel"],
                        "peak_c": round(e["peak_c"], 1),
                        "threshold_c": cfg.temp_hot,
                    },
                    "cause": "Die Kerntemperatur überschreitet den konfigurierten Grenzwert für mindestens zwei Sekunden.",
                    "action": "Grenzwert für diese Mischung prüfen; Schlupf, Druck und Lenkverlauf im Abschnitt vergleichen.",
                    "observed_loss_s": None,
                    "potential_s": None,
                    "potential_basis": "not measurable from temperature alone",
                    "confidence": "medium",
                    "corner_estimated": True,
                    "reference_id": reference.id if reference else None,
                }
            )
    return sorted(tips, key=lambda tip: tip.get("observed_loss_s") or 0, reverse=True)[
        :5
    ]


def lap_statistics(laps, length=0):
    valid = [l for l in laps if l.valid and l.complete]
    durations = np.array([l.duration_ms / 1000 for l in valid])
    if not valid:
        return {
            "count": 0,
            "best_s": None,
            "mean_s": None,
            "std_s": None,
            "theoretical_s": None,
            "sectors_s": [],
            "mini_sectors_s": [],
            "fuel_effect": None,
            "stint": [],
        }
    sectors = []
    for i in range(max(len(l.sectors_ms) for l in valid)):
        vals = [
            l.sectors_ms[i] / 1000
            for l in valid
            if i < len(l.sectors_ms) and l.sectors_ms[i] is not None
        ]
        sectors.append(min(vals) if vals else None)
    mini = []
    if length > 0:
        profiles = [normalize(l, length) for l in valid]
        edges = np.linspace(0, length, 21)
        for start, end in zip(edges[:-1], edges[1:]):
            vals = []
            for n in profiles:
                ta, tb = at(n, "time", start), at(n, "time", end)
                if ta is not None and tb is not None:
                    vals.append(tb - ta)
            mini.append(min(vals) if vals else None)
    med = float(np.median(durations))
    mad = float(np.median(np.abs(durations - med)))
    outliers = [
        l.id
        for l in valid
        if mad > 0.01 and abs(l.duration_ms / 1000 - med) > 3 * 1.4826 * mad
    ]
    fuel = [l.samples[0].channels.get("fuel") for l in valid]
    fuel_effect = None
    if len(valid) >= 8 and all(v is not None for v in fuel) and np.ptp(fuel) > 3:
        slope = float(np.polyfit(fuel, durations, 1)[0])
        fuel_effect = {
            "seconds_per_litre": slope,
            "estimated": True,
            "basis": "correlation only; tyre/driver effects uncontrolled",
        }
    return json_safe(
        {
            "count": len(valid),
            "best_s": float(np.min(durations)),
            "mean_s": float(np.mean(durations)),
            "std_s": float(np.std(durations)),
            "last_five_std_s": float(np.std(durations[-5:])),
            "outliers": outliers,
            "sectors_s": sectors,
            "theoretical_s": sum(sectors)
            if sectors and all(v is not None for v in sectors)
            else None,
            "mini_sectors_s": mini,
            "combined_mini_s": sum(mini)
            if mini and all(v is not None for v in mini)
            else None,
            "fuel_effect": fuel_effect,
            "stint": [
                {
                    "lap_id": l.id,
                    "lap": l.number,
                    "time_s": l.duration_ms / 1000,
                    "fuel_l": l.samples[0].channels.get("fuel"),
                    "tyres": {w.corner: w.core for w in l.samples[-1].tyres},
                }
                for l in valid
            ],
        }
    )


def tyre_warnings(tyres, cfg):
    return [
        {
            "wheel": w.corner,
            "temperature": "unavailable"
            if w.core is None
            else "cold"
            if w.core < cfg.temp_cold
            else "hot"
            if w.core > cfg.temp_hot
            else "in_range",
            "pressure": "unavailable"
            if w.pressure is None
            else "low"
            if w.pressure < cfg.pressure_low
            else "high"
            if w.pressure > cfg.pressure_high
            else "in_range",
            "locked_est": w.locked,
            "wheelspin_est": w.spinning,
        }
        for w in tyres
    ]


def summary_statistics(rows):
    """Archive statistics from compact lap summaries, never decompress all samples."""
    valid = sorted(
        [r for r in rows if r["valid"] and r["complete"]], key=lambda r: r["created_at"]
    )
    if not valid:
        return {
            "count": 0,
            "best_s": None,
            "mean_s": None,
            "std_s": None,
            "theoretical_s": None,
            "sectors_s": [],
            "mini_sectors_s": [],
            "fuel_effect": None,
            "stint": [],
        }
    times = np.array([r["duration_ms"] / 1000 for r in valid])
    sectors = []
    for i in range(max(len(r["sectors_ms"]) for r in valid)):
        values = [
            r["sectors_ms"][i] / 1000
            for r in valid
            if i < len(r["sectors_ms"]) and r["sectors_ms"][i] is not None
        ]
        sectors.append(min(values) if values else None)
    minis = []
    for i in range(20):
        vals = [
            r.get("analysis", {}).get("mini_sectors_s", [])[i]
            for r in valid
            if len(r.get("analysis", {}).get("mini_sectors_s", [])) == 20
        ]
        finite = [v for v in vals if v is not None]
        minis.append(min(finite) if finite else None)
    med = float(np.median(times))
    mad = float(np.median(np.abs(times - med)))
    fuel = [r.get("analysis", {}).get("fuel_start") for r in valid]
    fuel_effect = None
    if len(valid) >= 8 and all(v is not None for v in fuel) and np.ptp(fuel) > 3:
        fuel_effect = {
            "seconds_per_litre": float(np.polyfit(fuel, times, 1)[0]),
            "estimated": True,
            "basis": "correlation only; tyre/driver effects uncontrolled",
        }
    return json_safe(
        {
            "count": len(valid),
            "best_s": float(np.min(times)),
            "mean_s": float(np.mean(times)),
            "std_s": float(np.std(times)),
            "last_five_std_s": float(np.std(times[-5:])),
            "sectors_s": sectors,
            "theoretical_s": sum(sectors)
            if all(v is not None for v in sectors)
            else None,
            "mini_sectors_s": minis,
            "combined_mini_s": sum(minis)
            if all(v is not None for v in minis)
            else None,
            "outliers": [
                r["id"]
                for r in valid
                if mad > 0.01 and abs(r["duration_ms"] / 1000 - med) > 3 * 1.4826 * mad
            ],
            "fuel_effect": fuel_effect,
            "stint": [
                {
                    "lap_id": r["id"],
                    "lap": r["number"],
                    "time_s": r["duration_ms"] / 1000,
                    "fuel_l": r.get("analysis", {}).get("fuel_start"),
                    "tyres": r.get("analysis", {}).get("end_tyre_core", {}),
                }
                for r in valid
            ],
        }
    )
