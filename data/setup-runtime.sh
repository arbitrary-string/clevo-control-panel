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
  # Battery charge thresholds, performance mode, and the read-write fan
  # control attributes, if this clevo-acpi build has them. Same platform
  # device as the color_<zone> files above. The read-only fan attributes
  # (fanN_duty/fanN_temp*/fan_manual_active) are deliberately NOT touched
  # here -- their kernel-assigned mode (0444) already allows any user to
  # read them, so there's nothing to grant.
  for attr in charge_control_start_threshold charge_control_end_threshold \
      performance_mode \
      fan1_manual_duty fan2_manual_duty fan3_manual_duty \
      fan_watchdog_timeout_ms; do
    af="$CLEVO_LED_DIR/device/$attr"
    if [ -e "$af" ]; then
      chgrp "$GROUP" "$af" 2>/dev/null || true
      chmod 0664 "$af" 2>/dev/null || true
    fi
  done

  # Write-only attributes (kernel-assigned mode 0200, no show() handler
  # at all) -- add group write without adding a read bit that could
  # never actually be satisfied.
  for attr in fan_watchdog_ping fan_release; do
    af="$CLEVO_LED_DIR/device/$attr"
    if [ -e "$af" ]; then
      chgrp "$GROUP" "$af" 2>/dev/null || true
      chmod 0220 "$af" 2>/dev/null || true
    fi
  done
fi

mkdir -p "$STATE_DIR"

# Group-writable, with setgid so new files (auto-profile.json,
# performance-mode.state) created by whichever clevoctl member happens to
# write them first are automatically group-owned by clevoctl too, not
# just that one user's own primary group. Unlike keyboard's state.json
# (only ever written by save-keyboard-color.service running as root),
# these are written directly from an ordinary user session -- see
# clevo_control_panel/performance.py and auto_profile.py for why.
chgrp "$GROUP" "$STATE_DIR" 2>/dev/null || true
chmod 2775 "$STATE_DIR" 2>/dev/null || true
for f in performance-mode.state auto-profile.json; do
  if [ -e "$STATE_DIR/$f" ]; then
    chgrp "$GROUP" "$STATE_DIR/$f" 2>/dev/null || true
    chmod 0664 "$STATE_DIR/$f" 2>/dev/null || true
  fi
done

systemctl daemon-reload 2>/dev/null || true
systemctl enable --now save-keyboard-color.service restore-keyboard-color.service 2>/dev/null || true

# A systemd --user unit, not a system one, enabled for $TARGET_USER
# specifically -- see the comment at the top of clevo-fan-curve.service
# for why (never touch this EC interface before an interactive login
# exists). Always enabled once it can be: the daemon itself no-ops (a
# cheap poll of its own tiny config file, no EC access at all) whenever
# the fan-curve feature is disabled, so there's no cost to leaving it
# running unconditionally -- same reasoning as the two system services
# above. Its unit has its own ConditionPathExists guard for boards
# without the fan control attributes at all, so this is also safe to run
# on hardware that predates them. Requires an active session for
# $TARGET_USER (a real XDG_RUNTIME_DIR) to reach their systemd --user
# manager -- silently skipped otherwise (e.g. an unattended install with
# nobody logged in yet); it'll pick this up the next time they log in
# via the WantedBy=default.target in the unit's own [Install] section,
# same as any other --user unit enabled this way.
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
  TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || true)"
  if [ -n "$TARGET_UID" ] && [ -d "/run/user/$TARGET_UID" ]; then
    sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" \
      systemctl --user enable --now clevo-fan-curve.service 2>/dev/null || true
  fi
fi

if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
  echo "Clevo Control Panel: $TARGET_USER can now control the keyboard backlight, battery charge thresholds, and performance mode directly."
  echo "If this is the first time, log out and back in (or reboot) for group membership to apply."
fi
