"""One coaching point per take: what went well, one thing to work on, and the
next thing to practise (Review's coach card).

    advise(board, number) -> Advice | None

Built only from what was measured (palmcards.metrics, through the Board),
never from a guess, and never about an accent. One point, not a report: a
learner acts on one goal at a time (one thing done well and one to work on
per round, then another round).

Work on, the first that applies (COACH settings):
  0. over the target length (when there is one) by more than COACH.over_s or
     COACH.over_share: the section the most over its budget (budgets: the
     target shared by the sections' words),
  1. a key point left out: a sentence skipped before the last one said,
     then sentences not reached (the take stopped early),
  2. a long pause before a sentence, the longest: often a blank at a
     transition, where memory fails most,
  3. a sentence restarted, the one with the most restarts,
  4. fillers in one place, the sentence with the most,
  5. a pace outside COACH.pace_low .. pace_high,
  6. looking away from the screen more than COACH.away_share.
Went well: the largest gain over the take before it, else what is already
good in this one. Next: a drill of the sentence to work on, else a full run
again (after a drill: a full run, to put the part back in place).
"""

from __future__ import annotations

from dataclasses import dataclass

from palmcards.config import COACH, QA
from palmcards.metrics import value


@dataclass(frozen=True)
class Advice:
    well: str
    work: str
    next: str
    sentence: int | None = None  # the sentence to practise (Notes.sentences index)


def opening(text: str, words: int = COACH.quote_words) -> str:
    """A sentence's first few words, quoted: “We started this project…”."""
    parts = text.split()
    cut = " ".join(parts[:words]).rstrip(",.;:!?")
    return f"“{cut}…”" if len(parts) > words else f"“{text.rstrip('.')}”"


def mmss(seconds: float) -> str:
    return "{}:{:02d}".format(*divmod(round(abs(seconds)), 60))


def budgets(notes, target_s: float) -> list[float]:
    """Each section's share of the target length, by its words."""
    words = [sum(len(s.words) for s in sec.sentences) for sec in notes.sections]
    total = max(1, sum(words))
    return [target_s * w / total for w in words]


def section_times(board, n: int) -> dict[int, float]:
    """Seconds each section said in take n ran: from its first word said to the
    next section's first word said (the last: to its own last word)."""
    rows, notes = board.rows[n], board.notes
    spans: dict[int, list[float]] = {}
    for i, r in rows.items():
        if r.get("start") is not None:
            spans.setdefault(notes.sentences[i].section, []).extend((r["start"], r["end"]))
    starts = sorted((min(v), k) for k, v in spans.items())
    out = {}
    for (start, k), nxt in zip(starts, starts[1:] + [None]):
        out[k] = (nxt[0] if nxt else max(spans[k])) - start
    return out


def _section_name(board, k: int) -> str:
    sec = board.notes.sections[k]
    return f"“{sec.title}”" if sec.title else opening(sec.sentences[0].text)


def _over_time(board, n: int, target_s: float | None) -> tuple[str, int] | None:
    """(what to say, the over section) when the take ran over its target."""
    length = board.durations.get(n)
    if not target_s or length is None:
        return None
    over = length - target_s
    if over <= max(COACH.over_s, COACH.over_share * target_s):
        return None
    times, budget = section_times(board, n), budgets(board.notes, target_s)
    worst = max(times, key=lambda k: times[k] - budget[k], default=None)
    if worst is None or times[worst] <= budget[worst]:
        return f"Over time by {mmss(over)}: {mmss(length)} for {mmss(target_s)}", -1
    return (f"Over time by {mmss(over)}: {_section_name(board, worst)} ran {mmss(times[worst])} "
            f"for its {mmss(budget[worst])}"), worst


def advise(board, number: int, target_s: float | None = None) -> Advice | None:
    """The coaching for take `number` (None until it is analysed). `target_s`: the
    talk's target length, if one is set."""
    if number not in board.rows:
        return None
    if number in board.drills:
        return _drill(board, number)
    full = [t for t in board.takes if t not in board.drills and t < number]
    before = full[-1] if full else None
    well = _went_well(board, number, before, target_s)
    if (over := _over_time(board, number, target_s)) is not None:
        work, section = over
        if section < 0:
            return Advice(well, work, "Run it through again, a little tighter")
        first = board.notes.sections[section].sentences[0].index
        return Advice(well, work, "Tighten it in Prepare: select its paragraph, then bring two L-hands together",
                      first)
    work, sentence = _work_on(board, number)
    if sentence is not None:
        nxt = "Practise it: select it, then pinch and lift"
    elif work.startswith("You stopped"):
        nxt = "Run it through again, to the end"
    elif work == NOTHING:
        nxt = "Try it from memory: press W for fading cards" if board.memory.get(number) is None \
            else "Run it again with fading cards"
    else:
        nxt = "Run it through again"
    return Advice(well, work, nxt, sentence)


NOTHING = "Nothing stands out: keep the rhythm"


def _text(board, i: int) -> str:
    return opening(board.notes.sentences[i].text)


def _work_on(board, n: int) -> tuple[str, int | None]:
    rows = board.rows[n]
    said = sorted(i for i, r in rows.items() if r["status"] != "skipped")
    if not said:
        return "Nothing was heard in this take: check the microphone", None
    missed = [i for i, r in rows.items() if r["status"] == "skipped" and i < said[-1]]
    if missed:
        more = f" (and {len(missed) - 1} more)" if len(missed) > 1 else ""
        return f"You left out {_text(board, missed[0])}{more}", missed[0]
    if (left := len(board.notes.sentences) - 1 - said[-1]) > 0:
        return f"You stopped after {_text(board, said[-1])}: {left} sentence{'s' if left != 1 else ''} not reached", None
    # The longest silence between one sentence said and the next.
    timed = sorted((r["start"], i) for i, r in rows.items() if r.get("start") is not None)
    gaps = [(rows[b].get("pause_before_s") or 0.0, b, a) for (_, a), (_, b) in zip(timed, timed[1:])]
    if gaps and (gap := max(gaps))[0] >= COACH.pause_s:
        seconds, i, previous = gap
        section = board.notes.sentences[i].section != board.notes.sentences[previous].section
        where = " at the start of a section" if section else ""
        return f"A {seconds:.1f} s pause before {_text(board, i)}{where}", i
    restarts = board.restarts.get(n, {})
    if restarts and (worst := max(restarts, key=restarts.get)) is not None:
        times = "twice" if restarts[worst] == 2 else f"{restarts[worst]} times" if restarts[worst] > 2 else "once"
        return f"You restarted {_text(board, worst)} {times}", worst
    fillers = {i: len(r["fillers"]) for i, r in rows.items()}
    if fillers and (worst := max(fillers, key=fillers.get)) is not None and fillers[worst] >= COACH.fillers_in_one:
        return f"{fillers[worst]} fillers in {_text(board, worst)}", worst
    speech = (board.metrics.get(n) or {}).get("speech")
    pace = value(speech, "pace_wpm")
    if pace is not None and not COACH.pace_low <= pace <= COACH.pace_high:
        way = "fast" if pace > COACH.pace_high else "slow"
        return (f"Your pace was {pace:.0f} words a minute, a little {way}: aim for "
                f"{COACH.pace_low}-{COACH.pace_high}"), None
    away = _away(board, n)
    if away is not None and away > COACH.away_share:
        return f"You looked away from the screen {100 * away:.0f}% of the time", None
    return NOTHING, None


def _away(board, n: int) -> float | None:
    gaze = (board.metrics.get(n) or {}).get("gaze")
    return value(gaze, "away_share") if gaze else None


def _on_time(board, n: int, target_s: float | None) -> bool:
    length = board.durations.get(n)
    return bool(target_s) and length is not None and \
        abs(length - target_s) <= max(COACH.over_s, COACH.over_share * target_s)


def _went_well(board, n: int, before: int | None, target_s: float | None = None) -> str:
    now = _facts(board, n)
    if before is not None:
        then = _facts(board, before)
        gains = []  # (size of the gain, what to say)
        if _on_time(board, n, target_s) and not _on_time(board, before, target_s):
            gains.append((1.5, f"On time: {mmss(board.durations[n])} for {mmss(target_s)}"))
        if now["said"] > then["said"]:
            gains.append((now["said"] - then["said"], f"You said {now['said']} of {now['of']} key points, "
                                                      f"{now['said'] - then['said']} more than take {before}"))
        for key, name, unit in (("fillers", "fillers", " a minute"), ("pauses", "long pauses", ""),
                                ("restarts", "restarts", "")):
            a, b = then[key], now[key]
            if a is not None and b is not None and b < a:
                gains.append(((a - b) / max(a, 1.0), f"Fewer {name}: {_num(b)}{unit}, down from {_num(a)}"))
        if then["away"] is not None and now["away"] is not None and then["away"] - now["away"] >= 0.1:
            gains.append((2 * (then["away"] - now["away"]), f"More time on the screen: {100 * (1 - now['away']):.0f}%"))
        if gains:
            return max(gains)[1]
    # Nothing gained (or the first take): what is good in this one.
    if _on_time(board, n, target_s):
        return f"On time: {mmss(board.durations[n])} for {mmss(target_s)}"
    if now["of"] and now["said"] == now["of"]:
        return f"You said all {now['of']} key points"
    if now["restarts"] == 0:
        return "No restarts: you kept going"
    if now["fillers"] is not None and now["fillers"] < COACH.few_fillers:
        return f"Few fillers: {_num(now['fillers'])} a minute"
    if now["pace"] is not None and COACH.pace_low <= now["pace"] <= COACH.pace_high:
        return f"A steady pace: {now['pace']:.0f} words a minute"
    return f"You said {now['said']} of {now['of']} key points" if now["of"] else "The take is in"


def _num(x: float) -> str:
    return f"{x:g}" if x != int(x) else f"{int(x)}"


def _facts(board, n: int) -> dict:
    rows = board.rows[n]
    speech = (board.metrics.get(n) or {}).get("speech")
    return {"said": sum(r["status"] != "skipped" for r in rows.values()), "of": len(board.notes.sentences),
            "fillers": value(speech, "fillers_per_min"), "pauses": value(speech, "long_pauses"),
            "restarts": sum(board.restarts.get(n, {}).values()), "pace": value(speech, "pace_wpm"),
            "away": _away(board, n)}


def _drill(board, n: int) -> Advice:
    """A practice take of one sentence: against the takes before it that said it."""
    i = board.drills[n]
    row = board.rows[n].get(i)
    quote = _text(board, i)
    if row is None or row["status"] == "skipped":
        return Advice("The practice take is in", f"{quote} wasn't heard", "Practise it again: pinch and lift", i)
    earlier = [t for t in board.said_in(i) if t < n]
    fillers = len(row["fillers"])
    then = board.rows[earlier[-1]][i] if earlier else None
    restarts = board.restarts.get(n, {}).get(i, 0)
    if then is not None and len(then["fillers"]) > fillers:
        well = f"Fewer fillers in it: {fillers}, down from {len(then['fillers'])}"
    elif restarts == 0:
        well = "Said straight through"
    else:
        well = "The practice take is in"
    if restarts:
        work = f"You restarted it {'once' if restarts == 1 else f'{restarts} times'}"
    elif fillers:
        work = f"{fillers} filler{'s' if fillers != 1 else ''} in it"
    else:
        work = "It is ready: put it back in a full run"
    nxt = "Run it through again" if not restarts and not fillers else "Practise it once more, then a full run"
    return Advice(well, work, nxt, i)


def advise_answers(answers: list[dict]) -> Advice | None:
    """The coaching for a Q&A round: its answers, each {"question", "duration_s",
    "first_word_s" (silence before the first word, None if nothing was heard),
    "fillers"}. Short and direct is the aim: the answer first, then one reason."""
    if not answers:
        return None
    heard = [a for a in answers if a["first_word_s"] is not None]
    if not heard:
        return Advice("The answers are in", "No answer was heard: check the microphone", "Try the round again")
    q = lambda a: opening(a["question"])  # noqa: E731
    long = max(heard, key=lambda a: a["duration_s"])
    slow = max(heard, key=lambda a: a["first_word_s"])
    if long["duration_s"] > QA.answer_max_s:
        work = (f"Your answer to {q(long)} ran {mmss(long['duration_s'])}: aim for under "
                f"{mmss(QA.answer_max_s)}, the answer first, then one reason")
        nxt = "Say that answer in one sentence first, then add one reason"
    elif slow["first_word_s"] > QA.slow_start_s:
        work = f"You took {slow['first_word_s']:.1f} s to start on {q(slow)}: open with the answer itself"
        nxt = "Practise opening each answer with yes, no or a number"
    elif (most := max(heard, key=lambda a: a["fillers"]))["fillers"] >= COACH.fillers_in_one + 1:
        work = f"{most['fillers']} fillers in your answer to {q(most)}"
        nxt = "Pause instead of filling: a second of quiet reads as thought"
    else:
        work = "Short, direct answers: keep it that way"
        nxt = "Record another full take: new questions follow it"
    if all(a["duration_s"] <= QA.answer_max_s for a in heard):
        well = f"{'All ' if len(heard) > 1 else ''}{len(heard)} answer{'s' if len(heard) != 1 else ''} under " \
               f"{mmss(QA.answer_max_s)}"
    else:
        fast = min(heard, key=lambda a: a["first_word_s"])
        well = f"You started on {q(fast)} after {fast['first_word_s']:.1f} s"
    return Advice(well, work, nxt)
