#!/usr/bin/env bash
# Installs the sudoers rule that lets the clevoctl group apply CPU/GPU
# power profile settings (TLP + nvidia-smi) without a password prompt,
# for Clevo Control Panel's Quiet/Balanced/Performance modes. Optional --
# without this, those three modes still work, they just skip the
# CPU/GPU power scaling and only control fan behavior.
#
# Deliberately a separate, manually-run script rather than something
# install.sh does automatically: sudoers is sensitive enough to want an
# explicit, reviewable step. Run this yourself when you're ready:
#
#   bash data/setup-power-profile-sudoers.sh          (repo checkout)
#   /usr/lib/clevo-control-panel/setup-power-profile-sudoers.sh  (.deb install)
#
# Safe to re-run. Validates with `visudo -c` before touching the real
# sudoers directory, so a typo here can't break sudo system-wide.
set -euo pipefail

# A temp file, not one written next to this script: when installed by
# the .deb, this script lives under /usr/lib/clevo-control-panel, owned
# by root and not writable by the ordinary user who's meant to run this.
SUDOERS_FILE="$(mktemp)"
trap 'rm -f "$SUDOERS_FILE"' EXIT
DEST=/etc/sudoers.d/clevo-control-panel

cat > "$SUDOERS_FILE" <<'EOF'
# Lets the clevoctl group apply CPU/GPU power profile settings (TLP +
# nvidia-smi) without a password prompt, for Clevo Control Panel's
# performance mode switcher (tray menu, app, and CLI all need this to be
# instant, not gated on a polkit/password prompt every time).
#
# Scoped to three exact invocations only -- no wildcard argument matching
# -- so this cannot be used to run the script with any other argument,
# and cannot be used to run any other command as root.
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/apply-power-profile.sh quiet
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/apply-power-profile.sh balanced
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/apply-power-profile.sh performance
EOF

echo "Validating with visudo -c..."
sudo visudo -cf "$SUDOERS_FILE"

echo "Installing to $DEST..."
sudo install -m 0440 -o root -g root "$SUDOERS_FILE" "$DEST"

echo
echo "Done. Quiet/Balanced/Performance will now also apply real CPU/GPU"
echo "power profile changes (TLP + nvidia-smi if present) with no password"
echo "prompt."
