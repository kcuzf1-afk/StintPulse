"""Lap/sector state machine; joining midlap cannot create a valid full lap."""

from datetime import datetime, timezone
import uuid
from .models import Lap, Sample


def utc_now():
    return datetime.now(timezone.utc).isoformat()


LINE_HIGH = 0.96  # lap position just before the start/finish line
CROSSING_MS = 3000  # how long the two line signals may disagree


class LapRecorder:
    """At the start/finish line AC updates the spline position and the lap
    counter in separate frames, in either order. Both orders are ONE line
    crossing, never a teleport; samples in between belong to no lap."""

    def __init__(self, session_id, meta):
        self.session_id, self.meta = session_id, meta
        self.samples = []
        self.sectors = {}
        self.sector_positions = {}
        self.reasons = set()
        self.last = None
        self.complete = False
        self.crossing = None  # lap_ms when the spline wrapped before the counter
        self.await_wrap = None  # lap_ms when the counter changed before the spline
        self.accepted = False  # last processed sample was recorded

    def _begin(self, sample, boundary=False):
        self.samples = []
        self.sectors = {}
        self.sector_positions = {}
        self.reasons = set()
        self.complete = boundary or (sample.lap_pos < 0.02 and sample.lap_ms < 2500)
        if not self.complete:
            self.reasons.add("partial_start")
        if boundary:
            self.samples.append(
                sample.model_copy(
                    update={"lap_pos": 0.0, "lap_ms": 0, "synthetic_boundary": True}
                )
            )

    @staticmethod
    def _wraps_at_line(last, s):
        """Spline jumped from the end of the lap to its start while the lap
        clock kept running or restarted at the line. Real AC 1.16 (Sepang)
        resets position, iCurrentTime and iLastTime one frame before
        completedLaps."""
        return (
            last.lap_pos > 0.9
            and s.lap_pos < 0.1
            and s.completed_laps == last.completed_laps
            and (s.lap_ms >= last.lap_ms or s.lap_ms <= CROSSING_MS)
        )

    def process(self, s: Sample):
        self.accepted = False
        if s.status != 2:  # do not record menu, replay or pause as live laps
            return None
        finished = None
        last = self.last
        if last is None:
            self._begin(s)
        elif s.completed_laps != last.completed_laps:
            if s.completed_laps == last.completed_laps + 1:
                finished = self.finish(s.last_lap_ms, s)
            else:
                finished = self.abort("lap_counter_reset_or_gap")
            at_line = s.lap_ms < 4000 and (s.lap_pos < 0.04 or s.lap_pos > LINE_HIGH)
            self._begin(s, boundary=at_line)
            self.crossing = None
            if at_line and s.lap_pos > 0.5:
                # Counter first: the last metres before the line belong to no lap.
                self.await_wrap = s.lap_ms
                self.last = s
                return finished
        elif self.crossing is not None:
            # The lap clock may restart before the counter follows.
            waited = s.lap_ms - self.crossing if s.lap_ms >= self.crossing else s.lap_ms
            if waited <= CROSSING_MS and s.lap_pos <= 0.15:
                return None  # same line crossing; wait for the lap counter
            # The spline wrap was not the timing line (or the session restarted).
            self.crossing = None
            finished = self.abort("spline_wrap_without_lap_counter")
            self._begin(s)
        elif self.await_wrap is not None:
            if s.lap_pos > 0.5:
                if s.lap_ms - self.await_wrap <= CROSSING_MS:
                    self.last = s
                    return None  # still before the line
                self.reasons.add("line_position_mismatch")
                self.complete = False
            self.await_wrap = None
        elif self._wraps_at_line(last, s):
            # Spline first: the lap counter follows within a few frames.
            self.crossing = s.lap_ms
            return None
        elif s.lap_ms + 1500 < last.lap_ms or s.lap_pos + 0.08 < last.lap_pos:
            finished = self.abort("teleport_or_restart")
            self._begin(s)
        elif s.sector_index != self.last.sector_index:
            if s.sector_index == self.last.sector_index + 1 and s.last_sector_ms > 0:
                self.sectors[self.last.sector_index] = s.last_sector_ms
                self.sector_positions[self.last.sector_index] = s.lap_pos
            else:
                self.reasons.add("sector_gap")
        if self.last and s.completed_laps == self.last.completed_laps:
            distance_gap = (s.lap_pos - self.last.lap_pos) * self.meta.track_length
            if distance_gap > 100:
                self.reasons.add("capture_gap")
        if s.tyres_out >= 3:
            self.reasons.add("off_track_inferred")
        if s.penalty_s > 0:
            self.reasons.add("penalty")
        if s.in_pit:
            self.reasons.add("pit_lap")
        if s.channels.get("ai_controlled"):
            self.reasons.add("ai_controlled")
        self.samples.append(s)
        self.last = s
        self.accepted = True
        return finished

    def finish(self, duration, boundary=None):
        if not self.samples:
            return None
        duration = duration or self.samples[-1].lap_ms
        complete = self.complete and self.samples[-1].lap_pos > 0.96 and duration > 0
        if not complete:
            self.reasons.add("incomplete_distance")
        # Boundary estimates are explicitly tagged; official lap duration wins.
        if boundary and complete:
            self.samples.append(
                boundary.model_copy(
                    update={
                        "lap_pos": 1.0,
                        "lap_ms": duration,
                        "completed_laps": self.samples[-1].completed_laps,
                        "synthetic_boundary": True,
                    }
                )
            )
        sector_count = self.meta.sector_count
        sectors = [self.sectors.get(i) for i in range(sector_count)]
        if all(v is not None for v in sectors[:-1]) and complete:
            remainder = duration - sum(v for v in sectors[:-1] if v is not None)
            sectors[-1] = remainder if remainder > 0 else None
        lap = Lap(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            number=self.samples[0].completed_laps + 1,
            duration_ms=duration,
            valid=complete and not self.reasons,
            complete=complete,
            reasons=sorted(self.reasons),
            sectors_ms=sectors,
            sector_positions=[
                self.sector_positions[i] for i in sorted(self.sector_positions)
            ],
            samples=self.samples.copy(),
            created_at=utc_now(),
        )
        self.samples = []
        return lap

    def abort(self, reason):
        self.reasons.add(reason)
        self.complete = False
        return self.finish(self.samples[-1].lap_ms if self.samples else 0)


def lap_wall_window(lap):
    """(start, end) of a lap on the server wall clock (``captured_at``).

    A complete lap is bounded by its synthetic line samples, which are copies
    of the frames in which the car crossed the line. That holds even when the
    game was paused mid-lap (the lap clock stops, the wall clock does not).
    Partial laps span their first to last recorded frame."""
    samples = lap.samples
    if len(samples) < 2:
        return None
    if lap.complete and samples[0].synthetic_boundary and samples[-1].synthetic_boundary:
        return [samples[0].captured_at, samples[-1].captured_at]
    real = [s for s in samples if not s.synthetic_boundary] or samples
    return [real[0].captured_at, real[-1].captured_at]
