"""The word's options ring turned like a knob (Kat's "spin synonyms").

    knob = Knob(); knob.start(angle)          # the L appears
    k = knob.update(angle)                    # every frame: the step it is on
    ring = RingSelection(labels)
    ring.begin(); steps = ring.apply(k)       # [(node, label, dir), ...]

`Knob` turns an angle (degrees, relative to where the L appeared) into an
integer step k, unbounded, with hysteresis: it moves to k +/- 1 only once the
angle is step_deg / 2 + hyst_deg past step k's centre (k * step_deg), and
within dead_deg of the start angle it is back on step 0. A fast turn can cross
several steps in one update; each is taken. Picked up again after the L was
lost (`regrip`), it carries on from its step with no jump.

`RingSelection` follows the steps round the ring's nodes, wrapping, and holds
the pick by its label, not its index: alternatives arriving from the LLM join
the ring without moving the pick. Step 0 is the node the knob started on.
"""

from __future__ import annotations

from dataclasses import dataclass

from palmcards.config import KNOB


@dataclass
class Knob:
    step_deg: float = KNOB.step_deg
    hyst_deg: float = KNOB.hyst_deg
    dead_deg: float = KNOB.dead_deg
    zero: float | None = None  # the angle of step 0's centre, while the knob is held
    k: int = 0
    rel: float = 0.0  # the last angle, relative to zero

    @property
    def offset(self) -> float:
        """Degrees the knob is turned past its step's centre (within the
        hysteresis band, about +/- 11.5 deg): the app turns the ring this much
        of the way with the hand. 0 while it isn't held."""
        return 0.0 if self.zero is None else self.rel - self.k * self.step_deg

    def start(self, angle: float) -> None:
        """A new turn: this angle is step 0."""
        self.zero, self.k = angle, 0

    def regrip(self, angle: float) -> None:
        """Picked up again: this angle is the current step's centre."""
        self.zero = angle - self.k * self.step_deg

    def release(self) -> None:
        self.zero = None

    def update(self, angle: float) -> int:
        if self.zero is None:
            self.regrip(angle)
        rel = self.rel = angle - self.zero
        if abs(rel) <= self.dead_deg:
            self.k = 0
            return self.k
        edge = self.step_deg / 2 + self.hyst_deg
        while rel - self.k * self.step_deg > edge:
            self.k += 1
        while self.k * self.step_deg - rel > edge:
            self.k -= 1
        return self.k


@dataclass
class RingSelection:
    labels: tuple[str, ...] = ()
    pick: str | None = None
    start: str | None = None  # the pick when the knob started (step 0)
    k: int = 0  # the knob step the pick follows

    def __post_init__(self) -> None:
        self.set_labels(self.labels)

    @property
    def index(self) -> int:
        return self.labels.index(self.pick) if self.pick in self.labels else 0

    def set_labels(self, labels: tuple[str, ...]) -> None:
        """New nodes (alternatives arriving): the pick
        stays on its label; a label that has gone falls back to the first node."""
        self.labels = tuple(labels)
        if self.pick not in self.labels:
            self.pick = self.labels[0] if self.labels else None
        if self.start not in self.labels:
            self.start = self.labels[0] if self.labels else None

    def begin(self) -> None:
        """The knob starts: its step 0 is the node picked now."""
        self.start, self.k = self.pick, 0

    def apply(self, k: int) -> list[tuple[int, str, int]]:
        """Follow the knob to step k, one node per step: back on step 0 is the
        node it started on. Returns each step taken as (node, label, dir)."""
        steps: list[tuple[int, str, int]] = []
        if not self.labels:
            self.k = k
            return steps
        while self.k != k:
            d = 1 if k > self.k else -1
            self.k += d
            if self.k == 0 and self.start in self.labels:
                self.pick = self.start
            else:
                self.pick = self.labels[(self.index + d) % len(self.labels)]
            steps.append((self.index, self.pick, d))
        return steps
