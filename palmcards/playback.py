"""Playing a sentence of a take in Review.

    clip = sentence_clip(session, take, sentence)   # (audio, rate) or None
    player = ClipPlayer(); player.play(*clip); player.stop()

The sentence is the take's own (Notes.sentences index in the take's notes
revision); the clip runs from its first to its last matched word, padded a
little, from the take's WAV.
"""

from __future__ import annotations

import numpy as np

from palmcards.session import Session, TakeRecord, read_wav

PAD_S = 0.25  # heard before the first word and after the last


def sentence_clip(session: Session, take: TakeRecord, sentence: int) -> tuple[np.ndarray, int] | None:
    """The audio of one sentence of a take, or None if it wasn't said (or not yet aligned)."""
    if take.alignment is None or not 0 <= sentence < len(take.alignment["sentences"]):
        return None
    entry = take.alignment["sentences"][sentence]
    if entry["start"] is None:
        return None
    audio, rate = read_wav(session.dir / take.wav)
    a = max(0, int((entry["start"] - take.t_start - PAD_S) * rate))
    b = min(len(audio), int((entry["end"] - take.t_start + PAD_S) * rate))
    return (audio[a:b], rate) if b > a else None


class ClipPlayer:
    """One clip at a time through the default output (sounddevice), never blocking."""

    def __init__(self):
        self._sd = None

    def play(self, audio: np.ndarray, rate: int) -> None:
        import sounddevice as sd

        self._sd = sd
        sd.stop()
        sd.play(audio, rate)

    def stop(self) -> None:
        if self._sd is not None:
            self._sd.stop()
