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


def test_without_face_features_gaze_posture_and_touches_say_why():
    al, words, _ = aligned("Good evening everyone thank you.")
    m = metrics.take_metrics(al, words, 0.0, 30.0)
    assert m["gaze"]["value"] is None and "no face features" in m["gaze"]["reason"]
    assert m["posture"]["value"] is None and "no face features" in m["posture"]["reason"] and m["version"] == 3
    assert "no face features" in m["hands"]["face_touches"]["reason"]


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


# --- hands, face touches and posture from the take's features (milestone 7, stage 3) ---------

def hand_rows(n_frames, fps=30.0, t0=100.0, **columns):
    """Hand-row arrays: every column given as a list or a constant; missing columns NaN."""
    t = t0 + np.arange(n_frames) / fps
    out = {"hand_t": t}
    for key in ("n", "wrist_move", "tip_move", "tip_face", "y", "tip_oval", "scale"):
        v = columns.get(key, 1 if key == "n" else np.nan)
        arr = np.full(n_frames, v, dtype=np.float64) if np.isscalar(v) else np.asarray(v, dtype=np.float64)
        out[f"hand_{key}"] = arr.astype(np.int8) if key == "n" else arr.astype(np.float32)
    return out


def test_hand_movement_without_a_trace():
    # 2 s at 30 results/s with a hand in view, the wrist moving 0.1 palm per result: 3 palms/s;
    # then 1 s with no hand, which counts toward the take but not toward the movement.
    a = hand_rows(90, n=[1] * 60 + [0] * 30, wrist_move=0.1, tip_move=0.2)
    m = metrics.hands(None, None, 100.0, 103.0, a)
    assert m["in_view_share"]["value"] == pytest.approx(60 / 90, abs=1e-3)
    assert m["movement_palms_s"]["value"] == pytest.approx(3.0, abs=0.1)
    assert m["fingertip_movement_palms_s"]["value"] == pytest.approx(6.0, abs=0.2)
    brief = metrics.hands(None, None, 100.0, 101.0, hand_rows(20, n=1, wrist_move=0.1))
    assert brief["movement_palms_s"]["value"] is None and "needs 1" in brief["movement_palms_s"]["reason"]


def test_face_touches_need_the_outline_and_the_face_depth():
    # 60 results: a touch of 0.5 s (15 results) with one result off inside it (bridged), a hand over the
    # face in the picture but twice its size (in front of the face: not a touch), and a brush of 0.1 s.
    oval = np.full(60, 0.5)
    scale = np.full(60, 0.65)
    oval[5:20] = 0.0
    oval[12] = 0.2
    oval[30:45] = 0.0
    scale[30:45] = 1.6
    oval[50:53] = 0.0
    m = metrics.face_touches(hand_rows(60, tip_oval=oval, scale=scale))
    assert m["value"] == 1 and m["seconds"] == pytest.approx(14 / 30, abs=0.005)  # kept to 2 decimals


def test_face_touches_say_why_when_they_cannot_be_counted():
    old = hand_rows(10)
    del old["hand_tip_oval"]
    assert "features version 1" in metrics.face_touches(old)["reason"]
    assert metrics.face_touches(hand_rows(10, n=0))["value"] == 0  # no hand, no touch
    assert "never seen" in metrics.face_touches(hand_rows(10))["reason"]  # hands, but no face with them


CAL = {"id": "c1", "status": "ok", "posture": {"n": 20, "tilt": [2.0, 0.5], "width": [0.45, 0.01], "head": [0.45, 0.01]}}


def pose_rows(tilt, head, vis=0.9):
    n = len(tilt)
    return {"pose_t": np.arange(n) * 0.5, "pose_found": np.ones(n, np.int8), "pose_vis": np.full(n, vis, np.float32),
            "pose_tilt": np.asarray(tilt, np.float32), "pose_head": np.asarray(head, np.float32),
            "pose_width": np.full(n, 0.45, np.float32)}


def test_posture_against_the_calibration():
    # 20 readings: shoulders as calibrated but 5 of them tilted by 8 degrees; the head 0.1 lower in 10.
    tilt = [2.0] * 15 + [10.0] * 5
    head = [0.45] * 10 + [0.35] * 10
    m = metrics.posture(pose_rows(tilt, head), CAL)
    assert m["shoulder_tilt_deg"]["value"] == 0.0 and m["tilted_share"]["value"] == 0.25
    assert m["head_height_change"]["value"] == pytest.approx(-0.05, abs=1e-3)
    assert m["head_dropped_share"]["value"] == 0.5 and m["calibration"] == "c1"
    assert "calibration's +2.0" in m["shoulder_tilt_deg"]["basis"]


def test_posture_says_why_when_it_cannot_be_measured():
    rows = pose_rows([2.0] * 20, [0.45] * 20)
    assert "no posture baseline" in metrics.posture(rows, None)["reason"]
    assert "no posture baseline" in metrics.posture(rows, {**CAL, "status": "failed"})["reason"]
    assert "too few pose readings" in metrics.posture(pose_rows([2.0] * 5, [0.45] * 5), CAL)["reason"]
    assert "too few pose readings" in metrics.posture(pose_rows([2.0] * 20, [0.45] * 20, vis=0.2), CAL)["reason"]
    assert metrics.posture(None, CAL, "face tracking was off: x")["reason"] == "face tracking was off: x"
