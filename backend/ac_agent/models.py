from __future__ import annotations

import math
import re
import secrets
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


ACCESS_CODE = re.compile(r"[0-9]{8}")


def new_access_code():
    """Random 8-digit LAN access code (cryptographically secure)."""
    return f"{secrets.randbelow(10**8):08d}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Settings(StrictModel):
    language: Literal["de", "en"] = "de"
    units: Literal["metric", "imperial"] = "metric"
    capture_hz: int = Field(60, ge=20, le=120)
    broadcast_hz: int = Field(20, ge=10, le=30)
    source: Literal["ac", "demo"] = "ac"
    auto_save: bool = True
    storage_mb: int = Field(2048, ge=64, le=100000)
    lan: bool = False
    port: int = Field(8765, ge=1024, le=65535)
    access_token: str = Field("", max_length=128)
    temp_cold: float = Field(70, ge=0, le=200)
    temp_hot: float = Field(105, ge=0, le=250)
    pressure_low: float = Field(20, ge=0, le=60)
    pressure_high: float = Field(35, ge=0, le=80)
    steering_lock_deg: float | None = Field(None, gt=0, le=1500)
    wheelbase_m: float | None = Field(None, gt=1, le=6)
    steering_ratio: float | None = Field(None, ge=1, le=40)
    steering_yaw_sign: Literal[-1, 1] | None = None
    brake_threshold: float = Field(0.08, ge=0.01, le=0.8)
    full_throttle: float = Field(0.97, ge=0.8, le=1)
    lock_ratio: float = Field(0.65, ge=0.1, le=0.95)
    spin_ratio: float = Field(1.2, ge=1.05, le=3)
    slip_angle_deg: float = Field(8, ge=2, le=30)
    video_mode: Literal["none", "url", "file", "screen", "camera"] = "none"
    video_url: str = Field("", max_length=2048)
    # Camera label for video_mode "camera" (e.g. "OBS Virtual Camera"). Empty:
    # pick OBS Virtual Camera automatically, otherwise the first camera.
    video_device: str = Field("", max_length=200)
    # Video position = telemetry time + video_offset_s. Positive: the picture
    # lags behind telemetry (e.g. OBS/virtual camera latency).
    video_offset_s: float = Field(0, ge=-86400, le=86400)
    # Onboard recording (browser on the PC records the camera; see recordings.py).
    record_auto: bool = False
    # Bitrate preset; resolution and frame rate come from the video source (OBS).
    record_quality: Literal["saver", "standard", "high"] = "standard"
    record_storage_mb: int = Field(20480, ge=1024, le=4_000_000)
    # Sound with the video: "game" = only Assetto Corsa (process loopback),
    # "system" = everything audible on the PC, "off" = no sound.
    record_audio: Literal["game", "system", "off"] = "game"
    # Explicit retention rule: delete the oldest videos when the limit is hit.
    record_delete_oldest: bool = False
    # Empty: <data folder>ideos. Only changeable on the PC itself.
    record_dir: str = Field("", max_length=400)
    visible_widgets: list[str] = Field(
        default_factory=lambda: [
            "telemetry",
            "map",
            "tyres",
            "coach",
            "sectors",
            "onboard",
        ]
    )
    widget_order: list[str] = Field(
        default_factory=lambda: [
            "telemetry",
            "coach",
            "map",
            "tyres",
            "sectors",
            "onboard",
        ]
    )
    graph_colors: dict[str, str] = Field(
        default_factory=lambda: {
            "speed": "#ffcc57",
            "gas": "#60e7b0",
            "brake": "#ff6977",
            "steer": "#62d9ef",
        }
    )
    reference_lap_id: str | None = None
    # Lap-timing HUD: how long a completed lap stays on screen (seconds).
    hud_hold_s: float = Field(5, ge=1, le=30)
    # Optional MANUAL tyre label mapping: exact game compound name -> short
    # label (e.g. {"Semislick (SM)": "M"}). Never filled automatically.
    compound_map: dict[str, str] = Field(default_factory=dict, max_length=40)
    start_with_windows: bool = False
    # Ask GitHub for a newer release (only when a repository is configured).
    update_check: bool = True
    open_browser: bool = True
    # No LLM implementation in this release. Local rules only.
    ai_enabled: Literal[False] = False
    upload_telemetry: Literal[False] = False

    @model_validator(mode="after")
    def check_ranges(self):
        if self.temp_cold >= self.temp_hot or self.pressure_low >= self.pressure_high:
            raise ValueError("Lower warning thresholds must be below upper thresholds")
        if self.lan and not self.access_token:
            raise ValueError("LAN requires an 8-digit access code")
        if self.access_token and not ACCESS_CODE.fullmatch(self.access_token):
            raise ValueError("Access code must be exactly 8 digits")
        widgets = {"telemetry", "coach", "map", "tyres", "sectors", "onboard"}
        if len(self.widget_order) != 6 or set(self.widget_order) != widgets:
            raise ValueError(
                "Widget order must contain each of the six supported widgets once"
            )
        if (
            len(self.visible_widgets) > 6
            or len(set(self.visible_widgets)) != len(self.visible_widgets)
            or not set(self.visible_widgets) <= widgets
        ):
            raise ValueError("Unknown or duplicate visible widgets")
        for name, label in self.compound_map.items():
            if not 1 <= len(name) <= 66 or not re.fullmatch(r"[A-Za-z0-9+\-]{1,4}", label):
                raise ValueError(
                    "Tyre mapping: game compound name (1–66 characters) to a 1–4 character label"
                )
        for color in self.graph_colors.values():
            if (
                len(color) != 7
                or color[0] != "#"
                or any(c not in "0123456789abcdefABCDEF" for c in color[1:])
            ):
                raise ValueError("Graph colors must use #RRGGBB")
        return self


class SessionMeta(StrictModel):
    source: Literal["ac", "demo"]
    driver: str = Field("", max_length=150)
    car: str = Field("", max_length=100)
    track: str = Field("", max_length=100)
    layout: str = Field("", max_length=100)
    session_type: int = -1
    track_length: float = Field(0, ge=0, le=100000)
    sector_count: int = Field(3, ge=1, le=20)
    max_rpm: int = Field(0, ge=0, le=50000)
    max_fuel: float = Field(0, ge=0)
    tyre_radii: list[float] = Field(
        default_factory=lambda: [0] * 4, min_length=4, max_length=4
    )
    has_drs: bool = False
    has_ers: bool = False
    has_kers: bool = False
    compound: str = ""
    air_temp: float | None = None
    road_temp: float | None = None
    weather: str | None = None
    setup: dict | None = None
    ac_version: str = ""
    sm_version: str = ""
    raw_static: dict[str, Any] = Field(default_factory=dict)
    session_timer_unit: Literal["ms", "s", "unknown"] = "unknown"
    # Raw values that were outside a plausible range and were replaced by an
    # explicit "unknown" instead of blocking the stream (never invented data).
    unknown_fields: list[str] = Field(default_factory=list, max_length=50)

    def identity(self):
        return (
            self.source,
            self.driver,
            self.car,
            self.track,
            self.layout,
            self.session_type,
        )


class Tyre(StrictModel):
    corner: Literal["FL", "FR", "RL", "RR"]
    core: float | None = None
    inner: float | None = None
    middle: float | None = None
    outer: float | None = None
    surface: float | None = None
    pressure: float | None = None
    wear_raw: float | None = None
    load: float | None = None
    slip_raw: float | None = None
    angular_speed: float | None = None
    brake_temp: float | None = None
    travel: float | None = None
    locked: bool | None = None
    spinning: bool | None = None


class Sample(StrictModel):
    source: Literal["ac", "demo"]
    packet_id: int
    captured_at: float
    status: int = 2
    completed_laps: int = Field(0, ge=0)
    lap_ms: int = Field(0, ge=0)
    last_lap_ms: int = Field(0, ge=0)
    best_lap_ms: int = Field(0, ge=0)
    sector_index: int = Field(0, ge=0, le=19)
    last_sector_ms: int = Field(0, ge=0)
    position: int = 0
    session_left_ms: float | None = None
    lap_pos: float = Field(0, ge=0, le=1)
    coords: tuple[float, float, float] = (0, 0, 0)
    in_pit: bool = False
    tyres_out: int = 0
    penalty_s: float = 0
    channels: dict[str, float | None] = Field(default_factory=dict)
    tyres: list[Tyre] = Field(default_factory=list, max_length=4)
    synthetic_boundary: bool = False


class Lap(StrictModel):
    id: str
    session_id: str
    number: int
    duration_ms: int = Field(ge=0)
    valid: bool
    validity_basis: str = "inferred; original AC has no isValidLap shared-memory field"
    reasons: list[str] = Field(default_factory=list)
    complete: bool = True
    sectors_ms: list[int | None] = Field(default_factory=list)
    sector_positions: list[float] = Field(default_factory=list)
    samples: list[Sample] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    tips: list[dict] = Field(default_factory=list)
    created_at: str
    favorite: bool = False
    analysis: dict = Field(default_factory=dict)


def finite(value, default=None):
    v = float(value)
    return v if math.isfinite(v) else default


def json_safe(obj):
    """Convert NaN/gaps to JSON null, never fake measurements."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj
