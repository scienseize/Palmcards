"""Per-frame face, pose and hand features for milestone 7, and the calibration.

Pure functions on landmark arrays (no MediaPipe), so they are tested with
synthetic points. palmcards.vision turns MediaPipe results into these rows
during calibration and takes; take-NN.face.npz keeps them:

  face_t      result times on the app clock (the frame's capture time)
  face_found  1 if a face was found, else 0 (the other face_* values NaN):
              such frames are unclear, never "looking away"
  face_yaw, face_pitch, face_roll
              head rotation from the face's transformation matrix, degrees
              (mirrored frame; only differences from the calibration mean anything)
  face_iris_x iris centre along the line between the eye corners, 0 at the
              corner on the frame's left, 1 at the other; both eyes averaged
  face_iris_y iris centre below (+) or above (-) that line, in eye widths
  face_eye_open
              lid gap / eye width (a blink is below BODY.closed_eye)
  face_box    (n, 4) face bounding box, pixels: x0, y0, x1, y1
  pose_t, pose_found
  pose_tilt   shoulder line angle, degrees, + when the shoulder on the
              frame's right is lower
  pose_width  shoulder width / frame width (nearer the camera: wider)
  pose_head   nose height above the shoulders' midpoint, in shoulder widths
              (a dropped head: smaller)
  pose_vis    lowest visibility of the nose and both shoulders, 0..1
  hand_t      each hand-tracking result (every frame the tracker answered)
  hand_n      hands in view
  hand_wrist_move, hand_tip_move
              how far the wrist, and the fingertip that moved most, went since
              the previous result, in palm sizes (largest over the hands; NaN
              if no hand was in both results)
  hand_tip_face
              nearest fingertip to the face box, in palm sizes, 0 inside it
              (NaN without a hand or a face seen in the last BODY.face_max_age_s)
  hand_tip_oval
              nearest fingertip to the face's outline (the face oval
              landmarks), in face widths, 0 inside it (version 2)
  hand_scale  that hand's palm size / the face's width (cheek to cheek): about
              0.6-0.7 when the hand is as far from the camera as the face,
              larger when it is nearer (gesturing in front of the face), so a
              hand over the face in the picture is not taken for a touch
              (version 2)
  hand_y      highest wrist, fraction of the frame height from the top
  provenance  JSON: how the rows were made (see vision.Watcher.provenance)

Frames are mirrored at capture, like everything else in PalmCards.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from palmcards.config import BODY

VERSION = 2  # bump when a feature's definition changes; 2: hand_tip_oval, hand_scale

# Face Landmarker indices: each eye's corners and lids, and the iris centres.
EYES = ((33, 133, 159, 145), (362, 263, 386, 374))
IRISES = (468, 473)
# The face's outline (MediaPipe's FACE_OVAL, in order around the face) and its cheek points.
FACE_OVAL = (10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176,
             149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109)
CHEEKS = (234, 454)
# Pose Landmarker indices.
NOSE, LEFT_SHOULDER, RIGHT_SHOULDER = 0, 11, 12
# Hand landmark indices (palmcards.gestures).
WRIST, TIPS = 0, (4, 8, 12, 16, 20)

FACE_KEYS = ("yaw", "pitch", "roll", "iris_x", "iris_y", "eye_open")
POSE_KEYS = ("tilt", "width", "head", "vis")
HAND_KEYS = ("n", "wrist_move", "tip_move", "tip_face", "y", "tip_oval", "scale")
NAN = float("nan")


# --- face ----------------------------------------------------------------------

def head_angles(matrix: np.ndarray) -> tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees from a 4x4 transformation matrix, as R = Ry(yaw) Rx(pitch) Rz(roll)."""
    r = np.asarray(matrix, dtype=np.float64)[:3, :3]
    r = r / np.linalg.norm(r, axis=0)  # drop any scale
    pitch = math.asin(max(-1.0, min(1.0, -r[1, 2])))
    yaw = math.atan2(r[0, 2], r[2, 2])
    roll = math.atan2(r[1, 0], r[1, 1])
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def face_row(points: np.ndarray, matrix: np.ndarray | None) -> dict:
    """Features of one face. `points`: (478, 2) landmarks in pixels."""
    pts = np.asarray(points, dtype=np.float64)
    yaw, pitch, roll = head_angles(matrix) if matrix is not None else (NAN, NAN, NAN)
    xs, ys, opens = [], [], []
    irises = [pts[i] for i in IRISES]
    for a, b, top, bottom in EYES:
        c0, c1 = sorted((pts[a], pts[b]), key=lambda p: p[0])  # frame-left corner first
        width = float(np.linalg.norm(c1 - c0))
        if width < 1e-6:
            continue
        mid = (c0 + c1) / 2
        iris = min(irises, key=lambda p: float(np.linalg.norm(p - mid)))  # the iris in this eye
        axis = (c1 - c0) / width
        rel = iris - c0
        xs.append(float(rel @ axis) / width)
        ys.append(float(rel[0] * -axis[1] + rel[1] * axis[0]) / width)  # along the normal, + downwards
        opens.append(float(np.linalg.norm(pts[top] - pts[bottom])) / width)
    mean = lambda v: float(np.mean(v)) if v else NAN  # noqa: E731
    return {"yaw": yaw, "pitch": pitch, "roll": roll, "iris_x": mean(xs), "iris_y": mean(ys),
            "eye_open": mean(opens), "box": (float(pts[:, 0].min()), float(pts[:, 1].min()),
                                             float(pts[:, 0].max()), float(pts[:, 1].max())),
            "oval": pts[list(FACE_OVAL)].astype(np.float32),
            "width": float(np.linalg.norm(pts[CHEEKS[0]] - pts[CHEEKS[1]]))}


# --- pose ----------------------------------------------------------------------

def pose_row(points: np.ndarray, visibility: np.ndarray, frame_w: float) -> dict:
    """Features of one body. `points`: (33, 2) landmarks in pixels; `visibility`: (33,)."""
    pts = np.asarray(points, dtype=np.float64)
    a, b = sorted((pts[LEFT_SHOULDER], pts[RIGHT_SHOULDER]), key=lambda p: p[0])
    span = float(np.linalg.norm(b - a))
    vis = float(min(visibility[NOSE], visibility[LEFT_SHOULDER], visibility[RIGHT_SHOULDER]))
    if span < 1e-6:
        return {"tilt": NAN, "width": NAN, "head": NAN, "vis": vis}
    mid = (a + b) / 2
    return {"tilt": math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])), "width": span / frame_w,
            "head": float(mid[1] - pts[NOSE][1]) / span, "vis": vis}


# --- hands ---------------------------------------------------------------------

def _palm(points: np.ndarray) -> float:
    return max(float(np.linalg.norm(points[0] - points[9])), 1e-6)


def box_distance(p: np.ndarray, box: tuple[float, float, float, float]) -> float:
    """Distance from a point to a box, 0 inside."""
    x0, y0, x1, y1 = box
    return math.hypot(max(x0 - p[0], 0.0, p[0] - x1), max(y0 - p[1], 0.0, p[1] - y1))


def oval_distance(p: np.ndarray, oval: np.ndarray) -> float:
    """Distance from a point to the face outline, 0 inside it."""
    import cv2

    return max(0.0, -cv2.pointPolygonTest(oval.reshape(-1, 1, 2), (float(p[0]), float(p[1])), True))


def hand_row(hands: list[np.ndarray], previous: list[np.ndarray], face_box, frame_h: float,
             face: dict | None = None) -> dict:
    """Features of one hand-tracking result. `hands`, `previous`: (21, 2) landmark arrays in pixels;
    `face`: the latest face row ("oval", "width"), or None."""
    wrist_move = tip_move = tip_face = tip_oval = scale = NAN
    for pts in hands:
        palm = _palm(pts)
        if previous:
            prev = min(previous, key=lambda q: float(np.linalg.norm(q[WRIST] - pts[WRIST])))
            if np.linalg.norm(prev[WRIST] - pts[WRIST]) / palm <= BODY.hand_match_palms:  # the same hand
                w = float(np.linalg.norm(pts[WRIST] - prev[WRIST])) / palm
                t = max(float(np.linalg.norm(pts[i] - prev[i])) for i in TIPS) / palm
                wrist_move = w if math.isnan(wrist_move) else max(wrist_move, w)
                tip_move = t if math.isnan(tip_move) else max(tip_move, t)
        if face_box is not None:
            d = min(box_distance(pts[i], face_box) for i in TIPS) / palm
            tip_face = d if math.isnan(tip_face) else min(tip_face, d)
        if face is not None and face["width"] > 1e-6:
            d = min(oval_distance(pts[i], face["oval"]) for i in TIPS) / face["width"]
            if math.isnan(tip_oval) or d < tip_oval:  # the hand nearest the face gives the scale
                tip_oval, scale = d, palm / face["width"]
    y = min(float(p[WRIST][1]) for p in hands) / frame_h if hands else NAN
    return {"n": len(hands), "wrist_move": wrist_move, "tip_move": tip_move, "tip_face": tip_face, "y": y,
            "tip_oval": tip_oval, "scale": scale}


# --- rows ----------------------------------------------------------------------

@dataclass
class Rows:
    """Feature rows as they arrive; arrays() for the file."""
    face: list[tuple] = field(default_factory=list)  # (t, found, *FACE_KEYS, *box)
    pose: list[tuple] = field(default_factory=list)  # (t, found, *POSE_KEYS)
    hand: list[tuple] = field(default_factory=list)  # (t, *HAND_KEYS)

    def add_face(self, t: float, row: dict | None) -> None:
        if row is None:
            self.face.append((t, 0) + (NAN,) * (len(FACE_KEYS) + 4))
        else:
            self.face.append((t, 1) + tuple(row[k] for k in FACE_KEYS) + tuple(row["box"]))

    def add_pose(self, t: float, row: dict | None) -> None:
        self.pose.append((t, 0) + (NAN,) * len(POSE_KEYS) if row is None else (t, 1) + tuple(row[k] for k in POSE_KEYS))

    def add_hand(self, t: float, row: dict) -> None:
        self.hand.append((t,) + tuple(row[k] for k in HAND_KEYS))

    def arrays(self) -> dict[str, np.ndarray]:
        def table(rows, width):
            return np.array(rows, dtype=np.float64).reshape(-1, width)

        face = table(self.face, 2 + len(FACE_KEYS) + 4)
        pose = table(self.pose, 2 + len(POSE_KEYS))
        hand = table(self.hand, 1 + len(HAND_KEYS))
        out = {"face_t": face[:, 0], "face_found": face[:, 1].astype(np.int8)}
        out.update({f"face_{k}": face[:, 2 + i].astype(np.float32) for i, k in enumerate(FACE_KEYS)})
        out["face_box"] = face[:, 2 + len(FACE_KEYS):].astype(np.float32)
        out.update({"pose_t": pose[:, 0], "pose_found": pose[:, 1].astype(np.int8)})
        out.update({f"pose_{k}": pose[:, 2 + i].astype(np.float32) for i, k in enumerate(POSE_KEYS)})
        out["hand_t"] = hand[:, 0]
        out["hand_n"] = hand[:, 1].astype(np.int8)
        out.update({f"hand_{k}": hand[:, 1 + i].astype(np.float32) for i, k in enumerate(HAND_KEYS) if k != "n"})
        return out


def save(path: Path, rows: Rows, provenance: dict) -> None:
    """take-NN.face.npz, published atomically (a crash leaves no half file under the real name)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(f, provenance=np.array(json.dumps(provenance)), **rows.arrays())
    tmp.replace(path)


def load(path: Path) -> tuple[dict[str, np.ndarray], dict]:
    """(arrays, provenance) from a take-NN.face.npz."""
    with np.load(path) as data:
        arrays = {k: data[k] for k in data.files if k != "provenance"}
        return arrays, json.loads(str(data["provenance"]))


# --- calibration ---------------------------------------------------------------

def _spread(values: list[float]) -> list[float] | None:
    """[median, median absolute deviation], or None without values."""
    v = np.array([x for x in values if not math.isnan(x)], dtype=np.float64)
    if not len(v):
        return None
    med = float(np.median(v))
    return [round(med, 4), round(float(np.median(np.abs(v - med))), 4)]


def calibrate(rows: Rows, t0: float, t_end: float) -> dict:
    """The calibration's baselines from the rows gathered during it: where the
    face and eyes point when looking at the camera (the first BODY.calib_camera_s
    after t0) and at the notes (the next BODY.calib_notes_s), and the posture
    over both. The first BODY.calib_settle_s of each step are left out (the
    eyes are still moving there), and so are blinks and frames without a face.

    status: "ok"; "failed" (not enough face frames in a step); or
    "incomplete" (the take started before both steps were over)."""
    steps = {"camera": (t0, t0 + BODY.calib_camera_s),
             "notes": (t0 + BODY.calib_camera_s, t0 + BODY.calib_camera_s + BODY.calib_notes_s)}
    out: dict = {"version": VERSION, "status": "ok", "reason": ""}
    for name, (a, b) in steps.items():
        picked = [r for r in rows.face if a + BODY.calib_settle_s <= r[0] < b and r[1]
                  and not (r[2 + FACE_KEYS.index("eye_open")] < BODY.closed_eye)]
        out[name] = {"n": len(picked), **{k: _spread([r[2 + FACE_KEYS.index(k)] for r in picked])
                                          for k in ("yaw", "pitch", "iris_x", "iris_y")}}
    poses = [r for r in rows.pose if t0 <= r[0] < steps["notes"][1] and r[1] and r[2 + POSE_KEYS.index("vis")]
             >= BODY.min_visibility]
    out["posture"] = {"n": len(poses), **{k: _spread([r[2 + POSE_KEYS.index(k)] for r in poses])
                                          for k in ("tilt", "width", "head")}}
    if t_end < steps["notes"][1]:
        out["status"], out["reason"] = "incomplete", "the take started before the calibration was over"
    else:
        short = [name for name in steps if out[name]["n"] < BODY.calib_min_frames]
        if short:
            out["status"] = "failed"
            out["reason"] = " and ".join(f"face seen in {out[s]['n']} frames looking at the {s}" for s in short) \
                + f" (needs {BODY.calib_min_frames})"
    return out
