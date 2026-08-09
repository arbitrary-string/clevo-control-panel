#!/bin/sh
# Applies CPU (via TLP) and NVIDIA GPU power profile settings for a given
# Clevo Control Panel performance mode. Root-only -- invoked via a
# narrowly-scoped sudoers rule (see clevo-control-panel.sudoers) that only
# permits these three exact invocations, not arbitrary arguments. Not
# meant to be run directly by users.
#
# Intel iGPU (xe driver) frequency control: found under
# /sys/class/drm/cardN/device/tile0/gt0/freq0/{min,max}_freq -- missed on
# first investigation because it wasn't reachable via a naive `find`
# through the card's "device" symlink; it's there via the direct PCI
# device path. Only gt0 (rcs/ccs/bcs -- render/compute/blitter) is capped;
# gt1 (vcs/vecs -- video decode/encode) is deliberately left alone, since
# capping media engines wasn't asked for and could hurt video playback for
# no real benefit. Confirmed holding under real sustained load (vsync-
# disabled glxgears): capped at exactly the requested ceiling; uncapped,
# the same load reached 1500-1700MHz actual against a 2450MHz max. See
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
    # Disabling turbo alone only caps the CPU at its *base* clock (3700MHz
    # on this CPU) -- nowhere near quiet. A real ceiling needs an explicit
    # max frequency; confirmed holding under real sustained load.
    MAX_FREQ_KHZ=1500000
    INTEL_GT0_MAX_FREQ=800
    NVIDIA_WATTS=40
    NVIDIA_GPU_CLOCK_MAX=800
    ;;
  balanced)
    EPP=balance_performance
    BOOST=1
    HWP_BOOST=1
    MAX_FREQ_KHZ=0
    INTEL_GT0_MAX_FREQ=1500
    NVIDIA_WATTS=100
    NVIDIA_GPU_CLOCK_MAX=1800
    ;;
  performance)
    EPP=performance
    BOOST=1
    HWP_BOOST=1
    MAX_FREQ_KHZ=0
    INTEL_GT0_MAX_FREQ=0
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

# Hard frequency ceiling, applied directly rather than through TLP's own
# CPU_SCALING_MAX_FREQ_ON_AC/BAT: confirmed by testing that TLP treats "0"
# (its documented "no limit" sentinel) as "leave the current value alone,"
# not "reset to hardware max" -- so switching quiet -> balanced/performance
# would leave the CPU stuck at quiet's cap otherwise. This is also a
# hybrid P-core/E-core CPU (confirmed: per-core cpuinfo_max_freq genuinely
# differs, e.g. 4700000 vs 3700000 vs 3300000 kHz on this hardware), so
# "uncapped" means each core's own max, not one hardcoded value for all.
for d in /sys/devices/system/cpu/cpu*/cpufreq; do
  if [ "$MAX_FREQ_KHZ" -eq 0 ]; then
    target="$(cat "$d/cpuinfo_max_freq" 2>/dev/null)" || continue
  else
    target="$MAX_FREQ_KHZ"
  fi
  echo "$target" > "$d/scaling_max_freq" 2>/dev/null || true
done

# Intel iGPU (xe driver) gt0 (render/compute) frequency ceiling. Path
# resolved dynamically (not hardcoded to this machine's PCI address) by
# finding whichever /sys/class/drm/cardN/device is driven by "xe".
INTEL_GT0_FREQ_DIR=""
for drv_link in /sys/class/drm/card*/device/driver; do
  [ -e "$drv_link" ] || continue
  if [ "$(basename "$(readlink -f "$drv_link")")" = "xe" ]; then
    cand="$(dirname "$drv_link")/tile0/gt0/freq0"
    if [ -d "$cand" ]; then
      INTEL_GT0_FREQ_DIR="$cand"
      break
    fi
  fi
done

if [ -n "$INTEL_GT0_FREQ_DIR" ]; then
  if [ "$INTEL_GT0_MAX_FREQ" -eq 0 ]; then
    target="$(cat "$INTEL_GT0_FREQ_DIR/rp0_freq" 2>/dev/null)" || target=""
  else
    target="$INTEL_GT0_MAX_FREQ"
  fi
  if [ -n "$target" ]; then
    echo "$target" > "$INTEL_GT0_FREQ_DIR/max_freq" 2>/dev/null || true
  fi
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -pl "$NVIDIA_WATTS" >/dev/null 2>&1 || true

  if [ "$NVIDIA_GPU_CLOCK_MAX" -eq 0 ]; then
    nvidia-smi -rgc >/dev/null 2>&1 || true
  else
    nvidia-smi -lgc "300,$NVIDIA_GPU_CLOCK_MAX" >/dev/null 2>&1 || true
  fi
fi
