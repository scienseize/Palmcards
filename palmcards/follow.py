"""Where the speaker is in the notes, from live words (milestone 6b).

    f = Follower(notes, section=0)
    f.update(words) -> [FollowEvent]    words: newly confirmed LiveWords
    f.flick(t)      -> [FollowEvent]    the manual override: next section

The newest live words (FOLLOW.tail_words) are aligned with the M5 aligner
against the current section plus the opening of the next one only. The
place is the end of the latest run of note words matched in a row. Fillers
don't break a run, nor does one note word the live recognition dropped or
one word it misheard (FOLLOW.run_gap): live words are often lost. The alignment only goes forward through the
notes, so when the newest words are left over after it (a re-read of an
earlier sentence), they are aligned again on their own.

  - a run of FOLLOW.forward_words ending in a later sentence of the section
    moves the highlight on to it,
  - once the speaker is probably on the sentence's last word, the highlight
    goes on to the next one: live words lag the voice, so where the voice
    is now is estimated as the last confirmed word plus the lag times the
    speaking rate (never more than FOLLOW.handoff_words early). The words
    that finish the old sentence arrive after that and don't pull it back,
  - a run of FOLLOW.back_words ending in an earlier one moves it back
    (a re-read, more evidence needed as it's rarer),
  - a run with FOLLOW.forward_words in the next section's opening advances
    the section. Sections only ever move forward.

Going off script matches nothing, so the follow stalls, which is correct.

LiveFollow runs it during a take: microphone blocks in, events out.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

from palmcards.align import align
from palmcards.asr import LiveWord
from palmcards.config import ALIGN, FOLLOW
from palmcards.notes import Notes, normalize


@dataclass(frozen=True)
class FollowEvent:
    kind: str  # "sentence" (Notes.sentences index) | "section" (Notes.sections index)
    index: int
    t: float  # app time it was decided: when the deciding word was confirmed
    source: str = "voice"  # "voice" | "flick"


@dataclass(frozen=True)
class Run:
    length: int  # note words matched in a row
    sentences: tuple[int, ...]  # the sentence of each, in order
    first_word: int  # tail index of the run's first word
    after: int  # tail index just past the run's last word
    trailing: int  # words after the run that could have matched but didn't (not fillers)
    last_word: int = 0  # the run's last note word, as a word index in its sentence


class Follower:
    def __init__(self, notes: Notes, section: int = 0):
        self.sentences = notes.sentences
        self.sections = len(notes.sections)
        self.section = section
        self.sentence = self._first(section)
        self.tail: list[LiveWord] = []
        self._handoff: tuple[int, int] | None = None  # (sentence handed on from, its word at that moment)

    def _first(self, section: int) -> int:
        return next((i for i, s in enumerate(self.sentences) if s.section >= section), len(self.sentences) - 1)

    def scope(self) -> list[int]:
        """Sentences the live words are matched against: this section's, then
        the next section's up to at least FOLLOW.ahead_words words."""
        out = [i for i, s in enumerate(self.sentences) if s.section == self.section]
        ahead = 0
        for i, s in enumerate(self.sentences):
            if s.section == self.section + 1 and ahead < FOLLOW.ahead_words:
                out.append(i)
                ahead += len(s.words)
        return out

    def last_run(self) -> Run | None:
        """The run of note words matched in a row that ends at the newest match,
        or in the newest words if they were left over."""
        run = self._run(0)
        if run is not None and run.trailing >= FOLLOW.forward_words:
            run = self._run(run.after) or run
        return run

    def _run(self, offset: int) -> Run | None:
        """The last run within tail[offset:]; its indices are into the whole tail."""
        scope = self.scope()
        tail = self.tail[offset:]
        words = [{"text": w.text, "start": w.start, "end": w.end, "probability": w.probability} for w in tail]
        al = align([[w.norm for w in self.sentences[i].words] for i in scope], words)
        pairs = []  # (tail index, flat note index, sentence, word in the sentence)
        k = 0
        for entry, i in zip(al["sentences"], scope):
            pairs += [(t, k + wi, i, wi) for wi, t in enumerate(entry["words"]) if t is not None]
            k += len(entry["words"])
        if not pairs:
            return None
        pairs.sort()
        paired = {t for t, _, _, _ in pairs}
        fillers = set(ALIGN.fillers)
        # Words that don't break a run: fillers, words the aligner left out as
        # unsure or punctuation, and the second half of a split word.
        skippable = {t for t, w in enumerate(tail) if normalize(w.text) in fillers or not normalize(w.text)}
        skippable |= set(al["unsure"])
        for entry in al["sentences"]:
            skippable |= {last for _, last in entry.get("joined", [])}
        end = len(pairs) - 1
        start = end
        while start > 0:
            (t0, k0, _, _), (t1, k1, _, _) = pairs[start - 1], pairs[start]
            odd = sum(t not in skippable and t not in paired for t in range(t0 + 1, t1))
            if k1 - k0 - 1 > FOLLOW.run_gap or odd > FOLLOW.run_gap:
                break
            start -= 1
        run = pairs[start:end + 1]
        last = run[-1][0]
        trailing = sum(t not in skippable for t in range(last + 1, len(tail)))
        return Run(len(run), tuple(s for _, _, s, _ in run), offset + run[0][0], offset + last + 1, trailing,
                   run[-1][3])

    def update(self, words: list[LiveWord]) -> list[FollowEvent]:
        """Take newly confirmed live words; returns where the follow moved."""
        if not words:
            return []
        self.tail = (self.tail + list(words))[-FOLLOW.tail_words:]
        run = self.last_run()
        if run is None:
            return []
        t = max(w.confirmed_at if w.confirmed_at is not None else w.end for w in words)
        s = run.sentences[-1]
        if self._handoff is not None and s == self.sentence:
            self._handoff = None  # the voice has reached the new sentence: the handoff is over
        section = self.sentences[s].section
        if section == self.section + 1:
            if sum(self.sentences[x].section == section for x in run.sentences) < FOLLOW.forward_words:
                return []
            moved = s != self.sentence
            self.section, self.sentence, self._handoff = section, s, None
            self.tail = self.tail[run.first_word:]  # what came before belongs to the last section
            return [FollowEvent("section", section, t)] + ([FollowEvent("sentence", s, t)] if moved else [])
        if s > self.sentence and run.length >= FOLLOW.forward_words:
            self.sentence, self._handoff = s, None
            return [FollowEvent("sentence", s, t)]
        finishing = self._handoff is not None and s == self._handoff[0] and run.last_word >= self._handoff[1]
        if s < self.sentence and run.length >= FOLLOW.back_words and not finishing:
            self.sentence, self._handoff = s, None
            self.tail = self.tail[run.first_word:]  # the later words were read before the re-read
            return [FollowEvent("sentence", s, t)]
        # The voice is probably on the sentence's last word: the highlight goes on now.
        last = len(self.sentences[s].words) - 1
        ahead = self._words_ahead(t)
        if s == self.sentence and run.length >= FOLLOW.forward_words and last - run.last_word <= FOLLOW.handoff_words \
                and run.last_word + ahead >= last and s + 1 in self.scope():
            self.sentence, self._handoff = s + 1, (s, run.last_word)
            return [FollowEvent("sentence", s + 1, t)]
        return []

    def _words_ahead(self, now: float) -> float:
        """Words the voice has probably said since the newest confirmed one:
        the time since it ended times the recent speaking rate."""
        recent = self.tail[-6:]
        rate = 2.5  # words per second when there's too little to tell
        if len(recent) >= 3 and recent[-1].start > recent[0].start:
            rate = min(4.0, max(1.5, (len(recent) - 1) / (recent[-1].start - recent[0].start)))
        return max(0.0, now - self.tail[-1].end) * rate

    def flick(self, t: float) -> list[FollowEvent]:
        """The manual override: on to the next section's first sentence."""
        if self.section + 1 >= self.sections:
            return []
        self.section += 1
        self.sentence = self._first(self.section)
        self.tail, self._handoff = [], None
        return [FollowEvent("section", self.section, t, "flick"), FollowEvent("sentence", self.sentence, t, "flick")]


class LiveFollow:
    """Voice follow during a take, around a live stream (palmcards.asr).

    tap(block, t_end) is for the audio callback: it appends to a bounded
    deque and returns; it never waits and never fails the recording. A
    feeder thread resamples what arrived to 16 kHz and feeds the stream; the
    app polls on its own thread for FollowEvents, which move the display
    only (the recorded transcript comes after the take, as always).

    Anything that goes wrong (the model doesn't load, the reader dies) turns
    the follow off for the rest of the take: `state` is "failed" and `error`
    says why; recording and manual navigation carry on. One stream serves
    the whole app run (loaded at the first count-in); reset() clears it
    between takes.
    """

    def __init__(self, notes: Notes, make_stream, mic_rate: int, engine: str = "", model: str = ""):
        self.notes = notes
        self._make_stream = make_stream
        self.mic_rate = mic_rate
        self.engine, self.model = engine, model
        self.stream = None
        self.state = "off"  # off | starting | following | failed
        self.error = ""
        self.follower: Follower | None = None
        self._blocks: deque = deque(maxlen=FOLLOW.tap_blocks)
        self.dropped = 0  # blocks the feeder never saw (the deque overflowed)
        self._feeder: threading.Thread | None = None
        self._stop = threading.Event()
        self._lags: list[float] = []
        self._words = 0

    def prepare(self) -> None:
        """Start loading the model (during the count-in)."""
        # Resampling imports scipy.signal, slow the first time: do it now, off the frame loop.
        threading.Thread(target=__import__, args=("scipy.signal",), name="live-warm", daemon=True).start()
        if self.stream is None and self.state != "failed":
            try:
                self.stream = self._make_stream()
                self.state = "starting"
            except Exception as exc:  # no engine, no permission, bad settings
                self._fail(f"{type(exc).__name__}: {exc}")

    def _fail(self, why: str) -> None:
        self.state, self.error = "failed", why

    def start_take(self, section: int) -> None:
        self.prepare()
        self.follower = Follower(self.notes, section)
        self._blocks.clear()
        self._lags, self._words, self.dropped = [], 0, 0
        if self.stream is None:
            return
        try:
            self.stream.reset()
        except Exception as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return
        self._stop.clear()
        self._feeder = threading.Thread(target=self._feed, name="live-feed", daemon=True)
        self._feeder.start()

    def tap(self, block: np.ndarray, t_end: float) -> None:
        """From the audio callback: never blocks."""
        if len(self._blocks) == self._blocks.maxlen:
            self.dropped += 1
        self._blocks.append((t_end, block))

    def _feed(self) -> None:
        from palmcards.audio import resample

        while not self._stop.wait(FOLLOW.feed_s):
            chunk, t_end = [], None
            while self._blocks:
                t_end, block = self._blocks.popleft()
                chunk.append(block)
            if not chunk or self.state == "failed":
                continue
            try:
                self.stream.feed(resample(np.concatenate(chunk), self.mic_rate), t_end)
            except Exception as exc:
                self._fail(f"{type(exc).__name__}: {exc}")

    def poll(self) -> list[FollowEvent]:
        """Where the voice moved the follow since the last poll."""
        if self.stream is None or self.follower is None or self.state == "failed":
            return []
        try:
            words = self.stream.poll()
        except Exception as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return []
        if self.stream.state == "failed":
            self._fail(self.stream.errors[-1] if self.stream.errors else "the live stream failed")
            return []
        if self.stream.state == "ready":
            self.state = "following"
        self._words += len(words)
        self._lags += [w.confirmed_at - w.end for w in words if w.confirmed_at is not None]
        try:
            return self.follower.update(words)
        except Exception as exc:  # the follow is display only: never let it take the app down
            self._fail(f"{type(exc).__name__}: {exc}")
            return []

    def flick(self, t: float) -> list[FollowEvent]:
        return self.follower.flick(t) if self.follower else []

    def jump(self, section: int, sentence: int) -> None:
        """A manual move (keys): the follow carries on from there."""
        if self.follower is not None:
            self.follower.section, self.follower.sentence, self.follower.tail = section, sentence, []
            self.follower._handoff = None

    def stop_take(self) -> dict:
        """Stop feeding; the take's live statistics, for session.json."""
        self._stop.set()
        if self._feeder is not None:
            self._feeder.join(timeout=1.0)
            self._feeder = None
        lags = sorted(self._lags)
        stats = {"engine": self.engine, "model": self.model, "state": self.state, "words": self._words,
                 "dropped_blocks": self.dropped}
        if lags:
            stats["lag_median_s"] = round(lags[len(lags) // 2], 3)
            stats["lag_p90_s"] = round(lags[min(len(lags) - 1, int(len(lags) * 0.9))], 3)
        if self.error:
            stats["error"] = self.error
        self.follower = None
        return stats

    def close(self) -> None:
        self._stop.set()
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
