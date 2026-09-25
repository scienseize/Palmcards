"""Sessions on disk: one folder per app run, a WAV per take, session.json.

  sessions/20260925-143000-sample_notes/
      session.json
      take-01.wav
      take-02.wav

Take times (`t_start`, and the gesture log's `t`) share one clock: seconds
since the app started. Section marks are seconds into the take. Later
milestones line up audio, poses and events with them.

The folder is created with the first take, so a run without takes leaves
nothing behind.
"""

from __future__ import annotations

import json
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
SILENT_PEAK = 1e-3  # a take whose loudest sample is below this is silence


@dataclass
class TakeRecord:
    number: int
    wav: str  # file name inside the session folder
    started: str  # wall-clock time recording began, ISO 8601
    t_start: float  # app clock at the first sample
    duration_s: float
    sample_rate: int
    peak: float  # loudest |sample|, 0..1
    sections: list[dict] = field(default_factory=list)  # {"section": i, "t": s into the take}

    @property
    def silent(self) -> bool:
        return self.peak < SILENT_PEAK


@dataclass
class Session:
    notes: Path
    dir: Path
    gesture_log: str | None = None  # path relative to the sessions root
    takes: list[TakeRecord] = field(default_factory=list)

    @classmethod
    def create(cls, notes: str | Path, root: Path = SESSIONS_DIR, gesture_log: Path | None = None,
               now: datetime | None = None) -> Session:
        now = now or datetime.now()
        notes = Path(notes)
        log = None
        if gesture_log is not None:
            log = str(gesture_log.relative_to(root)) if gesture_log.is_relative_to(root) else str(gesture_log)
        return cls(notes, root / f"{now:%Y%m%d-%H%M%S}-{notes.stem}", log)

    @classmethod
    def load(cls, folder: str | Path) -> Session:
        folder = Path(folder)
        data = json.loads((folder / "session.json").read_text())
        takes = [TakeRecord(**t) for t in data["takes"]]
        return cls(Path(data["notes"]), folder, data.get("gesture_log"), takes)

    def add_take(self, audio: np.ndarray, rate: int, t_start: float, started: datetime,
                 sections: list[tuple[float, int]]) -> TakeRecord:
        """Save audio as the next take. `sections` is [(t into the take, section)]."""
        self.dir.mkdir(parents=True, exist_ok=True)
        number = len(self.takes) + 1
        wav = f"take-{number:02d}.wav"
        write_wav(self.dir / wav, audio, rate)
        take = TakeRecord(
            number=number,
            wav=wav,
            started=started.isoformat(timespec="milliseconds"),
            t_start=round(t_start, 3),
            duration_s=round(len(audio) / rate, 3),
            sample_rate=rate,
            peak=round(float(np.abs(audio).max()) if len(audio) else 0.0, 5),
            sections=[{"section": s, "t": round(t, 3)} for t, s in sections],
        )
        self.takes.append(take)
        self.save()
        return take

    def save(self) -> None:
        data = {
            "notes": str(self.notes),
            "gesture_log": self.gesture_log,
            "takes": [asdict(t) for t in self.takes],
        }
        tmp = self.dir / "session.json.tmp"
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(self.dir / "session.json")


def write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    """Mono float audio (-1..1) as 16-bit PCM."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).round().astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV -> (float32 audio in -1..1, sample rate); mono or first channel."""
    with wave.open(str(path), "rb") as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        channels, rate = f.getnchannels(), f.getframerate()
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    return pcm[::channels].astype(np.float32) / 32767, rate
