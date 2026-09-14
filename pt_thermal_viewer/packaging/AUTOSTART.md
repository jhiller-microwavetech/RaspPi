# Running pt_thermal_viewer automatically at boot

This sets up the app to launch full-screen as soon as the Pi finishes
booting, no login or terminal needed, via a `cron @reboot` entry for the
`pi` user — no separate compiled binary, just the existing `main.py`
script launched by a small wrapper.

## 0. One-time prerequisites (do these first if you haven't)

Everything below assumes the app already runs on this Pi — no `apt
install`, no downloads, no internet connection needed for anything in
this document. Nothing here fetches packages.

- `python3-opencv` / `python3-pyqt5` / `v4l-utils` should already be in
  place from getting the app running in the first place (see the main
  [README](../README.md)) — nothing new to install for autostart itself.
- `run_kiosk.sh` looks for `xset` (used to detect when the desktop is
  actually up — see step 2) but does **not** try to install it. `xset`
  ships as part of the standard Raspberry Pi OS Desktop image, so it's
  almost certainly already there; if it isn't, the script just falls
  back to a fixed 15-second sleep instead — no package needed either way.
- The PureThermal board configured once via GetThermal (Raw14 + TLinear +
  telemetry footer) — see the README's "One-time board configuration".
  If that's already done, nothing to redo here.
- Confirm `python3 src/main.py` actually runs and shows the thermal image
  when launched manually, logged in at the Pi's own desktop, *before*
  wiring up autostart. Debugging a GUI app that fails silently at boot is
  much harder than debugging it interactively.

## 1. Boot straight to the desktop, logged in, on X11

Run `sudo raspi-config` and set:

1. **System Options -> Boot / Auto Login -> Desktop Autologin.**
   Without this, the Pi boots to a login prompt and nothing is ever
   displayed on the touchscreen.
2. **Advanced Options -> Wayland -> X11.**
   Raspberry Pi OS (Bookworm and newer) now defaults to the Wayland/labwc
   desktop on every Pi model, including the Pi 3. Under plain Wayland
   there's no fixed `DISPLAY=:0` / `~/.Xauthority` the way there is under
   the classic X11 desktop, so `run_kiosk.sh` (which hardcodes those)
   won't reliably find a display to draw on. Explicitly selecting **X11**
   here restores that classic, predictable desktop session — it's also
   the session this repo's other docs (`CLAUDE.md`'s discussion of the
   LXDE/PIXEL panel and the `xcb` Qt platform plugin) already assume, and
   X11 has better legacy driver support for the Pi 3's VideoCore IV GPU
   than Wayland does.

   *If you'd rather stay on Wayland/labwc instead of switching to X11*,
   `cron @reboot` won't have a display to attach to at all the way this
   is written — you'd need to launch from labwc's own
   `~/.config/labwc/autostart` instead, which knows its own display. That
   variant hasn't been tested here; treat it as a starting point, not a
   verified recipe.

Reboot after changing these two settings.

## 2. Why a wrapper script, not a bare cron line

`cron`'s `@reboot` fires as soon as the cron daemon itself starts, which
is normally well before the X11 desktop (autologin) has finished coming
up — a plain `@reboot python3 main.py` line would usually fail because
there's no display yet. Cron also has no restart-on-crash the way
systemd does, so a bug that crashes the app would leave a blank screen
until the next reboot.

`packaging/run_kiosk.sh` handles both: it polls for the X server (via
`xset`, falling back to a fixed 15s sleep if `xset` isn't installed) for
up to 60 seconds before launching, then loops `main.py` forever — if it
ever exits, it's relaunched 3 seconds later. All output (its own and
`main.py`'s) goes to `~/pt_thermal_viewer/kiosk.log`.

## 3. Install the udev rule and the cron entry

From the Pi, in this repo:

```bash
sudo bash packaging/install.sh
```

This copies `packaging/99-purethermal.rules` to `/etc/udev/rules.d/`,
makes `packaging/run_kiosk.sh` executable, and adds an
`@reboot /path/to/packaging/run_kiosk.sh` line to the `pi` user's
crontab (skipping it if that line is already there, so it's safe to
re-run). It also does a couple of sanity checks (that the board's actual
USB VID:PID matches the udev rule, and that the repo is installed where
`run_kiosk.sh` expects) and warns you if not.

If your username, home directory, or install path isn't `pi` /
`/home/pi/pt_thermal_viewer`, edit `packaging/run_kiosk.sh` (`APP_DIR`,
`XAUTHORITY`, `LOG`) to match *before* running `install.sh`.

## 4. Test it without rebooting

```bash
sudo -u pi /home/pi/pt_thermal_viewer/packaging/run_kiosk.sh &
tail -f /home/pi/pt_thermal_viewer/kiosk.log
```

You should see the fullscreen thermal view come up over the desktop
within a few seconds. If it doesn't, the log almost always says why
(X server never came up, missing device, wrong frame format).

To stop the test run: `sudo pkill -f run_kiosk.sh` (this also kills the
`main.py` it launched).

Once it looks right:

```bash
sudo reboot
```

and confirm it comes up on its own after boot, with no login or terminal
interaction.

## Troubleshooting

- **`kiosk.log` says "X server never became ready after 60s".** The
  desktop isn't set to Desktop Autologin + X11 (step 1), or it's taking
  longer than 60s to come up (slow SD card) — raise the loop count near
  the top of `run_kiosk.sh` if the latter.
- **Works when run manually (`python3 src/main.py`) but not via cron.**
  cron jobs run with a minimal environment — no `$PATH` additions, no
  shell profile. `run_kiosk.sh` uses absolute paths (`/usr/bin/python3`)
  for exactly this reason; if you've customized it, keep that.
- **`RuntimeError` about unexpected frame size**, or the udev-rule VID:PID
  warning from `install.sh` — see the main README's "One-time board
  configuration" and "USB permissions" sections; these are board
  configuration issues, not autostart issues.
- **No PureThermal device at boot / it was plugged in after boot.** The
  restart loop in `run_kiosk.sh` retries every 3 seconds, so plugging the
  board in after boot (or a board that enumerates late) should recover
  on its own without a reboot.
- **Nothing in `kiosk.log` at all.** Confirm the crontab entry actually
  landed: `sudo crontab -u pi -l` should show the `@reboot` line. Also
  confirm cron itself is running: `systemctl status cron`.

## Uninstalling

```bash
sudo pkill -f run_kiosk.sh
crontab -u pi -l | grep -v run_kiosk.sh | crontab -u pi -
sudo rm /etc/udev/rules.d/99-purethermal.rules
```
