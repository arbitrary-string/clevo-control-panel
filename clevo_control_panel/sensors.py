"""Live hardware telemetry for the Dashboard page: CPU/GPU temperature and
utilization, and (for the Intel Xe iGPU) the DRM client-stats technique
confirmed by reading nvtop's source (~/odm-laptop-research/nvtop) --
Xe has no single global busy% file the way AMD's amdgpu exposes
gpu_busy_percent, so per-process engine-cycle deltas are read from
/proc/<pid>/fdinfo/<fd> and summed, the same approach nvtop itself uses.

Plain functions with a little module-level state for the delta-based
readings (CPU and Xe utilization both need two consecutive samples) --
fine since there's only ever one Dashboard page polling these in this
single-window app. No classes needed: every read here is a one-shot
snapshot, not stateful hardware control like fan.py/performance.py."""

import subprocess
from pathlib import Path


def read_cpu_temp_c():
    base = Path("/sys/class/hwmon")
    if not base.is_dir():
        return None
    for hwmon in sorted(base.glob("hwmon*")):
        try:
            if (hwmon / "name").read_text().strip() != "coretemp":
                continue
            return int((hwmon / "temp1_input").read_text().strip()) / 1000
        except OSError:
            continue
    return None


def read_cpu_frequency_mhz():
    """Average current frequency across online CPUs, in MHz."""
    freqs = []
    for path in sorted(
        Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq")
    ):
        try:
            freqs.append(int(path.read_text().strip()) / 1000)
        except OSError:
            continue
    return sum(freqs) / len(freqs) if freqs else None


_last_cpu_times = None


def read_cpu_utilization_percent():
    """Overall CPU utilization since the last call, via /proc/stat deltas.
    Returns None on the first call (needs two samples to compute a delta)."""
    global _last_cpu_times
    try:
        with open("/proc/stat") as f:
            fields = [int(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None

    idle = fields[3] + fields[4]  # idle + iowait
    total = sum(fields)

    previous = _last_cpu_times
    _last_cpu_times = (idle, total)
    if previous is None:
        return None

    prev_idle, prev_total = previous
    delta_idle = idle - prev_idle
    delta_total = total - prev_total
    if delta_total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1 - delta_idle / delta_total)))


def read_fan_rpms():
    """RPM for each `acpi_fan` hwmon device found, in device order --
    independent of clevo_acpi entirely (the standard ACPI4 fan interface),
    so this works even if the clevo-acpi module isn't loaded at all."""
    rpms = []
    base = Path("/sys/class/hwmon")
    if not base.is_dir():
        return rpms
    for hwmon in sorted(base.glob("hwmon*")):
        try:
            if (hwmon / "name").read_text().strip() != "acpi_fan":
                continue
            rpms.append(int((hwmon / "fan1_input").read_text().strip()))
        except OSError:
            continue
    return rpms


def read_nvidia_metrics():
    """(temp_c, utilization_percent) for the first NVIDIA GPU, or
    (None, None) if nvidia-smi isn't available or fails."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0:
        return None, None
    try:
        line = result.stdout.strip().splitlines()[0]
        temp_str, util_str = (p.strip() for p in line.split(","))
        return float(temp_str), float(util_str)
    except (ValueError, IndexError):
        return None, None


_xe_pci_address_cache = "unset"


def _xe_pci_address():
    """PCI address (e.g. "0000:00:02.0") of the card driven by the `xe`
    driver, cached after the first successful lookup -- this doesn't
    change while the system is running."""
    global _xe_pci_address_cache
    if _xe_pci_address_cache != "unset":
        return _xe_pci_address_cache

    address = None
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        device = card / "device"
        try:
            if (device / "driver").resolve().name != "xe":
                continue
            address = device.resolve().name
            break
        except OSError:
            continue

    _xe_pci_address_cache = address
    return address


def _iter_drm_fdinfo():
    """Yields the parsed fdinfo dict for every DRM file descriptor
    currently open by any process this one has permission to inspect --
    typically every process owned by the same user, which covers the
    common single-user-desktop case this is meant for."""
    for pid_dir in Path("/proc").glob("[0-9]*"):
        fd_dir = pid_dir / "fd"
        fdinfo_dir = pid_dir / "fdinfo"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if not str(fd.resolve()).startswith("/dev/dri/"):
                    continue
                lines = (fdinfo_dir / fd.name).read_text().splitlines()
            except OSError:
                continue
            info = {}
            for line in lines:
                key, sep, value = line.partition(":")
                if sep:
                    info[key.strip()] = value.strip()
            yield info


_last_xe_cycles = {}


def read_xe_igpu_utilization_percent():
    """Approximate Intel Xe iGPU busy% (render/compute/blitter engine),
    summed across all processes using it, since there's no single global
    counter for Xe the way AMD's gpu_busy_percent provides -- see
    ~/odm-laptop-research/nvtop's extract_gpuinfo_intel_xe.c for the
    technique this mirrors. Returns None if no xe-driven card is found;
    0.0 if one exists but nothing is currently using it; otherwise a
    delta-based percentage requiring two consecutive calls (the first
    call after a new client appears may under-report until the next tick)."""
    pci_address = _xe_pci_address()
    if pci_address is None:
        return None

    global _last_xe_cycles
    current = {}
    total_percent = 0.0
    found_card = False

    for info in _iter_drm_fdinfo():
        if info.get("drm-pdev") != pci_address:
            continue
        found_card = True
        client_id = info.get("drm-client-id")
        cycles = info.get("drm-cycles-rcs")
        total_cycles = info.get("drm-total-cycles-rcs")
        if client_id is None or cycles is None or total_cycles is None:
            continue
        try:
            cycles = int(cycles)
            total_cycles = int(total_cycles)
        except ValueError:
            continue
        current[client_id] = (cycles, total_cycles)

        previous = _last_xe_cycles.get(client_id)
        if previous is not None:
            prev_cycles, prev_total = previous
            delta_cycles = cycles - prev_cycles
            delta_total = total_cycles - prev_total
            if delta_total > 0:
                total_percent += 100.0 * delta_cycles / delta_total

    _last_xe_cycles = current
    if not found_card and not current:
        # Either genuinely idle, or this process can't see any matching
        # fd (e.g. everything using the GPU runs as a different user) --
        # can't distinguish the two from here, so report 0 either way
        # rather than None, which would read as "no iGPU present at all".
        return 0.0
    return min(100.0, total_percent)
