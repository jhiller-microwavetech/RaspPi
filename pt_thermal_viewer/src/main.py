import sys

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QMouseEvent
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QMessageBox,
)

from capture import LeptonCapture, ThermalFrame, IMG_WIDTH, IMG_HEIGHT
from render import Palette, render_frame, temp_at_c, scene_stats_c

POLL_MS = 110  # a bit slower than the 8.7 Hz sensor rate, avoids busy-polling


class ThermalView(QLabel):
    """Displays the false-color image and reports taps in image (x, y) coords."""

    def __init__(self, on_tap):
        super().__init__()
        self._on_tap = on_tap
        self._last_bgr = None
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

    def mousePressEvent(self, event: QMouseEvent):
        if self.pixmap() is None:
            return
        pm = self.pixmap()
        # Account for letterboxing: the pixmap is centered in the label.
        off_x = (self.width() - pm.width()) / 2
        off_y = (self.height() - pm.height()) / 2
        px = event.x() - off_x
        py = event.y() - off_y
        if not (0 <= px < pm.width() and 0 <= py < pm.height()):
            return
        img_x = int(px / pm.width() * IMG_WIDTH)
        img_y = int(py / pm.height() * IMG_HEIGHT)
        self._on_tap(img_x, img_y)


class MainWindow(QMainWindow):
    def __init__(self, device: str = None):
        super().__init__()
        self.setWindowTitle("Thermal Viewer")

        self.capture = LeptonCapture(device=device)
        self.palette = Palette.IRON
        self.spot_xy = None
        self.latest_frame: ThermalFrame = None

        self.view = ThermalView(self._on_tap)

        # --- sidebar -------------------------------------------------
        self.lbl_fpa = QLabel("FPA: -- C")
        self.lbl_housing = QLabel("Housing: -- C")
        self.lbl_scene = QLabel("Scene min/max: -- / --")
        self.lbl_spot = QLabel("Spot: tap image")
        self.lbl_ffc = QLabel("FFC: --")
        self.lbl_uptime = QLabel("Uptime: --")
        self.lbl_frame = QLabel("Frame #: --")
        for lbl in (
            self.lbl_fpa,
            self.lbl_housing,
            self.lbl_scene,
            self.lbl_spot,
            self.lbl_ffc,
            self.lbl_uptime,
            self.lbl_frame,
        ):
            lbl.setStyleSheet("font-size: 20px; color: white;")

        btn_iron = self._make_button("Iron", lambda: self._set_palette(Palette.IRON))
        btn_rainbow = self._make_button("Rainbow", lambda: self._set_palette(Palette.RAINBOW))
        btn_white = self._make_button("White Hot", lambda: self._set_palette(Palette.WHITE_HOT))
        btn_black = self._make_button("Black Hot", lambda: self._set_palette(Palette.BLACK_HOT))
        btn_ffc = self._make_button("Run FFC", self._on_ffc_clicked)
        btn_quit = self._make_button("Quit", self.close)

        sidebar = QVBoxLayout()
        sidebar.addWidget(self.lbl_fpa)
        sidebar.addWidget(self.lbl_housing)
        sidebar.addWidget(self.lbl_scene)
        sidebar.addWidget(self.lbl_spot)
        sidebar.addWidget(self.lbl_ffc)
        sidebar.addWidget(self.lbl_uptime)
        sidebar.addWidget(self.lbl_frame)
        sidebar.addStretch(1)
        sidebar.addWidget(btn_iron)
        sidebar.addWidget(btn_rainbow)
        sidebar.addWidget(btn_white)
        sidebar.addWidget(btn_black)
        sidebar.addWidget(btn_ffc)
        sidebar.addWidget(btn_quit)

        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setFixedWidth(220)
        sidebar_widget.setStyleSheet("background-color: #202020;")

        root = QHBoxLayout()
        root.addWidget(self.view, stretch=1)
        root.addWidget(sidebar_widget)

        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _make_button(text, slot):
        btn = QPushButton(text)
        btn.setMinimumHeight(56)
        btn.setStyleSheet("font-size: 18px;")
        btn.clicked.connect(slot)
        return btn

    def start(self):
        self.capture.start()
        self.timer.start(POLL_MS)

    def closeEvent(self, event):
        self.timer.stop()
        self.capture.stop()
        super().closeEvent(event)

    def _set_palette(self, palette: Palette):
        self.palette = palette

    def _on_tap(self, x, y):
        self.spot_xy = (x, y)

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

    def _tick(self):
        frame = self.capture.get_frame(timeout=0.0)
        if frame is None:
            return
        self.latest_frame = frame

        bgr = render_frame(frame.image_k100, palette=self.palette)
        self.view.show_bgr(bgr)

        stats = scene_stats_c(frame.image_k100)
        self.lbl_scene.setText(f"Scene min/max: {stats['min_c']:.1f} / {stats['max_c']:.1f} C")

        if self.spot_xy:
            x, y = self.spot_xy
            t = temp_at_c(frame.image_k100, x, y)
            self.lbl_spot.setText(f"Spot ({x},{y}): {t:.1f} C")

        tel = frame.telemetry
        if tel is not None:
            self.lbl_fpa.setText(f"FPA: {tel.fpa_temp_c:.1f} C")
            self.lbl_housing.setText(f"Housing: {tel.housing_temp_c:.1f} C")
            self.lbl_ffc.setText(f"FFC: {tel.ffc_state.name}")
            self.lbl_uptime.setText(f"Uptime: {tel.uptime_ms / 1000:.0f} s")
            self.lbl_frame.setText(f"Frame #: {tel.frame_counter}")


def main():
    args = sys.argv[1:]
    windowed = "--windowed" in args
    args = [a for a in args if a != "--windowed"]
    device = None
    if args:
        arg = args[0]
        device = int(arg) if arg.isdigit() else arg

    app = QApplication(sys.argv)
    win = MainWindow(device=device)
    if windowed:
        win.resize(1000, 500)
        win.show()
    else:
        win.showFullScreen()
    win.start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
