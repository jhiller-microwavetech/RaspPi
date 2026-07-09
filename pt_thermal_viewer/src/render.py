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


class TemporalSmoother:
    """
    Exponential moving average across frames, applied to the raw
    Kelvin-x100 data (before any display processing) to cut sensor
    noise. Since noise gets visually amplified once the image is
    stretched 4-5x to fill a display, smoothing here matters more than
    it would look like from the native 160x120 frame alone.

    alpha: weight given to the newest frame each update.
      1.0 = no smoothing (always just the newest frame)
      lower = more smoothing, but more motion lag/ghosting on fast
      moving heat sources -- 0.3-0.6 is a reasonable range to try.
    """

    def __init__(self, alpha: float = 0.5):
        self.alpha = alpha
        self._state = None

    def update(self, image_k100: np.ndarray) -> np.ndarray:
        frame = image_k100.astype(np.float32)
        if self._state is None or self._state.shape != frame.shape:
            self._state = frame
        else:
            self._state = self.alpha * frame + (1.0 - self.alpha) * self._state
        return self._state

    def reset(self):
        self._state = None


def k100_to_celsius(image_k100: np.ndarray) -> np.ndarray:
    return image_k100.astype(np.float32) / 100.0 - 273.15


def _unsharp_mask(gray8: np.ndarray, amount: float, sigma: float = 1.0) -> np.ndarray:
    """
    amount: 0 = no sharpening. ~0.5-1.0 is a reasonable "digital detail
    enhancement" range; higher starts to look artificial and will
    exaggerate noise, so pair a higher amount with more temporal
    smoothing, not less.
    """
    if amount <= 0:
        return gray8
    blurred = cv2.GaussianBlur(gray8, (0, 0), sigma)
    sharpened = cv2.addWeighted(gray8, 1.0 + amount, blurred, -amount, 0)
    return sharpened


def _colorize(gray8: np.ndarray, palette: Palette) -> np.ndarray:
    if palette in _CV_COLORMAPS:
        return cv2.applyColorMap(gray8, _CV_COLORMAPS[palette])
    elif palette == Palette.WHITE_HOT:
        return cv2.cvtColor(gray8, cv2.COLOR_GRAY2BGR)
    elif palette == Palette.BLACK_HOT:
        return cv2.cvtColor(255 - gray8, cv2.COLOR_GRAY2BGR)
    else:
        raise ValueError(f"Unknown palette {palette}")


def render_frame(
    image_k100: np.ndarray,
    palette: Palette = Palette.IRON,
    manual_range_c: "tuple[float, float] | None" = None,
    target_size: "tuple[int, int] | None" = (640, 480),
    sharpen_amount: float = 0.6,
) -> "tuple[np.ndarray, tuple[float, float]]":
    """
    Returns (bgr_image, (lo_c, hi_c)) -- the false-color image, and the
    Celsius range that was actually used for the contrast stretch this
    frame (needed by the caller to draw a matching scale bar).

    manual_range_c: optional (min_c, max_c) to lock the contrast
    stretch instead of auto-scaling to the current frame's min/max
    (auto-scaling makes cold/hot spots pop but means the same object
    can look like a different color from frame to frame).

    target_size: (width, height) to upscale to using cubic
    interpolation, done *before* colorizing so edges stay clean rather
    than getting an interpolated-color halo. Pass None to skip
    upscaling and return the native 160x120 image (e.g. if you're
    going to let Qt scale it instead).

    sharpen_amount: unsharp-mask strength applied at native resolution
    before upscaling, 0 to disable. Sharpening after upscale would just
    sharpen interpolation artifacts instead of real detail, so order
    matters here.
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

    gray8 = _unsharp_mask(gray8, sharpen_amount)

    if target_size is not None:
        gray8 = cv2.resize(gray8, target_size, interpolation=cv2.INTER_CUBIC)

    color = _colorize(gray8, palette)
    return color, (lo, hi)


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


def region_stats_c(image_k100: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> dict:
    """
    Same as scene_stats_c but restricted to a rectangular region.
    Coordinates are in native 160x120 image space and are clamped /
    ordered automatically, so it's safe to pass a rect drawn in either
    direction (e.g. dragged bottom-right to top-left).
    """
    h, w = image_k100.shape[:2]
    x0, x1 = sorted((int(np.clip(x0, 0, w - 1)), int(np.clip(x1, 0, w - 1))))
    y0, y1 = sorted((int(np.clip(y0, 0, h - 1)), int(np.clip(y1, 0, h - 1))))
    region = image_k100[y0 : y1 + 1, x0 : x1 + 1]
    temps_c = k100_to_celsius(region)
    return {
        "min_c": float(np.min(temps_c)),
        "max_c": float(np.max(temps_c)),
        "mean_c": float(np.mean(temps_c)),
    }


def celsius_to_fahrenheit(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _label_bg(img, text, org, scale=0.5, color=(255, 255, 255), thickness=1):
    """Draws text with a translucent dark backing box so it stays legible
    regardless of what false-color pixels are underneath it."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    x, y = org
    pad = 3
    overlay = img.copy()
    cv2.rectangle(
        overlay, (x - pad, y - th - pad), (x + tw + pad, y + baseline + pad), (0, 0, 0), -1
    )
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, dst=img)
    cv2.putText(img, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def draw_scale_bar(
    bgr: np.ndarray,
    palette: Palette,
    lo_label: str,
    hi_label: str,
    margin: int = 12,
    bar_width: int = 22,
) -> np.ndarray:
    """
    Draws a vertical color-scale legend in the top-right corner: a
    gradient strip matching the current palette, with the hot end's
    label at top and cold end's label at bottom. Mutates and returns bgr.
    """
    h, w = bgr.shape[:2]
    bar_h = h - 2 * margin
    x0 = w - margin - bar_width
    y0 = margin

    gray_col = np.linspace(255, 0, bar_h, dtype=np.uint8).reshape(bar_h, 1)
    gray_strip = np.repeat(gray_col, bar_width, axis=1)
    color_strip = _colorize(gray_strip, palette)

    bgr[y0 : y0 + bar_h, x0 : x0 + bar_width] = color_strip
    cv2.rectangle(
        bgr, (x0 - 1, y0 - 1), (x0 + bar_width, y0 + bar_h), (255, 255, 255), 1
    )

    _label_bg(bgr, hi_label, (x0 - 6 - _text_w(hi_label), y0 + 12))
    _label_bg(bgr, lo_label, (x0 - 6 - _text_w(lo_label), y0 + bar_h - 4))
    return bgr


def _text_w(text, scale=0.5, thickness=1):
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    return tw


def draw_crosshair(
    bgr: np.ndarray,
    native_size: "tuple[int, int]",
    spot_xy: "tuple[int, int]",
    label: str,
    size: int = 10,
) -> np.ndarray:
    """
    Draws a crosshair marker at spot_xy (given in native 160x120 image
    coordinates) scaled to bgr's actual size, plus a temperature label.
    Mutates and returns bgr.
    """
    h, w = bgr.shape[:2]
    native_w, native_h = native_size
    cx = int(spot_xy[0] / native_w * w)
    cy = int(spot_xy[1] / native_h * h)

    # black outline behind a white crosshair so it reads on any palette
    for color, thickness in [((0, 0, 0), 3), ((255, 255, 255), 1)]:
        cv2.line(bgr, (cx - size, cy), (cx + size, cy), color, thickness)
        cv2.line(bgr, (cx, cy - size), (cx, cy + size), color, thickness)
        cv2.circle(bgr, (cx, cy), 3, color, thickness if thickness == 1 else -1)

    _label_bg(bgr, label, (min(cx + size + 4, w - _text_w(label) - 8), max(cy, 14)))
    return bgr


def draw_roi(
    bgr: np.ndarray,
    native_size: "tuple[int, int]",
    roi_native: "tuple[int, int, int, int]",
    label: str,
    color=(0, 220, 255),
) -> np.ndarray:
    """
    Draws a rectangle for the region-of-interest (given in native
    160x120 coordinates, any corner order) scaled to bgr's actual size,
    plus a stats label above it. Mutates and returns bgr.
    """
    h, w = bgr.shape[:2]
    native_w, native_h = native_size
    x0, y0, x1, y1 = roi_native
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))

    px0, py0 = int(x0 / native_w * w), int(y0 / native_h * h)
    px1, py1 = int(x1 / native_w * w), int(y1 / native_h * h)

    cv2.rectangle(bgr, (px0, py0), (px1, py1), color, 2)
    label_y = py0 - 6 if py0 > 20 else py1 + 18
    _label_bg(bgr, label, (px0, label_y), color=color)
    return bgr
