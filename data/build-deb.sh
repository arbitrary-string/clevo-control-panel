#!/usr/bin/env bash
# Builds a .deb for Clevo Control Panel. Produces
# dist/clevo-control-panel_<ver>_all.deb but does NOT install it — that's a
# separate, deliberate step (sudo apt install ./dist/clevo-control-panel_*.deb).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from clevo_control_panel import __version__; print(__version__)")"
PKG_NAME=clevo-control-panel
ARCH=all

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
PKG_ROOT="$BUILD_DIR/${PKG_NAME}_${VERSION}_${ARCH}"

mkdir -p "$PKG_ROOT"/DEBIAN
mkdir -p "$PKG_ROOT"/usr/bin
mkdir -p "$PKG_ROOT"/usr/lib/clevo-control-panel
mkdir -p "$PKG_ROOT"/usr/share/applications
mkdir -p "$PKG_ROOT"/usr/share/icons/hicolor/scalable/apps
mkdir -p "$PKG_ROOT"/usr/share/doc/clevo-control-panel
mkdir -p "$PKG_ROOT"/usr/lib/systemd/system
mkdir -p "$PKG_ROOT"/etc/udev/rules.d
mkdir -p "$PKG_ROOT"/etc/xdg/autostart

# Python package.
cp -r "$REPO_ROOT/clevo_control_panel" "$PKG_ROOT/usr/lib/clevo-control-panel/clevo_control_panel"
find "$PKG_ROOT/usr/lib/clevo-control-panel/clevo_control_panel" -name '__pycache__' -exec rm -rf {} +

# Shared runtime setup script: used by postinst, and by the in-app
# "Run System Setup" button if it ever needs to be re-run.
install -m 0755 "$REPO_ROOT/data/setup-runtime.sh" \
  "$PKG_ROOT/usr/lib/clevo-control-panel/setup-runtime.sh"

# Launchers.
cat > "$PKG_ROOT/usr/bin/clevo-control-panel" <<'LAUNCHER'
#!/usr/bin/env python3
import sys

sys.path.insert(0, "/usr/lib/clevo-control-panel")

from clevo_control_panel.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
LAUNCHER
chmod 0755 "$PKG_ROOT/usr/bin/clevo-control-panel"

cat > "$PKG_ROOT/usr/bin/clevo-control-panel-cli" <<'LAUNCHER'
#!/usr/bin/env python3
import sys

sys.path.insert(0, "/usr/lib/clevo-control-panel")

from clevo_control_panel.cli import main

if __name__ == "__main__":
    sys.exit(main())
LAUNCHER
chmod 0755 "$PKG_ROOT/usr/bin/clevo-control-panel-cli"

# Desktop entry + autostart entry + icon.
sed 's#__EXEC_PATH__#/usr/bin/clevo-control-panel#' "$REPO_ROOT/data/clevo-control-panel.desktop.in" \
  > "$PKG_ROOT/usr/share/applications/clevo-control-panel.desktop"
sed 's#__EXEC_PATH__#/usr/bin/clevo-control-panel#' \
  "$REPO_ROOT/data/clevo-control-panel-autostart.desktop.in" \
  > "$PKG_ROOT/etc/xdg/autostart/clevo-control-panel.desktop"
install -m 0644 \
  "$REPO_ROOT/data/icons/hicolor/scalable/apps/com.mupdike.ClevoControlPanel.svg" \
  "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/com.mupdike.ClevoControlPanel.svg"

# Systemd units + udev rule (installed under package-managed paths, not
# /etc/systemd/system, which is reserved for local admin-created units).
# Only keyboard color/brightness persist this way -- performance mode
# persistence is handled by the app itself, see clevo_control_panel/app.py.
install -m 0644 "$REPO_ROOT/data/save-keyboard-color.service" \
  "$PKG_ROOT/usr/lib/systemd/system/save-keyboard-color.service"
install -m 0644 "$REPO_ROOT/data/restore-keyboard-color.service" \
  "$PKG_ROOT/usr/lib/systemd/system/restore-keyboard-color.service"
install -m 0644 "$REPO_ROOT/data/99-clevo-control-panel.rules" \
  "$PKG_ROOT/etc/udev/rules.d/99-clevo-control-panel.rules"

cp "$REPO_ROOT/README.md" "$PKG_ROOT/usr/share/doc/clevo-control-panel/README.md"
cat > "$PKG_ROOT/usr/share/doc/clevo-control-panel/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: clevo-control-panel
Source: https://github.com/arbitrary-string/clevo-control-panel

Files: *
Copyright: 2026 Michael Updike
License: GPL-3.0-or-later

License: GPL-3.0-or-later
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 GNU General Public License for more details.
 .
 On Debian/Ubuntu systems, the complete text of the GNU General Public
 License version 3 can be found in "/usr/share/common-licenses/GPL-3".
COPYRIGHT

cat > "$PKG_ROOT/DEBIAN/control" <<CONTROL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, adwaita-icon-theme, udev, systemd
Recommends: gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1
Maintainer: Michael Updike <arbitrarystring@gmail.com>
Description: Control keyboard RGB backlight, battery, and performance mode
 GTK4/libadwaita app and CLI for hardware features on System76 laptops and
 generic Clevo/Tongfang barebones: keyboard backlight color and brightness,
 and (on supporting clevo-acpi driver builds) battery charge threshold and
 performance/fan mode control, all persisting across reboots. Supports
 System76 laptops (system76_acpi driver) and generic Clevo/Tongfang
 barebones (clevo-acpi driver; see the clevo-acpi-dkms package for boards
 that need it enabled first).
CONTROL

echo "/etc/udev/rules.d/99-clevo-control-panel.rules" > "$PKG_ROOT/DEBIAN/conffiles"

cat > "$PKG_ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
case "$1" in
  configure)
    /usr/lib/clevo-control-panel/setup-runtime.sh || true
    ;;
esac
exit 0
POSTINST
chmod 0755 "$PKG_ROOT/DEBIAN/postinst"

cat > "$PKG_ROOT/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
case "$1" in
  remove|purge)
    systemctl disable --now save-keyboard-color.service restore-keyboard-color.service 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    ;;
esac
if [ "$1" = "purge" ]; then
  rm -rf /var/lib/clevo-control-panel
fi
exit 0
POSTRM
chmod 0755 "$PKG_ROOT/DEBIAN/postrm"

OUT_DIR="$REPO_ROOT/dist"
mkdir -p "$OUT_DIR"
OUT_DEB="$OUT_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"

dpkg-deb --root-owner-group --build "$PKG_ROOT" "$OUT_DEB"
echo
echo "Built: $OUT_DEB"
