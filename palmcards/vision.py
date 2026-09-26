"""Face and pose landmarkers for milestone 7's gaze and posture, at a reduced rate.

    FaceTracker()   MediaPipe Face Landmarker: one face, 478 landmarks (iris
                    included) and the head's transformation matrix
    PoseTracker()   MediaPipe Pose Landmarker (lite): one body, 33 landmarks

Both run in LIVE_STREAM mode like the hand tracker (palmcards.gestures): a
frame is only submitted on every Nth camera frame (BODY.face_every /
pose_every, from scripts/bench_vision.py), and skipped while the previous
one is still being worked on, so neither ever blocks the display loop.
They run only during calibration and takes, never in Prepare or Review.

Results are MediaPipe's own, handed over once by poll(); turning them into
features is the caller's business.

Make each tracker once and keep it: MediaPipe keeps 16 threads and about 14 MB
of every closed face + pose pair, so ones made per take would pile up. An idle
tracker (nothing submitted) costs no CPU.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from palmcards.config import BODY

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


class LandmarkTask:
    """The LIVE_STREAM plumbing shared by the face and pose trackers.

    `make_task(callback)` returns an object with detect_async(image, ms) and
    close(); the callback is called as callback(result, image, ms) when a
    result is ready (on a MediaPipe thread).
    """

    def __init__(self, make_task: Callable[[Callable], Any], every: int, offset: int = 0,
                 max_side: int = BODY.max_side, busy_timeout_s: float = BODY.busy_timeout_s,
                 clock: Callable[[], float] = time.perf_counter):
        self.every, self.offset = max(1, every), offset
        self.max_side = max_side
        self.busy_timeout_s = busy_timeout_s
        self.clock = clock
        self.latency_ms = 0.0  # submit -> result, smoothed
        self.submitted = 0  # frames handed to the model
        self.skipped = 0  # frames due on the schedule but dropped because the model was busy
        self.results = 0  # results delivered
        self.found = 0  # results with a face / body in them
        self._last_ms = -1
        self._lock = threading.Lock()
        self._busy = False
        self._busy_since = 0.0
        self._sent: dict[int, float] = {}
        self._result: tuple[Any, float] | None = None
        self._task = make_task(self._on_result)

    def due(self, index: int) -> bool:
        """Is camera frame `index` one this tracker runs on?"""
        return (index - self.offset) % self.every == 0

    def maybe_submit(self, frame_bgr: np.ndarray, t: float, index: int) -> bool:
        """Submit frame `index` if it is due; False if not due or skipped as busy."""
        return self.due(index) and self.submit(frame_bgr, t)

    def submit(self, frame_bgr: np.ndarray, t: float) -> bool:
        """Queue a frame (mirrored BGR, capture time `t` in s); False if skipped because busy."""
        now = self.clock()
        with self._lock:
            # A result normally clears _busy; the timeout guards against a
            # dropped callback stopping the tracker for good.
            if self._busy and now - self._busy_since < self.busy_timeout_s:
                self.skipped += 1
                return False
            self._busy, self._busy_since = True, now
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        small = cv2.resize(frame_bgr, None, fx=scale, fy=scale) if scale < 1 else frame_bgr
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        ms = max(int(t * 1000), self._last_ms + 1)  # timestamps must strictly increase
        self._last_ms = ms
        self._sent[ms] = now
        self.submitted += 1
        self._task.detect_async(self._image(rgb), ms)
        return True

    @staticmethod
    def _image(rgb: np.ndarray):
        import mediapipe as mp

        return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    def _found(self, result) -> bool:
        raise NotImplementedError

    def _on_result(self, result, _image, ms: int) -> None:
        sent = self._sent.pop(ms, None)
        found = self._found(result)
        with self._lock:
            if sent is not None:
                self.latency_ms = 0.9 * self.latency_ms + 0.1 * (self.clock() - sent) * 1000
            self.results += 1
            self.found += found
            self._result = (result, ms / 1000)
            self._busy = False

    def poll(self) -> tuple[Any, float] | None:
        """Newest (result, capture time in s) since the last poll, else None."""
        with self._lock:
            result, self._result = self._result, None
        return result

    def close(self) -> None:
        self._task.close()


def _model(name: str) -> str:
    path = MODELS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run python scripts/download_models.py --all")
    return str(path)


class FaceTracker(LandmarkTask):
    def __init__(self, every: int = BODY.face_every, offset: int = BODY.face_offset, **kw):
        def make(callback):
            from mediapipe.tasks.python import BaseOptions, vision

            return vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=_model("face_landmarker.task")),
                running_mode=vision.RunningMode.LIVE_STREAM, result_callback=callback, num_faces=1,
                min_face_detection_confidence=BODY.min_detection, min_face_presence_confidence=BODY.min_presence,
                min_tracking_confidence=BODY.min_tracking,
                output_face_blendshapes=False, output_facial_transformation_matrixes=True))

        super().__init__(kw.pop("make_task", make), every, offset, **kw)

    def _found(self, result) -> bool:
        return bool(result.face_landmarks)


class PoseTracker(LandmarkTask):
    def __init__(self, every: int = BODY.pose_every, offset: int = BODY.pose_offset, **kw):
        def make(callback):
            from mediapipe.tasks.python import BaseOptions, vision

            return vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=_model("pose_landmarker_lite.task")),
                running_mode=vision.RunningMode.LIVE_STREAM, result_callback=callback, num_poses=1,
                min_pose_detection_confidence=BODY.min_detection, min_pose_presence_confidence=BODY.min_presence,
                min_tracking_confidence=BODY.min_tracking, output_segmentation_masks=False))

        super().__init__(kw.pop("make_task", make), every, offset, **kw)

    def _found(self, result) -> bool:
        return bool(result.pose_landmarks)
