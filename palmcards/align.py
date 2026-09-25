"""Transcript <-> notes alignment.

    align(sentences, words) -> dict

`sentences` holds each note sentence's normalised words (`Word.norm`, cue
markup already stripped by notes.py); `words` is the transcript, each
{"text", "start", "end", ...} on the app clock. Transcript words are
normalised the same way (`notes.normalize`), so case and punctuation never
matter.

The core is a global alignment (Gotoh, affine gaps) scored with rapidfuzz
similarity, so it allows:
  - skipped note words and whole skipped sentences (one gap each),
  - extra transcript words (ad-libs), with near-free fillers,
  - misheard words (fuzzy pairs, never below ALIGN.min_sim),
  - Whisper splitting or joining a word ("every one" <-> "everyone"),
  - starting mid-notes, stopping early, chatter before or after (free end gaps).

Restarts ("we know where... we know where and we know when") are bound to
the last attempt: on ties the traceback prefers the later word, and a pass
afterwards marks runs of unmatched words that repeat the notes next to them
as restarts, moving the notes onto the later attempt where needed.

Result (word refs index `words`; times are the words' own clock):

    {"sentences": [{"sentence": 0, "status": "spoken", "coverage": 1.0,
                    "start": 49.51, "end": 51.02, "words": [0, 1, 2, None],
                    "misheard": [3], "joined": [[2, 3]]}],
     "fillers":  [{"word": 7, "text": "um", "t": 52.1}],
     "restarts": [{"sentence": 1, "words": [9, 10, 11]}],
     "extras":   [{"sentence": 2, "words": [30, 31]}],
     "unsure":   [40]}

`words[k]` is the transcript word for note word k (the first one if Whisper
split it; `joined` lists [note word, last transcript word] for those).
Status is spoken / partial / skipped by coverage (note words matched).
`unsure` lists words left out because Whisper gave them a very low
probability (usually hallucinations in noise).
"""

from __future__ import annotations

from rapidfuzz import fuzz
from rapidfuzz.process import cdist

from palmcards.config import ALIGN
from palmcards.notes import normalize

VERSION = 1  # bump when align() changes what it produces
NEG = float("-inf")
EPS = 1e-9  # scores this close count as a tie
M, X, Y = 0, 1, 2  # DP states: pair, note word skipped, transcript word extra
START = 255


def _sims(a: list[str], b: list[str]):
    """Similarity matrix, 0..1."""
    if not a or not b:
        return [[0.0] * len(b) for _ in a]
    return (cdist(a, b, scorer=fuzz.ratio) / 100.0).tolist()


def _pick(cands: list[tuple[float, int]]) -> tuple[float, int]:
    """Best (score, code); on a tie the earlier candidate wins."""
    best, code = NEG, -1
    for score, c in cands:
        if score > best + EPS:
            best, code = score, c
    return best, code


def _dp(notes: list[str], trans: list[str], filler: list[bool]):
    """Alignment of note words to transcript words.

    Returns [(note indices, transcript indices, similarity)] in order.
    """
    n, m = len(notes), len(trans)
    if n == 0 or m == 0:
        return []
    cfg = ALIGN
    s1 = _sims(notes, trans)
    # note word i vs transcript words j-1 + j (Whisper split it)
    sjt = _sims(notes, [trans[j - 1] + trans[j] for j in range(1, m)])
    # note words i-1 + i vs transcript word j (Whisper joined them)
    sjn = _sims([notes[i - 1] + notes[i] for i in range(1, n)], trans)

    def pair_score(sim: float, floor: float) -> float:
        return cfg.match_score - cfg.fuzzy_slope * (1.0 - sim) if sim >= floor else NEG

    op, ext, fc = cfg.gap_open, cfg.gap_extend, cfg.filler_cost
    # Score rows (lists of [M, X, Y]) and backpointer codes per cell.
    rows: list[list[list[float]]] = []
    last_col: list[list[float]] = []  # each row's cell at j = m, for the free trailing gaps
    back: list[list[tuple[int, int, int]]] = []
    for i in range(n + 1):
        row = [[NEG, NEG, NEG] for _ in range(m + 1)]
        brow = [(START, START, START)] * (m + 1)
        for j in range(m + 1):
            if i == 0 and j == 0:
                row[0][M] = 0.0
                continue
            if i == 0:
                row[j][Y] = 0.0  # chatter before the notes: free
                continue
            if j == 0:
                row[0][X] = 0.0  # starting mid-notes: free
                continue
            tf = filler[j - 1]

            # M: note word(s) paired with transcript word(s), or a substitution.
            # Code: move * 4 + predecessor state.
            cands = []
            prev = rows[i - 1][j - 1]
            if (p := pair_score(s1[i - 1][j - 1], cfg.min_sim)) > NEG:
                st, v = _best_state(prev)
                cands.append((v + p, 1 * 4 + st))
            # A join only counts if it beats each piece on its own
            # ("everyone we" is not "everyone").
            if j >= 2 and sjt[i - 1][j - 2] > max(s1[i - 1][j - 2], s1[i - 1][j - 1]) + EPS \
                    and (p := pair_score(sjt[i - 1][j - 2], cfg.join_min_sim)) > NEG:
                st, v = _best_state(rows[i - 1][j - 2])
                cands.append((v + p, 2 * 4 + st))
            if i >= 2 and sjn[i - 2][j - 1] > max(s1[i - 2][j - 1], s1[i - 1][j - 1]) + EPS \
                    and (p := pair_score(sjn[i - 2][j - 1], cfg.join_min_sim)) > NEG:
                st, v = _best_state(rows[i - 2][j - 1])
                cands.append((v + p, 3 * 4 + st))
            if tf:
                cands.append((row[j - 1][M] - fc, 4 * 4 + M))
            # Substitution: a word said instead of the note word, nothing
            # alike. Neither counts as matched; cheaper than two gaps.
            st, v = _best_state(prev)
            cands.append((v - cfg.substitute_cost, 5 * 4 + st))
            vm, cm = _pick(cands)

            # X: note word i-1 skipped.
            up = rows[i - 1][j]
            xc = [(up[M] - op, 1 * 4 + M), (up[Y] - op, 1 * 4 + Y), (up[X] - ext, 1 * 4 + X)]
            if tf:
                xc.append((row[j - 1][X] - fc, 2 * 4 + X))
            vx, cx = _pick(xc)

            # Y: transcript word j-1 extra.
            left = row[j - 1]
            vy, cy = _pick([(left[M] - op, M), (left[X] - op, X), (left[Y] - (fc if tf else ext), Y)])

            row[j] = [vm, vx, vy]
            brow[j] = (cm, cx, cy)
        rows.append(row)
        last_col.append(row[m])
        back.append(brow)
        if i >= 2:  # only rows i-1 and i-2 are read again
            rows[i - 2] = None

    # Free trailing gaps: end anywhere on the last row or column. Prefer
    # ending late (the full notes, the whole transcript) on ties.
    ends = [((n, j), rows[n][j]) for j in range(m, -1, -1)] + \
        [((i, m), last_col[i]) for i in range(n - 1, -1, -1)]
    best, end, end_state = NEG, None, M
    for cell_ij, cell in ends:
        st, v = _best_state(cell)
        if v > best + EPS:
            best, end, end_state = v, cell_ij, st
    return _traceback(back, end, end_state, s1, sjt, sjn)


def _best_state(cell: list[float]) -> tuple[int, float]:
    """State with the highest score; ties prefer M, then X, then Y."""
    st, v = M, cell[M]
    for s in (X, Y):
        if cell[s] > v + EPS:
            st, v = s, cell[s]
    return st, v


def _traceback(back, end, state, s1, sjt, sjn):
    pairs = []
    i, j = end
    while (i, j) != (0, 0):
        code = back[i][j][state]
        if code == START:
            break
        if state == M:
            move, pred = divmod(code, 4)
            if move == 1:
                pairs.append(((i - 1,), (j - 1,), s1[i - 1][j - 1]))
                i, j = i - 1, j - 1
            elif move == 2:
                pairs.append(((i - 1,), (j - 2, j - 1), sjt[i - 1][j - 2]))
                i, j = i - 1, j - 2
            elif move == 3:
                pairs.append(((i - 2, i - 1), (j - 1,), sjn[i - 2][j - 1]))
                i, j = i - 2, j - 1
            elif move == 4:  # filler after a pair
                j -= 1
            else:  # substitution: no pair
                i, j = i - 1, j - 1
            state = pred
        elif state == X:
            move, pred = divmod(code, 4)
            if move == 1:
                i -= 1
            else:
                j -= 1
            state = pred
        else:
            j -= 1
            state = code
    pairs.reverse()
    return pairs


def _close_enough(sims: list[float]) -> bool:
    """An attempt's full words resemble the notes well enough to be a restart
    (a lone near-miss like "right" for "tonight" is not)."""
    return not sims or sum(sims) / len(sims) >= ALIGN.restart_mean


class _Alignment:
    """Mutable note <-> transcript mapping, for the restart pass."""

    def __init__(self, notes: list[str], trans: list[str], filler: list[bool], pairs):
        self.notes, self.trans, self.filler = notes, trans, filler
        self.note_map: list[list[int] | None] = [None] * len(notes)
        self.note_sim = [0.0] * len(notes)
        self.t_note: list[int | None] = [None] * len(trans)
        self.restart: dict[int, int] = {}  # transcript position -> note index it restarted at
        for ns, ts, sim in pairs:
            for k in ns:
                self.note_map[k] = list(ts)
                self.note_sim[k] = sim
            for t in ts:
                self.t_note[t] = ns[0]

    def sim(self, t: int, k: int) -> float:
        return fuzz.ratio(self.trans[t], self.notes[k]) / 100.0

    def same(self, t: int, k: int) -> bool:
        return 0 <= k < len(self.notes) and self.sim(t, k) >= ALIGN.restart_sim

    def cut_off(self, t: int, k: int) -> bool:
        """A word broken off partway: "wh" for "where"."""
        w = self.trans[t]
        return (0 <= k < len(self.notes) and len(w) >= ALIGN.restart_prefix_min
                and len(w) < len(self.notes[k]) and self.notes[k].startswith(w))

    def runs(self) -> list[list[int]]:
        """Maximal runs of unmatched transcript positions."""
        out, cur = [], []
        for t, k in enumerate(self.t_note):
            if k is None:
                cur.append(t)
            elif cur:
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        return out

    def neighbours(self, run: list[int]) -> tuple[int | None, int | None]:
        """Note index matched just before the run, and just after it."""
        before = next((self.t_note[t] for t in range(run[0] - 1, -1, -1) if self.t_note[t] is not None), None)
        after = next((self.t_note[t] for t in range(run[-1] + 1, len(self.t_note)) if self.t_note[t] is not None), None)
        if before is not None:  # last note word of that pair (joins map two)
            while before + 1 < len(self.notes) and self.note_map[before + 1] is not None \
                    and self.note_map[before + 1] == self.note_map[before]:
                before += 1
        return before, after

    def mark_restarts_before(self, content: list[int], a: int) -> bool:
        """Attempts in `content` that start the notes at `a`, which the words
        after the run then say in full. Returns True if any were marked."""
        changed = False
        q = 0
        while q < len(content):
            if not (self.same(content[q], a) or self.cut_off(content[q], a)):
                q += 1
                continue
            k, sims = 0, []
            while q + k < len(content):
                t = content[q + k]
                if self.same(t, a + k):
                    sims.append(self.sim(t, a + k))
                    k += 1
                    continue
                if self.cut_off(t, a + k):
                    k += 1
                break
            if not _close_enough(sims):
                q += 1
                continue
            for t in content[q : q + k]:
                if t not in self.restart:
                    self.restart[t] = a
                    changed = True
            q += max(k, 1)
        return changed

    def move_onto_later(self, content: list[int], b: int) -> bool:
        """An attempt in `content` that repeats note words c..b, which the words
        before the run already said: move those notes onto the later attempt."""
        for q in range(len(content)):
            for e in range(len(content) - 1, q - 1, -1):
                length = e - q + 1
                c = b - length + 1
                if c < 0:
                    continue
                if not all(self.same(content[q + k], c + k) for k in range(length)):
                    continue
                if not _close_enough([self.sim(content[q + k], c + k) for k in range(length)]):
                    continue
                old = [self.note_map[k] for k in range(c, b + 1)]
                if any(o is None for o in old):
                    continue
                for k, o in zip(range(c, b + 1), old):
                    for t in o:
                        self.t_note[t] = None
                    t = content[q + k - c]
                    self.note_map[k] = [t]
                    self.note_sim[k] = self.sim(t, k)
                    self.t_note[t] = k
                return True
        return False

    def find_restarts(self) -> None:
        for _ in range(len(self.trans) + 1):
            changed = False
            for run in self.runs():
                content = [t for t in run if not self.filler[t] and t not in self.restart]
                if not content:
                    continue
                before, after = self.neighbours(run)
                if after is not None and self.mark_restarts_before(content, after):
                    changed = True
                    break
                if before is not None and self.move_onto_later(content, before):
                    changed = True
                    break
            if not changed:
                return


def align(sentences: list[list[str]], words: list[dict], fillers: tuple[str, ...] | None = None) -> dict:
    """Align note sentences (normalised words) to transcript words. See module doc.
    `fillers`: the filler words of the take's language (default ALIGN.fillers, English)."""
    cfg = ALIGN
    fillers = set(cfg.fillers if fillers is None else fillers)
    flat = [(si, wi, w) for si, sent in enumerate(sentences) for wi, w in enumerate(sent)]
    notes = [w for _, _, w in flat]
    # Transcript words that normalise to nothing ("-", "...") take no part,
    # nor do words Whisper was very unsure of: in silence and noise it
    # hallucinates ("Thank you.") at probabilities like 0.003.
    pos = [i for i, w in enumerate(words) if normalize(w["text"])]
    unsure = [i for i in pos if words[i].get("probability", 1.0) < cfg.min_probability]
    pos = [i for i in pos if words[i].get("probability", 1.0) >= cfg.min_probability]
    trans = [normalize(words[i]["text"]) for i in pos]
    filler = [t in fillers for t in trans]

    al = _Alignment(notes, trans, filler, _dp(notes, trans, filler))
    al.find_restarts()

    first_note = {}
    for k, (si, _, _) in enumerate(flat):
        first_note.setdefault(si, k)

    out_sentences = []
    for si, sent in enumerate(sentences):
        base = first_note.get(si, 0)
        idx, misheard, joined, times = [], [], [], []
        for wi in range(len(sent)):
            k = base + wi
            ts = al.note_map[k]
            if ts is None:
                idx.append(None)
                continue
            idx.append(pos[ts[0]])
            if len(ts) > 1:
                joined.append([wi, pos[ts[-1]]])
            if al.note_sim[k] < cfg.exact_sim:
                misheard.append(wi)
            times += [words[pos[t]]["start"] for t in ts] + [words[pos[t]]["end"] for t in ts]
        said = sum(i is not None for i in idx)
        coverage = round(said / len(sent), 3) if sent else 0.0
        if said == 0 or coverage < cfg.skipped_max:
            status = "skipped"
        elif coverage >= cfg.spoken_min:
            status = "spoken"
        else:
            status = "partial"
        entry = {
            "sentence": si,
            "status": status,
            "coverage": coverage,
            "start": min(times) if times and status != "skipped" else None,
            "end": max(times) if times and status != "skipped" else None,
            "words": idx,
            "misheard": misheard,
        }
        if joined:
            entry["joined"] = joined
        out_sentences.append(entry)

    def sentence_near(t: int) -> int | None:
        """Sentence of the matched word before transcript position t, else after."""
        for r in list(range(t - 1, -1, -1)) + list(range(t + 1, len(trans))):
            if al.t_note[r] is not None:
                return flat[al.t_note[r]][0]
        return None

    fillers_out, restarts, extras = [], [], []
    group: list[int] = []
    group_kind = None

    def flush() -> None:
        nonlocal group, group_kind
        if group:
            if group_kind == "restart":
                restarts.append({"sentence": flat[al.restart[group[0]]][0], "words": [pos[t] for t in group]})
            else:
                extras.append({"sentence": sentence_near(group[0]), "words": [pos[t] for t in group]})
        group, group_kind = [], None

    for t in range(len(trans)):
        if al.t_note[t] is not None:
            flush()
            continue
        if t in al.restart:
            kind = "restart"
        elif filler[t]:
            w = words[pos[t]]
            fillers_out.append({"word": pos[t], "text": trans[t], "t": w["start"]})
            continue  # a filler inside a restart or ad-lib doesn't split it
        else:
            kind = "extra"
        if kind != group_kind:
            flush()
        group_kind = kind
        group.append(t)
    flush()

    return {"sentences": out_sentences, "fillers": fillers_out, "restarts": restarts, "extras": extras,
            "unsure": unsure}


def counts(alignment: dict) -> dict[str, int]:
    """Sentence statuses and event totals, for summaries."""
    out = {"spoken": 0, "partial": 0, "skipped": 0}
    for s in alignment["sentences"]:
        out[s["status"]] += 1
    out.update(fillers=len(alignment["fillers"]), restarts=len(alignment["restarts"]),
               extras=len(alignment["extras"]))
    return out


def summary(alignment: dict) -> str:
    """One line: "6/7 sentences spoken, 1 partial, 3 fillers"."""
    c = counts(alignment)
    total = len(alignment["sentences"])
    parts = [f"{c['spoken']}/{total} sentences spoken"]
    for key, label in (("partial", "partial"), ("skipped", "skipped"), ("fillers", "filler"),
                       ("restarts", "restart"), ("extras", "ad-lib")):
        if c[key]:
            plural = "" if key in ("partial", "skipped") or c[key] == 1 else "s"
            parts.append(f"{c[key]} {label}{plural}")
    return ", ".join(parts)
