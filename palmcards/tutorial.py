"""The first-run gesture tutorial: one gesture at a time, each step done by
doing it (or skipped with Enter). Shown until finished, then again with g.

    t = Tutorial(); t.update(state, events, t_now); t.card -> (step, of, text) or None
"""

from __future__ import annotations

from dataclasses import dataclass

STEPS = (
    ("hand", "Hold one hand up inside the box on the right."),
    ("word", "Raise one finger. The highlight follows it, word by word."),
    ("sentence", "Now hold two fingers together to move by sentence."),
    ("focus", "Tap to select the sentence: curl the two fingers down and straighten them again."),
    ("back", "Lower your hand for a second to go back."),
    ("start", "You're ready. Raise a fist and hold it to record (or press T). "
              "To stop, hold an OK sign."),
)


@dataclass
class Tutorial:
    step: int = 0
    active: bool = True
    _hand_since: float | None = None

    @property
    def card(self) -> tuple[int, int, str] | None:
        if not self.active or self.step >= len(STEPS):
            return None
        return self.step + 1, len(STEPS), STEPS[self.step][1]

    @property
    def done(self) -> bool:
        return self.step >= len(STEPS)

    def restart(self) -> None:
        self.step, self.active, self._hand_since = 0, True, None

    def skip(self) -> None:
        self.step += 1

    def update(self, state, events, t: float) -> bool:
        """Advance on the gesture the current step asks for; True when it moved."""
        if not self.active or self.done:
            return False
        kind = STEPS[self.step][0]
        hit = False
        if kind == "hand":
            if state.primary is None:
                self._hand_since = None
            elif self._hand_since is None:
                self._hand_since = t
            hit = self._hand_since is not None and t - self._hand_since > 0.5
        elif kind in ("word", "sentence"):
            hit = state.mode == "browse" and state.level == kind
        elif kind == "focus":
            hit = any(e.kind == "focus" and e.level in ("sentence", "paragraph") for e in events)
        elif kind == "back":
            hit = any(e.kind == "back" for e in events)
        elif kind == "start":
            hit = any(e.kind == "count_in" for e in events)
        if hit:
            self.step += 1
        return hit
