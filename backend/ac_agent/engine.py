from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import logging
import platform
import queue
import struct
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from .analysis import (
    at,
    covers_full_lap,
    normalize,
    detect_events,
    coaching,
    track_map,
    summary_statistics,
    tyre_warnings,
)
from .database import Database
from .demo import DemoSource
from .laps import LapRecorder, lap_wall_window, utc_now
from .models import Lap, json_safe
from .shared_memory import ACSource
from .timing import lap_snapshot, merge_best_sectors, reference_info, timing_payload

log = logging.getLogger(__name__)

def demo_rate():
    """Demo time-lapse for automated tests only (AC_AGENT_DEMO_RATE)."""
    import os

    try:
        return min(20.0, max(0.1, float(os.environ.get("AC_AGENT_DEMO_RATE", "1"))))
    except ValueError:
        return 1.0


MAP_BINS = 1000  # provisional map: newest driven point per 0.1 % of the lap

# Only in these states real AC telemetry is flowing.
GAME_STATES = {"live", "paused", "replay"}

class Engine:
    def __init__(self, db: Database, settings):
        self.db, self.settings = db, settings
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.demo_ready = threading.Event()
        self.queue = queue.Queue(maxsize=240)
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ac-storage")
        self.analyzer = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ac-lap-analysis"
        )
        self.inflight_sessions = {}
        self.ring = deque(maxlen=7200)
        self.partial_map = {}  # bin -> [lap_pos, x, z], kept across laps
        self.latest, self.meta, self.session_id, self.recorder = None, None, None, None
        self.reference, self.ref_profile = None, None
        self.personal_best, self.pb_profile = None, None
        self.stats = {}
        self.tips = []
        self.map = None
        self.status = "starting"
        self.error = None
        self.dropped = 0
        self.total_samples = 0
        self.started = time.monotonic()
        self.last_seen = 0.0
        self.connected = False
        self.storage_status = {}
        self.threads = []
        self.demo_seed_last_ms = 0
        self.active_source = None
        self.source = None
        self.source_state = "starting"
        self.capture_errors = 0
        self.capture_error = None
        self.capture_error_at = None
        self.capture_exit = None
        self.capture_restarts = 0
        self.recheck_event = threading.Event()
        self.ws_clients = 0
        self.ws_last_send = None
        self.last_lap = None  # HUD snapshot of the most recently completed lap
        self.best_sectors = []  # personal sector bests before the current lap
        self.lap_hooks = []  # called with every saved lap (writer thread)

    def start(self):
        self.threads = [
            threading.Thread(target=self._capture, daemon=True, name="ac-capture"),
            threading.Thread(target=self._process, daemon=True, name="ac-analysis"),
        ]
        for t in self.threads:
            t.start()

    def capture_alive(self):
        return bool(self.threads) and self.threads[0].is_alive()

    def recheck(self):
        """'Verbindung erneut prüfen': reconnect now; revive a dead capture thread."""
        self.recheck_event.set()
        if self.threads and not self.capture_alive() and not self.stop_event.is_set():
            log.warning("Restarting capture thread (previous exit: %s)", self.capture_exit)
            self.capture_exit = None
            self.capture_restarts += 1
            self.threads[0] = threading.Thread(
                target=self._capture, daemon=True, name="ac-capture"
            )
            self.threads[0].start()

    def stop(self):
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=10)
        if self.recorder and self.recorder.samples:
            partial = self.recorder.abort("application_closed")
            self._schedule_complete(partial)
        self.analyzer.shutdown(wait=True, cancel_futures=False)
        self.writer.shutdown(wait=True, cancel_futures=False)
        if self.session_id:
            self.db.end_session(self.session_id)

    def _capture(self):
        source = None
        active = None
        deadline = time.monotonic()
        try:
            while not self.stop_event.is_set():
                try:
                    if active != self.settings.source:
                        if source:
                            source.close()
                        active = self.settings.source
                        self.demo_ready.clear()
                        self._switch_source(active)
                        source = (
                            DemoSource(rate=demo_rate()) if active == "demo" else ACSource()
                        )
                        self.source = source
                        self.recheck_event.clear()
                    if self.recheck_event.is_set():
                        self.recheck_event.clear()
                        if hasattr(source, "request_reconnect"):
                            source.request_reconnect()
                    frame = source.read(self.settings)
                    self.source_state = (
                        "demo"
                        if active == "demo"
                        else getattr(source, "state", "unknown")
                    )
                    if frame:
                        try:
                            self.queue.put_nowait(frame)
                            if active == "demo" and not self.demo_ready.is_set():
                                while not self.demo_ready.wait(0.1):
                                    if (
                                        self.stop_event.is_set()
                                        or self.settings.source != active
                                    ):
                                        break
                                source.start = time.monotonic()
                                deadline = time.monotonic()
                        except queue.Full:
                            self.dropped += 1
                except Exception as exc:
                    # One bad frame or OS error must never end capture silently.
                    message = (type(exc).__name__ + ": " + str(exc))[:200]
                    if message != self.capture_error:
                        log.exception("Capture error")
                    self.capture_errors += 1
                    self.capture_error, self.capture_error_at = message, time.time()
                    if source:
                        try:
                            source.close()
                        except Exception:
                            pass
                    self.stop_event.wait(0.5)
                deadline += 1 / self.settings.capture_hz
                wait = deadline - time.monotonic()
                if wait < -0.1:
                    deadline = time.monotonic()
                self.stop_event.wait(max(0, wait))
        except BaseException as exc:
            self.capture_exit = (type(exc).__name__ + ": " + str(exc))[:200]
            log.critical("Capture thread terminated: %s", self.capture_exit)
            raise
        finally:
            if source:
                source.close()
            if not self.stop_event.is_set() and self.capture_exit is None:
                self.capture_exit = "Capture loop ended unexpectedly"

    def _switch_source(self, new):
        """End the previous source's live view. Stored sessions stay archived."""
        with self.lock:
            previous = self.active_source
            self.active_source = new
            if previous is None:
                return
            log.info("Data source switched %s -> %s", previous, new)
            if self.recorder and self.recorder.samples:
                self._schedule_complete(self.recorder.abort("source_changed"))
            if self.session_id:
                self.db.end_session(self.session_id)
            self.latest = self.meta = self.session_id = self.recorder = None
            self.last_lap, self.best_sectors = None, []
            self.reference = self.ref_profile = None
            self.personal_best = self.pb_profile = None
            self.ring.clear()
            self.partial_map.clear()
            self.map = None
            self.stats, self.tips, self.storage_status = {}, [], {}
            self.connected, self.last_seen, self.error = False, 0.0, None
            self.source_state = "starting"
        # Frames already queued from the previous source must not reappear.
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break

    def _new_session(self, meta):
        if self.recorder and self.recorder.samples:
            self._schedule_complete(self.recorder.abort("session_changed"))
        if self.session_id:
            self.db.end_session(self.session_id)
        self.meta = meta
        self.session_id = self.db.new_session(meta)
        self.recorder = LapRecorder(self.session_id, meta)
        self.map = self.db.get_map(meta)
        self.partial_map.clear()
        if self.map is None and meta.track_length > 0:
            self.analyzer.submit(self._recover_map, meta.model_copy(), self.session_id)
        self.ring.clear()
        self.stats = {}
        self.tips = []
        self.update_reference()
        if meta.source == "demo":
            self._seed_demo()
        # HUD comparison state starts fresh for every session (car/track/layout).
        self.last_lap = None
        self.best_sectors = self.db.best_sectors(meta)

    def reference_info(self):
        kind = (
            "selected"
            if self.reference
            and self.settings.reference_lap_id
            and self.reference.id == self.settings.reference_lap_id
            else "personal_best"
        )
        return reference_info(self.reference, kind)

    def _lap_done(self, lap):
        """Snapshot a completed lap for the HUD, compared with values BEFORE it."""
        if not lap or len(lap.samples) < 2:
            return
        self.last_lap = lap_snapshot(
            lap, self.reference_info(), self.best_sectors, time.monotonic()
        )
        self.best_sectors = merge_best_sectors(self.best_sectors, lap)

    def _seed_demo(self):
        """Two generated laps enable immediate comparison; all rows tagged demo."""
        demo = DemoSource()
        epoch = time.time() - sum(demo._profiles[i][2][-1] for i in (0, 1))
        for number in (0, 1):
            samples = list(demo.recorded_lap(number))
            end = demo.make_sample(
                1.0, number, round(demo._profiles[number % 3][2][-1] * 1000)
            )
            samples.append(end)
            duration = end.lap_ms
            samples = [
                s.model_copy(update={"captured_at": epoch + s.lap_ms / 1000})
                for s in samples
            ]
            epoch += duration / 1000
            self.demo_seed_last_ms = duration
            times = demo._profiles[number % 3][2]
            import numpy as np

            splits = np.interp(
                [0, 1 / 3, 2 / 3, 1], demo._profiles[number % 3][0], times
            )
            sectors = [round(v * 1000) for v in np.diff(splits)]
            sectors[-1] = duration - sum(sectors[:-1])
            lap = Lap(
                id=str(uuid.uuid4()),
                session_id=self.session_id,
                number=number + 1,
                duration_ms=duration,
                valid=True,
                validity_basis="synthetic demo",
                sectors_ms=sectors,
                sector_positions=[1 / 3, 2 / 3],
                samples=samples,
                created_at=utc_now(),
            )
            self._complete(lap, synchronous=True)
        # Live demo follows the examples with lap 3.
        self.recorder = None
        self.recorder = LapRecorder(self.session_id, self.meta)

    def _process(self):
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                meta, sample = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    if meta.source != self.active_source:
                        continue  # queued frame of a source that was switched off
                    if meta.source == "demo":
                        sample = sample.model_copy(
                            update={"completed_laps": sample.completed_laps + 2}
                        )
                    reset = (
                        self.latest
                        and sample.completed_laps < self.latest.completed_laps
                    )
                    reconnect = (
                        self.last_seen > 0 and time.monotonic() - self.last_seen > 10
                    )
                    if (
                        self.meta is None
                        or self.meta.identity() != meta.identity()
                        or reset
                        or reconnect
                    ):
                        self._new_session(meta)
                    if meta.source == "demo" and sample.last_lap_ms == 0:
                        sample = sample.model_copy(
                            update={"last_lap_ms": self.demo_seed_last_ms}
                        )
                    self.meta = meta
                    self.connected = True
                    self.last_seen = time.monotonic()
                    self.latest = sample
                    self.demo_ready.set()
                    self.ring.append(sample)
                    self.total_samples += 1
                    if sample.status == 2 and self.recorder:
                        lap = self.recorder.process(sample)
                        if lap:
                            self._lap_done(lap)
                            self._schedule_complete(lap)
                        if self.recorder.accepted and not sample.in_pit:
                            self._track_point(sample)
            except Exception as exc:
                self.error = "Analysis error: " + str(exc)[:160]
            finally:
                self.demo_ready.set()
                self.queue.task_done()

    def _track_point(self, sample):
        """Grow the provisional map over lap boundaries without stacking laps:
        every position bin keeps only its newest point."""
        pos = min(max(sample.lap_pos, 0.0), 1.0)
        self.partial_map[min(int(pos * MAP_BINS), MAP_BINS - 1)] = [
            pos,
            sample.coords[0],
            sample.coords[2],
        ]

    def _recover_map(self, meta, sid):
        """Build the map from an already stored lap that covers the full track."""
        try:
            for lap_id in self.db.map_candidates(meta):
                with self.lock:
                    if self.session_id != sid or self.map:
                        return
                lap = self.db.lap(lap_id)
                if not lap or not covers_full_lap(lap, meta.track_length):
                    continue
                full = lap.model_copy(update={"complete": True})
                generated = track_map(full, meta.track_length)
                if not generated:
                    continue
                generated["recovered"] = True
                self.db.save_map(meta, generated)
                with self.lock:
                    if self.session_id == sid and not self.map:
                        self.map = generated
                log.info("Track map recovered from a stored lap")
                return
        except Exception as exc:  # never break the live session over it
            log.warning("Track map recovery failed: %s", str(exc)[:160])

    def compatible(self, lap):
        if not lap or not self.meta:
            return False
        session = self.db.session(lap.session_id)
        m = session["meta"] if session else None
        return m and (m["source"], m["car"], m["track"], m["layout"]) == (
            self.meta.source,
            self.meta.car,
            self.meta.track,
            self.meta.layout,
        )

    def update_reference(self):
        with self.lock:
            chosen = (
                self.db.lap(self.settings.reference_lap_id)
                if self.settings.reference_lap_id
                else None
            )
            self.personal_best = self.db.best(self.meta) if self.meta else None
            self.reference = (
                chosen
                if self.compatible(chosen) and chosen.complete
                else self.personal_best
            )
            try:
                self.ref_profile = (
                    normalize(self.reference, self.meta.track_length)
                    if self.reference and self.meta.track_length > 0
                    else None
                )
                self.pb_profile = (
                    normalize(self.personal_best, self.meta.track_length)
                    if self.personal_best and self.meta.track_length > 0
                    else None
                )
            except ValueError:
                self.ref_profile = None
                self.pb_profile = None

    def _schedule_complete(self, lap):
        """Snapshot the session before handing the lap to the analysis worker."""
        if not lap or len(lap.samples) < 2:
            return
        with self.lock:
            context = (
                self.meta.model_copy(),
                self.session_id,
                self.settings.model_copy(),
                self.reference,
            )
            sid = context[1]
            self.inflight_sessions[sid] = self.inflight_sessions.get(sid, 0) + 1
        future = self.analyzer.submit(self._complete, lap, False, context)

        def finished(result):
            with self.lock:
                self.inflight_sessions[sid] -= 1
                if not self.inflight_sessions[sid]:
                    del self.inflight_sessions[sid]
                try:
                    result.result()
                except Exception as exc:
                    self.error = "Lap analysis/storage error: " + str(exc)[:160]

        future.add_done_callback(finished)

    def _complete(self, lap, synchronous=False, context=None):
        if not lap or len(lap.samples) < 2:
            return
        meta, sid, cfg, reference = context or (
            self.meta.model_copy(),
            self.session_id,
            self.settings.model_copy(),
            self.reference,
        )
        n, generated = None, None
        window = lap_wall_window(lap)
        if window:
            # Lap start/end on the server clock: links laps to onboard video.
            lap.analysis["wall_window"] = window
        if meta.track_length > 0:
            try:
                n = normalize(lap, meta.track_length)
                import numpy as np

                edges = np.linspace(0, meta.track_length, 21)
                minis = []
                for start, end in zip(edges[:-1], edges[1:]):
                    ta, tb = at(n, "time", start), at(n, "time", end)
                    minis.append(tb - ta if ta is not None and tb is not None else None)
                lap.analysis.update(
                    {
                        "mini_sectors_s": minis,
                        "fuel_start": lap.samples[0].channels.get("fuel"),
                        "end_tyre_core": {
                            w.corner: w.core for w in lap.samples[-1].tyres
                        },
                    }
                )
                lap.events = detect_events(lap, meta.track_length, cfg)
                lap.tips = coaching(lap, reference, meta.track_length, cfg)
                if lap.complete:
                    generated = track_map(lap, meta.track_length)
            except ValueError as exc:
                lap.analysis["error"] = str(exc)
        with self.lock:
            if self.session_id == sid:
                self.tips = lap.tips
                if generated and not self.map:
                    self.map = generated
        if not cfg.auto_save and not synchronous:
            return

        def store():
            self.db.save_lap(lap)
            for hook in self.lap_hooks:
                hook(lap)
            if generated:
                self.db.save_map(meta, generated)
            stats = summary_statistics(self.db.list_laps(sid))
            with self.lock:
                active_sid = self.session_id
                protected = tuple(self.inflight_sessions)
                ref_id = self.settings.reference_lap_id
            storage = self.db.enforce_limit(
                cfg.storage_mb, active_sid, ref_id, protected
            )
            with self.lock:
                if self.session_id == sid:
                    self.stats = stats
                    self.storage_status = storage

        future = None
        if synchronous:
            store()
        else:
            future = self.writer.submit(store)
        # Publish only to the session from which the snapshot was taken.
        # Normalization and storage never hold the live-frame mutex.
        with self.lock:
            if self.session_id == sid and lap.valid and lap.complete:
                if (
                    self.reference is None
                    or lap.duration_ms < self.reference.duration_ms
                ) and not self.settings.reference_lap_id:
                    self.reference, self.ref_profile = lap, n
                if (
                    self.personal_best is None
                    or lap.duration_ms < self.personal_best.duration_ms
                ):
                    self.personal_best, self.pb_profile = lap, n
        if future:
            # Wait in the dedicated analysis worker, never in capture/WS threads.
            future.result()

    def payload(self):
        with self.lock:
            s = self.latest
            delta = None
            pb_delta = None
            ghost = None
            ghost_coords = None
            fuel_laps = None
            if s and self.ref_profile:
                ref_time = at(
                    self.ref_profile, "time", s.lap_pos * self.meta.track_length
                )
                delta = s.lap_ms / 1000 - ref_time if ref_time is not None else None
                import numpy as np

                mask = np.isfinite(self.ref_profile["time"])
                if mask.any():
                    ghost = (
                        float(
                            np.interp(
                                s.lap_ms / 1000,
                                self.ref_profile["time"][mask],
                                self.ref_profile["distance"][mask],
                            )
                        )
                        / self.meta.track_length
                    )
                    x, z = (
                        at(self.ref_profile, "x", ghost * self.meta.track_length),
                        at(self.ref_profile, "z", ghost * self.meta.track_length),
                    )
                    if x is not None and z is not None:
                        ghost_coords = [x, z]
            if s and self.pb_profile:
                pb_time = at(
                    self.pb_profile, "time", s.lap_pos * self.meta.track_length
                )
                pb_delta = s.lap_ms / 1000 - pb_time if pb_time is not None else None
            if s and self.reference:
                start = self.reference.samples[0].channels.get("fuel")
                end = self.reference.samples[-1].channels.get("fuel")
                fuel = s.channels.get("fuel")
                if (
                    start is not None
                    and end is not None
                    and fuel is not None
                    and start - end > 0.05
                ):
                    fuel_laps = fuel / (start - end)
            sector_ms = None
            if s and self.recorder:
                prior = [self.recorder.sectors.get(i) for i in range(s.sector_index)]
                if all(v is not None for v in prior):
                    sector_ms = s.lap_ms - sum(prior)
            status = self.game_status()
            source_message = getattr(self.source, "error", "") or None
            return json_safe(
                {
                    "type": "telemetry",
                    "status": status,
                    "source": self.active_source,
                    # Data is flowing from the selected source (demo or game).
                    "connected": bool(s and (status in GAME_STATES or status == "demo")),
                    # Real AC telemetry only; never true for demo data.
                    "game_connected": bool(
                        s and s.source == "ac" and status in GAME_STATES
                    ),
                    "source_message": None if status == "demo" else source_message,
                    "error": self.error
                    or (
                        source_message
                        if status in ("access_denied", "decode_error", "memory_error")
                        else None
                    )
                    or (self.capture_error if status == "capture_failed" else None),
                    "meta": self.meta.model_dump(exclude={"raw_static"})
                    if self.meta
                    else None,
                    "sample": s.model_dump() if s else None,
                    "session_id": self.session_id,
                    "reference_id": self.reference.id if self.reference else None,
                    "reference_ms": self.reference.duration_ms
                    if self.reference
                    else None,
                    "delta_s": delta,
                    "personal_best_delta_s": pb_delta,
                    "personal_best_ms": self.personal_best.duration_ms
                    if self.personal_best
                    else None,
                    "ghost_pos": ghost,
                    "ghost_coords": ghost_coords,
                    "fuel_laps_est": fuel_laps,
                    "current_sector_ms": sector_ms,
                    "statistics": {
                        k: v
                        for k, v in self.stats.items()
                        if k not in ("stint", "outliers")
                    },
                    "tips": self.tips,
                    "lap_reasons": sorted(self.recorder.reasons)
                    if self.recorder
                    else [],
                    "timing": timing_payload(
                        s,
                        self.meta,
                        self.recorder,
                        self.last_lap,
                        self.reference_info(),
                        self.best_sectors,
                        self.settings.hud_hold_s,
                        time.monotonic(),
                    ),
                    "tyre_warnings": tyre_warnings(s.tyres, self.settings) if s else [],
                    "recording": bool(
                        self.settings.auto_save
                        and status in ("live", "demo")
                        and s
                        and s.status == 2
                    ),
                    "storage": self.storage_status,
                    "performance": {
                        "samples": self.total_samples,
                        "dropped": self.dropped,
                        "queue_depth": self.queue.qsize(),
                        "uptime_s": time.monotonic() - self.started,
                    },
                }
            )

    def map_payload(self):
        with self.lock:
            return self.map or {
                "points": [self.partial_map[k] for k in sorted(self.partial_map)],
                "complete": False,
                "coverage": len(self.partial_map) / MAP_BINS,
                "source": self.meta.source if self.meta else None,
                "sector_positions": [],
                "events": [],
            }

    def game_status(self):
        if self.threads and not self.capture_alive() and not self.stop_event.is_set():
            return "capture_failed"
        if self.active_source == "demo":
            return "demo"
        state = self.source_state
        return "live" if state == "connected" else state

    def diagnostics(self):
        from .diagnostics import hints

        source = self.source
        game = source.diagnostics() if hasattr(source, "diagnostics") else None
        with self.lock:
            latest, last_seen = self.latest, self.last_seen
        now = time.monotonic()
        report = {
            "generated_at": utc_now(),
            "status": self.game_status(),
            "system": {
                "os": platform.platform(),
                "windows": sys.platform == "win32",
                "python": platform.python_version(),
                "python_bits": struct.calcsize("P") * 8,
                "machine": platform.machine(),
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "source": {
                "selected": self.settings.source,
                "active": self.active_source,
            },
            "capture_thread": {
                "alive": self.capture_alive(),
                "restarts": self.capture_restarts,
                "errors": self.capture_errors,
                "last_error": self.capture_error,
                "last_error_at": datetime.fromtimestamp(
                    self.capture_error_at, timezone.utc
                ).isoformat()
                if self.capture_error_at
                else None,
                "exit_reason": self.capture_exit,
            },
            "game": game,
            "engine": {
                "analysis_thread_alive": len(self.threads) > 1
                and self.threads[1].is_alive(),
                "samples": self.total_samples,
                "dropped": self.dropped,
                "queue_depth": self.queue.qsize(),
                "last_sample_age_s": round(now - last_seen, 2) if last_seen else None,
                "last_sample_source": latest.source if latest else None,
                "analysis_error": self.error,
            },
            "websocket": {
                "clients": self.ws_clients,
                "last_send_age_s": round(now - self.ws_last_send, 2)
                if self.ws_last_send
                else None,
            },
        }
        report["hints"] = hints(report)
        return report
