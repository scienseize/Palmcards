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
  - a run of FOLLOW.back_words ending in an earlier one moves it back
    (a re-read, more evidence needed as it's rarer),
  - a run with FOLLOW.forward_words in the next section's opening advances
    the section. Sections only ever move forward.

Going off script matches nothing, so the follow stalls, which is correct.
"""

from __future__ import annotations

from dataclasses import dataclass

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


class Follower:
    def __init__(self, notes: Notes, section: int = 0):
        self.sentences = notes.sentences
        self.sections = len(notes.sections)
        self.section = section
        self.sentence = self._first(section)
        self.tail: list[LiveWord] = []

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
        pairs = []  # (tail index, flat note index, sentence)
        k = 0
        for entry, i in zip(al["sentences"], scope):
            pairs += [(t, k + wi, i) for wi, t in enumerate(entry["words"]) if t is not None]
            k += len(entry["words"])
        if not pairs:
            return None
        pairs.sort()
        paired = {t for t, _, _ in pairs}
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
            (t0, k0, _), (t1, k1, _) = pairs[start - 1], pairs[start]
            odd = sum(t not in skippable and t not in paired for t in range(t0 + 1, t1))
            if k1 - k0 - 1 > FOLLOW.run_gap or odd > FOLLOW.run_gap:
                break
            start -= 1
        run = pairs[start:end + 1]
        last = run[-1][0]
        trailing = sum(t not in skippable for t in range(last + 1, len(tail)))
        return Run(len(run), tuple(s for _, _, s in run), offset + run[0][0], offset + last + 1, trailing)

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
        section = self.sentences[s].section
        if section == self.section + 1:
            if sum(self.sentences[x].section == section for x in run.sentences) < FOLLOW.forward_words:
                return []
            self.section, self.sentence = section, s
            self.tail = self.tail[run.first_word:]  # what came before belongs to the last section
            return [FollowEvent("section", section, t), FollowEvent("sentence", s, t)]
        if s > self.sentence and run.length >= FOLLOW.forward_words:
            self.sentence = s
            return [FollowEvent("sentence", s, t)]
        if s < self.sentence and run.length >= FOLLOW.back_words:
            self.sentence = s
            self.tail = self.tail[run.first_word:]  # the later words were read before the re-read
            return [FollowEvent("sentence", s, t)]
        return []

    def flick(self, t: float) -> list[FollowEvent]:
        """The manual override: on to the next section's first sentence."""
        if self.section + 1 >= self.sections:
            return []
        self.section += 1
        self.sentence = self._first(self.section)
        self.tail = []
        return [FollowEvent("section", self.section, t, "flick"), FollowEvent("sentence", self.sentence, t, "flick")]
