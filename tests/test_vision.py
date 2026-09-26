"""Face and pose trackers (milestone 7): the frame schedule, skipping while
busy, and results handed over once. A fake task stands in for MediaPipe, so
no model files are needed."""

from types import SimpleNamespace

import numpy as np

from palmcards.vision import FaceTracker, PoseTracker

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


def test_pose_counts_a_body_and_closes_its_task():
    made = []
    tracker = PoseTracker(5, 2, make_task=lambda cb: made.append(FakeTask(cb)) or made[-1])
    task = made[0]
    assert [i for i in range(12) if tracker.due(i)] == [2, 7]
    tracker.submit(FRAME, 0.0)
    task.answer(SimpleNamespace(pose_landmarks=[[object()]]))
    assert tracker.found == 1
    tracker.close()
    assert task.closed
