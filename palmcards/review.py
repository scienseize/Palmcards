"""Review's view of the analysed takes: how each went, side by side.

Nothing is judged: the takes are compared with one another on what was
measured (palmcards.metrics). While browsing, the take table sets the last
few full takes side by side (length, pace, fillers, long pauses, restarts,
pitch range, gaze, face touches, posture). A focused sentence lists every
take that said it, drills included, with that sentence's pace, fillers,
pitch range and gaze.

Each sentence also has a take it shows: the latest take in which it was
said, until another is picked by pointing at the take chips (the choice
stays after backing out, until a newer take says the sentence again). An
open palm (or `a`) plays the sentence from that take.
"""

from __future__ import annotations

from palmcards.config import REVIEW
from palmcards.metrics import sentence_speech, value
from palmcards.notes import Notes

TABLE_TAKES = 4  # full takes side by side in the take table


class Board:
    def __init__(self, notes: Notes):
        self.notes = notes
        self.rows: dict[int, dict[int, dict]] = {}  # take number -> sentence shown -> what was measured
        self.metrics: dict[int, dict] = {}  # take number -> metrics (palmcards.metrics), when measured
        self.durations: dict[int, float] = {}  # take number -> seconds
        self.drills: dict[int, int] = {}  # take number -> drilled sentence
        self.picked: dict[int, int] = {}  # sentence -> take chosen by pointing

    @property
    def takes(self) -> list[int]:
        return sorted(self.rows)

    def add(self, number: int, alignment: dict, metrics: dict | None = None, drill: int | None = None,
            sentence_map: dict[int, int] | None = None, duration_s: float | None = None) -> None:
        """An analysed take. `sentence_map` maps its sentences onto the notes
        shown (a take recorded with an earlier notes revision); sentences
        edited since have no place and are left out."""
        speech = ((metrics or {}).get("speech") or {}).get("sentences") or sentence_speech(alignment)
        gaze = {g["sentence"]: g for g in ((metrics or {}).get("gaze") or {}).get("sentences") or []}
        pitch = {v["sentence"]: v.get("pitch_range_st") for v in ((metrics or {}).get("voice") or {}).get("sentences") or []}
        place = (lambda i: sentence_map.get(i)) if sentence_map is not None else (lambda i: i)
        rows = {}
        for e in speech:
            i = place(e["sentence"])
            if i is None or i >= len(self.notes.sentences):
                continue
            rows[i] = {**e, "sentence": i, "pitch_range_st": pitch.get(e["sentence"]), "gaze": gaze.get(e["sentence"])}
        self.rows[number] = rows
        if metrics is not None:
            self.metrics[number] = metrics
        if duration_s is not None:
            self.durations[number] = duration_s
        if drill is not None and (d := place(drill)) is not None:
            self.drills[number] = d
        for i, row in rows.items():
            if row["status"] != "skipped":
                self.picked.pop(i, None)  # show the new take

    def said_in(self, sentence: int) -> list[int]:
        """Takes in which the sentence was said, oldest first."""
        return [n for n in self.takes if self.rows[n].get(sentence, {}).get("status", "skipped") != "skipped"]

    def shown(self, sentence: int) -> int | None:
        """The take this sentence shows (and plays)."""
        said = self.said_in(sentence)
        if self.picked.get(sentence) in said:
            return self.picked[sentence]
        if said:
            return said[-1]
        full = [n for n in self.takes if n not in self.drills]
        return max(full or self.takes, default=None)

    def take_name(self, number: int) -> str:
        return f"TAKE {number}" + (" (DRILL)" if number in self.drills else "")

    def take_label(self, sentence: int) -> str:
        """ "TAKE 3 (DRILL)  2 OF 3" for the label while a sentence is focused."""
        n = self.shown(sentence)
        if n is None:
            return "NO TAKE ANALYSED YET"
        label = self.take_name(n)
        said = self.said_in(sentence)
        if n in said and len(said) > 1:
            label += f"  {said.index(n) + 1} OF {len(said)}"
        elif n not in said:
            label += ": NOT SAID"
        return label

    def detail(self, sentence: int) -> tuple[str, ...]:
        """The focus panel's lines: the sentence in every take that said it,
        oldest first, the one it shows (and plays) marked."""
        said = self.said_in(sentence)
        if not said:
            return ("No take analysed yet.",) if not self.rows else ("Not said in any take yet.",)
        shown = self.shown(sentence)
        return tuple(("▸ " if n == shown else "  ") + self._sentence_line(n, sentence) for n in said)

    def _sentence_line(self, number: int, sentence: int) -> str:
        row = self.rows[number][sentence]
        parts = [f"{row['wpm']:.0f} wpm" if row["wpm"] is not None else "pace -"]
        n = len(row["fillers"])
        parts.append(f"{n} filler{'s' if n != 1 else ''}" if n else "no fillers")
        if row["pitch_range_st"] is not None:
            parts.append(f"pitch range {row['pitch_range_st']:g} st")
        parts.append(self._gaze_part(number, row["gaze"]))
        partly = " (partly said)" if row["status"] == "partial" else ""
        return f"{self.take_name(number).capitalize()}{partly}: {', '.join(p for p in parts if p)}"

    def _gaze_part(self, number: int, g: dict | None) -> str:
        """Where the speaker looked while saying the sentence in that take."""
        m = self.metrics.get(number)
        if m is None or (m.get("gaze") or {}).get("value", 0) is None:
            return ""
        judged = g["screen"] + g["away"] if g else 0
        if judged < REVIEW.min_gaze_readings:
            return ""
        return f"on screen {100 * g['screen'] / max(g['screen'] + g['away'] + g['unclear'], 1):.0f}%"

    def latest(self) -> int | None:
        """The newest analysed take."""
        return max(self.rows, default=None)

    def take_table(self, n: int = TABLE_TAKES) -> tuple[str, ...]:
        """The last `n` full takes side by side (a drill has only one sentence:
        it is compared in the sentence's panel instead). One line per metric,
        every line the same width (monospace), "-" where not measured."""
        full = [t for t in self.takes if t not in self.drills][-n:]
        if not full:
            return ()
        cols = [self._take_column(t) for t in full]
        rows = [("", [f"TAKE {t}" for t in full])] + [(name, [c[k] for c in cols]) for k, name in enumerate(METRIC_ROWS)]
        name_w = max(len(r[0]) for r in rows)
        col_w = max(len(v) for _, vals in rows for v in vals)
        return tuple(f"{name:<{name_w}}" + "".join(f"  {v:>{col_w}}" for v in vals) for name, vals in rows)

    def _take_column(self, number: int) -> list[str]:
        m = self.metrics.get(number) or {}
        speech, voice, gaze = m.get("speech"), m.get("voice"), m.get("gaze")
        hands, posture = m.get("hands"), m.get("posture")

        def fmt(v, form: str) -> str:
            return "-" if v is None else form.format(v)

        secs = self.durations.get(number)
        length = "-" if secs is None else "{}:{:02d}".format(*divmod(round(secs), 60))
        c = (gaze or {}).get("counts")
        on_screen = None
        if c and value(gaze, "screen_share") is not None:
            total = max(c["camera"] + c["notes"] + c["away"] + c["unclear"], 1)
            on_screen = 100 * (c["camera"] + c["notes"]) / total
        tilted = value(posture, "tilted_share")
        return [
            length,
            fmt(value(speech, "pace_wpm"), "{:.0f}"),
            fmt(value(speech, "fillers_per_min"), "{:g}"),
            fmt(value(speech, "long_pauses") if value(speech, "long_pauses") is not None
                else value(speech, "unplanned_long_pauses"), "{}"),  # metrics before version 5
            fmt(value(speech, "restarts"), "{}"),
            fmt(value(voice, "pitch_range_st"), "{:g}"),
            fmt(on_screen, "{:.0f}%"),
            fmt(value(hands, "face_touches"), "{}"),
            fmt(None if tilted is None else 100 * tilted, "{:.0f}%"),
        ]


METRIC_ROWS = ("LENGTH", "WPM", "FILLERS/MIN", "LONG PAUSES", "RESTARTS", "PITCH RANGE ST", "ON SCREEN",
               "FACE TOUCHES", "SHOULDERS TILTED")
