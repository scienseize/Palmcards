"""The evaluation arithmetic, on a synthetic labelled take (this checks the
script, not PalmCards' accuracy)."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from palmcards.session import Session

ROOT = Path(__file__).resolve().parent.parent


def evaluate_module():
    spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_agreement_abstention_word_error_and_false_triggers(tmp_path):
    root = tmp_path / "sessions"
    (tmp_path / "n.md").write_text("Hello / there friend. Good *night* all. [fall]")
    log = root / "gesture-logs" / "x.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(json.dumps(e) for e in [
        {"t": 11.0, "kind": "mode", "mode": "count_in"},   # intended (inside a labelled interval)
        {"t": 30.0, "kind": "zone", "command": "flick"},  # not intended: a false trigger
        {"t": 40.0, "kind": "key", "command": "stop", "acted": True},
        {"t": 40.0, "kind": "mode", "mode": "review"},     # from the key: not a gesture trigger
    ]) + "\n")
    session = Session.create(tmp_path / "n.md", root=root, gesture_log=log)
    take = session.add_take(np.zeros(800, np.float32), 8000, 10.0, datetime.now(), [(0.0, 0)])
    (session.dir / "take-01.transcript.json").write_text(json.dumps({"words": [
        {"text": "Hello", "start": 10.5, "end": 10.8}, {"text": "there", "start": 11.3, "end": 11.6}]}))
    (session.dir / "take-01.verdicts.json").write_text(json.dumps({"sentences": [
        {"sentence": 0, "marks": [{"verdict": "hit"}]},
        {"sentence": 1, "marks": [{"verdict": "unclear"}, {"verdict": "missed"}]}]}))
    alignment = {"sentences": [{"words": [0, 1, None]}, {"words": [None, None, None]}]}
    session.set_result(1, "take-01.transcript.json", alignment, "take-01.verdicts.json", {})
    labels = {"session": session.dir.name, "take": 1, "consent": True, "split": "holdout",
              "marks": [{"sentence": 0, "mark": 0, "label": "missed"},   # PalmCards said hit: a false hit
                        {"sentence": 1, "mark": 0, "label": "hit"},      # PalmCards abstained
                        {"sentence": 1, "mark": 1, "label": "missed"},   # agreement
                        {"sentence": 1, "mark": 1, "label": "unclear"}],  # not a reference: ignored
              "words": [{"sentence": 0, "word": 0, "start": 0.45}, {"sentence": 0, "word": 1, "start": 1.1},
                        {"sentence": 0, "word": 2, "start": 1.7}],
              "gestures": {"from": 0.0, "to": 60.0, "intended": [[10.5, 12.0, "start"]]}}
    m = evaluate_module()
    summary = m.summarise([m.evaluate(labels, root)])
    assert summary["marks"] == {"labelled": 3, "judged_by_both": 2, "agreement": 0.5, "false_hits": 1,
                                "false_misses": 0, "abstained": 1, "called_skipped": 0, "abstention_rate": 0.333}
    assert summary["words"] == {"labelled": 3, "unaligned": 1, "median_error_s": 0.125, "p90_error_s": 0.185}
    assert summary["gestures"] == {"minutes": 1.0, "false_triggers": 1, "per_minute": 1.0}
