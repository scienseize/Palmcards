"""Progress over days, and readiness, across every session of the same notes.

    points(session) -> [Point]                 one session's analysed takes
    history(root, stem, skip) -> [Point]       every other session of the notes
    days_table(points, target_s) -> lines      Review's Days view (key i)
    readiness(points, target_s) -> str         the coach card's last row

A changed notes file starts a new session, so a talk's history is every
session folder named for the file's stem (as data.latest_session_for reads
them). A point is a full take or an answer (a Q&A round's); practice takes of
one sentence are left out: they say nothing about the whole talk.

Ready means the last COACH.ready_takes full takes in a row each: said
COACH.ready_points of the key points, came in on time (when there is a
target), with at most COACH.ready_fillers fillers a minute; and, when
questions were practised, the last round's answers each under
QA.answer_max_s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from palmcards import fade
from palmcards.coach import mmss
from palmcards.config import COACH, QA
from palmcards.data import sessions
from palmcards.metrics import value


@dataclass(frozen=True)
class Point:
    when: datetime
    session: str
    number: int
    length_s: float
    answer: bool = False
    key_points: float | None = None  # share of the note sentences said
    fillers_per_min: float | None = None
    long_pauses: int | None = None
    memory: float | None = None  # fading cards: share of blank words said unprompted


def points(session) -> list[Point]:
    """A session's analysed takes, oldest first: full takes and answers."""
    out = []
    for take in session.takes:
        if take.alignment is None or take.drill is not None or take.status != "saved":
            continue
        try:
            when = datetime.fromisoformat(take.started)
        except (TypeError, ValueError):
            continue
        speech = (take.metrics or {}).get("speech")
        said = value(speech, "key_points")
        if said is None and not take.answer:  # metrics before version 6: from the alignment
            sentences = take.alignment.get("sentences", [])
            said = sum(s["status"] != "skipped" for s in sentences) / len(sentences) if sentences else None
        memory = None
        if take.fade and not take.answer:
            memory = fade.from_memory(take.alignment, take.fade.get("hidden", []), take.fade.get("prompted", []))
        out.append(Point(when, session.dir.name, take.number, take.duration_s, bool(take.answer),
                         None if take.answer else said, value(speech, "fillers_per_min"),
                         value(speech, "long_pauses"), memory))
    return out


def history(root: Path, stem: str, skip: Path | None = None) -> list[Point]:
    """Every analysed take of the notes named `stem` in the sessions under `root`,
    but the session at `skip` (the one open: the app reads it live)."""
    from palmcards.session import Session, SessionError

    out: list[Point] = []
    for folder in sessions(root):
        if folder.name[16:].rsplit("-", 1)[0] != stem or (skip is not None and folder == skip):
            continue
        try:
            out += points(Session.load(folder))
        except (SessionError, OSError, ValueError, KeyError, TypeError):
            continue  # an unreadable session is left out, not a reason to fail
    return sorted(out, key=lambda p: p.when)


def on_time(length_s: float, target_s: float | None) -> bool:
    return bool(target_s) and abs(length_s - target_s) <= max(COACH.over_s, COACH.over_share * target_s)


@dataclass(frozen=True)
class Day:
    day: date
    takes: int
    length_s: float | None  # the take nearest the target (no target: the day's last)
    key_points: float | None  # the day's best
    fillers_per_min: float | None  # the day's fewest
    memory: float | None  # the day's best
    answer_s: float | None  # the day's answers, median length


def days(all_points: list[Point], target_s: float | None = None) -> list[Day]:
    out = []
    for d in sorted({p.when.date() for p in all_points}):
        full = [p for p in all_points if p.when.date() == d and not p.answer]
        answers = sorted(p.length_s for p in all_points if p.when.date() == d and p.answer)
        best = lambda key, pick: pick((getattr(p, key) for p in full if getattr(p, key) is not None),  # noqa: E731
                                      default=None)
        length = None
        if full:
            length = min(full, key=lambda p: abs(p.length_s - target_s)).length_s if target_s else full[-1].length_s
        out.append(Day(d, len(full), length, best("key_points", max), best("fillers_per_min", min),
                       best("memory", max), answers[len(answers) // 2] if answers else None))
    return out


def days_table(all_points: list[Point], target_s: float | None = None, n: int = 5) -> tuple[str, ...]:
    """The last `n` days side by side, a line per measure, monospace like the take table."""
    rows_days = days(all_points, target_s)[-n:]
    if not rows_days:
        return ()
    show = lambda v, form: "-" if v is None else form(v)  # noqa: E731
    rows = [("", [d.day.strftime("%-d %b") for d in rows_days]),
            ("Takes", [str(d.takes) for d in rows_days]),
            ("Length" + (f" ({mmss(target_s)})" if target_s else ""), [show(d.length_s, mmss) for d in rows_days]),
            ("Key points", [show(d.key_points, lambda v: f"{100 * v:.0f}%") for d in rows_days]),
            ("Fillers a minute", [show(d.fillers_per_min, lambda v: f"{v:g}") for d in rows_days])]
    if any(d.memory is not None for d in rows_days):
        rows.append(("From memory", [show(d.memory, lambda v: f"{100 * v:.0f}%") for d in rows_days]))
    if any(d.answer_s is not None for d in rows_days):
        rows.append(("Answers", [show(d.answer_s, mmss) for d in rows_days]))
    name_w = max(len(r[0]) for r in rows)
    col_w = max(len(v) for _, vals in rows for v in vals)
    return tuple(f"{name:<{name_w}}" + "".join(f"  {v:>{col_w}}" for v in vals) for name, vals in rows)


def readiness(all_points: list[Point], target_s: float | None = None, points: bool = True) -> str:
    """Ready, or the first thing still missing (see the module doc)."""
    full = [p for p in all_points if not p.answer]
    k = COACH.ready_takes
    if len(full) < k:
        return f"Not ready yet: {k} good full takes in a row are the test ({len(full)} so far)"
    last = full[-k:]
    timed = sum(on_time(p.length_s, target_s) for p in last)
    if target_s and timed < k:
        return f"Not ready yet: on time in {timed} of the last {k} takes"
    points_ok = sum((p.key_points or 0) >= COACH.ready_points for p in last) if points else k
    if points_ok < k:  # (`points` False: a PDF speech, its notes are slide titles, not points to make)
        return f"Not ready yet: {COACH.ready_points:.0%} of the key points in {points_ok} of the last {k} takes"
    fillers = [p for p in last if (p.fillers_per_min or 0) > COACH.ready_fillers]
    if fillers:
        return f"Not ready yet: {fillers[-1].fillers_per_min:g} fillers a minute in a recent take"
    round_, current = [], []  # the latest Q&A round: the answers after a full take
    for p in all_points:
        if not p.answer:
            round_, current = (current or round_), []
        else:
            current.append(p)
    round_ = current or round_
    long = [p for p in round_ if p.length_s > QA.answer_max_s]
    if long:
        return f"Not ready yet: an answer ran {mmss(long[-1].length_s)}, over {mmss(QA.answer_max_s)}"
    parts = [f"the last {k} takes" + (" on time" if target_s else "")] + (["with the key points"] if points else [])
    if round_:
        parts.append(f"answers under {mmss(QA.answer_max_s)}")
    return "Ready: " + ", ".join(parts)
