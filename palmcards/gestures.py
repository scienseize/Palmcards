"""Landmarks -> poses -> gesture events, and the Prepare-mode grammar.

All coordinates are pixels in the mirrored frame (the frame is flipped once at
capture). Thresholds live in `palmcards.config`.

Pipeline per hand result:
  Hand (21 landmarks) -> features() -> classify() -> HandTrack (150 ms
  stability, FOLD and COMMIT events) -> Grammar (Browse/Focus at word,
  sentence and paragraph level, operation stubs) -> GestureEvent.

The grammar (Kat's "gestural editing/writing"):
  shape picks the scope     ONE = word, TWO = sentence, FLAT = paragraph
  close the hand to focus   PINCH (word) or FOLD fingers onto the thumb
  second shape operates     OPEN = options ring (word), L tilt = tone dial
                            (sentence), two L hands = length stretch (paragraph)
  pinch + lift commits      back to Browse at the same level
  drop the hand backs out   out of frame or below the bottom band for 1 s

The cursor is relative: a hand box on the right of the frame maps onto the
text box on the left; its top and bottom bands scroll.

Run `python -m palmcards.gestures` for a debug view with landmarks, poses,
feature values, the grammar state and the event log.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from palmcards.config import CURSOR, OPS, POSE, TIMING, TRACKING

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
LOG_DIR = Path(__file__).resolve().parent.parent / "sessions" / "gesture-logs"

# MediaPipe hand landmark indices.
WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_PIP, PINKY_TIP = 18, 20
FINGERS = ((INDEX_PIP, INDEX_TIP), (MIDDLE_PIP, MIDDLE_TIP), (RING_PIP, RING_TIP), (PINKY_PIP, PINKY_TIP))
TIPS = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]

# Pose classes.
ONE, TWO, FLAT, OPEN, L, PINCH, FIST, NONE = "ONE", "TWO", "FLAT", "OPEN", "L", "PINCH", "FIST", "NONE"
LEVEL_OF_SHAPE = {ONE: "word", TWO: "sentence", FLAT: "paragraph"}
SHAPE_OF_LEVEL = {v: k for k, v in LEVEL_OF_SHAPE.items()}
FOLD_TIPS = {TWO: (INDEX_TIP, MIDDLE_TIP), FLAT: (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)}


@dataclass
class Hand:
    points: np.ndarray  # (21, 2) float pixels, mirrored frame
    gesture: str = "None"  # recognizer category, shown in the debug view only
    score: float = 0.0
    handedness: str = ""

    def dist(self, a: int, b: int) -> float:
        return float(np.linalg.norm(self.points[a] - self.points[b]))

    @property
    def size(self) -> float:
        """palm = dist(wrist, middle MCP): a scale reference that ignores finger pose."""
        return max(self.dist(WRIST, MIDDLE_MCP), 1e-6)

    def point(self, i: int) -> tuple[float, float]:
        x, y = self.points[i]
        return float(x), float(y)


class HandTracker:
    """MediaPipe Gesture Recognizer in LIVE_STREAM mode: landmarks per hand.

    submit() never blocks the display loop: if the recognizer is still busy
    with the previous frame, the new frame is skipped. Results arrive on a
    MediaPipe thread; poll() hands the newest one to the caller once.
    """

    def __init__(self, num_hands: int = TRACKING.num_hands, max_side: int = TRACKING.max_side):
        from mediapipe.tasks.python import BaseOptions, vision

        model = MODELS_DIR / "gesture_recognizer.task"
        if not model.exists():
            raise FileNotFoundError(f"{model} missing: run python scripts/download_models.py")
        self.max_side = max_side
        self.latency_ms = 0.0  # submit -> result, smoothed
        self._last_ms = -1
        self._lock = threading.Lock()
        self._busy = False
        self._busy_since = 0.0
        self._size = (1, 1)
        self._submitted: dict[int, float] = {}
        self._result: tuple[list[Hand], float] | None = None
        self._recognizer = vision.GestureRecognizer.create_from_options(
            vision.GestureRecognizerOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.LIVE_STREAM,
                result_callback=self._on_result,
                num_hands=num_hands,
                min_hand_detection_confidence=TRACKING.min_detection,
                min_hand_presence_confidence=TRACKING.min_presence,
                min_tracking_confidence=TRACKING.min_tracking,
            )
        )

    def submit(self, frame_bgr: np.ndarray, t: float) -> bool:
        """Queue a frame for recognition; False if skipped because busy."""
        import mediapipe as mp

        now = time.perf_counter()
        with self._lock:
            # A result normally clears _busy; the timeout guards against a
            # dropped callback freezing tracking for good.
            if self._busy and now - self._busy_since < TRACKING.busy_timeout_s:
                return False
            self._busy, self._busy_since = True, now
        h, w = frame_bgr.shape[:2]
        self._size = (w, h)
        scale = min(1.0, self.max_side / max(h, w))
        small = cv2.resize(frame_bgr, None, fx=scale, fy=scale) if scale < 1 else frame_bgr
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        # Timestamps must strictly increase.
        ms = max(int(t * 1000), self._last_ms + 1)
        self._last_ms = ms
        self._submitted[ms] = time.perf_counter()
        self._recognizer.recognize_async(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms)
        return True

    def _on_result(self, result, _image, ms: int) -> None:
        w, h = self._size
        hands = []
        for i, lms in enumerate(result.hand_landmarks):
            pts = np.array([(lm.x * w, lm.y * h) for lm in lms], dtype=np.float32)
            gesture, score = "None", 0.0
            if i < len(result.gestures) and result.gestures[i]:
                gesture, score = result.gestures[i][0].category_name, result.gestures[i][0].score
            side = result.handedness[i][0].category_name if i < len(result.handedness) else ""
            hands.append(Hand(pts, gesture, score, side))
        sent = self._submitted.pop(ms, None)
        with self._lock:
            if sent is not None:
                self.latency_ms = 0.9 * self.latency_ms + 0.1 * (time.perf_counter() - sent) * 1000
            self._result = (hands, ms / 1000)
            self._busy = False

    def poll(self) -> tuple[list[Hand], float] | None:
        """Newest (hands, capture time in s) since the last poll, else None."""
        with self._lock:
            result, self._result = self._result, None
        return result

    def close(self) -> None:
        self._recognizer.close()


# --- smoothing ---------------------------------------------------------------

class OneEuro:
    """One Euro filter: smooths jitter when still, stays responsive when moving."""

    def __init__(self, min_cutoff: float = CURSOR.min_cutoff, beta: float = CURSOR.beta,
                 d_cutoff: float = CURSOR.d_cutoff):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self._x = self._dx = None
        self._t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: np.ndarray, t: float) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self._x is None or t <= self._t:
            self._x, self._dx, self._t = x, np.zeros_like(x), t
            return x
        dt = t - self._t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * float(np.linalg.norm(self._dx))
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1 - a) * self._x
        self._t = t
        return self._x


# --- features and poses ------------------------------------------------------

@dataclass(frozen=True)
class Features:
    """Per-hand measurements, distances in palm units."""

    extended: tuple[bool, bool, bool, bool]  # index, middle, ring, pinky
    thumb_out: bool
    pinch_dist: float  # thumb tip to index tip
    reach: float  # wrist to index tip
    together: bool  # index and middle tips close
    spread: float  # mean adjacent fingertip distance
    tilt: float  # index MCP -> tip from vertical, degrees, + = screen right
    thumb_index_angle: float  # degrees between thumb and index directions


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    cos = float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def features(hand: Hand) -> Features:
    palm = hand.size
    p = hand.points
    extended = tuple(
        hand.dist(WRIST, tip) > POSE.extended_ratio * hand.dist(WRIST, pip) for pip, tip in FINGERS
    )
    spread = (hand.dist(INDEX_TIP, MIDDLE_TIP) + hand.dist(MIDDLE_TIP, RING_TIP)
              + hand.dist(RING_TIP, PINKY_TIP)) / (3 * palm)
    index_dir = p[INDEX_TIP] - p[INDEX_MCP]
    return Features(
        extended=extended,
        thumb_out=hand.dist(THUMB_TIP, INDEX_MCP) > POSE.thumb_out * palm,
        pinch_dist=hand.dist(THUMB_TIP, INDEX_TIP) / palm,
        reach=hand.dist(WRIST, INDEX_TIP) / palm,
        together=hand.dist(INDEX_TIP, MIDDLE_TIP) / palm < POSE.together,
        spread=spread,
        tilt=math.degrees(math.atan2(float(index_dir[0]), float(-index_dir[1]))),
        thumb_index_angle=_angle_deg(p[THUMB_TIP] - p[THUMB_MCP], index_dir),
    )


def is_pinch(f: Features, was_pinching: bool) -> bool:
    return f.reach > POSE.pinch_min_reach and f.pinch_dist < (POSE.pinch_off if was_pinching else POSE.pinch_on)


def classify(f: Features, was_pinching: bool = False) -> str:
    if is_pinch(f, was_pinching):
        return PINCH
    index, middle, ring, pinky = f.extended
    if index and middle and ring and pinky:
        if f.thumb_out and f.spread > POSE.open_spread_min:
            return OPEN
        return FLAT if f.spread < POSE.flat_spread_max else NONE
    if index and not (middle or ring or pinky):
        if not f.thumb_out:
            return ONE
        return L if POSE.l_angle_min <= f.thumb_index_angle <= POSE.l_angle_max else NONE
    if index and middle and not (ring or pinky):
        return TWO if f.together else NONE  # a spread V sign is ignored
    if not any(f.extended):
        return FIST
    return NONE


def fold_dist(hand: Hand, tips: tuple[int, ...]) -> float:
    """Mean fingertip-to-thumb distance over `tips`, in palm units."""
    return sum(hand.dist(t, THUMB_TIP) for t in tips) / (len(tips) * hand.size)


class HandTrack:
    """One hand over time: stable pose plus FOLD and COMMIT detection."""

    def __init__(self):
        self.raw = NONE
        self.stable = NONE
        self.pinching = False
        self.pinch_start: float | None = None
        self.feat: Features | None = None
        self.hand: Hand | None = None
        self.last_seen = 0.0
        self._cand = NONE
        self._cand_since = 0.0
        self._lift: deque[tuple[float, float]] = deque()
        self._lift_fired = False
        self._fold_tips: tuple[int, ...] | None = None
        self._fold: deque[tuple[float, float]] = deque()
        self._fold_armed = False

    def update(self, hand: Hand, t: float, frame_h: float) -> list[str]:
        """Returns event names: "pose" (stable pose changed), "fold", "commit"."""
        events: list[str] = []
        self.hand, self.last_seen = hand, t
        self.feat = f = features(hand)
        self.raw = classify(f, self.pinching)
        self.pinching = is_pinch(f, self.pinching)

        if self.raw != self._cand:
            self._cand, self._cand_since = self.raw, t
        if self._cand != self.stable and t - self._cand_since >= TIMING.stable_s - 1e-9:
            self.stable = self._cand
            events.append("pose")

        # FOLD: from TWO or FLAT, the extended fingertips converge on the
        # thumb quickly. The folding hand passes through NONE and PINCH, so
        # those keep the fingers of the last TWO/FLAT.
        if self.stable in FOLD_TIPS:
            if self._fold_tips != FOLD_TIPS[self.stable]:
                self._fold_tips = FOLD_TIPS[self.stable]
                self._fold.clear()
        elif self.stable not in (NONE, PINCH):
            self._fold_tips = None
            self._fold.clear()
        if self._fold_tips:
            d = fold_dist(hand, self._fold_tips)
            self._fold.append((t, d))
            while self._fold and self._fold[0][0] < t - TIMING.fold_window_s:
                self._fold.popleft()
            if d >= TIMING.fold_start_dist:
                self._fold_armed = True
            elif (d < TIMING.fold_dist and self._fold_armed
                  and any(dd >= TIMING.fold_start_dist for _, dd in self._fold)):
                self._fold_armed = False
                events.append("fold")

        # COMMIT: pinch held while the wrist rises quickly.
        if self.pinching:
            wy = float(hand.points[WRIST][1])
            if self.pinch_start is None:
                self.pinch_start, self._lift_fired = t, False
                self._lift.clear()
            self._lift.append((t, wy))
            while self._lift and self._lift[0][0] < t - TIMING.commit_window_s:
                self._lift.popleft()
            if not self._lift_fired and max(y for _, y in self._lift) - wy > TIMING.commit_rise * frame_h:
                self._lift_fired = True
                events.append("commit")
        else:
            self.pinch_start = None
        return events


# --- relative cursor -----------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(max(x, lo), hi)


class RelativeCursor:
    """Smoothed fingertip -> (u, v) in the hand box, plus edge-scroll rate."""

    def __init__(self, frame_size: tuple[int, int]):
        w, h = frame_size
        x0, y0, x1, y1 = CURSOR.hand_box
        self.box = (x0 * w, y0 * h, x1 * w, y1 * h)
        self._filter = OneEuro()
        self.uv: tuple[float, float] | None = None

    def update(self, point: tuple[float, float], t: float) -> tuple[float, float]:
        x, y = self._filter(np.array(point), t)
        bx0, by0, bx1, by1 = self.box
        self.uv = (_clamp((x - bx0) / (bx1 - bx0)), _clamp((y - by0) / (by1 - by0)))
        return self.uv

    def reset(self) -> None:
        self._filter.reset()
        self.uv = None

    @property
    def scroll_rate(self) -> float:
        """Rows per second: negative in the top band, positive in the bottom."""
        if self.uv is None:
            return 0.0
        v, band = self.uv[1], CURSOR.edge_band
        if v < band:
            return -CURSOR.edge_speed_rows_s * (band - v) / band
        if v > 1 - band:
            return CURSOR.edge_speed_rows_s * (v - (1 - band)) / band
        return 0.0


# --- event log -----------------------------------------------------------------

class GestureLog:
    """Timestamped poses and events: kept in memory, appended to JSONL if given a path."""

    def __init__(self, path: Path | None = None, keep: int = 200):
        self.entries: deque[dict] = deque(maxlen=keep)
        self.path = path
        self._file = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", buffering=1)

    @classmethod
    def to_session_dir(cls) -> "GestureLog":
        return cls(LOG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl")

    def __call__(self, t: float, kind: str, **detail) -> None:
        entry = {"t": round(t, 3), "kind": kind, **detail}
        self.entries.append(entry)
        if self._file:
            self._file.write(json.dumps(entry) + "\n")

    def tail(self, n: int) -> list[str]:
        out = []
        for e in list(self.entries)[-n:]:
            rest = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "kind"))
            out.append(f"{e['t']:7.2f}s {e['kind']} {rest}")
        return out

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None


# --- grammar -------------------------------------------------------------------

@dataclass
class GestureEvent:
    kind: str  # "focus" | "commit" | "back"
    t: float
    level: str | None = None
    op: str | None = None
    value: float | None = None  # tone (-1 cold .. 1 warm) or length ratio


@dataclass
class GestureState:
    mode: str = "idle"  # idle | browse | focus
    level: str | None = None  # word | sentence | paragraph
    op: str | None = None  # ring | tone | stretch (stubs)
    cursor: tuple[float, float] | None = None  # (u, v) in the hand box
    scroll_rate: float = 0.0  # rows per second
    pointing: bool = False  # L-hand pointing at ring nodes
    tone: float = 0.0  # -1 cold .. 1 warm
    stretch: float = 1.0  # length ratio
    stretch_ends: tuple[tuple[float, float], tuple[float, float]] | None = None
    drop_progress: float = 0.0  # 0..1 while backing out
    primary: HandTrack | None = None
    secondary: HandTrack | None = None


class Grammar:
    """Prepare-mode state machine over up to two tracked hands."""

    def __init__(self, frame_size: tuple[int, int], log: GestureLog | None = None):
        self.w, self.h = frame_size
        self.state = GestureState()
        self.cursor = RelativeCursor(frame_size)
        self.log = log or GestureLog()
        self.tracks: dict[str, HandTrack] = {}
        self._primary_key: str | None = None
        self._lost_since: float | None = None  # browse: primary hand missing
        self._gone_since: float | None = None  # focus: hand missing or dropped
        self._focus_armed = False  # browse: level shape seen since the last focus
        self._commit_armed_t: float | None = None  # focus: pinch released at this time
        self._tilt0: float | None = None
        self._d0: float | None = None

    # -- hands --

    def _assign(self, hands: list[Hand]) -> list[tuple[str, Hand]]:
        keyed, seen = [], set()
        for hand in hands:
            key = hand.handedness or "hand"
            while key in seen:
                key += "+"
            seen.add(key)
            keyed.append((key, hand))
        return keyed

    def _pick_primary(self, keyed: list[tuple[str, Hand]]) -> str | None:
        if not keyed:
            return None
        keys = [k for k, _ in keyed]
        if self._primary_key in keys:
            return self._primary_key
        bx0, by0, bx1, by1 = self.cursor.box
        in_box = [k for k, h in keyed if bx0 <= h.points[WRIST][0] <= bx1]
        if in_box:
            return in_box[0]
        return max(keyed, key=lambda kh: kh[1].points[WRIST][0])[0]

    # -- update --

    def update(self, hands: list[Hand], t: float) -> list[GestureEvent]:
        keyed = self._assign(hands)
        track_events: dict[str, list[str]] = {}
        for key, hand in keyed:
            track = self.tracks.setdefault(key, HandTrack())
            evs = track.update(hand, t, self.h)
            track_events[key] = evs
            for ev in evs:
                if ev == "pose":
                    self.log(t, "pose", hand=key, pose=track.stable)
                else:  # raw events; "pinch_lift" only becomes a commit when focused and armed
                    self.log(t, "pinch_lift" if ev == "commit" else ev, hand=key)
        present = {k for k, _ in keyed}
        for key in [k for k, tr in self.tracks.items()
                    if k not in present and t - tr.last_seen > TIMING.browse_lost_s]:
            del self.tracks[key]

        s = self.state
        self._primary_key = self._pick_primary(keyed)
        s.primary = self.tracks.get(self._primary_key) if self._primary_key in present else None
        s.secondary = next((self.tracks[k] for k, _ in keyed if k != self._primary_key), None)

        events: list[GestureEvent] = []
        if s.mode == "focus":
            self._update_focus(t, track_events, events)
        else:
            self._update_browse(t, track_events.get(self._primary_key, []), events)
        return events

    def _update_browse(self, t: float, primary_events: list[str], events: list[GestureEvent]) -> None:
        s, p = self.state, self.state.primary
        if p is None:
            s.scroll_rate = 0.0
            if s.mode == "browse":
                self._lost_since = t if self._lost_since is None else self._lost_since
                if t - self._lost_since >= TIMING.browse_lost_s:
                    s.mode, s.level, s.cursor = "idle", None, None
                    self.cursor.reset()
                    self.log(t, "idle")
            return
        self._lost_since = None

        if p.stable in LEVEL_OF_SHAPE:
            level = LEVEL_OF_SHAPE[p.stable]
            if s.mode != "browse" or s.level != level:
                s.mode, s.level = "browse", level
                self.log(t, "browse", level=level)
            self._focus_armed = True
        if s.mode != "browse":
            return

        # The cursor only follows the hand while it holds the level's shape,
        # so closing the hand to focus doesn't drag the highlight along.
        if p.raw == SHAPE_OF_LEVEL[s.level]:
            s.cursor = self.cursor.update(p.hand.point(INDEX_TIP), t)
            s.scroll_rate = self.cursor.scroll_rate
        else:
            s.scroll_rate = 0.0

        if not self._focus_armed:
            return
        if (s.level == "word" and p.stable == PINCH) or (s.level != "word" and "fold" in primary_events):
            self._enter_focus(t, events)

    def _enter_focus(self, t: float, events: list[GestureEvent]) -> None:
        s = self.state
        s.mode, s.op, s.scroll_rate = "focus", None, 0.0
        s.pointing, s.tone, s.stretch, s.stretch_ends, s.drop_progress = False, 0.0, 1.0, None, 0.0
        self._focus_armed = False
        self._commit_armed_t = None
        self._gone_since = None
        self._tilt0 = self._d0 = None
        events.append(GestureEvent("focus", t, s.level))
        self.log(t, "focus", level=s.level)

    def _leave_focus(self, kind: str, t: float, events: list[GestureEvent], **extra) -> None:
        s = self.state
        ev = GestureEvent(kind, t, s.level, s.op, **extra)
        events.append(ev)
        self.log(t, kind, level=s.level, op=s.op, value=ev.value)
        s.mode, s.op, s.pointing, s.stretch_ends, s.drop_progress = "browse", None, False, None, 0.0
        self._focus_armed = False
        self._lost_since = None

    def _update_focus(self, t: float, track_events: dict[str, list[str]], events: list[GestureEvent]) -> None:
        s, p = self.state, self.state.primary
        gone = "no hand" if p is None else "low" if p.hand.points[:, 1].min() > TIMING.drop_band * self.h else None
        if gone:
            if self._gone_since is None:
                self._gone_since = t
                self.log(t, "drop_start", reason=gone)
            s.drop_progress = min(1.0, (t - self._gone_since) / TIMING.drop_s)
            if s.drop_progress >= 1.0:
                self._leave_focus("back", t, events)
            return
        self._gone_since, s.drop_progress = None, 0.0

        # Only a pinch that starts after the hand has opened again commits,
        # so the pinch or fold that focused can't commit by itself.
        if self._commit_armed_t is None and not p.pinching:
            self._commit_armed_t = t
        if self._commit_armed_t is not None:
            for key, evs in track_events.items():
                tr = self.tracks[key]
                if "commit" in evs and tr.pinch_start is not None and tr.pinch_start >= self._commit_armed_t:
                    value = s.tone if s.op == "tone" else s.stretch if s.op == "stretch" else None
                    self._leave_focus("commit", t, events, value=value)
                    return

        self._operate(t, events)

    def _operate(self, t: float, events: list[GestureEvent]) -> None:
        s, p, q = self.state, self.state.primary, self.state.secondary
        if s.level == "word":
            if p.stable == OPEN and s.op != "ring":
                s.op = "ring"
                self.log(t, "op", op="ring")
            if s.op == "ring":
                s.pointing = p.stable == L
                if p.raw == L:
                    s.cursor = self.cursor.update(p.hand.point(INDEX_TIP), t)
        elif s.level == "sentence":
            if p.stable == L and p.raw == L:
                tilt = p.feat.tilt
                if s.op != "tone":
                    s.op, self._tilt0 = "tone", tilt
                    self.log(t, "op", op="tone")
                elif self._tilt0 is None:  # dial picked up again: continue from its value
                    self._tilt0 = tilt - s.tone * OPS.tone_range_deg
                s.tone = _clamp((tilt - self._tilt0) / OPS.tone_range_deg, -1.0, 1.0)
            else:
                self._tilt0 = None
        elif s.level == "paragraph":
            if q is not None and p.stable == L and q.stable == L and p.raw == L and q.raw == L:
                a, b = p.hand.point(INDEX_TIP), q.hand.point(INDEX_TIP)
                d = math.dist(a, b) / ((p.hand.size + q.hand.size) / 2)
                if s.op != "stretch":
                    s.op, self._d0 = "stretch", d
                    self.log(t, "op", op="stretch")
                elif self._d0 is None:
                    self._d0 = d / s.stretch
                s.stretch = _clamp(d / self._d0, OPS.stretch_min, OPS.stretch_max)
                s.stretch_ends = (a, b)
            else:
                self._d0, s.stretch_ends = None, None


# --- drawing -------------------------------------------------------------------

YELLOW = (0, 215, 255)
CYAN = (255, 230, 0)


def draw_landmarks(frame: np.ndarray, hand: Hand) -> None:
    pts = hand.points.astype(int)
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, tuple(pts[a]), tuple(pts[b]), (200, 200, 200), 2, cv2.LINE_AA)
    for p in pts:
        cv2.circle(frame, tuple(p), 3, (255, 255, 255), -1, cv2.LINE_AA)


def draw_fingertips(frame: np.ndarray, state: GestureState) -> None:
    """Yellow dot on the active fingertip, small dots on the rest; cyan for a second hand."""
    for track, color in ((state.secondary, CYAN), (state.primary, YELLOW)):
        if track is None or track.hand is None:
            continue
        for i in TIPS:
            c = tuple(int(v) for v in track.hand.points[i])
            cv2.circle(frame, c, 9 if i == INDEX_TIP else 4, color, -1, cv2.LINE_AA)


def draw_hand_box(frame: np.ndarray, cursor: RelativeCursor) -> None:
    x0, y0, x1, y1 = (int(v) for v in cursor.box)
    band = int((y1 - y0) * CURSOR.edge_band)
    cv2.rectangle(frame, (x0, y0), (x1, y1), (160, 160, 160), 1, cv2.LINE_AA)
    for y in (y0 + band, y1 - band):
        cv2.line(frame, (x0, y), (x1, y), (100, 100, 100), 1, cv2.LINE_AA)
    if cursor.uv is not None:
        u, v = cursor.uv
        cv2.circle(frame, (int(x0 + u * (x1 - x0)), int(y0 + v * (y1 - y0))), 5, (160, 160, 160), 1, cv2.LINE_AA)


def _debug_view() -> None:
    from palmcards.capture import Camera

    tracker = HandTracker()
    log = GestureLog.to_session_dir()
    with Camera() as cam:
        frame = cam.read()
        h, w = frame.shape[:2]
        grammar = Grammar((w, h), log)
        t0 = time.perf_counter()
        while True:
            frame = cam.read()
            tracker.submit(frame, time.perf_counter() - t0)
            if (result := tracker.poll()) is not None:
                grammar.update(*result)
            s = grammar.state
            draw_hand_box(frame, grammar.cursor)
            for track in (s.primary, s.secondary):
                if track is not None:
                    draw_landmarks(frame, track.hand)
            draw_fingertips(frame, s)

            op = f"  op {s.op}" if s.op else ""
            lines = [f"{s.mode.upper()} {s.level or ''}{op}  tone {s.tone:+.2f}  stretch {s.stretch:.2f}"
                     f"  drop {s.drop_progress:.0%}",
                     f"camera {cam.fps:4.1f} fps  hands {tracker.latency_ms:4.1f} ms"]
            for name, track in (("primary", s.primary), ("second", s.secondary)):
                if track is None or track.feat is None:
                    continue
                f = track.feat
                ext = "".join("x" if e else "." for e in f.extended)
                lines += [
                    f"{name}: {track.stable:5} (raw {track.raw:5})  mp {track.hand.gesture}  {track.hand.handedness}",
                    f"  ext {ext} thumb {'out' if f.thumb_out else 'in '} pinch {f.pinch_dist:.2f} "
                    f"spread {f.spread:.2f} tilt {f.tilt:+4.0f} L {f.thumb_index_angle:3.0f}",
                ]
            if s.primary is None:
                lines.append("no hand")
            for i, text in enumerate(lines + [""] + log.tail(8)):
                cv2.putText(frame, text, (20, 32 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow("PalmCards gestures (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    log.close()
    tracker.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    _debug_view()
