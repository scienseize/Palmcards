"""Playing a sentence or a paragraph of a take in Review, and what is playing.

    clip = sentence_clip(session, take, sentence)   # (audio, rate) or None
    clip = span_clip(session, take, sentences)      # a paragraph: first said word to last
    player = ClipPlayer(); player.play(*clip); player.stop()

    playback = Playback()                            # one per app: a take's clip or Prepare's "hear it"
    playback.start_clip(player, clip, target, now)
    playback.start_say(speaker, words, target, now)
    playback.poll(now); playback.progress(now); playback.stop()

The sentence is the take's own (Notes.sentences index in the take's notes
revision); the clip runs from its first to its last matched word, padded a
little, from the take's WAV. A span runs from the first matched word of the
first sentence said to the last of the last one, pauses and all.

Playback keeps what plays, one thing at a time, with the target it plays
for (the app decides what a target is and stops it when the screen no
longer shows it). A clip's length is known; `say` reports no position, so
its progress is an estimate from the word count, held short of the end
until it has finished.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from palmcards.session import Session, TakeRecord, read_wav

PAD_S = 0.25  # heard before the first word and after the last
END_SLACK_S = 0.15  # a clip counts as playing this long past its length (the device's latency)
SAY_HELD_AT = 0.95  # "hear it" progress waits here until `say` has finished


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


@dataclass
class Playing:
    kind: str  # "clip" | "say"
    target: tuple
    start: float
    duration_s: float
    device: object  # the player or the speaker, to stop it


class Playback:
    """What plays now, for which target, and how far it has got."""

    def __init__(self):
        self.current: Playing | None = None

    @property
    def playing(self) -> bool:
        return self.current is not None

    @property
    def target(self) -> tuple | None:
        return self.current.target if self.current is not None else None

    def start_clip(self, player, clip: tuple[np.ndarray, int], target: tuple, now: float) -> None:
        """Play a take's clip (the player stops anything it was playing first)."""
        self.stop()
        audio, rate = clip
        player.play(audio, rate)
        self.current = Playing("clip", target, now, len(audio) / rate, player)

    def start_say(self, speaker, words: list[str], target: tuple, now: float) -> None:
        self.stop()
        speaker.say_words(words)
        estimate = speaker.estimate_s(words) if hasattr(speaker, "estimate_s") else 0.4 * len(words)
        self.current = Playing("say", target, now, max(estimate, 0.1), speaker)

    def stop(self) -> bool:
        """Stop what plays, at once. True if something was playing."""
        cur, self.current = self.current, None
        if cur is None:
            return False
        cur.device.stop()
        return True

    def poll(self, now: float) -> bool:
        """Forget what has finished by itself. True while something plays."""
        cur = self.current
        if cur is None:
            return False
        if cur.kind == "clip":
            done = now - cur.start >= cur.duration_s + END_SLACK_S
        elif hasattr(cur.device, "speaking"):
            done = not cur.device.speaking
        else:  # a speaker that can't tell: its estimate
            done = now - cur.start >= cur.duration_s
        if done:
            self.current = None
        return not done

    def progress(self, now: float) -> float | None:
        """0..1 through what plays, or None."""
        cur = self.current
        if cur is None:
            return None
        p = (now - cur.start) / cur.duration_s
        return min(p, SAY_HELD_AT) if cur.kind == "say" else min(max(p, 0.0), 1.0)
