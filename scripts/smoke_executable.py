"""Start a packaged build and verify real demo API data, then stop cleanly."""

import json
import os
import subprocess
import socket
import sys
import tempfile
import time
import urllib.request
import re
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="ac-agent-smoke-") as directory:
    executable = str(Path(sys.argv[1]).resolve())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            executable,
            "--demo",
            "--no-browser",
            "--data-dir",
            directory,
            "--port",
            str(port),
        ],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        # Temporary AC folders: the smoke test never reads or writes real ones.
        env={
            **os.environ,
            "AC_AGENT_AC_DIR": str(Path(directory) / "ac"),
            "AC_AGENT_SETUPS_DIR": str(Path(directory) / "setups"),
        },
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Packaged application exited before the smoke test")
            try:
                data = json.load(
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/live", timeout=1
                    )
                )
                if data.get("sample") and data["sample"]["source"] == "demo":
                    if data.get("error"):
                        raise RuntimeError(data["error"])
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Packaged demo did not expose live telemetry")
        html = (
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)
            .read()
            .decode()
        )
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
        if not assets:
            raise RuntimeError("Packaged dashboard assets are missing")
        for asset in assets:
            if (
                len(
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{asset}", timeout=3
                    ).read()
                )
                < 100
            ):
                raise RuntimeError("Packaged dashboard asset is empty")
        sessions = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sessions", timeout=3)
        )
        if not sessions or sessions[0]["lap_count"] < 2:
            raise RuntimeError("Packaged demo did not persist its comparison laps")
        setup = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/setup/status", timeout=3)
        )
        if not setup["ai"]["sdk"] or setup["ai"]["state"] != "off":
            raise RuntimeError("Setup assistant: Anthropic SDK missing in the package")
        print("Packaged demo, stored laps, dashboard assets and setup assistant smoke test passed")
    finally:
        if process.poll() is None:
            if os.name == "nt":
                import signal

                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
