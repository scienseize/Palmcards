"""Springs for what moves on screen: the options ring's turn, the focus
panel's scroll (palmcards/render.py; response and damping in style.MOTION).

A spring is described the way Apple's are: `response`, roughly how long it
takes to get there, in seconds, and `damping`, 1.0 for no overshoot (critically
damped), lower for a little bounce. It is evaluated in closed form as a
function of time since it was last retargeted, so reading it is pure (any
frame, any order, as often as wanted) and retargeting mid-flight carries on
from where it is with the speed it has: nothing jumps, nothing restarts.

    s = Spring()
    s.snap(0.0, t)                    # at rest at 0
    s.retarget(1.0, t, response=0.2)  # off towards 1
    s.at(t + 0.1)                     # where it is 0.1 s later

`rubberband` is the resistance past a limit: the further past, the less it follows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-3  # offset and speed (units, units/s) under which a spring is at rest


@dataclass
class Spring:
    target: float = 0.0
    response: float = 0.2  # s
    damping: float = 1.0  # 1 = critically damped; above 1 is treated as 1
    _x0: float = 0.0  # offset from the target at _t0
    _v0: float = 0.0  # speed at _t0
    _t0: float = 0.0

    def snap(self, value: float, t: float) -> None:
        """At rest at `value` from time t."""
        self.target, self._x0, self._v0, self._t0 = value, 0.0, 0.0, t

    def retarget(self, target: float, t: float, response: float | None = None,
                 damping: float | None = None) -> None:
        """Head for `target` from time t, starting where the spring is at t,
        with its speed. The same target again changes nothing."""
        response = self.response if response is None else response
        damping = self.damping if damping is None else damping
        if target == self.target and response == self.response and damping == self.damping:
            return
        if response <= 0:
            self.response, self.damping = response, damping
            self.snap(target, t)
            return
        x, v = self._state(t)
        self.response, self.damping = response, damping
        self.target, self._x0, self._v0, self._t0 = target, x - target, v, t

    def at(self, t: float) -> float:
        return self._state(t)[0]

    def velocity(self, t: float) -> float:
        return self._state(t)[1]

    def settled(self, t: float) -> bool:
        x, v = self._offset(t)
        return abs(x) < EPS and abs(v) < EPS

    def _state(self, t: float) -> tuple[float, float]:
        """(value, speed) at time t."""
        x, v = self._offset(t)
        if abs(x) < EPS and abs(v) < EPS:
            return self.target, 0.0
        return self.target + x, v

    def _offset(self, t: float) -> tuple[float, float]:
        """Offset from the target and speed at time t, in closed form."""
        x0, v0 = self._x0, self._v0
        if (x0 == 0 and v0 == 0) or self.response <= 0:
            return 0.0, 0.0
        dt = max(0.0, t - self._t0)
        w = 2 * math.pi / self.response
        z = min(self.damping, 1.0)
        if z >= 1.0:
            b = v0 + w * x0
            e = math.exp(-w * dt)
            return (x0 + b * dt) * e, (v0 - w * b * dt) * e
        a, wd = z * w, w * math.sqrt(1 - z * z)
        b = (v0 + a * x0) / wd
        e, c, s = math.exp(-a * dt), math.cos(wd * dt), math.sin(wd * dt)
        x = e * (x0 * c + b * s)
        return x, -a * x + e * (-x0 * wd * s + b * wd * c)


def rubberband(over: float, dim: float, c: float = 0.55) -> float:
    """How far something follows when pushed `over` past its limit, for a
    thing `dim` big: about c of the push at first, never as far as dim."""
    if dim <= 0:
        return 0.0
    return over * dim * c / (dim + c * abs(over))
