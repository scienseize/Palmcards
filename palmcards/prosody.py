"""Pitch and loudness of a take, for the delivery-mark verdicts (palmcards.cues).

Computed once per take, in the transcription worker, and cached next to the
WAV as take-NN.prosody.npz, so verdicts can be recomputed (and thresholds
tuned) without running pyin again:

    t        frame centres on the app clock (t_start + seconds into the take)
    f0       pitch in Hz, NaN where pyin found no voice
    rms_db   loudness, dBFS

Voiced frames much quieter than the take's speech (CUES.voiced_floor_db)
are dropped when the file is used, not when it is made, so that threshold
can be tuned without running pyin again. Pitch is compared in semitones
relative to the speaker's median for the take (`Prosody.st`), not in Hz,
so the same thresholds work for any voice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from palmcards.config import CUES

EMPTY = np.zeros(0, np.float64)


@dataclass
class Prosody:
    t: np.ndarray
    f0: np.ndarray  # as pyin gave it
    rms_db: np.ndarray
    voiced: np.ndarray = field(init=False)  # pyin found a pitch, and it's loud enough to be speech
    median: float = field(init=False)  # the take's median pitch in Hz, NaN if nothing was voiced

    def __post_init__(self):
        self.voiced = np.isfinite(self.f0)
        if self.voiced.any():
            loud = float(np.percentile(self.rms_db[self.voiced], 95))
            self.voiced &= self.rms_db >= loud - CUES.voiced_floor_db
        self.median = float(np.median(self.f0[self.voiced])) if self.voiced.any() else float("nan")

    @property
    def hop(self) -> float:
        return CUES.hop_s

    @property
    def st(self) -> np.ndarray:
        """Pitch in semitones from the take's median; NaN where unvoiced."""
        out = np.full(len(self.f0), np.nan)
        if self.voiced.any():
            out[self.voiced] = 12.0 * np.log2(self.f0[self.voiced] / self.median)
        return out

    def window(self, t0: float, t1: float) -> slice:
        """Frames with centres in [t0, t1]."""
        return slice(int(np.searchsorted(self.t, t0, "left")), int(np.searchsorted(self.t, t1, "right")))

    def __len__(self) -> int:
        return len(self.t)


def analyse(audio: np.ndarray, rate: int, t_start: float) -> Prosody:
    """pyin pitch and RMS loudness of mono audio (ideally 16 kHz, it is slow)."""
    import librosa  # slow to import; only the worker and tests need it

    hop = max(1, round(rate * CUES.hop_s))
    if len(audio) < CUES.frame_length:
        return Prosody(EMPTY, EMPTY, EMPTY)
    audio = audio.astype(np.float32)
    f0, voiced, _ = librosa.pyin(audio, fmin=CUES.fmin_hz, fmax=CUES.fmax_hz, sr=rate,
                                 frame_length=CUES.frame_length, hop_length=hop)
    rms = librosa.feature.rms(y=audio, frame_length=max(hop, round(rate * CUES.rms_frame_s)), hop_length=hop)[0]
    n = min(len(f0), len(rms))
    f0 = np.where(voiced[:n], f0[:n], np.nan).astype(np.float64)
    rms_db = 20 * np.log10(np.maximum(rms[:n].astype(np.float64), 1e-6))
    return Prosody(t_start + np.arange(n) * hop / rate, f0, rms_db)


def save(p: Prosody, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(tmp, t=p.t, f0=p.f0, rms_db=p.rms_db)
    tmp.replace(path)


def load(path: Path) -> Prosody:
    with np.load(path) as d:
        return Prosody(d["t"], d["f0"], d["rms_db"])
