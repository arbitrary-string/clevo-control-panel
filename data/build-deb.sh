#!/usr/bin/env bash
# Builds a .deb for Keyboard Colors. Produces dist/keyboardcolors_<ver>_all.deb
# but does NOT install it — that's a separate, deliberate step
# (sudo apt install ./dist/keyboardcolors_*.deb).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from keyboardcolors import __version__; print(__version__)")"
PKG_NAME=keyboardcolors
ARCH=all

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
PKG_ROOT="$BUILD_DIR/${PKG_NAME}_${VERSION}_${ARCH}"

mkdir -p "$PKG_ROOT"/DEBIAN
mkdir -p "$PKG_ROOT"/usr/bin
mkdir -p "$PKG_ROOT"/usr/lib/keyboardcolors
mkdir -p "$PKG_ROOT"/usr/share/applications
mkdir -p "$PKG_ROOT"/usr/share/icons/hicolor/scalable/apps
mkdir -p "$PKG_ROOT"/usr/share/doc/keyboardcolors
mkdir -p "$PKG_ROOT"/usr/lib/systemd/system
mkdir -p "$PKG_ROOT"/etc/udev/rules.d

# Python package.
cp -r "$REPO_ROOT/keyboardcolors" "$PKG_ROOT/usr/lib/keyboardcolors/keyboardcolors"
find "$PKG_ROOT/usr/lib/keyboardcolors/keyboardcolors" -name '__pycache__' -exec rm -rf {} +

# Shared runtime setup script: used by postinst, and by the in-app
# "Run System Setup" button if it ever needs to be re-run.
install -m 0755 "$REPO_ROOT/data/setup-runtime.sh" \
  "$PKG_ROOT/usr/lib/keyboardcolors/setup-runtime.sh"

# Launcher.
cat > "$PKG_ROOT/usr/bin/keyboardcolors" <<'LAUNCHER'
#!/usr/bin/env python3
import sys

sys.path.insert(0, "/usr/lib/keyboardcolors")

from keyboardcolors.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
LAUNCHER
chmod 0755 "$PKG_ROOT/usr/bin/keyboardcolors"

# Desktop entry + icon.
sed 's#__EXEC_PATH__#/usr/bin/keyboardcolors#' "$REPO_ROOT/data/keyboardcolors.desktop.in" \
  > "$PKG_ROOT/usr/share/applications/keyboardcolors.desktop"
install -m 0644 \
  "$REPO_ROOT/data/icons/hicolor/scalable/apps/com.mupdike.KeyboardColors.svg" \
  "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/com.mupdike.KeyboardColors.svg"

# Systemd units + udev rule (installed under package-managed paths, not
# /etc/systemd/system, which is reserved for local admin-created units).
install -m 0644 "$REPO_ROOT/data/save-keyboard-color.service" \
  "$PKG_ROOT/usr/lib/systemd/system/save-keyboard-color.service"
install -m 0644 "$REPO_ROOT/data/restore-keyboard-color.service" \
  "$PKG_ROOT/usr/lib/systemd/system/restore-keyboard-color.service"
install -m 0644 "$REPO_ROOT/data/99-keyboardcolors.rules" \
  "$PKG_ROOT/etc/udev/rules.d/99-keyboardcolors.rules"

cp "$REPO_ROOT/README.md" "$PKG_ROOT/usr/share/doc/keyboardcolors/README.md"
cat > "$PKG_ROOT/usr/share/doc/keyboardcolors/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: keyboardcolors
Source: https://github.com/arbitrary-string/keyboardcolors

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
Maintainer: Michael Updike <arbitrarystring@gmail.com>
Description: Control the System76 keyboard RGB backlight
 GTK4/libadwaita app to set the RGB keyboard backlight color and
 brightness on System76 laptops (system76_acpi driver), with settings
 that persist across reboots.
CONTROL

echo "/etc/udev/rules.d/99-keyboardcolors.rules" > "$PKG_ROOT/DEBIAN/conffiles"

cat > "$PKG_ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
case "$1" in
  configure)
    /usr/lib/keyboardcolors/setup-runtime.sh || true
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
  rm -rf /var/lib/keyboardcolors
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
