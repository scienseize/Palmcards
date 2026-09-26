"""Take metrics (milestone 7): what can be measured, and nothing inferred.

    take_metrics(alignment, words, t_start, duration_s, gesture_log, trace) -> dict

Every metric is an observation with what it rests on, never a judgment of
the speaker. One without enough to go on is None with the reason, like an
"unclear" verdict.

  speech   pace over the sentences said (words per minute of speaking
           time), fillers per minute of the take, unplanned long pauses
           (silences between words over METRICS.long_pause_s that no pause
           mark asked for), restarts and ad-libs. From the transcript.
  hands    hand-shape changes per minute (the gesture log's poses); from the
           take's features (or, before them, a --trace recording) the share
           of the take a hand was in view and how much the wrists and the
           fingertips moved (palm widths per second while a hand was in view);
           and face touches (count and seconds). These are movement, not
           "fidgeting": what the movement means is for the speaker to judge.
  gaze     while a sentence was being said, the share of face readings
           at the screen (camera or notes) or away, against the session's
           eye calibration, with how many were unclear, overall and per
           sentence (palmcards.gaze; from take-NN.face.npz). Camera vs notes
           is counted but not reported: it did not hold up in the checks.
  posture  shoulder tilt and head height against the calibration's
           baseline: the median change, and the share of pose readings
           tilted over METRICS.tilt_deg or dropped over METRICS.head_drop.

Computed after the take in the analysis worker (palmcards.speech), never
on the camera loop; stored on the take in session.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from palmcards import features, gaze as gaze_mod
from palmcards.config import BODY, METRICS
from palmcards.notes import normalize

VERSION = 3  # 2: gaze; 3: posture, hands from the features, face touches


def _none(reason: str) -> dict:
    return {"value": None, "reason": reason}


def speech(alignment: dict, words: list[dict], duration_s: float, planned_pauses: set[int] = frozenset()) -> dict:
    """`planned_pauses`: transcript word indices a pause mark sits before."""
    said = [s for s in alignment["sentences"] if s["status"] != "skipped" and s["start"] is not None]
    n_words = sum(sum(i is not None for i in s["words"]) for s in said)
    speaking_s = sum(s["end"] - s["start"] for s in said)
    out = {}
    if speaking_s >= METRICS.min_speaking_s and n_words >= METRICS.min_words:
        out["pace_wpm"] = {"value": round(60 * n_words / speaking_s, 1),
                           "basis": f"{n_words} words over {speaking_s:.1f} s of sentences said"}
    else:
        out["pace_wpm"] = _none(f"too little said ({n_words} words, {speaking_s:.1f} s)")
    minutes = duration_s / 60
    if duration_s >= METRICS.min_take_s:
        out["fillers_per_min"] = {"value": round(len(alignment["fillers"]) / minutes, 2),
                                  "basis": f"{len(alignment['fillers'])} fillers in {duration_s:.0f} s"}
    else:
        out["fillers_per_min"] = _none(f"take too short ({duration_s:.0f} s)")
    unsure = set(alignment.get("unsure", []))
    real = [i for i, w in enumerate(words) if normalize(w["text"]) and i not in unsure]
    gaps = [(words[b]["start"] - words[a]["end"], b) for a, b in zip(real, real[1:])]
    long = [g for g, b in gaps if g >= METRICS.long_pause_s and b not in planned_pauses]
    out["unplanned_long_pauses"] = {"value": len(long), "longest_s": round(max(long), 2) if long else None,
                                    "basis": f"silences over {METRICS.long_pause_s:g} s no pause mark asked for"}
    out["restarts"] = {"value": len(alignment["restarts"]), "basis": "phrases said again"}
    out["ad_libs"] = {"value": len(alignment["extras"]), "basis": "runs of words not in the notes"}
    return out


def _read_jsonl(path: Path, t0: float, t1: float) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                if t0 <= entry.get("t", -1) <= t1:
                    out.append(entry)
    return out


def hands(gesture_log: Path | None, trace: Path | None, t0: float, t1: float,
          arrays: dict | None = None, why: str = "") -> dict:
    """`arrays`: the take's features (take-NN.face.npz), when it has them;
    `why`: why not. Movement comes from them, else from a --trace recording."""
    minutes = (t1 - t0) / 60
    out = {}
    if gesture_log is not None and gesture_log.exists() and minutes > 0:
        poses = [e for e in _read_jsonl(gesture_log, t0, t1) if e["kind"] == "pose" and e["pose"] != "NONE"]
        out["shape_changes_per_min"] = {"value": round(len(poses) / minutes, 1),
                                        "basis": f"{len(poses)} hand shapes recognised during the take"}
    else:
        out["shape_changes_per_min"] = _none("no gesture log for this take")
    if arrays is not None:
        out.update(_hand_movement(arrays))
        out["face_touches"] = face_touches(arrays)
        return out
    out["face_touches"] = _none(why or "no face features for this take")
    if trace is None or not trace.exists():
        reason = f"no hand features ({why}) and recorded without --trace" if why else \
            "recorded without --trace (hand landmarks not kept)"
        out["in_view_share"], out["movement_palms_s"] = _none(reason), _none(reason)
        return out
    frames = _read_jsonl(trace, t0, t1)
    if len(frames) < 2:
        out["in_view_share"], out["movement_palms_s"] = _none("no traced frames"), _none("no traced frames")
        return out
    out["in_view_share"] = {"value": round(sum(bool(f["hands"]) for f in frames) / len(frames), 3),
                            "basis": f"{len(frames)} traced frames"}
    travel, seconds = 0.0, 0.0
    for a, b in zip(frames, frames[1:]):
        if len(a["hands"]) == 1 and len(b["hands"]) == 1 and b["t"] - a["t"] < 0.2:  # one hand, both frames
            pa, pb = a["hands"][0]["points"], b["hands"][0]["points"]
            palm = math.dist(pb[0], pb[9]) or 1.0
            travel += math.dist(pa[0], pb[0]) / palm
            seconds += b["t"] - a["t"]
    out["movement_palms_s"] = {"value": round(travel / seconds, 2), "basis": f"{seconds:.1f} s with one hand in view"} \
        if seconds >= 1.0 else _none("under a second with one hand in view")
    return out


def _hand_movement(a: dict) -> dict:
    """In view, and how far the wrists and fingertips travelled, from the hand rows of the features."""
    t, n = a["hand_t"], a["hand_n"]
    if len(t) < 2:
        why = "no hand-tracking results in the take"
        return {"in_view_share": _none(why), "movement_palms_s": _none(why), "fingertip_movement_palms_s": _none(why)}
    out = {"in_view_share": {"value": round(float(np.mean(n > 0)), 3), "basis": f"{len(t)} hand-tracking results"}}
    dt = np.diff(t)
    pair = (n[1:] > 0) & (n[:-1] > 0) & (dt <= METRICS.move_max_gap_s)  # a hand in view in both results
    seconds = float(dt[pair].sum())
    for key, column in (("movement_palms_s", "hand_wrist_move"), ("fingertip_movement_palms_s", "hand_tip_move")):
        moved = a[column][1:][pair]
        if seconds < METRICS.min_hand_s:
            out[key] = _none(f"a hand in view for {seconds:.1f} s (needs {METRICS.min_hand_s:g})")
        else:
            out[key] = {"value": round(float(np.nansum(moved)) / seconds, 2),
                        "basis": f"{seconds:.1f} s with a hand in view"}
    return out


def face_touches(a: dict) -> dict:
    """Times a fingertip was on the face: inside its outline (or within METRICS.touch_margin
    face widths) with the hand at the face's depth by its size, for METRICS.touch_min_s or more."""
    if "hand_tip_oval" not in a:
        return _none("recorded before face-touch features (features version 1)")
    t, n, d, scale = a["hand_t"], a["hand_n"], a["hand_tip_oval"], a["hand_scale"]
    in_view = n > 0
    if not in_view.any():
        return {"value": 0, "seconds": 0.0, "basis": "no hand in view during the take"}
    measured = in_view & ~np.isnan(d)
    if not measured.any():
        return _none("a hand was in view but the face was never seen with it")
    touching = measured & (d <= METRICS.touch_margin) & (scale >= METRICS.touch_scale_min) & \
        (scale <= METRICS.touch_scale_max)
    runs, start, last = [], None, None
    for ti, on in zip(t, touching):
        if start is not None and ti - last > METRICS.touch_gap_s:  # off for longer than a gap: the touch ended
            runs.append(last - start)
            start = None
        if on:
            start = ti if start is None else start
            last = ti
    if start is not None:
        runs.append(last - start)
    kept = [r for r in runs if r >= METRICS.touch_min_s]
    touches, seconds = len(kept), sum(kept)
    return {"value": touches, "seconds": round(seconds, 2),
            "basis": f"a fingertip on the face's outline with the hand at the face's depth, {METRICS.touch_min_s:g} s "
                     f"or longer; {int(measured.sum())} hand results with the face in view"}


def posture(a: dict | None, calibration: dict | None, why: str = "") -> dict:
    """Shoulder tilt and head height against the calibration's posture baseline."""
    if a is None:
        return _none(why or "no face features for this take")
    base = (calibration or {}).get("posture") or {}
    if not calibration or calibration.get("status") != "ok" or not base.get("tilt") or not base.get("head"):
        return _none("no posture baseline: no usable eye calibration when the take was recorded")
    ok = (a["pose_found"] == 1) & (a["pose_vis"] >= BODY.min_visibility) & ~np.isnan(a["pose_tilt"]) & \
        ~np.isnan(a["pose_head"])
    count = int(ok.sum())
    if count < METRICS.min_pose_readings:
        return _none(f"too few pose readings with the shoulders in view ({count}, needs {METRICS.min_pose_readings})")
    tilt = a["pose_tilt"][ok] - base["tilt"][0]
    head = a["pose_head"][ok] - base["head"][0]
    basis = f"{count} pose readings against calibration {calibration.get('id', '?')}"
    return {
        "version": 1,
        "calibration": calibration.get("id"),
        "shoulder_tilt_deg": {"value": round(float(np.median(tilt)), 1),
                              "basis": f"median, degrees from the calibration's {base['tilt'][0]:+.1f}; {basis}"},
        "tilted_share": {"value": round(float(np.mean(np.abs(tilt) > METRICS.tilt_deg)), 3),
                         "basis": f"shoulder line over {METRICS.tilt_deg:g} degrees from the calibration's; {basis}"},
        "head_height_change": {"value": round(float(np.median(head)), 3),
                               "basis": f"median, nose above the shoulders in shoulder widths, against the "
                                        f"calibration's {base['head'][0]:.2f}; {basis}"},
        "head_dropped_share": {"value": round(float(np.mean(head < -METRICS.head_drop)), 3),
                               "basis": f"head over {METRICS.head_drop:g} shoulder widths lower than the "
                                        f"calibration's; {basis}"},
    }


def _features(vision: dict | None, face: Path | None) -> tuple[dict | None, str]:
    """The take's features, or None and why not."""
    if vision is None:
        return None, "no face features for this take (recorded before milestone 7)"
    if vision.get("state") != "recorded":
        return None, f"face tracking was {vision.get('state')}: {vision.get('reason') or vision.get('error', '')}"
    if face is None or not face.exists():
        return None, "the face features file is missing"
    return features.load(face)[0], ""


def gaze(alignment: dict, vision: dict | None, face: Path | None, calibration: dict | None) -> dict:
    """`vision`: the take's record of its face features; `face`: their file;
    `calibration`: the one the take was recorded with."""
    arrays, why = _features(vision, face)
    if arrays is None:
        return _none(why)
    return gaze_mod.take_gaze(arrays, calibration, alignment, vision.get("calibration"))


def take_metrics(alignment: dict, words: list[dict], t_start: float, duration_s: float,
                 gesture_log: Path | None = None, trace: Path | None = None,
                 planned_pauses: set[int] = frozenset(), vision: dict | None = None, face: Path | None = None,
                 calibration: dict | None = None) -> dict:
    arrays, why = _features(vision, face)
    return {
        "version": VERSION,
        "speech": speech(alignment, words, duration_s, planned_pauses),
        "hands": hands(gesture_log, trace, t_start, t_start + duration_s, arrays, why),
        "gaze": gaze_mod.take_gaze(arrays, calibration, alignment, vision.get("calibration")) if arrays is not None
        else _none(why),
        "posture": posture(arrays, calibration, why),
    }


def planned_pause_words(alignment: dict, marks: list[list]) -> set[int]:
    """Transcript words that a pause mark sits before (their silence was asked for)."""
    out = set()
    for entry, sent_marks in zip(alignment["sentences"], marks):
        for kind, word in sent_marks:
            if kind in ("short_pause", "long_pause") and word is not None and word < len(entry["words"]):
                if (t := entry["words"][word]) is not None:
                    out.add(t)
    return out


def summary(m: dict) -> str:
    """A short line: "142 WPM, 1.5 FILLERS/MIN"."""
    s = m["speech"]
    parts = []
    if s["pace_wpm"]["value"] is not None:
        parts.append(f"{s['pace_wpm']['value']:.0f} WPM")
    if s["fillers_per_min"]["value"] is not None:
        parts.append(f"{s['fillers_per_min']['value']:g} FILLERS/MIN")
    return ", ".join(parts)
