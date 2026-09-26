"""Choosing by pointing: the point starts on the current item, the nearest item
wins by a margin, and a pinch's rewind puts the pick back."""

from palmcards.config import OPS
from palmcards.pick import Picker

ROW = [(100.0, 50.0), (200.0, 50.0), (300.0, 50.0)]  # three items, 100 px apart
SCALE = (500.0, 400.0)  # px per hand-box width / height


def test_the_point_starts_on_the_picked_item_and_moves_with_the_fingertip():
    p = Picker(index=1)
    assert p.update(0.0, (0.0, 0.0), ROW, SCALE) == 1  # no jump when pointing starts
    assert p.update(0.1, (0.2, 0.0), ROW, SCALE) == 2  # 100 px right: the next
    assert p.update(0.2, (-0.2, 0.0), ROW, SCALE) == 0  # 200 px back from there: the first


def test_a_margin_keeps_the_pick_between_neighbours():
    p = Picker(index=0)
    p.update(0.0, (0.0, 0.0), ROW, SCALE)
    half = 50 / SCALE[0]  # halfway to the next item
    assert p.update(0.1, (half + 0.01, 0.0), ROW, SCALE) == 0  # just past halfway: not yet
    far = (50 + OPS.pick_margin * 100 / 2 + 2) / SCALE[0]  # past halfway by more than the margin
    assert p.update(0.2, (far, 0.0), ROW, SCALE) == 1
    assert p.update(0.3, (half - 0.01, 0.0), ROW, SCALE) == 1  # back just under halfway: stays


def test_not_pointing_keeps_the_pick_and_the_next_start_anchors_again():
    p = Picker(index=0)
    p.update(0.0, (0.0, 0.0), ROW, SCALE)
    p.update(0.1, (0.2, 0.0), ROW, SCALE)
    assert p.update(0.2, None, ROW, SCALE) == 1 and p.anchor is None
    assert p.update(0.3, (0.9, 0.0), ROW, SCALE) == 1  # picked up again: starts on item 1


def test_a_rewind_puts_the_pick_back_and_empty_items_are_left_alone():
    p = Picker(index=0)
    p.update(1.0, (0.0, 0.0), ROW, SCALE)
    p.update(1.2, (0.2, 0.0), ROW, SCALE)
    assert p.index == 1
    p.rewind(1.1)
    assert p.index == 0
    assert Picker(index=3).update(0.0, (0.0, 0.0), [], SCALE) == 3
    assert Picker(index=5).update(0.0, None, ROW, SCALE) == 2  # kept on the items


def test_the_picker_knows_where_the_point_is_while_pointing():
    p = Picker(index=1)
    p.update(0.0, (0.0, 0.0), ROW, SCALE)
    assert p.at == ROW[1]
    p.update(0.1, (0.1, 0.0), ROW, SCALE)
    assert p.at == (ROW[1][0] + 0.1 * SCALE[0], ROW[1][1])
    p.update(0.2, None, ROW, SCALE)
    assert p.at is None
