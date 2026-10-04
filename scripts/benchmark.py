"""Reproducible loopback measurement. Synthetic source; excludes startup warm-up."""

import argparse
import asyncio
import json
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
import websockets


def get(port, path):
    return json.load(
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/{path}", timeout=5)
    )


async def measure(port, seconds):
    await asyncio.sleep(3)
    before = get(port, "performance")
    laps_before = sum(s["lap_count"] for s in get(port, "sessions"))
    started = time.monotonic()
    received = 0
    messages = 0
    cpus = []
    rams = []
    arrivals = []
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/ws", subprotocols=["ac-agent"]
    ) as ws:
        last_poll = started
        while time.monotonic() - started < seconds:
            message = await asyncio.wait_for(ws.recv(), timeout=5)
            arrivals.append(time.monotonic())
            received += len(message.encode())
            messages += 1
            if time.monotonic() - last_poll >= 1:
                stats = await asyncio.to_thread(get, port, "performance")
                cpus.append(stats["cpu_percent"])
                rams.append(stats["ram_mb"])
                last_poll = time.monotonic()
    after = get(port, "performance")
    duration = time.monotonic() - started
    laps_after = sum(s["lap_count"] for s in get(port, "sessions"))
    intervals = [b - a for a, b in zip(arrivals, arrivals[1:])]
    return {
        "source": "synthetic demo; no AC game attached",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "duration_s": round(duration, 2),
        "clients": 1,
        "capture_hz_observed": round(
            (after["samples"] - before["samples"]) / duration, 2
        ),
        "websocket_hz_observed": round(messages / duration, 2),
        "websocket_kib_per_s": round(received / 1024 / duration, 2),
        "cpu_percent_mean_one_core": round(sum(cpus) / len(cpus), 2) if cpus else None,
        "ram_mb_peak": round(max(rams), 2) if rams else None,
        "dropped_samples": after["dropped"] - before["dropped"],
        "websocket_interval_max_ms": round(max(intervals) * 1000, 2)
        if intervals
        else None,
        "database_mb": round(after["database_mb"], 2),
        "laps_before": laps_before,
        "laps_after": laps_after,
        "automatically_saved_laps": laps_after - laps_before,
        "note": "Backend CPU/RAM only; HTTP loopback and one WebSocket consumer. No video/UI or real Windows AC load.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 300:
        parser.error("Use 5–300 seconds")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="ac-agent-benchmark-") as data_dir:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ac_agent",
                "--demo",
                "--no-browser",
                "--port",
                str(port),
                "--data-dir",
                data_dir,
            ],
            stdout=subprocess.DEVNULL,
        )
        try:
            for _ in range(200):
                if child.poll() is not None:
                    raise RuntimeError("Backend exited")
                try:
                    if get(port, "live").get("sample"):
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Demo did not initialize")
            result = asyncio.run(measure(port, args.seconds))
            output = json.dumps(result, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output + "\n")
            print(output)
        finally:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
