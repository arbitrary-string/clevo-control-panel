"""Command-line control of the keyboard backlight and battery charge
thresholds, sharing the same backends as the GUI app. Also used internally
by the boot-persistence systemd units (`keyboard save-state` /
`keyboard restore-state`) so they don't need to know backend details."""

import argparse
import colorsys
import json
import signal
import sys
import time
from pathlib import Path

from .backend import BacklightError, KeyboardBacklight
from .battery import ChargeThresholdError, ChargeThresholds

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


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Control keyboard RGB backlight (system76_acpi or clevo-acpi) and "
            "battery charge thresholds (clevo-acpi flexicharger only)."
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
