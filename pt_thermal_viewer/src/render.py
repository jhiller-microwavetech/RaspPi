"""
Turns a (120, 160) uint16 TLinear frame (Kelvin x 100 per pixel) into an
8-bit false-color image for display, and provides temperature lookups
for a "tap to read" spot meter.

Because TLinear is enabled on the sensor, every pixel is already an
absolute, calibrated temperature -- we don't need to do our own AGC
math for accuracy, only for *display contrast*. The min/max-stretch
below is purely cosmetic (maps current scene's temp range to 0-255);
it does not affect the temperature values you read out.
"""

from enum import Enum

import cv2
import numpy as np


class Palette(Enum):
    IRON = "iron"        # COLORMAP_INFERNO
    RAINBOW = "rainbow"  # COLORMAP_JET
    WHITE_HOT = "white_hot"
    BLACK_HOT = "black_hot"


_CV_COLORMAPS = {
    Palette.IRON: cv2.COLORMAP_INFERNO,
    Palette.RAINBOW: cv2.COLORMAP_JET,
}


def k100_to_celsius(image_k100: np.ndarray) -> np.ndarray:
    return image_k100.astype(np.float32) / 100.0 - 273.15


def render_frame(
    image_k100: np.ndarray,
    palette: Palette = Palette.IRON,
    manual_range_c: "tuple[float, float] | None" = None,
) -> np.ndarray:
    """
    Returns an (H, W, 3) uint8 BGR image ready to hand to Qt (after a
    BGR->RGB swap) or to cv2.imshow directly.

    manual_range_c: optional (min_c, max_c) to lock the contrast
    stretch instead of auto-scaling to the current frame's min/max
    (auto-scaling makes cold/hot spots pop but means the same object
    can look like a different color from frame to frame).
    """
    temps_c = k100_to_celsius(image_k100)

    if manual_range_c is not None:
        lo, hi = manual_range_c
    else:
        lo, hi = float(np.min(temps_c)), float(np.max(temps_c))
        if hi - lo < 1e-3:
            hi = lo + 1e-3

    stretched = np.clip((temps_c - lo) / (hi - lo), 0.0, 1.0)
    gray8 = (stretched * 255.0).astype(np.uint8)

    if palette in _CV_COLORMAPS:
        color = cv2.applyColorMap(gray8, _CV_COLORMAPS[palette])
    elif palette == Palette.WHITE_HOT:
        color = cv2.cvtColor(gray8, cv2.COLOR_GRAY2BGR)
    elif palette == Palette.BLACK_HOT:
        color = cv2.cvtColor(255 - gray8, cv2.COLOR_GRAY2BGR)
    else:
        raise ValueError(f"Unknown palette {palette}")

    return color


def temp_at_c(image_k100: np.ndarray, x: int, y: int) -> float:
    """Point temperature in Celsius at pixel (x, y) in the 160x120 image."""
    y = int(np.clip(y, 0, image_k100.shape[0] - 1))
    x = int(np.clip(x, 0, image_k100.shape[1] - 1))
    return float(image_k100[y, x]) / 100.0 - 273.15


def scene_stats_c(image_k100: np.ndarray) -> dict:
    temps_c = k100_to_celsius(image_k100)
    return {
        "min_c": float(np.min(temps_c)),
        "max_c": float(np.max(temps_c)),
        "mean_c": float(np.mean(temps_c)),
    }
