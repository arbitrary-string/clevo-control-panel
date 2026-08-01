# Keyboard Colors

A small GTK4 / libadwaita app for controlling the RGB keyboard backlight on
System76 laptops (the single-zone `system76_acpi::kbd_backlight` LED device),
with settings that survive a reboot.

## Why this exists

On my own System76 laptop, the official Keyboard Configurator app hasn't
reliably remembered a color across a reboot for me, and
[OpenRGB](https://openrgb.org/) doesn't detect this keyboard at all. I
haven't tried every third-party tool that claims to handle this, so I can't
say whether one of them already solves it well — but after not finding a
solution that worked for me, I built this one instead. It grew out of a set
of shell aliases + systemd services I'd put together earlier for doing the
same thing from the terminal.

## About the development process

This was built with Claude. I'm a developer (nothing too serious or professional)
going back to BASIC and Visual Basic in the 80s/90s, comfortable with 
bash/PHP/JavaScript, but new to Python and to GTK/GNOME app development 
specifically — this project has doubled as how I'm learning both. I tested 
everything on real hardware (my Darter Pro 7), caught and fixed bugs,
and made the calls on architecture, licensing, and distribution. But a
meaningful share of the actual code was written with AI assistance, and I'd
rather be upfront about that than have it be a surprise to anyone looking
through the source. The only reason I'm publishing this at all is because
I find it genuinely useful, and if you have a Darter Pro 7 running Ubuntu 26.04,
you probably will too. It easily changes the keyboard back-light colors and brightness,
and changes persist across reboots. 

## Features

- 32 curated preset colors, grouped by family
- Custom color picker, with a favorites list you can add to / remove from
- Brightness slider
- Reboot persistence: the color and brightness in effect at shutdown are
  automatically restored on the next boot — whether they were set from this
  app, a terminal alias, or the keyboard's own hardware shortcut
- No root prompts during normal use: a one-time setup grants your user
  direct write access via a dedicated group + udev rule

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

**Via the `.deb` (recommended)** — download the latest release asset and:

```
sudo apt install ./keyboardcolors_<version>_all.deb
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

- Creates a `kbdlight` group and adds your user to it
- Installs a udev rule so the LED device's `color` and `brightness` sysfs
  files are group-writable on every boot
- Installs and enables two systemd services that save the color/brightness
  on shutdown and restore them on boot
- Installs a desktop launcher (and, for the `.deb`, an icon)

The app also has a "Repair Setup" button (gear icon → Settings) that re-runs
this via `pkexec` — useful if a second user account on the same machine needs
access, or if something's gotten out of sync.

## Running

```
./bin/keyboardcolors
```

or launch "Keyboard Colors" from your app grid after setup.

## How persistence works

`/sys/class/leds/.../color` and `.../brightness` are virtual files that reset
on every boot. `save-keyboard-color.service` runs just before shutdown/reboot
and copies the current values to `/var/lib/keyboardcolors/`;
`restore-keyboard-color.service` runs early at boot and writes them back. This
captures changes made any way — this app, a shell alias, or the keyboard's
own Fn-key color shortcut — since it reads the live hardware state rather
than tracking who changed it.

## Project layout

```
keyboardcolors/   Python package: backend, UI, config, setup helper
bin/keyboardcolors  Launcher script
data/             udev rule, systemd units, install script, desktop file
```

## License

Copyright (c) 2026 Michael Updike. GPLv3 — see [LICENSE](LICENSE).
