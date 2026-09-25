"""Debug player: hear a take while the notes follow it, from the alignment.

  python -m palmcards.player SESSION_DIR [--take N]     (default: the last take)

Plays the take's WAV and highlights, in the usual text overlay, the sentence
being spoken (orange) and the note word being said (chip). Under it runs a
caption of what Whisper heard: matched words white, fillers yellow, restarts
red, ad-libs cyan, unsure words grey. The strip at the bottom is the whole
take: sentence spans (green spoken, orange partial), filler and restart
ticks, section changes, the playhead.

Keys: space pause/play, left/right (or , .) seek 5 s, n/p next/previous
sentence, q/Esc quit.

The take must have been transcribed (by the app, or `python -m palmcards.speech`).
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
import threading
from pathlib import Path

import cv2
import numpy as np

from palmcards.render import Hit, TextOverlay, ViewState, draw_strip as _draw_strip
from palmcards.session import LegacyNotes, Session, read_wav
from palmcards.style import PLAYER, bgr

WINDOW = "PalmCards take player"
SIZE = PLAYER.size
SEEK_S = 5.0
LEFT_KEYS = {2, 63234, 65361, ord(",")}  # arrow key codes differ by platform
RIGHT_KEYS = {3, 63235, 65363, ord(".")}


class Playback:
    """The WAV through the default output; the callback's frame count is the playhead."""

    def __init__(self, audio: np.ndarray, rate: int):
        import sounddevice as sd

        self.audio, self.rate = audio.astype(np.float32), rate
        self.pos = 0
        self.paused = False
        self._lock = threading.Lock()
        self.stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", callback=self._callback)
        self.stream.start()

    def _callback(self, outdata, frames, _time, _status) -> None:
        with self._lock:
            chunk = np.zeros(frames, np.float32)
            if not self.paused:
                piece = self.audio[self.pos : self.pos + frames]
                chunk[: len(piece)] = piece
                self.pos = min(self.pos + frames, len(self.audio))
                if self.pos >= len(self.audio):
                    self.paused = True
        outdata[:, 0] = chunk

    @property
    def duration(self) -> float:
        return len(self.audio) / self.rate

    @property
    def t(self) -> float:
        """Seconds into the take being heard now (output latency allowed for)."""
        lag = 0.0 if self.paused else self.stream.latency
        return max(0.0, self.pos / self.rate - lag)

    def seek(self, t: float) -> None:
        with self._lock:
            self.pos = int(min(max(t, 0.0), self.duration) * self.rate)

    def toggle(self) -> None:
        with self._lock:
            if self.pos >= len(self.audio):
                self.pos = 0
            self.paused = not self.paused

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


class Timeline:
    """Who says what when, from the alignment; all times seconds into the take."""

    def __init__(self, alignment: dict, words: list[dict], t_start: float):
        self.words = words
        self.t_start = t_start
        self.sentences = alignment["sentences"]
        # transcript word -> (kind, note sentence, note word)
        self.kind: dict[int, tuple[str, int | None, int | None]] = {}
        for s in self.sentences:
            for wi, ti in enumerate(s["words"]):
                if ti is not None:
                    self.kind[ti] = ("word", s["sentence"], wi)
            for wi, last in s.get("joined", []):
                for ti in range(s["words"][wi] + 1, last + 1):
                    self.kind[ti] = ("word", s["sentence"], wi)
        for f in alignment["fillers"]:
            self.kind[f["word"]] = ("filler", None, None)
        for r in alignment["restarts"]:
            for ti in r["words"]:
                self.kind[ti] = ("restart", r["sentence"], None)
        for e in alignment["extras"]:
            for ti in e["words"]:
                self.kind[ti] = ("extra", e["sentence"], None)
        for ti in alignment.get("unsure", []):
            self.kind[ti] = ("unsure", None, None)
        self.starts = [w["start"] - t_start for w in words]
        spoken = [s for s in self.sentences if s["start"] is not None]
        self.spans = sorted((s["start"] - t_start, s["end"] - t_start, s["sentence"]) for s in spoken)

    def word_at(self, t: float) -> int | None:
        """Transcript word being said at t (or the one just finished, briefly)."""
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0:
            return None
        return i if t <= self.words[i]["end"] - self.t_start + 0.15 else None

    def sentence_at(self, t: float) -> int | None:
        """Sentence being spoken at t, else the last one started."""
        current = None
        for start, _, si in self.spans:
            if start <= t:
                current = si
        return current

    def next_start(self, t: float, step: int) -> float | None:
        starts = [s for s, _, _ in self.spans]
        if step > 0:
            return next((s for s in starts if s > t + 0.05), None)
        before = [s for s in starts if s < t - 0.5]  # a little slack: "previous" from just after a start
        return before[-1] if before else 0.0


def _clock(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def draw_caption(frame: np.ndarray, overlay: TextOverlay, timeline: Timeline, current: int | None, t: float) -> None:
    """What Whisper heard up to now, coloured by what the aligner made of it."""
    shown = [i for i, s in enumerate(timeline.starts) if s <= t][-10:]
    overlay.draw_caption(frame, [(timeline.words[i]["text"], timeline.kind.get(i, ("extra", None, None))[0],
                                  i == current) for i in shown])


def draw_strip(frame: np.ndarray, timeline: Timeline, sections: list[dict], alignment: dict, t: float,
               duration: float) -> None:
    """The whole take along the bottom, from the alignment; see render.draw_strip."""
    spans = [(start, end, timeline.sentences[si]["status"]) for start, end, si in timeline.spans]
    fillers = [f["t"] - timeline.t_start for f in alignment["fillers"]]
    restarts = [timeline.words[r["words"][0]]["start"] - timeline.t_start for r in alignment["restarts"]]
    _draw_strip(frame, spans, [sec["t"] for sec in sections[1:]], fillers, restarts, t, duration)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m palmcards.player", description="Play a take with its alignment.")
    ap.add_argument("session", type=Path)
    ap.add_argument("--take", type=int, help="1-based; default the last take")
    args = ap.parse_args(argv)

    session = Session.load(args.session)
    if not session.takes:
        print("This session has no takes.", file=sys.stderr)
        return 1
    take = session.take(args.take) if args.take else session.takes[-1]
    if take.alignment is None or take.transcript is None:
        print(f"Take {take.number} has no alignment yet. Run: python -m palmcards.speech {session.dir} "
              f"--take {take.number}", file=sys.stderr)
        return 1
    try:
        notes = session.notes_for(take)
    except LegacyNotes:
        print(f"warning: take {take.number} was recorded before PalmCards kept the notes; showing {session.notes} "
              "as it is now, which may differ from what was rehearsed", file=sys.stderr)
        notes = session.current_notes_unverified()
    if len(notes.sentences) != len(take.alignment["sentences"]):
        print("warning: the notes have changed since this take was aligned; the highlight may be off. "
              f"Re-align with: python -m palmcards.speech {session.dir} --realign", file=sys.stderr)
    words = json.loads((session.dir / take.transcript).read_text())["words"]
    timeline = Timeline(take.alignment, words, take.t_start)
    audio, rate = read_wav(session.dir / take.wav)

    overlay = TextOverlay(notes.sentences, SIZE)
    view = ViewState(app="player")
    playback = Playback(audio, rate)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, *SIZE)
    shown_sentence = None
    try:
        while True:
            t = playback.t
            ti = timeline.word_at(t)
            kind, k_sentence, k_word = timeline.kind.get(ti, (None, None, None)) if ti is not None else (None,) * 3
            si = timeline.sentence_at(t)
            if si is not None and si < len(notes.sentences):
                view.current = si
                if si != shown_sentence:
                    view.scroll = overlay.scroll_to(si)
                    shown_sentence = si
            if kind == "word" and k_sentence < len(notes.sentences):
                view.mode, view.level, view.hover = "browse", "word", Hit(k_sentence, k_word)
            else:
                view.mode, view.level, view.hover = "idle", None, None

            state = " PAUSED" if playback.paused else ""
            view.title = f"TAKE {take.number}  {_clock(t)} / {_clock(playback.duration)}{state}"
            if kind in ("restart", "extra", "unsure", "filler"):
                label = {"restart": "RESTART", "extra": "AD-LIB", "unsure": "UNSURE (LEFT OUT)", "filler": "FILLER"}[kind]
                view.status = f"{label}: {words[ti]['text'].upper()}"
            elif si is not None:
                s = take.alignment["sentences"][si]
                view.status = f"SENTENCE {si + 1}: {s['status'].upper()} {s['coverage']:.0%}"
                if s["misheard"]:
                    view.status += f"  (HEARD: {', '.join(words[s['words'][k]]['text'] for k in s['misheard']).upper()})"
            else:
                view.status = "SPACE: PLAY/PAUSE  ARROWS: SEEK  N/P: SENTENCE  Q: QUIT"

            frame = np.full((SIZE[1], SIZE[0], 3), bgr(PLAYER.background), np.uint8)
            overlay.draw(frame, view)
            draw_caption(frame, overlay, timeline, ti, t)
            draw_strip(frame, timeline, take.sections, take.alignment, t, playback.duration)
            cv2.imshow(WINDOW, frame)

            key = cv2.waitKeyEx(15)
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                playback.toggle()
            elif key in LEFT_KEYS:
                playback.seek(t - SEEK_S)
            elif key in RIGHT_KEYS:
                playback.seek(t + SEEK_S)
            elif key in (ord("n"), ord("p")):
                target = timeline.next_start(t, 1 if key == ord("n") else -1)
                if target is not None:
                    playback.seek(max(0.0, target - 0.2))
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        playback.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
