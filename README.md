# Clevo Control Panel

A small GTK4 / libadwaita app (plus a CLI) for hardware features on System76
laptops and generic Clevo/Tongfang barebones: RGB keyboard backlight control
(with settings that survive a reboot), and battery charge threshold control
on boards whose `clevo-acpi` driver build supports it. Two keyboard hardware
backends are auto-detected:

- **System76 laptops** — the single-zone `system76_acpi::kbd_backlight` LED
  device (e.g. Darter Pro 7).
- **Generic Clevo/Tongfang barebones** — the multi-zone (left/center/right/
  numpad/lightbar) `clevo-acpi::kbd_backlight` device used by a much wider
  range of hardware resold under many brands. Some of these boards need a
  separate driver-enablement package first to get the kernel recognizing
  them at all — this app itself doesn't touch drivers, only whichever LED
  device is already present. Battery charge threshold control is available
  only on this backend, and only if the installed `clevo-acpi` build
  includes `charge_control_start_threshold` / `charge_control_end_threshold`
  sysfs support.

## Why this exists

On my own System76 laptop, the official Keyboard Configurator app hasn't
reliably remembered a color across a reboot for me, and
[OpenRGB](https://openrgb.org/) doesn't detect this keyboard at all. I
haven't tried every third-party tool that claims to handle this, so I can't
say whether one of them already solves it well — but after not finding a
solution that worked for me, I built this one instead. It grew out of a set
of shell aliases + systemd services I'd put together earlier for doing the
same thing from the terminal.

It later grew a second keyboard backend after a separate project (getting the
keyboard RGB working on a brand-new, not-yet-recognized Clevo barebone)
turned up the same underlying problem in a more general form: good hardware,
backlight control that technically exists in the firmware, and no polished
way for an ordinary user to reach it from Linux. Rather than build a second
app, this one grew a pluggable backend.

Battery charge threshold control followed the same pattern: that Clevo
barebone also has a Windows-only "battery saver" feature (stop charging at
a configurable percentage, resume at another) to reduce long-term battery
wear, with no Linux equivalent. Since the underlying `clevo-acpi` platform
device was already right there, that driver grew the sysfs attributes for
it, and this app grew a page to control them.

## About the development process

This was built with Claude. I'm a coder (nothing too serious or professional)
going back to BASIC and Visual Basic in the 80s/90s, comfortable with 
bash/PHP/JavaScript, but new to Python and to GTK/GNOME app development 
specifically — this project has doubled as how I'm learning both. I tested 
everything on real hardware (my Darter Pro 7, and a Clevo/Tongfang barebone),
caught and fixed bugs, and made the calls on architecture, licensing, and
distribution. But a meaningful share of the actual code was written with AI
assistance, and I'd rather be upfront about that than have it be a surprise
to anyone looking through the source. The only reason I'm publishing this at
all is because I find it genuinely useful, and if you have a Darter Pro 7
(or a generic Clevo/Tongfang-based laptop with the `clevo-acpi` backend)
running a recent Ubuntu, you probably will too.

## Features

**Keyboard**
- 32 curated preset colors, grouped by family
- Custom color picker, with a favorites list you can add to / remove from
- Brightness slider
- Reboot persistence: the color and brightness in effect at shutdown are
  automatically restored on the next boot — whether they were set from this
  app, a terminal alias, or the keyboard's own hardware shortcut

**Battery** (clevo-acpi backend only, driver-dependent)
- Set the percentage at which charging resumes and the percentage at which
  it stops, to keep the battery out of the high-wear near-100% range for
  laptops that mostly run on AC power
- Thresholds are stored on the embedded controller/firmware side, the same
  place Windows' Control Center software would write them, so they persist
  across reboots on their own — no separate persistence service needed

**Both**
- No root prompts during normal use: a one-time setup grants your user
  direct write access via a dedicated group + udev rule
- `clevo-control-panel-cli` for scripting: keyboard static colors
  (whole-keyboard or a single zone on multi-zone hardware), brightness,
  built-in lighting effects (color cycle, breathe, per-zone rainbow wave),
  and battery threshold status/set

## Requirements

Everything needed (Python 3, PyGObject, GTK4, libadwaita) ships with a
standard GNOME desktop, with one exception: `python3-gi-cairo`, which
provides the PyGObject/Cairo bridge used to draw the color swatches. It's
a small official-repo package; `install.sh` installs it automatically if
missing (`sudo apt install python3-gi-cairo` to do it manually).

Desktop compatibility: this is a plain GTK4/libadwaita app, so it also runs
under KDE Plasma and COSMIC (it will just carry libadwaita's GNOME-style
theming rather than matching those desktops natively).

## Installing

**Via the `.deb` (recommended)** — download the `.deb` from the
[latest release](https://github.com/arbitrary-string/clevo-control-panel/releases/latest)
and:

```
sudo apt install ./clevo-control-panel_<version>_all.deb
```

This installs the app, desktop launcher, and icon, and its `postinst` step
automatically does the one-time system setup below for whoever ran `apt
install`. **Log out and back in (or reboot) once** afterwards — group
membership only applies to new login sessions.

**From a repo checkout instead**, run once from a terminal:

```
sudo bash data/install.sh "$USER"
```

Either path:

- Creates a `clevoctl` group and adds your user to it
- Installs a udev rule so the relevant sysfs files (LED color/brightness,
  and battery charge thresholds if present) are group-writable on every boot
- Installs and enables two systemd services that save the keyboard
  color/brightness on shutdown and restore them on boot
- Installs a desktop launcher (and, for the `.deb`, an icon), plus the
  `clevo-control-panel-cli` command

The app also has a "Repair Setup" button (gear icon → Settings) that re-runs
this via `pkexec` — useful if a second user account on the same machine needs
access, or if something's gotten out of sync.

## Running

```
./bin/clevo-control-panel          # GUI
./bin/clevo-control-panel-cli keyboard status
./bin/clevo-control-panel-cli keyboard color ff00aa
./bin/clevo-control-panel-cli keyboard color 00ff00 --zone left   # multi-zone hardware only
./bin/clevo-control-panel-cli keyboard brightness 150
./bin/clevo-control-panel-cli keyboard effect breathe --color 00ffff
./bin/clevo-control-panel-cli battery status
./bin/clevo-control-panel-cli battery set --start 70 --end 80
```

or launch "Clevo Control Panel" from your app grid after setup.

## How persistence works

The keyboard LED device's sysfs files are virtual and reset on every boot.
`save-keyboard-color.service` runs `clevo-control-panel-cli keyboard
save-state` just before shutdown/reboot, which writes the current brightness
and per-zone color(s) to `/var/lib/clevo-control-panel/state.json`;
`restore-keyboard-color.service` runs `clevo-control-panel-cli keyboard
restore-state` early at boot and writes them back. This captures changes
made any way — this app, the CLI, or the keyboard's own Fn-key color
shortcut — since it reads the live hardware state rather than tracking who
changed it.

Battery charge thresholds work differently: they're stored by the embedded
controller/firmware itself (the same mechanism Windows' Control Center
software uses), so they already survive a reboot without any systemd
service — the sysfs files just reflect whatever's currently stored there.

## Project layout

```
clevo_control_panel/       Python package: backends, UI, CLI, config, setup helper
bin/clevo-control-panel       GUI launcher script
bin/clevo-control-panel-cli    CLI launcher script
data/                       udev rule, systemd units, install script, desktop file
```

## License

Copyright (c) 2026 Michael Updike. GPLv3 — see [LICENSE](LICENSE).
