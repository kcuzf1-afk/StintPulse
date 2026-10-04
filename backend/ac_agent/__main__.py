import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import sys
import threading
import urllib.request
import webbrowser
import uvicorn
from . import APP_NAME, __version__
from .app import create_app, default_data_dir
from .database import Database


def setup_logging(data_dir):
    """Connection/decoding log; never contains tokens or memory contents."""
    directory = Path(data_dir) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "stintpulse.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root = logging.getLogger("ac_agent")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(console)
    return path


def running_instance(port):
    """Version of a StintPulse already answering on this port, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            data = json.load(r)
        return (data.get("version") or "?") if data.get("ok") else None
    except Exception:
        return None


def port_free(host, port):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def pause_before_exit():
    """A double-clicked EXE closes its window at once; keep messages readable."""
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        try:
            input("\nEnter drücken zum Schließen / press Enter to close ...")
        except EOFError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} {__version__} - telemetry, analysis and onboard video for Assetto Corsa"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Start isolated synthetic data source"
    )
    parser.add_argument(
        "--ac", action="store_true", help="Use original AC shared memory"
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args()
    if args.demo and args.ac:
        parser.error("Use --demo or --ac")
    if bool(args.cert) != bool(args.key):
        parser.error("TLS requires both --cert and --key")
    directory = Path(sys._MEIPASS) / "web" if getattr(sys, "frozen", False) else None
    data_dir = args.data_dir or default_data_dir()
    log_file = setup_logging(data_dir)
    # The source is a runtime choice: every start uses the real game unless
    # --demo is given. A demo toggled earlier in the dashboard is never resumed
    # silently from saved settings.
    source = "demo" if args.demo else "ac"
    # Checked BEFORE the app opens its data: a second start must never touch
    # the recordings of the instance that is already running.
    db = Database(Path(data_dir) / "telemetry.sqlite")
    saved = db.get_settings()
    db.close()
    port = args.port or saved.port
    scheme = "https" if args.cert else "http"
    url = f"{scheme}://localhost:{port}"
    host = "0.0.0.0" if saved.lan else "127.0.0.1"
    if running_instance(port):
        # Double-clicked again: just show the running dashboard.
        print(f"{APP_NAME} läuft bereits / is already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not port_free(host, port):
        print(
            f"Port {port} ist von einem anderen Programm belegt. Dieses Programm beenden "
            f"oder {APP_NAME} mit --port 8766 starten (Einstellungen > Netzwerk ändert den Port dauerhaft)."
        )
        print(f"Port {port} is used by another program. Close it or start with --port 8766.")
        logging.getLogger("ac_agent").error("Port %s is in use", port)
        pause_before_exit()
        sys.exit(1)
    app = create_app(
        data_dir,
        source,
        web_dir=directory,
        port=args.port,
    )
    cfg = app.state.engine.settings
    logging.getLogger("ac_agent").info(
        "Starting %s %s with data source %s (log: %s)", APP_NAME, __version__, source, log_file
    )
    print(f"{APP_NAME} {__version__}")
    print(
        "Data source: "
        + ("DEMO (synthetic, no game connection)" if source == "demo" else "Assetto Corsa shared memory")
    )
    print(f"Data folder: {data_dir}")
    if cfg.open_browser and not args.no_browser:

        def open_when_ready():
            import time, ssl

            for _ in range(80):
                try:
                    urllib.request.urlopen(
                        url + "/api/health",
                        timeout=0.2,
                        context=ssl._create_unverified_context(),
                    )
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(0.1)

        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"Dashboard: {url}")
    print("Dieses Fenster offen lassen, solange du StintPulse nutzt. / Keep this window open.")
    if cfg.lan:
        # Shown only in the local console window of the PC, never in the log.
        print(f"LAN access code: {cfg.access_token[:4]} {cfg.access_token[4:]}")
        print("On the phone/tablet (same Wi-Fi) open: http://<PC-IP>:" + str(cfg.port))
    if cfg.lan and not args.cert:
        print(
            "LAN is using HTTP. Access code and telemetry are unencrypted; use a trusted network or configure TLS."
        )
    uvicorn.run(
        app,
        host=host,
        port=cfg.port,
        access_log=False,
        log_level="warning",
        ssl_certfile=str(args.cert) if args.cert else None,
        ssl_keyfile=str(args.key) if args.key else None,
    )


def run():
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # unexpected start failure: readable, then exit
        logging.getLogger("ac_agent").exception("Start failed")
        print(f"\n{APP_NAME} konnte nicht starten / could not start: {exc}")
        pause_before_exit()
        sys.exit(1)


if __name__ == "__main__":
    run()
