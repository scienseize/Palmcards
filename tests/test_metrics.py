"""Take metrics: observations with what they rest on, None with a reason
when there isn't enough, and nothing about gaze or posture yet."""

import json

import numpy as np
import pytest

from palmcards import metrics
from palmcards.align import align
from palmcards.notes import parse_text
from tests.test_cues import speak

TEXT = "Good evening everyone thank you. / We are so glad you could make it tonight."


def aligned(script, text=TEXT):
    notes = parse_text(text)
    words = speak(script)
    return align([[w.norm for w in s.words] for s in notes.sentences], words), words, notes


def test_speech_metrics_rest_on_what_was_said():
    al, words, notes = aligned("Good evening everyone um thank you. <2.0> We are so glad <1.8> you could make "
                               "it tonight. so")
    marks = [[[m.kind, m.word] for m in s.marks] for s in notes.sentences]
    m = metrics.speech(al, words, 60.0, metrics.planned_pause_words(al, marks))
    assert m["pace_wpm"]["value"] > 100 and "14 words" in m["pace_wpm"]["basis"]
    assert m["fillers_per_min"]["value"] == 2.0  # "um" and the trailing "so" in a minute
    # The 2.0 s gap sits where the notes ask for a pause: planned. The 1.8 s one isn't.
    assert m["unplanned_long_pauses"]["value"] == 1 and m["unplanned_long_pauses"]["longest_s"] == 1.8
    assert (m["restarts"]["value"], m["ad_libs"]["value"]) == (0, 0)


def test_too_little_to_go_on_says_so():
    al, words, _ = aligned("Good evening")
    m = metrics.speech(al, words, 4.0)
    assert m["pace_wpm"]["value"] is None and "too little said" in m["pace_wpm"]["reason"]
    assert m["fillers_per_min"]["value"] is None and "too short" in m["fillers_per_min"]["reason"]


def test_hands_from_the_gesture_log_and_the_trace(tmp_path):
    log = tmp_path / "x.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"t": 9.0, "kind": "pose", "hand": "Right", "pose": "FIST"},  # before the take
        {"t": 11.0, "kind": "pose", "hand": "Right", "pose": "OPEN"},
        {"t": 12.0, "kind": "pose", "hand": "Right", "pose": "NONE"},
        {"t": 13.0, "kind": "pose", "hand": "Right", "pose": "ONE"}]) + "\n")
    m = metrics.hands(log, None, 10.0, 40.0)
    assert m["shape_changes_per_min"]["value"] == 4.0  # 2 shapes in half a minute (NONE isn't one)
    assert m["movement_palms_s"]["value"] is None and "--trace" in m["movement_palms_s"]["reason"]
    trace = tmp_path / "x.trace.jsonl"
    hand = lambda x: {"label": "Right", "points": [[x, 100.0]] + [[0.0, 0.0]] * 8 + [[x, 200.0]] + [[0.0, 0.0]] * 11}
    frames = [{"t": 10.0 + k * 0.1, "hands": [hand(100.0 + 10 * k)] if k < 30 else []} for k in range(40)]
    trace.write_text("\n".join(json.dumps(f) for f in frames) + "\n")
    m = metrics.hands(log, trace, 10.0, 14.0)
    assert m["in_view_share"]["value"] == pytest.approx(30 / 40, abs=0.03)
    assert m["movement_palms_s"]["value"] == pytest.approx(1.0)  # 10 px per 0.1 s, palm 100 px


def test_gaze_and_posture_are_not_measured():
    al, words, _ = aligned("Good evening everyone thank you.")
    m = metrics.take_metrics(al, words, 0.0, 30.0)
    assert m["gaze"]["value"] is None and "not measured" in m["gaze"]["reason"]
    assert m["posture"]["value"] is None and m["version"] == 1


def test_the_analysis_stores_metrics_on_the_take(tmp_path):
    from datetime import datetime

    from palmcards.session import Session
    from palmcards.speech import make_job, run_job

    (tmp_path / "n.md").write_text(TEXT)
    session = Session.create(tmp_path / "n.md", root=tmp_path / "sessions")
    take = session.add_take(np.zeros(16000 * 12, np.float32), 16000, 5.0, datetime.now(), [(0.0, 0)])
    result = run_job({**make_job(session, take, session.notes_for(take)), "job": "j"})
    assert result["metrics"]["speech"]["pace_wpm"]["value"] is None  # a silent take
    session.set_result(1, result["transcript"], result["alignment"], result["verdicts"], result["marks"],
                       result["metrics"])
    assert Session.load(session.dir).take(1).metrics["gaze"]["value"] is None
