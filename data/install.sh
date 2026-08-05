#!/usr/bin/env bash
# One-time system setup for a repo checkout of Keyboard Colors (not the .deb,
# which handles this via postinst + data/setup-runtime.sh instead).
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

echo "Setting up Keyboard Colors for user: $TARGET_USER"

# 0. Runtime dependency: PyGObject's cairo integration, needed to draw the
#    color swatches. Ships as a separate package from python3-gi on Debian/Ubuntu.
if command -v apt-get >/dev/null 2>&1 && ! dpkg -s python3-gi-cairo >/dev/null 2>&1; then
  apt-get install -y python3-gi-cairo
fi

# 1. Static files a .deb would normally place via its own file list.
install -m 0644 "$SCRIPT_DIR/99-keyboardcolors.rules" /etc/udev/rules.d/99-keyboardcolors.rules
install -m 0644 "$SCRIPT_DIR/save-keyboard-color.service" /etc/systemd/system/save-keyboard-color.service
install -m 0644 "$SCRIPT_DIR/restore-keyboard-color.service" /etc/systemd/system/restore-keyboard-color.service

# The systemd units above hardcode /usr/bin/keyboardcolors-cli (an absolute
# path, since a .deb install would provide it there). A repo checkout has no
# such file on PATH otherwise, so symlink it in.
ln -sf "$SCRIPT_DIR/../bin/keyboardcolors-cli" /usr/bin/keyboardcolors-cli
ln -sf "$SCRIPT_DIR/../bin/keyboardcolors" /usr/bin/keyboardcolors

USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
APPS_DIR="$USER_HOME/.local/share/applications"
ICON_DIR="$USER_HOME/.local/share/icons/hicolor/scalable/apps"
sudo -u "$TARGET_USER" mkdir -p "$APPS_DIR" "$ICON_DIR"
sed "s#__EXEC_PATH__#$SCRIPT_DIR/../bin/keyboardcolors#" "$SCRIPT_DIR/keyboardcolors.desktop.in" \
  > "$APPS_DIR/keyboardcolors.desktop"
chown "$TARGET_USER:$TARGET_USER" "$APPS_DIR/keyboardcolors.desktop"
install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" \
  "$SCRIPT_DIR/icons/hicolor/scalable/apps/com.mupdike.KeyboardColors.svg" \
  "$ICON_DIR/com.mupdike.KeyboardColors.svg"
sudo -u "$TARGET_USER" gtk-update-icon-cache -q -t -f "$USER_HOME/.local/share/icons/hicolor" 2>/dev/null || true

# 2. Runtime activation: group, udev trigger, systemd enable (shared with the
#    .deb's postinst).
"$SCRIPT_DIR/setup-runtime.sh" "$TARGET_USER"

echo
echo "Setup complete."
if ! id -nG "$TARGET_USER" | grep -qw kbdlight; then
  echo "NOTE: could not confirm group membership; check 'groups $TARGET_USER'."
fi
echo "If $TARGET_USER was just added to the 'kbdlight' group for the first time,"
echo "log out and back in (or reboot) for that membership to take effect."
