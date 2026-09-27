"""Cut gesture samples for the replay regression tests out of `--trace` recordings.

    python scripts/cut_gesture_samples.py

Reads sessions/gesture-logs/<stem>.trace.jsonl (MediaPipe's hand landmarks,
recorded live with `main.py --trace`) and the gesture log next to it, and
writes one samples/gestures/<name>.json per entry in SEGMENTS: the frames in
the window, rebased to t = 0, points rounded to whole pixels, as
`expected` what the app recognised live in that window, and as `inputs` what
the app told the grammar (the options ring's nodes, from the log's
"ring_nodes" lines: the last one before the window, at t = 0, and those in it).

The traces themselves stay in sessions/ (gitignored); only the samples are
committed. They hold hand landmarks only: no image, no face, no voice.

Recordings from before the options ring was a knob (no "ring_nodes" lines in
the log) have no ring steps in their log; their `expected` takes the steps
from the replay instead, so the sample still pins down how the knob reads
those turns.

A recording made before a behaviour existed shows the old result in its log
(one finger in Review browsed words until 2026-09-27). Such a segment is
named in FROM_REPLAY with the reason: its grammar entries (focus, back,
commit, op, palm_hold) are taken from the replay and printed, to be checked
by eye against the log; its mode and zone entries stay the live ones.

Before writing, each sample is replayed through the current gesture code.
It must reproduce the live result, or, for a known issue, must still show
the issue (so a stale note can't hide a fix).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from palmcards.replay import KINDS, SAMPLES_DIR, differences, entry, replay  # noqa: E402

LOGS = ROOT / "sessions" / "gesture-logs"
FRAME_SIZE = (1280, 720)  # capture.Camera's size when these were recorded

# (name, trace stem, window in app seconds, mode it starts in, description, known issue or None)
SEGMENTS = [
    ("word-ring-commit", "20260925-024808", (116.0, 130.1), "prepare",
     "One finger browses words, pinch focuses, open palm spreads the options ring, an L turns it "
     "(steps from the replay: recorded before the knob), pinch + lift commits.", None),
    ("sentence-fold-back", "20260925-024808", (137.8, 143.7), "prepare",
     "Two fingers browse sentences, folding them onto the thumb focuses, dropping the hand backs out.", None),
    ("sentence-tone-back", "20260925-024808", (171.4, 183.9), "prepare",
     "Sentence focus, then an L-hand tilt turns the tone dial; the hand drops to back out.", None),
    ("paragraph-stretch-back", "20260925-101345", (5.2, 14.1), "prepare",
     "Flat hand browses paragraphs, fold focuses, two L-hands stretch the length, drop backs out.", None),
    ("fist-starts-take", "20260925-101345", (44.6, 49.3), "prepare",
     "A fist held 1 s starts the 3-2-1 count-in, then Rehearse.", None),
    ("rehearse-one-flick", "20260925-101345", (65.0, 83.3), "rehearse",
     "18 s of rehearsing with hands moving; exactly one flick in the command zone.", None),
    ("rehearse-stop-then-review", "20260925-101345", (98.1, 104.1), "rehearse",
     "Open palm held in the zone stops the take into Review; a flick right after is only logged.", None),
    ("review-back-to-prepare", "20260925-101345", (106.8, 120.1), "review",
     "Open palm held in the zone goes from Review back to Prepare.", None),
    ("rehearse-three-flicks", "20260925-101345", (129.0, 142.1), "rehearse",
     "Three flicks in a row, each one next section.", None),
    # Recorded before a held fist started takes (milestone 4). Fists formed
    # mid-gesture here used to start takes by mistake; only a fist raised
    # into view as a fist counts now.
    ("slow-pinch-is-not-a-take", "20260925-024808", (50.6, 58.3), "prepare",
     "Closing into a pinch: thumb on the index tip but the index barely reaching (1.03 palms), "
     "so it reads as FIST for 1.8 s before PINCH. Word focus, then back; no take.", None),
    ("resting-fist-is-not-a-take", "20260925-024808", (184.3, 193.9), "prepare",
     "After a pinch the hand turns over (tilt ~145 deg) and rests closed for about a second. Nothing happens.", None),
    ("curling-flat-hand-is-not-a-take", "20260925-024808", (213.7, 229.7), "prepare",
     "Flat hand browsing paragraphs curls into a fist for 2.5 s, then folds to focus and stretches; no take.", None),
    ("review-one-finger-sentence", "20260926-193557", (293.2, 298.6), "review",
     "In Review one finger browses sentences: pinching it focuses the sentence (not a word), dropping the hand "
     "backs out.", None),
    ("review-paragraph-play", "20260927-162724", (72.5, 86.5), "review",
     "In Review a flat hand folds to focus a paragraph; an open palm held on it plays the paragraph "
     "(palm_hold), once; dropping the hand backs out.", None),
]

# Segments whose grammar entries come from the replay (see the docstring): name -> why.
FROM_REPLAY = {
    "review-one-finger-sentence": "recorded when one finger browsed words in Review: the log has focus word at "
                                  "295.23 and back word at 298.07; the replay has the sentence at the same times",
}
GRAMMAR_KINDS = ("focus", "back", "commit", "op", "palm_hold")


def main() -> int:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, stem, (a, b), mode, description, issue in SEGMENTS:
        trace, live_log = LOGS / f"{stem}.trace.jsonl", LOGS / f"{stem}.jsonl"
        if not trace.exists():
            print(f"skip  {name}: {trace.name} not found")
            continue
        frames = []
        for line in trace.open():
            fr = json.loads(line)
            if a <= fr["t"] <= b:
                frames.append({"t": round(fr["t"] - a, 3), "hands": [
                    {"label": h["label"], "points": [round(v) for xy in h["points"] for v in xy]}
                    for h in fr["hands"]]})
        live = [json.loads(line) for line in live_log.open()]
        nodes = [e for e in live if e["kind"] == "ring_nodes" and e["t"] <= b]
        before = [e for e in nodes if e["t"] < a][-1:]
        inputs = [{"t": round(max(e["t"] - a, 0.0), 3), "ring_nodes": e["nodes"]}
                  for e in before + [e for e in nodes if e["t"] >= a]]
        sample = {
            "name": name,
            "description": description,
            "source": trace.name,
            "window": [a, b],
            "frame_size": list(FRAME_SIZE),
            "start_mode": mode,
            "expected": [entry(e, a) for e in live if e["kind"] in KINDS and a <= e["t"] <= b],
            "known_issue": issue,
            **({"inputs": inputs} if inputs else {}),
            "frames": frames,
        }
        if name in FROM_REPLAY:
            kept = [e for e in sample["expected"] if e["kind"] not in GRAMMAR_KINDS]
            replayed = [e for e in replay(sample) if e["kind"] in GRAMMAR_KINDS]
            sample["expected"] = sorted(kept + replayed, key=lambda e: e["t"])
            sample["description"] += f" Expected grammar entries from the replay: {FROM_REPLAY[name]}."
            print(f"      {name}: from the replay {replayed}")
        elif not any(e["kind"] == "ring_nodes" for e in live):  # before the knob: its steps from the replay
            steps = [e for e in replay(sample) if e.get("op") == "ring_step"]
            sample["expected"] = sorted(sample["expected"] + steps, key=lambda e: e["t"])
        problems = differences(sample["expected"], replay(sample))
        if issue and not problems:
            print(f"STALE {name}: the known issue no longer shows; drop the note")
            bad += 1
            continue
        if not issue and problems:
            print(f"DIFF  {name}: {'; '.join(problems)}")
            bad += 1
            continue
        out = SAMPLES_DIR / f"{name}.json"
        # One field per line and one frame per line: readable, and diffs stay small.
        head = [f" {json.dumps(k)}: {json.dumps(v)}" for k, v in sample.items() if k not in ("expected", "frames")]
        expected = ",\n".join("  " + json.dumps(e) for e in sample["expected"])
        body = ",\n".join("  " + json.dumps(f, separators=(",", ":")) for f in frames)
        expected = f"[\n{expected}\n ]" if expected else "[]"
        text = ("{\n" + ",\n".join(head) + f',\n "expected": {expected},\n'
                ' "frames": [\n' + body + "\n ]\n}\n")
        assert json.loads(text) == sample
        out.write_text(text)
        print(f"wrote {out.relative_to(ROOT)} ({len(frames)} frames, {len(text) // 1024} KB)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
