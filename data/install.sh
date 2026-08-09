#!/usr/bin/env bash
# One-time system setup for a repo checkout of Clevo Control Panel (not the
# .deb, which handles this via postinst + data/setup-runtime.sh instead).
# Run as root (via sudo or pkexec). Safe to re-run.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root (e.g. sudo $0 or via pkexec)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] && [ -n "${PKEXEC_UID:-}" ]; then
  TARGET_USER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
fi
if [ -z "$TARGET_USER" ]; then
  TARGET_USER="${1:-}"
fi
if [ -z "$TARGET_USER" ]; then
  echo "Could not determine which user to grant access to." >&2
  echo "Pass the username as an argument: sudo $0 <username>" >&2
  exit 1
fi
USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

echo "Setting up Clevo Control Panel for user: $TARGET_USER"

# 0. Runtime dependencies:
#    - python3-gi-cairo: PyGObject's cairo integration, needed to draw the
#      color swatches.
#    - gir1.2-gtk-3.0 / gir1.2-ayatanaappindicator3-0.1: the tray icon
#      helper process (see tray_helper.py for why it's GTK3, not GTK4 like
#      the rest of the app). The app degrades gracefully to no tray icon
#      if these are missing.
#    - tlp: applies real CPU (and, if present, NVIDIA GPU) power/
#      performance scaling for the Quiet/Balanced/Performance modes, on
#      top of the fan behavior those modes already control directly. See
#      data/apply-power-profile.sh.
if command -v apt-get >/dev/null 2>&1; then
  for pkg in python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 tlp; do
    dpkg -s "$pkg" >/dev/null 2>&1 || apt-get install -y "$pkg"
  done
fi

# 1. Static files a .deb would normally place via its own file list.
install -m 0644 "$SCRIPT_DIR/99-clevo-control-panel.rules" /etc/udev/rules.d/99-clevo-control-panel.rules
install -m 0644 "$SCRIPT_DIR/save-keyboard-color.service" /etc/systemd/system/save-keyboard-color.service
install -m 0644 "$SCRIPT_DIR/restore-keyboard-color.service" /etc/systemd/system/restore-keyboard-color.service

# A systemd --user unit, not a system one -- see the comment at the top
# of clevo-fan-curve.service for why (never touch this EC interface
# before an interactive login exists). Installed into $TARGET_USER's own
# unit directory rather than a system-wide user-unit path, matching this
# script's existing per-user scope (it already only sets up one user).
USER_SYSTEMD_DIR="$USER_HOME/.config/systemd/user"
sudo -u "$TARGET_USER" mkdir -p "$USER_SYSTEMD_DIR"
install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" \
  "$SCRIPT_DIR/clevo-fan-curve.service" "$USER_SYSTEMD_DIR/clevo-fan-curve.service"

# The systemd units above hardcode /usr/bin/clevo-control-panel-cli and
# /usr/bin/clevo-fan-curve-daemon (absolute paths, since a .deb install
# would provide them there). A repo checkout has no such files on PATH
# otherwise, so symlink them in.
ln -sf "$SCRIPT_DIR/../bin/clevo-control-panel-cli" /usr/bin/clevo-control-panel-cli
ln -sf "$SCRIPT_DIR/../bin/clevo-control-panel" /usr/bin/clevo-control-panel
ln -sf "$SCRIPT_DIR/../bin/clevo-fan-curve-daemon" /usr/bin/clevo-fan-curve-daemon

# apply-power-profile.sh needs a stable absolute path too, since it's
# referenced by exact path in the sudoers rule that grants the clevoctl
# group passwordless access to it (see the printed instructions at the
# end of this script -- that file is intentionally NOT installed
# automatically here).
mkdir -p /usr/lib/clevo-control-panel
ln -sf "$SCRIPT_DIR/apply-power-profile.sh" /usr/lib/clevo-control-panel/apply-power-profile.sh
chmod 0755 "$SCRIPT_DIR/apply-power-profile.sh"

APPS_DIR="$USER_HOME/.local/share/applications"
ICON_DIR="$USER_HOME/.local/share/icons/hicolor/scalable/apps"
sudo -u "$TARGET_USER" mkdir -p "$APPS_DIR" "$ICON_DIR"
sed "s#__EXEC_PATH__#$SCRIPT_DIR/../bin/clevo-control-panel#" "$SCRIPT_DIR/clevo-control-panel.desktop.in" \
  > "$APPS_DIR/clevo-control-panel.desktop"
chown "$TARGET_USER:$TARGET_USER" "$APPS_DIR/clevo-control-panel.desktop"
install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" \
  "$SCRIPT_DIR/icons/hicolor/scalable/apps/com.mupdike.ClevoControlPanel.svg" \
  "$ICON_DIR/com.mupdike.ClevoControlPanel.svg"
sudo -u "$TARGET_USER" gtk-update-icon-cache -q -t -f "$USER_HOME/.local/share/icons/hicolor" 2>/dev/null || true

# System-wide autostart entry (applies to any user's login, not just
# $TARGET_USER): starts minimized to the tray, restoring the last-set
# performance mode -- see clevo_control_panel/app.py for why this happens
# here rather than via an early-boot systemd service.
mkdir -p /etc/xdg/autostart
sed "s#__EXEC_PATH__#$SCRIPT_DIR/../bin/clevo-control-panel#" \
  "$SCRIPT_DIR/clevo-control-panel-autostart.desktop.in" \
  > /etc/xdg/autostart/clevo-control-panel.desktop

# 2. Runtime activation: group, udev trigger, systemd enable (shared with the
#    .deb's postinst).
"$SCRIPT_DIR/setup-runtime.sh" "$TARGET_USER"

echo
echo "Setup complete."
if ! id -nG "$TARGET_USER" | grep -qw clevoctl; then
  echo "NOTE: could not confirm group membership; check 'groups $TARGET_USER'."
fi
echo "If $TARGET_USER was just added to the 'clevoctl' group for the first time,"
echo "log out and back in (or reboot) for that membership to take effect."

if [ ! -f /etc/sudoers.d/clevo-control-panel ]; then
  echo
  echo "One more manual step, deliberately not done automatically: to let"
  echo "Quiet/Balanced/Performance mode also apply real CPU/GPU power"
  echo "profile changes (via TLP and, if present, nvidia-smi) without a"
  echo "password prompt every time, run this yourself (as the target user,"
  echo "not root -- it calls sudo itself where needed):"
  echo
  echo "  bash $SCRIPT_DIR/setup-power-profile-sudoers.sh"
  echo
  echo "Without this, those three modes will just control fan behavior,"
  echo "same as before -- CPU/GPU power scaling is skipped."
fi
