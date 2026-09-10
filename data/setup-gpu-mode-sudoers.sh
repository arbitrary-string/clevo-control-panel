#!/usr/bin/env bash
# Installs the sudoers rule that lets the clevoctl group switch this
# board's BIOS-level GPU MUX mode (MSHybrid <-> dGPU-only) without a
# password prompt. Optional -- without this, the GPU Mode section in the
# app still shows your current mode, it just can't switch it (the
# button will fail with a permissions error).
#
# Deliberately a separate, manually-run script rather than something
# install.sh does automatically -- same reasoning as
# setup-power-profile-sudoers.sh: sudoers is sensitive enough to want an
# explicit, reviewable step. Also deliberately its OWN separate opt-in
# rather than folded into that script: you might want one of these two
# features' sudo access without the other. Run this yourself when
# you're ready:
#
#   bash data/setup-gpu-mode-sudoers.sh                      (repo checkout)
#   /usr/lib/clevo-control-panel/setup-gpu-mode-sudoers.sh   (.deb install)
#
# Safe to re-run. Validates with `visudo -c` before touching the real
# sudoers directory, so a typo here can't break sudo system-wide.
set -euo pipefail

SUDOERS_FILE="$(mktemp)"
trap 'rm -f "$SUDOERS_FILE"' EXIT
DEST=/etc/sudoers.d/clevo-control-panel-gpu-mode

cat > "$SUDOERS_FILE" <<'EOF'
# Lets the clevoctl group switch this board's BIOS-level GPU MUX mode
# (see switch-gpu-mode.sh's own comment for what this actually does and
# why it's safe -- a firmware NVRAM write, not a live hardware action)
# without a password prompt.
#
# Scoped to two exact invocations only -- no wildcard argument matching
# -- so this cannot be used to run the script with any other argument,
# and cannot be used to run any other command as root.
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/switch-gpu-mode.sh dgpu
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/switch-gpu-mode.sh mshybrid
EOF

echo "Validating with visudo -c..."
sudo visudo -cf "$SUDOERS_FILE"

echo "Installing to $DEST..."
sudo install -m 0440 -o root -g root "$SUDOERS_FILE" "$DEST"

echo
echo "Done. The GPU Mode section will now be able to switch modes with no"
echo "password prompt (still requires a manual reboot to actually apply)."
