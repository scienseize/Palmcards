"""The home card: switch to another speech without leaving the app.

Shown at launch when PalmCards reopened the file from last time, and on key
l in Prepare or Review. One row per choice: carry on with the speech open now,
start it over, carry on with another speech practised before, add slides to the
written notes (palmcards.slides), or open a file (notes, or slides as the speech).
A row is chosen as a sentence is in Review (one or two fingers to browse, a
tap to select) or with keys (j / k, Enter, 1-9). Choosing anything but the
speech open now closes everything and opens it, as at launch (`Next`)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from palmcards.config import CURSOR
from palmcards.data import sessions, started
from palmcards.gestures import REVIEW_LEVEL_OF_SHAPE, Grammar, GestureLog, Hand

OTHERS = 4  # speeches practised before, newest first


@dataclass(frozen=True)
class Next:
    """What to open once everything is closed: a notes file (`new`: a new session
    even if the file is unchanged), or a saved session to continue."""
    path: Path | None = None
    folder: Path | None = None
    new: bool = False


@dataclass(frozen=True)
class Entry:
    title: str
    detail: str
    next: Next | None = None  # None: stay with the speech open now (or, with `pick`, the Open dialog)
    pick: bool = False
    slides: bool = False  # the Open dialog for a PDF of slides, shown beside the notes in a take


def _takes(n: int) -> str:
    return "No takes yet" if n == 0 else f"{n} take{'' if n == 1 else 's'}"


def recent(root: Path, skip: Path | None = None, limit: int = OTHERS) -> list[tuple[Path, Path, int]]:
    """(session folder, notes file, takes) for the newest session of each notes file
    practised before, newest first, without `skip` (the file open now)."""
    seen = {Path(skip).expanduser().resolve()} if skip else set()
    out = []
    for folder in reversed(sessions(root)):
        try:
            data = json.loads((folder / "session.json").read_text())
            notes = Path(data["notes"]).expanduser().resolve()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if notes in seen or data.get("current") is None:  # None: from before the notes were kept
            continue
        seen.add(notes)
        out.append((folder, notes, len(data.get("takes") or ())))
        if len(out) == limit:
            break
    return out


def entries(root: Path, folder: Path, notes: Path, takes: int,
            slides: tuple[str, int | None] | None = None) -> list[Entry]:
    """The rows: this speech (continue, start over), the others, slides, a file. `slides`:
    the attached PDF's name and pages (None: the file is missing or changed)."""
    notes = Path(notes)
    rows = [Entry(f"Continue {notes.name}", f"{_takes(takes)} · since {started(folder):%-d %B}")]
    if notes.is_file():
        rows.append(Entry(f"Start {notes.name} over", "A new session · your takes stay saved",
                          Next(path=notes, new=True)))
    for other, path, n in recent(root, notes):
        rows.append(Entry(path.name, f"{_takes(n)} · last {started(other):%-d %B}", Next(folder=other)))
    if notes.suffix.lower() != ".pdf":  # a PDF speech is its own slides
        if slides is None:
            rows.append(Entry("Add slides…", "A PDF from Keynote, PowerPoint or Google Slides", slides=True))
        elif slides[1] is None:
            rows.append(Entry(f"Slides missing: {slides[0]}", "Choose the PDF again", slides=True))
        else:
            rows.append(Entry(f"Slides: {slides[0]}", f"{slides[1]} slides · a clicker moves them in a take",
                              slides=True))
    rows.append(Entry("Open another file…", "A .txt, .md, .docx, or slides as .pdf", pick=True))
    return rows


def row_at(v: float, n: int) -> int:
    """The row under the hand: the hand box's height (inside its scroll bands) shared by the rows."""
    band = CURSOR.edge_band
    return min(n - 1, max(0, int((v - band) / (1 - 2 * band) * n)))


class Home:
    """The card's state: its rows, the one picked, and a grammar of its own, so
    nothing the hand does while choosing reaches Prepare or Review (a fist starts
    no take)."""

    def __init__(self, rows: list[Entry], frame_size: tuple[int, int], log: GestureLog | None = None):
        self.rows = rows
        self.picked = 0
        self.grammar = Grammar(frame_size, log)
        self.grammar.operations = False
        self.grammar.shape_levels = REVIEW_LEVEL_OF_SHAPE  # sentences and paragraphs: a tap selects

    def update(self, hands: list[Hand], t: float) -> Entry | None:
        """The hand result: the row chosen by a tap, else None."""
        events = self.grammar.update(hands, t)
        gs = self.grammar.state
        if gs.mode == "browse" and gs.cursor is not None:
            self.picked = row_at(gs.cursor[1], len(self.rows))
        if gs.mode == "focus":
            self.grammar.reset()
        return self.rows[self.picked] if any(e.kind == "focus" for e in events) else None

    def step(self, by: int) -> None:
        self.picked = min(max(self.picked + by, 0), len(self.rows) - 1)

    def choose(self, i: int) -> Entry | None:
        if 0 <= i < len(self.rows):
            self.picked = i
            return self.rows[i]
        return None

    @property
    def browsing(self) -> bool:
        return self.grammar.state.mode == "browse"
