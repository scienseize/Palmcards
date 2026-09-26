"""Where the speaker was looking, from a take's face features and the session's calibration.

    classify(arrays, calibration)  -> a class per face reading, and why none if it can't
    take_gaze(arrays, calibration, alignment)
                                   -> the take's gaze metric: shares while speaking,
                                      overall and per sentence
    prompt_schedule(seed)          -> the gaze check's timed prompts

Each reading with a face is compared with the calibration's two baselines
(looking into the camera, reading the notes): the distance over head yaw,
pitch and iris position, each feature divided by its scale (GAZE.mad_scale x
the larger of the two steps' spreads, at least GAZE.floor_*). A reading is
"camera" when it is nearer the camera baseline and within GAZE.camera_radius,
"notes" when nearer the notes' and within GAZE.notes_radius, "away" when
it is too far from both, and "unclear" without a face, during a blink, or
with a value missing. A calibration whose baselines are too close to tell
apart (GAZE.min_separation) makes every reading unclear.

These are observations, not judgments: how much eye contact suits a talk is
the speaker's call. Validated only by the gaze check so far (scripts/evaluate.py
--gaze): the prompts a person followed against these classes.
"""

from __future__ import annotations

import random

import numpy as np

from palmcards.config import BODY, GAZE

VERSION = 1
CLASSES = ("camera", "notes", "away", "unclear")
FEATURES = ("yaw", "pitch", "iris_x", "iris_y")
AWAY_HINTS = ("DOWN AT THE DESK", "TO YOUR LEFT", "TO YOUR RIGHT", "UP AT THE CEILING")


def scales(calibration: dict) -> dict[str, float]:
    floors = {"yaw": GAZE.floor_yaw, "pitch": GAZE.floor_pitch, "iris_x": GAZE.floor_iris_x,
              "iris_y": GAZE.floor_iris_y}
    out = {}
    for k in FEATURES:
        mads = [calibration[step][k][1] for step in ("camera", "notes") if calibration[step].get(k)]
        out[k] = max(floors[k], GAZE.mad_scale * max(mads, default=0.0))
    return out


def _centre(calibration: dict, step: str) -> np.ndarray:
    return np.array([calibration[step][k][0] if calibration[step].get(k) else np.nan for k in FEATURES])


def separation(calibration: dict) -> float:
    """How far apart the two baselines are, in scaled units."""
    s = np.array([scales(calibration)[k] for k in FEATURES])
    d = (_centre(calibration, "camera") - _centre(calibration, "notes")) / s
    return float(np.sqrt(np.nansum(d * d)))


def usable(calibration: dict | None) -> str:
    """Why this calibration can't classify gaze, or "" if it can."""
    if calibration is None:
        return "no eye calibration when the take was recorded"
    if calibration.get("status") != "ok":
        return f"the eye calibration {calibration.get('status', 'failed')}: {calibration.get('reason', '')}"
    if any(calibration[step].get(k) is None for step in ("camera", "notes") for k in FEATURES):
        return "the eye calibration is missing a baseline"
    sep = separation(calibration)
    if sep < GAZE.min_separation:
        return f"the eye calibration can't tell camera from notes apart (separation {sep:.1f}, needs " \
               f"{GAZE.min_separation:g})"
    return ""


def classify(arrays: dict[str, np.ndarray], calibration: dict | None) -> np.ndarray:
    """A class per face reading (CLASSES); all "unclear" if the calibration can't be used."""
    n = len(arrays["face_t"])
    out = np.full(n, "unclear", dtype=object)
    if usable(calibration) or n == 0:
        return out
    s = np.array([scales(calibration)[k] for k in FEATURES])
    x = np.stack([arrays[f"face_{k}"].astype(np.float64) for k in FEATURES], axis=1)
    d_cam = np.sqrt((((x - _centre(calibration, "camera")) / s) ** 2).sum(axis=1))
    d_notes = np.sqrt((((x - _centre(calibration, "notes")) / s) ** 2).sum(axis=1))
    clear = (arrays["face_found"] == 1) & ~np.isnan(x).any(axis=1) & ~(arrays["face_eye_open"] < BODY.closed_eye)
    out[clear] = "away"
    out[clear & (d_cam <= d_notes) & (d_cam <= GAZE.camera_radius)] = "camera"
    out[clear & (d_notes < d_cam) & (d_notes <= GAZE.notes_radius)] = "notes"
    return out


def _none(reason: str) -> dict:
    return {"value": None, "reason": reason}


def _counts(labels: np.ndarray) -> dict[str, int]:
    return {c: int((labels == c).sum()) for c in CLASSES}


def speaking(t: np.ndarray, alignment: dict) -> tuple[np.ndarray, list[tuple[int, np.ndarray]]]:
    """Which readings fall while a sentence was being said, overall and per sentence."""
    per = []
    any_ = np.zeros(len(t), dtype=bool)
    for i, s in enumerate(alignment["sentences"]):
        if s["status"] == "skipped" or s["start"] is None:
            continue
        inside = (t >= s["start"] - GAZE.speech_pad_s) & (t <= s["end"] + GAZE.speech_pad_s)
        per.append((s.get("sentence", i), inside))
        any_ |= inside
    return any_, per


def take_gaze(arrays: dict[str, np.ndarray] | None, calibration: dict | None, alignment: dict,
              calibration_id: str | None = None, off_reason: str = "") -> dict:
    """The take's gaze metric: while a sentence was being said, the share of the
    judged readings looking into the camera, at the notes, or away; how many
    were unclear; and the same counts per sentence."""
    if arrays is None:
        return _none(off_reason or "no face features for this take")
    if why := usable(calibration):
        return _none(why)
    labels = classify(arrays, calibration)
    during, per = speaking(arrays["face_t"], alignment)
    counts = _counts(labels[during])
    judged = counts["camera"] + counts["notes"] + counts["away"]
    total = judged + counts["unclear"]
    if judged < GAZE.min_frames:
        return _none(f"too few readings judged while speaking ({judged} of {total}, needs {GAZE.min_frames})")
    basis = f"{judged} face readings judged while speaking ({counts['unclear']} more unclear), " \
            f"calibration {calibration_id or calibration.get('id', '?')}"
    return {
        "version": VERSION,
        "calibration": calibration_id or calibration.get("id"),
        "camera_share": {"value": round(counts["camera"] / judged, 3), "basis": basis},
        "notes_share": {"value": round(counts["notes"] / judged, 3), "basis": basis},
        "away_share": {"value": round(counts["away"] / judged, 3), "basis": basis},
        "unclear_share": {"value": round(counts["unclear"] / total, 3), "basis": f"{total} face readings while speaking"},
        "counts": counts,
        "sentences": [{"sentence": i, **_counts(labels[inside])} for i, inside in per],
    }


# --- the gaze check -----------------------------------------------------------------

def prompt_schedule(seed: int, each: int | None = None, step_s: float | None = None) -> list[dict]:
    """Camera, notes and away prompts, `each` times each (GAZE.check_each), shuffled
    with no target twice in a row; `t0`/`t1` in seconds from the start of the
    take, GAZE.check_step_s apart. Away prompts name a direction, in turn."""
    each = GAZE.check_each if each is None else each
    step_s = GAZE.check_step_s if step_s is None else step_s
    rng = random.Random(seed)
    for _ in range(1000):
        order = [c for c in ("camera", "notes", "away") for _ in range(each)]
        rng.shuffle(order)
        if all(a != b for a, b in zip(order, order[1:])):
            break
    out, away = [], 0
    for i, target in enumerate(order):
        hint = ""
        if target == "away":
            hint, away = AWAY_HINTS[away % len(AWAY_HINTS)], away + 1
        out.append({"target": target, "hint": hint, "t0": round(i * step_s, 3), "t1": round((i + 1) * step_s, 3)})
    return out


def prompt_text(prompt: dict) -> str:
    return {"camera": "LOOK INTO THE CAMERA", "notes": "READ THE NOTES",
            "away": f"LOOK AWAY: {prompt['hint']}"}[prompt["target"]]


def check_agreement(arrays: dict[str, np.ndarray], calibration: dict | None, prompts: list[dict],
                    t0: float, settle_s: float | None = None) -> dict:
    """The prompts (on the app clock from `t0`) against the classes of the readings
    inside them, the first `settle_s` (GAZE.check_settle_s) of each prompt left out."""
    settle_s = GAZE.check_settle_s if settle_s is None else settle_s
    labels = classify(arrays, calibration)
    t = arrays["face_t"]
    confusion = {target: {c: 0 for c in CLASSES} for target in ("camera", "notes", "away")}
    features = {target: [] for target in confusion}
    x = np.stack([arrays[f"face_{k}"] for k in FEATURES], axis=1)
    for p in prompts:
        inside = (t >= t0 + p["t0"] + settle_s) & (t < t0 + p["t1"])
        for label in labels[inside]:
            confusion[p["target"]][label] += 1
        features[p["target"]].append(x[inside & (arrays["face_found"] == 1)])
    judged = [(target, c) for target, row in confusion.items() for c in ("camera", "notes", "away")
              for _ in range(row[c])]
    agree = sum(a == b for a, b in judged)
    total = sum(sum(row.values()) for row in confusion.values())
    medians = {}
    for target, chunks in features.items():
        rows = np.concatenate(chunks) if chunks else np.zeros((0, len(FEATURES)))
        medians[target] = {k: round(float(np.nanmedian(rows[:, i])), 4) if len(rows) else None
                           for i, k in enumerate(FEATURES)}
    return {
        "calibration_usable": usable(calibration) or "yes",
        "readings": total,
        "judged": len(judged),
        "unclear_share": round(1 - len(judged) / total, 3) if total else None,
        "agreement": round(agree / len(judged), 3) if judged else None,
        "kappa": _kappa(judged),
        "recall": {target: round(row[target] / j, 3) if (j := row["camera"] + row["notes"] + row["away"]) else None
                   for target, row in confusion.items()},
        "confusion": confusion,
        "medians": medians,
    }


def _kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa: agreement beyond what the class frequencies alone would give."""
    if not pairs:
        return None
    n = len(pairs)
    classes = ("camera", "notes", "away")
    observed = sum(a == b for a, b in pairs) / n
    expected = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in classes)
    return round((observed - expected) / (1 - expected), 3) if expected < 1 else None

