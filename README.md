# Clevo Control Panel

A small GTK4 / libadwaita app (plus a CLI) for hardware features on System76
laptops and generic Clevo/Tongfang barebones: RGB keyboard backlight control
(with settings that survive a reboot), battery charge threshold control, and
performance mode control (fan behavior plus, optionally, real CPU/GPU power
scaling), on boards whose `clevo-acpi` driver build supports each feature.
Two keyboard hardware backends are auto-detected:

- **System76 laptops** — the single-zone `system76_acpi::kbd_backlight` LED
  device (e.g. Darter Pro 7).
- **Generic Clevo/Tongfang barebones** — the multi-zone (left/center/right/
  numpad/lightbar) `clevo-acpi::kbd_backlight` device used by a much wider
  range of hardware resold under many brands. Some of these boards need a
  separate driver-enablement package first to get the kernel recognizing
  them at all — this app itself doesn't touch drivers, only whichever LED
  device is already present. Battery charge threshold and performance mode
  control are available only on this backend, and only if the installed
  `clevo-acpi` build includes the corresponding sysfs support
  (`charge_control_start_threshold`/`charge_control_end_threshold`, and
  `performance_mode`, respectively).

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

Performance/fan mode control followed the same pattern again: that Clevo
barebone's Windows Control Center software also has a Performance/Balanced/
Quiet mode selector with no Linux equivalent, and no vendor documentation
for how it works. The mapping this app uses was determined empirically
(observing real fan RPM response to sustained CPU load for each candidate
EC command value, with the laptop fully cooled down between isolated
tests) rather than from any spec — see
`~/laptopissues/performance-mode/NOTES.md` in that project's development
history for the raw data, if you're curious or trying to validate this on
different hardware.

Measuring that mapping turned up something worth knowing if you have this
same EC command on your own board: it only ever affects fan curves. RAPL
power limits, EPP, and measured power draw under load were all identical
across every mode when tested back-to-back. So Balanced/Quiet/Performance
also drive real CPU (and, where possible, GPU) power scaling through
standard tools instead — TLP for the CPU, `nvidia-smi` clock locking for
the GPU — layered on top of the same fan control. See "CPU/GPU power
scaling" below.

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
- "Charge to 100% This Time": a one-shot override for the occasional trip
  or long day away from AC, without having to remember to raise (and later
  lower) your normal thresholds by hand. Reverts on its own the next time
  the laptop is running on battery — including across a full shutdown and
  reboot with AC unplugged in between. See "Charge to 100% this time"
  below for exactly how and why

**Performance** (clevo-acpi backend only, driver-dependent)
- Switch between Balanced, Quiet, Performance, and Max Fan modes, from the
  app window, the tray icon menu, or the CLI
- Balanced/Quiet/Performance also apply real CPU power scaling (via TLP)
  and, on laptops with an NVIDIA GPU, GPU clock scaling (via `nvidia-smi`)
  — not just fan curves. See "CPU/GPU power scaling" below; this part is
  optional and needs a one-time manual setup step
- Max Fan pins both fans at high speed regardless of actual temperature
  until you switch away from it — it's a manual cooling-boost override, not
  a normal thermal profile, so it's called out separately in the app, and
  leaves CPU/GPU power settings exactly as they were
- Reboot persistence: the mode you set is saved the moment you set it, and
  restored once the desktop is fully up at your next login (not via an
  early-boot systemd service — see "How persistence works" below for why)
- The app window and tray menu both poll the live mode every 5 seconds, so
  switching from one stays reflected in the other without needing a manual
  refresh
- Optional automatic switching by power source: turn it on, pick which of
  Balanced/Quiet/Performance to use on AC and which to use on battery, and
  the mode follows the AC adapter from then on. Manual mode buttons stay
  visible but are disabled while this is on (Max Fan stays available
  either way, since it's an override, not one of the two auto-picked
  profiles) — turn auto-switch off to take manual control back. See
  "Automatic profile switching" below
- Optional display refresh rate switching alongside the same AC/battery
  trigger — pick a rate for AC, a (typically lower) rate for battery, to
  save power. Part of the same "Automatic Switching" toggle, not a
  separate feature. See "Automatic profile switching" below
- Optional continuous, temperature-driven **custom fan curve**, as an
  alternative to the four fixed presets — a real background daemon with a
  kernel-level dead-man's-switch, so a crashed or killed control process
  can never leave the fan stuck at a stale speed. See "Custom fan curve"
  below

**Tray icon**
- A small always-present tray icon (started automatically at login,
  minimized — no window pops up) with a quick menu: open the main window,
  jump straight to any performance mode with a radio-button indicator
  showing the current one, or quit
- Closing the main window hides it to the tray instead of quitting; use
  the tray menu's Quit to actually exit

**All**
- No root prompts during normal use: a one-time setup grants your user
  direct write access via a dedicated group + udev rule
- `clevo-control-panel-cli` for scripting: keyboard static colors
  (whole-keyboard or a single zone on multi-zone hardware), brightness,
  built-in lighting effects (color cycle, breathe, per-zone rainbow wave),
  battery threshold status/set, performance mode status/set,
  automatic-switching status/set (including refresh rate), and fan curve
  status/enable/disable/release/manual-set

## Requirements

Everything needed (Python 3, PyGObject, GTK4, libadwaita) ships with a
standard GNOME desktop, with two exceptions, both small official-repo
packages that `install.sh` installs automatically if missing:

- `python3-gi-cairo`, the PyGObject/Cairo bridge used to draw the color
  swatches
- `gir1.2-gtk-3.0` and `gir1.2-ayatanaappindicator3-0.1`, used only by the
  separate tray icon helper process (see "Tray icon architecture" below
  for why it's a separate GTK3 process rather than part of the main GTK4
  app). Optional in the sense that the app runs fine without them — you
  just won't get a tray icon, and closing the window will quit the app
  instead of hiding it, since there'd be no way to get it back otherwise.

Also depends on `tlp` (installed automatically by `install.sh`) for real
CPU power scaling — see "CPU/GPU power scaling" below. `nvidia-smi`, if
you have an NVIDIA GPU, is used the same way but isn't a hard dependency
(that part just no-ops without it).

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
  and battery charge thresholds/performance mode if present) are
  group-writable on every boot
- Installs and enables two systemd services that save the keyboard
  color/brightness on shutdown and restore them on boot
- Installs a desktop launcher (and, for the `.deb`, an icon), an autostart
  entry that launches the app minimized at login (tray icon + performance
  mode restore, no window), plus the `clevo-control-panel-cli` command

The app also has a "Repair Setup" button (gear icon → Settings) that re-runs
this via `pkexec` — useful if a second user account on the same machine needs
access, or if something's gotten out of sync.

One more optional, manual step: `data/setup-power-profile-sudoers.sh` (see
"CPU/GPU power scaling" below) — not run automatically by either install
path, since it touches sudoers.

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
./bin/clevo-control-panel-cli performance status
./bin/clevo-control-panel-cli performance set quiet
./bin/clevo-control-panel-cli performance auto status
./bin/clevo-control-panel-cli performance auto set --enabled on --ac performance --battery quiet
./bin/clevo-control-panel-cli performance auto set --ac-refresh 165 --battery-refresh 60
./bin/clevo-control-panel-cli fan status
./bin/clevo-control-panel-cli fan curve show
./bin/clevo-control-panel-cli fan enable
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

Performance mode is different again, and deliberately does **not** use an
early-boot systemd service the way keyboard color does. An earlier version
did exactly that, and while investigating an unrelated issue with it, the
laptop's fans spiked to full speed and it powered off abruptly a few
seconds into boot — details in
`~/laptopissues/performance-mode/NOTES.md`. The evidence pointed at
applying this specific, reverse-engineered EC command that early (while
ACPI/EC subsystems are still initializing) as the likely cause, something
never actually tested before shipping it that way. The fix: persistence
now happens entirely in the app layer, at a point that's always
fully-booted and interactive, exactly like every real test of this
feature. `performance.py`'s `set_mode()` writes the new mode to
`/var/lib/clevo-control-panel/performance-mode.state` the moment it's
set (no shutdown-time hook needed — the read is a plain cached-value
read with no EC interaction); the app's own startup restores it, once,
the first time the app runs after login (via the autostart entry below).

## CPU/GPU power scaling

The reverse-engineered EC command behind Balanced/Quiet/Performance turned
out to only affect fan curves — confirmed by testing (identical RAPL power
limits, EPP, and measured power draw across all three modes under the same
load). So real CPU/GPU scaling is layered on top using standard, well-
supported tools instead of more EC reverse-engineering:

- **CPU, via [TLP](https://linrunner.de/tlp/)**: each mode writes
  `/etc/tlp.d/90-clevo-control-panel.conf` (CPU governor, EPP, turbo boost,
  HWP dynamic boost — the same value for AC and battery, since this is a
  deliberate choice made through the app, not something that should change
  just because the power source did) and runs `tlp start` to apply it
  immediately. Quiet also sets a hard `scaling_max_freq` ceiling (1500MHz),
  applied directly rather than through TLP — disabling turbo alone only
  limits the CPU to its *base* clock (3700MHz on the hardware this was
  built on), nowhere near quiet, and TLP's own equivalent setting turned
  out to leave a previous cap in place rather than reset it when a mode
  doesn't want one. Confirmed both directions hold correctly, including
  that a hybrid CPU's cores can have genuinely different max frequencies
  (uncapping restores each core's own maximum, not one shared value).
- **GPU, via `nvidia-smi`, if you have an NVIDIA GPU**: `nvidia-smi -pl`
  (power limit) is attempted but is a no-op on many laptop/Max-Q GPUs —
  NVIDIA locks that down at the vBIOS level ("not supported in current
  scope"), confirmed on the hardware this was built on. `nvidia-smi -lgc`
  (GPU clock locking) is a different mechanism and isn't restricted the
  same way — confirmed working, including under real load. Quiet caps the
  GPU clock low, Balanced caps it moderately, Performance removes the cap
  entirely.
- **Intel iGPU (`xe` driver)**: also capped, via
  `/sys/class/drm/cardN/device/tile0/gt0/freq0/max_freq` — found on a
  second, more thorough look; the first search missed it by not
  traversing through the card's `device` symlink correctly. Only `gt0`
  (the render/compute/blitter engine, the one that actually reaches
  2.4GHz) is capped — quiet=800MHz, balanced=1500MHz,
  performance=uncapped. `gt1` (video decode/encode) is deliberately left
  alone, since capping media engines wasn't asked for and could hurt
  video playback for no real benefit. Confirmed holding under real
  sustained load. Device path resolved dynamically (matches whichever
  `/sys/class/drm/cardN` is driven by `xe`), not hardcoded to one
  machine's PCI address.

Both `tlp start` and `nvidia-smi -pl`/`-lgc`/Intel `max_freq` need root. Rather than a
`pkexec` prompt on every single mode switch (which would defeat the point
of a quick tray-menu switch), a small script
(`data/apply-power-profile.sh`) does the actual work, and a narrowly-scoped
sudoers rule lets the `clevoctl` group run it — **only** with the exact
arguments `quiet`, `balanced`, or `performance`, nothing else, no wildcard
argument matching. This is a deliberate, security-sensitive file, so it's
never installed automatically: run `data/setup-power-profile-sudoers.sh`
yourself when you're ready (it validates with `visudo -c` before touching
anything real). Without it, these three modes still work exactly as
before — fan control only, CPU/GPU scaling silently skipped.

## Automatic profile switching

Turn on "Automatic switching" in the Performance page and pick a profile
for AC power and a (typically lower-power) profile for battery — from then
on, the mode follows the power source with no manual switching needed.
Only Balanced/Quiet/Performance are selectable here; Max Fan is a manual
cooling override, not a real profile, so it's excluded from the picker and
always stays available to click even while auto-switch owns the other
three.

The setting lives in `/var/lib/clevo-control-panel/auto-profile.json`
(system-wide like the other state files, not per-user — the hardware is
shared by whoever's logged in) and is applied in three places, all
funneling through the same `performance.py` `set_mode()` a manual click
would use:

- **At app startup**: if auto-switch is on, the mode is set from the
  current AC/battery state immediately, rather than restoring whatever was
  last manually set.
- **On every change you make in the app** (toggling the switch, changing
  either dropdown): applied immediately, not left to wait for a
  notification.
- **While the app keeps running**: `power_source.py` subscribes to
  [UPower](https://upower.freedesktop.org/)'s `OnBattery` D-Bus property
  (the same system service GNOME/KDE already run for the battery icon and
  power settings) and switches the instant AC is plugged or unplugged —
  no polling, no added dependency, since UPower is already present on any
  standard desktop. Only falls back to polling
  `/sys/class/power_supply/*/type == Mains` every 5 seconds if UPower
  isn't running at all, e.g. a minimal install without a full desktop
  environment. On hardware with no distinguishable AC/battery supply
  (e.g. a desktop), auto-switch has nothing to key off of and the app
  falls back to manual mode.

The manual mode buttons (Balanced/Quiet/Performance, not Max Fan) become
insensitive while auto-switch is on, both in the app window and the tray
menu — otherwise a manual click there would just get silently overridden
on the next power-source check, which would be confusing. Turn auto-switch
off to take manual control back.

The same toggle also optionally drives **display refresh rate**: pick a
rate for AC and a (typically lower) rate for battery, left unset by
default ("Don't change") so nothing happens unless you opt in. Applied
through `display.py`'s `DisplayRefreshRate`, which talks to GNOME
Mutter's own `org.gnome.Mutter.DisplayConfig` D-Bus interface — the same
one GNOME Settings' Displays panel uses internally, and the Wayland-native
way to do this (there's no `xrandr` under Wayland). Only works under
GNOME/Mutter, not other Wayland compositors. A failure here (unsupported
rate, no Mutter D-Bus, a non-GNOME session) is swallowed silently, the
same way a CPU/GPU power-profile failure is — this is a comfort/
battery-saving feature, not something that should ever block or crash a
profile switch.

## Charge to 100% this time

The Battery page's charge thresholds are a hysteresis window, not a
simple ceiling: charging only **resumes** once capacity drops to/below
the *start* percentage, and raising the *end* percentage alone does
nothing if the battery is currently sitting stopped somewhere between
the two (a very plausible moment to reach for this button in the first
place). "Charge to 100% This Time" raises **both** thresholds — end to
100, start to 99 — so charging resumes immediately regardless of current
capacity, saves your normal thresholds to a small state file
(`/var/lib/clevo-control-panel/charge-override.json`), and reverts them
automatically the next time the laptop is seen running on battery.

That last part works even across a full shutdown: `PowerSourceMonitor`
checks the *current* power source once immediately whenever it's
constructed, not just on live changes, so if you shut down while
charging and unplug at some point before or shortly after your next
login, the app's autostart entry notices it's on battery and reverts
right then — even though nothing was running to see the actual unplug
happen.

Two details worth knowing if you're touching this code: the override
uses 99, not 100, for the start threshold, and end has to be raised
*before* start — confirmed live that this EC silently rejects (keeps the
previous value, doesn't clamp) any write that would leave start >= end,
so start can never actually reach 100 while end is 100, and raising
start past its old value requires end to already be higher. Reverting
goes in the opposite order (start, then end) for the same reason.

## Custom fan curve

An alternative to the four fixed presets: a continuous, temperature-driven
duty curve you define yourself, built directly on the same `clevo-acpi`
EC commands `performance_mode` already uses (a second, independently
discovered command family — see `~/odm-laptop-research/NOTES.md` for how
it was found and validated). Turn it on in the Performance page, edit the
temperature/duty points, set a critical-temperature override and a
maximum-duty comfort ceiling, then Apply.

**Safety design** — this genuinely controls cooling, so it's built with
several independent layers, not just one:

- A **real background daemon** (`clevo-fan-curve.service`, `Type=notify`,
  `Restart=on-failure`), not a GUI-process timer — curve-following keeps
  working even if the window is closed, and systemd restarts it within
  seconds if it crashes. It's a systemd **--user** unit, not a system
  one, so it only ever starts once you've actually logged in, never
  during early boot — the same rule `performance_mode` persistence
  already follows, after an earlier incident traced a hard power-off to
  a different reverse-engineered EC command being issued from a system
  unit at early boot (see `~/laptopissues/performance-mode/NOTES.md`).
- **Graceful release on any normal exit** (stop, restart, shutdown,
  exception) — the daemon always hands control back to firmware auto
  before it goes away.
- **A hard critical-temperature override**, re-checked every single tick
  independent of the curve or the max-duty cap — if temperature crosses
  the line, both fans go to 100%, full stop.
- **A kernel-level watchdog inside `clevo-acpi.c` itself** — a genuine
  dead-man's-switch: if nothing renews the manual override within a
  configurable timeout (default 15s), the *kernel* releases to firmware
  auto control on its own, with no userspace process involved at all.
  Confirmed live, including the harshest case: an uncatchably killed
  (`SIGKILL`) daemon still gets its fan control released automatically,
  purely by the kernel timing out.
- A **prominent manual "Release Fan Control to Firmware" button**, always
  reachable regardless of the toggle state, plus a live daemon-health
  indicator (service active? status file fresh? critical override
  engaged?) so a silently stuck daemon is visible, not invisible.

Curve mode and the four discrete presets are mutually exclusive with
automatic switching (both make continuous, automated claims on the same
EC state) — turning one on turns the other off, with a toast explaining
why. All four mode buttons, Max Fan included, become insensitive while a
curve is active, since Max Fan and a curve would otherwise race for
control of the literal same fan-duty actuator.

`clevo-control-panel-cli fan status|curve show|curve set|enable|disable|
release|set` gives the same functionality from the command line — see
`--help` on each.

## Tray icon architecture

The tray icon runs as a **separate process** from the main GTK4/libadwaita
app, using the classic GTK3-based `AyatanaAppIndicator3` — not a toolkit
choice made lightly. The newer, GTK4-safe `AyatanaAppIndicatorGlib`
library was tried first, but its `set_menu()` doesn't actually implement
the `com.canonical.dbusmenu` D-Bus interface GNOME's `ubuntu-appindicators`
shell extension needs to show a working menu (confirmed by testing, not
documentation — it's a young library, first packaged for Debian in March
2025). GTK3 and GTK4 can't be loaded in the same process, so the tray
lives in its own small process (`clevo_control_panel/tray_helper.py`),
talking to the main app over D-Bus for Open/Quit (using `Gio.Application`'s
own built-in remote-activation and remote-actions support — no custom IPC
code needed there), while reading/writing performance mode directly
(`backend.py`/`performance.py` are plain Python with no GTK dependency at
all, so there's no reason to round-trip through the main process for
that). The two processes are loosely coupled on purpose: the helper
watches the main app's D-Bus name and quits shortly after it disappears,
however that happens.

## Project layout

```
clevo_control_panel/       Python package: backends, UI, CLI, tray helper, config, setup helper
bin/clevo-control-panel       GUI launcher script
bin/clevo-control-panel-cli    CLI launcher script
data/                       udev rule, systemd units, autostart/desktop entries, install script,
                            apply-power-profile.sh + its sudoers setup script
```

## License

Copyright (c) 2026 Michael Updike. GPLv3 — see [LICENSE](LICENSE).
