"""Take metrics (milestone 7): what can be measured, and nothing inferred.

    take_metrics(alignment, words, t_start, duration_s, gesture_log, trace) -> dict

Every metric is an observation with what it rests on, never a judgment of
the speaker. One without enough to go on is None with the reason, like an
"unclear" verdict.

  speech   pace over the sentences said (words per minute of speaking
           time), fillers per minute of the take, unplanned long pauses
           (silences between words over METRICS.long_pause_s that no pause
           mark asked for), restarts and ad-libs. From the transcript.
  hands    hand-shape changes per minute (the gesture log's poses) and, if
           the session was recorded with --trace, the share of the take a
           hand was in view and how much it moved (the wrist, in palm widths
           per second). These are movement, not "fidgeting": what the
           movement means is for the speaker to judge.
  gaze     while a sentence was being said, the share of face readings
           at the screen (camera or notes) or away, against the session's
           eye calibration, with how many were unclear, overall and per
           sentence (palmcards.gaze; from take-NN.face.npz). Camera vs notes
           is counted but not reported: it did not hold up in the checks.
  posture  not measured yet: pose features are recorded (take-NN.face.npz);
           posture comes with milestone 7's stage 3.

Computed after the take in the analysis worker (palmcards.speech), never
on the camera loop; stored on the take in session.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from palmcards import features, gaze as gaze_mod
from palmcards.config import METRICS
from palmcards.notes import normalize

VERSION = 2  # 2: gaze


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


def hands(gesture_log: Path | None, trace: Path | None, t0: float, t1: float) -> dict:
    minutes = (t1 - t0) / 60
    out = {}
    if gesture_log is not None and gesture_log.exists() and minutes > 0:
        poses = [e for e in _read_jsonl(gesture_log, t0, t1) if e["kind"] == "pose" and e["pose"] != "NONE"]
        out["shape_changes_per_min"] = {"value": round(len(poses) / minutes, 1),
                                        "basis": f"{len(poses)} hand shapes recognised during the take"}
    else:
        out["shape_changes_per_min"] = _none("no gesture log for this take")
    if trace is None or not trace.exists():
        reason = "recorded without --trace (hand landmarks not kept)"
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


def gaze(alignment: dict, vision: dict | None, face: Path | None, calibration: dict | None) -> dict:
    """`vision`: the take's record of its face features; `face`: their file;
    `calibration`: the one the take was recorded with."""
    if vision is None:
        return _none("no face features for this take (recorded before milestone 7)")
    if vision.get("state") != "recorded":
        return _none(f"face tracking was {vision.get('state')}: {vision.get('reason') or vision.get('error', '')}")
    if face is None or not face.exists():
        return _none("the face features file is missing")
    arrays, _ = features.load(face)
    return gaze_mod.take_gaze(arrays, calibration, alignment, vision.get("calibration"))


def take_metrics(alignment: dict, words: list[dict], t_start: float, duration_s: float,
                 gesture_log: Path | None = None, trace: Path | None = None,
                 planned_pauses: set[int] = frozenset(), vision: dict | None = None, face: Path | None = None,
                 calibration: dict | None = None) -> dict:
    return {
        "version": VERSION,
        "speech": speech(alignment, words, duration_s, planned_pauses),
        "hands": hands(gesture_log, trace, t_start, t_start + duration_s),
        "gaze": gaze(alignment, vision, face, calibration),
        "posture": _none("not measured yet: pose features are recorded, posture comes next (milestone 7)"),
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
