"""Playing a sentence or a paragraph of a take in Review, and what is playing.

    clip = sentence_clip(session, take, sentence)   # (audio, rate) or None
    clip = span_clip(session, take, sentences)      # a paragraph: first said word to last
    player = ClipPlayer(); player.play(*clip); player.stop()

    playback = Playback()                            # one per app: a take's clip or Prepare's "hear it"
    playback.start_clip(player, clip, target, now)
    playback.start_rendered(player, speaker, words, target, now)  # "hear it": the speaker's audio
    playback.start_say(speaker, words, target, now)
    playback.poll(now); playback.progress(now); playback.stop()
    playback.start_clip(player, clip, target, now, video=reader, video_t0=t0)   # with the take's video
    playback.video_frame(now)                        # the video's frame for what plays now, or None

The sentence is the take's own (Notes.sentences index in the take's notes
revision); the clip runs from its first to its last matched word, padded a
little, from the take's WAV. A span runs from the first matched word of the
first sentence said to the last of the last one, pauses and all.

Playback keeps what plays, one thing at a time, with the target it plays
for (the app decides what a target is and stops it when the screen no
longer shows it). A clip's length is known. "Hear it" plays the speaker's
audio of the sentence as a clip (rendered in the background from when the
sentence was focused; if it isn't ready yet, it starts as soon as it is),
so its progress is exact too. Only a speaker that can't render speaks
live, and `say` reports no position: that progress is an estimate from the
word count, held short of the end until it has finished.

A take recorded with video replays it with the clip: a VideoReader
(palmcards.video) started at the clip's first moment (clip_span), asked each
frame for the moment the audio has reached, the same clock as the progress bar.
"""

from __future__ import annotations

import json
from concurrent.futures import Future
from dataclasses import dataclass

import numpy as np

from palmcards.config import METRICS, REVIEW

from palmcards.session import Session, TakeRecord, read_wav

PAD_S = 0.25  # heard before the first word and after the last
END_SLACK_S = 0.15  # a clip counts as playing this long past its length (the device's latency)
SAY_HELD_AT = 0.95  # "hear it" progress waits here until `say` has finished


def sentence_clip(session: Session, take: TakeRecord, sentence: int) -> tuple[np.ndarray, int] | None:
    """The audio of one sentence of a take, or None if it wasn't said (or not yet aligned)."""
    return span_clip(session, take, [sentence])


def clip_span(take: TakeRecord, sentences: list[int]) -> tuple[float, float] | None:
    """App times the take's sentences (its own indices) play from and to: the
    first word said to the last, padded, within the take. None if none of
    them was said (or not yet aligned)."""
    if take.alignment is None:
        return None
    entries = [take.alignment["sentences"][i] for i in sentences if 0 <= i < len(take.alignment["sentences"])]
    said = [e for e in entries if e["start"] is not None and e["end"] is not None]
    if not said:
        return None
    t0 = max(take.t_start, min(e["start"] for e in said) - PAD_S)
    t1 = min(take.t_start + take.duration_s, max(e["end"] for e in said) + PAD_S)
    return (t0, t1) if t1 > t0 else None


def span_clip(session: Session, take: TakeRecord, sentences: list[int]) -> tuple[np.ndarray, int] | None:
    """The audio of the take's sentences (its own indices) from the first word
    said to the last (clip_span), or None if none of them was said (or not yet aligned)."""
    span = clip_span(take, sentences)
    if span is None:
        return None
    audio, rate = read_wav(session.dir / take.wav)
    a = max(0, int((span[0] - take.t_start) * rate))
    b = min(len(audio), int((span[1] - take.t_start) * rate))
    return (audio[a:b], rate) if b > a else None


def take_words(session: Session, take: TakeRecord) -> list[dict]:
    """The take's transcript words (app clock), or none if it has no transcript."""
    if not take.transcript or not (session.dir / take.transcript).exists():
        return []
    return json.loads((session.dir / take.transcript).read_text()).get("words", [])


def replay_words(session: Session, take: TakeRecord, span: tuple[float, float]) -> list[tuple[float, float, str, str]]:
    """The take's words said within `span`, for a replay's captions: (start,
    end, text, kind), kind as the take player colours them ("word", "filler",
    "restart", "extra", "unsure")."""
    from palmcards.player import Timeline

    words = take_words(session, take)
    if not words or not take.alignment:
        return []
    kinds = Timeline(take.alignment, words, take.t_start).kind
    return [(w["start"], w["end"], w["text"].strip(), kinds.get(i, ("extra", None, None))[0])
            for i, w in enumerate(words) if span[0] <= w["start"] <= span[1] and w["text"].strip()]


def clip_marks(session: Session, take: TakeRecord, span: tuple[float, float]) -> list[tuple[str, float, float]]:
    """What a replay's progress bar marks, within `span` (app times): (kind,
    from, to) with kind "filler" or "restart" (a moment: from == to), "pause"
    (a silence between words over METRICS.long_pause_s) or "away" (looking
    away from the screen, by the take's eye calibration, REVIEW.away_min_s
    or more). What the take has too little for is left out, never guessed."""
    from palmcards import gaze, metrics
    from palmcards.notes import normalize

    t0, t1 = span
    marks: list[tuple[str, float, float]] = []
    alignment = take.alignment or {}
    words = take_words(session, take)
    for f in alignment.get("fillers", []):
        if t0 <= f["t"] <= t1:
            marks.append(("filler", f["t"], f["t"]))
    for r in alignment.get("restarts", []):
        if r["words"] and r["words"][0] < len(words) and t0 <= (t := words[r["words"][0]]["start"]) <= t1:
            marks.append(("restart", t, t))
    unsure = set(alignment.get("unsure", []))
    real = [i for i, w in enumerate(words) if normalize(w["text"]) and i not in unsure]
    for a, b in zip(real, real[1:]):
        gap_from, gap_to = words[a]["end"], words[b]["start"]
        if gap_to - gap_from >= METRICS.long_pause_s and gap_to > t0 and gap_from < t1:
            marks.append(("pause", max(gap_from, t0), min(gap_to, t1)))
    marks += _away(take, span, session, gaze, metrics)
    return sorted(marks, key=lambda m: m[1])


def _away(take: TakeRecord, span: tuple[float, float], session: Session, gaze, metrics) -> list[tuple[str, float, float]]:
    face = session.dir / take.face_name
    arrays, _ = metrics.take_features(take.vision, face)
    calibration = next((c for c in session.calibrations
                        if take.vision and c["id"] == take.vision.get("calibration")), None)
    if arrays is None or calibration is None or gaze.usable(calibration):
        return []
    labels, times = gaze.classify(arrays, calibration), arrays["face_t"]
    runs, start, last = [], None, None
    for t, label in zip(times, labels):
        if not span[0] <= t <= span[1] or label == "unclear":
            continue
        if label == "away":
            if start is None or t - last > REVIEW.away_gap_s:
                if start is not None:
                    runs.append((start, last))
                start = t
            last = t
        elif start is not None:  # looking at the screen again ends it (only unclear readings are bridged)
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    return [("away", float(a), float(b)) for a, b in runs if b - a >= REVIEW.away_min_s]


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
    kind: str  # "clip" | "say" | "rendering" (a clip still being made)
    target: tuple
    start: float
    duration_s: float
    device: object  # the player or the speaker, to stop it
    render: Future | None = None  # rendering: the clip to come
    speaker: object = None  # rendering: says the words instead if making the clip failed
    words: list[str] | None = None
    video: object = None  # a clip's VideoReader (the take's video), or None: audio only
    video_t0: float = 0.0  # app time of the clip's first sample


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

    def start_clip(self, player, clip: tuple[np.ndarray, int], target: tuple, now: float, video=None,
                   video_t0: float = 0.0) -> None:
        """Play a take's clip (the player stops anything it was playing first),
        with its video from `video_t0` (the clip's first sample, app time) if given."""
        self.stop()
        audio, rate = clip
        if video is not None:
            video.start(video_t0)  # decoding from the keyframe before it while the audio starts
        player.play(audio, rate)
        self.current = Playing("clip", target, now, len(audio) / rate, player, video=video, video_t0=video_t0)

    def clip_time(self, now: float) -> float | None:
        """The app time in the take the clip has reached (the progress bar's clock), or None."""
        cur = self.current
        if cur is None or cur.kind != "clip":
            return None
        return cur.video_t0 + min(max(now - cur.start, 0.0), cur.duration_s)

    def video_frame(self, now: float):
        """The video's frame for the moment the clip has reached (the progress
        bar's clock), or None: no video, or none decoded yet."""
        cur = self.current
        if cur is None or cur.video is None:
            return None
        return cur.video.frame_at(self.clip_time(now))

    def start_rendered(self, player, speaker, words: list[str], target: tuple, now: float) -> None:
        """Play the speaker's audio of the words (speaker.render_async): at once
        if it is ready, else as soon as it is (poll); it counts as playing from
        now. If making it failed, the speaker says them live instead."""
        self.stop()
        self.current = Playing("rendering", target, now, 0.0, player, speaker.render_async(words), speaker,
                               list(words))
        self.poll(now)

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
        if cur.video is not None:
            cur.video.close()
        return True

    def poll(self, now: float) -> bool:
        """Forget what has finished by itself. True while something plays."""
        cur = self.current
        if cur is None:
            return False
        if cur.kind == "rendering":
            if not cur.render.done():
                return True
            try:
                clip = cur.render.result()
            except Exception:  # noqa: BLE001 - `say` failed to render: say it live
                self.start_say(cur.speaker, cur.words, cur.target, now)
                return True
            self.start_clip(cur.device, clip, cur.target, now)
            return True
        if cur.kind == "clip":
            done = now - cur.start >= cur.duration_s + END_SLACK_S
        elif hasattr(cur.device, "speaking"):
            done = not cur.device.speaking
        else:  # a speaker that can't tell: its estimate
            done = now - cur.start >= cur.duration_s
        if done:
            self.current = None
            if cur.video is not None:
                cur.video.close()
        return not done

    def progress(self, now: float) -> float | None:
        """0..1 through what plays, or None."""
        cur = self.current
        if cur is None:
            return None
        if cur.kind == "rendering":
            return 0.0
        p = (now - cur.start) / cur.duration_s
        return min(p, SAY_HELD_AT) if cur.kind == "say" else min(max(p, 0.0), 1.0)
