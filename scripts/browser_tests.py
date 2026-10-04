"""Run browser integration tests against an isolated local demo server."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("npm is required; install Node.js first")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="ac-agent-browser-tests-") as data_dir:
        server = subprocess.Popen(
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
            cwd=root,
            # Demo time-lapse: laps of ~15 s, so the video tests can record
            # complete laps quickly (test data only, never real telemetry).
            # Test tone instead of real PC sound for the recording tests.
            env={**os.environ, "AC_AGENT_DEMO_RATE": "6", "AC_AGENT_TEST_AUDIO": "1"},
        )
        try:
            for _ in range(200):
                if server.poll() is not None:
                    raise RuntimeError("Demo server exited before the browser tests")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/live", timeout=1
                    ) as response:
                        if json.load(response).get("sample"):
                            break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Demo server did not initialize")
            test_env = {**os.environ, "AC_AGENT_TEST_URL": f"http://127.0.0.1:{port}"}
            return subprocess.run(
                [npm, "run", "test:e2e"], cwd=root / "frontend", env=test_env
            ).returncode
        finally:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    sys.exit(main())
