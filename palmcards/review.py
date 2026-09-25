"""Review's view of the judged takes.

Each sentence shows one take: by default the latest take in which it was
said (so after a drill, the drilled sentence shows the drill and the rest
the last full take). The take dial on a focused sentence steps through the
takes in which that sentence was said, and the choice stays after backing
out, until a newer take says the sentence again.

The app hands this the verdicts (palmcards.cues) as each take is judged;
it builds what the overlay draws: the verdict of every mark, the focus
panel's lines and the take label.
"""

from __future__ import annotations

from palmcards.cues import ENDINGS, PACES, mark_label
from palmcards.notes import Notes


def _position(m: dict) -> float:
    """Reading order of a mark: pace first, then by word, ending last."""
    if m["kind"] in PACES:
        return -1.0
    if m["kind"] in ENDINGS:
        return float("inf")
    return m["word"] - (0.5 if m["kind"] != "stress" else 0.0)


class Board:
    def __init__(self, notes: Notes):
        self.notes = notes
        self.takes: dict[int, dict] = {}  # take number -> verdicts (palmcards.cues)
        self.drills: dict[int, int] = {}  # take number -> drilled sentence
        self.picked: dict[int, int] = {}  # sentence -> take chosen with the dial

    def add(self, number: int, verdicts: dict, drill: int | None = None,
            sentence_map: dict[int, int] | None = None) -> None:
        """A judged take. `sentence_map` maps its sentences onto the notes shown
        (a take recorded with an earlier notes revision); sentences edited
        since have no place and are left out."""
        if sentence_map is not None:
            placed = {sentence_map[s["sentence"]]: s for s in verdicts["sentences"] if s["sentence"] in sentence_map}
            verdicts = {**verdicts, "sentences": [
                {**placed[i], "sentence": i} if i in placed else
                {"sentence": i, "status": "skipped", "wpm": None, "fillers": [], "marks": []}
                for i in range(len(self.notes.sentences))]}
            drill = sentence_map.get(drill) if drill is not None else None
        self.takes[number] = verdicts
        if drill is not None:
            self.drills[number] = drill
        for s in verdicts["sentences"]:
            if s["status"] != "skipped":
                self.picked.pop(s["sentence"], None)  # show the new take

    def said_in(self, sentence: int) -> list[int]:
        """Takes in which the sentence was said, oldest first."""
        return [n for n, v in sorted(self.takes.items())
                if sentence < len(v["sentences"]) and v["sentences"][sentence]["status"] != "skipped"]

    def shown(self, sentence: int) -> int | None:
        """The take this sentence shows."""
        said = self.said_in(sentence)
        if self.picked.get(sentence) in said:
            return self.picked[sentence]
        if said:
            return said[-1]
        full = [n for n in self.takes if n not in self.drills]
        return max(full or self.takes, default=None)

    def step(self, sentence: int, delta: int) -> None:
        """Turn the take dial: `delta` takes later (or earlier), clamped."""
        said = self.said_in(sentence)
        if not said or not delta:
            return
        now = self.shown(sentence)
        i = said.index(now) if now in said else len(said) - 1
        self.picked[sentence] = said[min(max(i + delta, 0), len(said) - 1)]

    def _sentence(self, sentence: int) -> tuple[int, dict] | None:
        n = self.shown(sentence)
        if n is None or sentence >= len(self.takes[n]["sentences"]):
            return None
        return n, self.takes[n]["sentences"][sentence]

    def mark_verdicts(self) -> tuple[tuple[str, ...], ...]:
        """Per sentence, each mark's verdict in the take it shows ("" if none)."""
        out = []
        for i in range(len(self.notes.sentences)):
            got = self._sentence(i)
            out.append(tuple(m["verdict"] for m in got[1]["marks"]) if got else ())
        return tuple(out)

    def take_label(self, sentence: int) -> str:
        """ "TAKE 3 (DRILL)  2 OF 3" for the label while a sentence is focused."""
        n = self.shown(sentence)
        if n is None:
            return "NO TAKE JUDGED YET"
        label = f"TAKE {n}" + (" (DRILL)" if n in self.drills else "")
        said = self.said_in(sentence)
        if n in said and len(said) > 1:
            label += f"  {said.index(n) + 1} OF {len(said)}"
        elif n not in said:
            label += ": NOT SAID"
        return label

    def detail(self, sentence: int) -> tuple[tuple[str, str], ...]:
        """(verdict or "", line) for the focus panel: each mark, then pace and fillers."""
        got = self._sentence(sentence)
        if got is None:
            return (("", "No take judged yet."),)
        n, s = got
        texts = [w.text for w in self.notes.sentences[sentence].words]
        lines = []
        for m in sorted(s["marks"], key=_position):
            lines.append((m["verdict"], f"{mark_label(m['kind'], m['word'], texts)}  {m['verdict'].upper()}  "
                                        f"{m['reason']}"))
        if s["status"] == "skipped":
            lines.append(("", "Not said in this take."))
            return tuple(lines)
        if s["status"] == "partial":
            lines.append(("", "Only partly said."))
        base = self.takes[n]["take_wpm"]
        if s["wpm"] is not None:
            lines.append(("", f"Pace {s['wpm']:.0f} wpm" + (f", take {base:.0f}" if base else "")))
        fillers = s["fillers"]
        lines.append(("", f"Fillers: {', '.join(fillers)}" if fillers else "No fillers."))
        return tuple(lines)

    def summary(self, number: int) -> str:
        """ "5 HIT, 2 MISSED, 1 UNCLEAR" for the take's status line: each count
        on its own, as unclear (too little evidence) is not a miss."""
        c = self.takes[number]["counts"]
        if not sum(c.values()):
            return "NO MARKS"
        parts = [f"{c['hit']} HIT"] + [f"{c[k]} {k.upper()}" for k in ("missed", "unclear", "skipped") if c[k]]
        return ", ".join(parts)
