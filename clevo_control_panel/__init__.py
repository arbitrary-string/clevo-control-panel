"""Clevo Control Panel: a GTK4/libadwaita app (and CLI) for hardware features
on System76 laptops and generic Clevo/Tongfang barebones -- keyboard RGB
backlight, battery charge threshold control on boards with the clevo-acpi
driver's flexicharger support, performance mode control (fan behavior,
a tray icon with quick mode select, and optionally real CPU/GPU power
scaling via TLP/nvidia-smi) on boards with the clevo-acpi driver's
performance_mode support, and (on boards with the fan control attributes)
a continuous temperature-driven fan curve with a background daemon and
kernel-level watchdog, plus automatic display refresh-rate switching by
power source."""

__version__ = "0.9.1"
