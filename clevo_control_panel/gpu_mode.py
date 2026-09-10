"""BIOS-level GPU MUX mode (MSHybrid vs dGPU-only) detection and
switching, board-specific to this Clevo/Tongfang L550JNP (Panther Lake)
Insyde H2O firmware.

## What this is, and why it works the way it does

Switching which GPU physically drives the panel isn't something the OS
can do live -- it's decided by firmware at boot, before any display
driver initializes. The only thing this module (or anything at OS
runtime) can do is write a *pending request* to firmware NVRAM and ask
for a reboot; the actual mux reconfiguration happens during the next
POST, before this or any other userspace code runs again.

The specific mechanism was found by diffing every UEFI Setup-related
NVRAM variable between a real BIOS-Setup-confirmed MSHybrid boot and a
real BIOS-Setup-confirmed dGPU boot (see
~/odm-laptop-research/mux-investigation/ for the full writeup and raw
dumps): exactly one byte, in one variable, differed in a way that turned
out to be both necessary and sufficient -- confirmed live, writing only
that byte with everything else in BIOS untouched produced a real,
verified mode change on reboot, in both directions.

This is deliberately NOT the ACPI `_DSM` mechanism found earlier
(`\\_SB.PC00.GFX0._DSM` function 0x15, using the same EC command
dispatcher clevo-acpi.c's DCHU functions use). That path has a confirmed
live side effect: attempting it with an active display session hung the
system hard enough to require a forced power-off, and even a clean
reboot afterward never actually persisted the requested mode. The NVRAM
write used here only touches firmware configuration storage -- it has
no live hardware effect until the *next* boot reads it, which is what
makes it safe to call from a running session in the first place.

## Portability

The exact GUID/offset/values here are almost certainly specific to this
board's firmware and are not expected to work, or to be safe to try,
on different hardware -- see switch-gpu-mode.sh's own comment for the
same warning at the point where it actually matters (the privileged
script that performs the write).
"""

import subprocess

SWITCH_SCRIPT = "/usr/lib/clevo-control-panel/switch-gpu-mode.sh"

INTEL_VENDOR_ID = "8086"


class GpuModeError(RuntimeError):
    pass


def detect_current_mode():
    """Returns "mshybrid", "dgpu", or None (undetermined) -- purely from
    what's actually enumerated on the PCI bus right now, no privileged
    access needed. In MSHybrid mode the Intel iGPU appears as a VGA
    display controller alongside the NVIDIA GPU; in dGPU mode it doesn't
    appear as a display controller at all (confirmed live on this board)."""
    try:
        result = subprocess.run(
            ["lspci", "-nnmm", "-d", f"{INTEL_VENDOR_ID}:"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    # lspci -nnmm's quoted class field includes the numeric class ID
    # suffix (e.g. "VGA compatible controller [0300]") -- confirmed live
    # this broke an exact-string comparison here, always returning False
    # regardless of actual state. startswith() instead.
    intel_is_display_controller = any(
        line.split('"')[1].startswith("VGA compatible controller")
        for line in result.stdout.splitlines()
        if line.strip()
    )

    nvidia_present = subprocess.run(
        ["lspci", "-d", "10de:"], capture_output=True, timeout=5, check=False
    ).stdout.strip()
    if not nvidia_present:
        return None  # no NVIDIA GPU at all -- this feature doesn't apply here

    return "mshybrid" if intel_is_display_controller else "dgpu"


def switch_to(mode):
    """Requests a switch to "dgpu" or "mshybrid", taking effect on next
    reboot -- never live. Raises GpuModeError with the script's own
    stderr on failure (including the deliberately-refused case of the
    NVRAM byte not matching either known value, or the variable not
    existing on this board at all)."""
    if mode not in ("dgpu", "mshybrid"):
        raise GpuModeError(f"invalid mode: {mode!r}")

    try:
        result = subprocess.run(
            ["sudo", "-n", SWITCH_SCRIPT, mode],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GpuModeError(f"could not run {SWITCH_SCRIPT}: {e}") from e

    if result.returncode != 0:
        raise GpuModeError(result.stderr.strip() or "switch-gpu-mode.sh failed")

    return result.stdout.strip()
