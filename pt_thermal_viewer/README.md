# PureThermal Mini Pro (Lepton 3.5) Viewer — Pi 3 + 7" touchscreen

Fullscreen PyQt5 app that reads the PureThermal board as a UVC device,
renders a false-color thermal image, and shows live telemetry
(FPA/housing temp, uptime, frame counter, FFC state, spot-meter on tap).

## How it works

The PureThermal board (STM32F412) does all the VoSPI/CCI talking to the
Lepton on your behalf and just presents a standard UVC webcam to the
Pi — so this does **not** reuse your PIC32 VoSPI/CCI driver work; the
board is the driver here. On Linux this shows up as `/dev/videoX` and
can be read with plain V4L2/OpenCV.

With **Raw14 + TLinear + telemetry footer** enabled on the board (a
one-time, persistent setting — see below), each frame is 160×122
16-bit pixels:
- rows 0–119: pixel value = scene temperature in Kelvin × 100 (TLinear
  does the flux→temperature math on-sensor, so no host-side AGC/Planck
  calculations are needed for accuracy — see `render.py`)
- rows 120–121: telemetry footer, parsed per the FLIR Lepton
  Engineering Datasheet Rev 400, Table 2/3 (`telemetry.py`)

## One-time board configuration

The video format / TLinear / telemetry settings need to be set once
and persist across power cycles. The safest way to do this without
guessing at USB extension-unit register values is to use FLIR/GroupGets'
own open-source tool:

1. Install **GetThermal** (https://github.com/groupgets/GetThermal) —
   builds on Linux, or run it once from any Linux machine with the
   board plugged in.
2. In GetThermal: set Video Output Format → Raw14, enable Radiometry +
   TLinear (resolution 0.01), enable Telemetry → Footer.
3. Unplug/replug (or power-cycle) and confirm with:
   ```
   v4l2-ctl --list-devices
   v4l2-ctl -d /dev/videoX --list-formats-ext
   ```
   You want to see a 160x122 16-bit greyscale (Y16) mode.

I did not wire up an in-app FFC-trigger button for the same reason —
it requires sending a Lepton CCI command through the board's UVC
extension unit, and the exact extension-unit GUID / control-selector
values are firmware-build-specific. GroupGets' reference example has
the real, tested values:
https://github.com/groupgets/purethermal1-uvc-capture (the ctypes/
libuvc example does CCI read/write over the XU) — worth adapting once
you have the board in hand to check firmware version against it.

## Install (Raspberry Pi OS, Pi 3 Model B)

Use **apt**, not pip, for OpenCV and PyQt5 — building either from
source via pip on a Pi 3's single-core-speed A53 with 1GB RAM is
extremely slow and often runs out of memory.

```bash
sudo apt update
sudo apt install -y python3-opencv python3-pyqt5 v4l-utils
```

`numpy` comes in as a dependency of python3-opencv.

## USB permissions

```bash
sudo cp packaging/99-purethermal.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```
(Check `lsusb` for the actual VID:PID if it's not `1e4e:0100` — some
resellers/firmware builds differ.)

## Run it

```bash
cd src
python3 main.py                # auto-detects the device
python3 main.py /dev/video2    # or specify explicitly
```

## Autostart on boot (kiosk mode)

```bash
sudo cp packaging/pt-thermal-viewer.service /etc/systemd/system/
sudo systemctl enable pt-thermal-viewer.service
```
Edit the `User`/`WorkingDirectory`/paths in the unit file to match
your actual install location and username first. Requires the Pi set
to boot to desktop with auto-login (`sudo raspi-config` → System
Options → Boot / Auto Login → Desktop Autologin).

## Performance notes for the Pi 3

The Lepton itself only outputs ~8.7 unique frames/sec, which is well
within a Pi 3's budget — the bottleneck is Qt image scaling and touch
event handling, not the sensor. A few things already tuned for this:
- `Qt.FastTransformation` (nearest-neighbor) instead of smooth scaling
  in `ThermalView.show_bgr` — smooth scaling a 160×120 image up to a
  7" panel every frame is unnecessary CPU/GPU load for a thermal
  camera; if you want smoother edges and framerate holds up fine on
  your actual hardware, switch to `Qt.SmoothTransformation`.
- The capture thread drops stale frames instead of queuing them
  (`queue.Full` handling in `capture.py`) so the UI never falls behind.
- PyQt5 over PyQt6: better legacy driver support for the Pi 3's
  VideoCore IV GPU.

If it's still choppy once running on real hardware, the next thing to
check is whether Qt is using the `eglfs` or `xcb` platform plugin
appropriately for how your desktop environment is configured — that's
much easier to diagnose with the actual display in front of you than
to guess at here.

## Known things to verify once you have real frames

- **Word order** for the 32-bit `TimeCounter` and `FrameCounter`
  telemetry fields: the datasheet gives the word range but doesn't
  explicitly state MSW-vs-LSW-first (unlike the status-bits field,
  which *is* explicit and is implemented correctly). `telemetry.py`
  assumes MSW-first; if uptime/frame-counter values look wrong (e.g.
  jumping by 65536, or reading as garbage), flip
  `WORD_ORDER_MSW_FIRST` in `telemetry.py`.
- Frame height: code expects 122 rows (120 image + 2 telemetry) but
  falls back gracefully to 120 (no telemetry) if that's what the
  board reports — check `LeptonCapture._has_telemetry`.

## File layout

```
src/
  telemetry.py    # parses the Lepton telemetry footer (FLIR datasheet Table 2/3)
  capture.py      # V4L2/OpenCV capture thread, device auto-detect
  render.py       # Kelvin->false-color image, spot-temp lookup
  main.py         # PyQt5 fullscreen touch UI
packaging/
  99-purethermal.rules       # udev rule for USB permissions
  pt-thermal-viewer.service  # systemd kiosk autostart
```
