"""Record deterministic DEMO samples and independent struct.pack ABI fixtures.

No AC game was connected to create these fixtures. Binary pages are constructed
at externally checked ABI offsets, not via the production ctypes encoder.
"""

from pathlib import Path
import json
import struct
import numpy as np
from ac_agent.demo import DemoSource
from ac_agent.models import Lap

root = Path(__file__).resolve().parents[1]
out = root / "tests" / "fixtures"
out.mkdir(parents=True, exist_ok=True)
demo = DemoSource()
laps = []
for index in (0, 1):
    samples = list(demo.recorded_lap(index, hz=30))
    duration = round(demo._profiles[index][2][-1] * 1000)
    samples.append(demo.make_sample(1, index, duration))
    samples = [
        s.model_copy(update={"captured_at": 1790985600 + index * 100 + s.lap_ms / 1000})
        for s in samples
    ]
    splits = np.interp(
        [0, 1 / 3, 2 / 3, 1], demo._profiles[index][0], demo._profiles[index][2]
    )
    sectors = [round(v * 1000) for v in np.diff(splits)]
    sectors[-1] = duration - sum(sectors[:-1])
    laps.append(
        Lap(
            id=f"demo-fixture-{index}",
            session_id="demo-fixture-session",
            number=index + 1,
            duration_ms=duration,
            valid=True,
            validity_basis="synthetic demo fixture",
            sectors_ms=sectors,
            sector_positions=[1 / 3, 2 / 3],
            samples=samples,
            created_at="2026-10-03T00:00:00+00:00",
        )
    )
(out / "demo-recording.json").write_text(
    json.dumps(
        {
            "provenance": "synthetic recorded demo, NOT live AC",
            "meta": demo.meta.model_dump(),
            "laps": [l.model_dump() for l in laps],
        },
        separators=(",", ":"),
    )
)
p = bytearray(580)
g = bytearray(296)
s = bytearray(684)


def put(buf, offset, fmt, *values):
    struct.pack_into("<" + fmt, buf, offset, *values)


def text(buf, offset, count, value):
    data = value.encode("utf-16-le")
    assert len(data) < count * 2
    buf[offset : offset + len(data)] = data


put(p, 0, "i", 713)
put(p, 4, "fff", 0.62, 0.13, 42)
put(p, 16, "ii", 5, 8470)
put(p, 24, "ff", 0.18, 183.4)
put(p, 44, "3f", 1.25, 0.06, -0.74)
for off, values in (
    (56, [0.1] * 4),
    (72, [2400] * 4),
    (88, [26.1, 26.2, 26.3, 26.4]),
    (104, [150] * 4),
    (120, [100] * 4),
    (152, [89, 90, 91, 92]),
    (184, [0.02] * 4),
    (348, [260] * 4),
    (368, [88, 89, 90, 91]),
    (384, [86] * 4),
    (400, [84] * 4),
):
    put(p, off, "4f", *values)
put(p, 288, "ff", 23, 31)
put(p, 576, "f", 50)
put(g, 0, "iii", 812, 2, 0)
put(g, 132, "iiiii", 2, 1, 23782, 98234, 97812)
put(g, 152, "f", 600000)
put(g, 160, "iiii", 0, 1, 25000, 0)
text(g, 176, 33, "Medium")
put(g, 248, "f", 0.29)
put(g, 252, "3f", 40, 0, 300)
text(s, 0, 15, "1.16")
text(s, 30, 15, "1.16.4")
put(s, 60, "ii", 1, 1)
for off, value in (
    (68, "test_formula"),
    (134, "test_track"),
    (200, "Jörg"),
    (266, "Müller"),
    (332, "JM"),
):
    text(s, off, 33, value)
put(s, 400, "i", 3)
put(s, 412, "i", 10500)
put(s, 416, "f", 80)
put(s, 436, "4f", 0.33, 0.33, 0.33, 0.33)
put(s, 520, "f", 4200)
text(s, 524, 33, "long_configuration_24chars")
for name, buf in (("physics", p), ("graphics", g), ("static", s)):
    (out / (name + ".bin")).write_bytes(buf)
print("Generated", len(laps), "demo laps and fixed-offset ABI pages")
