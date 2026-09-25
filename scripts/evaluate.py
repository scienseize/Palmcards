"""Compare PalmCards' verdicts, word timings and gesture events with human labels.

  python scripts/evaluate.py LABELS.json [...] [--split tune|holdout] [--json OUT]

Each labels file describes one take (format and protocol: docs/evaluation.md).
Reports, over all the files given (optionally one split only):

  marks     for marks a person judged hit or missed: agreement where PalmCards
            also judged (hit/missed), its false hits and false misses, and
            how often it abstained (unclear) or called the sentence skipped
  words     |PalmCards' word start - the labelled start|: median, p90, and
            words PalmCards did not align at all
  gestures  mode events in the gesture log outside every labelled
            intended-action interval: false triggers, per minute of video

Synthetic tests check this arithmetic, not whether PalmCards is right: only
real, consented, labelled takes can say that.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from palmcards.paths import data_dir  # noqa: E402
from palmcards.session import Session  # noqa: E402

# Gesture-log entries that are the app acting on a gesture: a zone command
# (flick, hold), a focus or commit, a mode change (a take started or
# stopped...). Keys are logged as "key" and are never false triggers.
ACTIONS = ("zone", "focus", "commit", "mode")


def evaluate(labels: dict, root: Path | None = None) -> dict:
    """Metrics for one labelled take."""
    root = root or data_dir()
    session = Session.load(root / labels["session"])
    take = session.take(labels["take"])
    verdicts = json.loads((session.dir / take.verdicts).read_text()) if take.verdicts else {"sentences": []}
    by_sentence = {s["sentence"]: s["marks"] for s in verdicts["sentences"]}
    out = {"marks": [], "words": [], "false_triggers": None, "minutes": None}
    for m in labels.get("marks", []):
        if m["label"] not in ("hit", "missed"):
            continue  # a person's "unclear" is not a reference
        marks = by_sentence.get(m["sentence"], [])
        system = marks[m["mark"]]["verdict"] if m["mark"] < len(marks) else "absent"
        out["marks"].append((m["label"], system))
    if labels.get("words") and take.alignment and take.transcript:
        words = json.loads((session.dir / take.transcript).read_text())["words"]
        for w in labels["words"]:
            idx = take.alignment["sentences"][w["sentence"]]["words"][w["word"]]
            out["words"].append(None if idx is None else words[idx]["start"] - (take.t_start + w["start"]))
    if "gestures" in labels and session.gesture_log:
        log = root / session.gesture_log
        events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        span = labels["gestures"]
        t0, t1 = span["from"], span["to"]
        keyed = [e["t"] for e in events if e["kind"] == "key" and e.get("acted")]
        fired = [e for e in events if e["kind"] in ACTIONS and t0 <= e["t"] <= t1
                 and not (e["kind"] == "mode" and any(abs(e["t"] - k) < 0.05 for k in keyed))]
        intended = span["intended"]
        out["false_triggers"] = sum(not any(a <= e["t"] <= b for a, b, _ in intended) for e in fired)
        out["minutes"] = (t1 - t0) / 60
    return out


def summarise(results: list[dict]) -> dict:
    pairs = [p for r in results for p in r["marks"]]
    judged = [(h, s) for h, s in pairs if s in ("hit", "missed")]
    agree = sum(h == s for h, s in judged)
    offsets = [abs(o) for r in results for o in r["words"] if o is not None]
    unaligned = sum(o is None for r in results for o in r["words"])
    minutes = sum(r["minutes"] or 0 for r in results)
    triggers = sum(r["false_triggers"] or 0 for r in results)
    return {
        "marks": {"labelled": len(pairs), "judged_by_both": len(judged),
                  "agreement": round(agree / len(judged), 3) if judged else None,
                  "false_hits": sum(h == "missed" and s == "hit" for h, s in judged),
                  "false_misses": sum(h == "hit" and s == "missed" for h, s in judged),
                  "abstained": sum(s == "unclear" for _, s in pairs),
                  "called_skipped": sum(s == "skipped" for _, s in pairs),
                  "abstention_rate": round(sum(s == "unclear" for _, s in pairs) / len(pairs), 3) if pairs else None},
        "words": {"labelled": len(offsets) + unaligned, "unaligned": unaligned,
                  "median_error_s": round(float(np.median(offsets)), 3) if offsets else None,
                  "p90_error_s": round(float(np.percentile(offsets, 90)), 3) if offsets else None},
        "gestures": {"minutes": round(minutes, 2), "false_triggers": triggers,
                     "per_minute": round(triggers / minutes, 3) if minutes else None},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("labels", type=Path, nargs="+")
    ap.add_argument("--split", choices=("tune", "holdout"))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    files = [json.loads(p.read_text()) for p in args.labels]
    files = [f for f in files if args.split is None or f.get("split") == args.split]
    if not files:
        print("no labelled takes for that split", file=sys.stderr)
        return 1
    if any(not f.get("consent") for f in files):
        print("error: every labelled take must record the speaker's consent (\"consent\": true)", file=sys.stderr)
        return 1
    summary = summarise([evaluate(f) for f in files])
    print(json.dumps(summary, indent=1))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
