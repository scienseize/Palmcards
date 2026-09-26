"""The eye calibration inside the count-in and the face/pose/hand features of
a take, through the app with fake devices (milestone 7, stage 1)."""

import json
from dataclasses import replace

import pytest

import main
import palmcards.features
import palmcards.gaze
import palmcards.vision
from palmcards import features
from palmcards.config import BODY, GAZE
from tests.test_app_lifecycle import Rig
from tests.test_evaluate import evaluate_module
from tests.test_vision import watcher

# A short calibration, face on every frame: the scripted count-in lasts 78 frames of at least 10 ms.
QUICK = replace(BODY, calib_camera_s=0.2, calib_notes_s=0.2, calib_settle_s=0.0, calib_min_frames=1,
                calib_face_every=1)
TWO_TAKES = {2: ("count_in", "count_in"), 80: ("take_start", "rehearse"), 100: ("take_stop", "review"),
             110: ("count_in", "count_in"), 190: ("take_start", "rehearse")}
QUIT = 210


@pytest.fixture
def quick(monkeypatch):
    for module in (main, palmcards.features, palmcards.vision):
        monkeypatch.setattr(module, "BODY", QUICK)


def log_kinds(rig):
    return [json.loads(line)["kind"] for line in (rig.tmp / "log.jsonl").read_text().splitlines()]


def test_the_first_count_in_calibrates_and_the_take_keeps_its_features(tmp_path, monkeypatch, quick):
    rig = Rig(tmp_path, monkeypatch, script=TWO_TAKES, keys={QUIT: ord("q")}, vision=watcher)
    assert rig.run() == 0
    session = rig.session()
    (cal,) = session.calibrations  # once per session: the second count-in doesn't calibrate
    assert cal["id"] == "c1" and cal["status"] == "ok", cal
    assert cal["camera"]["n"] >= 1 and cal["notes"]["n"] >= 1 and cal["frame_size"] == [640, 360]
    for number in (1, 2):
        take = session.take(number)
        assert take.vision["state"] == "recorded" and take.vision["calibration"] == "c1"
        arrays, provenance = features.load(session.dir / take.vision["file"])
        assert provenance["take"] == number and provenance["calibration"] == "c1"
        assert len(arrays["face_t"]) > 0 and arrays["face_found"].all()
        # Every row is inside the take, on the app clock.
        assert arrays["face_t"].min() >= take.t_start - 0.5
    assert log_kinds(rig).count("calibration") == 1


def test_e_calibrates_again_at_the_next_count_in(tmp_path, monkeypatch, quick):
    rig = Rig(tmp_path, monkeypatch, script=TWO_TAKES, keys={105: ord("e"), QUIT: ord("q")}, vision=watcher)
    assert rig.run() == 0
    session = rig.session()
    assert [c["id"] for c in session.calibrations] == ["c1", "c2"]
    assert session.take(2).vision["calibration"] == "c2"


def test_a_calibration_without_a_face_fails_and_runs_again(tmp_path, monkeypatch, quick):
    from types import SimpleNamespace

    no_face = lambda: watcher(face_answer=lambda: SimpleNamespace(face_landmarks=[],  # noqa: E731
                                                                  facial_transformation_matrixes=[]))
    rig = Rig(tmp_path, monkeypatch, script=TWO_TAKES, keys={QUIT: ord("q")}, vision=no_face)
    assert rig.run() == 0
    session = rig.session()
    assert [c["status"] for c in session.calibrations] == ["failed", "failed"]
    assert "face seen in 0 frames" in session.calibrations[0]["reason"]
    assert session.calibration is None
    take = session.take(1)
    assert take.vision["state"] == "recorded" and take.vision["calibration"] is None
    assert take.vision["counts"]["face"]["found"] == 0


def test_a_take_without_face_tracking_says_so(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, keys={30: ord("q")})
    assert rig.run() == 0
    session = rig.session()
    assert session.take(1).vision == {"state": "off", "reason": "face and pose tracking not opened"}
    assert session.calibrations == []


def test_a_face_model_that_fails_to_load_leaves_the_takes_working(tmp_path, monkeypatch):
    def broken():
        raise FileNotFoundError("models/face_landmarker.task missing")

    rig = Rig(tmp_path, monkeypatch, keys={30: ord("q")}, vision=broken)
    assert rig.run() == 0
    take = rig.session().take(1)
    assert take.status == "saved" and take.vision["state"] == "off" and "missing" in take.vision["reason"]


def test_a_gaze_check_take_prompts_then_stops_itself(tmp_path, monkeypatch, quick):
    monkeypatch.setattr(palmcards.gaze, "GAZE", replace(GAZE, check_each=1, check_step_s=0.15, check_settle_s=0.0))
    evaluate = evaluate_module()
    rig = Rig(tmp_path, monkeypatch, script={2: ("count_in", "count_in"), 80: ("take_start", "rehearse")},
              keys={160: ord("q")}, vision=watcher, gaze_check=True)
    assert rig.run() == 0
    session = rig.session()
    take = session.take(1)
    assert take.status == "saved" and take.duration_s < 1.0  # it stopped after its 3 prompts, not at quitting
    prompts = take.gaze_check["prompts"]
    assert sorted(p["target"] for p in prompts) == ["away", "camera", "notes"]
    assert "gaze_check" in log_kinds(rig)
    r = evaluate.gaze_check(session.dir)
    assert r["take"] == 1 and r["calibration"] == "c1" and r["readings"] > 0
    # The fake face never moves, so its calibration can't tell camera from notes: all unclear, and said so.
    assert "can't tell camera from notes" in r["calibration_usable"] and r["judged"] == 0
    assert "agreement None" in evaluate.gaze_report(r)
    sweep = evaluate.gaze_sweep(session.dir)
    assert len(sweep) == 8 and all(row["kappa"] is None and not row["usable"] for row in sweep)
    assert palmcards.gaze.GAZE.check_step_s == 0.15  # the sweep puts the settings back
