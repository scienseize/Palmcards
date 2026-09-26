"""Face and pose landmarkers for milestone 7's gaze and posture, at a reduced rate.

    FaceTracker()   MediaPipe Face Landmarker: one face, 478 landmarks (iris
                    included) and the head's transformation matrix
    PoseTracker()   MediaPipe Pose Landmarker (lite): one body, 33 landmarks

    Watcher         runs both during the calibration and takes, and turns
                    their results (and the hand tracker's) into the feature
                    rows of palmcards.features

Both run in LIVE_STREAM mode like the hand tracker (palmcards.gestures): a
frame is submitted once at least N camera frames have passed since the last
one (BODY.face_every / pose_every, from scripts/bench_vision.py), never on a
frame that is already late (BODY.late_ms: it waits for the next one), and
not while the previous frame is still being worked on, so neither ever
blocks the display loop. They run only during calibration and takes, never
in Prepare or Review.

Make each tracker once and keep it: MediaPipe keeps 16 threads and about 14 MB
of every closed face + pose pair, so ones made per take would pile up. An idle
tracker (nothing submitted) costs no CPU.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from palmcards import features
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
        self.deferred = 0  # due frames put off because the frame was late
        self.results = 0  # results delivered
        self.found = 0  # results with a face / body in them
        self._last_ms = -1
        self._last_index: int | None = None  # the last frame index submitted
        self._lock = threading.Lock()
        self._busy = False
        self._busy_since = 0.0
        self._sent: dict[int, float] = {}
        self._result: tuple[Any, float] | None = None
        import mediapipe  # here, not at the first frame: the first import takes most of a second

        self._mp = mediapipe
        self._task = make_task(self._on_result)

    def due(self, index: int) -> bool:
        """Is camera frame `index` one this tracker should run on? The first
        from `offset` on, then each `every` frames after the last one submitted."""
        if self._last_index is None:
            return index >= self.offset
        return index - self._last_index >= self.every

    def maybe_submit(self, frame_bgr: np.ndarray, t: float, index: int, late: bool = False) -> bool:
        """Submit frame `index` if it is due and the frame isn't late; False if
        not due, put off (late) or skipped as busy (both retried next frame)."""
        if not self.due(index):
            return False
        if late:
            self.deferred += 1
            return False
        if not self.submit(frame_bgr, t):
            return False
        self._last_index = index
        return True

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

    def _image(self, rgb: np.ndarray):
        return self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)

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


class Watcher:
    """Face, pose and hand features during the calibration and takes.

    begin_calibration(t) .. finish_calibration(t) -> the calibration's summary
    begin_take(t) .. end_take() -> the take's feature rows
    frame(frame, t, index, late) and hands(hands, t) every frame; they do
    nothing outside a calibration or take. Results are turned into rows on
    the caller's thread when they are polled: a few landmarks each, cheap.
    """

    def __init__(self, face: LandmarkTask, pose: LandmarkTask, models: dict[str, str] | None = None):
        self.face, self.pose = face, pose
        self.models = models or {}  # model file -> sha256, for the provenance
        self.state = "idle"  # idle | calibrating | take
        self.t0 = 0.0  # when the calibration or take began
        self.rows = features.Rows()
        self.size: tuple[int, int] | None = None  # frame size, pixels
        self._face_box: tuple[float, tuple] | None = None  # (t, box) of the last face seen
        self._previous_hands: list[np.ndarray] = []
        self._counts0: dict = {}

    @classmethod
    def open(cls) -> "Watcher":
        """Both trackers; FileNotFoundError if a model is missing."""
        face = FaceTracker()
        try:
            pose = PoseTracker()
        except BaseException:
            face.close()
            raise
        models = {name: _sha256(MODELS_DIR / name) for name in ("face_landmarker.task", "pose_landmarker_lite.task")}
        return cls(face, pose, models)

    # --- calibration and takes ------------------------------------------------

    def _begin(self, state: str, t: float, face_every: int, pose_every: int) -> None:
        self.state, self.t0, self.rows = state, t, features.Rows()
        self.face.every, self.pose.every = face_every, pose_every
        self._face_box, self._previous_hands = None, []
        self._counts0 = self._counts()

    def begin_calibration(self, t: float) -> None:
        self._begin("calibrating", t, BODY.calib_face_every, BODY.calib_pose_every)

    def phase(self, t: float) -> str | None:
        """The calibration step at `t`: "camera", "notes", or None."""
        if self.state != "calibrating":
            return None
        if t - self.t0 < BODY.calib_camera_s:
            return "camera"
        return "notes" if t - self.t0 < BODY.calib_camera_s + BODY.calib_notes_s else None

    def finish_calibration(self, t: float) -> dict:
        """The calibration's summary (palmcards.features.calibrate), with where it was made."""
        self._drain()
        out = features.calibrate(self.rows, self.t0, t)
        out.update(t=round(self.t0, 3), frame_size=list(self.size) if self.size else None,
                   counts=self._counts_since())
        self.state = "idle"
        return out

    def cancel(self) -> None:
        self.state = "idle"

    def begin_take(self, t: float) -> None:
        self._begin("take", t, BODY.face_every, BODY.pose_every)

    def end_take(self) -> tuple[features.Rows, dict]:
        """The take's rows and counts (results, found, put off, skipped)."""
        self._drain()
        self.state = "idle"
        return self.rows, self._counts_since()

    def provenance(self, **extra) -> dict:
        try:
            mp_version = version("mediapipe")
        except PackageNotFoundError:
            mp_version = None
        return {"features": features.VERSION, "mediapipe": mp_version, "models": self.models,
                "body": asdict(BODY), "frame_size": list(self.size) if self.size else None, "mirrored": True,
                "clock": "app", **extra}

    # --- every frame --------------------------------------------------------

    def frame(self, frame_bgr: np.ndarray, t: float, index: int, late: bool = False) -> None:
        if self.state == "idle":
            return
        h, w = frame_bgr.shape[:2]
        self.size = (w, h)
        self.face.maybe_submit(frame_bgr, t, index, late)
        self.pose.maybe_submit(frame_bgr, t, index, late)
        self._drain()

    def hands(self, hands: list, t: float) -> None:
        """A hand-tracking result (palmcards.gestures.Hand list); recorded during takes."""
        if self.state != "take" or self.size is None or t < self.t0:
            return
        pts = [np.asarray(h.points, dtype=np.float64) for h in hands]
        box = self._face_box[1] if self._face_box and t - self._face_box[0] <= BODY.face_box_max_age_s else None
        self.rows.add_hand(t, features.hand_row(pts, self._previous_hands, box, self.size[1]))
        self._previous_hands = pts

    def _drain(self) -> None:
        if self.size is None:
            return
        w, h = self.size
        # A result for a frame from before this calibration or take began (it was
        # still being worked on) belongs to neither.
        if (got := self.face.poll()) is not None and got[1] >= self.t0:
            result, t = got
            row = None
            if result.face_landmarks:
                pts = np.array([(p.x * w, p.y * h) for p in result.face_landmarks[0]], dtype=np.float64)
                matrices = getattr(result, "facial_transformation_matrixes", None)
                row = features.face_row(pts, np.asarray(matrices[0]) if matrices else None)
                self._face_box = (t, row["box"])
            self.rows.add_face(t, row)
        if (got := self.pose.poll()) is not None and got[1] >= self.t0:
            result, t = got
            row = None
            if result.pose_landmarks:
                lms = result.pose_landmarks[0]
                pts = np.array([(p.x * w, p.y * h) for p in lms], dtype=np.float64)
                vis = np.array([getattr(p, "visibility", None) or 0.0 for p in lms], dtype=np.float64)
                row = features.pose_row(pts, vis, w)
            self.rows.add_pose(t, row)

    def _counts(self) -> dict:
        return {k: {a: getattr(tr, a) for a in ("results", "found", "deferred", "skipped")}
                for k, tr in (("face", self.face), ("pose", self.pose))}

    def _counts_since(self) -> dict:
        now = self._counts()
        out = {k: {a: now[k][a] - self._counts0.get(k, {}).get(a, 0) for a in now[k]} for k in now}
        out["hands"] = {"results": len(self.rows.hand)}
        return out

    def close(self) -> None:
        try:
            self.face.close()
        finally:
            self.pose.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()
