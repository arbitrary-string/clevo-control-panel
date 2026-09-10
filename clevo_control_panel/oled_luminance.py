"""Full-range OLED brightness control via the NVIDIA proprietary driver's
private RM (Resource Manager) API, direct-to-hardware DPCD AUX-channel
writes -- dGPU-mode only.

## Why this exists

On this laptop's OLED panel, in dGPU mode (the panel muxed directly to
the NVIDIA GPU, no Intel iGPU involved), the standard
`/sys/class/backlight/nvidia_0/brightness` interface only ever drives
the panel's legacy 8-bit eDP brightness register (DPCD 0x722/0x723) --
confirmed live by watching that exact register track the GNOME slider
byte-for-byte. That register's own maximum (255/255, i.e. sysfs "100")
tops out well below this panel's real capability: this panel's EDID
advertises a genuine 507.5 cd/m^2 full-coverage maximum, but the legacy
path never gets anywhere near that.

The panel also advertises PANEL_LUMINANCE_CONTROL_CAP (DPCD 0x703 bit 4)
-- a second, higher-range absolute-luminance control mode, addressed via
DPCD 0x734 (TARGET_LUMINANCE, in milli-cd/m^2). Writing that register
directly does *nothing* on its own -- confirmed live, the value reads
back correctly but the panel visibly doesn't react -- because the mode
itself is disabled: DPCD 0x721 bit 7 (the "luminance control enable"
flag) reads 0 by default. Setting that single bit, with the same
TARGET_LUMINANCE value already sitting in 0x734, immediately unlocked
real full-range brightness on this exact panel, live-verified.

## Why NVIDIA's own RM API, not standard kernel DRM/backlight APIs

There's no public kernel interface for this: the proprietary nvidia-drm
driver doesn't expose an AUX/DPCD debugfs path the way the open-source
i915/xe/amdgpu drivers do (confirmed -- checked this system's
`/sys/kernel/debug/dri/<gpu>/eDP-1/` and there's nothing there but
`edid_override`/`force`/`vrr_range`/`output_bpc`). The only way to reach
raw DPCD registers under the nvidia driver at all is through its own
private `/dev/nvidiactl`+`/dev/nvidia0` RM ioctl interface -- the same
one NVIDIA's own X11/Wayland driver stack uses internally. This module
independently reimplements the small slice of that protocol needed here
(client/device/display object allocation, then `CMD_DP_AUXCH_CTRL` for
raw AUX reads/writes) -- these are NVIDIA's own fixed kernel-ABI
constants, not copyrightable expression, but the code here is an
original implementation, not copied from any other project.

## Scope and safety

This only ever touches a single, standard, VESA-documented DPCD register
range designed to be written live by any driver -- fundamentally
different in risk from this project's EC-RAM-based mechanisms (see
`ec-modern-standby-fix`) or the GPU MUX switch investigation (which
turned out to have real, undocumented live side effects and was
abandoned). Reads and writes here can't hang the display or need a
reboot to recover from; worst case is "no visible effect."

This module is entirely inert on any board where dGPU-mode NVIDIA isn't
driving the panel directly (i.e. MSHybrid mode, or any board without an
NVIDIA GPU at all) -- `NvidiaDpcdBacklight()` simply raises
`NvidiaDpcdBacklightError` if `/dev/nvidia0` doesn't exist or no eDP
display advertising DPCD backlight is found, which the caller is
expected to treat as "not applicable here," not a real error.
"""

import ctypes
import fcntl
import os

NV01_ROOT_CLIENT = 0x41
NV01_DEVICE_0 = 0x80
NV04_DISPLAY_COMMON = 0x73

CMD_GET_SUPPORTED = 0x730107
CMD_GET_CONNECT_STATE = 0x730108
CMD_DP_AUXCH_CTRL = 0x731341

DPCD_EDP_CAPS = 0x700
DPCD_LUMINANCE_CAP_BYTE_OFFSET = 3  # cap[3] == DPCD 0x703; bit 4 == PANEL_LUMINANCE_CONTROL_CAP
DPCD_LUMINANCE_CAP_BIT = 0x10
DPCD_BACKLIGHT_MODE_SET = 0x721
DPCD_LUMINANCE_ENABLE_BIT = 0x80  # bit 7
DPCD_TARGET_LUMINANCE = 0x734

AUX_CMD_READ = 0x09
AUX_CMD_WRITE = 0x08


class NVOS21(ctypes.Structure):
    _fields_ = [
        ("hRoot", ctypes.c_uint32),
        ("hObjectParent", ctypes.c_uint32),
        ("hObjectNew", ctypes.c_uint32),
        ("hClass", ctypes.c_int32),
        ("pAllocParms", ctypes.c_uint64),
        ("paramsSize", ctypes.c_uint32),
        ("status", ctypes.c_int32),
    ]


class NVOS54(ctypes.Structure):
    _fields_ = [
        ("hClient", ctypes.c_uint32),
        ("hObject", ctypes.c_uint32),
        ("cmd", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("params", ctypes.c_uint64),
        ("paramsSize", ctypes.c_uint32),
        ("status", ctypes.c_int32),
    ]


class NV0080AllocParams(ctypes.Structure):
    _fields_ = [
        ("deviceId", ctypes.c_uint32),
        ("hClientShare", ctypes.c_uint32),
        ("hTargetClient", ctypes.c_uint32),
        ("hTargetDevice", ctypes.c_uint32),
        ("flags", ctypes.c_int32),
        ("_pad0", ctypes.c_uint32),
        ("vaSpaceSize", ctypes.c_uint64),
        ("vaStartInternal", ctypes.c_uint64),
        ("vaLimitInternal", ctypes.c_uint64),
        ("vaMode", ctypes.c_int32),
        ("_pad1", ctypes.c_uint32),
    ]


class GetSupportedParams(ctypes.Structure):
    _fields_ = [
        ("subDeviceInstance", ctypes.c_uint32),
        ("displayMask", ctypes.c_uint32),
        ("displayMaskDDC", ctypes.c_uint32),
    ]


class GetConnectStateParams(ctypes.Structure):
    _fields_ = [
        ("subDeviceInstance", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("displayMask", ctypes.c_uint32),
        ("retryTimeMs", ctypes.c_uint32),
    ]


class AuxChCtrlParams(ctypes.Structure):
    _fields_ = [
        ("subDeviceInstance", ctypes.c_uint32),
        ("displayId", ctypes.c_uint32),
        ("bAddrOnly", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 3),
        ("cmd", ctypes.c_uint32),
        ("addr", ctypes.c_uint32),
        ("data", ctypes.c_uint8 * 16),
        ("size", ctypes.c_uint32),
        ("replyType", ctypes.c_uint32),
        ("retryTimeMs", ctypes.c_uint32),
    ]


class RegisterFdParams(ctypes.Structure):
    _fields_ = [("ctl_fd", ctypes.c_int)]


def _iowr(type_char, nr, ctype):
    # Standard Linux ioctl request-number encoding (asm-generic/ioctl.h):
    # dir(2 bits) | size(14 bits) | type(8 bits) | nr(8 bits), dir=READ|WRITE=3.
    size = ctypes.sizeof(ctype)
    return (3 << 30) | (size << 16) | (ord(type_char) << 8) | nr


_IOCTL_RM_ALLOC = _iowr("F", 0x2B, NVOS21)
_IOCTL_RM_CONTROL = _iowr("F", 0x2A, NVOS54)
_IOCTL_REGISTER_FD = _iowr("F", 201, RegisterFdParams)


class NvidiaDpcdBacklightError(RuntimeError):
    pass


class NvidiaDpcdBacklight:
    """One open RM session against the NVIDIA driver, scoped to reading/
    writing DPCD registers on whichever display it finds advertising eDP
    backlight support. Raises NvidiaDpcdBacklightError from __init__ if
    this machine isn't currently in a state where any of this applies --
    that's the expected, normal outcome in MSHybrid mode."""

    def __init__(self):
        self._ctl_fd = None
        self._dev_fd = None

        # Handle values derived from this process's own PID rather than
        # fixed constants -- confirmed live, repeatedly, that two
        # processes (or even two successive short-lived processes, if
        # the first's cleanup hasn't fully completed yet) contending for
        # the identical hardcoded handle value causes RM alloc/control
        # calls to fail outright. PID-derived values can't collide with
        # anything else using this same scheme, regardless of timing.
        pid_part = os.getpid() & 0xFFFF
        self._client_handle = 0xBB000000 | pid_part
        self._device_handle = 0xBC000000 | pid_part
        self._display_handle = 0xBD000000 | pid_part

        try:
            self._ctl_fd = os.open("/dev/nvidiactl", os.O_RDWR)
            self._dev_fd = os.open("/dev/nvidia0", os.O_RDWR)
        except OSError as e:
            self.close()
            raise NvidiaDpcdBacklightError(
                f"nvidia device nodes not available ({e}) -- not in dGPU mode?"
            ) from e

        try:
            reg = RegisterFdParams(ctl_fd=self._ctl_fd)
            fcntl.ioctl(self._dev_fd, _IOCTL_REGISTER_FD, reg, True)

            self._rm_alloc(
                self._client_handle, self._client_handle, self._client_handle, NV01_ROOT_CLIENT
            )
            device_params = NV0080AllocParams()
            self._rm_alloc(
                self._client_handle,
                self._client_handle,
                self._device_handle,
                NV01_DEVICE_0,
                device_params,
            )
            self._rm_alloc(
                self._client_handle,
                self._device_handle,
                self._display_handle,
                NV04_DISPLAY_COMMON,
            )
        except NvidiaDpcdBacklightError:
            self.close()
            raise

        self.edp_display_id = self._find_edp()
        if self.edp_display_id is None:
            self.close()
            raise NvidiaDpcdBacklightError(
                "no eDP display advertising DPCD backlight found via the nvidia driver"
            )

    def close(self):
        for fd in (self._ctl_fd, self._dev_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._ctl_fd = None
        self._dev_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def _rm_alloc(self, h_root, h_object_parent, h_object_new, h_class, params=None):
        x = NVOS21(
            hRoot=h_root,
            hObjectParent=h_object_parent,
            hObjectNew=h_object_new,
            hClass=h_class,
            pAllocParms=ctypes.addressof(params) if params is not None else 0,
            paramsSize=ctypes.sizeof(params) if params is not None else 0,
            status=0,
        )
        try:
            fcntl.ioctl(self._ctl_fd, _IOCTL_RM_ALLOC, x, True)
        except OSError as e:
            raise NvidiaDpcdBacklightError(f"RM alloc ioctl failed: {e}") from e
        if x.status != 0:
            raise NvidiaDpcdBacklightError(
                f"RM alloc of class 0x{h_class:x} failed: status=0x{x.status:x}"
            )

    def _rm_control(self, h_object, cmd, params):
        x = NVOS54(
            hClient=self._client_handle,
            hObject=h_object,
            cmd=cmd,
            flags=0,
            params=ctypes.addressof(params),
            paramsSize=ctypes.sizeof(params),
            status=0,
        )
        try:
            fcntl.ioctl(self._ctl_fd, _IOCTL_RM_CONTROL, x, True)
        except OSError as e:
            raise NvidiaDpcdBacklightError(f"RM control ioctl failed: {e}") from e
        if x.status != 0:
            raise NvidiaDpcdBacklightError(
                f"RM control 0x{cmd:x} failed: status=0x{x.status:x}"
            )

    def _dpcd_read(self, display_id, addr, size):
        a = AuxChCtrlParams(displayId=display_id, cmd=AUX_CMD_READ, addr=addr, size=min(size, 16))
        self._rm_control(self._display_handle, CMD_DP_AUXCH_CTRL, a)
        return bytes(a.data[: a.size])

    def _dpcd_write(self, display_id, addr, data):
        a = AuxChCtrlParams(displayId=display_id, cmd=AUX_CMD_WRITE, addr=addr, size=len(data))
        for i, b in enumerate(data):
            a.data[i] = b
        self._rm_control(self._display_handle, CMD_DP_AUXCH_CTRL, a)

    def _find_edp(self):
        supported = GetSupportedParams()
        self._rm_control(self._display_handle, CMD_GET_SUPPORTED, supported)

        connect = GetConnectStateParams(displayMask=supported.displayMask, flags=1)
        self._rm_control(self._display_handle, CMD_GET_CONNECT_STATE, connect)

        for bit in range(32):
            display_id = 1 << bit
            if not (connect.displayMask & display_id):
                continue
            try:
                caps = self._dpcd_read(display_id, DPCD_EDP_CAPS, 4)
            except NvidiaDpcdBacklightError:
                continue
            if caps[2] & 0x02:
                return display_id
        return None

    def has_luminance_control_cap(self):
        caps = self._dpcd_read(self.edp_display_id, DPCD_EDP_CAPS, 4)
        return bool(caps[DPCD_LUMINANCE_CAP_BYTE_OFFSET] & DPCD_LUMINANCE_CAP_BIT)

    def is_luminance_mode_enabled(self):
        mode = self._dpcd_read(self.edp_display_id, DPCD_BACKLIGHT_MODE_SET, 1)
        return bool(mode[0] & DPCD_LUMINANCE_ENABLE_BIT)

    def enable_luminance_mode(self):
        """Idempotent: only writes if the bit isn't already set, so this
        is safe to call on every daemon tick without spamming AUX writes.

        Confirmed live: this specific AUX write can report a non-zero RM
        status even though the value lands on the panel correctly -- a
        real, repeatable false-negative from the RM API, not a real
        failure. Verify via readback before trusting a reported error."""
        mode = self._dpcd_read(self.edp_display_id, DPCD_BACKLIGHT_MODE_SET, 1)
        if mode[0] & DPCD_LUMINANCE_ENABLE_BIT:
            return
        try:
            self._dpcd_write(
                self.edp_display_id,
                DPCD_BACKLIGHT_MODE_SET,
                bytes([mode[0] | DPCD_LUMINANCE_ENABLE_BIT]),
            )
        except NvidiaDpcdBacklightError:
            if self.is_luminance_mode_enabled():
                return
            raise

    def get_target_luminance_mcd(self):
        b = self._dpcd_read(self.edp_display_id, DPCD_TARGET_LUMINANCE, 3)
        return b[0] | (b[1] << 8) | (b[2] << 16)

    def set_target_luminance_mcd(self, mcd):
        # Same false-negative-status caveat as enable_luminance_mode()
        # above, confirmed live for this register specifically: a
        # reported RM failure here does not reliably mean the write
        # didn't land. Verify via readback rather than trust the status.
        b = bytes([mcd & 0xFF, (mcd >> 8) & 0xFF, (mcd >> 16) & 0xFF])
        try:
            self._dpcd_write(self.edp_display_id, DPCD_TARGET_LUMINANCE, b)
        except NvidiaDpcdBacklightError:
            if self.get_target_luminance_mcd() == mcd:
                return
            raise
