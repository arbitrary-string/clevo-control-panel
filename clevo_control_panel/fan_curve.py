"""Continuous temperature-driven fan curve, layered on top of fan.py's
FanControl the same way auto_profile.py layers policy on top of
performance.py. This module owns three things:

- FanCurveConfig: the on-disk JSON config (curve points, hysteresis,
  critical temp, enabled flag) -- shared, system-wide, same directory as
  auto-profile.json/performance-mode.state, for the same reason (the
  hardware being controlled is shared by whoever's logged in).
- FanCurvePolicy: pure temp -> duty% computation (linear interpolation
  + hysteresis), no I/O, easy to reason about independent of the daemon
  loop around it.
- FanCurveDaemon: the actual control loop, meant to run continuously as
  a systemd service (see data/clevo-fan-curve.service), not as a GUI
  timer -- if the loop stops for any reason (clean exit, crash, kill),
  the kernel's own watchdog (see fan.py/clevo-acpi.c) releases fan
  control back to firmware auto on its own within a bounded time, so a
  dead daemon can never leave the fan stuck at a stale speed.

See ~/odm-laptop-research/NOTES.md for the full design writeup this is
based on, and ~/.claude/plans/floofy-imagining-dream.md for the plan that
was implemented here.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path("/var/lib/clevo-control-panel")
CONFIG_FILE = CONFIG_DIR / "fan-curve.json"
STATUS_FILE = CONFIG_DIR / "fan-daemon-status.json"

DEFAULT_POLL_INTERVAL_SECONDS = 2
DEFAULT_HYSTERESIS_C = 3
DEFAULT_CRITICAL_TEMP_C = 95
DEFAULT_MAX_DUTY_PERCENT = 100
DEFAULT_CURVE = [
    {"temp_c": 40, "percent": 20},
    {"temp_c": 55, "percent": 35},
    {"temp_c": 65, "percent": 55},
    {"temp_c": 75, "percent": 75},
    {"temp_c": 85, "percent": 100},
]

# Consecutive missed temperature readings before the daemon gives up and
# releases to firmware auto control for that cycle -- mirrors the same
# "return control to firmware after repeated missing telemetry" rule
# reviewed in a third-party fan-control project earlier in this project's
# research, rather than continuing to hold a stale duty with no way to
# tell if it's still appropriate.
MAX_MISSING_TEMP_READINGS = 3


def _clamp_int(value, low, high, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def validate_curve(curve):
    if not isinstance(curve, list) or len(curve) < 2:
        raise ValueError("curve must be a list of at least two points")
    points = []
    for point in curve:
        temp_c = int(point["temp_c"])
        percent = int(point["percent"])
        if not 0 <= percent <= 100:
            raise ValueError("curve duty percent must be 0-100")
        points.append({"temp_c": temp_c, "percent": percent})
    points.sort(key=lambda p: p["temp_c"])
    return points


class FanCurveConfig:
    """enabled/curve/hysteresis/critical-temp/etc, loaded from and saved
    to CONFIG_FILE. `per_fan` is written but not yet read -- reserved for
    a future per-fan-curve feature so the schema needs no migration when
    that lands; today every fan follows the same linked curve."""

    def __init__(self):
        self.enabled = False
        self.poll_interval_seconds = DEFAULT_POLL_INTERVAL_SECONDS
        self.hysteresis_c = DEFAULT_HYSTERESIS_C
        self.critical_temp_c = DEFAULT_CRITICAL_TEMP_C
        self.max_duty_percent = DEFAULT_MAX_DUTY_PERCENT
        self.temp_source = "auto"
        self.linked = True
        self.curve = [dict(point) for point in DEFAULT_CURVE]
        self._mtime = None
        self.load()

    def load(self):
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            return

        self.enabled = bool(data.get("enabled", self.enabled))
        self.poll_interval_seconds = _clamp_int(
            data.get("poll_interval_seconds"), 1, 30, self.poll_interval_seconds
        )
        self.hysteresis_c = _clamp_int(
            data.get("hysteresis_c"), 0, 20, self.hysteresis_c
        )
        self.critical_temp_c = _clamp_int(
            data.get("critical_temp_c"), 60, 105, self.critical_temp_c
        )
        self.max_duty_percent = _clamp_int(
            data.get("max_duty_percent"), 1, 100, self.max_duty_percent
        )
        if data.get("temp_source") == "auto":
            self.temp_source = "auto"
        self.linked = bool(data.get("linked", self.linked))

        curve = data.get("curve")
        if curve is not None:
            try:
                self.curve = validate_curve(curve)
            except (ValueError, KeyError, TypeError):
                pass  # keep the previous curve if the file has something malformed

    def reload_if_changed(self):
        try:
            mtime = CONFIG_FILE.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._mtime = mtime
            self.load()
        return self

    def save(self):
        data = {
            "enabled": self.enabled,
            "poll_interval_seconds": self.poll_interval_seconds,
            "hysteresis_c": self.hysteresis_c,
            "critical_temp_c": self.critical_temp_c,
            "max_duty_percent": self.max_duty_percent,
            "temp_source": self.temp_source,
            "linked": self.linked,
            "curve": self.curve,
            "per_fan": {"1": [], "2": []},
        }
        try:
            # Atomic write -- see _write_status_file()'s comment for why
            # (rename only needs directory permissions, not permissions
            # on a pre-existing target file written by a different identity).
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass  # best-effort, matches auto_profile.py's own persistence


class FanCurvePolicy:
    """Pure temp -> duty% computation: linear interpolation between
    curve points (clamped at the ends), capped at max_duty_percent, with
    hysteresis so duty only steps back down once temp has dropped
    hysteresis_c below whatever temperature triggered the last step up
    -- prevents rapid duty hunting right at a curve breakpoint. No I/O;
    a fresh instance is created whenever the underlying config changes,
    which is an acceptable, deliberate reset of the hysteresis state
    since the curve itself changed anyway."""

    def __init__(self, curve, hysteresis_c, max_duty_percent):
        self._points = [(p["temp_c"], p["percent"]) for p in curve]
        self._hysteresis_c = hysteresis_c
        self._max_duty_percent = max_duty_percent
        self._last_duty = None
        self._last_trigger_temp = None

    def _interpolate(self, temp_c):
        points = self._points
        if temp_c <= points[0][0]:
            return points[0][1]
        if temp_c >= points[-1][0]:
            return points[-1][1]
        for (t0, d0), (t1, d1) in zip(points, points[1:]):
            if t0 <= temp_c <= t1:
                if t1 == t0:
                    return d1
                return d0 + (d1 - d0) * (temp_c - t0) / (t1 - t0)
        return points[-1][1]  # unreachable given the bounds checks above

    def compute(self, temp_c):
        target = min(round(self._interpolate(temp_c)), self._max_duty_percent)

        if self._last_duty is None or target > self._last_duty:
            self._last_duty = target
            self._last_trigger_temp = temp_c
        elif target < self._last_duty:
            if temp_c <= self._last_trigger_temp - self._hysteresis_c:
                self._last_duty = target
                self._last_trigger_temp = temp_c

        return self._last_duty


def _read_cpu_temp_c():
    base = Path("/sys/class/hwmon")
    if not base.is_dir():
        return None
    for hwmon in sorted(base.glob("hwmon*")):
        try:
            if (hwmon / "name").read_text().strip() != "coretemp":
                continue
            return int((hwmon / "temp1_input").read_text().strip()) / 1000
        except OSError:
            continue
    return None


def _read_gpu_temp_c():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def read_control_temperature(temp_source):
    """The real thermal-junction reading the curve is driven by --
    deliberately not fan1_temp/fan2_temp (those are fan-shroud-adjacent
    EC readings, better suited to dashboard display than as the primary
    control signal). Only "auto" (max of CPU package temp and GPU temp,
    whichever are available) is implemented for now."""
    if temp_source != "auto":
        return None
    candidates = [t for t in (_read_cpu_temp_c(), _read_gpu_temp_c()) if t is not None]
    return max(candidates) if candidates else None


def _sd_notify(message):
    """Minimal manual write to systemd's notify socket -- no new pip
    dependency, consistent with this project's existing preference for
    graceful degradation (e.g. tray_helper.py's optional-GTK3 handling).
    A no-op if not run under systemd (e.g. invoked manually for testing)."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.connect(address)
            sock.sendall(message.encode())
        finally:
            sock.close()
    except OSError:
        pass


def _write_status_file(critical_override_active, current_targets):
    data = {
        "timestamp": time.time(),
        "critical_override_active": critical_override_active,
        "current_targets": current_targets,
    }
    try:
        # Atomic write (temp file + rename), not a plain write_text():
        # renaming only needs write+execute on the containing directory,
        # not on the target file itself -- confirmed live that a
        # pre-existing status file created by a different identity (e.g.
        # a one-off manual root-run test) with narrower permissions would
        # otherwise silently and permanently block every future write
        # from the real (group-based) daemon identity. Also gets a real
        # correctness bonus: no reader can ever observe a half-written file.
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(STATUS_FILE)
    except OSError:
        pass  # best-effort; a stale/missing status file just reads as "daemon unhealthy" in the GUI


class FanCurveDaemon:
    def __init__(self, fan_control, config=None):
        self.fan_control = fan_control
        self.config = config or FanCurveConfig()
        self._policy = None
        self._policy_key = None
        self._last_duty = {}
        self._missing_temp_count = 0
        self._stop = False

    def _install_signal_handlers(self):
        def handler(signum, frame):
            self._stop = True

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def _policy_for(self, config):
        key = (
            tuple((p["temp_c"], p["percent"]) for p in config.curve),
            config.hysteresis_c,
            config.max_duty_percent,
        )
        if key != self._policy_key:
            self._policy = FanCurvePolicy(
                config.curve, config.hysteresis_c, config.max_duty_percent
            )
            self._policy_key = key
        return self._policy

    def run(self):
        self._install_signal_handlers()
        _sd_notify("READY=1")

        try:
            while not self._stop:
                self.config.reload_if_changed()

                # systemd's WatchdogSec= only proves this loop itself is
                # still alive and ticking -- a completely different thing
                # from the kernel's own fan_watchdog_ping (which proves a
                # *manual fan override* is still actively wanted). Pet it
                # every iteration unconditionally, including while curve
                # mode is disabled (the default, most-of-the-time state)
                # -- forgetting this meant systemd killed the daemon as
                # "hung" every WatchdogSec= seconds whenever the feature
                # was simply turned off, confirmed live during testing.
                _sd_notify("WATCHDOG=1")

                if not self.config.enabled:
                    self._last_duty.clear()
                    self._missing_temp_count = 0
                    time.sleep(self.config.poll_interval_seconds)
                    continue

                temp = read_control_temperature(self.config.temp_source)
                if temp is None:
                    self._missing_temp_count += 1
                    if self._missing_temp_count >= MAX_MISSING_TEMP_READINGS:
                        self.fan_control.release()
                        self._last_duty.clear()
                    time.sleep(self.config.poll_interval_seconds)
                    continue
                self._missing_temp_count = 0

                critical = temp >= self.config.critical_temp_c
                target = 100 if critical else self._policy_for(self.config).compute(temp)

                any_written = False
                targets = {}
                for fan in self.fan_control.fans():
                    if critical or target != self._last_duty.get(fan):
                        self.fan_control.set_manual_duty(fan, target)
                        self._last_duty[fan] = target
                        any_written = True
                    targets[str(fan)] = self._last_duty[fan]
                if not any_written:
                    self.fan_control.ping()

                _write_status_file(critical, targets)

                time.sleep(self.config.poll_interval_seconds)
        finally:
            self.fan_control.release()  # unconditional: covers stop/restart/shutdown/exception


def main():
    from .backend import BacklightError, KeyboardBacklight
    from .fan import FanControl, FanControlError

    try:
        backend = KeyboardBacklight()
    except BacklightError as exc:
        print(f"clevo-fan-curve-daemon: {exc}", file=sys.stderr)
        return 1

    try:
        fan_control = FanControl(backend.device_dir)
    except FanControlError as exc:
        print(f"clevo-fan-curve-daemon: {exc}", file=sys.stderr)
        return 1

    FanCurveDaemon(fan_control).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
