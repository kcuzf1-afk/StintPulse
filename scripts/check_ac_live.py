"""Read-only Windows acceptance check with the original game running.

Run while sitting in the car in a driving session. Prints the separate
process/shared-memory diagnosis and exits 0 only if real AC samples arrived.
The check never writes to the AC pages and never falls back to demo data.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ac_agent.models import Settings  # noqa: E402
from ac_agent.shared_memory import ACSource  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=8)
    args = parser.parse_args()
    if not 3 <= args.seconds <= 60:
        parser.error("Use 3–60 seconds")
    if sys.platform != "win32":
        parser.error("Real AC shared memory requires Windows")
    source = ACSource()
    cfg = Settings()
    count = 0
    latest = None
    meta = None
    states = []
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.seconds:
            result = source.read(cfg)
            if result:
                meta, latest = result
                count += 1
            if not states or states[-1] != source.state:
                states.append(source.state)
            time.sleep(1 / 60)
        elapsed = time.monotonic() - started
        report = {
            "ok": latest is not None,
            "states_seen": states,
            "diagnostics": source.diagnostics(),
        }
        if latest is not None:
            report.update(
                {
                    "source": "ac",
                    "car": meta.car,
                    "track": meta.track,
                    "layout": meta.layout,
                    "ac_version": meta.ac_version or "unknown",
                    "sm_version": meta.sm_version or "unknown",
                    "game_status": latest.status,
                    "samples": count,
                    "average_hz": round(count / elapsed, 2),
                    "speed_kmh": latest.channels.get("speed"),
                    "rpm": latest.channels.get("rpm"),
                    "lap_ms": latest.lap_ms,
                    "session_timer_unit": meta.session_timer_unit,
                }
            )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if latest is not None else 1
    finally:
        source.close()


if __name__ == "__main__":
    sys.exit(main())
