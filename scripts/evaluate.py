"""Compare PalmCards' verdicts, word timings and gesture events with human labels.

  python scripts/evaluate.py LABELS.json [...] [--split tune|holdout] [--json OUT]
  python scripts/evaluate.py --gaze RUN [--take N] [--sweep] [--json OUT]

Each labels file describes one take (format and protocol: docs/evaluation.md).
Reports, over all the files given (optionally one split only):

  marks     for marks a person judged hit or missed: agreement where PalmCards
            also judged (hit/missed), its false hits and false misses, and
            how often it abstained (unclear) or called the sentence skipped
  words     |PalmCards' word start - the labelled start|: median, p90, and
            words PalmCards did not align at all
  gestures  mode events in the gesture log outside every labelled
            intended-action interval: false triggers, per minute of video

--gaze reads a gaze-check take (main.py --gaze-check; default: the session's
latest) and compares its prompts (camera, notes, away) with the gaze
classifier's class for each face reading inside them, the first
GAZE.check_settle_s of each prompt left out: agreement and Cohen's kappa over
the readings judged, recall per target, the confusion table, how many were
unclear, and each target's median head and iris values (for tuning GAZE).
--sweep scores the same take again over a grid of GAZE settings (the scale
floors x 1-4, the camera and notes radii) and lists the best by kappa. Tune
on one check take, then confirm on another: a setting picked on a take
always looks better on that take than it will on the next.

Synthetic tests check this arithmetic, not whether PalmCards is right: only
real, consented, labelled takes can say that.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from palmcards import features, gaze  # noqa: E402
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


def gaze_check(run: str | Path, take_number: int | None = None, root: Path | None = None) -> dict:
    """A gaze-check take's prompts against the classifier (see the module's --gaze)."""
    folder, take, arrays, calibration = _gaze_take(run, take_number, root)
    out = gaze.check_agreement(arrays, calibration, take.gaze_check["prompts"], take.gaze_check["t0"])
    out.update(session=folder.name, take=take.number, calibration=calibration["id"] if calibration else None,
               separation=round(gaze.separation(calibration), 2) if calibration and calibration.get("status") == "ok"
               else None)
    return out


def gaze_sweep(run: str | Path, take_number: int | None = None, root: Path | None = None, top: int = 8) -> list[dict]:
    """The check take scored over a grid of GAZE settings, best kappa first."""
    _, take, arrays, calibration = _gaze_take(run, take_number, root)
    base, rows = gaze.GAZE, []
    try:
        for f, cam, notes in itertools.product((1.0, 1.5, 2.0, 3.0, 4.0), (2.0, 3.0, 4.0, 5.0), (3.0, 4.0, 5.0, 6.0)):
            gaze.GAZE = replace(base, floor_yaw=base.floor_yaw * f, floor_pitch=base.floor_pitch * f,
                                floor_iris_x=base.floor_iris_x * f, floor_iris_y=base.floor_iris_y * f,
                                camera_radius=cam, notes_radius=notes)
            r = gaze.check_agreement(arrays, calibration, take.gaze_check["prompts"], take.gaze_check["t0"])
            rows.append({"floors_x": f, "camera_radius": cam, "notes_radius": notes, "kappa": r["kappa"],
                         "agreement": r["agreement"], "recall": r["recall"],
                         "usable": r["calibration_usable"] == "yes"})
    finally:
        gaze.GAZE = base
    rows.sort(key=lambda r: -1 if r["kappa"] is None else r["kappa"], reverse=True)
    return rows[:top]


def _gaze_take(run: str | Path, take_number: int | None, root: Path | None):
    folder = Path(run)
    if not (folder / "session.json").exists():
        folder = (root or data_dir()) / str(run)
    session = Session.load(folder)
    checks = [t for t in session.takes if t.gaze_check]
    if take_number is not None:
        take = session.take(take_number)
        if not take.gaze_check:
            raise SystemExit(f"take {take_number} of {folder.name} is not a gaze check (main.py --gaze-check)")
    elif checks:
        take = checks[-1]
    else:
        raise SystemExit(f"{folder.name} has no gaze-check take: record one with main.py --gaze-check")
    if not take.vision or take.vision.get("state") != "recorded":
        raise SystemExit(f"take {take.number} has no face features ({(take.vision or {}).get('reason', 'none')})")
    arrays, _ = features.load(session.dir / take.vision["file"])
    calibration = next((c for c in session.calibrations if c["id"] == take.vision.get("calibration")), None)
    return folder, take, arrays, calibration


def gaze_report(r: dict) -> str:
    lines = [f"gaze check: take {r['take']} of {r['session']}, calibration {r['calibration']} "
             f"(separation {r['separation']}; usable: {r['calibration_usable']})",
             f"  readings {r['readings']}, judged {r['judged']}, unclear {r['unclear_share']}",
             f"  agreement {r['agreement']}, kappa {r['kappa']}",
             f"  {'prompt':<8} {'camera':>7} {'notes':>7} {'away':>7} {'unclear':>8}  recall   median yaw, pitch, iris x, y"]
    for target, row in r["confusion"].items():
        m = r["medians"][target]
        med = ", ".join("-" if m[k] is None else f"{m[k]:g}" for k in gaze.FEATURES)
        lines.append(f"  {target:<8} {row['camera']:>7} {row['notes']:>7} {row['away']:>7} {row['unclear']:>8}  "
                     f"{r['recall'][target]!s:<7}  {med}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("labels", type=Path, nargs="*")
    ap.add_argument("--split", choices=("tune", "holdout"))
    ap.add_argument("--gaze", metavar="RUN", help="report a gaze-check take of this session")
    ap.add_argument("--take", type=int, help="with --gaze: the take (default: the latest gaze check)")
    ap.add_argument("--sweep", action="store_true", help="with --gaze: score a grid of GAZE settings")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    if args.gaze:
        result = gaze_check(args.gaze, args.take)
        print(gaze_report(result))
        if args.sweep:
            result["sweep"] = gaze_sweep(args.gaze, args.take)
            print("  best settings (GAZE floors x, camera radius, notes radius): kappa, agreement, recall")
            for r in result["sweep"]:
                print(f"    x{r['floors_x']:g}, {r['camera_radius']:g}, {r['notes_radius']:g}: {r['kappa']}, "
                      f"{r['agreement']}, {r['recall']}" + ("" if r["usable"] else "  (calibration unusable)"))
        if args.json:
            args.json.write_text(json.dumps(result, indent=1) + "\n")
        return 0
    if not args.labels:
        ap.error("labels files, or --gaze RUN")
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
