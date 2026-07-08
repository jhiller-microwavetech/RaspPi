"""
Parses the Lepton 3.5 telemetry footer/header.

Word offsets below are taken directly from the FLIR Lepton Engineering
Datasheet Rev 400 (Teledyne FLIR, Doc# 500-0771-01-09), Table 2
"Telemetry Data Content and Encoding" and Table 3 "Status Bit Encoding".

Layout on the wire: telemetry occupies 2 extra 160-pixel video lines
(320 x 16-bit words). Only the first 240 of those words are defined
(Row A: words 0-79, Row B: words 80-159, Row C: words 160-239); the
remainder is reserved padding.

One thing to verify against your actual hardware output once you have
frames in hand: the 16-bit word order for the two multi-word fields
(TimeCounter, FrameCounter) -- the datasheet gives the word range but
not explicitly whether the first word is the MSW or LSW. This code
assumes first-word-is-MSW (big-endian word order), which is standard
for Lepton/FLIR SDK fields. If uptime or frame counter look wrong
(e.g. jump by 65536 or read as garbage), flip WORD_ORDER_MSW_FIRST
below and it'll fix both.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

TELEMETRY_ROWS = 2          # extra video lines when telemetry is on
TELEMETRY_WORDS = 160 * TELEMETRY_ROWS   # 320 uint16 words total
ROW_WORDS = 80               # each logical Row (A/B/C) is 80 words

WORD_ORDER_MSW_FIRST = True


class FFCState(IntEnum):
    NEVER_COMMANDED = 0
    IMMINENT = 1
    IN_PROGRESS = 2
    COMPLETE = 3


class GainMode(IntEnum):
    HIGH = 0
    LOW = 1
    AUTO = 2


@dataclass
class Telemetry:
    revision_major: int
    revision_minor: int
    uptime_ms: int
    ffc_desired: bool
    ffc_state: FFCState
    agc_enabled: bool
    shutter_lockout: bool
    overtemp_imminent: bool
    frame_counter: int
    frame_mean: int
    fpa_temp_k: float           # Kelvin
    housing_temp_k: float       # Kelvin
    fpa_temp_at_last_ffc_k: float
    time_at_last_ffc_ms: int
    gain_mode: GainMode
    effective_gain_mode: GainMode
    tlinear_enabled: bool
    tlinear_resolution: float   # 0.1 or 0.01 (Kelvin per LSB)
    spotmeter_mean_k: float
    spotmeter_max_k: float
    spotmeter_min_k: float
    spotmeter_population: int

    @property
    def fpa_temp_c(self) -> float:
        return self.fpa_temp_k - 273.15

    @property
    def housing_temp_c(self) -> float:
        return self.housing_temp_k - 273.15

    @property
    def spotmeter_mean_c(self) -> float:
        return self.spotmeter_mean_k - 273.15

    @property
    def spotmeter_max_c(self) -> float:
        return self.spotmeter_max_k - 273.15

    @property
    def spotmeter_min_c(self) -> float:
        return self.spotmeter_min_k - 273.15


def _combine32(words: np.ndarray, idx: int) -> int:
    """Combine two consecutive uint16 words at `idx` into a uint32."""
    w0, w1 = int(words[idx]), int(words[idx + 1])
    if WORD_ORDER_MSW_FIRST:
        return (w0 << 16) | w1
    return (w1 << 16) | w0


def parse_telemetry(raw_words: np.ndarray) -> Telemetry:
    """
    raw_words: 1D uint16 numpy array, length >= 240 (the telemetry footer
    or header reshaped from its 2 video lines, in row-major order).
    """
    if raw_words.dtype != np.uint16:
        raw_words = raw_words.astype(np.uint16)
    w = raw_words

    row_a = w[0 * ROW_WORDS: 1 * ROW_WORDS]
    row_b = w[1 * ROW_WORDS: 2 * ROW_WORDS]
    row_c = w[2 * ROW_WORDS: 3 * ROW_WORDS]

    rev_word = int(row_a[0])
    revision_major = (rev_word >> 8) & 0xFF
    revision_minor = rev_word & 0xFF

    uptime_ms = _combine32(row_a, 1)

    # Table 3 explicitly documents this field as word3 = bits 0-15 (LSW),
    # word4 = bits 16-31 (MSW) -- this is stated directly, unlike the
    # other multi-word fields, so it does not follow WORD_ORDER_MSW_FIRST.
    status = (int(row_a[4]) << 16) | int(row_a[3])
    ffc_desired = bool((status >> 3) & 0x1)
    ffc_state = FFCState((status >> 4) & 0x3)
    agc_enabled = bool((status >> 12) & 0x1)
    shutter_lockout = bool((status >> 15) & 0x1)
    overtemp_imminent = bool((status >> 20) & 0x1)

    frame_counter = _combine32(row_a, 20)
    frame_mean = int(row_a[22])

    fpa_temp_k = int(row_a[24]) / 100.0
    housing_temp_k = int(row_a[26]) / 100.0
    fpa_temp_at_last_ffc_k = int(row_a[29]) / 100.0
    time_at_last_ffc_ms = _combine32(row_a, 30)

    gain_mode = GainMode(int(row_c[5]))
    effective_gain_mode = GainMode(int(row_c[6]))

    tlinear_enabled = bool(row_c[48])
    tlinear_resolution = 0.01 if int(row_c[49]) == 1 else 0.1

    spotmeter_mean_k = int(row_c[50]) * tlinear_resolution
    spotmeter_max_k = int(row_c[51]) * tlinear_resolution
    spotmeter_min_k = int(row_c[52]) * tlinear_resolution
    spotmeter_population = int(row_c[53])

    return Telemetry(
        revision_major=revision_major,
        revision_minor=revision_minor,
        uptime_ms=uptime_ms,
        ffc_desired=ffc_desired,
        ffc_state=ffc_state,
        agc_enabled=agc_enabled,
        shutter_lockout=shutter_lockout,
        overtemp_imminent=overtemp_imminent,
        frame_counter=frame_counter,
        frame_mean=frame_mean,
        fpa_temp_k=fpa_temp_k,
        housing_temp_k=housing_temp_k,
        fpa_temp_at_last_ffc_k=fpa_temp_at_last_ffc_k,
        time_at_last_ffc_ms=time_at_last_ffc_ms,
        gain_mode=gain_mode,
        effective_gain_mode=effective_gain_mode,
        tlinear_enabled=tlinear_enabled,
        tlinear_resolution=tlinear_resolution,
        spotmeter_mean_k=spotmeter_mean_k,
        spotmeter_max_k=spotmeter_max_k,
        spotmeter_min_k=spotmeter_min_k,
        spotmeter_population=spotmeter_population,
    )
