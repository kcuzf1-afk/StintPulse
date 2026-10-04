"""Verified original Assetto Corsa 1.16 ABI. Sources: docs/DATA_SOURCES.md.

UTF-16 code units are explicit: ctypes.c_wchar is 4 bytes on Linux and must
never be used to decode a Windows page in a portable decoder.
"""

from __future__ import annotations

import ctypes as C
import logging
import math
import sys
import time
from collections import deque
from datetime import datetime, timezone
from .models import SessionMeta, Sample, Tyre, Settings, finite

log = logging.getLogger(__name__)

I, F, W = C.c_int32, C.c_float, C.c_uint16
F3, F4 = F * 3, F * 4


class Physics(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("packetId", I),
        ("gas", F),
        ("brake", F),
        ("fuel", F),
        ("gear", I),
        ("rpms", I),
        ("steerAngle", F),
        ("speedKmh", F),
        ("velocity", F3),
        ("accG", F3),
        ("wheelSlip", F4),
        ("wheelLoad", F4),
        ("wheelsPressure", F4),
        ("wheelAngularSpeed", F4),
        ("tyreWear", F4),
        ("tyreDirtyLevel", F4),
        ("tyreCoreTemperature", F4),
        ("camberRAD", F4),
        ("suspensionTravel", F4),
        ("drs", F),
        ("tc", F),
        ("heading", F),
        ("pitch", F),
        ("roll", F),
        ("cgHeight", F),
        ("carDamage", F * 5),
        ("numberOfTyresOut", I),
        ("pitLimiterOn", I),
        ("abs", F),
        ("kersCharge", F),
        ("kersInput", F),
        ("autoShifterOn", I),
        ("rideHeight", F * 2),
        ("turboBoost", F),
        ("ballast", F),
        ("airDensity", F),
        ("airTemp", F),
        ("roadTemp", F),
        ("localAngularVel", F3),
        ("finalFF", F),
        ("performanceMeter", F),
        ("engineBrake", I),
        ("ersRecoveryLevel", I),
        ("ersPowerLevel", I),
        ("ersHeatCharging", I),
        ("ersIsCharging", I),
        ("kersCurrentKJ", F),
        ("drsAvailable", I),
        ("drsEnabled", I),
        ("brakeTemp", F4),
        ("clutch", F),
        ("tyreTempI", F4),
        ("tyreTempM", F4),
        ("tyreTempO", F4),
        ("isAIControlled", I),
        ("tyreContactPoint", F3 * 4),
        ("tyreContactNormal", F3 * 4),
        ("tyreContactHeading", F3 * 4),
        ("brakeBias", F),
        ("localVelocity", F3),
    ]


class Graphics(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("packetId", I),
        ("status", I),
        ("session", I),
        ("currentTime", W * 15),
        ("lastTime", W * 15),
        ("bestTime", W * 15),
        ("split", W * 15),
        ("completedLaps", I),
        ("position", I),
        ("iCurrentTime", I),
        ("iLastTime", I),
        ("iBestTime", I),
        ("sessionTimeLeft", F),
        ("distanceTraveled", F),
        ("isInPit", I),
        ("currentSectorIndex", I),
        ("lastSectorTime", I),
        ("numberOfLaps", I),
        ("tyreCompound", W * 33),
        ("replayTimeMultiplier", F),
        ("normalizedCarPosition", F),
        ("carCoordinates", F3),
        ("penaltyTime", F),
        ("flag", I),
        ("idealLineOn", I),
        ("isInPitLine", I),
        ("surfaceGrip", F),
        ("mandatoryPitDone", I),
        ("windSpeed", F),
        ("windDirection", F),
    ]


class Static(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("smVersion", W * 15),
        ("acVersion", W * 15),
        ("numberOfSessions", I),
        ("numCars", I),
        ("carModel", W * 33),
        ("track", W * 33),
        ("playerName", W * 33),
        ("playerSurname", W * 33),
        ("playerNick", W * 33),
        ("sectorCount", I),
        ("maxTorque", F),
        ("maxPower", F),
        ("maxRpm", I),
        ("maxFuel", F),
        ("suspensionMaxTravel", F4),
        ("tyreRadius", F4),
        ("maxTurboBoost", F),
        ("airTemp", F),
        ("roadTemp", F),
        ("penaltiesEnabled", I),
        ("aidFuelRate", F),
        ("aidTireRate", F),
        ("aidMechanicalDamage", F),
        ("aidAllowTyreBlankets", I),
        ("aidStability", F),
        ("aidAutoClutch", I),
        ("aidAutoBlip", I),
        ("hasDRS", I),
        ("hasERS", I),
        ("hasKERS", I),
        ("kersMaxJ", F),
        ("engineBrakeSettingsCount", I),
        ("ersPowerControllerCount", I),
        ("trackSPlineLength", F),
        ("trackConfiguration", W * 33),
        ("ersMaxJ", F),
        ("isTimedRace", I),
        ("hasExtraLap", I),
        ("carSkin", W * 33),
        ("reversedGridPositions", I),
        ("pitWindowStart", I),
        ("pitWindowEnd", I),
    ]


def utf16(field):
    return bytes(field).decode("utf-16-le", errors="replace").split("\0", 1)[0]


def decode(structure, raw: bytes):
    if len(raw) < C.sizeof(structure):
        raise ValueError(f"Truncated {structure.__name__} page: {len(raw)} bytes")
    return structure.from_buffer_copy(raw[: C.sizeof(structure)])


def raw_page(page):
    def convert(value):
        if isinstance(value, (int, float)):
            return finite(value)
        if isinstance(value, C.Array):
            if value._type_ is W:
                return utf16(value)
            return [convert(v) for v in value]
        return None

    return {name: convert(getattr(page, name)) for name, _ in page._fields_}


def flatten_numeric(prefix, value, output):
    if isinstance(value, list):
        for i, v in enumerate(value):
            flatten_numeric(f"{prefix}_{i}", v, output)
    elif isinstance(value, (int, float)) or value is None:
        output[prefix] = value




# graphics.status (AC_STATUS) of the original game.
STATUS_NAMES = {0: "AC_OFF", 1: "AC_REPLAY", 2: "AC_LIVE", 3: "AC_PAUSE"}


class DecodeError(ValueError):
    """A concrete, loggable failure to decode one frame. Never fatal for capture."""


def decode_frame(p: Physics, g: Graphics, s: Static, settings: Settings):
    if g.status not in STATUS_NAMES:
        raise DecodeError(
            f"Unknown graphics.status={g.status}; page does not match the original AC layout"
        )
    if not math.isfinite(g.normalizedCarPosition) or not all(
        math.isfinite(v) for v in g.carCoordinates
    ):
        raise DecodeError(
            "Non-finite position (graphics.normalizedCarPosition/carCoordinates)"
        )
    # Single unusual metadata values (often from mods) must not block the whole
    # stream. They are replaced by an explicit "unknown" and listed in the meta.
    unknown = []
    sector_count = s.sectorCount
    if not 1 <= sector_count <= 20:
        unknown.append(
            f"static.sectorCount={sector_count} outside 1..20; lap handled as one sector"
        )
        sector_count = 1
    length = finite(s.trackSPlineLength, 0)
    if not 0 <= length <= 100000:
        unknown.append(f"static.trackSPlineLength={length} outside 0..100000 m")
        length = 0
    max_rpm = s.maxRpm
    if not 0 <= max_rpm <= 50000:
        unknown.append(f"static.maxRpm={max_rpm} outside 0..50000")
        max_rpm = 0

    def text(value, limit, field):
        if len(value) > limit:
            unknown.append(f"{field} truncated from {len(value)} characters")
            return value[:limit]
        return value

    meta = SessionMeta(
        source="ac",
        driver=text(
            " ".join(filter(None, [utf16(s.playerName), utf16(s.playerSurname)]))
            or utf16(s.playerNick),
            150,
            "static.playerName",
        ),
        car=text(utf16(s.carModel), 100, "static.carModel"),
        track=text(utf16(s.track), 100, "static.track"),
        layout=text(utf16(s.trackConfiguration), 100, "static.trackConfiguration"),
        session_type=g.session,
        track_length=length,
        sector_count=sector_count,
        max_rpm=max_rpm,
        max_fuel=max(0, finite(s.maxFuel, 0)),
        tyre_radii=[max(0, finite(v, 0)) for v in s.tyreRadius],
        has_drs=bool(s.hasDRS),
        has_ers=bool(s.hasERS),
        has_kers=bool(s.hasKERS),
        compound=utf16(g.tyreCompound),
        air_temp=finite(p.airTemp),
        road_temp=finite(p.roadTemp),
        ac_version=utf16(s.acVersion),
        sm_version=utf16(s.smVersion),
        raw_static=raw_page(s),
        unknown_fields=unknown,
    )
    channels = dict(
        speed=finite(p.speedKmh),
        rpm=float(p.rpms),
        gear=float(p.gear - 1),
        gas=finite(p.gas),
        brake=finite(p.brake),
        clutch=finite(p.clutch),
        steer=finite(p.steerAngle),
        steer_deg=finite(p.steerAngle * settings.steering_lock_deg)
        if settings.steering_lock_deg
        else None,
        g_lat=finite(p.accG[0]),
        g_vert=finite(p.accG[1]),
        g_long=finite(p.accG[2]),
        fuel=finite(p.fuel),
        yaw_rate=finite(p.localAngularVel[1]),
        local_vx=finite(p.localVelocity[0]),
        local_vz=finite(p.localVelocity[2]),
        ride_front=finite(p.rideHeight[0]),
        ride_rear=finite(p.rideHeight[1]),
        cg_height=finite(p.cgHeight),
        brake_bias=finite(p.brakeBias),
        abs_raw=finite(p.abs),
        tc_raw=finite(p.tc),
        abs_active=None,
        tc_active=None,
        engine_temp=None,
        engine_health=None,
        aero_damage=None,
        brake_pressure=None,
        damper_velocity=None,
        bottoming=None,
        turbo=finite(p.turboBoost),
        drs=finite(p.drs) if s.hasDRS else None,
        drs_available=float(p.drsAvailable) if s.hasDRS else None,
        kers_charge=finite(p.kersCharge) if s.hasKERS or s.hasERS else None,
        kers_input=finite(p.kersInput) if s.hasKERS else None,
        ers_kj=finite(p.kersCurrentKJ) if s.hasERS else None,
        ers_power=float(p.ersPowerLevel) if s.hasERS else None,
        air_temp=finite(p.airTemp),
        road_temp=finite(p.roadTemp),
        pit_limiter=float(p.pitLimiterOn),
        ai_controlled=float(p.isAIControlled),
    )
    for i, v in enumerate(p.carDamage):
        channels[f"damage_{i}"] = finite(v)
    # Preserve every numeric field in the verified ABI, with explicit raw names.
    for prefix, page in (("raw_physics", p), ("raw_graphics", g)):
        for name, value in raw_page(page).items():
            flatten_numeric(prefix + "_" + name, value, channels)
    tyres = []
    for i, corner in enumerate(("FL", "FR", "RL", "RR")):
        wheel_ms = abs(float(p.wheelAngularSpeed[i])) * meta.tyre_radii[i]
        speed_ms = abs(float(p.localVelocity[2]))
        usable = (
            meta.tyre_radii[i] > 0.05
            and speed_ms > 8.3
            and math.isfinite(wheel_ms)
            and math.isfinite(speed_ms)
        )
        tyre = Tyre(
            corner=corner,
            core=finite(p.tyreCoreTemperature[i]),
            inner=finite(p.tyreTempI[i]),
            middle=finite(p.tyreTempM[i]),
            outer=finite(p.tyreTempO[i]),
            # No independent surface-temperature field in the verified base ABI.
            pressure=finite(p.wheelsPressure[i]),
            wear_raw=finite(p.tyreWear[i]),
            load=finite(p.wheelLoad[i]),
            slip_raw=finite(p.wheelSlip[i]),
            angular_speed=finite(p.wheelAngularSpeed[i]),
            brake_temp=finite(p.brakeTemp[i]),
            travel=finite(p.suspensionTravel[i]),
            locked=bool(
                p.brake > settings.brake_threshold
                and wheel_ms / speed_ms < settings.lock_ratio
            )
            if usable
            else None,
            spinning=bool(p.gas > 0.3 and wheel_ms / speed_ms > settings.spin_ratio)
            if usable
            else None,
        )
        tyres.append(tyre)
        for key in (
            "core",
            "inner",
            "middle",
            "outer",
            "pressure",
            "wear_raw",
            "load",
            "slip_raw",
            "angular_speed",
            "brake_temp",
            "travel",
        ):
            channels[f"{key}_{corner}"] = getattr(tyre, key)
    sample = Sample(
        source="ac",
        packet_id=p.packetId,
        captured_at=time.time(),
        status=g.status,
        completed_laps=max(0, g.completedLaps),
        lap_ms=max(0, g.iCurrentTime),
        last_lap_ms=max(0, g.iLastTime),
        best_lap_ms=max(0, g.iBestTime),
        sector_index=max(0, min(sector_count - 1, g.currentSectorIndex)),
        last_sector_ms=max(0, g.lastSectorTime),
        position=g.position,
        session_left_ms=None,
        lap_pos=min(1, max(0, finite(g.normalizedCarPosition, 0))),
        coords=tuple(finite(v, 0) for v in g.carCoordinates),
        in_pit=bool(g.isInPit or g.isInPitLine),
        tyres_out=p.numberOfTyresOut,
        penalty_s=finite(g.penaltyTime, 0),
        channels=channels,
        tyres=tyres,
    )
    return meta, sample


class SessionTimer:
    """Verify timer units from observed rate against the millisecond lap clock.

    No ACC-derived unit assumption. Unknown/non-counting timers remain null.
    """

    def __init__(self):
        self.anchor = None
        self.scale = None
        self.unit = "unknown"

    def read(self, lap_count, lap_ms, remaining, status):
        if remaining is None or remaining < 0:
            return None
        if self.scale is not None:
            return remaining * self.scale
        if status != 2:
            return None
        if (
            self.anchor is None
            or self.anchor[0] != lap_count
            or lap_ms < self.anchor[1]
        ):
            self.anchor = (lap_count, lap_ms, remaining)
        elif lap_ms - self.anchor[1] >= 1500:
            ratio = (self.anchor[2] - remaining) / (lap_ms - self.anchor[1])
            if 0.8 <= ratio <= 1.2:
                self.scale, self.unit = 1.0, "ms"
            elif 0.0008 <= ratio <= 0.0012:
                self.scale, self.unit = 1000.0, "s"
            else:
                self.anchor = (lap_count, lap_ms, remaining)
        return remaining * self.scale if self.scale else None


# --- Windows access -------------------------------------------------------

FILE_MAP_READ = 0x0004
PAGE_READONLY = 0x02
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_TOO_SMALL = -1  # project-specific: mapping smaller than the verified struct

GAME_PROCESSES = ("acs.exe", "acs_x86.exe")
# A launcher alone is never a driving session; reported for diagnostics only.
LAUNCHER_PROCESSES = (
    "assettocorsa.exe",
    "content manager.exe",
    "content manager safe.exe",
)


class MappingError(OSError):
    def __init__(self, page, code, message):
        super().__init__(code, message)
        self.page, self.code, self.message = page, code, message


def _win_message(code):
    try:
        return C.FormatError(code).strip()
    except Exception:
        return f"Windows error {code}"


_KERNEL = None


def _kernel32():
    global _KERNEL
    if _KERNEL is None:
        k = C.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.argtypes = [C.c_uint32, C.c_int, C.c_wchar_p]
        k.OpenFileMappingW.restype = C.c_void_p
        k.MapViewOfFile.argtypes = [
            C.c_void_p,
            C.c_uint32,
            C.c_uint32,
            C.c_uint32,
            C.c_size_t,
        ]
        k.MapViewOfFile.restype = C.c_void_p
        k.UnmapViewOfFile.argtypes = [C.c_void_p]
        k.UnmapViewOfFile.restype = C.c_int
        k.CloseHandle.argtypes = [C.c_void_p]
        k.CloseHandle.restype = C.c_int
        k.VirtualQuery.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
        k.VirtualQuery.restype = C.c_size_t
        _KERNEL = k
    return _KERNEL


class _MemoryInfo(C.Structure):
    # MEMORY_BASIC_INFORMATION; PartitionId only exists in the 64-bit layout.
    _fields_ = [
        ("BaseAddress", C.c_void_p),
        ("AllocationBase", C.c_void_p),
        ("AllocationProtect", C.c_uint32),
    ]
    _fields_ += [("PartitionId", C.c_uint16)] if C.sizeof(C.c_void_p) == 8 else []
    _fields_ += [
        ("RegionSize", C.c_size_t),
        ("State", C.c_uint32),
        ("Protect", C.c_uint32),
        ("Type", C.c_uint32),
    ]


class WindowsPage:
    """Read-only view of an EXISTING named mapping.

    OpenFileMappingW(FILE_MAP_READ) never creates a mapping, so a missing game
    page can never be replaced by an empty fake page. The view is mapped with
    FILE_MAP_READ only; Windows marks it PAGE_READONLY.
    """

    def __init__(self, name, size):
        k = _kernel32()
        self.name, self.size = name, size
        self.handle = k.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not self.handle:
            code = C.get_last_error()
            raise MappingError(name, code, _win_message(code))
        self.view = k.MapViewOfFile(self.handle, FILE_MAP_READ, 0, 0, 0)
        if not self.view:
            code = C.get_last_error()
            k.CloseHandle(self.handle)
            raise MappingError(name, code, _win_message(code))
        info = _MemoryInfo()
        if k.VirtualQuery(self.view, C.byref(info), C.sizeof(info)):
            self.mapped_bytes, self.protect = int(info.RegionSize), int(info.Protect)
        else:
            self.mapped_bytes, self.protect = None, None
        if self.mapped_bytes is not None and self.mapped_bytes < size:
            mapped = self.mapped_bytes
            self.close()
            raise MappingError(
                name,
                ERROR_TOO_SMALL,
                f"Mapping has {mapped} bytes, original AC layout needs {size}",
            )

    @property
    def read_only(self):
        return None if self.protect is None else self.protect == PAGE_READONLY

    def read(self, count=None):
        return C.string_at(self.view, self.size if count is None else count)

    def close(self):
        k = _kernel32()
        if self.view:
            k.UnmapViewOfFile(self.view)
            self.view = None
        if self.handle:
            k.CloseHandle(self.handle)
            self.handle = None


def probe_processes():
    """Process check is diagnostic only; it never blocks readable pages."""
    try:
        import psutil
    except ImportError as exc:
        return {"status": "unavailable", "game": [], "launcher": [], "error": str(exc)}
    try:
        game, launcher = set(), set()
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in GAME_PROCESSES:
                game.add(name)
            elif name in LAUNCHER_PROCESSES:
                launcher.add(name)
        return {
            "status": "found" if game else "not_found",
            "game": sorted(game),
            "launcher": sorted(launcher),
            "error": None,
        }
    except Exception as exc:  # psutil.Error, OSError, permission problems
        return {
            "status": "unavailable",
            "game": [],
            "launcher": [],
            "error": (type(exc).__name__ + ": " + str(exc))[:200],
        }


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


class ACSource:
    """Original-AC shared memory reader with explicit, diagnosable states.

    States: unsupported_platform, waiting_game, waiting_session, access_denied,
    memory_error, not_initialized, connected, paused, replay, stale,
    decode_error.

    packetId double-check protects each individual page; physics and graphics
    clocks are independent, so cross-page atomicity is not claimed.
    """

    names = (
        ("acpmf_physics", Physics),
        ("acpmf_graphics", Graphics),  # original AC: plural "graphics"
        ("acpmf_static", Static),
    )
    RETRY_S = 1.0
    PROCESS_INTERVAL_S = 2.0
    STALE_S = 2.0
    RELEASE_S = 5.0

    def __init__(self, page_opener=None, process_probe=None, clock=None):
        self.page_opener = page_opener
        self.process_probe = process_probe or probe_processes
        self.clock = clock or time.monotonic
        self.maps = []
        self.state = "waiting_game"
        self.error = "Waiting for Assetto Corsa"
        self.process = {"status": "unchecked", "game": [], "launcher": [], "error": None}
        self.page_info = {}
        self._reset_page_info()
        self.next_connect_at = 0.0
        self.next_process_at = 0.0
        self.last_key = None
        self.last_change = None
        self.physics_packet = self.graphics_packet = self.game_status = None
        self.last_decode_at = None
        self.last_decode_error = None
        self.last_decode_error_at = None
        self.decode_errors = 0
        self.torn_snapshots = 0
        self.connects = 0
        self.samples = 0
        self.unknown_fields = []
        self._rates = deque(maxlen=1024)
        self._logged = {}
        self.timer = SessionTimer()
        self.session_identity = None

    # -- state ----------------------------------------------------------

    def _reset_page_info(self):
        for name, struct in self.names:
            self.page_info[name] = {
                "name": name,
                "present": False,
                "readable": False,
                "initialized": False,
                "read_only": None,
                "expected_bytes": C.sizeof(struct),
                "read_bytes": 0,
                "mapped_bytes": None,
                "error_code": None,
                "error": None,
            }

    def _set(self, state, message):
        if state != self.state:
            log.info("AC source state %s -> %s: %s", self.state, state, message)
        self.state, self.error = state, message

    def _log_once(self, key, message, interval=30.0):
        now = self.clock()
        if now - self._logged.get(key, -1e9) >= interval:
            self._logged[key] = now
            log.warning(message)

    def _check_process(self, now):
        self.process = self.process_probe()
        self.next_process_at = now + self.PROCESS_INTERVAL_S

    def _open(self, name, size):
        if self.page_opener:
            return self.page_opener(name, size)
        return WindowsPage(name, size)

    # -- connection -----------------------------------------------------

    def connect(self):
        now = self.clock()
        if sys.platform != "win32" and self.page_opener is None:
            self._set(
                "unsupported_platform",
                "Real AC shared memory is Windows-only; use Demo on this system",
            )
            return False
        self._check_process(now)
        self._reset_page_info()
        opened, failures = [], []
        for name, struct in self.names:
            info = self.page_info[name]
            try:
                page = self._open(name, C.sizeof(struct))
            except MappingError as exc:
                info.update(
                    present=exc.code != ERROR_FILE_NOT_FOUND,
                    error_code=exc.code,
                    error=exc.message,
                )
                failures.append(exc)
                continue
            except OSError as exc:
                info.update(error=(type(exc).__name__ + ": " + str(exc))[:200])
                failures.append(MappingError(name, getattr(exc, "winerror", None), str(exc)))
                continue
            info.update(
                present=True,
                readable=True,
                read_only=page.read_only,
                mapped_bytes=page.mapped_bytes,
            )
            opened.append(page)
        if failures:
            for page in opened:
                page.close()
            codes = {f.code for f in failures}
            game = self.process["status"] == "found"
            if ERROR_ACCESS_DENIED in codes:
                self._set(
                    "access_denied",
                    "Access denied to AC shared memory (Windows error 5)",
                )
            elif codes <= {ERROR_FILE_NOT_FOUND}:
                missing = ", ".join(f.page for f in failures)
                self._set(
                    "waiting_session" if game else "waiting_game",
                    ("AC process running, shared memory not yet created: " if game else "Assetto Corsa not running / no session: ")
                    + "missing "
                    + missing,
                )
            else:
                first = next(f for f in failures if f.code != ERROR_FILE_NOT_FOUND)
                self._set("memory_error", f"{first.page}: {first.message}")
                self._log_once("map:" + first.message, "AC mapping error: " + self.error)
            return False
        self.maps = opened
        self.connects += 1
        self.last_key, self.last_change = None, now
        self._set("not_initialized", "Shared memory opened; waiting for first data")
        return True

    def request_reconnect(self):
        """Used by 'Verbindung erneut prüfen': drop handles, check again now."""
        self.close()
        self.next_connect_at = 0.0
        self.next_process_at = 0.0
        self._set("waiting_game", "Re-checking connection")

    def close(self):
        for page in self.maps:
            try:
                page.close()
            except Exception as exc:  # never let cleanup kill capture
                log.warning("Closing %s failed: %s", getattr(page, "name", "?"), exc)
        self.maps = []

    # -- reading --------------------------------------------------------

    def _snapshot(self, page, static):
        for _ in range(3):
            raw = page.read()
            if static:
                if raw == page.read():
                    return raw
            elif raw[:4] == page.read(4):
                return raw
        self.torn_snapshots += 1
        return None

    def read(self, settings):
        now = self.clock()
        if not self.maps:
            if now < self.next_connect_at:
                return None
            # At most one connection attempt and process scan per second.
            self.next_connect_at = now + self.RETRY_S
            if not self.connect():
                return None
        elif now >= self.next_process_at:
            self._check_process(now)
        try:
            return self._read_pages(settings, now)
        except DecodeError as exc:
            self.decode_errors += 1
            self.last_decode_error = str(exc)[:300]
            self.last_decode_error_at = time.time()
            self._set("decode_error", self.last_decode_error)
            self._log_once("decode:" + self.last_decode_error, "AC decode error: " + self.last_decode_error)
            return None

    def _read_pages(self, settings, now):
        raws = []
        for page, (name, struct) in zip(self.maps, self.names):
            raw = self._snapshot(page, struct is Static)
            if raw is None:
                return None
            self.page_info[name]["read_bytes"] = len(raw)
            raws.append(raw)
        try:
            p, g, s = [decode(struct, raw) for (_, struct), raw in zip(self.names, raws)]
        except ValueError as exc:
            raise DecodeError(str(exc)) from exc
        self.physics_packet, self.graphics_packet = p.packetId, g.packetId
        self.game_status = g.status
        static_ready = bool(utf16(s.carModel) or utf16(s.track))
        for (name, _), ready in zip(
            self.names, (p.packetId != 0, g.packetId != 0, static_ready)
        ):
            self.page_info[name]["initialized"] = ready
        key = (p.packetId, g.packetId)
        changed = key != self.last_key
        if changed:
            self.last_key, self.last_change = key, now
        if g.status == 0:
            self._set("waiting_session", "AC reports AC_OFF: no active driving session")
            return self._maybe_release(now)
        if not static_ready:
            self._set(
                "not_initialized",
                "Static page not initialized yet (carModel/track empty)",
            )
            return self._maybe_release(now)
        if not changed:
            age = now - self.last_change
            if g.status == 3:
                self._set("paused", "Game paused")
            elif g.status == 1 and age < self.RELEASE_S:
                self._set("replay", "Replay (no new frames)")
            elif age >= self.STALE_S:
                self._set("stale", f"Packet IDs unchanged for {age:.1f} s")
                return self._maybe_release(now)
            return None
        meta, sample = decode_frame(p, g, s, settings)
        if meta.identity() != self.session_identity:
            self.timer = SessionTimer()
            self.session_identity = meta.identity()
        sample.session_left_ms = self.timer.read(
            sample.completed_laps,
            sample.lap_ms,
            finite(g.sessionTimeLeft),
            sample.status,
        )
        meta.session_timer_unit = self.timer.unit
        if meta.unknown_fields != self.unknown_fields:
            self.unknown_fields = list(meta.unknown_fields)
            if self.unknown_fields:
                log.warning("AC metadata marked unknown: %s", "; ".join(self.unknown_fields))
        self._set({1: "replay", 2: "connected", 3: "paused"}[g.status], "")
        self.samples += 1
        self._rates.append(now)
        self.last_decode_at = time.time()
        return meta, sample

    def _maybe_release(self, now):
        """Release handles of a vanished game so a restart is picked up.

        Only when the process is not confirmed running; while acs.exe runs the
        mapping stays open (pause/menu/loading keep the same pages).
        """
        if (
            self.last_change is not None
            and now - self.last_change >= self.RELEASE_S
            and self.process["status"] != "found"
        ):
            self.close()
            self.next_connect_at = now + self.RETRY_S
            self._set(
                "waiting_game",
                "AC pages stopped updating and acs.exe was not confirmed; reconnecting",
            )
        return None

    def observed_hz(self, window=2.0):
        now = self.clock()
        return round(sum(1 for t in self._rates if now - t <= window) / window, 1)

    def diagnostics(self):
        now = self.clock()
        return {
            "state": self.state,
            "message": self.error,
            "process": dict(self.process),
            "pages": [dict(self.page_info[name]) for name, _ in self.names],
            "physics_packet_id": self.physics_packet,
            "graphics_packet_id": self.graphics_packet,
            "game_status": {
                "code": self.game_status,
                "name": STATUS_NAMES.get(self.game_status, "unknown")
                if self.game_status is not None
                else None,
            },
            "last_update_age_s": round(now - self.last_change, 2)
            if self.last_change is not None and self.maps
            else None,
            "last_decode_at": _iso(self.last_decode_at),
            "last_decode_error": self.last_decode_error,
            "last_decode_error_at": _iso(self.last_decode_error_at),
            "decode_errors": self.decode_errors,
            "torn_snapshots": self.torn_snapshots,
            "connects": self.connects,
            "samples": self.samples,
            "observed_hz": self.observed_hz(),
            "unknown_fields": list(self.unknown_fields),
        }
