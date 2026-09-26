"""Choosing among things on screen by pointing (the word's options ring, the
suggested marks in a sentence, Review's take chips).

    Picker.update(t, point, positions, scale) -> index

After an L, the grammar publishes how far the index fingertip has moved
(GestureState.point, hand-box units). The picker puts that on screen: the
point starts on the item picked when pointing starts and moves with the
fingertip at `scale` pixels per hand-box unit (the scale of browsing). The
nearest item is picked, but only once the point is nearer to it than to the
current one by OPS.pick_margin of the gap between the two, so the pick
doesn't flicker between neighbours. When a pinch rewinds the point, rewind()
puts the pick back to what it was at that moment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from palmcards.config import OPS


@dataclass
class Picker:
    index: int = 0
    anchor: tuple[float, float] | None = None  # screen px of point (0, 0)
    history: list[tuple[float, int]] = field(default_factory=list)  # (t, index), the last second

    def reset(self, index: int = 0) -> None:
        self.index, self.anchor, self.history = index, None, []

    def update(self, t: float, point: tuple[float, float] | None, positions: list[tuple[float, float]],
               scale: tuple[float, float]) -> int:
        """The picked index, after the point (None: not pointing) moved over items at `positions`."""
        if not positions:
            return self.index
        self.index = min(max(self.index, 0), len(positions) - 1)
        if point is None:
            self.anchor = None
            return self.index
        sx, sy = scale[0] * OPS.point_gain, scale[1] * OPS.point_gain
        if self.anchor is None:  # the point starts on the item picked now
            cx, cy = positions[self.index]
            self.anchor = (cx - point[0] * sx, cy - point[1] * sy)
        at = (self.anchor[0] + point[0] * sx, self.anchor[1] + point[1] * sy)
        near = min(range(len(positions)), key=lambda i: math.dist(at, positions[i]))
        if near != self.index:
            gap = math.dist(positions[near], positions[self.index])
            if math.dist(at, positions[self.index]) - math.dist(at, positions[near]) > OPS.pick_margin * gap:
                self.index = near
        self.history.append((t, self.index))
        while self.history and self.history[0][0] < t - 1.0:
            self.history.pop(0)
        return self.index

    def rewind(self, t: float) -> None:
        """The pick as it was at time t (a pinch took the point back there)."""
        before = [i for tt, i in self.history if tt <= t]
        if before:
            self.index = before[-1]
