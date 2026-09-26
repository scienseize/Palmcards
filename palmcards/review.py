"""Review's view of the judged takes.

Each sentence shows one take: by default the latest take in which it was
said (so after a drill, the drilled sentence shows the drill and the rest
the last full take). The take dial on a focused sentence steps through the
takes in which that sentence was said, and the choice stays after backing
out, until a newer take says the sentence again.

The app hands this the verdicts (palmcards.cues) and metrics
(palmcards.metrics) as each take is judged; it builds what the overlay
draws: the verdict of every mark, the focus panel's lines (with where the
speaker looked while saying the sentence), the take label, and the take's
summary card.
"""

from __future__ import annotations

from palmcards.config import REVIEW
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
        self.metrics: dict[int, dict] = {}  # take number -> metrics (palmcards.metrics), when analysed
        self.gaze: dict[int, dict[int, dict]] = {}  # take number -> sentence shown -> gaze counts

    def add(self, number: int, verdicts: dict, drill: int | None = None,
            sentence_map: dict[int, int] | None = None, metrics: dict | None = None) -> None:
        """A judged take. `sentence_map` maps its sentences onto the notes shown
        (a take recorded with an earlier notes revision); sentences edited
        since have no place and are left out."""
        if metrics is not None:
            self.metrics[number] = metrics
            per = (metrics.get("gaze") or {}).get("sentences") or []
            self.gaze[number] = {sentence_map[g["sentence"]] if sentence_map is not None else g["sentence"]: g
                                 for g in per if sentence_map is None or g["sentence"] in sentence_map}
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
        if line := self._gaze_line(n, sentence):
            lines.append(("", line))
        return tuple(lines)

    def _gaze_line(self, number: int, sentence: int) -> str:
        """Where the speaker looked while saying the sentence in that take, or why it isn't known."""
        m = self.metrics.get(number)
        if m is None:
            return ""
        gaze = m.get("gaze") or {}
        if gaze.get("value", 0) is None:
            return "Gaze not measured."
        g = self.gaze.get(number, {}).get(sentence)
        judged = g["screen"] + g["away"] if g else 0
        if judged < REVIEW.min_gaze_readings:
            return f"Gaze: too few readings ({judged})."
        return f"{_gaze_shares(g['screen'], g['away'], g['unclear']).capitalize()}."

    def latest(self) -> int | None:
        """The newest judged take."""
        return max(self.takes, default=None)

    def take_summary(self, number: int, duration_s: float | None = None) -> tuple[str, ...]:
        """The take's summary card: marks, speech, gaze, hands, posture; "NOT MEASURED"
        where a metric has too little to go on (its reason is in session.json)."""
        if number not in self.takes:
            return ()
        head = f"TAKE {number}" + (" (DRILL)" if number in self.drills else "")
        if duration_s:
            m_, s_ = divmod(round(duration_s), 60)
            head += f"  {m_}:{s_:02d}"
        lines = [head, self.summary(number)]
        m = self.metrics.get(number)
        if m is None:
            return tuple(lines + ["METRICS NOT MEASURED YET"])
        v = lambda d, k: (d.get(k) or {}).get("value")  # noqa: E731
        speech = m.get("speech", {})
        said = [f"{v(speech, 'pace_wpm'):.0f} WPM" if v(speech, "pace_wpm") is not None else "",
                f"{v(speech, 'fillers_per_min'):g} FILLERS/MIN" if v(speech, "fillers_per_min") is not None else ""]
        lines.append("  ".join(p for p in said if p) or "PACE NOT MEASURED")
        gaze = m.get("gaze") or {}
        if (c := gaze.get("counts")) and v(gaze, "screen_share") is not None:
            lines.append(_gaze_shares(c["camera"] + c["notes"], c["away"], c["unclear"]).upper().replace(",", " "))
        else:
            lines.append("GAZE NOT MEASURED")
        hands = m.get("hands", {})
        parts = []
        if v(hands, "in_view_share") is not None:
            parts.append(f"HANDS IN VIEW {100 * v(hands, 'in_view_share'):.0f}%")
        if v(hands, "face_touches") is not None:
            n = v(hands, "face_touches")
            parts.append(f"{n} FACE TOUCH{'ES' if n != 1 else ''}")
        lines.append("  ".join(parts) or "HANDS NOT MEASURED")
        posture = m.get("posture") or {}
        if v(posture, "tilted_share") is not None:
            lines.append(f"SHOULDERS TILTED {100 * v(posture, 'tilted_share'):.0f}%  "
                         f"HEAD DROPPED {100 * v(posture, 'head_dropped_share'):.0f}%")
        else:
            lines.append("POSTURE NOT MEASURED")
        return tuple(lines)

    def summary(self, number: int) -> str:
        """ "5 HIT, 2 MISSED, 1 UNCLEAR" for the take's status line: each count
        on its own, as unclear (too little evidence) is not a miss."""
        c = self.takes[number]["counts"]
        if not sum(c.values()):
            return "NO MARKS"
        parts = [f"{c['hit']} HIT"] + [f"{c[k]} {k.upper()}" for k in ("missed", "unclear", "skipped") if c[k]]
        return ", ".join(parts)


def _gaze_shares(screen: int, away: int, unclear: int) -> str:
    """ "on screen 83%, away 3%, unclear 14%": shares of every reading while speaking, adding up to 100."""
    total = max(screen + away + unclear, 1)
    parts = [f"on screen {100 * screen / total:.0f}%", f"away {100 * away / total:.0f}%"]
    if unclear:
        parts.append(f"unclear {100 * unclear / total:.0f}%")
    return ", ".join(parts)
