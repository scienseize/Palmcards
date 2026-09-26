"""Milestone 7 features on synthetic landmarks: head angles, iris position,
shoulders, hand movement and face distance, the calibration summary, and
the take-NN.face.npz file."""

import math

import numpy as np
import pytest

from palmcards import features
from palmcards.config import BODY
from palmcards.features import Rows, calibrate, face_row, hand_row, head_angles, pose_row


def rotation(yaw=0.0, pitch=0.0, roll=0.0):
    a, b, c = (math.radians(v) for v in (yaw, pitch, roll))
    ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    m = np.eye(4)
    m[:3, :3] = 2.0 * (ry @ rx @ rz)  # a scale, as the matrices may carry one
    return m


def face_points(iris_dx=0.0, iris_dy=0.0, open_=0.3):
    """478 points in pixels: two 60 px wide eyes, irises shifted by a fraction of the eye width."""
    pts = np.tile([640.0, 360.0], (478, 1))
    pts[0], pts[1] = (560, 250), (720, 470)  # the face's extent
    for (a, b, top, bottom), cx, iris in zip(features.EYES, (600, 680), features.IRISES):
        pts[a], pts[b] = (cx - 30, 330), (cx + 30, 330)
        pts[top], pts[bottom] = (cx, 330 - 30 * open_), (cx, 330 + 30 * open_)
        pts[iris] = (cx + 60 * iris_dx, 330 + 60 * iris_dy)
    return pts


def test_head_angles_come_back_from_the_matrix():
    yaw, pitch, roll = head_angles(rotation(yaw=20, pitch=-10, roll=5))
    assert (yaw, pitch, roll) == pytest.approx((20, -10, 5), abs=1e-6)


def test_iris_position_is_measured_between_the_eye_corners():
    centred = face_row(face_points(), rotation())
    assert centred["iris_x"] == pytest.approx(0.5) and centred["iris_y"] == pytest.approx(0.0)
    right_down = face_row(face_points(iris_dx=0.1, iris_dy=0.05), None)
    assert right_down["iris_x"] == pytest.approx(0.6) and right_down["iris_y"] == pytest.approx(0.05)
    assert math.isnan(right_down["yaw"])  # no matrix, no head angles
    assert centred["eye_open"] == pytest.approx(0.3)  # an 18 px lid gap over a 60 px eye
    assert centred["box"] == (560, 250, 720, 470)


def test_shoulder_tilt_width_and_head_height():
    pts = np.zeros((33, 2))
    pts[features.LEFT_SHOULDER], pts[features.RIGHT_SHOULDER] = (500, 500), (700, 520)  # frame-right one lower
    pts[features.NOSE] = (600, 310)
    row = pose_row(pts, np.full(33, 0.9), 1280)
    assert row["tilt"] == pytest.approx(math.degrees(math.atan2(20, 200)))
    assert row["width"] == pytest.approx(math.hypot(200, 20) / 1280)
    assert row["head"] == pytest.approx(200 / math.hypot(200, 20))
    swapped = pts.copy()  # the order of the landmarks doesn't matter, only where they are
    swapped[[features.LEFT_SHOULDER, features.RIGHT_SHOULDER]] = pts[[features.RIGHT_SHOULDER, features.LEFT_SHOULDER]]
    assert pose_row(swapped, np.full(33, 0.9), 1280)["tilt"] == pytest.approx(row["tilt"])


def hand(x, y, palm=50.0):
    pts = np.zeros((21, 2))
    pts[:] = (x, y)
    pts[9] = (x, y - palm)
    for i in features.TIPS:
        pts[i] = (x, y - 2 * palm)
    return pts


def test_hand_movement_in_palms_and_distance_to_the_face():
    before, after = hand(900, 600), hand(950, 600)
    row = hand_row([after], [before], face_box=(560, 250, 720, 470), frame_h=720)
    assert row["n"] == 1 and row["wrist_move"] == pytest.approx(1.0) and row["tip_move"] == pytest.approx(1.0)
    assert row["tip_face"] == pytest.approx(math.hypot(950 - 720, 500 - 470) / 50)
    assert row["y"] == pytest.approx(600 / 720)
    touching = hand_row([hand(640, 450)], [], face_box=(560, 250, 720, 470), frame_h=720)
    assert touching["tip_face"] == 0.0 and math.isnan(touching["wrist_move"])  # no previous hand
    empty = hand_row([], [before], face_box=None, frame_h=720)
    assert empty["n"] == 0 and math.isnan(empty["y"]) and math.isnan(empty["tip_face"])


def test_a_far_away_hand_is_not_the_same_hand():
    row = hand_row([hand(200, 600)], [hand(1000, 600)], None, 720)
    assert math.isnan(row["wrist_move"])


END = 10.0 + BODY.calib_camera_s + BODY.calib_notes_s  # the calibration's end, from t0 = 10


def calibration_rows(t0=10.0, camera_frames=15, notes_frames=15, blink_at=None):
    rows = Rows()
    step = BODY.calib_camera_s / camera_frames
    for i in range(camera_frames):
        t = t0 + i * step
        row = face_row(face_points(open_=0.05 if blink_at == i else 0.3), rotation(yaw=0))
        rows.add_face(t, row)
    step = BODY.calib_notes_s / notes_frames
    for i in range(notes_frames):
        t = t0 + BODY.calib_camera_s + i * step
        rows.add_face(t, face_row(face_points(iris_dy=0.1), rotation(pitch=-15)))
    pts = np.zeros((33, 2))
    pts[features.LEFT_SHOULDER], pts[features.RIGHT_SHOULDER], pts[features.NOSE] = (500, 500), (700, 500), (600, 300)
    for i in range(6):
        rows.add_pose(t0 + i * 0.6, pose_row(pts, np.full(33, 0.9), 1280))
    rows.add_face(t0 + 0.9, None)  # a frame without a face: left out
    return rows


def test_calibration_gives_both_baselines_and_the_posture():
    out = calibrate(calibration_rows(), 10.0, 10.0 + BODY.calib_camera_s + BODY.calib_notes_s)
    assert out["status"] == "ok", out
    settle = BODY.calib_settle_s / (BODY.calib_camera_s / 15)
    assert out["camera"]["n"] == 15 - math.ceil(settle - 1e-9)  # the settling frames are left out
    assert out["camera"]["pitch"][0] == pytest.approx(0.0, abs=1e-6)
    assert out["notes"]["pitch"][0] == pytest.approx(-15.0) and out["notes"]["iris_y"][0] == pytest.approx(0.1)
    assert out["posture"]["n"] == 6 and out["posture"]["tilt"] == [0.0, 0.0]
    assert out["posture"]["head"][0] == pytest.approx(1.0)


def test_blinks_are_left_out_of_the_calibration():
    with_blink = calibrate(calibration_rows(blink_at=10), 10.0, END)
    assert with_blink["camera"]["n"] == calibrate(calibration_rows(), 10.0, END)["camera"]["n"] - 1


def test_a_calibration_without_enough_face_fails_and_says_why():
    out = calibrate(calibration_rows(camera_frames=4), 10.0, END)
    assert out["status"] == "failed" and "looking at the camera" in out["reason"]


def test_a_calibration_cut_short_is_incomplete():
    out = calibrate(calibration_rows(), 10.0, 12.5)
    assert out["status"] == "incomplete"


def test_the_feature_file_round_trips_with_its_provenance(tmp_path):
    rows = Rows()
    rows.add_face(1.0, face_row(face_points(), rotation(yaw=3)))
    rows.add_face(1.2, None)
    rows.add_hand(1.1, hand_row([hand(900, 600)], [], None, 720))
    path = tmp_path / "take-01.face.npz"
    features.save(path, rows, {"features": features.VERSION, "take": 1})
    arrays, provenance = features.load(path)
    assert provenance == {"features": features.VERSION, "take": 1}
    assert arrays["face_t"].tolist() == [1.0, 1.2] and arrays["face_found"].tolist() == [1, 0]
    assert arrays["face_yaw"][0] == pytest.approx(3.0, abs=1e-4) and math.isnan(arrays["face_yaw"][1])
    assert arrays["face_box"].shape == (2, 4) and len(arrays["pose_t"]) == 0
    assert arrays["hand_n"].tolist() == [1] and not list(tmp_path.glob("*.tmp"))


def test_fingertips_against_the_face_outline_and_the_hand_scale():
    face = {"oval": np.array([(560, 250), (720, 250), (720, 470), (560, 470)], np.float32), "width": 160.0}
    inside = hand_row([hand(640, 500)], [], face_box=None, frame_h=720, face=face)  # fingertips at y 400
    assert inside["tip_oval"] == 0.0 and inside["scale"] == pytest.approx(50 / 160)
    outside = hand_row([hand(900, 600)], [], face_box=None, frame_h=720, face=face)  # tips at (900, 500)
    assert outside["tip_oval"] == pytest.approx(math.hypot(900 - 720, 500 - 470) / 160, rel=1e-3)
    # Two hands: the one nearest the face gives the scale.
    both = hand_row([hand(900, 600, palm=100), hand(640, 500)], [], face_box=None, frame_h=720, face=face)
    assert both["tip_oval"] == 0.0 and both["scale"] == pytest.approx(50 / 160)
    assert math.isnan(hand_row([hand(640, 500)], [], None, 720)["tip_oval"])  # no face seen


def test_a_face_row_keeps_its_outline_and_width():
    row = face_row(face_points(), rotation())
    assert row["oval"].shape == (len(features.FACE_OVAL), 2) and row["width"] >= 0.0
