"""Fading cards: the notes thin out take by take.

A note word said in the newest takes that said its sentence, that many in a
row, fades: dimmed after FADE.dim_after takes, hidden (a blank) after
FADE.hide_after. A take that didn't say the sentence at all (it stopped
early, or drilled another sentence) neither counts nor breaks the run. An
edited sentence is new wording: it has no takes, so it shows in full.

In a take, when the voice stalls on hidden words, the next few come back
(prompt). Afterwards Review says how many hidden words were said without
being prompted (from_memory): an observation, like the other measures.

Words are (sentence, word) pairs in the current notes' indices.
"""

from __future__ import annotations

from palmcards.config import FADE

Word = tuple[int, int]


def levels(history: list[dict[int, tuple[bool, ...]]]) -> dict[Word, int]:
    """`history`: takes oldest first, each {sentence: which of its words were said},
    with only the sentences the take said. Returns each word's run of takes, newest first."""
    out: dict[Word, int] = {}
    done: set[Word] = set()
    for take in reversed(history):
        for s, said in take.items():
            for w, yes in enumerate(said):
                if (s, w) in done:
                    continue
                if yes:
                    out[(s, w)] = out.get((s, w), 0) + 1
                else:
                    done.add((s, w))
    return out


def faded(lv: dict[Word, int]) -> tuple[frozenset[Word], frozenset[Word]]:
    """(dimmed, hidden) words."""
    hidden = frozenset(k for k, n in lv.items() if n >= FADE.hide_after)
    dimmed = frozenset(k for k, n in lv.items() if FADE.dim_after <= n < FADE.hide_after)
    return dimmed, hidden


def prompt(hidden: frozenset[Word] | set[Word], order: list[Word], after: Word, n: int = FADE.prompt_words) \
        -> list[Word]:
    """The next `n` hidden words after `after` (word -1: the sentence's start), in
    reading order `order` (every note word)."""
    nxt = [k for k in order if k > after and k in hidden]
    return nxt[:n]


def from_memory(alignment: dict, hidden: list[Word], prompted: list[Word]) -> float | None:
    """Share of the take's hidden words it said without a prompt; None with none hidden.
    `alignment` is the take's (palmcards.align), in the indices the words were recorded in."""
    if not hidden:
        return None
    words = [e.get("words", []) for e in alignment["sentences"]]
    shown = {tuple(p) for p in prompted}

    def said(s: int, w: int) -> bool:
        return s < len(words) and w < len(words[s]) and words[s][w] is not None

    return sum(said(s, w) for s, w in map(tuple, hidden) if (s, w) not in shown) / len(hidden)


def _demo() -> None:
    hist = [{0: (True, True, False)}, {0: (True, False, True), 1: (True,)}, {0: (True, True, True)}]
    lv = levels(hist)
    assert lv == {(0, 0): 3, (0, 1): 1, (0, 2): 2, (1, 0): 1}, lv
    dimmed, hidden = faded(lv)
    assert hidden == {(0, 0), (0, 2)} and dimmed == {(0, 1), (1, 0)}
    order = [(0, 0), (0, 1), (0, 2), (1, 0)]
    assert prompt(hidden, order, (0, -1), 3) == [(0, 0), (0, 2)]
    al = {"sentences": [{"words": [0, None, 2]}, {"words": [3]}]}
    assert from_memory(al, [[0, 0], [0, 2]], [[0, 2]]) == 0.5
    assert from_memory(al, [], []) is None


if __name__ == "__main__":
    _demo()
    print("ok")
