"""Face and pose trackers (milestone 7): the frame schedule, skipping while
busy or late, results handed over once, and the watcher that turns them into
feature rows during the calibration and takes. A fake task stands in for
MediaPipe, so no model files are needed."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from palmcards import features
from palmcards.config import BODY
from palmcards.gestures import Hand
from palmcards.vision import FaceTracker, PoseTracker, Watcher
from tests.test_features import face_points, hand, rotation

FRAME = np.zeros((720, 1280, 3), np.uint8)


class FakeTask:
    def __init__(self, callback):
        self.callback = callback
        self.calls: list[int] = []
        self.images = []
        self.closed = False

    def detect_async(self, image, ms):
        self.calls.append(ms)
        self.images.append(image)

    def answer(self, result):
        self.callback(result, self.images[-1], self.calls[-1])

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def face(every=3, offset=1, clock=None):
    tasks = []

    def make(callback):
        tasks.append(FakeTask(callback))
        return tasks[-1]

    tracker = FaceTracker(every, offset, make_task=make, clock=clock or Clock())
    return tracker, tasks[0]


FOUND = SimpleNamespace(face_landmarks=[[object()]], facial_transformation_matrixes=[np.eye(4)])
EMPTY = SimpleNamespace(face_landmarks=[], facial_transformation_matrixes=[])


def test_runs_on_every_nth_frame_from_its_offset():
    tracker, task = face(every=3, offset=1)
    for i in range(10):
        if tracker.maybe_submit(FRAME, i / 30, i):
            task.answer(FOUND)
    assert [round(ms / 1000 * 30) for ms in task.calls] == [1, 4, 7]
    assert tracker.submitted == 3 and tracker.results == 3 and tracker.skipped == 0


def test_a_due_frame_is_skipped_while_busy_and_resumes_after_the_result():
    clock = Clock()
    tracker, task = face(every=1, offset=0, clock=clock)
    assert tracker.submit(FRAME, 0.0)
    assert not tracker.submit(FRAME, 0.033)  # no result yet
    assert tracker.skipped == 1
    task.answer(FOUND)
    assert tracker.submit(FRAME, 0.066)
    assert len(task.calls) == 2


def test_a_lost_result_frees_the_tracker_after_the_busy_timeout():
    clock = Clock()
    tracker, task = face(every=1, offset=0, clock=clock)
    tracker.submit(FRAME, 0.0)
    clock.t = 0.4
    assert not tracker.submit(FRAME, 0.4)
    clock.t = 0.6
    assert tracker.submit(FRAME, 0.6)


def test_timestamps_strictly_increase_even_for_repeated_times():
    tracker, task = face(every=1, offset=0)
    for _ in range(3):
        tracker.submit(FRAME, 1.0)
        task.answer(FOUND)
    assert task.calls == [1000, 1001, 1002]


def test_poll_hands_each_result_over_once_and_counts_what_was_found():
    tracker, task = face(every=1, offset=0)
    tracker.submit(FRAME, 0.5)
    task.answer(EMPTY)
    result, t = tracker.poll()
    assert result is EMPTY and t == 0.5
    assert tracker.poll() is None
    tracker.submit(FRAME, 0.6)
    task.answer(FOUND)
    assert tracker.results == 2 and tracker.found == 1


def test_frames_are_downscaled_before_the_model():
    tracker, task = face(every=1, offset=0)
    tracker.submit(FRAME, 0.0)
    assert task.images[0].width == 640 and task.images[0].height == 360


def test_a_late_frame_puts_the_run_off_to_the_next_frame():
    tracker, task = face(every=3, offset=0)
    submitted = []
    for i in range(10):
        if tracker.maybe_submit(FRAME, i / 30, i, late=i in (3, 4)):
            submitted.append(i)
            task.answer(FOUND)
    assert submitted == [0, 5, 8] and tracker.deferred == 2


def test_pose_counts_a_body_and_closes_its_task():
    made = []
    tracker = PoseTracker(5, 2, make_task=lambda cb: made.append(FakeTask(cb)) or made[-1])
    task = made[0]
    assert not tracker.due(1) and tracker.due(2)
    tracker.submit(FRAME, 0.0)
    task.answer(SimpleNamespace(pose_landmarks=[[object()]]))
    assert tracker.found == 1
    tracker.close()
    assert task.closed


# --- the watcher: calibration and takes --------------------------------------------------


class AnsweringTask(FakeTask):
    """Answers every frame at once, with what `answer_with` says."""

    def __init__(self, callback, answer_with):
        super().__init__(callback)
        self.answer_with = answer_with

    def detect_async(self, image, ms):
        super().detect_async(image, ms)
        self.callback(self.answer_with(), image, ms)


def face_result(w=1280, h=720, **kw):
    pts = face_points(**kw)
    return SimpleNamespace(face_landmarks=[[SimpleNamespace(x=x / w, y=y / h) for x, y in pts]],
                           facial_transformation_matrixes=[rotation()])


def pose_result():
    lms = [SimpleNamespace(x=0.5, y=0.7, visibility=0.9) for _ in range(33)]
    lms[features.LEFT_SHOULDER] = SimpleNamespace(x=0.4, y=0.7, visibility=0.9)
    lms[features.RIGHT_SHOULDER] = SimpleNamespace(x=0.6, y=0.7, visibility=0.9)
    lms[features.NOSE] = SimpleNamespace(x=0.5, y=0.45, visibility=0.9)
    return SimpleNamespace(pose_landmarks=[lms])


def watcher(face_answer=face_result, pose_answer=pose_result):
    face = FaceTracker(make_task=lambda cb: AnsweringTask(cb, face_answer))
    pose = PoseTracker(make_task=lambda cb: AnsweringTask(cb, pose_answer))
    return Watcher(face, pose, {"face_landmarker.task": "abc"})


def run_frames(w, t0, seconds, index0=0, fps=30):
    for i in range(round(seconds * fps)):
        w.frame(FRAME, t0 + i / fps, index0 + i)
    return index0 + round(seconds * fps)


def test_nothing_is_submitted_outside_a_calibration_or_take():
    w = watcher()
    run_frames(w, 0.0, 1.0)
    assert w.face.submitted == 0 and w.pose.submitted == 0


def test_a_calibration_runs_faster_then_gives_its_summary():
    w = watcher()
    w.begin_calibration(5.0)
    assert w.face.every == BODY.calib_face_every
    run_frames(w, 5.0, 0.1)
    assert w.phase(5.1) == "camera" and w.phase(5.0 + BODY.calib_camera_s + 0.1) == "notes"
    run_frames(w, 5.1, BODY.calib_camera_s + BODY.calib_notes_s - 0.1, index0=3)
    out = w.finish_calibration(5.0 + BODY.calib_camera_s + BODY.calib_notes_s)
    assert out["status"] == "ok", out
    assert out["camera"]["n"] >= BODY.calib_min_frames and out["posture"]["n"] > 0
    assert out["frame_size"] == [1280, 720] and out["dot"][0] == 640 and w.state == "idle"
    assert w.phase(6.0) is None


def test_a_take_records_face_pose_and_hands_at_the_take_rates(tmp_path):
    w = watcher()
    w.begin_take(20.0)
    assert (w.face.every, w.pose.every) == (BODY.face_every, BODY.pose_every)
    for i in range(60):
        t = 20.0 + i / 30
        w.frame(FRAME, t, 100 + i)
        w.hands([Hand(hand(900 + i, 600))], t)
    rows, counts = w.end_take()
    assert len(rows.face) == 60 // BODY.face_every and len(rows.pose) == 60 // BODY.pose_every
    assert len(rows.hand) == 60 and counts["hands"]["results"] == 60
    assert counts["face"]["results"] == len(rows.face) and counts["face"]["found"] == len(rows.face)
    path = tmp_path / "take-01.face.npz"
    features.save(path, rows, w.provenance(take=1))
    arrays, provenance = features.load(path)
    assert provenance["models"] == {"face_landmarker.task": "abc"} and provenance["frame_size"] == [1280, 720]
    assert arrays["hand_wrist_move"][1] == pytest.approx(1 / 50)  # 1 px a frame, 50 px palms
    assert arrays["hand_tip_face"][0] == pytest.approx(math.hypot(900 - 720, 500 - 470) / 50)


def test_frames_without_a_face_are_kept_as_unclear_rows():
    w = watcher(face_answer=lambda: SimpleNamespace(face_landmarks=[], facial_transformation_matrixes=[]))
    w.begin_take(0.0)
    run_frames(w, 0.0, 1.0)
    rows, counts = w.end_take()
    assert rows.face and all(r[1] == 0 for r in rows.face) and counts["face"]["found"] == 0


def test_hands_are_only_recorded_during_takes():
    w = watcher()
    w.begin_calibration(0.0)
    run_frames(w, 0.0, 0.2)
    w.hands([Hand(hand(900, 600))], 0.1)
    assert w.rows.hand == []
