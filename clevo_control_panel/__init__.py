"""Clevo Control Panel: a GTK4/libadwaita app (and CLI) for hardware features
on System76 laptops and generic Clevo/Tongfang barebones -- keyboard RGB
backlight, battery charge threshold control on boards with the clevo-acpi
driver's flexicharger support, performance mode control (fan behavior,
a tray icon with quick mode select, and optionally real CPU/GPU power
scaling via TLP/nvidia-smi) on boards with the clevo-acpi driver's
performance_mode support, and (on boards with the fan control attributes)
a continuous temperature-driven fan curve with a background daemon and
kernel-level watchdog, plus automatic display refresh-rate switching by
power source. On boards with an NVIDIA dGPU driving an OLED panel
directly in dGPU mode, also extends the standard brightness slider to
the panel's real full-range maximum via direct DPCD writes, working
around the legacy brightness path's much lower cap. On boards with a
hardware GPU MUX, also supports switching BIOS-level GPU mode
(MSHybrid/dGPU) via a firmware NVRAM write, applied on next reboot,
automatically steering prime-select away from an Intel-only setting
first if needed so dGPU mode doesn't come up to a black screen, and
keeping the Soft Brightness Plus GNOME extension enabled only in
MSHybrid mode, since it conflicts with dGPU mode's own brightness
control."""

__version__ = "0.11.5"
