"""Synthetic, deterministic demo. Never masquerades as AC measurements."""

import math
import time
import numpy as np
from .models import Sample, SessionMeta, Tyre


LENGTH = 4200.0
CORNERS = np.array([0.095, 0.205, 0.32, 0.435, 0.565, 0.66, 0.775, 0.895])


class DemoSource:
    def __init__(self, rate=1.0):
        self.rate = rate
        self.start = time.monotonic()
        self.packet = 0
        self.error = ""
        self.meta = SessionMeta(
            source="demo",
            driver="Demo Driver",
            car="Prototype R",
            track="Engineering Circuit",
            layout="GP",
            session_type=0,
            track_length=LENGTH,
            sector_count=3,
            max_rpm=10500,
            max_fuel=80,
            tyre_radii=[0.32] * 4,
            compound="Demo Medium",
            air_temp=23,
            road_temp=31,
            ac_version="DEMO",
            sm_version="SYNTHETIC",
            session_timer_unit="ms",
        )
        self._profiles = [self.profile(i) for i in range(3)]

    @staticmethod
    def profile(variant):
        u = np.linspace(0, 1, 4201)
        speed = np.full_like(u, 242.0)
        for i, corner in enumerate(CORNERS):
            width = 0.028 + 0.004 * (i % 3)
            speed -= (110 + 28 * (i % 3)) * np.exp(-(((u - corner) / width) ** 2))
        if variant:
            # Measured differences are produced from synthetic lap samples.
            speed -= (8 + variant * 2) * np.exp(-(((u - CORNERS[2]) / 0.04) ** 2))
            speed -= 6 * np.exp(-(((u - CORNERS[5]) / 0.027) ** 2))
        speed = np.clip(speed, 58, 255)
        times = np.r_[0, np.cumsum((LENGTH / 4200) / (speed[:-1] / 3.6))]
        return u, speed, times

    def make_sample(self, u, lap=0, ms=None):
        variant = lap % 3
        grid, speeds, times = self._profiles[variant]
        speed = float(np.interp(u, grid, speeds))
        derivative = float(np.interp(u, grid, np.gradient(speeds, grid)))
        brake = float(np.clip(-derivative / 6500, 0, 0.95))
        gas = float(np.clip(1 - brake * 1.8 - max(0, -derivative) / 14000, 0.02, 1))
        steer = sum(
            (-0.36 if i % 2 else 0.38) * math.exp(-(((u - c) / 0.022) ** 2))
            for i, c in enumerate(CORNERS)
        )
        theta = u * 2 * math.pi
        x, z = (
            620 * math.cos(theta) + 120 * math.sin(3 * theta),
            450 * math.sin(theta) + 90 * math.sin(4 * theta),
        )
        gear = max(1, min(6, int(speed / 43) + 1))
        rpm = 5800 + (speed % 43) / 43 * 3900
        elapsed = float(np.interp(u, grid, times))
        lap_ms = round(elapsed * 1000) if ms is None else ms
        sector = min(2, int(u * 3))
        split_times = np.interp([0, 1 / 3, 2 / 3, 1], grid, times)
        last_sector = (
            round((split_times[sector] - split_times[sector - 1]) * 1000)
            if sector
            else 0
        )
        self.packet += 1
        channels = dict(
            speed=speed,
            gas=gas,
            brake=brake,
            clutch=0.0,
            gear=float(gear),
            rpm=rpm,
            steer=steer,
            steer_deg=None,
            g_lat=steer * 4.5,
            g_long=derivative / 3.6 * speed / 3.6 / LENGTH / 9.81,
            g_vert=0.06 * math.sin(theta * 50),
            fuel=45 - (lap + u) * 2.1,
            yaw_rate=steer * 0.9,
            local_vx=steer * 0.4,
            local_vz=speed / 3.6,
            air_temp=23.0,
            road_temp=31.0,
            ride_front=0.045,
            ride_rear=0.062,
            cg_height=0.28,
            brake_bias=0.56,
            abs_raw=0.5,
            tc_raw=0.3,
            abs_active=None,
            tc_active=None,
            engine_temp=None,
            engine_health=None,
            aero_damage=None,
            brake_pressure=None,
            damper_velocity=None,
            bottoming=None,
            drs=None,
            drs_available=None,
            kers_charge=None,
            kers_input=None,
            ers_kj=None,
            ers_power=None,
            turbo=0.0,
            pit_limiter=0.0,
            ai_controlled=0.0,
        )
        tyres = []
        for i, corner in enumerate(("FL", "FR", "RL", "RR")):
            temp = (
                86
                + 3 * math.sin(theta * 2 + i)
                + lap * 1.2
                + (abs(steer) * 22 if i == 0 else 0)
            )
            tyre = Tyre(
                corner=corner,
                core=temp,
                inner=temp + 3,
                middle=temp - 1,
                outer=temp - 4,
                pressure=27 + math.sin(theta + i) * 0.3,
                wear_raw=100 - (lap + u) * 0.8,
                load=2400 + steer * (450 if i % 2 else -450),
                slip_raw=0.12 + brake * 0.2,
                angular_speed=speed / 3.6 / 0.32,
                brake_temp=230 + brake * 430,
                travel=0.021 + abs(steer) * 0.015,
                locked=False,
                spinning=False,
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
        for i in range(5):
            channels[f"damage_{i}"] = 0.0
        previous = round(self._profiles[(lap - 1) % 3][2][-1] * 1000) if lap else 0
        return Sample(
            source="demo",
            packet_id=self.packet,
            captured_at=time.time(),
            completed_laps=lap,
            lap_ms=lap_ms,
            last_lap_ms=previous,
            best_lap_ms=round(self._profiles[0][2][-1] * 1000) if lap else 0,
            sector_index=sector,
            last_sector_ms=last_sector,
            position=1,
            session_left_ms=max(0, 1800000 - (lap * 100000 + lap_ms)),
            lap_pos=float(u),
            coords=(x, 0.0, z),
            channels=channels,
            tyres=tyres,
        )

    def read(self, settings):
        elapsed = (time.monotonic() - self.start) * self.rate
        lap = 0
        while elapsed >= self._profiles[lap % 3][2][-1]:
            elapsed -= self._profiles[lap % 3][2][-1]
            lap += 1
        grid, _, times = self._profiles[lap % 3]
        u = float(np.interp(elapsed, times, grid))
        return self.meta, self.make_sample(u, lap, round(elapsed * 1000))

    def recorded_lap(self, number, hz=60):
        grid, _, times = self._profiles[number % 3]
        for elapsed in np.arange(0, times[-1], 1 / hz):
            yield self.make_sample(
                float(np.interp(elapsed, times, grid)), number, round(elapsed * 1000)
            )

    def close(self):
        pass
