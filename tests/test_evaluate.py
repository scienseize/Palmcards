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


def test_word_error_and_false_triggers(tmp_path):
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
    alignment = {"sentences": [{"words": [0, 1, None]}, {"words": [None, None, None]}]}
    session.set_result(1, "take-01.transcript.json", alignment)
    labels = {"session": session.dir.name, "take": 1, "consent": True, "split": "holdout",
              "words": [{"sentence": 0, "word": 0, "start": 0.45}, {"sentence": 0, "word": 1, "start": 1.1},
                        {"sentence": 0, "word": 2, "start": 1.7}],
              "gestures": {"from": 0.0, "to": 60.0, "intended": [[10.5, 12.0, "start"]]}}
    m = evaluate_module()
    summary = m.summarise([m.evaluate(labels, root)])
    assert "marks" not in summary
    assert summary["words"] == {"labelled": 3, "unaligned": 1, "median_error_s": 0.125, "p90_error_s": 0.185}
    assert summary["gestures"] == {"minutes": 1.0, "false_triggers": 1, "per_minute": 1.0}


def test_the_take_table_has_a_row_per_take_with_its_metrics_and_why_any_is_missing(tmp_path):
    import csv
    import io

    root = tmp_path / "sessions"
    (tmp_path / "n.md").write_text("Hello there. Good night all.")
    session = Session.create(tmp_path / "n.md", root=root)
    for _ in range(2):
        session.add_take(np.zeros(800, np.float32), 8000, 10.0, datetime.now(), [(0.0, 0)])
    take = session.take(1)
    take.marks = {"hit": 2, "missed": 1, "unclear": 0, "skipped": 0}  # from when marks were judged: not a column
    take.vision = {"state": "recorded", "file": "take-01.face.npz", "calibration": "c1"}
    take.metrics = {
        "version": 4,
        "speech": {"pace_wpm": {"value": 140.0}, "fillers_per_min": {"value": None, "reason": "take too short (5 s)"},
                   "unplanned_long_pauses": {"value": 0}, "restarts": {"value": 1}, "ad_libs": {"value": 0}},
        "hands": {"shape_changes_per_min": {"value": 3.0}, "in_view_share": {"value": 0.2},
                  "movement_palms_s": {"value": 1.1}, "fingertip_movement_palms_s": {"value": 2.0},
                  "face_touches": {"value": 2, "seconds": 1.4}},
        "gaze": {"screen_share": {"value": 0.9}, "away_share": {"value": 0.1}, "unclear_share": {"value": 0.05}},
        "voice": {"pitch_range_st": {"value": 6.4}, "pitch_sd_st": {"value": 2.1},
                  "loudness_range_db": {"value": 9.5}, "voiced_s": {"value": 31.0}},
        "posture": {"value": None, "reason": "no posture baseline: no usable eye calibration"},
    }
    session.save()
    session.release()
    m = evaluate_module()
    rows = m.take_table([session.dir.name], root)
    assert [r["take"] for r in rows] == [1, 2]
    one, two = rows
    assert one["calibration"] == "c1" and "hit" not in one and one["pace_wpm"] == 140.0 and one["pitch_range_st"] == 6.4
    assert one["face_touches"] == 2 and one["face_touch_s"] == 1.4 and one["gaze_screen_share"] == 0.9
    assert one["fillers_per_min"] == "" and one["tilted_share"] == ""
    assert "fillers_per_min: take too short (5 s)" in one["missing"]
    assert one["missing"].count("posture: no posture baseline") == 1  # the group's reason once
    assert two["pace_wpm"] == "" and "metrics: none" in two["missing"]
    out = io.StringIO()
    m.write_table(rows, out)
    back = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert list(back[0]) == list(m.COLUMNS) and back[0]["gaze_away_share"] == "0.1"
