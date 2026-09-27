"""Replay recorded hand landmarks through the gesture code, for regression tests.

A gesture sample (samples/gestures/*.json) is a stretch of a `main.py --trace`
recording: MediaPipe's 21 hand landmarks per frame, already run once, so
replaying needs no camera, no model and no video. Each sample carries what
the app recognised live while the gesture was performed (`expected`), and
the replay must reproduce it.

    {"name": "word-ring-commit",
     "description": "...",
     "source": "20260925-024808.trace.jsonl", "window": [116.0, 130.1],
     "frame_size": [1280, 720], "start_mode": "prepare",
     "expected": [{"t": 3.21, "kind": "focus", "level": "word"}, ...],
     "known_issue": null,
     "frames": [{"t": 0.0, "hands": [{"label": "Left", "points": [x0, y0, ..., x20, y20]}]}, ...]}

Times are seconds from the start of the sample; points are whole pixels in
the mirrored frame. `expected` holds the grammar and mode entries of the
gesture log (KINDS); poses are left out, they flicker too much to pin down.
A step of the options ring's knob keeps its direction (`dir`). What the app
told the grammar is in `inputs`, given back at its time: the options ring's
nodes, [{"t": 1.2, "ring_nodes": ["imparted", "stamped", "hear it"]}]
(optional; without it the ring has the grammar's DEFAULT_RING), and the focus
hold while audio plays, {"t": 3.4, "focus_hold": "play"} and then null when it
ends (the app logs these after the hand frame they follow, so they are given
back before the next one).
`known_issue` explains a sample the current code gets wrong: the sample is
kept as evidence, and its test is an expected failure until the bug is fixed.

    python -m palmcards.replay [SAMPLE ...]      (default: all of samples/gestures/)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from palmcards.gestures import GestureLog, Hand, ModeMachine

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "gestures"
KINDS = ("focus", "back", "commit", "op", "zone", "mode", "palm_hold", "palm_stop")
DETAIL = ("level", "op", "command", "mode")  # the field that says which focus, op, ...
EXTRA = ("dir",)  # kept as well: which way the ring's knob stepped
TIME_TOL = 0.25  # seconds an entry may drift before it counts as a change


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def hands(frame: dict) -> list[Hand]:
    return [Hand(np.array(h["points"], np.float32).reshape(21, 2), handedness=h["label"]) for h in frame["hands"]]


def entry(e: dict, t0: float = 0.0) -> dict:
    """A log entry reduced to its time, kind and the one field that matters."""
    out = {"t": round(e["t"] - t0, 3), "kind": e["kind"]}
    for key in DETAIL:
        if key in e:
            out[key] = e[key]
            break
    for key in EXTRA:
        if key in e:
            out[key] = e[key]
    return out


def replay(sample: dict) -> list[dict]:
    """The sample's frames through ModeMachine; its grammar and mode log entries."""
    log = GestureLog(keep=1_000_000)
    machine = ModeMachine(tuple(sample["frame_size"]), log)
    frames = sample["frames"]
    if sample["start_mode"] != "prepare":
        machine.enter(sample["start_mode"], frames[0]["t"] if frames else 0.0)
    log.entries.clear()
    inputs = sorted(sample.get("inputs", []), key=lambda i: i["t"])
    for frame in frames:
        while inputs and inputs[0]["t"] <= frame["t"]:
            given = inputs.pop(0)
            if "ring_nodes" in given:
                machine.grammar.set_ring_labels(given["t"], tuple(given["ring_nodes"]))
            if "focus_hold" in given:
                machine.grammar.set_focus_hold(given["t"], given["focus_hold"])
        machine.update(hands(frame), frame["t"])
        machine.tick(frame["t"])
    return [entry(e) for e in log.entries if e["kind"] in KINDS]


def differences(expected: list[dict], actual: list[dict], tol: float = TIME_TOL) -> list[str]:
    """What changed, in words; empty if the replay matches."""
    def label(e: dict) -> str:
        return " ".join(str(v) for k, v in e.items() if k != "t")

    problems = []
    if [label(e) for e in expected] != [label(e) for e in actual]:
        problems.append(f"expected {[label(e) for e in expected]}, got {[label(e) for e in actual]}")
    else:
        for e, a in zip(expected, actual):
            if abs(e["t"] - a["t"]) > tol:
                problems.append(f"{label(e)} at {a['t']:.2f} s, expected {e['t']:.2f} s")
    return problems


def _main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv] or sorted(SAMPLES_DIR.glob("*.json"))
    failed = 0
    for path in paths:
        sample = load(path)
        problems = differences(sample["expected"], replay(sample))
        if sample.get("known_issue"):
            status = "KNOWN ISSUE (fixed?)" if not problems else "known issue"
        else:
            status = "ok" if not problems else "CHANGED"
            failed += bool(problems)
        print(f"{status:>20}  {path.stem}")
        for p in problems:
            print(f"{'':22}{p}")
        if sample.get("known_issue") and problems:
            print(f"{'':22}({sample['known_issue']})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
