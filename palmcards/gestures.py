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
  second shape operates     OPEN = options ring, L turn = ring knob (word); L tilt = tone dial,
                            OPEN = suggested marks, then L turn = knob through them and a pinch
                            (no lift) accepts or rejects one (sentence); two L hands = length
                            stretch (paragraph)
  pinch + lift commits      back to Browse at the same level
  drop the hand backs out   out of frame or below the bottom band for 1 s

The cursor is relative: a hand box on the right of the frame maps onto the
text box on the left; its top and bottom bands scroll.

Around the grammar, ModeMachine runs the app's modes: a fist held 1 s starts
a take after a 3-2-1 count-in; in Rehearse only the command zone (top right)
listens, for a flick (next section) and an open palm held 1.5 s (stop, on to
Review). In Review the same open palm in the zone goes back to Prepare.

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

from palmcards.config import CURSOR, OPS, POSE, REHEARSE, TIMING, TRACKING
from palmcards.paths import data_dir

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
LOG_DIR = data_dir() / "gesture-logs"

# MediaPipe hand landmark indices.
WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_PIP, PINKY_TIP = 18, 20
FINGERS = ((INDEX_PIP, INDEX_TIP), (MIDDLE_PIP, MIDDLE_TIP), (RING_PIP, RING_TIP), (PINKY_PIP, PINKY_TIP))
TIPS = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
PALM = (WRIST, INDEX_MCP, MIDDLE_MCP, 13, 17)  # wrist and the four finger MCPs
FINGER_TIPS = (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)

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
        # First stable pose other than NONE since the hand came into view: a
        # take starts only from a fist raised as such, not one formed mid-gesture.
        self.first_pose: str | None = None
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
            if self.first_pose is None and self.stable != NONE:
                self.first_pose = self.stable
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
    # Grammar: "focus" | "commit" | "back" | "toggle" (a pinch without a lift on
    # spread marks: accept or reject the current one). Modes: "count_in" | "drill" (a
    # count-in for one sentence) | "count_in_cancel" | "take_start" |
    # "next_section" | "previous_section" | "take_stop" | "to_prepare".
    kind: str
    t: float
    level: str | None = None
    op: str | None = None
    value: float | None = None  # tone (-1 cold .. 1 warm) or length ratio
    source: str | None = None  # a mode event from a key rather than a gesture: "key"
    sentence: int | None = None  # "drill": the sentence it rehearses, set when the drill is decided


@dataclass
class GestureState:
    mode: str = "idle"  # idle | browse | focus
    level: str | None = None  # word | sentence | paragraph
    op: str | None = None  # ring | tone | marks | stretch (Prepare), take (Review's take dial)
    cursor: tuple[float, float] | None = None  # (u, v) in the hand box
    scroll_rate: float = 0.0  # rows per second
    pointing: bool = False  # L-hand turning the knob (ring, or spread marks)
    knob: int = 0  # knob steps turned since the focus; ring node = knob mod nodes, marks: the app clamps
    tone: float = 0.0  # -1 cold .. 1 warm
    stretch: float = 1.0  # length ratio
    take_step: int = 0  # Review: take-dial steps turned since the focus (clamped to dial_limits)
    dial_limits: tuple[int, int] | None = None  # set by the app: the range of the marks knob or the take dial
    closing: bool = False  # the hand working a dial is closing into a pinch: the dial is held (and shown so)
    stretch_ends: tuple[tuple[float, float], tuple[float, float]] | None = None
    drop_progress: float = 0.0  # 0..1 while backing out
    primary: HandTrack | None = None
    secondary: HandTrack | None = None


class Grammar:
    """Browse/focus state machine over up to two tracked hands (Prepare and Review)."""

    def __init__(self, frame_size: tuple[int, int], log: GestureLog | None = None):
        self.w, self.h = frame_size
        self.state = GestureState()
        self.cursor = RelativeCursor(frame_size)
        self.log = log or GestureLog()
        self.tracks: dict[str, HandTrack] = {}
        self.operations = True  # Prepare's ring, tone and stretch; off in Review
        self.take_dial = False  # Review's take dial on a focused sentence
        self._primary_key: str | None = None
        self._lost_since: float | None = None  # browse: primary hand missing
        self._gone_since: float | None = None  # focus: hand missing or dropped
        self._focus_armed = False  # browse: level shape seen since the last focus
        self._commit_armed_t: float | None = None  # focus: pinch released at this time
        self._tilt0: float | None = None
        self._d0: float | None = None
        self._knob0: float | None = None
        self._toggle_armed = False  # spread marks: a pinch (after the hand opened again) is being held
        self._dial_hist: deque[dict] = deque()  # focus: the dials' values per frame, for the rewind

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

    def reset(self) -> None:
        """Forget browse and focus; the hand tracks stay."""
        s = self.state
        self.state = GestureState(primary=s.primary, secondary=s.secondary)
        self.cursor.reset()
        self._lost_since = self._gone_since = None
        self._focus_armed = False
        self._commit_armed_t = None
        self._tilt0 = self._d0 = self._knob0 = None
        self._toggle_armed = False

    def update(self, hands: list[Hand], t: float) -> list[GestureEvent]:
        return self.step(self.track(hands, t), t)

    def step(self, track_events: dict[str, list[str]], t: float) -> list[GestureEvent]:
        """Run browse/focus on hands already passed through track()."""
        events: list[GestureEvent] = []
        if self.state.mode == "focus":
            self._update_focus(t, track_events, events)
        else:
            self._update_browse(t, track_events.get(self._primary_key, []), events)
        return events

    def track(self, hands: list[Hand], t: float) -> dict[str, list[str]]:
        """Update the hand tracks, log their poses and pick the primary hand.

        Returns the raw track events of each hand present this frame.
        """
        keyed = self._assign(hands)
        present = {k for k, _ in keyed}
        track_events: dict[str, list[str]] = {}
        for key, hand in keyed:
            if key not in self.tracks:
                self.tracks[key] = track = HandTrack()
                # MediaPipe flips a hand's label during fast moves: a "new" hand
                # where one just vanished is the same hand, and keeps its history.
                if (old := self._vanished_near(hand, present)) is not None:
                    track.first_pose = old.first_pose
            track = self.tracks[key]
            evs = track.update(hand, t, self.h)
            track_events[key] = evs
            for ev in evs:
                if ev == "pose":
                    self.log(t, "pose", hand=key, pose=track.stable)
                else:  # raw events; "pinch_lift" only becomes a commit when focused and armed
                    self.log(t, "pinch_lift" if ev == "commit" else ev, hand=key)
        for key in [k for k, tr in self.tracks.items()
                    if k not in present and t - tr.last_seen > TIMING.browse_lost_s]:
            del self.tracks[key]

        s = self.state
        self._primary_key = self._pick_primary(keyed)
        s.primary = self.tracks.get(self._primary_key) if self._primary_key in present else None
        s.secondary = next((self.tracks[k] for k, _ in keyed if k != self._primary_key), None)
        return track_events

    def _vanished_near(self, hand: Hand, present: set[str]) -> HandTrack | None:
        """A recently seen track, missing this frame, whose wrist was close to this hand's."""
        for key, track in self.tracks.items():
            if key in present or track.hand is None:
                continue
            gap = float(np.linalg.norm(track.hand.points[WRIST] - hand.points[WRIST]))
            if gap < TIMING.handover_palms * hand.size:
                return track
        return None

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
        s.pointing, s.knob, s.tone, s.stretch, s.stretch_ends, s.drop_progress = False, 0, 0.0, 1.0, None, 0.0
        s.take_step = 0
        self._focus_armed = False
        self._commit_armed_t = None
        self._gone_since = None
        self._tilt0 = self._d0 = self._knob0 = None
        self._toggle_armed = False
        self._dial_hist.clear()
        s.closing = False
        events.append(GestureEvent("focus", t, s.level))
        self.log(t, "focus", level=s.level)

    def _leave_focus(self, kind: str, t: float, events: list[GestureEvent], **extra) -> None:
        s = self.state
        ev = GestureEvent(kind, t, s.level, s.op, **extra)
        events.append(ev)
        self.log(t, kind, level=s.level, op=s.op, value=ev.value)
        s.mode, s.op, s.pointing, s.stretch_ends, s.drop_progress = "browse", None, False, None, 0.0
        s.closing = False
        self._focus_armed = False
        self._toggle_armed = False
        self._lost_since = None

    def _update_focus(self, t: float, track_events: dict[str, list[str]], events: list[GestureEvent]) -> None:
        s, p = self.state, self.state.primary
        gone = "no hand" if p is None else "low" if p.hand.points[:, 1].min() > TIMING.drop_band * self.h else None
        if gone:
            self._toggle_armed = False  # a pinch the hand took out of view doesn't toggle
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

        self._guard_pinch(t)
        if self.operations:
            self._operate(t, events)
        elif self.take_dial and s.level == "sentence":
            self._dial_takes(t)
        self._remember(t)

    # -- dials and pinches: curling the index to pinch must not turn a dial --

    DIALS = ("ring", "marks", "tone", "stretch", "take")

    def _dial_hands(self) -> list[HandTrack]:
        s = self.state
        hands = [s.primary] + ([s.secondary] if s.op == "stretch" and s.secondary is not None else [])
        return [h for h in hands if h is not None and h.feat is not None]

    def _guard_pinch(self, t: float) -> None:
        """A hand working a dial that starts closing into a pinch (thumb tip near
        the index tip, or a pinch registered) rewinds the dial to where it was
        before the thumb moved in, and holds it (state.closing) until the thumb
        opens again. See config OPS.closing_enter."""
        s = self.state
        if s.op not in self.DIALS:
            s.closing = False
            return
        limit = OPS.closing_leave if s.closing else OPS.closing_enter
        closing = any(h.pinching or h.feat.pinch_dist < limit for h in self._dial_hands())
        if closing and not s.closing:
            self._rewind(t)
        if closing != s.closing:
            s.closing = closing
            self._knob0 = self._tilt0 = self._d0 = None  # when the dial goes on, it picks up from its value
        if closing:
            s.pointing = False

    def _remember(self, t: float) -> None:
        s = self.state
        if s.op not in self.DIALS:
            return
        thumb = min((h.feat.pinch_dist for h in self._dial_hands()), default=0.0)
        self._dial_hist.append({"t": t, "thumb": thumb, "knob": s.knob, "tone": s.tone, "stretch": s.stretch,
                                "take_step": s.take_step})
        while self._dial_hist and self._dial_hist[0]["t"] < t - 1.0:
            self._dial_hist.popleft()

    def _rewind(self, t: float) -> None:
        """The dials back to their values from just before the thumb started
        closing: the latest moment in the last OPS.rewind_max_s with the thumb
        within OPS.rewind_plateau palms of its farthest from the index tip."""
        recent = [e for e in self._dial_hist if e["t"] >= t - OPS.rewind_max_s]
        if not recent:
            return
        top = max(e["thumb"] for e in recent)
        back = [e for e in recent if e["thumb"] >= top - OPS.rewind_plateau][-1]
        s = self.state
        before = {k: getattr(s, k) for k in ("knob", "tone", "stretch", "take_step")}
        s.knob, s.tone, s.stretch, s.take_step = back["knob"], back["tone"], back["stretch"], back["take_step"]
        changed = {k: [v, getattr(s, k)] for k, v in before.items() if v != getattr(s, k)}
        if changed:
            self.log(t, "rewind", op=s.op, back_s=round(t - back["t"], 3), **changed)

    def _clamp_dial(self, name: str, tilt: float, step_deg: float) -> None:
        """Keep an integer dial in state.dial_limits; turning past an end moves the
        dial's zero along, so turning back moves at once."""
        s, lim = self.state, self.state.dial_limits
        value = getattr(s, name)
        if lim is None or lim[0] > lim[1] or lim[0] <= value <= lim[1]:
            return
        value = min(max(value, lim[0]), lim[1])
        setattr(s, name, value)
        self._knob0 = tilt - value * step_deg

    def _holds_l(self, track: HandTrack | None, started: bool) -> bool:
        """An L starts a control; once started, any shape with the index up
        keeps it going, so a thumb drifting in mid-movement doesn't freeze it."""
        if track is None:
            return False
        if not started:
            return track.stable == L
        return track.feat.extended[0] and not track.pinching and track.raw in (L, ONE, NONE)

    def _turn_knob(self, p: HandTrack) -> None:
        """A knob: turning the L-hand steps `knob`, relative to the angle it had
        when it appeared, with a little hysteresis so a step doesn't flicker at
        a boundary; picked up again, it continues from where it was. A pinch
        stops it (the hand isn't an L then), so pinching doesn't turn it."""
        s = self.state
        s.pointing = not s.closing and self._holds_l(p, self._knob0 is not None)
        if s.pointing:
            tilt = p.feat.tilt
            if self._knob0 is None:
                self._knob0 = tilt - s.knob * OPS.knob_step_deg
            pos = (tilt - self._knob0) / OPS.knob_step_deg
            if abs(pos - s.knob) > 0.5 + OPS.knob_hysteresis:
                s.knob = round(pos)
            if s.op == "marks":  # the ring wraps; the marks stop at their ends
                self._clamp_dial("knob", tilt, OPS.knob_step_deg)
        else:
            self._knob0 = None

    def _watch_toggle(self, t: float, p: HandTrack, events: list[GestureEvent]) -> None:
        """Spread marks: a pinch held (stable) and let go without a lift is a
        "toggle" (a lift is the commit, which leaves the focus first). Only a
        pinch that starts after the hand opened again counts, as for the
        commit, so the fold that focused can't toggle."""
        armed = self._commit_armed_t
        if p.stable == PINCH and p.pinch_start is not None and armed is not None and p.pinch_start >= armed:
            self._toggle_armed = True
        elif self._toggle_armed and p.stable != PINCH:
            self._toggle_armed = False
            s = self.state
            events.append(GestureEvent("toggle", t, s.level, s.op))
            self.log(t, "toggle", level=s.level, knob=s.knob)

    def open_marks(self, t: float) -> bool:
        """Spread the suggested marks on a focused sentence (an open palm, or the
        `m` key). Returns whether it did (Prepare, focused on a sentence)."""
        s = self.state
        if not (self.operations and s.mode == "focus" and s.level == "sentence"):
            return False
        if s.op != "marks":
            s.op, s.tone, self._tilt0 = "marks", 0.0, None
            self.log(t, "op", op="marks")
        return True

    def _dial_takes(self, t: float) -> None:
        """Review: an L-hand turned like a knob steps through the takes,
        relative to its angle when it appears, like the options-ring knob."""
        s, p = self.state, self.state.primary
        if not s.closing and self._holds_l(p, self._knob0 is not None):
            tilt = p.feat.tilt
            if s.op != "take":
                s.op = "take"
                self.log(t, "op", op="take")
            if self._knob0 is None:  # picked up again: continue from where it was
                self._knob0 = tilt - s.take_step * OPS.take_step_deg
            pos = (tilt - self._knob0) / OPS.take_step_deg
            if abs(pos - s.take_step) > 0.5 + OPS.knob_hysteresis:
                s.take_step = round(pos)
            self._clamp_dial("take_step", tilt, OPS.take_step_deg)
        else:
            self._knob0 = None

    def _operate(self, t: float, events: list[GestureEvent]) -> None:
        s, p, q = self.state, self.state.primary, self.state.secondary
        if s.level == "word":
            if p.stable == OPEN and s.op != "ring":
                s.op = "ring"
                self.log(t, "op", op="ring")
            if s.op == "ring":
                self._turn_knob(p)
        elif s.level == "sentence":
            # An open palm spreads suggested marks, like the word ring; from then
            # on the L-hand no longer turns the tone dial (a tone preview is
            # dropped: nothing was sent for it).
            if p.stable == OPEN and s.op != "marks":
                self.open_marks(t)
            if s.op == "marks":
                self._turn_knob(p)
                self._watch_toggle(t, p, events)
                return
            if not s.closing and self._holds_l(p, self._tilt0 is not None):
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
            started = self._d0 is not None
            if not s.closing and self._holds_l(p, started) and self._holds_l(q, started):
                a, b = p.hand.point(INDEX_TIP), q.hand.point(INDEX_TIP)
                d = math.dist(a, b) / ((p.hand.size + q.hand.size) / 2)
                if s.op != "stretch":
                    s.op, self._d0 = "stretch", d
                    self.log(t, "op", op="stretch")
                elif self._d0 is None:
                    self._d0 = d / s.stretch
                s.stretch = _clamp(d / self._d0, OPS.stretch_min, OPS.stretch_max)
                s.stretch_ends = (a, b)
            elif not s.closing:
                self._d0, s.stretch_ends = None, None
            else:
                self._d0 = None  # held while a hand closes; the line stays


# --- rehearse: command zone and modes ------------------------------------------

def palm_center(hand: Hand) -> tuple[float, float]:
    x, y = hand.points[list(PALM)].mean(axis=0)
    return float(x), float(y)


class CommandZone:
    """Commands made by a hand whose palm is inside the zone (top right).

      "flick"  the hand, settled in the zone, swings sideways fast
      "hold"   open palm held in the zone (stop a take, cancel the count-in,
               leave Review for Prepare)

    The flick is measured at the fingertips, which travel furthest when the
    hand swings from the wrist, and may carry the hand out of the zone: it
    counts if it started inside. The zone follows one hand by position, not
    by MediaPipe's handedness label (which flips during fast moves), and
    rides out brief tracking dropouts.
    """

    def __init__(self, frame_size: tuple[int, int]):
        w, h = frame_size
        x0, y0, x1, y1 = REHEARSE.zone
        self.box = (x0 * w, y0 * h, x1 * w, y1 * h)
        self.reset()

    def reset(self) -> None:
        self.active = False  # the followed hand is inside the zone now
        self.hold_progress = 0.0  # open palm, 0..1
        self.flick_progress = 0.0  # sideways travel toward a flick, 0..1
        self._pos: tuple[float, float] | None = None  # followed hand's palm centre
        self._seen = self._entered = self._last_inside = 0.0
        self._path: deque[tuple[float, np.ndarray, float, bool]] = deque()  # (t, fingertips, palm, inside)
        self._open_since: float | None = None
        self._last_open = -math.inf
        self._cooldown_until = -math.inf

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1

    def _pick(self, hands: list[Hand]) -> Hand | None:
        if self._pos is not None and hands:
            near = min(hands, key=lambda h: math.dist(palm_center(h), self._pos))
            if math.dist(palm_center(near), self._pos) < REHEARSE.follow_palms * near.size:
                return near
        return next((h for h in hands if self.contains(*palm_center(h))), None)

    def update(self, tracks: dict[str, HandTrack], t: float) -> list[str]:
        followed = self._pos is not None
        hand = self._pick([tr.hand for tr in tracks.values()])
        if followed and hand is not None and math.dist(palm_center(hand), self._pos) >= REHEARSE.follow_palms * hand.size:
            self.reset()  # a different hand in the zone: start over with it
            followed = False
        if hand is None:
            self.active = False
            if followed and t - self._seen > REHEARSE.dropout_s:
                self.reset()
            return []
        track = next(tr for tr in tracks.values() if tr.hand is hand)
        pc = palm_center(hand)
        inside = self.contains(*pc)
        if not followed:
            self._entered = self._last_inside = t
        if inside:
            self._last_inside = t
        elif t - self._last_inside > REHEARSE.flick_window_s:  # wandered off: let it go
            self.reset()
            return []
        self._pos, self._seen, self.active = pc, t, inside

        events: list[str] = []
        # Samples from before the hand settled are left out, so sweeping a
        # hand into the zone isn't a flick.
        if t - self._entered >= REHEARSE.settle_s - 1e-9:
            self._path.append((t, hand.points[list(FINGER_TIPS)].copy(), hand.size, inside))
        while self._path and self._path[0][0] < t - REHEARSE.flick_window_s:
            self._path.popleft()
        self.flick_progress = 0.0
        if self._path and self._path[0][3] and t >= self._cooldown_until:
            _, tips0, palm0, _ = self._path[0]
            # The fingertip that travelled furthest sideways: an extended
            # finger swinging from the wrist moves most.
            moved = (hand.points[list(FINGER_TIPS)] - tips0) / palm0
            dx, dy = moved[int(np.argmax(np.abs(moved[:, 0])))]
            self.flick_progress = min(1.0, abs(dx) / REHEARSE.flick_dist)
            if abs(dx) > REHEARSE.flick_dist and abs(dx) > REHEARSE.flick_straightness * abs(dy):
                events.append("flick")
                self._cooldown_until = t + REHEARSE.flick_cooldown_s
                self._path.clear()
                self.flick_progress = 0.0

        # The open palm counts frame by frame (raw pose), forgiving short
        # misreads, so a label flip or a blurry frame doesn't restart it.
        if inside and track.raw == OPEN and t >= self._cooldown_until:
            self._last_open = t
            if self._open_since is None:
                self._open_since = t
        elif t - self._last_open > REHEARSE.hold_grace_s:
            self._open_since = None
        if self._open_since is None:
            self.hold_progress = 0.0
        else:
            self.hold_progress = min(1.0, (t - self._open_since) / REHEARSE.hold_s)
            if self.hold_progress >= 1.0:
                events.append("hold")
                self._open_since, self.hold_progress = None, 0.0
        return events


class ModeMachine:
    """The app's modes around the Prepare grammar.

      prepare, review  --fist held 1 s-->  count_in  --3 s-->  rehearse
      review    --pinch + lift on a focused sentence-->  count_in (a drill)
      rehearse  --open palm held in the zone-->  review
      count_in  --open palm held in the zone-->  back where it came from
      review    --open palm held in the zone-->  prepare

    Prepare and Review run the grammar (Review without Prepare's operations,
    with the take dial instead). A drill rehearses one sentence: the same
    count-in and recording, but no flick to the next section.
    In count_in and rehearse the hands only act inside the command zone; they
    are still tracked, so every pose is logged.
    """

    def __init__(self, frame_size: tuple[int, int], log: GestureLog | None = None):
        self.grammar = Grammar(frame_size, log)
        self.log = self.grammar.log
        self.zone = CommandZone(frame_size)
        self.mode = "prepare"  # prepare | count_in | rehearse | review
        self.start_progress = 0.0  # fist hold, 0..1
        self.count_in_end = 0.0
        self.drill = False  # the count-in or take is a drill
        self._back_to = "prepare"
        self._fist_since: float | None = None

    @property
    def state(self) -> GestureState:
        return self.grammar.state

    def update(self, hands: list[Hand], t: float) -> list[GestureEvent]:
        track_events = self.grammar.track(hands, t)
        events: list[GestureEvent] = []
        if self.mode in ("prepare", "review"):
            events += self.grammar.step(track_events, t)
            if self.mode == "review" and any(e.kind == "commit" and e.level == "sentence" for e in events):
                # The app fills in `sentence` (the focused one) before any handler clears the focus.
                events.append(GestureEvent("drill", t, "sentence"))
                self._enter("count_in", t)
                self.drill = True
                return events
            if self._fist_held(t):
                events.append(GestureEvent("count_in", t))
                self._enter("count_in", t)
                return events
        if self.mode == "prepare":
            return events

        for command in self.zone.update({k: self.grammar.tracks[k] for k in track_events}, t):
            self.log(t, "zone", command=command)
            if command == "flick" and self.mode == "rehearse" and not self.drill:
                events.append(GestureEvent("next_section", t))
            elif command == "hold" and self.mode == "rehearse":
                events.append(GestureEvent("take_stop", t))
                self._enter("review", t)
            elif command == "hold" and self.mode == "count_in":
                events.append(GestureEvent("count_in_cancel", t))
                self._enter(self._back_to, t)
            elif command == "hold" and self.mode == "review":
                events.append(GestureEvent("to_prepare", t))
                self._enter("prepare", t)
        return events

    def tick(self, t: float) -> list[GestureEvent]:
        """Call every displayed frame: ends the count-in on time."""
        if self.mode == "count_in" and t >= self.count_in_end:
            self._enter("rehearse", t)
            return [GestureEvent("take_start", t)]
        return []

    def command(self, name: str, t: float) -> list[GestureEvent]:
        """The transitions the gestures make, for the keyboard fallback (usable
        when hand tracking isn't): "start" a take, "stop" it (or cancel the
        count-in), "next" section, back to "prepare" from Review. Anything
        else, or a command the mode doesn't take, does nothing."""
        before = self.mode
        events: list[GestureEvent] = []
        if name == "start" and self.mode in ("prepare", "review"):
            events.append(GestureEvent("count_in", t))
            self._enter("count_in", t)
        elif name == "stop" and self.mode == "rehearse":
            events.append(GestureEvent("take_stop", t))
            self._enter("review", t)
        elif name == "stop" and self.mode == "count_in":
            events.append(GestureEvent("count_in_cancel", t))
            self._enter(self._back_to, t)
        elif name == "next" and self.mode == "rehearse" and not self.drill:
            events.append(GestureEvent("next_section", t, source="key"))
        elif name == "previous" and self.mode == "rehearse" and not self.drill:
            events.append(GestureEvent("previous_section", t, source="key"))
        elif name == "prepare" and self.mode == "review":
            events.append(GestureEvent("to_prepare", t))
            self._enter("prepare", t)
        self.log(t, "key", command=name, mode=before, acted=bool(events))
        return events

    def enter(self, mode: str, t: float) -> None:
        """Jump straight into a mode, e.g. to replay a recording made in Rehearse."""
        self._enter(mode, t)

    def cancel_count_in(self, t: float) -> None:
        """For the app, e.g. when the microphone can't be opened."""
        if self.mode == "count_in":
            self._enter(self._back_to, t)

    def _fist_held(self, t: float) -> bool:
        """A fist raised into view as a fist, held. One formed from another pose
        (a slow pinch, a flat hand curling, a hand resting closed between
        gestures) doesn't count: those started takes by mistake."""
        p = self.grammar.state.primary
        if p is None or p.stable != FIST or p.first_pose != FIST or self.grammar.state.mode == "focus":
            self._fist_since, self.start_progress = None, 0.0
            return False
        if self._fist_since is None:
            self._fist_since = t
        self.start_progress = min(1.0, (t - self._fist_since) / REHEARSE.start_hold_s)
        return self.start_progress >= 1.0

    def _enter(self, mode: str, t: float) -> None:
        if mode == "count_in":
            self._back_to = self.mode
            self.count_in_end = t + REHEARSE.count_in_s
        self.mode = mode
        self.drill = self.drill and mode in ("count_in", "rehearse")
        self.grammar.reset()
        self.grammar.operations = mode == "prepare"
        self.grammar.take_dial = mode == "review"
        self.zone.reset()
        self._fist_since, self.start_progress = None, 0.0
        self.log(t, "mode", mode=mode)


# --- debug view (drawing is in palmcards.render) ----------------------------------

def _debug_view() -> None:
    from palmcards.capture import Camera
    from palmcards.render import draw_debug_text, draw_fingertips, draw_hand_box, draw_landmarks, draw_zone_outline

    tracker = HandTracker()
    log = GestureLog.to_session_dir()
    with Camera() as cam:
        frame = cam.read()
        h, w = frame.shape[:2]
        modes = ModeMachine((w, h), log)
        t0 = time.perf_counter()
        while True:
            frame = cam.read()
            tracker.submit(frame, time.perf_counter() - t0)
            if (result := tracker.poll()) is not None:
                modes.update(*result)
            modes.tick(time.perf_counter() - t0)
            s = modes.state
            if modes.mode != "prepare":
                draw_zone_outline(frame, modes.zone)
            else:
                draw_hand_box(frame, modes.grammar.cursor)
            for track in (s.primary, s.secondary):
                if track is not None:
                    draw_landmarks(frame, track.hand)
            draw_fingertips(frame, s)

            op = f"  op {s.op}" if s.op else ""
            lines = [f"{modes.mode.upper()}  start {modes.start_progress:.0%}  hold {modes.zone.hold_progress:.0%}"
                     f"  flick {modes.zone.flick_progress:.0%}",
                     f"{s.mode.upper()} {s.level or ''}{op}  tone {s.tone:+.2f}  stretch {s.stretch:.2f}"
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
            draw_debug_text(frame, lines + [""] + log.tail(8))
            cv2.imshow("PalmCards gestures (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    log.close()
    tracker.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    _debug_view()
