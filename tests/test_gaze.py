"""The gaze classifier against a calibration, the take's gaze metric (only
while speaking), the gaze check's prompts and its agreement arithmetic."""

import numpy as np
import pytest

from palmcards import gaze, metrics
from palmcards.config import GAZE

CAL = {"id": "c1", "status": "ok",
       "camera": {"n": 20, "yaw": [0.0, 0.2], "pitch": [0.0, 0.2], "iris_x": [0.53, 0.005], "iris_y": [-0.08, 0.003]},
       "notes": {"n": 20, "yaw": [-8.0, 0.5], "pitch": [1.0, 0.5], "iris_x": [0.49, 0.008], "iris_y": [-0.085, 0.002]}}
CAMERA = (0.0, 0.0, 0.53, -0.08)
NOTES = (-8.0, 1.0, 0.49, -0.085)
AWAY = (15.0, -12.0, 0.62, 0.02)


def readings(rows, t=None, found=None, eye_open=None):
    """Face arrays from (yaw, pitch, iris_x, iris_y) rows."""
    x = np.array(rows, dtype=np.float64).reshape(-1, 4)
    n = len(x)
    return {"face_t": np.arange(n, dtype=np.float64) * 0.2 if t is None else np.asarray(t, dtype=np.float64),
            "face_found": np.ones(n, np.int8) if found is None else np.asarray(found, np.int8),
            "face_yaw": x[:, 0].astype(np.float32), "face_pitch": x[:, 1].astype(np.float32),
            "face_iris_x": x[:, 2].astype(np.float32), "face_iris_y": x[:, 3].astype(np.float32),
            "face_eye_open": np.full(n, 0.3, np.float32) if eye_open is None else np.asarray(eye_open, np.float32)}


def test_scales_have_floors_and_follow_the_calibration_spread():
    s = gaze.scales(CAL)
    assert s["yaw"] == GAZE.floor_yaw  # 2 x 0.5 is under the floor
    assert s["iris_x"] == pytest.approx(GAZE.mad_scale * 0.008)
    assert gaze.separation(CAL) > GAZE.min_separation and gaze.usable(CAL) == "" and gaze.separates(CAL) == ""


def test_each_reading_gets_a_class():
    labels = gaze.classify(readings([CAMERA, NOTES, AWAY, (-1.0, 0.2, 0.525, -0.08), (-7.0, 2.0, 0.495, -0.09)]), CAL)
    assert labels.tolist() == ["camera", "notes", "away", "camera", "notes"]


def test_no_face_a_blink_or_a_missing_value_is_unclear():
    arrays = readings([CAMERA, CAMERA, (np.nan, 0.0, 0.53, -0.08)], found=[0, 1, 1], eye_open=[0.3, 0.05, 0.3])
    assert gaze.classify(arrays, CAL).tolist() == ["unclear"] * 3


def test_a_calibration_that_cannot_split_camera_from_notes_still_tells_screen_from_away():
    close = {**CAL, "notes": {**CAL["camera"], "yaw": [0.5, 0.2]}}
    assert gaze.usable(close) == "" and "can't tell camera from notes" in gaze.separates(close)
    labels = gaze.classify(readings([CAMERA, (0.5, 0.0, 0.53, -0.08), AWAY]), close)
    assert labels[2] == "away" and set(labels[:2]) <= {"camera", "notes"}
    assert "failed" in gaze.usable({"status": "failed", "reason": "no face"})
    assert gaze.usable(None) == "no eye calibration when the take was recorded"
    assert set(gaze.classify(readings([CAMERA]), None)) == {"unclear"}


def alignment(*spans):
    return {"sentences": [{"sentence": i, "status": "spoken", "start": a, "end": b} for i, (a, b) in enumerate(spans)]
            + [{"sentence": len(spans), "status": "skipped", "start": None, "end": None}]}


def test_the_take_metric_counts_only_readings_while_speaking():
    # Sentence 0 from 10 to 14 s looking at the camera, sentence 1 from 20 to 24 s at the notes;
    # a silence in between spent looking away is not counted.
    t = np.concatenate([np.arange(10, 14, 0.2), np.arange(15, 19, 0.2), np.arange(20, 24, 0.2)])
    rows = [CAMERA] * 20 + [AWAY] * 20 + [NOTES] * 15 + [AWAY] * 5
    m = gaze.take_gaze(readings(rows, t=t), CAL, alignment((10.0, 13.9), (20.0, 23.9)))
    assert m["screen_share"]["value"] == pytest.approx(35 / 40) and m["away_share"]["value"] == pytest.approx(5 / 40)
    assert m["unclear_share"]["value"] == 0.0 and "camera_share" not in m  # the split is not reported as a share
    assert m["counts"] == {"camera": 20, "notes": 15, "away": 5, "unclear": 0}
    assert m["split"]["validated"] is False and m["split"]["separation"] > GAZE.min_separation
    assert m["sentences"] == [{"sentence": 0, "screen": 20, "away": 0, "unclear": 0, "camera": 20, "notes": 0},
                              {"sentence": 1, "screen": 15, "away": 5, "unclear": 0, "camera": 0, "notes": 15}]
    assert m["calibration"] == "c1" and "40 face readings judged while speaking" in m["screen_share"]["basis"]


def test_too_little_to_go_on_is_none_with_a_reason():
    m = gaze.take_gaze(readings([CAMERA] * 5, t=np.arange(10, 11, 0.2)), CAL, alignment((10.0, 11.0)))
    assert m["value"] is None and "too few readings" in m["reason"]
    assert gaze.take_gaze(None, CAL, alignment((0, 1)))["reason"] == "no face features for this take"
    assert "no eye calibration" in gaze.take_gaze(readings([CAMERA]), None, alignment((0, 1)))["reason"]


def test_the_metric_says_why_a_take_has_no_gaze(tmp_path):
    al = alignment((0.0, 1.0))
    assert "before milestone 7" in metrics.gaze(al, None, None, CAL)["reason"]
    off = metrics.gaze(al, {"state": "off", "reason": "models/face_landmarker.task missing"}, None, CAL)
    assert off["reason"] == "face tracking was off: models/face_landmarker.task missing"
    missing = metrics.gaze(al, {"state": "recorded", "file": "take-01.face.npz"}, tmp_path / "take-01.face.npz", CAL)
    assert missing["reason"] == "the face features file is missing"


def test_the_prompt_schedule_is_shuffled_without_repeats_and_reproducible():
    a = gaze.prompt_schedule(7)
    assert a == gaze.prompt_schedule(7) and a != gaze.prompt_schedule(8)
    targets = [p["target"] for p in a]
    assert sorted(targets) == sorted(["camera", "notes", "away"] * GAZE.check_each)
    assert all(x != y for x, y in zip(targets, targets[1:]))
    assert a[0]["t0"] == 0.0 and a[-1]["t1"] == pytest.approx(len(a) * GAZE.check_step_s)
    hints = [p["hint"] for p in a if p["target"] == "away"]
    assert hints == list(gaze.AWAY_HINTS[:len(hints)])
    assert gaze.prompt_text(a[0]).startswith(("LOOK INTO", "READ", "LOOK AWAY"))


def test_the_check_compares_prompts_with_the_classes_after_settling():
    prompts = [{"target": "camera", "hint": "", "t0": 0.0, "t1": 4.0},
               {"target": "away", "hint": "TO YOUR LEFT", "t0": 4.0, "t1": 8.0},
               {"target": "notes", "hint": "", "t0": 8.0, "t1": 12.0}]
    t = np.arange(0, 12, 0.2) + 100.0
    # The first second of each prompt still looks where the last one did (left out);
    # one away reading is misclassed as notes, one camera reading has no face.
    rows = [AWAY] * 5 + [CAMERA] * 15 + [CAMERA] * 5 + [AWAY] * 14 + [NOTES] + [AWAY] * 5 + [NOTES] * 15
    found = [1] * 60
    found[10] = 0
    r = gaze.check_agreement(readings(rows, t=t, found=found), CAL, prompts, t0=100.0)
    assert r["confusion"]["camera"] == {"camera": 14, "notes": 0, "away": 0, "unclear": 1}
    assert r["confusion"]["away"] == {"camera": 0, "notes": 1, "away": 14, "unclear": 0}
    assert r["confusion"]["notes"] == {"camera": 0, "notes": 15, "away": 0, "unclear": 0}
    assert r["judged"] == 44 and r["agreement"] == pytest.approx(43 / 44, abs=1e-3)
    assert r["recall"]["away"] == pytest.approx(14 / 15, abs=1e-3) and 0.9 < r["kappa"] < 1.0
    assert r["medians"]["notes"]["yaw"] == pytest.approx(-8.0)
    # Screen (camera or notes) against away: one away reading was called notes.
    assert r["screen"]["recall"] == {"screen": 1.0, "away": pytest.approx(14 / 15, abs=1e-3)}
    assert r["screen"]["agreement"] == pytest.approx(43 / 44, abs=1e-3) and 0.9 < r["screen"]["kappa"] < 1.0


def test_kappa_is_zero_for_chance_agreement():
    pairs = [("camera", "camera"), ("camera", "notes"), ("notes", "camera"), ("notes", "notes")]
    assert gaze._kappa(pairs) == 0.0
