"""Telemetry key figures for the setup assistant.

Only representative laps are used: incomplete laps, pit (out/in) laps,
invalid laps, laps with damage, data gaps or a spin and clear lap-time
outliers are excluded - always with a visible reason, never silently.
Handling figures are indicators (combined wheel slip, body slip angle,
steering per lateral g), not a measurement of under-/oversteer; they are
linked to the driver's feedback with an explicit verdict.
"""
from __future__ import annotations

import math
import warnings
from typing import Literal

import numpy as np
from pydantic import Field

from .analysis import detect_events
from .models import Lap, StrictModel, json_safe

MAX_LAPS = 20
GOALS = {
    "qualifying": "Qualifying (eine schnelle Runde)",
    "race": "Rennen (Reifen und Konstanz über den Stint)",
    "consistency": "Konstanz",
    "custom": "Individuell",
}
ISSUES = {
    "understeer": "Untersteuern",
    "oversteer": "Übersteuern",
    "lockup": "Blockierende Räder",
    "wheelspin": "Durchdrehende Räder / Traktion",
    "unstable_braking": "Unruhig beim Bremsen",
    "bottoming": "Aufsetzen",
    "kerbs": "Unruhig über Randsteine",
    "tyre_temps": "Reifentemperaturen",
    "instability": "Instabil / nervös",
    "other": "Sonstiges",
}
PHASES = {
    "braking": "Bremsen",
    "entry": "Kurveneingang",
    "mid": "Kurvenmitte",
    "exit": "Kurvenausgang",
    "lowspeed": "Langsame Kurven",
    "highspeed": "Schnelle Kurven",
    "straight": "Gerade",
    "general": "Allgemein",
}
CORNERS = ("FL", "FR", "RL", "RR")


class FeedbackItem(StrictModel):
    issue: Literal[tuple(ISSUES)]  # type: ignore[valid-type]
    phase: Literal[tuple(PHASES)]  # type: ignore[valid-type]
    corners: str = Field("", max_length=80)
    severity: Literal[1, 2, 3] = 2


class Feedback(StrictModel):
    items: list[FeedbackItem] = Field(default_factory=list, max_length=8)
    text: str = Field("", max_length=1000)


AVAILABILITY = (
    ("speed", "Geschwindigkeit", ("speed",)),
    ("pedals", "Gas und Bremse", ("gas", "brake")),
    ("steer", "Lenkung", ("steer",)),
    ("g_lat", "Querbeschleunigung", ("g_lat",)),
    ("yaw", "Gierrate", ("yaw_rate",)),
    ("body_slip", "Schwimmwinkel (Fahrzeuggeschwindigkeit längs/quer)", ("local_vx", "local_vz")),
    ("wheel_slip", "Radschlupf je Rad", tuple(f"slip_raw_{c}" for c in CORNERS)),
    ("tyre_temp", "Reifentemperatur innen/mitte/außen", tuple(f"{k}_{c}" for k in ("inner", "middle", "outer") for c in CORNERS)),
    ("tyre_core", "Reifenkerntemperatur", tuple(f"core_{c}" for c in CORNERS)),
    ("pressure", "Reifendruck", tuple(f"pressure_{c}" for c in CORNERS)),
    ("wear", "Reifenverschleiß", tuple(f"wear_raw_{c}" for c in CORNERS)),
    ("ride", "Bodenfreiheit vorne/hinten", ("ride_front", "ride_rear")),
    ("travel", "Federweg", tuple(f"travel_{c}" for c in CORNERS)),
    ("brake_temp", "Bremstemperatur", tuple(f"brake_temp_{c}" for c in CORNERS)),
    ("damage", "Schaden", tuple(f"damage_{i}" for i in range(5))),
)
NEEDED = sorted({k for _, _, keys in AVAILABILITY for k in keys} | {"fuel", "air_temp", "road_temp"})


def profile(lap: Lap, length: float, keys, step=5.0):
    """Distance-aligned channels (like analysis.normalize, needed keys only)."""
    dist = np.array([s.lap_pos * length for s in lap.samples], dtype=float)
    elapsed = np.array([s.lap_ms / 1000 for s in lap.samples], dtype=float)
    keep, furthest, last = [], -1.0, -1.0
    for i, (d, t) in enumerate(zip(dist, elapsed)):
        if d > furthest + 1e-5 and t >= last:
            keep.append(i)
            furthest, last = d, t
    if len(keep) < 2:
        raise ValueError("No increasing lap-distance samples")
    dist, elapsed = dist[keep], elapsed[keep]
    grid = np.unique(np.r_[np.arange(0, length, step), length])

    def interp(values):
        vals = np.array([np.nan if v is None else v for v in values], dtype=float)[keep]
        ok = np.isfinite(vals)
        if ok.sum() < 2:
            return np.full(len(grid), np.nan)
        out = np.interp(grid, dist[ok], vals[ok], left=np.nan, right=np.nan)
        for a, b in zip(dist[ok][:-1], dist[ok][1:]):
            if b - a > 100:
                out[(grid > a) & (grid < b)] = np.nan
        return out

    ch = {k: interp([s.channels.get(k) for s in lap.samples]) for k in keys}
    return grid, interp([s.lap_ms / 1000 for s in lap.samples]), ch


def finite_median(values):
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return float(np.median(vals)) if vals else None


def rounded(value, digits=2):
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def screen(laps: list[Lap], length: float, cfg, include_invalid=False):
    """Split laps into representative and excluded ones (with reasons).
    include_invalid: track-limit laps count, but stay visibly marked."""
    rows = []
    for lap in laps:
        reasons, notes = [], []
        limits = notes if include_invalid else reasons
        if not lap.complete:
            reasons.append("unvollständig")
        if any(s.in_pit for s in lap.samples):
            reasons.append("Boxengasse (Out-/Inlap)")
        if not lap.valid:
            limits.append("ungültig" + (f" ({', '.join(lap.reasons[:2])})" if lap.reasons else ""))
        if len(lap.samples) < 50:
            reasons.append("zu wenige Messpunkte")
        gaps = [b.lap_ms - a.lap_ms for a, b in zip(lap.samples, lap.samples[1:])]
        if gaps and max(gaps) > 1000:
            reasons.append("Datenlücke über 1 s")
        damage = [
            max((s.channels.get(f"damage_{i}") or 0) for i in range(5)) for s in lap.samples
        ]
        if damage and max(damage) - damage[0] > 0.01:
            reasons.append("Schaden während der Runde (Unfall)")
        events = lap.events
        if not events and length > 0 and len(lap.samples) >= 2:
            try:
                events = detect_events(lap, length, cfg)
            except ValueError:
                events = []
        if any(e["type"] == "off_track" for e in events):
            limits.append("Strecke verlassen")
        if any(e["type"] == "body_slip" and (e.get("peak_deg") or 0) > 25 for e in events):
            reasons.append("Dreher / starker Rutscher")
        if include_invalid and notes:
            notes.append("auf Wunsch einbezogen")
        rows.append({"lap": lap, "events": events, "reasons": reasons, "notes": notes})
    clean = [r for r in rows if not r["reasons"]]
    if len(clean) >= 3:
        times = np.array([r["lap"].duration_ms / 1000 for r in clean])
        med = float(np.median(times))
        mad = float(np.median(np.abs(times - med)))
        for r in clean:
            t = r["lap"].duration_ms / 1000
            if t > med * 1.03 and t - med > max(3 * 1.4826 * mad, 0.5):
                r["reasons"].append(f"Zeit-Ausreißer (+{t - med:.1f} s zum Median)")
    return rows


def corner_windows(reference_events, length):
    apexes = sorted(e["distance_m"] for e in reference_events if e["type"] == "apex")
    windows = []
    for i, apex in enumerate(apexes):
        before = apexes[i - 1] if i else max(0.0, apex - 300)
        after = apexes[i + 1] if i + 1 < len(apexes) else min(length, apex + 300)
        windows.append(
            {
                "apex": apex,
                "entry": (max(before + (apex - before) * 0.4, apex - 150), apex - 10),
                "mid": (apex - 10, apex + 10),
                "exit": (apex + 10, min(apex + (after - apex) * 0.6, apex + 150)),
            }
        )
    return windows


def phase_figures(grid, ch, window):
    with warnings.catch_warnings():  # all-NaN slices are expected (missing channels)
        warnings.simplefilter("ignore", RuntimeWarning)
        return _phase_figures(grid, ch, window)


def _phase_figures(grid, ch, window):
    lo, hi = window
    mask = (grid >= lo) & (grid <= hi)
    speed = ch["speed"][mask]
    g_lat = np.abs(ch["g_lat"][mask])
    turning = np.isfinite(speed) & (speed > 30) & np.isfinite(g_lat) & (g_lat > 0.3)
    if turning.sum() < 2:
        return None
    out = {}
    # Wheel slip is combined (longitudinal + lateral): braking and throttle
    # dominate it, so the front/rear balance only uses sections with neither.
    coast = turning & (np.nan_to_num(ch["brake"][mask], nan=1) < 0.05) & (np.nan_to_num(ch["gas"][mask], nan=1) < 0.5)
    out["slip_balance"] = None
    if coast.sum() >= 2:
        front = np.nanmean([ch[f"slip_raw_{c}"][mask][coast] for c in ("FL", "FR")], axis=0)
        rear = np.nanmean([ch[f"slip_raw_{c}"][mask][coast] for c in ("RL", "RR")], axis=0)
        f, r = np.nanmean(front), np.nanmean(rear)
        out["slip_balance"] = float(f / r) if np.isfinite(f) and np.isfinite(r) and r > 1e-4 else None
    with np.errstate(all="ignore"):
        vx, vz = ch["local_vx"][mask][turning], ch["local_vz"][mask][turning]
        beta = np.degrees(np.arctan2(np.abs(vx), np.maximum(np.abs(vz), 0.01)))
        out["body_slip_deg"] = float(np.nanmean(beta)) if np.isfinite(beta).any() else None
        steer = np.abs(ch["steer"][mask][turning])
        g = g_lat[turning]
        out["steer_per_g"] = float(np.nanmean(steer) / np.nanmean(g)) if np.isfinite(steer).any() and np.nanmean(g) > 0.1 else None
    return out


def analyse(laps: list[Lap], metas: dict, length: float, cfg, include_invalid=False):
    """Key figures over the representative laps of one car/track."""
    if not laps:
        raise ValueError("Keine Runden ausgewählt")
    if len(laps) > MAX_LAPS:
        raise ValueError(f"Höchstens {MAX_LAPS} Runden pro Analyse")
    if len({l.samples[0].source for l in laps if l.samples}) > 1:
        raise ValueError("Demo- und echte Runden dürfen nicht gemischt werden")
    if length <= 0:
        raise ValueError("Streckenlänge unbekannt - Runden nicht auswertbar")
    rows = screen(laps, length, cfg, include_invalid)
    used = [r for r in rows if not r["reasons"]]
    warnings, notes = [], []
    if not used:
        warnings.append("Keine repräsentative Runde: alle gewählten Runden sind ausgeschlossen")
    elif len(used) < 3:
        warnings.append(f"Nur {len(used)} repräsentative Runde(n) - Kennwerte sind unsicher (empfohlen: mindestens 3)")
    if laps[0].samples and laps[0].samples[0].source == "demo":
        warnings.append("Demo-Telemetrie (synthetisch) - nicht für echte Setup-Entscheidungen geeignet")

    # Data availability over the used laps (share of samples with a value).
    availability = []
    pool = [r["lap"] for r in used] or [r["lap"] for r in rows]
    total = sum(len(l.samples) for l in pool) or 1
    present = {}
    for key in NEEDED:
        present[key] = sum(1 for l in pool for s in l.samples if s.channels.get(key) is not None) / total
    for key, label, keys in AVAILABILITY:
        ratio = min(present.get(k, 0) for k in keys)
        availability.append({"key": key, "label": label, "ratio": round(ratio, 3), "available": ratio >= 0.8})
    available = {a["key"] for a in availability if a["available"]}
    if not (cfg.steering_lock_deg and cfg.wheelbase_m and cfg.steering_ratio):
        notes.append("Lenkung nicht kalibriert (Einstellungen → Erweitert): kein Soll-Gierraten-Vergleich, Unter-/Übersteuern nur über Schlupf-Indizien")

    profiles = []
    for r in used:
        try:
            profiles.append((r, *profile(r["lap"], length, NEEDED)))
        except ValueError:
            r["reasons"].append("Runde nicht auswertbar")
    used = [r for r in used if not r["reasons"]]
    reference = min(used, key=lambda r: r["lap"].duration_ms) if used else None
    windows = corner_windows(reference["events"], length) if reference else []

    lap_times = [r["lap"].duration_ms / 1000 for r in used]
    kpis: dict = {}
    if lap_times:
        kpis["lap_time"] = {
            "median_s": rounded(float(np.median(lap_times)), 3),
            "best_s": rounded(min(lap_times), 3),
            "spread_s": rounded(max(lap_times) - min(lap_times), 3),
            "stdev_s": rounded(float(np.std(lap_times)), 3),
        }
        sector_count = max(len(r["lap"].sectors_ms) for r in used)
        kpis["sectors_median_s"] = [
            rounded(finite_median([r["lap"].sectors_ms[i] / 1000 for r in used if i < len(r["lap"].sectors_ms) and r["lap"].sectors_ms[i]]), 3)
            for i in range(sector_count)
        ]

    phase_values = {p: {"slip_balance": [], "body_slip_deg": [], "steer_per_g": []} for p in ("entry", "mid", "exit")}
    speed_class: dict[str, list] = {}
    top, ride_f, ride_r, traction = [], [], [], []
    tyres = {c: {k: [] for k in ("inner", "middle", "outer", "core", "pressure_hot", "wear_per_lap")} for c in CORNERS}
    brakes = {"front": [], "rear": []}
    for r, grid, _time, ch in profiles:
        if r["reasons"]:
            continue
        speed = ch["speed"]
        if np.isfinite(speed).any():
            top.append(float(np.nanmax(speed)))
        handling = {"speed", "g_lat", "steer", "local_vx", "local_vz"} <= {
            k for k in ch if np.isfinite(ch[k]).mean() > 0.5
        } and "wheel_slip" in available
        for w in windows:
            if not handling:
                break
            for phase in ("entry", "mid", "exit"):
                figures = phase_figures(grid, ch, w[phase])
                if figures:
                    for k, v in figures.items():
                        if v is not None:
                            phase_values[phase][k].append(v)
            apex_speed = np.interp(w["apex"], grid, speed)
            mid = phase_figures(grid, ch, w["mid"])
            if mid and mid.get("slip_balance") is not None and np.isfinite(apex_speed):
                cls = "low" if apex_speed < 120 else "high" if apex_speed >= 160 else "medium"
                speed_class.setdefault(cls, []).append(mid["slip_balance"])
        fast = np.isfinite(speed) & (speed > 60)
        half = grid >= grid[-1] / 2
        for c in CORNERS:
            for k in ("inner", "middle", "outer", "core"):
                v = ch.get(f"{k}_{c}")
                if v is not None and np.isfinite(v[fast]).any():
                    tyres[c][k].append(float(np.nanmean(v[fast])))
            p = ch.get(f"pressure_{c}")
            if p is not None and np.isfinite(p[half]).any():
                tyres[c]["pressure_hot"].append(float(np.nanmean(p[half])))
            wear = [s.channels.get(f"wear_raw_{c}") for s in r["lap"].samples]
            wear = [x for x in wear if x is not None]
            if len(wear) > 10:
                tyres[c]["wear_per_lap"].append(float(wear[0] - wear[-1]))
        for side, cs in (("front", ("FL", "FR")), ("rear", ("RL", "RR"))):
            vals = [np.nanmax(ch[f"brake_temp_{c}"]) for c in cs if np.isfinite(ch[f"brake_temp_{c}"]).any()]
            if vals:
                brakes[side].append(float(max(vals)))
        quick = np.isfinite(speed) & (speed > 100)
        for key, target in (("ride_front", ride_f), ("ride_rear", ride_r)):
            v = ch[key][quick]
            if np.isfinite(v).sum() > 10:
                target.append(float(np.nanpercentile(v, 2)))
        events = r["events"]
        for w in windows:
            full = [e["distance_m"] for e in events if e["type"] == "full_throttle" and w["apex"] <= e["distance_m"] <= w["exit"][1] + 150]
            if full:
                t_apex = np.interp(w["apex"], grid, _time)
                t_full = np.interp(full[0], grid, _time)
                if np.isfinite(t_apex) and np.isfinite(t_full):
                    traction.append(float(t_full - t_apex))

    kpis["top_speed_kmh"] = rounded(finite_median(top), 1)
    kpis["phases"] = {
        p: {k: rounded(finite_median(v), 3) for k, v in vals.items()} | {"corner_phases": len(vals["body_slip_deg"])}
        for p, vals in phase_values.items()
    }
    kpis["mid_corner_slip_balance_by_speed"] = {
        k: {"median": rounded(finite_median(v), 3), "corners": len(v)} for k, v in speed_class.items() if v
    }
    per_lap = len(used) or 1
    counts = {}
    for r in used:
        for e in r["events"]:
            t = e["type"]
            wheel = e.get("wheel", "")
            key = {
                "wheel_lock": "wheel_lock_" + ("front" if wheel in ("FL", "FR") else "rear"),
                "wheelspin": "wheelspin_" + ("front" if wheel in ("FL", "FR") else "rear"),
            }.get(t, t)
            if key in (
                "wheel_lock_front", "wheel_lock_rear", "wheelspin_front", "wheelspin_rear", "body_slip",
                "countersteer_est", "understeer_est", "oversteer_est", "unstable_braking", "throttle_correction",
                "tyre_overheat",
            ):
                counts[key] = counts.get(key, 0) + 1
    kpis["events_per_lap"] = {k: round(v / per_lap, 2) for k, v in sorted(counts.items())}
    kpis["traction_apex_to_full_throttle_s"] = rounded(finite_median(traction), 2)
    kpis["tyres"] = {
        c: {k: rounded(finite_median(v), 3 if k == "wear_per_lap" else 1) for k, v in vals.items()}
        for c, vals in tyres.items()
    }
    kpis["ride_height_min_m"] = {"front": rounded(finite_median(ride_f), 4), "rear": rounded(finite_median(ride_r), 4)}
    kpis["brake_temp_max_c"] = {k: rounded(finite_median(v), 0) for k, v in brakes.items()}

    conditions = conditions_of([r["lap"] for r in used], metas)
    lap_rows = [
        {
            "id": r["lap"].id,
            "session_id": r["lap"].session_id,
            "number": r["lap"].number,
            "duration_ms": r["lap"].duration_ms,
            "used": not r["reasons"],
            "reasons": r["reasons"],
            "notes": r["notes"],
        }
        for r in rows
    ]
    return json_safe(
        {
            "laps": lap_rows,
            "used": len(used),
            "excluded": len(rows) - len(used),
            "warnings": warnings,
            "notes": notes,
            "availability": availability,
            "kpis": kpis,
            "conditions": conditions,
        }
    )


def conditions_of(laps, metas):
    def span(values, digits=1):
        vals = [v for v in values if v is not None and math.isfinite(v)]
        return [round(min(vals), digits), round(max(vals), digits)] if vals else None

    return {
        "fuel_start_l": span([l.samples[0].channels.get("fuel") for l in laps if l.samples]),
        "air_c": span([l.samples[0].channels.get("air_temp") for l in laps if l.samples]),
        "road_c": span([l.samples[0].channels.get("road_temp") for l in laps if l.samples]),
        "compounds": sorted({(metas.get(l.session_id) or {}).get("compound") or "" for l in laps} - {""}),
        "sessions": len({l.session_id for l in laps}),
    }


# ------------------------------------------------- feedback <-> measurement


def evidence(feedback: Feedback, result: dict):
    """Deterministic verdict per feedback item. Measurements are indicators."""
    k = result.get("kpis", {})
    phases = k.get("phases", {})
    events = k.get("events_per_lap", {})
    available = {a["key"] for a in result.get("availability", []) if a["available"]}
    out = []
    for i, item in enumerate(feedback.items):
        verdict, details = "nicht messbar", []
        phase = item.phase
        if item.issue in ("understeer", "oversteer"):
            figures = phases.get(phase) or (phases.get("mid") if phase in ("lowspeed", "highspeed", "general") else None)
            if phase in ("lowspeed", "highspeed"):
                by_speed = k.get("mid_corner_slip_balance_by_speed", {}).get("low" if phase == "lowspeed" else "high")
                figures = {"slip_balance": by_speed["median"]} if by_speed else None
            balance = (figures or {}).get("slip_balance")
            if balance is None or "wheel_slip" not in available:
                details.append("Radschlupf hier nicht auswertbar (fehlt oder von Bremse/Gas überlagert)")
            else:
                details.append(f"Schlupfverhältnis vorne/hinten {balance:.2f} (ohne starkes Bremsen/Gas; Indiz, fahrzeugabhängig)")
                front_heavy = balance > 1.25
                rear_heavy = balance < 0.8
                if item.issue == "understeer":
                    verdict = "stützt" if front_heavy else "widerspricht eher" if rear_heavy else "nicht eindeutig"
                else:
                    verdict = "stützt" if rear_heavy else "widerspricht eher" if front_heavy else "nicht eindeutig"
            est = events.get("understeer_est" if item.issue == "understeer" else "oversteer_est")
            if est:
                details.append(f"{est} geschätzte {'Untersteuer' if item.issue == 'understeer' else 'Übersteuer'}-Phasen pro Runde (Gierraten-Vergleich)")
            if item.issue == "oversteer":
                slides = (events.get("countersteer_est") or 0) + (events.get("body_slip") or 0)
                if events.get("countersteer_est"):
                    details.append(f"{events['countersteer_est']} Gegenlenk-Phasen pro Runde (geschätzt)")
                if events.get("body_slip"):
                    details.append(f"{events['body_slip']} Rutscher mit erhöhtem Schwimmwinkel pro Runde (ganze Runde, nicht nur diese Phase)")
                if slides >= 0.5 and verdict in ("nicht messbar", "nicht eindeutig"):
                    verdict = "stützt"
            if verdict == "nicht messbar" and est:
                verdict = "stützt"
        elif item.issue == "lockup":
            f, r = events.get("wheel_lock_front", 0), events.get("wheel_lock_rear", 0)
            verdict = "stützt" if f + r >= 0.5 else "nicht eindeutig"
            details.append(f"Blockier-Ereignisse pro Runde: vorne {f}, hinten {r} (Schätzung aus Raddrehzahl)")
        elif item.issue == "wheelspin":
            f, r = events.get("wheelspin_front", 0), events.get("wheelspin_rear", 0)
            verdict = "stützt" if f + r >= 0.5 else "nicht eindeutig"
            details.append(f"Durchdreh-Ereignisse pro Runde: vorne {f}, hinten {r} (Schätzung aus Raddrehzahl)")
            if k.get("traction_apex_to_full_throttle_s") is not None:
                details.append(f"Scheitel bis Vollgas im Median {k['traction_apex_to_full_throttle_s']} s")
        elif item.issue == "unstable_braking":
            n = events.get("unstable_braking", 0)
            verdict = "stützt" if n >= 0.5 else "nicht eindeutig"
            details.append(f"Unruhige Bremsphasen (Pedalkorrekturen) pro Runde: {n}")
        elif item.issue == "bottoming":
            ride = k.get("ride_height_min_m", {})
            if "ride" not in available or ride.get("front") is None:
                details.append("Bodenfreiheit nicht verfügbar")
            else:
                low = min(v for v in (ride.get("front"), ride.get("rear")) if v is not None)
                details.append(f"Minimale Bodenfreiheit (2-%-Wert, schnell) vorne {ride.get('front')} m, hinten {ride.get('rear')} m")
                verdict = "stützt" if low < 0.005 else "nicht eindeutig"
        elif item.issue == "tyre_temps":
            if "tyre_temp" not in available:
                details.append("Reifentemperaturen nicht verfügbar")
            else:
                verdict = "nicht eindeutig"
                for c, t in k.get("tyres", {}).items():
                    if t.get("inner") is not None and t.get("outer") is not None:
                        details.append(f"{c}: innen {t['inner']} / mitte {t.get('middle')} / außen {t['outer']} °C")
        else:
            details.append("Mit den aufgezeichneten Kanälen nicht direkt messbar - nur deine Rückmeldung")
        out.append(
            {
                "item": i,
                "issue": item.issue,
                "phase": item.phase,
                "label": f"{ISSUES[item.issue]} · {PHASES[item.phase]}",
                "verdict": verdict,
                "details": details,
            }
        )
    return out
