#!/usr/bin/env bash
# Installs the sudoers rule that lets the clevoctl group run
# `prime-select on-demand` without a password prompt. Optional -- without
# this, the GPU Mode section's dGPU-mode switch still works, it just can't
# also fix a prime-select setting of "intel" for you first; if you're in
# that state and use the switch anyway, you may need to manually run
# `sudo prime-select on-demand` (or `nvidia`) yourself after rebooting into
# a black screen, from a TTY or over SSH.
#
# Deliberately a separate, manually-run script, same reasoning as
# setup-gpu-mode-sudoers.sh and setup-power-profile-sudoers.sh: sudoers is
# sensitive enough to want an explicit, reviewable step, and this is its
# own opt-in separate from those two -- you might want GPU MUX switching
# without this safety net, or vice versa. Run this yourself when you're
# ready:
#
#   bash data/setup-prime-select-sudoers.sh                      (repo checkout)
#   /usr/lib/clevo-control-panel/setup-prime-select-sudoers.sh   (.deb install)
#
# Safe to re-run. Validates with `visudo -c` before touching the real
# sudoers directory, so a typo here can't break sudo system-wide.
set -euo pipefail

SUDOERS_FILE="$(mktemp)"
trap 'rm -f "$SUDOERS_FILE"' EXIT
DEST=/etc/sudoers.d/clevo-control-panel-prime-select

cat > "$SUDOERS_FILE" <<'EOF'
# Lets the clevoctl group run `prime-select on-demand` (via
# set-prime-select.sh's exact-argument wrapper) without a password
# prompt, so the GPU Mode section can automatically steer away from an
# "intel"-only driver preference right before switching to dGPU-only
# mode -- otherwise the panel comes up to a black screen after reboot,
# since dGPU mode disconnects the Intel iGPU from the panel entirely.
#
# Scoped to one exact invocation only -- no wildcard argument matching --
# so this cannot be used to run prime-select with any other mode, and
# cannot be used to run any other command as root.
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/set-prime-select.sh on-demand
EOF

echo "Validating with visudo -c..."
sudo visudo -cf "$SUDOERS_FILE"

echo "Installing to $DEST..."
sudo install -m 0440 -o root -g root "$SUDOERS_FILE" "$DEST"

echo
echo "Done. Switching to dGPU mode will now also fix an \"intel\"-only"
echo "prime-select setting automatically, before the reboot dialog appears."
