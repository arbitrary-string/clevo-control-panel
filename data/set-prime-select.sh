#!/bin/sh
# Narrow root-only wrapper around Ubuntu's own prime-select, invoked via a
# sudoers rule that only permits this exact invocation (see
# setup-prime-select-sudoers.sh). Not meant to be run directly by users.
#
# Only "on-demand" is accepted, deliberately -- the app only ever needs to
# switch automatically to steer away from "intel" right before a GPU MUX
# mode switch reboots into dGPU-only mode (see prime_select.py's module
# docstring for why "intel" specifically is the dangerous state: the panel
# is no longer wired to the Intel iGPU at all in dGPU mode, so a session
# still bound to Intel-only comes up to a black screen). It never needs to
# switch to "intel" or "nvidia" on its own, so those aren't exposed here.
#
# prime-select's own on-demand switch runs update-initramfs and
# update-grub internally, so this can legitimately take tens of seconds --
# that's on the caller (prime_select.py) to run off the GTK main thread,
# not something this script does anything special for.
set -eu

MODE="${1:-}"
if [ "$MODE" != "on-demand" ]; then
    echo "set-prime-select.sh: only 'on-demand' is supported" >&2
    exit 1
fi

exec /usr/bin/prime-select on-demand
