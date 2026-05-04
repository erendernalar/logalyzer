import os
import math
import time
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from .log_data import LogData, FlightEvent


# ArduPlane + ArduCopter mode number → name mapping
# Plane modes: https://ardupilot.org/plane/docs/flight-modes.html
# Copter modes: https://ardupilot.org/copter/docs/flight-modes.html
_MODE_NAMES = {
    # ArduPlane
    0: 'MANUAL', 1: 'CIRCLE', 2: 'STABILIZE', 3: 'TRAINING',
    4: 'ACRO', 5: 'FBWA', 6: 'FBWB', 7: 'CRUISE', 8: 'AUTOTUNE',
    10: 'AUTO', 11: 'RTL', 12: 'LOITER', 13: 'TAKEOFF',
    14: 'AVOID_ADSB', 15: 'GUIDED', 16: 'INITIALISING',
    17: 'QSTABILIZE', 18: 'QHOVER', 19: 'QLOITER', 20: 'QLAND',
    21: 'QRTL', 22: 'QAUTOTUNE', 23: 'QACRO', 24: 'THERMAL',
    25: 'LOITER_ALT_QLAND',
}
_COPTER_MODE_NAMES = {
    0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
    4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
    9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 14: 'FLIP',
    15: 'AUTOTUNE', 16: 'POSHOLD', 17: 'BRAKE', 18: 'THROW',
    19: 'AVOID_ADSB', 20: 'GUIDED_NOGPS', 21: 'SMART_RTL',
    22: 'FLOWHOLD', 23: 'FOLLOW', 24: 'ZIGZAG',
    25: 'SYSTEMID', 26: 'AUTOROTATE', 27: 'AUTO_RTL',
}


def _haversine(lat1, lon1, lat2, lon2):
    """Return distance in metres between two WGS-84 coordinates."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class LogLoader(QThread):
    """
    Parses an ArduPilot .BIN (or .tlog) file in a background thread.

    Signals
    -------
    progress(int)       0-100 percentage
    status(str)         human-readable status message
    finished(LogData)   emitted when parse is complete
    error(str)          emitted on unrecoverable failure
    """

    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(object)   # LogData
    error = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # ── Internal accumulators ───────────────────────────────

    def _make_acc(self):
        return {}   # field_name -> list

    def _append(self, acc, msg, fields):
        for f in fields:
            val = getattr(msg, f, None)
            if val is None:
                val = 0
            acc.setdefault(f, []).append(val)

    def _to_arrays(self, acc):
        result = {}
        for k, v in acc.items():
            arr = np.array(v)
            if arr.dtype.kind == 'f':
                arr = arr.astype(np.float32)
            result[k] = arr
        return result

    # ── Main parse ──────────────────────────────────────────

    def run(self):
        try:
            self._parse()
        except Exception as exc:
            self.error.emit(f"Parse error: {exc}")

    def _parse(self):
        from pymavlink import mavutil

        path = self.file_path
        file_size = os.path.getsize(path)

        self.status.emit("Opening log file…")
        self.progress.emit(2)

        try:
            mlog = mavutil.mavlink_connection(
                path,
                robust_parsing=True,
                dialect='ardupilotmega',
                zero_time_base=True,
            )
        except Exception as exc:
            self.error.emit(f"Cannot open file: {exc}")
            return

        # ── Accumulators ─────────────────────────────────────
        imu_acc  = self._make_acc()
        vibe_acc = self._make_acc()
        gps_acc  = self._make_acc()
        pos_acc  = self._make_acc()
        att_acc  = self._make_acc()
        bat_acc  = self._make_acc()
        baro_acc = self._make_acc()
        rcin_acc = self._make_acc()
        rcou_acc = self._make_acc()
        arsp_acc = self._make_acc()
        motb_acc = self._make_acc()
        qtun_acc = self._make_acc()

        events = []
        params = {}
        vehicle_type = ""
        firmware_version = ""
        total_messages = 0
        last_progress = 0
        mb_total = file_size / 1_048_576
        parse_start = time.monotonic()

        self.status.emit(f"Parsing…  0 msgs · 0.0 / {mb_total:.1f} MB")

        while not self._cancelled:
            try:
                msg = mlog.recv_match(blocking=False)
            except Exception:
                break
            if msg is None:
                break

            total_messages += 1
            mtype = msg.get_type()

            # progress update every 1 000 messages
            if total_messages % 1000 == 0:
                try:
                    pos = getattr(mlog, 'offset', None)
                    if pos is None:
                        pos = mlog.f.tell()
                    pct = min(99, int(pos / file_size * 100))
                    if pct != last_progress:
                        self.progress.emit(pct)
                        last_progress = pct
                    mb_read = pos / 1_048_576
                    elapsed = time.monotonic() - parse_start
                    rate_k  = (total_messages / elapsed / 1000) if elapsed > 0 else 0
                    self.status.emit(
                        f"Parsing…  {total_messages:,} msgs · "
                        f"{mb_read:.1f} / {mb_total:.1f} MB · "
                        f"{rate_k:.1f}k msg/s"
                    )
                except Exception:
                    pass

            if mtype == 'IMU':
                self._append(imu_acc, msg,
                    ['TimeUS', 'I', 'GyrX', 'GyrY', 'GyrZ', 'AccX', 'AccY', 'AccZ', 'T'])

            elif mtype == 'VIBE':
                self._append(vibe_acc, msg,
                    ['TimeUS', 'IMU', 'VibeX', 'VibeY', 'VibeZ', 'Clip'])

            elif mtype == 'GPS':
                self._append(gps_acc, msg,
                    ['TimeUS', 'I', 'Status', 'NSats', 'HDop',
                     'Lat', 'Lng', 'Alt', 'Spd', 'GCrs', 'VZ'])

            elif mtype == 'POS':
                self._append(pos_acc, msg,
                    ['TimeUS', 'Lat', 'Lng', 'Alt', 'RelHomeAlt', 'RelOriginAlt'])

            elif mtype == 'ATT':
                self._append(att_acc, msg,
                    ['TimeUS', 'DesRoll', 'Roll', 'DesPitch', 'Pitch', 'DesYaw', 'Yaw'])

            elif mtype == 'BAT':
                self._append(bat_acc, msg,
                    ['TimeUS', 'Inst', 'Volt', 'VoltR', 'Curr', 'CurrTot', 'EnrgTot', 'RemPct'])

            elif mtype == 'BARO':
                self._append(baro_acc, msg,
                    ['TimeUS', 'I', 'Alt', 'AltAMSL', 'Press', 'Temp'])

            elif mtype == 'RCIN':
                fields = ['TimeUS'] + [f'C{i}' for i in range(1, 15)
                                       if hasattr(msg, f'C{i}')]
                self._append(rcin_acc, msg, fields)

            elif mtype == 'RCOU':
                fields = ['TimeUS'] + [f'C{i}' for i in range(1, 15)
                                       if hasattr(msg, f'C{i}')]
                self._append(rcou_acc, msg, fields)

            elif mtype == 'ARSP':
                self._append(arsp_acc, msg, ['TimeUS', 'I', 'Airspeed', 'U'])

            elif mtype == 'MOTB':
                self._append(motb_acc, msg,
                    ['TimeUS', 'LiftMax', 'BatVolt', 'ThLimit', 'ThrOut'])

            elif mtype == 'QTUN':
                self._append(qtun_acc, msg,
                    ['TimeUS', 'Tilt', 'Dsired', 'Ang', 'Dist'])

            elif mtype == 'EV':
                # ARM=10, DISARM=11
                ev_id = getattr(msg, 'Id', 0)
                t = getattr(msg, 'TimeUS', 0)
                if ev_id == 10:
                    events.append(FlightEvent(t, 'arm', 'Armed'))
                elif ev_id == 11:
                    events.append(FlightEvent(t, 'disarm', 'Disarmed'))

            elif mtype == 'MODE':
                t = getattr(msg, 'TimeUS', 0)
                mode_num = int(getattr(msg, 'Mode', getattr(msg, 'ModeNum', 0)))
                # Pick lookup table based on vehicle type detected so far
                lut = _COPTER_MODE_NAMES if 'Copter' in vehicle_type else _MODE_NAMES
                mode_name = lut.get(mode_num, str(mode_num))
                events.append(FlightEvent(t, 'mode_change', f'Mode: {mode_name}'))

            elif mtype == 'PARM':
                name = getattr(msg, 'Name', '')
                val = getattr(msg, 'Value', 0.0)
                if name:
                    params[name] = float(val)

            elif mtype == 'MSG':
                txt = getattr(msg, 'Message', '')
                if not vehicle_type and any(v in txt for v in
                        ['ArduPlane', 'ArduCopter', 'ArduRover', 'ArduSub', 'AntennaTracker']):
                    for v in ['ArduPlane', 'ArduCopter', 'ArduRover', 'ArduSub']:
                        if v in txt:
                            vehicle_type = v
                            break

            elif mtype == 'VER':
                firmware_version = getattr(msg, 'FWStr', '')

        if self._cancelled:
            return

        self.status.emit("Processing data…")
        self.progress.emit(99)

        # ── Convert to arrays ─────────────────────────────────
        log = LogData()
        log.file_path = path
        log.file_size_bytes = file_size
        log.vehicle_type = vehicle_type
        log.firmware_version = firmware_version
        log.total_messages = total_messages
        log.events = events
        log.params = params

        log.imu  = self._to_arrays(imu_acc)
        log.vibe = self._to_arrays(vibe_acc)
        log.gps  = self._to_arrays(gps_acc)
        log.pos  = self._to_arrays(pos_acc)
        log.att  = self._to_arrays(att_acc)
        log.bat  = self._to_arrays(bat_acc)
        log.baro = self._to_arrays(baro_acc)
        log.rcin = self._to_arrays(rcin_acc)
        log.rcou = self._to_arrays(rcou_acc)
        log.arsp = self._to_arrays(arsp_acc)
        log.motb = self._to_arrays(motb_acc)
        log.qtun = self._to_arrays(qtun_acc)

        # ── Derived fields ────────────────────────────────────
        self._compute_derived(log)

        self.progress.emit(100)
        self.status.emit("Done.")
        self.finished.emit(log)

    def _compute_derived(self, log: LogData):
        # Time range
        candidates = []
        for d in (log.imu, log.gps, log.att):
            if 'TimeUS' in d and len(d['TimeUS']) > 0:
                candidates.append((int(d['TimeUS'][0]), int(d['TimeUS'][-1])))
        if candidates:
            log.start_time_us = min(s for s, _ in candidates)
            log.end_time_us   = max(e for _, e in candidates)

        # Arm / disarm times
        for ev in log.events:
            if ev.event_type == 'arm' and log.arm_time_us == 0:
                log.arm_time_us = ev.time_us
            if ev.event_type == 'disarm':
                log.disarm_time_us = ev.time_us

        # GPS-derived
        if 'Lat' in log.gps and len(log.gps['Lat']) > 0:
            status = log.gps.get('Status', np.ones(len(log.gps['Lat'])))
            mask = status >= 3

            lats = log.gps['Lat']
            lngs = log.gps['Lng']
            alts = log.gps['Alt']

            fixed_lats = lats[mask]
            fixed_lngs = lngs[mask]

            if len(fixed_lats) > 0:
                log.home_lat = float(fixed_lats[0])
                log.home_lng = float(fixed_lngs[0])
                log.home_alt = float(alts[mask][0]) if len(alts[mask]) > 0 else 0.0

            # Cumulative 2D distance + altitude range
            cum_dist = np.zeros(len(lats), dtype=np.float32)
            total_2d = 0.0
            total_3d = 0.0
            for i in range(1, len(lats)):
                if status[i] >= 3 and status[i - 1] >= 3:
                    d2 = _haversine(lats[i-1], lngs[i-1], lats[i], lngs[i])
                    dalt = float(alts[i]) - float(alts[i-1])
                    d3 = math.sqrt(d2 ** 2 + dalt ** 2)
                    total_2d += d2
                    total_3d += d3
                cum_dist[i] = total_2d

            log.gps['CumDist'] = cum_dist
            log.total_distance_2d_m = total_2d
            log.total_distance_3d_m = total_3d

            if 'Spd' in log.gps and len(log.gps['Spd']) > 0:
                spd = log.gps['Spd'][mask]
                if len(spd) > 0:
                    log.max_speed_ms = float(spd.max())
                    log.avg_speed_ms = float(spd.mean())

        # Altitude
        if 'Alt' in log.baro and len(log.baro['Alt']) > 0:
            log.max_altitude_m = float(log.baro['Alt'].max())
            log.min_altitude_m = float(log.baro['Alt'].min())
        elif 'Alt' in log.gps and len(log.gps['Alt']) > 0:
            log.max_altitude_m = float(log.gps['Alt'].max())
            log.min_altitude_m = float(log.gps['Alt'].min())
