#!/usr/bin/env bash
# Installs the sudoers rule that lets the clevoctl group nudge the
# running clevo-oled-luminance daemon (a system service) into an
# immediate DPCD luminance reassert, without a password prompt. Optional
# -- without this, a display refresh-rate switch in dGPU mode just
# relies on the daemon's own 1-second periodic safety reassert instead
# of an instant one, same as before this feature existed.
#
# Deliberately a separate, manually-run script, same reasoning as this
# project's other sudoers setup scripts: sudoers is sensitive enough to
# want an explicit, reviewable step. Run this yourself when you're
# ready:
#
#   bash data/setup-oled-luminance-nudge-sudoers.sh                      (repo checkout)
#   /usr/lib/clevo-control-panel/setup-oled-luminance-nudge-sudoers.sh   (.deb install)
#
# Safe to re-run. Validates with `visudo -c` before touching the real
# sudoers directory, so a typo here can't break sudo system-wide.
set -euo pipefail

SUDOERS_FILE="$(mktemp)"
trap 'rm -f "$SUDOERS_FILE"' EXIT
DEST=/etc/sudoers.d/clevo-control-panel-oled-luminance-nudge

cat > "$SUDOERS_FILE" <<'EOF'
# Lets the clevoctl group nudge clevo-oled-luminance.service into an
# immediate DPCD reassert (via nudge-oled-luminance.sh) without a
# password prompt, right after a display refresh-rate switch disturbs
# the panel's DPCD luminance-mode state.
#
# Scoped to one exact invocation only -- no wildcard argument matching
# -- so this cannot be used to run any other command as root.
%clevoctl ALL=(root) NOPASSWD: /usr/lib/clevo-control-panel/nudge-oled-luminance.sh
EOF

echo "Validating with visudo -c..."
sudo visudo -cf "$SUDOERS_FILE"

echo "Installing to $DEST..."
sudo install -m 0440 -o root -g root "$SUDOERS_FILE" "$DEST"

echo
echo "Done. Display refresh-rate switches in dGPU mode will now nudge the"
echo "OLED brightness daemon into an instant reassert instead of waiting"
echo "up to 1 second for its own periodic safety reassert."
