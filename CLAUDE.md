# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fullscreen PyQt5 touch-UI thermal camera viewer for a PureThermal Mini Pro
(FLIR Lepton 3.5) board, targeting a Raspberry Pi 3 + 7" touchscreen kiosk.
The board presents itself as a standard UVC webcam (`/dev/videoX` on Linux),
so this app talks to it via plain OpenCV/V4L2 — it does not do any
VoSPI/CCI/SPI driver work itself (that's all done on-board by the
PureThermal's STM32F412).

All source lives under `pt_thermal_viewer/`.

## Running

```bash
cd pt_thermal_viewer/src
python3 main.py                # auto-detects the device (Linux only)
python3 main.py /dev/video2    # explicit device path (Linux)
python3 main.py 1              # explicit device index (Windows)
```

The window is always a fixed, frameless 800x480 (`BASE_WINDOW_W`/`BASE_WINDOW_H`
in `main.py`) — the exact resolution of the target Raspberry Pi 7" Touch
Display — on every platform, including Windows dev machines. There is no
`--windowed`/fullscreen distinction anymore; don't reintroduce one without
checking with the user first.

On Raspberry Pi OS, the window also carries `Qt.WindowStaysOnTopHint` and is
explicitly `move(0, 0)`'d in `main()` — the LXDE/PIXEL desktop panel reserves
screen space via an EWMH strut, so without this the window manager places a
plain frameless window inside the strut-reduced work area (pushed down below
the panel), clipping the bottom of the sidebar off-screen. This can only
actually be verified on real Pi hardware, same caveat as device auto-detect
below — on Windows the flag/move are harmless no-ops behaviorally (window is
already undecorated and at the dev monitor's origin-ish position).

There is no test suite, linter, or build step in this repo — it's a small
script-style app with no packaging beyond the systemd unit in `packaging/`.

On Windows dev machines, OpenCV can't auto-detect the device by name (no
reliable cross-backend way to query a camera's name before opening it) —
use `capture.find_windows_cameras()` to probe indices, or check Device
Manager, then pass the index explicitly.

## Required board configuration (not done by this code)

This app assumes the PureThermal board has already been configured, once,
to stream 160x122 16-bit frames: **Raw14 video format + Radiometry/TLinear
(0.01 K/LSB) + Telemetry footer enabled**. This is a persistent on-board
setting done externally via GetThermal (see README) — the app has no code
path to set it. If you see a `RuntimeError` about unexpected frame size in
`capture.LeptonCapture.open`, it's this, not an app bug.

FFC (flat-field correction) triggering is deliberately *not* implemented
(`MainWindow._on_ffc_clicked` just shows a dialog) — it requires sending a
Lepton CCI command through the board's UVC extension unit, and the
extension-unit GUID/control-selector values are firmware-build-specific.
Don't try to "finish" this without real hardware to verify against; see the
comment in `main.py` for the reference implementation to adapt
(groupgets/purethermal1-uvc-capture).

## Data pipeline / architecture

The four source files form a straight pipeline, each with one job:

1. **`capture.py`** — `LeptonCapture` opens the UVC device via OpenCV
   (`CAP_V4L2` on Linux, `CAP_DSHOW` on Windows), forces a 16-bit greyscale
   (`Y16`) stream so OpenCV doesn't mangle it into 8-bit BGR, and runs a
   background thread that reads frames and splits each one into:
   - `image_k100`: the top 120 rows, uint16, **value = Kelvin × 100 per
     pixel** (this is TLinear-calibrated absolute temperature straight from
     the sensor — no host-side Planck/AGC math needed for accuracy)
   - the bottom 2 rows, handed to `telemetry.parse_telemetry`

   Frames are pushed through a `queue.Queue(maxsize=2)`; on `queue.Full` the
   oldest frame is dropped rather than blocking, so a slow UI never causes
   the capture thread to back up — the queue always holds only the newest
   frame(s). `LeptonCapture.get_frame()` is what the UI polls.

2. **`telemetry.py`** — `parse_telemetry` decodes the 320-word (2-row)
   telemetry footer per FLIR Lepton Engineering Datasheet Rev 400, Table
   2/3, into a `Telemetry` dataclass (FPA/housing temp, FFC state, uptime,
   frame counter, spotmeter stats, etc). Word offsets are hardcoded from the
   datasheet and unlikely to need touching except for the one flagged
   ambiguity: `WORD_ORDER_MSW_FIRST` — the datasheet doesn't explicitly
   state word order for the multi-word `TimeCounter`/`FrameCounter` fields
   (unlike the status-bits field, which is explicit and correct as-is). If
   uptime/frame-counter values look wrong on real hardware (jumping by
   65536, or garbage), flip that flag.

3. **`render.py`** — pure functions turning `image_k100` into display
   output and temperature readouts. No Qt/UI dependency, so it's testable
   standalone. Key pieces:
   - `TemporalSmoother`: EMA across frames applied to raw Kelvin×100 data
     (before display processing) to cut sensor noise, since noise gets
     visually amplified once the 160×120 image is stretched 4-5x to fill a
     display.
   - `render_frame`: min/max-stretches the *current frame's* temperature
     range to 0-255 for **display contrast only** — this never affects the
     actual temperature values read out elsewhere (those always come from
     `image_k100`/`temp_at_c`/`scene_stats_c`/`region_stats_c` directly).
     Order matters here: unsharp-mask at native resolution → upscale with
     cubic interpolation → colorize (sharpening after upscale would just
     sharpen interpolation artifacts; colorizing before upscale would give
     an interpolated-color halo at edges).
   - `temp_at_c` / `scene_stats_c` / `region_stats_c`: spot and region
     temperature lookups directly from the calibrated Kelvin data, used for
     the tap-to-read spot meter and drag-to-select ROI.
   - `draw_scale_bar` / `draw_crosshair` / `draw_roi`: OpenCV overlay
     drawing, mutate the BGR image in place and return it.

4. **`main.py`** — PyQt5 `MainWindow`. Polls `LeptonCapture` on a
   `QTimer` (110ms, a bit slower than the sensor's true ~8.7Hz to avoid
   busy-polling), runs each frame through the smoother and `render_frame`,
   draws overlays, and updates the sidebar's live-metrics labels.
   - `ThermalView` (a `QLabel` subclass) handles tap-vs-drag detection:
     a plain tap reports a spot-meter point, dragging past
     `DRAG_THRESHOLD_PX` (in native image pixels, not screen pixels)
     switches to reporting a live-updating ROI rectangle instead. Screen
     coordinates are translated back to native 160×120 image coordinates
     accounting for letterboxing (`_to_image_xy`) — a no-op in practice
     since `SIDEBAR_WIDTH` is chosen so the video panel is exactly 640×480
     (see below), but kept as the general mechanism in case that ever
     changes.
   - **Sidebar sizing**: there is no dynamic scaling anymore — the window
     is a fixed `BASE_WINDOW_W x BASE_WINDOW_H` (800×480) on every
     platform (see "Running" above). `SIDEBAR_WIDTH` is *derived*, not a
     free constant: `BASE_WINDOW_W - BASE_WINDOW_H * IMG_WIDTH // IMG_HEIGHT`
     gives the video panel exactly a 4:3, 640×480 area with zero letterbox
     gaps, and the sidebar gets whatever's left (160px). Don't widen the
     sidebar without shrinking the video to match, or the top/bottom
     letterbox gaps come back. The rest of the sidebar's sizing (fonts,
     button heights, spacing) are plain pixel constants (`TITLE_PX`,
     `BUTTON_HEIGHT`, `MARGIN`, etc. near the top of `main.py`) tuned to
     fit inside that narrow 160px width and `BASE_WINDOW_H` tall without a
     scrollbar — e.g. the PROCESSING buttons are stacked full-width rather
     than a 2-column grid because "Smoothing: Heavy" doesn't fit in an
     ~70px half-width cell. `MainWindow.__init__` asserts the sidebar's
     `sizeHint()` fits within `BASE_WINDOW_H` — if you add sidebar
     content, either trim something else or that assertion fires
     immediately at startup rather than silently clipping a button
     off-screen. Every `QGridLayout` nested inside the sidebar (`grid`,
     `palette_grid`, `actions_grid`) has `setContentsMargins(0, 0, 0, 0)`
     explicitly — without it, Qt's default per-layout margin silently
     adds ~18px each, which is what was blowing the height budget and
     forcing a scrollbar before this was fixed.

## Pi 3 performance constraints (why some things look "unoptimized")

The Lepton sensor only outputs ~8.7 unique frames/sec, well within a Pi 3's
budget — the bottleneck is Qt image scaling and touch handling, not the
sensor. Several choices in the code exist specifically for this:
- `Qt.FastTransformation` (nearest-neighbor) in `ThermalView.show_bgr`
  instead of smooth scaling — deliberate, not an oversight.
- The capture thread drops stale frames instead of queuing (see above).
- PyQt5 rather than PyQt6, for better legacy driver support on the Pi 3's
  VideoCore IV GPU.

Don't "fix" these toward more CPU/GPU-expensive defaults without checking
with the user first — they're tuned for the target hardware, not this dev
machine.

## Windows vs. Linux dev loop

This is being developed on Windows but deployed on Raspberry Pi OS
(Linux/V4L2). `capture.py`, `main.py` branch on `platform.system()` /
`IS_WINDOWS` for backend selection (`CAP_DSHOW` vs `CAP_V4L2`) and device
auto-detection (Linux-only, via sysfs). When changing capture logic, keep
both paths working, and note that device auto-detect can only actually be
verified on Linux.

## Context Navigation

When you need to understand the codebase, docs, or any files in this project:
1. ALWAYS query the knowledge graph first: `/graphify query "your question"`
2. Only read raw files if I explicitly say "read the file" or "look at the raw file"
3. Use `graphify-out/wiki/index.md` as your navigation entrypoint for browsing structure
