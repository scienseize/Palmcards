"""The options ring's knob: an angle sequence in, a node sequence out."""

import pytest

from palmcards.config import KNOB
from palmcards.knob import Knob, RingSelection

RING = ("imparted", "stamped", "imposed", "hear it")


def turn(angles, labels=RING, knob=None, ring=None):
    """Feed relative angles (degrees) from a fresh start; the node index after each."""
    knob = knob or Knob()
    ring = ring or RingSelection(labels)
    knob.start(0.0)
    ring.begin()
    out = []
    for a in angles:
        ring.apply(knob.update(a))
        out.append(ring.index)
    return out


def test_the_defaults_are_the_configured_ones():
    edge = KNOB.step_deg / 2 + KNOB.hyst_deg
    assert (KNOB.step_deg, KNOB.hyst_deg, KNOB.dead_deg, edge) == (15.0, 4.0, 5.0, 11.5)


def test_hysteresis_at_a_boundary():
    # Stays on 0 until 11.5 deg (the midpoint 7.5 + 4), then steps.
    assert turn([6, 9, 11.4, 11.6]) == [0, 0, 0, 1]
    # Back from step 1 (centre 15): past the midpoint (7.5) it stays on 1, and
    # only 11.5 before 15 (3.5 deg, inside the dead zone here) goes back.
    assert turn([12, 9, 7, 6, 4.9]) == [1, 1, 1, 1, 0]
    # Wobbling around the midpoint doesn't flicker.
    assert turn([8, 7, 8.5, 6.5, 9, 7]) == [0] * 6
    # Step 1 to 2: 26.5 deg (15 + 11.5), and 2 back to 1 at 18.5 (30 - 11.5).
    assert turn([12, 26.4, 26.6, 20, 18.6, 18.4]) == [1, 1, 2, 2, 2, 1]


def test_turning_left_steps_back():
    assert turn([-11.4, -11.6, -26.6]) == [0, 3, 2]  # wraps: 0 -> hear it -> imposed


def test_dead_zone_returns_to_the_start_node():
    knob = Knob(step_deg=6.0, hyst_deg=0.0)  # a fine knob: it would step at 3 deg, inside the dead zone
    assert turn([4.9, 5.1, 4.9, -4.9, -5.1], knob=knob) == [0, 1, 0, 0, 3]
    # With the default knob: from step 1, 4.9 deg is the start node again.
    assert turn([12, 5.1, 4.9]) == [1, 1, 0]


def test_wraps_both_ways():
    forward = [12 + 15 * i for i in range(6)]  # 12, 27, 42, ... one step each
    assert turn(forward) == [1, 2, 3, 0, 1, 2]
    backward = [-12 - 15 * i for i in range(6)]
    assert turn(backward) == [3, 2, 1, 0, 3, 2]


def test_a_fast_turn_takes_every_step_between():
    knob, ring = Knob(), RingSelection(RING)
    knob.start(0.0)
    ring.begin()
    steps = ring.apply(knob.update(40.0))  # 40 deg in one frame: steps 1 and 2 (3 needs 30 + 11.5)
    assert steps == [(1, "stamped", 1), (2, "imposed", 1)]
    assert ring.apply(knob.update(-12.0)) == [(1, "stamped", -1), (0, "imparted", -1), (3, "hear it", -1)]


def test_nodes_arriving_mid_turn_keep_the_pick():
    knob, ring = Knob(), RingSelection(("imparted", "imposed", "hear it"))
    knob.start(0.0)
    ring.begin()
    ring.apply(knob.update(12.0))
    assert ring.pick == "imposed"
    ring.set_labels(("imparted", "stamped", "inflicted", "imposed", "hear it"))  # the LLM answered
    assert ring.pick == "imposed" and ring.index == 3
    assert ring.apply(knob.update(27.0)) == [(4, "hear it", 1)]
    # Turning back steps through the new nodes, and step 0 is the start node.
    assert [s[1] for s in ring.apply(knob.update(-12.0))] == ["imposed", "imparted", "hear it"]
    assert [s[1] for s in ring.apply(knob.update(12.0))] == ["imparted", "stamped"]


def test_a_node_that_goes_falls_back_to_the_first():
    ring = RingSelection(("being", "imposed", "hear it"))
    ring.pick = "imposed"
    ring.set_labels(("being", "stamped", "hear it"))
    assert ring.pick == "being"


def test_regrip_carries_on_with_no_jump():
    knob, ring = Knob(), RingSelection(RING)
    knob.start(0.0)
    ring.begin()
    ring.apply(knob.update(12.0))
    knob.release()  # the L was lost
    knob.regrip(-30.0)  # and comes back at another angle
    assert ring.apply(knob.update(-30.0)) == []
    assert ring.apply(knob.update(-30.0 + 11.6)) == [(2, "imposed", 1)]


@pytest.mark.parametrize("angles", [[], [0.0], [3.0, -3.0]])
def test_holding_still_keeps_the_start_node(angles):
    assert turn(angles) == [0] * len(angles)


def test_the_offset_is_how_far_past_its_step_the_knob_is_turned():
    knob = Knob(step_deg=15, hyst_deg=4, dead_deg=5)
    assert knob.offset == 0.0  # not held
    knob.start(100.0)
    knob.update(103.0)
    assert knob.offset == pytest.approx(3.0)
    knob.update(111.0)  # 11 deg: still on step 0, most of the way to the next
    assert (knob.k, knob.offset) == (0, pytest.approx(11.0))
    knob.update(112.0)  # past 11.5: step 1, and the offset carries on from its centre
    assert (knob.k, knob.offset) == (1, pytest.approx(-3.0))
    knob.release()
    assert knob.offset == 0.0
