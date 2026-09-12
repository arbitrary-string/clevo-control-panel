#!/bin/sh
# Signals the running clevo-oled-luminance daemon to immediately
# reassert DPCD luminance mode, rather than waiting for its periodic
# safety reassert. Invoked right after a display refresh-rate switch
# (see window.py's _apply_auto_profile_now) -- confirmed live that
# applying a new monitor config through Mutter resets DPCD 0x721's
# luminance-mode-enable bit the same way a legacy brightness change
# does, silently, with no corresponding backlight-percentage change for
# the daemon's own change-detection to notice on its own.
#
# Root-only -- invoked via a narrowly-scoped sudoers rule (see
# setup-oled-luminance-nudge-sudoers.sh) since the daemon is a system
# service. A no-op (not an error) if the daemon isn't running at all --
# this is a pure timing optimization, never a hard requirement.
set -eu

systemctl kill --signal=SIGUSR1 clevo-oled-luminance.service 2>/dev/null || true
