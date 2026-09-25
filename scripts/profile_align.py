"""How long the transcript <-> notes alignment takes, and how much memory,
for scripts of growing length and for a script that repeats itself.

  python scripts/profile_align.py [WORDS ...]

Each case: notes of N words in sentences of 10, a transcript that says them
with 5% of the words misheard, 3% fillers and one restart per 100 words,
aligned once. Prints seconds and peak Python memory (tracemalloc).
"""

import random
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from palmcards.align import align  # noqa: E402

VOCAB = ("we started this project with one question what if practice felt like a conversation most of us "
         "rehearse in our heads and are very forgiving so built mirror that listens it shows your notes beside "
         "face you mark how each line should sound say out loud tells landed no scores accent coaching just "
         "own plan checked against voice").split()


def case(n_words: int, repeat: bool = False, seed: int = 0):
    rng = random.Random(seed)
    if repeat:  # one 50-word passage said over and over (a refrain, a chorus)
        passage = [rng.choice(VOCAB) for _ in range(50)]
        flat = (passage * (n_words // 50 + 1))[:n_words]
    else:
        flat = [rng.choice(VOCAB) for _ in range(n_words)]
    sentences = [flat[i:i + 10] for i in range(0, len(flat), 10)]
    words, t = [], 0.0
    for i, w in enumerate(flat):
        if i % 100 == 50:  # a restart: the previous three words again
            for r in flat[max(0, i - 3):i]:
                words.append({"text": r, "start": t, "end": t + 0.3, "probability": 0.9})
                t += 0.35
        if rng.random() < 0.03:
            words.append({"text": "um", "start": t, "end": t + 0.2, "probability": 0.9})
            t += 0.25
        text = w if rng.random() > 0.05 else w[:-1] + "x"
        words.append({"text": text, "start": t, "end": t + 0.3, "probability": 0.9})
        t += 0.35
    return sentences, words


def run(n: int, repeat: bool = False) -> tuple[float, float, int]:
    sentences, words = case(n, repeat)
    tracemalloc.start()
    t = time.perf_counter()
    result = align(sentences, words)
    seconds = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()
    spoken = sum(s["status"] == "spoken" for s in result["sentences"])
    return seconds, peak, round(100 * spoken / len(result["sentences"]))


if __name__ == "__main__":
    sizes = [int(a) for a in sys.argv[1:]] or [100, 300, 600, 1200]
    print(f"{'words':>6} {'script':>8} {'seconds':>8} {'peak MB':>8} {'spoken %':>8}")
    for n in sizes:
        for repeat in (False, True):
            s, mb, ok = run(n, repeat)
            print(f"{n:6d} {'repeats' if repeat else 'varied':>8} {s:8.2f} {mb:8.1f} {ok:8d}")
