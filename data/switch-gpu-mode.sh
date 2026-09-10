#!/bin/sh
# Switches this board's BIOS-level GPU MUX mode (MSHybrid <-> dGPU-only)
# via a direct UEFI NVRAM write. Root-only -- invoked via a narrowly-
# scoped sudoers rule (see clevo-control-panel.sudoers) that only permits
# these two exact invocations, not arbitrary arguments. Not meant to be
# run directly by users.
#
# WARNING: the exact variable GUID/offset/values used here were reverse-
# engineered specifically for this board's Insyde H2O firmware (Clevo/
# Tongfang L550JNP, Panther Lake) by diffing every UEFI Setup-related
# NVRAM variable between a real BIOS-Setup-confirmed MSHybrid boot and a
# real BIOS-Setup-confirmed dGPU boot -- only one byte in one variable
# differed in a way that turned out to be both necessary and sufficient
# (confirmed live: writing only this byte, with a completely untouched
# BIOS otherwise, produced a real, verified mode change on reboot in both
# directions). This is almost certainly NOT portable to any other board
# or firmware vendor without redoing that same diffing investigation --
# see ~/odm-laptop-research/mux-investigation/ for the full writeup and
# raw dumps this was derived from. Do not reuse these constants on
# different hardware.
#
# Deliberately NOT the ACPI _DSM mechanism found earlier
# (\_SB.PC00.GFX0._DSM function 0x15) -- that path has a confirmed live
# side effect (attempting it with a display session active hung the
# system hard enough to require a forced power-off) and never actually
# persisted the requested mode even across a clean reboot. This NVRAM
# write only ever touches firmware configuration storage; it has no live
# hardware effect until the *next* boot reads it, which is what makes it
# safe to call from a running session.
#
# efivarfs marks every variable immutable by default, freshly on every
# boot (not a property of the NVRAM itself) -- confirmed live this causes
# every write to silently fail with EPERM after a reboot until it's
# explicitly cleared again; this script always clears it before writing
# rather than assuming a previous invocation already took care of it.
set -eu

VAR_PATH="/sys/firmware/efi/efivars/Setup-a04a27f4-df00-4d42-b552-39511302113d"
OFFSET=430  # 0x1ae
DGPU_VALUE=2
MSHYBRID_VALUE=3

case "${1:-}" in
    dgpu) TARGET_VALUE=$DGPU_VALUE ;;
    mshybrid) TARGET_VALUE=$MSHYBRID_VALUE ;;
    *)
        echo "Usage: $0 <dgpu|mshybrid>" >&2
        exit 1
        ;;
esac

if [ ! -e "$VAR_PATH" ]; then
    echo "GPU mode NVRAM variable not found on this system -- this script is board-specific, see the warning comment above" >&2
    exit 1
fi

chattr -i "$VAR_PATH" 2>/dev/null || true

python3 - "$VAR_PATH" "$OFFSET" "$TARGET_VALUE" "$DGPU_VALUE" "$MSHYBRID_VALUE" << 'PYEOF'
import subprocess
import sys

path, offset, target, dgpu_val, mshybrid_val = (
    sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
)

result = subprocess.run(["cat", path], capture_output=True)
if result.returncode != 0:
    print(f"Failed to read {path}: {result.stderr.decode()}", file=sys.stderr)
    sys.exit(1)
data = bytearray(result.stdout)

current = data[offset]
if current not in (dgpu_val, mshybrid_val):
    print(
        f"Refusing to proceed: byte at offset {offset} is 0x{current:02x}, "
        f"neither the known dGPU (0x{dgpu_val:02x}) nor MSHybrid (0x{mshybrid_val:02x}) "
        "value -- this system's state doesn't match what this script expects.",
        file=sys.stderr,
    )
    sys.exit(1)

if current == target:
    print(f"Already at the requested mode (byte already 0x{target:02x}) -- nothing to do.")
    sys.exit(0)

data[offset] = target

try:
    with open(path, "wb") as f:
        f.write(bytes(data))
except OSError as e:
    print(f"Write failed: {e}", file=sys.stderr)
    sys.exit(1)

result = subprocess.run(["cat", path], capture_output=True)
readback = bytearray(result.stdout)
if readback[offset] != target:
    print(
        f"Write did not take effect (readback 0x{readback[offset]:02x}, expected 0x{target:02x})",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"Switched to 0x{target:02x} -- reboot required to apply.")
PYEOF
