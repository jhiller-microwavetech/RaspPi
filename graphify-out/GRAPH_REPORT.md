# Graph Report - pt_thermal_viewer_final  (2026-07-09)

## Corpus Check
- 7 files · ~7,030 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 128 nodes · 221 edges · 8 communities (7 shown, 1 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 25 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a87a6555`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- render.py
- MainWindow
- Telemetry
- LeptonCapture
- .__init__
- PureThermal Mini Pro (Lepton 3.5) Viewer — Pi 3 + 7" touchscreen
- ThermalView
- CLAUDE.md

## God Nodes (most connected - your core abstractions)
1. `MainWindow` - 27 edges
2. `ThermalView` - 14 edges
3. `LeptonCapture` - 12 edges
4. `StatusDot` - 10 edges
5. `PureThermal Mini Pro (Lepton 3.5) Viewer — Pi 3 + 7" touchscreen` - 10 edges
6. `Palette` - 9 edges
7. `TemporalSmoother` - 9 edges
8. `Telemetry` - 9 edges
9. `render_frame()` - 8 edges
10. `draw_scale_bar()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `ThermalFrame` --uses--> `Telemetry`  [INFERRED]
  pt_thermal_viewer/src/capture.py → pt_thermal_viewer/src/telemetry.py
- `MainWindow` --uses--> `ThermalFrame`  [INFERRED]
  pt_thermal_viewer/src/main.py → pt_thermal_viewer/src/capture.py
- `ThermalView` --uses--> `ThermalFrame`  [INFERRED]
  pt_thermal_viewer/src/main.py → pt_thermal_viewer/src/capture.py
- `LeptonCapture` --uses--> `Telemetry`  [INFERRED]
  pt_thermal_viewer/src/capture.py → pt_thermal_viewer/src/telemetry.py
- `MainWindow` --uses--> `LeptonCapture`  [INFERRED]
  pt_thermal_viewer/src/main.py → pt_thermal_viewer/src/capture.py

## Import Cycles
- None detected.

## Communities (8 total, 1 thin omitted)

### Community 0 - "render.py"
Cohesion: 0.15
Nodes (25): Enum, celsius_to_fahrenheit(), _colorize(), draw_crosshair(), draw_roi(), draw_scale_bar(), k100_to_celsius(), _label_bg() (+17 more)

### Community 1 - "MainWindow"
Cohesion: 0.12
Nodes (8): main(), MainWindow, Adds a section title spanning both grid columns, returns next row., Adds a label/value row to the grid, returns the value QLabel to update later., QFrame, QGridLayout, QLabel, QMainWindow

### Community 2 - "Telemetry"
Cohesion: 0.17
Nodes (10): IntEnum, _combine32(), FFCState, GainMode, parse_telemetry(), ndarray, Parses the Lepton 3.5 telemetry footer/header.  Word offsets below are taken dir, raw_words: 1D uint16 numpy array, length >= 240 (the telemetry footer     or hea (+2 more)

### Community 3 - "LeptonCapture"
Cohesion: 0.14
Nodes (10): DeviceRef, find_purethermal_device(), find_windows_cameras(), LeptonCapture, Frame capture from a PureThermal Mini Pro (Lepton 3.5) UVC device.  Assumes the, Linux: scans /dev/video* sysfs names for "PureThermal"/"Lepton"/"FLIR".     Wind, Debug helper: probes camera indices 0..max_index-1 and reports which     ones op, ThermalFrame (+2 more)

### Community 5 - "PureThermal Mini Pro (Lepton 3.5) Viewer — Pi 3 + 7" touchscreen"
Cohesion: 0.18
Nodes (10): Autostart on boot (kiosk mode), File layout, How it works, Install (Raspberry Pi OS, Pi 3 Model B), Known things to verify once you have real frames, One-time board configuration, Performance notes for the Pi 3, PureThermal Mini Pro (Lepton 3.5) Viewer — Pi 3 + 7" touchscreen (+2 more)

### Community 7 - "ThermalView"
Cohesion: 0.20
Nodes (7): _mono_font(), ndarray, Displays the false-color image. A plain tap reports an (x, y) point     in nativ, ThermalView, QFont, QMouseEvent, QWidget

### Community 8 - "CLAUDE.md"
Cohesion: 0.22
Nodes (7): Context Navigation, Data pipeline / architecture, Pi 3 performance constraints (why some things look "unoptimized"), Required board configuration (not done by this code), Running, What this is, Windows vs. Linux dev loop

## Knowledge Gaps
- **16 isolated node(s):** `What this is`, `Running`, `Required board configuration (not done by this code)`, `Data pipeline / architecture`, `Pi 3 performance constraints (why some things look "unoptimized")` (+11 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MainWindow` connect `MainWindow` to `render.py`, `LeptonCapture`, `.__init__`, `ThermalView`?**
  _High betweenness centrality (0.278) - this node is a cross-community bridge._
- **Why does `LeptonCapture` connect `LeptonCapture` to `MainWindow`, `Telemetry`, `ThermalView`?**
  _High betweenness centrality (0.142) - this node is a cross-community bridge._
- **Why does `ThermalView` connect `ThermalView` to `render.py`, `MainWindow`, `LeptonCapture`, `.__init__`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `MainWindow` (e.g. with `LeptonCapture` and `ThermalFrame`) actually correct?**
  _`MainWindow` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `ThermalView` (e.g. with `LeptonCapture` and `ThermalFrame`) actually correct?**
  _`ThermalView` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `LeptonCapture` (e.g. with `Telemetry` and `MainWindow`) actually correct?**
  _`LeptonCapture` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `StatusDot` (e.g. with `LeptonCapture` and `ThermalFrame`) actually correct?**
  _`StatusDot` has 4 INFERRED edges - model-reasoned connections that need verification._