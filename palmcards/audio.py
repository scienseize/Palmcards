"""Audio for the recognisers: 16 kHz mono float32, silence trimmed."""

from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np

from palmcards.config import SPEECH
from palmcards.session import read_wav


def resample(audio: np.ndarray, rate: int, target: int = SPEECH.rate) -> np.ndarray:
    if rate == target:
        return audio.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(rate, target)
    return resample_poly(audio, target // g, rate // g).astype(np.float32)


def trim_silence(audio: np.ndarray, rate: int) -> tuple[int, int]:
    """(start, end) sample range holding the sound, padded; (0, 0) if none."""
    frame = max(1, int(rate * SPEECH.trim_frame_s))
    n = len(audio) // frame
    if n == 0:
        return 0, 0
    frames = audio[: n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    loud = np.flatnonzero(db > max(SPEECH.trim_floor_db, db.max() - SPEECH.trim_below_peak_db))
    if len(loud) == 0:
        return 0, 0
    pad = int(rate * SPEECH.trim_pad_s)
    return max(0, loud[0] * frame - pad), min(len(audio), (loud[-1] + 1) * frame + pad)


def prepare_audio(wav: Path) -> tuple[np.ndarray, float]:
    """16 kHz mono float32 with the silence at both ends trimmed, and the
    seconds cut from the front."""
    audio, rate = read_wav(wav)
    audio = resample(audio, rate)
    start, end = trim_silence(audio, SPEECH.rate)
    return audio[start:end], start / SPEECH.rate


def rms_db(audio: np.ndarray) -> float:
    """Loudness of a stretch of audio, dBFS."""
    if len(audio) == 0:
        return -120.0
    return float(20 * np.log10(max(float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))), 1e-6)))
