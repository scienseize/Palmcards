import numpy as np
import pytest

from palmcards.motion import Spring, rubberband


def samples(s: Spring, t0: float, t1: float, n: int = 200) -> np.ndarray:
    return np.array([s.at(t) for t in np.linspace(t0, t1, n)])


def test_a_critically_damped_spring_gets_there_without_overshooting():
    s = Spring()
    s.snap(0.0, 10.0)
    s.retarget(1.0, 10.0, response=0.2)
    xs = samples(s, 10.0, 11.0)
    assert xs[0] == 0.0 and (np.diff(xs) >= -1e-12).all() and xs.max() <= 1.0
    assert s.at(11.0) == 1.0 and s.settled(11.0)  # a second is five responses: at rest, exactly
    assert 0.3 < s.at(10.1) < 0.9  # half a response in: well on its way


def test_reading_is_pure_in_time():
    s = Spring()
    s.snap(2.0, 0.0)
    s.retarget(-3.0, 0.0, response=0.3)
    a = s.at(0.15)
    s.at(0.9), s.at(0.01)
    assert s.at(0.15) == a
    assert s.at(-1.0) == 2.0  # before it started: where it started


def test_retargeting_mid_flight_keeps_its_place_and_speed():
    s = Spring()
    s.snap(0.0, 0.0)
    s.retarget(1.0, 0.0, response=0.25)
    x, v = s.at(0.08), s.velocity(0.08)
    assert v > 0
    s.retarget(-1.0, 0.08)  # reversed: no jump, and it keeps moving the old way for a moment
    assert s.at(0.08) == pytest.approx(x) and s.velocity(0.08) == pytest.approx(v)
    assert s.at(0.09) > x
    assert s.at(2.0) == -1.0


def test_the_same_target_again_changes_nothing():
    s = Spring()
    s.snap(0.0, 0.0)
    s.retarget(1.0, 0.0, response=0.2)
    before = s.at(0.05)
    s.retarget(1.0, 0.04)
    assert s.at(0.05) == before


def test_no_response_snaps():
    s = Spring()
    s.snap(0.0, 0.0)
    s.retarget(5.0, 1.0, response=0.0)
    assert s.at(1.0) == 5.0


def test_less_damping_overshoots_a_little_and_settles():
    s = Spring()
    s.snap(0.0, 0.0)
    s.retarget(1.0, 0.0, response=0.3, damping=0.7)
    xs = samples(s, 0.0, 3.0, 600)
    assert 1.0 < xs.max() < 1.2
    assert s.at(3.0) == 1.0


def test_rubberband_resists_more_the_further_it_goes():
    dim = 100.0
    xs = [rubberband(o, dim) for o in (0, 10, 50, 200, 1000, 10 ** 6)]
    assert xs[0] == 0 and all(b > a for a, b in zip(xs, xs[1:]))
    assert xs[1] > 5  # a little past: about c of the push
    assert xs[-1] < dim
    assert rubberband(-50, dim) == -rubberband(50, dim)
