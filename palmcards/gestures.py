"""Landmarks -> gesture events, mode-aware state machine, command zone.

All coordinates are pixels in the mirrored frame (the frame is flipped once at
capture), so hit-testing against the rendered text needs no conversion.

Milestone 3 covers Prepare mode: point to hover, pinch-tap to select a word,
pinch-drag vertically to scroll, open palm held 1 s to cancel.

Run `python -m palmcards.gestures` for a debug view with landmarks, the
recognizer's gesture name, pinch ratio and the Prepare state machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# MediaPipe hand landmark indices.
WRIST = 0
THUMB_TIP = 4
INDEX_PIP, INDEX_TIP = 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_PIP, PINKY_TIP = 18, 20

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]


@dataclass
class Hand:
    points: np.ndarray  # (21, 2) float pixels, mirrored frame
    gesture: str = "None"  # recognizer category, e.g. "Open_Palm"
    score: float = 0.0
    handedness: str = ""

    def dist(self, a: int, b: int) -> float:
        return float(np.linalg.norm(self.points[a] - self.points[b]))

    @property
    def size(self) -> float:
        """Wrist to middle knuckle: a scale reference that ignores finger pose."""
        return max(self.dist(WRIST, MIDDLE_MCP), 1e-6)

    @property
    def pinch_ratio(self) -> float:
        return self.dist(THUMB_TIP, INDEX_TIP) / self.size

    @property
    def pinch_point(self) -> tuple[float, float]:
        x, y = (self.points[THUMB_TIP] + self.points[INDEX_TIP]) / 2
        return float(x), float(y)

    @property
    def index_tip(self) -> tuple[float, float]:
        x, y = self.points[INDEX_TIP]
        return float(x), float(y)

    def extended(self, pip: int, tip: int) -> bool:
        # Works in any direction, unlike the recognizer's Pointing_Up class.
        return self.dist(WRIST, tip) > 1.15 * self.dist(WRIST, pip)

    @property
    def is_pointing(self) -> bool:
        if not self.extended(INDEX_PIP, INDEX_TIP):
            return False
        curled = sum(
            not self.extended(pip, tip)
            for pip, tip in ((MIDDLE_PIP, MIDDLE_TIP), (RING_PIP, RING_TIP), (PINKY_PIP, PINKY_TIP))
        )
        return curled >= 2

    @property
    def is_open_palm(self) -> bool:
        return self.gesture == "Open_Palm" and self.score >= 0.5


class HandTracker:
    """MediaPipe Gesture Recognizer in VIDEO mode: landmarks plus gesture class."""

    def __init__(self, num_hands: int = 1, max_side: int = 640):
        from mediapipe.tasks.python import BaseOptions, vision

        model = MODELS_DIR / "gesture_recognizer.task"
        if not model.exists():
            raise FileNotFoundError(f"{model} missing: run python scripts/download_models.py")
        self.max_side = max_side
        self._last_ms = -1
        self._recognizer = vision.GestureRecognizer.create_from_options(
            vision.GestureRecognizerOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=num_hands,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        )

    def detect(self, frame_bgr: np.ndarray, t: float) -> list[Hand]:
        import mediapipe as mp

        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        small = cv2.resize(frame_bgr, None, fx=scale, fy=scale) if scale < 1 else frame_bgr
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        # VIDEO mode needs strictly increasing integer timestamps.
        ms = max(int(t * 1000), self._last_ms + 1)
        self._last_ms = ms
        result = self._recognizer.recognize_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms)

        hands = []
        for i, lms in enumerate(result.hand_landmarks):
            pts = np.array([(lm.x * w, lm.y * h) for lm in lms], dtype=np.float32)
            gesture, score = "None", 0.0
            if i < len(result.gestures) and result.gestures[i]:
                gesture, score = result.gestures[i][0].category_name, result.gestures[i][0].score
            side = result.handedness[i][0].category_name if i < len(result.handedness) else ""
            hands.append(Hand(pts, gesture, score, side))
        return hands

    def close(self) -> None:
        self._recognizer.close()


# --- smoothing ---------------------------------------------------------------

class OneEuro:
    """One Euro filter: smooths jitter when still, stays responsive when moving."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.02, d_cutoff: float = 1.0):
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


# --- Prepare mode -------------------------------------------------------------

@dataclass
class GestureEvent:
    kind: str  # "hover" | "select" | "scroll" | "cancel"
    x: float = 0.0
    y: float = 0.0
    dy: float = 0.0  # scroll: vertical pixels moved since last event


@dataclass
class PrepareGestures:
    """Turns one tracked hand per frame into Prepare-mode events.

    - Pointing pose: "hover" at the smoothed index fingertip.
    - Pinch released without moving: "select" at where the pinch started
      (the release motion itself drifts, so the start point is the intent).
    - Pinch moved vertically past a threshold: "scroll" deltas until release.
    - Open palm held PALM_HOLD s: one "cancel", then must drop the palm to rearm.
    """

    PINCH_ON: float = 0.28  # thumb-index distance / hand size
    PINCH_OFF: float = 0.42  # hysteresis so a held pinch doesn't flicker
    DRAG_START_PX: float = 24.0
    PALM_HOLD: float = 1.0
    PALM_GRACE: float = 0.2  # tolerate brief recognizer dropouts

    cursor: tuple[float, float] | None = None
    pinching: bool = False
    dragging: bool = False
    palm_progress: float = 0.0
    _pinch_start: tuple[float, float] | None = None
    _last_y: float = 0.0
    _palm_start: float | None = None
    _palm_seen: float = -1e9
    _palm_fired: bool = False
    _filter: OneEuro = field(default_factory=OneEuro)

    @property
    def state(self) -> str:
        if self.palm_progress > 0:
            return "palm"
        if self.dragging:
            return "drag"
        if self.pinching:
            return "pinch"
        return "point" if self.cursor else "idle"

    def update(self, hand: Hand | None, t: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        self._update_palm(hand, t, events)

        if hand is None or self.palm_progress > 0:
            # Losing the hand mid-pinch is not a select.
            self.pinching = self.dragging = False
            self.cursor = None
            self._filter.reset()
            return events

        ratio = hand.pinch_ratio
        if not self.pinching and ratio < self.PINCH_ON:
            self.pinching, self.dragging = True, False
            self._filter.reset()
            self._pinch_start = self._smooth(hand.pinch_point, t)
            self._last_y = self._pinch_start[1]
            self.cursor = self._pinch_start
            return events

        if self.pinching:
            x, y = self._smooth(hand.pinch_point, t)
            self.cursor = (x, y)
            if ratio > self.PINCH_OFF:
                self.pinching = False
                if not self.dragging:
                    events.append(GestureEvent("select", *self._pinch_start))
                self.dragging = False
                self._filter.reset()
                return events
            if not self.dragging and abs(y - self._pinch_start[1]) > self.DRAG_START_PX:
                self.dragging = True
            if self.dragging:
                events.append(GestureEvent("scroll", x, y, dy=y - self._last_y))
            self._last_y = y
            return events

        if hand.is_pointing:
            x, y = self._smooth(hand.index_tip, t)
            self.cursor = (x, y)
            events.append(GestureEvent("hover", x, y))
        else:
            self.cursor = None
            self._filter.reset()
        return events

    def _smooth(self, p: tuple[float, float], t: float) -> tuple[float, float]:
        x, y = self._filter(np.array(p), t)
        return float(x), float(y)

    def _update_palm(self, hand: Hand | None, t: float, events: list[GestureEvent]) -> None:
        if hand is not None and hand.is_open_palm:
            if self._palm_start is None:
                self._palm_start = t
            self._palm_seen = t
        elif t - self._palm_seen > self.PALM_GRACE:
            self._palm_start = None
            self._palm_fired = False

        if self._palm_start is None or self._palm_fired:
            self.palm_progress = 0.0
            return
        self.palm_progress = min(1.0, (t - self._palm_start) / self.PALM_HOLD)
        if self.palm_progress >= 1.0:
            events.append(GestureEvent("cancel"))
            self._palm_fired = True
            self.palm_progress = 0.0


# --- drawing -----------------------------------------------------------------

def draw_landmarks(frame: np.ndarray, hand: Hand) -> None:
    pts = hand.points.astype(int)
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, tuple(pts[a]), tuple(pts[b]), (200, 200, 200), 2, cv2.LINE_AA)
    for i, p in enumerate(pts):
        color = (0, 140, 255) if i in (THUMB_TIP, INDEX_TIP) else (255, 255, 255)
        cv2.circle(frame, tuple(p), 4, color, -1, cv2.LINE_AA)


def draw_cursor(frame: np.ndarray, g: PrepareGestures, hand: Hand | None) -> None:
    """Fingertip cursor, pinch ring, and open-palm hold progress."""
    orange = (0, 140, 255)
    if g.cursor is not None:
        c = tuple(int(v) for v in g.cursor)
        if g.pinching:
            cv2.circle(frame, c, 14, orange, 3 if g.dragging else 2, cv2.LINE_AA)
            cv2.circle(frame, c, 4, orange, -1, cv2.LINE_AA)
        else:
            cv2.circle(frame, c, 8, (255, 255, 255), 2, cv2.LINE_AA)
    if g.palm_progress > 0 and hand is not None:
        center = tuple(int(v) for v in hand.points[MIDDLE_MCP])
        r = int(hand.size * 0.9)
        cv2.ellipse(frame, center, (r, r), -90, 0, 360, (80, 80, 80), 3, cv2.LINE_AA)
        cv2.ellipse(frame, center, (r, r), -90, 0, int(360 * g.palm_progress), orange, 4, cv2.LINE_AA)


def _debug_view() -> None:
    import time

    from palmcards.capture import Camera

    tracker = HandTracker()
    prepare = PrepareGestures()
    log: list[str] = []
    with Camera() as cam:
        t0 = time.perf_counter()
        while True:
            frame = cam.read()
            t = time.perf_counter() - t0
            hands = tracker.detect(frame, t)
            hand = hands[0] if hands else None
            for ev in prepare.update(hand, t):
                if ev.kind != "hover":
                    log = (log + [f"{t:6.2f}s {ev.kind}" + (f" dy={ev.dy:+.0f}" if ev.kind == "scroll" else "")])[-6:]
            if hand:
                draw_landmarks(frame, hand)
            draw_cursor(frame, prepare, hand)

            lines = [f"state: {prepare.state}"]
            if hand:
                lines += [
                    f"gesture: {hand.gesture} ({hand.score:.2f})  {hand.handedness}",
                    f"pinch ratio: {hand.pinch_ratio:.2f}  (on<{prepare.PINCH_ON} off>{prepare.PINCH_OFF})",
                    f"pointing: {hand.is_pointing}",
                ]
            else:
                lines.append("no hand")
            for i, text in enumerate(lines + [""] + log):
                cv2.putText(frame, text, (20, 36 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow("PalmCards gestures (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    tracker.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    _debug_view()
