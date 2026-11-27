"""An easter egg: the Friend's sign from 20th Century Boys. Point the index finger
up with the back of the hand to the camera (the way nobody points to browse) and
hold it; the Friend's symbol fills the screen for a moment. Prepare and Review only.

The picture is not shipped (it is the manga's): put it at palmcards/friend.jpg,
which is gitignored. Without it the sign does nothing.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from palmcards.gestures import HOLD_GRACE_S, ONE, Hand, classify, features

PICTURE = Path(__file__).with_name("friend.jpg")
# Replayed through the 29 recorded traces (102,085 hand frames, 2026-11-27), these never fire; looser
# ones did: a hand edge-on reads either way, and people do browse with the back of the hand, tilted.
HOLD_S = 1.5  # the sign held this long
UPRIGHT_DEG = 15.0  # the index finger within this of straight up
FACING_MIN = 0.4  # how squarely the back faces the camera (see back_to_camera)
SHOW_S = 3.0  # the picture shown this long
FADE_IN_S, FADE_OUT_S = 0.25, 0.6


def back_to_camera(hand: Hand) -> bool:
    """The back of the hand squarely to the camera. The turn wrist -> index knuckle
    -> pinky knuckle on screen (y down), over the palm squared: in the mirrored frame
    a hand MediaPipe labels "Left" (the user's right hand) shows its palm when it is
    clockwise (+), as in 98% of the pointing frames recorded; the other hand is the
    mirror image. Near 0 the hand is edge-on, and either reading is noise."""
    p = hand.points
    a, b = p[5] - p[0], p[17] - p[0]
    z = float(a[0] * b[1] - a[1] * b[0]) / hand.size ** 2
    return (z if hand.handedness == "Right" else -z) >= FACING_MIN


def is_friend_sign(hand: Hand) -> bool:
    """One finger up (ONE: index up, the others curled, the thumb in), upright, the back to the camera."""
    f = features(hand)
    return classify(f) == ONE and abs(f.tilt) <= UPRIGHT_DEG and back_to_camera(hand)


class Friend:
    """The sign held HOLD_S by any hand (misreads forgiven for HOLD_GRACE_S, once per
    hold), then the picture for SHOW_S. update() every frame the hands are read;
    alpha(t) is how much of the picture to show now."""

    def __init__(self, picture: Path = PICTURE):
        self.picture = picture
        self._image: np.ndarray | None = None
        self._scaled: np.ndarray | None = None
        self._since: float | None = None
        self._last = -math.inf
        self._spent = False
        self.shown_at: float | None = None

    @property
    def available(self) -> bool:
        return self.picture.exists()

    def update(self, hands: list[Hand], t: float) -> bool:
        """True on the frame the hold completes (the picture starts)."""
        if not self.available:
            return False
        if any(is_friend_sign(h) for h in hands):
            self._last = t
            if self._spent:
                return False
            self._since = t if self._since is None else self._since
            if t - self._since >= HOLD_S:
                self._since, self._spent, self.shown_at = None, True, t
                return True
        elif t - self._last > HOLD_GRACE_S:
            self._since, self._spent = None, False
        return False

    def alpha(self, t: float) -> float:
        if self.shown_at is None or not 0 <= (dt := t - self.shown_at) <= SHOW_S:
            return 0.0
        return min(1.0, dt / FADE_IN_S, (SHOW_S - dt) / FADE_OUT_S)

    def draw(self, frame: np.ndarray, t: float) -> None:
        """The picture over `frame` (in place), fitted and centred on black, as alpha(t) says."""
        if (a := self.alpha(t)) <= 0:
            return
        if self._image is None:
            self._image = cv2.imread(str(self.picture))
            if self._image is None:  # unreadable: the egg is off
                self.shown_at = None
                return
        if self._scaled is None or self._scaled.shape != frame.shape:
            fh, fw = frame.shape[:2]
            ih, iw = self._image.shape[:2]
            k = min(fw / iw, fh / ih)
            pic = cv2.resize(self._image, (round(iw * k), round(ih * k)), interpolation=cv2.INTER_AREA)
            self._scaled = np.zeros_like(frame)
            y, x = (fh - pic.shape[0]) // 2, (fw - pic.shape[1]) // 2
            self._scaled[y:y + pic.shape[0], x:x + pic.shape[1]] = pic
        cv2.addWeighted(self._scaled, a, frame, 1 - a, 0, dst=frame)
