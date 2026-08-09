"""Command-line control of the keyboard backlight, battery charge
thresholds, and performance mode, sharing the same backends as the GUI
app. Also used internally by the keyboard boot-persistence systemd units
(`keyboard save-state` / `keyboard restore-state`) so they don't need to
know backend details. Performance mode persistence works differently --
see performance.py -- and has no systemd units of its own."""

import argparse
import colorsys
import json
import signal
import sys
import time
from pathlib import Path

from .auto_profile import AutoProfileConfig, is_on_ac
from .backend import BacklightError, KeyboardBacklight
from .battery import ChargeThresholdError, ChargeThresholds
from .display import DisplayRefreshRate, DisplayRefreshRateError
from .fan import FanControl, FanControlError
from .fan_curve import STATUS_FILE, FanCurveConfig, validate_curve
from .performance import MODES as PERFORMANCE_MODES
from .performance import POWER_PROFILE_MODES
from .performance import PerformanceMode, PerformanceModeError
from .sensors import read_fan_rpms

DEFAULT_CYCLE = ["FFFFFF", "0000FF", "FF0000", "FF00FF", "00FF00", "00FFFF", "FFFF00"]

# Fixed key used to store the single color of a single-zone backend in the
# same state-file shape as a multi-zone one.
SINGLE_ZONE_KEY = "_single"


def die(msg):
    print(f"clevo-control-panel-cli: {msg}", file=sys.stderr)
    sys.exit(1)


def _open_backend():
    try:
        return KeyboardBacklight()
    except BacklightError as e:
        die(str(e))


def _open_battery():
    backend = _open_backend()
    try:
        return ChargeThresholds(backend.device_dir)
    except ChargeThresholdError as e:
        die(str(e))


def _open_performance():
    backend = _open_backend()
    try:
        return PerformanceMode(backend.device_dir)
    except PerformanceModeError as e:
        die(str(e))


def _open_fan_control():
    backend = _open_backend()
    try:
        return FanControl(backend.device_dir)
    except FanControlError as e:
        die(str(e))


def _write(fn, *args):
    try:
        fn(*args)
    except PermissionError:
        die(
            "permission denied. Log out/in after setup added you to the "
            "'clevoctl' group, or run this command with sudo."
        )
    except (OSError, ValueError) as e:
        die(str(e))


# ---- keyboard commands ----


def cmd_keyboard_status(args):
    backend = _open_backend()
    print(f"backend: {backend.name}")
    print(f"brightness: {backend.get_brightness()}/{backend.max_brightness}")
    if backend.zones is None:
        print(f"color: #{backend.get_color()}")
    else:
        for zone in backend.zones:
            print(f"{zone:>9}: #{backend.get_color(zone)}")


def cmd_keyboard_brightness(args):
    backend = _open_backend()
    if not 0 <= args.value <= backend.max_brightness:
        die(f"brightness must be 0-{backend.max_brightness}")
    _write(backend.set_brightness, args.value)


def cmd_keyboard_color(args):
    backend = _open_backend()
    if args.zone and backend.zones is None:
        die(f"backend {backend.name!r} has no zones (single-zone hardware)")
    _write(backend.set_color, args.color, args.zone)


def _install_sigint_stop():
    stop = {"flag": False}

    def handler(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    return stop


def cmd_keyboard_effect_cycle(args):
    backend = _open_backend()
    colors = args.colors or DEFAULT_CYCLE
    stop = _install_sigint_stop()
    i = 0
    while not stop["flag"]:
        _write(backend.set_color, colors[i % len(colors)])
        i += 1
        time.sleep(args.period)


def cmd_keyboard_effect_breathe(args):
    backend = _open_backend()
    _write(backend.set_color, args.color)
    max_b = backend.max_brightness
    stop = _install_sigint_stop()
    steps = 40
    t = 0
    while not stop["flag"]:
        phase = (t % steps) / steps
        level = 1 - abs(2 * phase - 1)  # triangle wave 0..1..0
        _write(backend.set_brightness, max(1, int(level * max_b)))
        t += 1
        time.sleep(args.period / steps)
    _write(backend.set_brightness, max_b)


def cmd_keyboard_effect_wave(args):
    backend = _open_backend()
    if backend.zones is None:
        die(f"backend {backend.name!r} has no zones; use 'effect cycle' instead")
    stop = _install_sigint_stop()
    n = len(backend.zones)
    t = 0.0
    while not stop["flag"]:
        for idx, zone in enumerate(backend.zones):
            hue = (t + idx / n) % 1.0
            r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0))
            _write(backend.set_color, f"{r:02x}{g:02x}{b:02x}", zone)
        t += args.step
        time.sleep(args.period)


def cmd_keyboard_save_state(args):
    try:
        backend = KeyboardBacklight()
    except BacklightError:
        return  # nothing to save if there's no supported hardware
    state = {"brightness": backend.get_brightness(), "zones": {}}
    if backend.zones is None:
        state["zones"][SINGLE_ZONE_KEY] = backend.get_color()
    else:
        for zone in backend.zones:
            state["zones"][zone] = backend.get_color(zone)
    Path(args.path).write_text(json.dumps(state))


def cmd_keyboard_restore_state(args):
    path = Path(args.path)
    if not path.exists():
        return
    try:
        backend = KeyboardBacklight()
    except BacklightError:
        return
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    for zone, hex_color in state.get("zones", {}).items():
        try:
            if backend.zones is None:
                if zone == SINGLE_ZONE_KEY:
                    backend.set_color(hex_color)
            elif zone in backend.zones:
                backend.set_color(hex_color, zone)
        except (OSError, ValueError):
            pass  # best-effort: don't let one bad zone block the rest

    if "brightness" in state:
        try:
            backend.set_brightness(state["brightness"])
        except (OSError, ValueError):
            pass


# ---- battery commands ----


def cmd_battery_status(args):
    battery = _open_battery()
    print(f"start threshold: {battery.get_start()}%")
    print(f"end threshold: {battery.get_end()}%")
    print(f"writable: {'yes' if battery.is_writable() else 'no'}")


def cmd_battery_set(args):
    if args.start is None and args.end is None:
        die("specify --start and/or --end")
    battery = _open_battery()
    if args.start is not None:
        _write(battery.set_start, args.start)
    if args.end is not None:
        _write(battery.set_end, args.end)


# ---- performance commands ----


def cmd_performance_status(args):
    performance = _open_performance()
    print(f"mode: {performance.get_mode()}")
    print(f"writable: {'yes' if performance.is_writable() else 'no'}")


def cmd_performance_set(args):
    performance = _open_performance()
    _write(performance.set_mode, args.mode)


def cmd_performance_auto_status(args):
    config = AutoProfileConfig()
    print(f"enabled: {'yes' if config.enabled else 'no'}")
    print(f"ac profile: {config.ac_profile}")
    print(f"battery profile: {config.battery_profile}")
    on_ac = is_on_ac()
    if on_ac is None:
        print("current power source: unknown (no AC/battery distinction found)")
    else:
        print(f"current power source: {'AC' if on_ac else 'battery'}")


def cmd_performance_auto_set(args):
    if (
        args.enabled is None
        and args.ac is None
        and args.battery is None
        and args.ac_refresh is None
        and args.battery_refresh is None
    ):
        die("specify --enabled, --ac, --battery, --ac-refresh, and/or --battery-refresh")

    config = AutoProfileConfig()
    if args.enabled is not None:
        config.enabled = args.enabled == "on"
    if args.ac is not None:
        config.ac_profile = args.ac
    if args.battery is not None:
        config.battery_profile = args.battery
    if args.ac_refresh is not None:
        if not 1 <= args.ac_refresh <= 1000:
            die("--ac-refresh must be 1-1000")
        config.ac_refresh_hz = args.ac_refresh
    if args.battery_refresh is not None:
        if not 1 <= args.battery_refresh <= 1000:
            die("--battery-refresh must be 1-1000")
        config.battery_refresh_hz = args.battery_refresh
    config.save()

    if config.enabled:
        on_ac = is_on_ac()
        if on_ac is not None:
            performance = _open_performance()
            _write(performance.set_mode, config.profile_for(on_ac))
            hz = config.refresh_hz_for(on_ac)
            if hz is not None:
                try:
                    DisplayRefreshRate().set_rate(hz)
                except DisplayRefreshRateError:
                    pass  # comfort feature only; never block the rest of the switch


# ---- fan commands ----


def _fan_daemon_health_hint(config):
    if not config.enabled:
        return "not needed (curve disabled)"
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return "unknown -- no status file yet (daemon may not be running)"
    age = time.time() - status.get("timestamp", 0)
    if age > 10:
        return (
            f"stale ({age:.0f}s old) -- fan speed could be stuck; check "
            "'systemctl status clevo-fan-curve.service'"
        )
    if status.get("critical_override_active"):
        return "running, critical temp override active"
    return "running, curve active"


def cmd_fan_status(args):
    fan_control = _open_fan_control()
    rpms = read_fan_rpms()
    for i, fan in enumerate(fan_control.fans()):
        rpm = f"{rpms[i]} RPM" if i < len(rpms) else "RPM unknown"
        print(
            f"fan{fan}: {fan_control.get_duty(fan)}% duty, "
            f"{fan_control.get_temp(fan)}C, {rpm}"
        )
    print(f"manual override active: {'yes' if fan_control.is_manual_active() else 'no'}")
    config = FanCurveConfig()
    print(f"curve: {'enabled' if config.enabled else 'disabled'}")
    print(f"daemon: {_fan_daemon_health_hint(config)}")


def cmd_fan_curve_show(args):
    config = FanCurveConfig()
    print(f"enabled: {'yes' if config.enabled else 'no'}")
    print(f"hysteresis: {config.hysteresis_c}C")
    print(f"critical temp: {config.critical_temp_c}C")
    print(f"max duty: {config.max_duty_percent}%")
    print("points (temp_c:percent):")
    for point in config.curve:
        print(f"  {point['temp_c']}:{point['percent']}")


def cmd_fan_curve_set(args):
    if (
        args.point is None
        and args.hysteresis is None
        and args.critical_temp is None
        and args.max_duty is None
    ):
        die("specify --point, --hysteresis, --critical-temp, and/or --max-duty")

    config = FanCurveConfig()

    if args.point is not None:
        points = []
        for spec in args.point:
            temp_str, sep, percent_str = spec.partition(":")
            if not sep:
                die(f"invalid --point {spec!r}, expected TEMP:PERCENT")
            try:
                points.append({"temp_c": int(temp_str), "percent": int(percent_str)})
            except ValueError:
                die(f"invalid --point {spec!r}, expected TEMP:PERCENT")
        try:
            config.curve = validate_curve(points)
        except ValueError as e:
            die(str(e))

    if args.hysteresis is not None:
        if not 0 <= args.hysteresis <= 20:
            die("--hysteresis must be 0-20")
        config.hysteresis_c = args.hysteresis

    if args.critical_temp is not None:
        if not 60 <= args.critical_temp <= 105:
            die("--critical-temp must be 60-105")
        config.critical_temp_c = args.critical_temp

    if args.max_duty is not None:
        if not 1 <= args.max_duty <= 100:
            die("--max-duty must be 1-100")
        config.max_duty_percent = args.max_duty

    if any(p["temp_c"] >= config.critical_temp_c for p in config.curve):
        die("curve points must all be below the critical temp")

    config.save()


def cmd_fan_enable(args):
    config = FanCurveConfig()
    config.enabled = True
    config.save()
    print("fan curve enabled")


def cmd_fan_disable(args):
    config = FanCurveConfig()
    config.enabled = False
    config.save()
    print("fan curve disabled")


def cmd_fan_release(args):
    fan_control = _open_fan_control()
    _write(fan_control.release)
    print("fan control released to firmware auto")


def cmd_fan_set(args):
    fan_control = _open_fan_control()
    if args.fan == "all":
        fans = fan_control.fans()
    else:
        fan = int(args.fan)
        if fan not in fan_control.fans():
            die(f"fan {fan} not present on this board")
        fans = [fan]

    for fan in fans:
        _write(fan_control.set_manual_duty, fan, args.duty)

    if args.duration is not None:
        stop = _install_sigint_stop()
        deadline = time.monotonic() + args.duration
        while not stop["flag"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(2, remaining))
            _write(fan_control.ping)
        _write(fan_control.release)
        print("fan control released to firmware auto")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Control keyboard RGB backlight (system76_acpi or clevo-acpi), "
            "battery charge thresholds, performance/fan mode, and custom "
            "fan curves (clevo-acpi only)."
        )
    )
    top = parser.add_subparsers(dest="group", required=True)

    keyboard = top.add_parser("keyboard", help="keyboard RGB backlight control")
    sub = keyboard.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="show backend, brightness, and zone colors")
    p.set_defaults(func=cmd_keyboard_status)

    p = sub.add_parser("brightness", help="set backlight brightness")
    p.add_argument("value", type=int)
    p.set_defaults(func=cmd_keyboard_brightness)

    p = sub.add_parser("color", help="set a static color")
    p.add_argument("color", help="hex color, e.g. ff0000 or #ff0000")
    p.add_argument("--zone", help="zone name (multi-zone hardware only)")
    p.set_defaults(func=cmd_keyboard_color)

    effect = sub.add_parser("effect", help="run a lighting effect (Ctrl+C to stop)")
    effect_sub = effect.add_subparsers(dest="effect", required=True)

    p = effect_sub.add_parser("cycle", help="cycle through a color list")
    p.add_argument("--colors", nargs="+", help="hex colors to cycle through")
    p.add_argument("--period", type=float, default=1.0, help="seconds per color")
    p.set_defaults(func=cmd_keyboard_effect_cycle)

    p = effect_sub.add_parser("breathe", help="pulse brightness with a fixed color")
    p.add_argument("--color", default="FFFFFF")
    p.add_argument("--period", type=float, default=2.0, help="seconds per full pulse")
    p.set_defaults(func=cmd_keyboard_effect_breathe)

    p = effect_sub.add_parser(
        "wave", help="moving rainbow across zones (multi-zone only)"
    )
    p.add_argument("--period", type=float, default=0.05, help="seconds between steps")
    p.add_argument("--step", type=float, default=0.02, help="hue advance per step")
    p.set_defaults(func=cmd_keyboard_effect_wave)

    p = sub.add_parser("save-state", help=argparse.SUPPRESS)  # used by systemd units
    p.add_argument("path")
    p.set_defaults(func=cmd_keyboard_save_state)

    p = sub.add_parser("restore-state", help=argparse.SUPPRESS)
    p.add_argument("path")
    p.set_defaults(func=cmd_keyboard_restore_state)

    battery = top.add_parser("battery", help="battery charge threshold control")
    bsub = battery.add_subparsers(dest="command", required=True)

    p = bsub.add_parser("status", help="show current charge thresholds")
    p.set_defaults(func=cmd_battery_status)

    p = bsub.add_parser("set", help="set charge threshold(s)")
    p.add_argument("--start", type=int, help="resume charging below this percent")
    p.add_argument("--end", type=int, help="stop charging at this percent")
    p.set_defaults(func=cmd_battery_set)

    performance = top.add_parser(
        "performance", help="performance/fan mode control"
    )
    psub = performance.add_subparsers(dest="command", required=True)

    p = psub.add_parser("status", help="show current performance mode")
    p.set_defaults(func=cmd_performance_status)

    p = psub.add_parser("set", help="set performance mode")
    p.add_argument("mode", choices=PERFORMANCE_MODES)
    p.set_defaults(func=cmd_performance_set)

    auto = psub.add_parser(
        "auto", help="automatic profile switching by power source"
    )
    asub = auto.add_subparsers(dest="auto_command", required=True)

    p = asub.add_parser("status", help="show auto-switch configuration")
    p.set_defaults(func=cmd_performance_auto_status)

    p = asub.add_parser("set", help="configure auto-switch")
    p.add_argument("--enabled", choices=["on", "off"])
    p.add_argument(
        "--ac", choices=sorted(POWER_PROFILE_MODES), help="profile to use on AC power"
    )
    p.add_argument(
        "--battery",
        choices=sorted(POWER_PROFILE_MODES),
        help="profile to use on battery power",
    )
    p.add_argument(
        "--ac-refresh",
        type=int,
        metavar="HZ",
        help="display refresh rate to apply on AC power (GNOME/Wayland only); omit to leave unchanged",
    )
    p.add_argument(
        "--battery-refresh",
        type=int,
        metavar="HZ",
        help="display refresh rate to apply on battery power (GNOME/Wayland only); omit to leave unchanged",
    )
    p.set_defaults(func=cmd_performance_auto_set)

    fan = top.add_parser("fan", help="custom fan curve and manual fan control")
    fsub = fan.add_subparsers(dest="command", required=True)

    p = fsub.add_parser("status", help="show live fan duty/RPM/temp and curve status")
    p.set_defaults(func=cmd_fan_status)

    curve = fsub.add_parser("curve", help="view or edit the fan curve")
    csub = curve.add_subparsers(dest="curve_command", required=True)

    p = csub.add_parser("show", help="show the current fan curve and settings")
    p.set_defaults(func=cmd_fan_curve_show)

    p = csub.add_parser(
        "set", help="replace the fan curve points and/or safety settings"
    )
    p.add_argument(
        "--point",
        action="append",
        metavar="TEMP:PERCENT",
        help="a curve point, e.g. --point 65:55; repeat for multiple points. "
        "Replaces the whole point list when given.",
    )
    p.add_argument("--hysteresis", type=int, metavar="N", help="degrees C, 0-20")
    p.add_argument("--critical-temp", type=int, metavar="N", help="degrees C, 60-105")
    p.add_argument("--max-duty", type=int, metavar="N", help="percent, 1-100")
    p.set_defaults(func=cmd_fan_curve_set)

    p = fsub.add_parser("enable", help="enable the fan curve daemon")
    p.set_defaults(func=cmd_fan_enable)

    p = fsub.add_parser("disable", help="disable the fan curve daemon")
    p.set_defaults(func=cmd_fan_disable)

    p = fsub.add_parser(
        "release", help="immediately release fan control back to firmware auto"
    )
    p.set_defaults(func=cmd_fan_release)

    p = fsub.add_parser(
        "set", help="manually command a fan duty (kernel watchdog auto-releases)"
    )
    p.add_argument("--fan", choices=["1", "2", "all"], required=True)
    p.add_argument("--duty", type=int, required=True, metavar="N", help="percent, 0-100")
    p.add_argument(
        "--duration",
        type=float,
        metavar="SECONDS",
        help="keep petting the watchdog for this long, then release; if "
        "omitted, the kernel watchdog releases on its own once this "
        "process exits and stops petting",
    )
    p.set_defaults(func=cmd_fan_set)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
