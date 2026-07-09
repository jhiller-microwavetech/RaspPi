import sys

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QMouseEvent, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QPushButton,
    QButtonGroup,
    QMessageBox,
    QSizePolicy,
)

from capture import LeptonCapture, ThermalFrame, IMG_WIDTH, IMG_HEIGHT
from render import (
    Palette,
    TemporalSmoother,
    render_frame,
    temp_at_c,
    scene_stats_c,
    region_stats_c,
    celsius_to_fahrenheit,
    draw_scale_bar,
    draw_crosshair,
    draw_roi,
)

POLL_MS = 110  # a bit slower than the 8.7 Hz sensor rate, avoids busy-polling

# Official Raspberry Pi Foundation 7" Touch Display resolution. The app
# window is fixed at exactly this size on every platform -- it's the only
# size this UI ever runs at, so every size below is a plain constant tuned
# to fit that one size, not a scale-derived value. The sidebar's natural
# height is asserted (see MainWindow.__init__) to fit within the window
# without scrolling -- if you add sidebar content, either trim something
# else or that assertion will fire.
BASE_WINDOW_W = 800
BASE_WINDOW_H = 480
# 4:3 sensor image needs exactly BASE_WINDOW_H * 4/3 = 640px to fill the
# window's full height with no letterbox gaps -- the sidebar gets whatever
# is left over, not the other way around. Don't widen this without also
# shrinking the video, or the letterbox gaps come back.
SIDEBAR_WIDTH = BASE_WINDOW_W - (BASE_WINDOW_H * IMG_WIDTH // IMG_HEIGHT)

TITLE_PX = 13
SECTION_PX = 9
LABEL_PX = 9
VALUE_PX = 10
BUTTON_PX = 10
BUTTON_HEIGHT = 27
MARGIN = 5
SPACING = 1
GRID_H_SPACING = 8
GRID_V_SPACING = 0
PALETTE_SPACING = 3

_STYLESHEET = f"""
QMainWindow, QWidget#Central {{ background-color: #121212; }}

QWidget#Sidebar {{
    background-color: #191919;
    border-left: 1px solid #2a2a2a;
}}

QLabel#Title {{
    color: #e5e5e5;
    font-size: {TITLE_PX}px;
    font-weight: 700;
}}

QLabel[role="sectionTitle"] {{
    color: #7a7a7a;
    font-size: {SECTION_PX}px;
    font-weight: 600;
}}

QLabel[role="metricLabel"] {{
    color: #9a9a9a;
    font-size: {LABEL_PX}px;
}}

QLabel[role="metricValue"] {{
    color: #f2f2f2;
    font-size: {VALUE_PX}px;
    font-weight: 600;
}}

QFrame[role="divider"] {{
    background-color: #2a2a2a;
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

QPushButton {{
    background-color: #232323;
    color: #d5d5d5;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 2px 4px;
    font-size: {BUTTON_PX}px;
}}
QPushButton:hover {{ background-color: #2b2b2b; }}
QPushButton:pressed {{ background-color: #181818; }}
QPushButton:checkable:checked {{
    background-color: #2f6fb0;
    border: 1px solid #4a8fd4;
    color: white;
    font-weight: 600;
}}

QPushButton#ffcButton {{
    background-color: #2c2410;
    border: 1px solid #55461c;
    color: #e8c97a;
}}
QPushButton#ffcButton:hover {{ background-color: #382e14; }}

QPushButton#quitButton {{
    background-color: #2a1414;
    border: 1px solid #4d1f1f;
    color: #e08080;
}}
QPushButton#quitButton:hover {{ background-color: #351818; }}
"""


def _mono_font() -> QFont:
    # Must be constructed after QApplication exists -- QFont matching
    # touches the platform font database, which segfaults if called at
    # module import time before a QGuiApplication is running.
    font = QFont("Consolas")
    if not font.exactMatch():
        font = QFont("DejaVu Sans Mono")
    font.setStyleHint(QFont.Monospace)
    return font


class StatusDot(QLabel):
    """Small colored circle indicating a state at a glance (FFC, connection, etc)."""

    def __init__(self, diameter: int = 10):
        super().__init__()
        self._d = diameter
        self.setFixedSize(diameter, diameter)
        self.set_color("#555555")

    def set_color(self, hex_color: str):
        self.setStyleSheet(
            f"background-color: {hex_color}; border-radius: {self._d // 2}px;"
        )


class ThermalView(QLabel):
    """
    Displays the false-color image. A plain tap reports an (x, y) point
    in native image coords via on_tap; dragging past a small threshold
    reports a live-updating rectangle via on_drag instead.
    """

    DRAG_THRESHOLD_PX = 3  # native-image pixels before a tap becomes a drag

    def __init__(self, on_tap, on_drag):
        super().__init__()
        self._on_tap = on_tap
        self._on_drag = on_drag
        self._last_bgr = None
        self._press_xy = None
        self._dragging = False
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color: black;")

    def show_bgr(self, bgr: np.ndarray):
        self._last_bgr = bgr
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(
            self.width(), self.height(), Qt.KeepAspectRatio, Qt.FastTransformation
        )
        self.setPixmap(pix)

    def _to_image_xy(self, event: QMouseEvent):
        if self.pixmap() is None:
            return None
        pm = self.pixmap()
        # Account for letterboxing: the pixmap is centered in the label.
        off_x = (self.width() - pm.width()) / 2
        off_y = (self.height() - pm.height()) / 2
        px = event.x() - off_x
        py = event.y() - off_y
        if not (0 <= px < pm.width() and 0 <= py < pm.height()):
            return None
        img_x = int(px / pm.width() * IMG_WIDTH)
        img_y = int(py / pm.height() * IMG_HEIGHT)
        return img_x, img_y

    def mousePressEvent(self, event: QMouseEvent):
        xy = self._to_image_xy(event)
        if xy is None:
            return
        self._press_xy = xy
        self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._press_xy is None:
            return
        xy = self._to_image_xy(event)
        if xy is None:
            return
        dx = abs(xy[0] - self._press_xy[0])
        dy = abs(xy[1] - self._press_xy[1])
        if self._dragging or dx >= self.DRAG_THRESHOLD_PX or dy >= self.DRAG_THRESHOLD_PX:
            self._dragging = True
            self._on_drag(self._press_xy[0], self._press_xy[1], xy[0], xy[1])

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._press_xy is None:
            return
        if self._dragging:
            xy = self._to_image_xy(event) or self._press_xy
            self._on_drag(self._press_xy[0], self._press_xy[1], xy[0], xy[1])
        else:
            self._on_tap(*self._press_xy)
        self._press_xy = None
        self._dragging = False


class MainWindow(QMainWindow):
    def __init__(self, device: str = None):
        super().__init__()
        self.setWindowTitle("Thermal Viewer")
        # WindowStaysOnTopHint matters on Raspberry Pi OS: the LXDE/PIXEL
        # desktop panel reserves screen space via an EWMH strut, so a
        # normally-placed window gets pushed down below it (which is what
        # was clipping the bottom of the sidebar on real hardware) unless
        # it explicitly paints above the panel. Combined with the explicit
        # move(0, 0) in main() (WMs auto-place new windows inside the
        # strut-reduced work area, ignoring a naive geometry request).
        self.setWindowFlags(
            self.windowFlags() | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setFixedSize(BASE_WINDOW_W, BASE_WINDOW_H)
        self._mono_font = _mono_font()
        self.setStyleSheet(_STYLESHEET)

        self.capture = LeptonCapture(device=device)
        self.palette = Palette.IRON
        self.spot_xy = None
        self.roi = None  # (x0, y0, x1, y1) in native 160x120 coords, or None
        self.fahrenheit = False
        self.latest_frame: ThermalFrame = None
        self.smoother = TemporalSmoother(alpha=0.5)
        self.sharpen_amount = 0.6

        self.view = ThermalView(self._on_tap, self._on_drag)
        self.sidebar_widget = self._build_sidebar()

        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.view, stretch=1)
        root.addWidget(self.sidebar_widget)

        central = QWidget()
        central.setObjectName("Central")
        central.setLayout(root)
        self.setCentralWidget(central)

        # The window is a fixed BASE_WINDOW_W x BASE_WINDOW_H, so this is a
        # one-time layout sanity check, not a live constraint: if sidebar
        # content ever grows past the window's height, fail loudly here
        # instead of silently clipping buttons off the bottom at runtime.
        natural_h = self.sidebar_widget.sizeHint().height()
        assert natural_h <= BASE_WINDOW_H, (
            f"Sidebar content ({natural_h}px) is taller than the fixed "
            f"{BASE_WINDOW_H}px window -- trim content or shrink sizing "
            f"constants at the top of main.py."
        )

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # --- sidebar construction -----------------------------------------

    def _build_sidebar(self) -> QWidget:
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        sidebar.setSpacing(SPACING)

        title = QLabel("THERMAL VIEWER")
        title.setObjectName("Title")
        sidebar.addWidget(title)

        self.btn_units = self._make_button("Units: \u00b0C", self._toggle_units)
        sidebar.addWidget(self.btn_units)

        # --- live readings grid ---
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(GRID_H_SPACING)
        grid.setVerticalSpacing(GRID_V_SPACING)
        row = 0

        row = self._section(sidebar, grid, row, "TEMPERATURE")
        self.val_fpa = self._metric(grid, row, "FPA"); row += 1
        self.val_housing = self._metric(grid, row, "Housing"); row += 1
        self.val_spot = self._metric(grid, row, "Spot (tap image)"); row += 1

        row = self._section(sidebar, grid, row, "SCENE")
        self.val_min = self._metric(grid, row, "Min"); row += 1
        self.val_max = self._metric(grid, row, "Max"); row += 1
        self.val_mean = self._metric(grid, row, "Mean"); row += 1

        row = self._section(sidebar, grid, row, "REGION (drag to select)")
        self.val_roi_min = self._metric(grid, row, "Min"); row += 1
        self.val_roi_max = self._metric(grid, row, "Max"); row += 1
        self.val_roi_mean = self._metric(grid, row, "Avg"); row += 1

        row = self._section(sidebar, grid, row, "STATUS")
        self.dot_ffc = StatusDot()
        ffc_label = QLabel("FFC")
        ffc_label.setProperty("role", "metricLabel")
        self.val_ffc = QLabel("--")
        self.val_ffc.setProperty("role", "metricValue")
        self.val_ffc.setFont(self._mono_font)
        ffc_row = QHBoxLayout()
        ffc_row.setContentsMargins(0, 0, 0, 0)
        ffc_row.addWidget(self.dot_ffc)
        ffc_row.addWidget(ffc_label)
        ffc_row_widget = QWidget()
        ffc_row_widget.setLayout(ffc_row)
        grid.addWidget(ffc_row_widget, row, 0)
        grid.addWidget(self.val_ffc, row, 1, alignment=Qt.AlignRight)
        row += 1

        self.val_uptime = self._metric(grid, row, "Uptime"); row += 1
        self.val_frame = self._metric(grid, row, "Frame #"); row += 1

        grid_widget = QWidget()
        grid_widget.setLayout(grid)
        sidebar.addWidget(grid_widget)
        sidebar.addWidget(self._divider())

        # --- palette selector ---
        sidebar.addWidget(self._section_title("PALETTE"))
        palette_grid = QGridLayout()
        palette_grid.setContentsMargins(0, 0, 0, 0)
        palette_grid.setSpacing(PALETTE_SPACING)
        self.palette_group = QButtonGroup(self)
        self.palette_group.setExclusive(True)
        palette_defs = [
            ("Iron", Palette.IRON),
            ("Rainbow", Palette.RAINBOW),
            ("White Hot", Palette.WHITE_HOT),
            ("Black Hot", Palette.BLACK_HOT),
        ]
        for i, (label, pal) in enumerate(palette_defs):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(pal == self.palette)
            btn.setMinimumHeight(BUTTON_HEIGHT)
            btn.clicked.connect(lambda _checked, p=pal: self._set_palette(p))
            self.palette_group.addButton(btn)
            palette_grid.addWidget(btn, i // 2, i % 2)
        palette_grid_widget = QWidget()
        palette_grid_widget.setLayout(palette_grid)
        sidebar.addWidget(palette_grid_widget)
        sidebar.addWidget(self._divider())

        # --- processing --- (stacked full-width: at SIDEBAR_WIDTH's 160px,
        # a 2-column half-width button can't fit "Smoothing: Heavy" etc)
        sidebar.addWidget(self._section_title("PROCESSING"))
        self.btn_smooth = self._make_button("Smoothing: Med", self._toggle_smoothing)
        self.btn_sharpen = self._make_button("Sharpen: Med", self._cycle_sharpen)
        btn_clear_roi = self._make_button("Clear ROI / Spot", self._clear_selection)
        sidebar.addWidget(self.btn_smooth)
        sidebar.addWidget(self.btn_sharpen)
        sidebar.addWidget(btn_clear_roi)
        sidebar.addWidget(self._divider())

        sidebar.addStretch(1)

        # --- actions --- (also two-column, same reasoning as PROCESSING)
        actions_grid = QGridLayout()
        actions_grid.setContentsMargins(0, 0, 0, 0)
        actions_grid.setSpacing(PALETTE_SPACING)
        btn_ffc = self._make_button("Run FFC", self._on_ffc_clicked)
        btn_ffc.setObjectName("ffcButton")
        btn_quit = self._make_button("Quit", self.close)
        btn_quit.setObjectName("quitButton")
        actions_grid.addWidget(btn_ffc, 0, 0)
        actions_grid.addWidget(btn_quit, 0, 1)
        actions_grid_widget = QWidget()
        actions_grid_widget.setLayout(actions_grid)
        sidebar.addWidget(actions_grid_widget)

        content = QWidget()
        content.setObjectName("Sidebar")
        content.setLayout(sidebar)
        content.setFixedWidth(SIDEBAR_WIDTH)
        content.setFixedHeight(BASE_WINDOW_H)
        return content

    def _section(self, parent_layout, grid, row, title) -> int:
        """Adds a section title spanning both grid columns, returns next row."""
        lbl = self._section_title(title)
        grid.addWidget(lbl, row, 0, 1, 2)
        return row + 1

    @staticmethod
    def _section_title(text) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "sectionTitle")
        return lbl

    def _metric(self, grid: QGridLayout, row: int, label_text: str) -> QLabel:
        """Adds a label/value row to the grid, returns the value QLabel to update later."""
        label = QLabel(label_text)
        label.setProperty("role", "metricLabel")
        value = QLabel("--")
        value.setProperty("role", "metricValue")
        value.setFont(self._mono_font)
        grid.addWidget(label, row, 0)
        grid.addWidget(value, row, 1, alignment=Qt.AlignRight)
        return value

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setProperty("role", "divider")
        line.setFrameShape(QFrame.HLine)
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return line

    def _make_button(self, text, slot):
        btn = QPushButton(text)
        btn.setMinimumHeight(BUTTON_HEIGHT)
        btn.clicked.connect(slot)
        return btn

    # --- behavior -------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def start(self):
        self.capture.start()
        self.timer.start(POLL_MS)

    def closeEvent(self, event):
        self.timer.stop()
        self.capture.stop()
        super().closeEvent(event)

    def _set_palette(self, palette: Palette):
        self.palette = palette

    def _toggle_units(self):
        self.fahrenheit = not self.fahrenheit
        self.btn_units.setText("Units: \u00b0F" if self.fahrenheit else "Units: \u00b0C")

    def _fmt_temp(self, celsius: float) -> str:
        if self.fahrenheit:
            return f"{celsius_to_fahrenheit(celsius):.1f} \u00b0F"
        return f"{celsius:.1f} \u00b0C"

    def _toggle_smoothing(self):
        levels = [("Off", 1.0), ("Light", 0.75), ("Med", 0.5), ("Heavy", 0.3)]
        names = [n for n, _ in levels]
        current = self.btn_smooth.text().split(": ")[1]
        next_idx = (names.index(current) + 1) % len(levels)
        name, alpha = levels[next_idx]
        self.smoother.alpha = alpha
        self.smoother.reset()
        self.btn_smooth.setText(f"Smoothing: {name}")

    def _cycle_sharpen(self):
        levels = [("Off", 0.0), ("Low", 0.3), ("Med", 0.6), ("High", 1.0)]
        names = [n for n, _ in levels]
        current = self.btn_sharpen.text().split(": ")[1]
        next_idx = (names.index(current) + 1) % len(levels)
        name, amount = levels[next_idx]
        self.sharpen_amount = amount
        self.btn_sharpen.setText(f"Sharpen: {name}")

    def _on_tap(self, x, y):
        self.spot_xy = (x, y)
        self.roi = None

    def _on_drag(self, x0, y0, x1, y1):
        self.roi = (x0, y0, x1, y1)
        self.spot_xy = None

    def _clear_selection(self):
        self.spot_xy = None
        self.roi = None

    def _on_ffc_clicked(self):
        # Triggering FFC requires sending a Lepton CCI command through the
        # PureThermal UVC extension unit. That needs the board's exact
        # extension-unit GUID / control-selector values, which differ by
        # firmware build and which I didn't have verified hardware access
        # to confirm -- so it's deliberately not wired up here rather than
        # guessing at register values.
        #
        # See GroupGets' reference implementation for the real values:
        #   https://github.com/groupgets/purethermal1-uvc-capture
        # (the ctypes/libuvc example there does CCI read/write over the XU)
        QMessageBox.information(
            self,
            "Not wired up",
            "FFC trigger isn't implemented yet -- it needs CCI-over-UVC "
            "extension-unit calls specific to your firmware build. See the "
            "comment in _on_ffc_clicked in main.py for the reference to "
            "adapt.",
        )

    _FFC_COLORS = {
        "NEVER_COMMANDED": "#555555",
        "IMMINENT": "#d4a72c",
        "IN_PROGRESS": "#d4a72c",
        "COMPLETE": "#3fae4a",
    }

    def _tick(self):
        frame = self.capture.get_frame(timeout=0.0)
        if frame is None:
            return
        self.latest_frame = frame

        smoothed_k100 = self.smoother.update(frame.image_k100)

        bgr, (lo_c, hi_c) = render_frame(
            smoothed_k100, palette=self.palette, sharpen_amount=self.sharpen_amount
        )

        draw_scale_bar(bgr, self.palette, self._fmt_temp(lo_c), self._fmt_temp(hi_c))

        if self.spot_xy:
            x, y = self.spot_xy
            t = temp_at_c(smoothed_k100, x, y)
            draw_crosshair(bgr, (IMG_WIDTH, IMG_HEIGHT), (x, y), self._fmt_temp(t))
            self.val_spot.setText(self._fmt_temp(t))
        else:
            self.val_spot.setText("--")

        if self.roi:
            roi_stats = region_stats_c(smoothed_k100, *self.roi)
            label = (
                f"Min {self._fmt_temp(roi_stats['min_c'])}  "
                f"Max {self._fmt_temp(roi_stats['max_c'])}"
            )
            draw_roi(bgr, (IMG_WIDTH, IMG_HEIGHT), self.roi, label)
            self.val_roi_min.setText(self._fmt_temp(roi_stats["min_c"]))
            self.val_roi_max.setText(self._fmt_temp(roi_stats["max_c"]))
            self.val_roi_mean.setText(self._fmt_temp(roi_stats["mean_c"]))
        else:
            self.val_roi_min.setText("--")
            self.val_roi_max.setText("--")
            self.val_roi_mean.setText("--")

        self.view.show_bgr(bgr)

        stats = scene_stats_c(smoothed_k100)
        self.val_min.setText(self._fmt_temp(stats["min_c"]))
        self.val_max.setText(self._fmt_temp(stats["max_c"]))
        self.val_mean.setText(self._fmt_temp(stats["mean_c"]))

        tel = frame.telemetry
        if tel is not None:
            self.val_fpa.setText(self._fmt_temp(tel.fpa_temp_c))
            self.val_housing.setText(self._fmt_temp(tel.housing_temp_c))
            self.val_ffc.setText(tel.ffc_state.name.replace("_", " "))
            self.dot_ffc.set_color(self._FFC_COLORS.get(tel.ffc_state.name, "#555555"))
            self.val_uptime.setText(f"{tel.uptime_ms / 1000:.0f} s")
            self.val_frame.setText(str(tel.frame_counter))


def main():
    args = sys.argv[1:]
    device = None
    if args:
        arg = args[0]
        device = int(arg) if arg.isdigit() else arg

    app = QApplication(sys.argv)
    win = MainWindow(device=device)
    # Force the physical top-left corner rather than trusting the window
    # manager's auto-placement, which on Raspberry Pi OS keeps new windows
    # inside the desktop panel's strut-reduced work area by default.
    win.move(0, 0)
    win.show()
    win.raise_()
    win.activateWindow()
    win.start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

