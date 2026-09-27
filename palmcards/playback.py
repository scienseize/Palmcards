"""Playing a sentence or a paragraph of a take in Review.

    clip = sentence_clip(session, take, sentence)   # (audio, rate) or None
    clip = span_clip(session, take, sentences)      # a paragraph: first said word to last
    player = ClipPlayer(); player.play(*clip); player.stop()

The sentence is the take's own (Notes.sentences index in the take's notes
revision); the clip runs from its first to its last matched word, padded a
little, from the take's WAV. A span runs from the first matched word of the
first sentence said to the last of the last one, pauses and all.
"""

from __future__ import annotations

import numpy as np

from palmcards.session import Session, TakeRecord, read_wav

PAD_S = 0.25  # heard before the first word and after the last


def sentence_clip(session: Session, take: TakeRecord, sentence: int) -> tuple[np.ndarray, int] | None:
    """The audio of one sentence of a take, or None if it wasn't said (or not yet aligned)."""
    return span_clip(session, take, [sentence])


def span_clip(session: Session, take: TakeRecord, sentences: list[int]) -> tuple[np.ndarray, int] | None:
    """The audio of the take's sentences (its own indices) from the first word
    said to the last, or None if none of them was said (or not yet aligned)."""
    if take.alignment is None:
        return None
    entries = [take.alignment["sentences"][i] for i in sentences if 0 <= i < len(take.alignment["sentences"])]
    said = [e for e in entries if e["start"] is not None and e["end"] is not None]
    if not said:
        return None
    audio, rate = read_wav(session.dir / take.wav)
    a = max(0, int((min(e["start"] for e in said) - take.t_start - PAD_S) * rate))
    b = min(len(audio), int((max(e["end"] for e in said) - take.t_start + PAD_S) * rate))
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
