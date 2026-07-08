"""
Frame capture from a PureThermal Mini Pro (Lepton 3.5) UVC device.

Assumes the board has already been configured (once, persists across
power cycles) for:
  - Video Output Format: Raw14
  - Radiometry + TLinear: enabled, resolution 0.01 K/LSB
  - Telemetry: enabled, location = Footer

See configure_lepton.py to set this up the first time, or set it via
GetThermal (https://github.com/groupgets/GetThermal) once.

With that configuration, the UVC device streams 16-bit greyscale
frames sized 160 x 122: the top 120 rows are TLinear pixel data
(Kelvin x 100 per pixel) and the bottom 2 rows are the telemetry
footer (see telemetry.py).
"""

import glob
import platform
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional, Union

import cv2
import numpy as np

from telemetry import Telemetry, TELEMETRY_ROWS, parse_telemetry

IMG_WIDTH = 160
IMG_HEIGHT = 120
FRAME_HEIGHT = IMG_HEIGHT + TELEMETRY_ROWS  # 122

IS_WINDOWS = platform.system() == "Windows"

DeviceRef = Union[str, int]


@dataclass
class ThermalFrame:
    image_k100: np.ndarray   # uint16, (120, 160), value = Kelvin * 100
    telemetry: Optional[Telemetry]
    timestamp: float


def find_purethermal_device() -> Optional[DeviceRef]:
    """
    Linux: scans /dev/video* sysfs names for "PureThermal"/"Lepton"/"FLIR".
    Windows: OpenCV has no reliable cross-backend way to query a camera's
    name before opening it, so there's nothing to auto-detect here --
    pass the device index explicitly (see find_windows_cameras() below
    to help figure out which index it is).
    """
    if IS_WINDOWS:
        return None
    for path in sorted(glob.glob("/dev/video*")):
        try:
            name_path = f"/sys/class/video4linux/{path.split('/')[-1]}/name"
            with open(name_path) as f:
                name = f.read()
            if "PureThermal" in name or "Lepton" in name or "FLIR" in name:
                return path
        except OSError:
            continue
    return None


def find_windows_cameras(max_index: int = 8) -> list:
    """
    Debug helper: probes camera indices 0..max_index-1 and reports which
    ones open and at what default resolution. OpenCV/DirectShow doesn't
    expose device *names* this way -- cross-reference against Device
    Manager (or `ffmpeg -list_devices true -f dshow -i dummy` if you
    have ffmpeg) to match an index to "PureThermal".
    """
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append((i, w, h))
        cap.release()
    return found


class LeptonCapture:
    def __init__(self, device: Optional[DeviceRef] = None, queue_size: int = 2):
        self.device = device if device is not None else find_purethermal_device()
        if self.device is None:
            hint = (
                "On Windows, pass the camera index explicitly, e.g. "
                "LeptonCapture(1) -- run find_windows_cameras() or check "
                "Device Manager to figure out which index it is."
                if IS_WINDOWS
                else "Pass the device path explicitly, e.g. "
                "LeptonCapture('/dev/video0'). Use `v4l2-ctl --list-devices` "
                "to find it."
            )
            raise RuntimeError(f"Could not auto-detect a PureThermal device. {hint}")
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._q: "queue.Queue[ThermalFrame]" = queue.Queue(maxsize=queue_size)

    def open(self):
        backend = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_V4L2
        cap = cv2.VideoCapture(self.device, backend)
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open {self.device}")

        # Ask for a 16-bit greyscale stream and stop OpenCV from
        # mangling it into 8-bit BGR.
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        fourcc = cv2.VideoWriter_fourcc(*"Y16 ")
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, IMG_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (actual_w, actual_h) not in ((IMG_WIDTH, FRAME_HEIGHT), (IMG_WIDTH, IMG_HEIGHT)):
            check_hint = (
                "Check Device Manager / your capture settings for the "
                "board's current video format."
                if IS_WINDOWS
                else f"Check `v4l2-ctl -d {self.device} --list-formats-ext`."
            )
            raise RuntimeError(
                f"Unexpected frame size {actual_w}x{actual_h} from {self.device}. "
                f"Expected {IMG_WIDTH}x{FRAME_HEIGHT} (raw14 + telemetry footer). "
                f"Check that the board is configured for Raw14 + telemetry "
                f"(via GetThermal). {check_hint}"
            )
        self._has_telemetry = actual_h == FRAME_HEIGHT
        self._cap = cap

    def start(self):
        if self._cap is None:
            self.open()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _loop(self):
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            # OpenCV may hand back a single-channel 16-bit image, or
            # occasionally a 3-channel view over the same 16-bit data
            # depending on driver quirks -- normalize to 2D uint16.
            arr = np.asarray(frame)
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            arr = arr.astype(np.uint16, copy=False)

            image = arr[:IMG_HEIGHT, :]
            telemetry_obj = None
            if self._has_telemetry:
                tel_words = arr[IMG_HEIGHT:FRAME_HEIGHT, :].reshape(-1)
                try:
                    telemetry_obj = parse_telemetry(tel_words)
                except Exception:
                    telemetry_obj = None

            tframe = ThermalFrame(
                image_k100=image, telemetry=telemetry_obj, timestamp=time.time()
            )

            # Keep only the newest frame -- an old thermal frame is
            # worthless once a new one exists, and the Pi 3 can't afford
            # to fall behind.
            try:
                self._q.put_nowait(tframe)
            except queue.Full:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
                self._q.put_nowait(tframe)

    def get_frame(self, timeout: float = 1.0) -> Optional[ThermalFrame]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None
