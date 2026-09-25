"""The compiled alignment fill gives exactly the reference fill's result."""

import random
import sys
from pathlib import Path

import pytest

from palmcards import align as A
from palmcards.config import ALIGN

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from profile_align import case  # noqa: E402


def compare(notes, trans):
    filler = [t in set(ALIGN.fillers) for t in trans]
    assert A._dp(notes, trans, filler) == A._dp_python(notes, trans, filler)


def test_the_fill_is_compiled():
    assert type(A._fill_fast).__name__ == "CPUDispatcher"


@pytest.mark.parametrize("seed", range(40))
def test_random_scripts_align_the_same(seed):
    rng = random.Random(seed)
    vocab = ["we", "know", "where", "every", "one", "everyone", "um", "uh", "so", "being", "here", "tonight",
             "thank", "you", "for", "the", "plan", "place", "wh", "come"]
    notes = [rng.choice(vocab) for _ in range(rng.randint(1, 40))]
    trans = [rng.choice(vocab) for _ in range(rng.randint(1, 40))]
    compare(notes, trans)


@pytest.mark.parametrize("n, repeat", [(60, False), (60, True), (150, False), (150, True)])
def test_realistic_takes_align_the_same(n, repeat):
    sentences, words = case(n, repeat, seed=n)
    notes = [w for s in sentences for w in s]
    trans = [A.normalize(w["text"]) for w in words]
    compare(notes, trans)


def test_joins_splits_restarts_and_skips_align_the_same():
    notes = "good evening every one thank you for being here tonight we know where we are going".split()
    trans = "um good evening everyone thank you for be ing here we know wh we know where are going so".split()
    compare(notes, trans)
    compare(notes, trans[5:])  # starts mid-notes
    compare(notes[4:], trans)  # chatter first
