from dataclasses import dataclass, field
import numpy as np


@dataclass
class FlightEvent:
    time_us: int
    event_type: str   # "arm", "disarm", "mode_change"
    detail: str       # human-readable, e.g. "Mode: QLOITER"


@dataclass
class LogData:
    # ── File metadata ──────────────────────────────────────
    file_path: str = ""
    file_size_bytes: int = 0
    vehicle_type: str = ""
    firmware_version: str = ""
    total_messages: int = 0

    # ── Time ───────────────────────────────────────────────
    start_time_us: int = 0
    end_time_us: int = 0

    @property
    def duration_seconds(self) -> float:
        if self.end_time_us <= self.start_time_us:
            return 0.0
        return (self.end_time_us - self.start_time_us) / 1_000_000

    @property
    def flight_time_seconds(self) -> float:
        """Sum of every individual ARM→DISARM interval — pure airtime, no ground time."""
        arm_times    = sorted(ev.time_us for ev in self.events if ev.event_type == 'arm')
        disarm_times = sorted(ev.time_us for ev in self.events if ev.event_type == 'disarm')

        if not arm_times:
            return self.duration_seconds

        total = 0.0
        used  = set()
        for arm_t in arm_times:
            matched = None
            for i, d in enumerate(disarm_times):
                if d > arm_t and i not in used:
                    matched = d
                    used.add(i)
                    break
            if matched is not None:
                total += (matched - arm_t) / 1_000_000
            elif self.end_time_us > arm_t:
                # No DISARM recorded — count to end of log for this arm
                total += (self.end_time_us - arm_t) / 1_000_000

        return total if total > 0 else self.duration_seconds

    # ── Message arrays (dict of 1-D numpy arrays) ──────────
    # Keys match ArduPilot field names; TimeUS is always int64.

    imu: dict = field(default_factory=dict)
    # TimeUS, I(instance), GyrX/Y/Z, AccX/Y/Z, T

    vibe: dict = field(default_factory=dict)
    # TimeUS, IMU(instance), VibeX/Y/Z, Clip

    gps: dict = field(default_factory=dict)
    # TimeUS, I, Status, NSats, HDop, Lat, Lng, Alt, Spd, GCrs, VZ, CumDist(computed)

    pos: dict = field(default_factory=dict)
    # TimeUS, Lat, Lng, Alt, RelHomeAlt, RelOriginAlt

    att: dict = field(default_factory=dict)
    # TimeUS, DesRoll, Roll, DesPitch, Pitch, DesYaw, Yaw

    bat: dict = field(default_factory=dict)
    # TimeUS, Inst, Volt, VoltR, Curr, CurrTot, EnrgTot, RemPct

    baro: dict = field(default_factory=dict)
    # TimeUS, I, Alt, AltAMSL, Press, Temp

    rcou: dict = field(default_factory=dict)
    # TimeUS, C1..C14

    arsp: dict = field(default_factory=dict)
    # TimeUS, I, Airspeed, U (use flag)

    motb: dict = field(default_factory=dict)
    # TimeUS, LiftMax, BatVolt, ThLimit, ThrOut

    qtun: dict = field(default_factory=dict)
    # TimeUS, Tilt, Dsired, Ang, Dist  (QuadPlane transition tuning — optional)

    # ── Events ─────────────────────────────────────────────
    events: list = field(default_factory=list)

    # ── Parameters ─────────────────────────────────────────
    params: dict = field(default_factory=dict)

    # ── Computed summary fields ────────────────────────────
    home_lat: float = 0.0
    home_lng: float = 0.0
    home_alt: float = 0.0
    max_altitude_m: float = 0.0
    min_altitude_m: float = 0.0
    total_distance_2d_m: float = 0.0
    total_distance_3d_m: float = 0.0
    arm_time_us: int = 0
    disarm_time_us: int = 0
    max_speed_ms: float = 0.0
    avg_speed_ms: float = 0.0

    def is_empty(self) -> bool:
        return self.file_path == ""
