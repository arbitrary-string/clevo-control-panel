#!/bin/sh
# Applies CPU (via TLP) and NVIDIA GPU power profile settings for a given
# Clevo Control Panel performance mode. Root-only -- invoked via a
# narrowly-scoped sudoers rule (see clevo-control-panel.sudoers) that only
# permits these three exact invocations, not arbitrary arguments. Not
# meant to be run directly by users.
#
# Intel iGPU (xe driver) frequency control was deliberately left out: no
# stable sysfs interface for it was found, only debugfs, which is not
# something to script against. See
# ~/laptopissues/performance-mode/NOTES.md for the full writeup.
#
# NVIDIA GPU power limit (-pl) is attempted but does nothing on this exact
# laptop -- the vendor/vBIOS locks it ("not supported in current scope")
# on this Max-Q mobile GPU. Left in anyway since it's harmless (silently
# a no-op here) and might work on other RTX-laptop configurations. Clock
# locking (-lgc) is NOT locked down the same way and is the real lever
# used here -- confirmed working, and safe to rely on since this GPU
# doesn't use PCI runtime suspend (power/control=on, not "auto"), so the
# lock won't get silently reset by a runtime power-cycle mid-session.
set -eu

CONF=/etc/tlp.d/90-clevo-control-panel.conf

case "${1:-}" in
  quiet)
    EPP=power
    BOOST=0
    HWP_BOOST=0
    NVIDIA_WATTS=40
    NVIDIA_GPU_CLOCK_MAX=800
    ;;
  balanced)
    EPP=balance_performance
    BOOST=1
    HWP_BOOST=1
    NVIDIA_WATTS=100
    NVIDIA_GPU_CLOCK_MAX=1800
    ;;
  performance)
    EPP=performance
    BOOST=1
    HWP_BOOST=1
    NVIDIA_WATTS=115
    NVIDIA_GPU_CLOCK_MAX=0
    ;;
  *)
    echo "usage: $0 quiet|balanced|performance" >&2
    exit 1
    ;;
esac

# Same value on AC and battery: this profile is a deliberate, explicit
# choice made through the app, not something that should quietly change
# just because the power source did.
cat > "$CONF" <<EOF
# Managed by Clevo Control Panel -- overwritten on every performance mode
# change, don't hand-edit. See ~/laptopissues/performance-mode/NOTES.md.
CPU_SCALING_GOVERNOR_ON_AC=powersave
CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=$EPP
CPU_ENERGY_PERF_POLICY_ON_BAT=$EPP
CPU_BOOST_ON_AC=$BOOST
CPU_BOOST_ON_BAT=$BOOST
CPU_HWP_DYN_BOOST_ON_AC=$HWP_BOOST
CPU_HWP_DYN_BOOST_ON_BAT=$HWP_BOOST
EOF

command -v tlp >/dev/null 2>&1 && tlp start >/dev/null 2>&1 || true

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -pl "$NVIDIA_WATTS" >/dev/null 2>&1 || true

  if [ "$NVIDIA_GPU_CLOCK_MAX" -eq 0 ]; then
    nvidia-smi -rgc >/dev/null 2>&1 || true
  else
    nvidia-smi -lgc "300,$NVIDIA_GPU_CLOCK_MAX" >/dev/null 2>&1 || true
  fi
fi
