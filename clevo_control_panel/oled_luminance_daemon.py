"""Background daemon keeping this panel's real full-range OLED brightness
active, layered on top of oled_luminance.py the same way fan_curve.py
layers a control loop on top of fan.py.

Unlike the fan curve (opt-in, off by default), this defaults to enabled:
it's a straight bug workaround with no downside when applicable, and
nothing to configure to get the benefit -- your existing brightness
slider/hotkeys just start reaching the panel's real range instead of the
legacy path's capped one.

Mode is fixed for the life of a boot (a live MUX switch isn't a thing on
this hardware -- see the mux investigation writeup in
~/odm-laptop-research/NOTES.md), so unlike the fan curve daemon this
doesn't need to keep re-checking applicability throughout its life:
hardware init happens once at startup in main(), and a clean exit (not a
crash-loop) is the expected, normal outcome on any board/mode where this
doesn't apply -- MSHybrid mode, a different panel without
PANEL_LUMINANCE_CONTROL_CAP, or no NVIDIA GPU at all.

The one thing that *does* need continuous handling during the run: every
GNOME/hotkey brightness change goes through the nvidia driver's own
legacy-path brightness handling first, which -- confirmed live -- resets
DPCD 0x721's luminance-mode-enable bit as a side effect. So this
re-enables luminance mode on every detected brightness change, not just
once at startup, immediately followed by the real TARGET_LUMINANCE write
for that change -- not a fixed slow poll independent of it.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

from .oled_luminance import NvidiaDpcdBacklight, NvidiaDpcdBacklightError

CONFIG_DIR = Path("/var/lib/clevo-control-panel")
CONFIG_FILE = CONFIG_DIR / "oled-luminance.json"
STATUS_FILE = CONFIG_DIR / "oled-luminance-status.json"

BACKLIGHT_SYSFS = Path("/sys/class/backlight/nvidia_0/brightness")

# Verified live on this exact panel (EDO SH / EF25QBA63.A): EDID full-
# coverage max is 507.5 cd/m^2, 500 leaves headroom under that. A
# different panel would need different values -- see has_luminance_
# control_cap() in oled_luminance.py, which gates whether this daemon
# does anything at all rather than assuming these defaults are safe
# elsewhere.
DEFAULT_MIN_NITS = 5
DEFAULT_MAX_NITS = 500

POLL_INTERVAL_SECONDS = 0.1  # cheap: one small sysfs read; DPCD traffic only on an actual change
SAFETY_REASSERT_SECONDS = 30  # cheap independent-of-slider-activity re-check, defense in depth


def _clamp_int(value, low, high, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


class OledLuminanceConfig:
    def __init__(self):
        self.enabled = True
        self.min_nits = DEFAULT_MIN_NITS
        self.max_nits = DEFAULT_MAX_NITS
        self._mtime = None
        self.load()

    def load(self):
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            return
        self.enabled = bool(data.get("enabled", self.enabled))
        self.min_nits = _clamp_int(data.get("min_nits"), 1, 2000, self.min_nits)
        self.max_nits = _clamp_int(data.get("max_nits"), self.min_nits + 1, 2000, self.max_nits)

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
        data = {"enabled": self.enabled, "min_nits": self.min_nits, "max_nits": self.max_nits}
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass


def _percent_to_mcd(percent, min_nits, max_nits):
    """Same exponential (perceptually-even) mapping as this project's
    prior EC-RAM-fix-era brightness tooling used for a comparable OLED
    panel: linear-in-percent would spend most of the slider's range on
    luminance differences too small to perceive near the top end."""
    percent = max(0, min(100, percent))
    if percent <= 0:
        nits = min_nits
    elif percent >= 100:
        nits = max_nits
    else:
        nits = min_nits * ((max_nits / min_nits) ** (percent / 100.0))
    return int(nits * 1000)  # cd/m^2 -> mcd/m^2


def _write_status(state, current_nits=None, reason=None):
    data = {
        "timestamp": time.time(),
        "state": state,  # "active" | "disabled" | "not_applicable" | "unsupported_panel" | "error"
        "current_nits": current_nits,
        "reason": reason,
    }
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(STATUS_FILE)
    except OSError:
        pass


class OledLuminanceDaemon:
    def __init__(self, backlight, config=None):
        self.backlight = backlight
        self.config = config or OledLuminanceConfig()
        self._stop = False
        self._last_percent = None

    def _install_signal_handlers(self):
        def handler(signum, frame):
            self._stop = True

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def _read_slider_percent(self):
        try:
            return int(BACKLIGHT_SYSFS.read_text().strip())
        except (OSError, ValueError):
            return None

    def _reinit_backlight(self):
        try:
            self.backlight.close()
        except Exception:
            pass
        self.backlight = NvidiaDpcdBacklight()

    def _apply(self, percent):
        # A DPCD AUX-channel write can fail transiently through a
        # perfectly good, already-open RM session -- confirmed live,
        # repeatedly, retrying the *same* session on the next tick never
        # recovered on its own. nv-bright's own author hit and documented
        # this exact failure mode, and their fix wasn't "wait and retry
        # the same handles," it was closing and reopening the RM session
        # entirely before retrying -- that's what actually clears it.
        mcd = _percent_to_mcd(percent, self.config.min_nits, self.config.max_nits)
        try:
            self.backlight.enable_luminance_mode()
            self.backlight.set_target_luminance_mcd(mcd)
        except NvidiaDpcdBacklightError:
            self._reinit_backlight()
            self.backlight.enable_luminance_mode()
            self.backlight.set_target_luminance_mcd(mcd)
        return mcd

    def run(self):
        self._install_signal_handlers()

        if self._read_slider_percent() is None:
            _write_status("error", reason="could not read nvidia_0 backlight sysfs")
            return

        # Deliberately not applying the current brightness immediately
        # here: self._last_percent starts as None, which the loop below
        # already treats as "changed" on its first iteration -- one code
        # path for the initial apply and every later one, including the
        # same NvidiaDpcdBacklightError handling (confirmed live this
        # matters: an unguarded initial DPCD write here once hit a
        # transient AUX-channel failure -- the same category nv-bright's
        # own author documented -- and crashed the whole process instead
        # of just retrying next tick like every other failure does).
        last_reassert = time.monotonic()

        while not self._stop:
            self.config.reload_if_changed()
            time.sleep(POLL_INTERVAL_SECONDS)

            if not self.config.enabled:
                if self._last_percent is not None:
                    _write_status("disabled")
                    self._last_percent = None
                continue

            percent = self._read_slider_percent()
            if percent is None:
                continue

            now = time.monotonic()
            changed = percent != self._last_percent
            due_for_safety_reassert = (now - last_reassert) >= SAFETY_REASSERT_SECONDS
            if not (changed or due_for_safety_reassert):
                continue

            try:
                mcd = self._apply(percent)
            except NvidiaDpcdBacklightError as exc:
                _write_status("error", reason=str(exc))
                continue

            self._last_percent = percent
            last_reassert = now
            _write_status("active", current_nits=mcd / 1000)


INIT_RETRY_ATTEMPTS = 5
INIT_RETRY_DELAY_SECONDS = 2


def _open_backlight_with_retry():
    """The unit's own ConditionPathExists=/dev/nvidia0 already means the
    device exists by the time this runs -- so a failure constructing
    NvidiaDpcdBacklight here means either genuine MSHybrid-mode-style
    unavailability (no eDP display will ever be found, however many
    times we ask) or a startup race, where the GPU/display subsystem
    hasn't fully settled yet even after graphical.target -- confirmed
    live: the very first boot after adding this retry loop hit exactly
    this, failing once immediately at boot and succeeding on ordinary
    manual retry moments later. There's no reliable way to tell those
    two cases apart from the exception alone, so retry a handful of
    times with a short delay before concluding it's really the former.
    """
    last_exc = None
    for attempt in range(INIT_RETRY_ATTEMPTS):
        try:
            return NvidiaDpcdBacklight()
        except NvidiaDpcdBacklightError as exc:
            last_exc = exc
            if attempt < INIT_RETRY_ATTEMPTS - 1:
                time.sleep(INIT_RETRY_DELAY_SECONDS)
    raise last_exc


def main():
    try:
        backlight = _open_backlight_with_retry()
    except NvidiaDpcdBacklightError as exc:
        # Expected, normal outcome in MSHybrid mode (or any board without
        # this GPU/panel combination) -- not a failure worth a restart loop.
        _write_status("not_applicable", reason=str(exc))
        print(f"clevo-oled-luminance-daemon: not applicable here: {exc}", file=sys.stderr)
        return 0

    try:
        if not backlight.has_luminance_control_cap():
            _write_status(
                "unsupported_panel",
                reason="panel does not advertise PANEL_LUMINANCE_CONTROL_CAP",
            )
            print(
                "clevo-oled-luminance-daemon: panel doesn't support the extended "
                "luminance-control mode this daemon requires",
                file=sys.stderr,
            )
            return 0

        OledLuminanceDaemon(backlight).run()
        return 0
    finally:
        backlight.close()


if __name__ == "__main__":
    sys.exit(main())
