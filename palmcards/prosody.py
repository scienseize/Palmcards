"""Pitch and loudness of a take, for the delivery-mark verdicts (palmcards.cues).

Computed once per take, in the transcription worker, and cached next to the
WAV as take-NN.prosody.npz, so verdicts can be recomputed (and thresholds
tuned) without running pyin again:

    t        frame centres on the app clock (t_start + seconds into the take)
    f0       pitch in Hz, NaN where pyin found no voice
    rms_db   loudness, dBFS

The cache records how it was made (`provenance`): the WAV's hash, the
rate pyin ran at, every extraction setting, the extractor and librosa
versions, and the time origin (t_start). A cache whose provenance doesn't
match what would be made now is stale: it is made again, except when
re-judging without re-measuring (speech --realign), which uses it and says
it is stale. A cache from before provenance was kept is "unknown". The frame
hop always comes from the cache itself, never from today's settings.

Voiced frames much quieter than the take's speech (CUES.voiced_floor_db)
are dropped when the file is used, not when it is made, so that threshold
can be tuned without running pyin again. Pitch is compared in semitones
relative to the speaker's median for the take (`Prosody.st`), not in Hz,
so the same thresholds work for any voice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from palmcards.config import CUES

EMPTY = np.zeros(0, np.float64)
EXTRACTOR_VERSION = 1  # bump when analyse() changes what it measures


def extraction(rate: int) -> dict:
    """The settings that shape analyse()'s output at this rate."""
    try:
        librosa_version = version("librosa")
    except PackageNotFoundError:
        librosa_version = None
    return {"extractor": EXTRACTOR_VERSION, "librosa": librosa_version, "rate": rate, "fmin_hz": CUES.fmin_hz,
            "fmax_hz": CUES.fmax_hz, "frame_length": CUES.frame_length, "hop_s": CUES.hop_s,
            "frame_hop_s": max(1, round(rate * CUES.hop_s)) / rate,  # the hop in whole samples, as used
            "rms_frame_s": CUES.rms_frame_s}


def provenance(wav: Path, rate: int, t_start: float) -> dict:
    """What a cache made now from this WAV would record."""
    wav = Path(wav)
    digest = hashlib.sha256(wav.read_bytes()).hexdigest() if wav.exists() else None
    return {"wav_sha256": digest, "t_start": t_start, **extraction(rate)}


@dataclass
class Prosody:
    t: np.ndarray
    f0: np.ndarray  # as pyin gave it
    rms_db: np.ndarray
    hop_s: float | None = None  # frame hop it was made with (None: work it out from t)
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
        """Seconds per frame, as the data was made: recorded with it, or read
        off the frame times; never today's CUES.hop_s."""
        if self.hop_s is not None:
            return self.hop_s
        if len(self.t) > 1:
            return float(np.median(np.diff(self.t)))
        return CUES.hop_s  # no frames: nothing is measured with it

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
    return Prosody(t_start + np.arange(n) * hop / rate, f0, rms_db, hop / rate)


def save(p: Prosody, path: Path, meta: dict | None = None) -> None:
    tmp = path.with_name(path.name + ".tmp.npz")
    extra = {"provenance": np.array(json.dumps(meta))} if meta is not None else {}
    np.savez_compressed(tmp, t=p.t, f0=p.f0, rms_db=p.rms_db, **extra)
    tmp.replace(path)


def load(path: Path) -> tuple[Prosody, dict | None]:
    """The cached measurements, and how they were made (None: an old cache)."""
    with np.load(path) as d:
        meta = json.loads(str(d["provenance"])) if "provenance" in d.files else None
        hop = meta.get("frame_hop_s") if meta else None
        return Prosody(d["t"], d["f0"], d["rms_db"], hop), meta
