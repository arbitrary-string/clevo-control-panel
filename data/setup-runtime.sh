#!/bin/sh
# Runtime setup for Clevo Control Panel: group, udev activation, systemd
# services. Safe to re-run. Assumes the desktop entry, icon, udev rule, and
# systemd units are already installed at their standard locations (either by
# the .deb package, or by data/install.sh in a repo checkout).
set -e

GROUP=clevoctl
S76_LED_DIR="/sys/class/leds/system76_acpi::kbd_backlight"
CLEVO_LED_DIR="/sys/class/leds/clevo-acpi::kbd_backlight"
STATE_DIR="/var/lib/clevo-control-panel"

TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] && [ -n "${PKEXEC_UID:-}" ]; then
  TARGET_USER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
fi
if [ -z "$TARGET_USER" ]; then
  TARGET_USER="$(logname 2>/dev/null || true)"
fi
if [ -z "$TARGET_USER" ]; then
  TARGET_USER="${1:-}"
fi

getent group "$GROUP" >/dev/null 2>&1 || groupadd --system "$GROUP"

if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
  usermod -aG "$GROUP" "$TARGET_USER"
else
  echo "Clevo Control Panel: could not determine which user to grant hardware access to." >&2
  echo "Run: sudo usermod -aG $GROUP <username>" >&2
fi

udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --action=add --subsystem-match=leds --subsystem-match=platform 2>/dev/null || true

if [ -e "$S76_LED_DIR/color" ]; then
  chgrp "$GROUP" "$S76_LED_DIR/color" "$S76_LED_DIR/brightness" 2>/dev/null || true
  chmod 0664 "$S76_LED_DIR/color" "$S76_LED_DIR/brightness" 2>/dev/null || true
fi

if [ -e "$CLEVO_LED_DIR/brightness" ]; then
  chgrp "$GROUP" "$CLEVO_LED_DIR/brightness" 2>/dev/null || true
  chmod 0664 "$CLEVO_LED_DIR/brightness" 2>/dev/null || true
  for zone in left center right numpad lightbar; do
    zf="$CLEVO_LED_DIR/device/color_$zone"
    if [ -e "$zf" ]; then
      chgrp "$GROUP" "$zf" 2>/dev/null || true
      chmod 0664 "$zf" 2>/dev/null || true
    fi
  done
  # Battery charge thresholds, if this clevo-acpi build has flexicharger
  # support. Same platform device as the color_<zone> files above.
  for attr in charge_control_start_threshold charge_control_end_threshold; do
    af="$CLEVO_LED_DIR/device/$attr"
    if [ -e "$af" ]; then
      chgrp "$GROUP" "$af" 2>/dev/null || true
      chmod 0664 "$af" 2>/dev/null || true
    fi
  done
fi

mkdir -p "$STATE_DIR"

systemctl daemon-reload 2>/dev/null || true
systemctl enable --now save-keyboard-color.service restore-keyboard-color.service 2>/dev/null || true

if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
  echo "Clevo Control Panel: $TARGET_USER can now control the keyboard backlight and battery charge thresholds directly."
  echo "If this is the first time, log out and back in (or reboot) for group membership to apply."
fi
