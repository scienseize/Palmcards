"""Planned delivery marks vs measured delivery -> verdicts.

    verdicts(marks, alignment, words, prosody, baseline_wpm=None) -> dict

`marks` holds each note sentence's marks as [kind, word] pairs (`Mark.kind`,
`Mark.word`, in `Sentence.marks` order), `alignment` and `words` are a take's
M5 alignment and transcript words (app clock), `prosody` its pitch and
loudness (palmcards.prosody), or None if it couldn't be computed.

Every mark gets one verdict, with the measured value, the threshold and a
one-line reason for the Review screen:

  hit / missed   measured and judged
  unclear        not enough to judge (too few words, no voice, word not heard)
  skipped        the mark's sentence wasn't said; never "missed"

  / //           silence between the words around the mark (the word before
                 it: the last word said before the note word, so a restart
                 is measured on its final attempt). A filler in the gap is a
                 miss. Words Whisper was unsure of (noise) are ignored.
  [slow] [fast]  sentence words per minute against the take's average, over
                 the spoken sentences without a pace mark (a drill has no
                 others, so it is given a full take's average instead)
  *word*         the word's peak loudness and pitch against the other words'
                 peaks, windows padded a little. Pitch and loudness drift
                 down through a sentence, so with enough other words the
                 baseline is a line fitted through their peaks over time
                 (evaluated at the word), else their median
  [rise] [fall]  slope of a line (Theil-Sen, robust to pitch-tracking
                 octave jumps) through the pitch of the last ~0.5 s of
                 voiced sound in the sentence

Thresholds are CUES in palmcards/config.py; the result records the values
used, so verdicts saved earlier stay readable after tuning.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np

from palmcards.config import CUES
from palmcards.notes import MarkKind, normalize
from palmcards.prosody import Prosody

VERDICTS = ("hit", "missed", "unclear", "skipped")
PAUSES = (MarkKind.SHORT_PAUSE, MarkKind.LONG_PAUSE)
PACES = (MarkKind.SLOW, MarkKind.FAST)
ENDINGS = (MarkKind.RISE, MarkKind.FALL)


def _v(kind: str, word: int | None, verdict: str, reason: str, value=None, threshold=None) -> dict:
    return {"kind": kind, "word": word, "verdict": verdict, "value": value, "threshold": threshold,
            "reason": reason}


class _Take:
    """A take's alignment, transcript and prosody, indexed for the checks."""

    def __init__(self, alignment: dict, words: list[dict], prosody: Prosody | None):
        self.sentences = alignment["sentences"]
        self.words = words
        unsure = set(alignment.get("unsure", []))
        # Transcript words that take part: not punctuation, not noise.
        self.real = [i for i, w in enumerate(words) if normalize(w["text"]) and i not in unsure]
        self.fillers = {f["word"] for f in alignment["fillers"]}
        self.restarts = {i for r in alignment["restarts"] for i in r["words"]}
        self.extras = {i for e in alignment["extras"] for i in e["words"]}
        self.prosody = prosody if prosody is not None and len(prosody) else None
        self.st = self.prosody.st if self.prosody is not None else None
        self._joined = {s["sentence"]: dict(s.get("joined", [])) for s in self.sentences}

    def span(self, si: int, wi: int) -> tuple[int, int] | None:
        """First and last transcript word said for note word wi of sentence si."""
        if not 0 <= si < len(self.sentences):
            return None
        idx = self.sentences[si]["words"]
        if not 0 <= wi < len(idx) or idx[wi] is None:
            return None
        return idx[wi], self._joined[si].get(wi, idx[wi])

    def next_word_start(self, after: int) -> float | None:
        """Start of the first real transcript word after transcript word `after`."""
        return next((self.words[i]["start"] for i in self.real if i > after), None)


# --- pauses --------------------------------------------------------------------------

def _pause(tk: _Take, kind: str, si: int, wi: int) -> dict:
    s = tk.sentences[si]
    need = CUES.long_pause_s if kind == MarkKind.LONG_PAUSE else CUES.short_pause_s
    if s["status"] == "skipped":
        return _v(kind, wi, "skipped", "sentence not said")
    n = len(s["words"])
    if wi > 0:
        before = tk.span(si, wi - 1)
    else:
        before = tk.span(si - 1, len(tk.sentences[si - 1]["words"]) - 1) if si > 0 else None
    if wi < n:
        after = tk.span(si, wi)
    else:
        after = tk.span(si + 1, 0) if si + 1 < len(tk.sentences) else None
    if si == 0 and wi == 0:
        return _v(kind, wi, "unclear", "nothing is said before it", threshold=need)
    if wi == n and si + 1 == len(tk.sentences):
        return _v(kind, wi, "unclear", "nothing is said after it", threshold=need)
    if before is None:
        return _v(kind, wi, "unclear", "the word before it wasn't heard", threshold=need)
    if after is None:
        return _v(kind, wi, "unclear", "the word after it wasn't heard", threshold=need)
    tp, tn = before[1], after[0]
    if tn <= tp:
        return _v(kind, wi, "unclear", "the words around it were heard out of order", threshold=need)

    between = [i for i in tk.real if tp < i < tn]
    last = between[-1] if between else tp
    gap = round(max(0.0, tk.words[tn]["start"] - tk.words[last]["end"]), 3)
    if fill := [i for i in between if i in tk.fillers]:
        return _v(kind, wi, "missed", f'filled with "{tk.words[fill[0]]["text"].strip(",.")}"', gap, need)
    after_what = ""
    if last in tk.restarts:
        after_what = " after the restart"
    elif last in tk.extras:
        after_what = " after the ad-lib"
    if gap >= need - 1e-9:
        return _v(kind, wi, "hit", f"{gap:.2f} s pause{after_what}", gap, need)
    if kind == MarkKind.LONG_PAUSE and gap >= CUES.short_pause_s:
        return _v(kind, wi, "missed", f"only a short pause, {gap:.2f} s{after_what} (needs {need:.2f})", gap, need)
    return _v(kind, wi, "missed", f"no pause, {gap:.2f} s{after_what} (needs {need:.2f})", gap, need)


# --- pace ----------------------------------------------------------------------------

def sentence_rate(entry: dict) -> tuple[int, float] | None:
    """(aligned note words, seconds from first to last), if enough to judge pace."""
    said = sum(i is not None for i in entry["words"])
    if entry["start"] is None or said < CUES.pace_min_words:
        return None
    seconds = entry["end"] - entry["start"]
    return (said, seconds) if seconds > 0 else None


def _wpm(words: int, seconds: float) -> float:
    return round(60.0 * words / seconds, 1)


def _pace(tk: _Take, kind: str, si: int, base: float | None, base_note: str) -> dict:
    s = tk.sentences[si]
    need = CUES.slow_ratio if kind == MarkKind.SLOW else CUES.fast_ratio
    if s["status"] == "skipped":
        return _v(kind, None, "skipped", "sentence not said")
    rate = sentence_rate(s)
    if rate is None:
        said = sum(i is not None for i in s["words"])
        return _v(kind, None, "unclear", f"only {said} word{'s' if said != 1 else ''} heard", threshold=need)
    if base is None:
        why = "no earlier full take to compare with" if base_note == DRILL else "no unmarked sentence to compare with"
        return _v(kind, None, "unclear", why, threshold=need)
    wpm = _wpm(*rate)
    ratio = round(wpm / base, 3)
    where = f"{wpm:.0f} wpm, {ratio:.0%} of {base:.0f}{base_note}"
    if kind == MarkKind.SLOW:
        ok = ratio < need
        return _v(kind, None, "hit" if ok else "missed", where + ("" if ok else f" (needs under {need:.0%})"),
                  ratio, need)
    ok = ratio > need
    return _v(kind, None, "hit" if ok else "missed", where + ("" if ok else f" (needs over {need:.0%})"), ratio, need)


def take_wpm(tk: _Take, marks: list[list]) -> float | None:
    """Average pace over the spoken sentences without a pace mark."""
    total_w, total_s = 0, 0.0
    for si, s in enumerate(tk.sentences):
        if s["status"] == "skipped" or any(k in PACES for k, _ in marks[si]):
            continue
        if (rate := sentence_rate(s)) is not None:
            total_w, total_s = total_w + rate[0], total_s + rate[1]
    return _wpm(total_w, total_s) if total_w else None


# --- stress --------------------------------------------------------------------------

def _peaks(tk: _Take, si: int, wi: int) -> tuple[float, float, float, float] | None:
    """(peak loudness dB, peak pitch st or NaN, word length s, word middle t) for a note word."""
    span = tk.span(si, wi)
    if span is None:
        return None
    t0, t1 = tk.words[span[0]]["start"], tk.words[span[1]]["end"]
    sl = tk.prosody.window(t0 - CUES.stress_pad_s, t1 + CUES.stress_pad_s)
    loud = tk.prosody.rms_db[sl]
    if not len(loud):
        return None
    pitch = tk.st[sl]
    pitch = pitch[np.isfinite(pitch)]
    peak_pitch = float(np.percentile(pitch, CUES.peak_percentile)) if len(pitch) >= CUES.stress_min_voiced else np.nan
    return float(np.percentile(loud, CUES.peak_percentile)), peak_pitch, t1 - t0, (t0 + t1) / 2


def _baseline(points: list[tuple[float, float]], t: float) -> float:
    """What the other words predict at time t: their drift line, or their median."""
    if len(points) >= CUES.stress_trend_min:
        ts, vs = np.array(points).T
        if np.ptp(ts) > 0:
            slope, icept = np.polyfit(ts - ts.mean(), vs, 1)
            return float(icept + slope * (t - ts.mean()))
    return float(np.median([v for _, v in points]))


def _stress(tk: _Take, si: int, wi: int, stressed: set[int]) -> dict:
    kind = MarkKind.STRESS
    s = tk.sentences[si]
    need = {"loud_db": CUES.stress_loud_db, "pitch_st": CUES.stress_pitch_st}
    if s["status"] == "skipped":
        return _v(kind, wi, "skipped", "sentence not said")
    if tk.prosody is None:
        return _v(kind, wi, "unclear", "no pitch or loudness data for this take", threshold=need)
    mine = _peaks(tk, si, wi)
    if mine is None:
        return _v(kind, wi, "unclear", "the word wasn't heard", threshold=need)
    loud, pitch, length, mid = mine
    if length < CUES.stress_min_s:
        return _v(kind, wi, "unclear", f"too short to measure ({length:.2f} s)", threshold=need)
    others = [p for k in range(len(s["words"])) if k != wi and k not in stressed
              if (p := _peaks(tk, si, k)) is not None]
    if len(others) < CUES.stress_min_others:
        return _v(kind, wi, "unclear", "too few other words to compare with", threshold=need)
    dl = loud - _baseline([(p[3], p[0]) for p in others], mid)
    other_pitch = [(p[3], p[1]) for p in others if np.isfinite(p[1])]
    dp = pitch - _baseline(other_pitch, mid) if np.isfinite(pitch) and len(other_pitch) >= CUES.stress_min_others \
        else float("nan")
    value = {"loud_db": round(dl, 1), "pitch_st": round(dp, 1) if np.isfinite(dp) else None}
    heard = f"{dl:+.1f} dB" + (f", {dp:+.1f} st" if np.isfinite(dp) else "") + " against the other words"
    if dl >= CUES.stress_loud_db or (np.isfinite(dp) and dp >= CUES.stress_pitch_st):
        return _v(kind, wi, "hit", heard, value, need)
    if not np.isfinite(dp):
        return _v(kind, wi, "unclear", f"{dl:+.1f} dB, too little voice to judge the pitch", value, need)
    return _v(kind, wi, "missed", f"{heard} (needs +{CUES.stress_loud_db:g} dB or +{CUES.stress_pitch_st:g} st)",
              value, need)


# --- ending intonation ---------------------------------------------------------------

def _ending(tk: _Take, kind: str, si: int) -> dict:
    s = tk.sentences[si]
    need = CUES.ending_slope_st_s if kind == MarkKind.RISE else -CUES.ending_slope_st_s
    if s["status"] == "skipped":
        return _v(kind, None, "skipped", "sentence not said")
    if tk.prosody is None:
        return _v(kind, None, "unclear", "no pitch data for this take", threshold=need)
    last_word = max(i for i in s["words"] if i is not None)
    last_word = max([last_word] + [j for wi, j in s.get("joined", [])])
    t1 = s["end"] + CUES.ending_pad_s
    if (nxt := tk.next_word_start(last_word)) is not None:
        t1 = min(t1, max(nxt, s["end"]))
    sl = tk.prosody.window(s["start"], t1)
    t, st = tk.prosody.t[sl], tk.st[sl]
    voiced = np.isfinite(st)
    if not voiced.any():
        return _v(kind, None, "unclear", "no voiced sound at the end", threshold=need)
    t, st = t[voiced], st[voiced]
    keep = t >= t[-1] - CUES.ending_window_s
    t, st = t[keep], st[keep]
    if len(t) * tk.prosody.hop < CUES.ending_min_voiced_s:
        return _v(kind, None, "unclear", f"too little voiced sound at the end ({len(t) * tk.prosody.hop:.2f} s)",
                  threshold=need)
    from scipy.stats import theilslopes

    slope = float(theilslopes(st, t)[0])
    change = slope * (t[-1] - t[0])
    if abs(slope) < 1.0:
        heard = "pitch stayed level"
    else:
        heard = f"pitch {'rose' if slope > 0 else 'fell'} {abs(change):.1f} st at the end"
    ok = slope >= need if kind == MarkKind.RISE else slope <= need
    rate = f"{slope:+.1f} st/s" + ("" if ok else f", needs {need:+.1f}")
    return _v(kind, None, "hit" if ok else "missed", f"{heard} ({rate})", round(slope, 2), need)


# --- the take ------------------------------------------------------------------------

def _sentence_fillers(tk: _Take, alignment: dict) -> dict[int, list[str]]:
    """Fillers by sentence: the one they fall in, or the next one said after them."""
    spoken = sorted((s for s in tk.sentences if s["start"] is not None), key=lambda s: s["start"])
    out: dict[int, list[str]] = {}
    if not spoken:
        return out
    for f in alignment["fillers"]:
        s = next((s for s in spoken if s["end"] >= f["t"]), spoken[-1])
        out.setdefault(s["sentence"], []).append(f["text"])
    return out


DRILL = "drill"


def verdicts(marks: list[list], alignment: dict, words: list[dict], prosody: Prosody | None = None,
             baseline_wpm: float | None = None, drill: bool = False) -> dict:
    """Verdicts for every mark of every sentence. See the module doc. A
    drill's pace is judged only against `baseline_wpm` (the last full
    take's); without one it is unclear."""
    tk = _Take(alignment, words, prosody)
    base = take_wpm(tk, marks)
    base_note = " wpm, the take's average"
    if baseline_wpm is not None:
        base, base_note = baseline_wpm, " wpm in the last full take"
    elif drill:
        base, base_note = None, DRILL
    fillers = _sentence_fillers(tk, alignment)
    out_sentences = []
    counts = dict.fromkeys(VERDICTS, 0)
    for si, s in enumerate(tk.sentences):
        stressed = {w for k, w in marks[si] if k == MarkKind.STRESS}
        out = []
        for kind, wi in marks[si]:
            if kind in PAUSES:
                v = _pause(tk, kind, si, wi)
            elif kind in PACES:
                v = _pace(tk, kind, si, base, base_note)
            elif kind == MarkKind.STRESS:
                v = _stress(tk, si, wi, stressed)
            else:
                v = _ending(tk, kind, si)
            counts[v["verdict"]] += 1
            out.append(v)
        rate = sentence_rate(s)
        out_sentences.append({"sentence": si, "status": s["status"], "wpm": _wpm(*rate) if rate else None,
                              "fillers": fillers.get(si, []), "marks": out})
    return {"take_wpm": base, "baseline": "given" if baseline_wpm is not None else "none" if drill else "take",
            "counts": counts, "thresholds": asdict(CUES), "sentences": out_sentences}


def summary(v: dict) -> str:
    """One line: "5 hit, 2 missed, 1 unclear (8 marks)". Each count on its
    own: unclear means too little evidence, not a poor delivery, so it is
    never folded into a hit rate."""
    c = v["counts"]
    total = sum(c.values())
    if not total:
        return "no delivery marks"
    parts = [f"{c['hit']} hit"] + [f"{c[k]} {k}" for k in ("missed", "unclear", "skipped") if c[k]]
    return f"{', '.join(parts)} ({total} mark{'s' if total != 1 else ''})"


# --- labels --------------------------------------------------------------------------

def mark_label(kind: str, word: int | None, texts: list[str]) -> str:
    """How a mark reads in a list: '/ before "Thank"', '*being*', '[slow]'."""
    if kind in PAUSES:
        slash = "//" if kind == MarkKind.LONG_PAUSE else "/"
        if word is not None and word < len(texts):
            return f'{slash} before "{texts[word].strip(",.;:!?")}"'
        return f"{slash} at the end"
    if kind == MarkKind.STRESS:
        return f"*{texts[word].strip(',.;:!?')}*" if word is not None and word < len(texts) else "*stress*"
    return f"[{kind}]"


def report(v: dict, sentence_words: list[list[str]], texts: list[str]) -> str:
    """Readable verdicts for the terminal."""
    lines = []
    for s in v["sentences"]:
        if not s["marks"]:
            continue
        lines.append(f"  [{s['sentence']:2d}] {texts[s['sentence']]}")
        for m in s["marks"]:
            label = mark_label(m["kind"], m["word"], sentence_words[s["sentence"]])
            lines.append(f"         {m['verdict']:<8}{label:<22} {m['reason']}")
    if v["take_wpm"] is not None:
        lines.append(f"  pace {v['take_wpm']:.0f} wpm ({'last full take' if v['baseline'] == 'given' else 'take average'})")
    lines.append("  " + summary(v))
    return "\n".join(lines)
