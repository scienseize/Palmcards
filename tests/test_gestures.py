import math

import numpy as np
import pytest

from palmcards.config import CURSOR, TIMING
from palmcards.gestures import (
    FIST, FLAT, L, NONE, ONE, OPEN, PINCH, TWO,
    Grammar, Hand, HandTrack, RelativeCursor, classify, features,
)

W, H = 1280, 720
DT = 1 / 30

# Upright synthetic hand, wrist at the origin, palm (wrist to middle MCP) = 100 px.
BASE = {
    0: (0, 0),
    1: (-30, -20), 2: (-45, -45), 3: (-40, -60),
    5: (-30, -95), 9: (0, -100), 13: (25, -95), 17: (50, -85),
    6: (-30, -140), 10: (0, -140), 14: (25, -140), 18: (50, -140),
    7: (-30, -170), 11: (0, -170), 15: (25, -170), 19: (50, -170),
}
CURLED = {8: (-30, -80), 12: (0, -80), 16: (25, -80), 20: (50, -75)}  # tips folded into the palm
THUMB_IN = (-15, -120)
THUMB_OUT = (-140, -80)


def hand(tips=None, thumb=THUMB_IN, origin=(960, 600), scale=1.0, rotate=0.0, label=""):
    pts = dict(BASE) | CURLED | {4: thumb} | (tips or {})
    arr = np.array([pts[i] for i in range(21)], np.float64) * scale
    th = math.radians(rotate)  # + turns the fingers toward screen right
    rot = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    arr = arr @ rot.T + np.array(origin)
    return Hand(arr.astype(np.float32), handedness=label)


def one(**kw):
    return hand({8: (-30, -200)}, **kw)


def two(**kw):
    return hand({8: (-12, -200), 12: (5, -200)}, **kw)


def flat(**kw):
    return hand({8: (-30, -200), 12: (0, -205), 16: (25, -200), 20: (50, -190)}, **kw)


def open_palm(**kw):
    return hand({8: (-80, -200), 12: (-20, -210), 16: (40, -200), 20: (100, -180)}, thumb=THUMB_OUT, **kw)


def l_hand(**kw):
    return hand({8: (-30, -200)}, thumb=THUMB_OUT, **kw)


def pinch(**kw):
    return hand({8: (-30, -200)}, thumb=(-27, -197), **kw)


def fist(**kw):
    return hand(thumb=(-20, -85), **kw)  # thumb wrapped over the curled fingers


def run(target, frames, t0=0.0):
    """Feed frames (a hand, a list of hands, or None) at 30 fps; returns (events, end time)."""
    events, t = [], t0
    for f in frames:
        hands = [] if f is None else f if isinstance(f, list) else [f]
        if isinstance(target, HandTrack):
            events += target.update(hands[0], t, H)
        else:
            events += target.update(hands, t)
        t += DT
    return events, t


def hold(make, seconds, **kw):
    return [make(**kw)] * round(seconds / DT)


def lerp_frames(make_tips, a, b, n, **kw):
    """n frames moving the given fingertips from positions a to b."""
    out = []
    for i in range(1, n + 1):
        k = i / n
        tips = {j: tuple(np.add(np.multiply(a[j], 1 - k), np.multiply(b[j], k))) for j in a}
        out.append(make_tips(tips, **kw))
    return out


# --- classifier ------------------------------------------------------------------

@pytest.mark.parametrize("make, pose", [
    (one, ONE), (two, TWO), (flat, FLAT), (open_palm, OPEN), (l_hand, L), (pinch, PINCH), (fist, FIST),
])
def test_classify_each_pose(make, pose):
    assert classify(features(make())) == pose


def test_spread_v_sign_and_three_fingers_are_none():
    v_sign = hand({8: (-60, -200), 12: (30, -200)})
    three = hand({8: (-30, -200), 12: (0, -200), 16: (25, -200)})
    assert classify(features(v_sign)) == NONE
    assert classify(features(three)) == NONE


def test_l_needs_a_right_angle_between_thumb_and_index():
    thumb_up_alongside = hand({8: (-30, -200)}, thumb=(-130, -200))  # thumb out but parallel to index
    assert classify(features(thumb_up_alongside)) == NONE


@pytest.mark.parametrize("scale", [0.5, 2.0])
@pytest.mark.parametrize("make, pose", [(one, ONE), (two, TWO), (flat, FLAT), (open_palm, OPEN), (l_hand, L)])
def test_classifier_is_scale_invariant(make, pose, scale):
    assert classify(features(make(scale=scale))) == pose


def test_pinch_hysteresis():
    loose = hand({8: (-30, -200)}, thumb=(-30, -170))  # 0.30 palms apart: between on and off
    assert classify(features(loose), was_pinching=False) != PINCH
    assert classify(features(loose), was_pinching=True) == PINCH


def test_tilt_sign():
    assert features(one(rotate=20)).tilt == pytest.approx(20, abs=0.5)
    assert features(one(rotate=-30)).tilt == pytest.approx(-30, abs=0.5)


# --- stability and events -----------------------------------------------------------

def test_stable_pose_needs_150ms_and_ignores_blips():
    tr = HandTrack()
    run(tr, hold(one, 0.3))
    assert tr.stable == ONE
    run(tr, [two(), two()] + hold(one, 0.1))  # 2-frame blip
    assert tr.stable == ONE
    run(tr, hold(two, TIMING.stable_s + DT))
    assert tr.stable == TWO


TWO_TIPS = {8: (-12, -200), 12: (5, -200)}
TWO_FOLDED = {8: (-15, -125), 12: (-8, -125)}
FLAT_TIPS = {8: (-30, -200), 12: (0, -205), 16: (25, -200), 20: (50, -190)}
FLAT_FOLDED = {8: (-18, -125), 12: (-12, -125), 16: (-8, -122), 20: (-5, -120)}


def test_fast_fold_from_two_and_flat_fires():
    for make, a, b in ((two, TWO_TIPS, TWO_FOLDED), (flat, FLAT_TIPS, FLAT_FOLDED)):
        tr = HandTrack()
        run(tr, hold(make, 0.3))
        events, _ = run(tr, lerp_frames(hand, a, b, 6))  # 0.2 s
        assert events.count("fold") == 1


def test_slow_fold_does_not_fire():
    tr = HandTrack()
    run(tr, hold(two, 0.3))
    events, _ = run(tr, lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 60))  # 2 s
    assert "fold" not in events


def test_no_fold_from_one():
    tr = HandTrack()
    run(tr, hold(one, 0.3))
    events, _ = run(tr, hold(pinch, 0.3))
    assert "fold" not in events


def lift(seconds, rise_px, make=pinch):
    n = round(seconds / DT)
    return [make(origin=(960, 600 - rise_px * i / n)) for i in range(1, n + 1)]


def test_pinch_and_fast_lift_commits_once():
    tr = HandTrack()
    events, _ = run(tr, hold(pinch, 0.2) + lift(0.4, 0.2 * H) + hold(pinch, 0.3, origin=(960, 600 - 0.2 * H)))
    assert events.count("commit") == 1


def test_slow_lift_or_open_hand_lift_does_not_commit():
    tr = HandTrack()
    events, _ = run(tr, hold(pinch, 0.2) + lift(1.5, 0.2 * H))
    assert "commit" not in events
    tr = HandTrack()
    events, _ = run(tr, hold(one, 0.2) + lift(0.4, 0.2 * H, make=one))
    assert "commit" not in events


# --- relative cursor ------------------------------------------------------------

def test_cursor_maps_hand_box_and_edges_scroll():
    c = RelativeCursor((W, H))
    x0, y0, x1, y1 = c.box
    assert c.update((x0, y0), 0.0) == (0.0, 0.0)
    c.reset()
    assert c.update((x1 + 50, y1 + 50), 0.0) == (1.0, 1.0)  # clamped
    assert c.scroll_rate == pytest.approx(CURSOR.edge_speed_rows_s)
    c.reset()
    c.update((x0, y0), 0.0)
    assert c.scroll_rate == pytest.approx(-CURSOR.edge_speed_rows_s)
    c.reset()
    c.update(((x0 + x1) / 2, (y0 + y1) / 2), 0.0)
    assert c.scroll_rate == 0.0


# --- grammar --------------------------------------------------------------------------

def test_word_flow_browse_focus_ring_point_commit():
    g = Grammar((W, H))
    run(g, hold(one, 0.3))
    assert (g.state.mode, g.state.level) == ("browse", "word")
    assert g.state.cursor is not None

    events, t = run(g, hold(pinch, 0.3))
    assert [e.kind for e in events] == ["focus"] and g.state.mode == "focus"

    # Lifting the focusing pinch does not commit.
    events, t = run(g, lift(0.4, 0.2 * H), t)
    assert events == [] and g.state.mode == "focus"

    events, t = run(g, hold(open_palm, 0.3), t)
    assert g.state.op == "ring"
    run(g, hold(l_hand, 0.3), t)
    assert g.state.pointing

    events, t = run(g, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t + 0.3)
    assert [e.kind for e in events] == ["commit"]
    assert (g.state.mode, g.state.level, g.state.op) == ("browse", "word", None)


def test_pinch_right_after_commit_does_not_refocus():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3) + hold(open_palm, 0.3))
    events, t = run(g, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert [e.kind for e in events] == ["commit"]
    events, t = run(g, hold(pinch, 0.5, origin=(960, 600 - 0.2 * H)), t)
    assert events == [] and g.state.mode == "browse"
    events, _ = run(g, hold(one, 0.3) + hold(pinch, 0.3), t)
    assert [e.kind for e in events] == ["focus"]


def test_sentence_fold_focus_and_tone_dial_is_relative():
    g = Grammar((W, H))
    _, t = run(g, hold(two, 0.3))
    assert g.state.level == "sentence"
    events, t = run(g, lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 6), t)
    assert [e.kind for e in events] == ["focus"]

    # The dial starts wherever the L-hand is tilted when it appears.
    _, t = run(g, hold(l_hand, 0.3, rotate=-10), t)
    assert g.state.op == "tone" and g.state.tone == pytest.approx(0, abs=0.01)
    _, t = run(g, hold(l_hand, 0.3, rotate=12.5), t)
    assert g.state.tone == pytest.approx(0.5, abs=0.02)  # 22.5 of 45 degrees, toward warm
    _, t = run(g, hold(l_hand, 0.3, rotate=-80), t)
    assert g.state.tone == -1.0  # clamped cold

    events, _ = run(g, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert [(e.kind, e.op, e.value) for e in events] == [("commit", "tone", -1.0)]


def test_paragraph_two_l_hands_stretch_relative_to_start():
    g = Grammar((W, H))
    _, t = run(g, hold(flat, 0.3))
    assert g.state.level == "paragraph"
    events, t = run(g, lerp_frames(hand, FLAT_TIPS, FLAT_FOLDED, 6), t)
    assert [e.kind for e in events] == ["focus"]

    def pair(gap):
        return [l_hand(origin=(960, 500), label="Right"), l_hand(origin=(960 - gap, 500), label="Left")]

    _, t = run(g, [pair(300)] * 10, t)
    assert g.state.op == "stretch" and g.state.stretch == pytest.approx(1.0)
    _, t = run(g, [pair(450)] * 10, t)
    assert g.state.stretch == pytest.approx(1.5, abs=0.01)
    assert g.state.stretch_ends is not None


def test_drop_hand_for_one_second_backs_out():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3))
    assert g.state.mode == "focus"
    events, t = run(g, [None] * 15, t)  # 0.5 s
    assert events == [] and g.state.mode == "focus" and 0.3 < g.state.drop_progress < 0.7
    events, t = run(g, [None] * 20, t)
    assert [e.kind for e in events] == ["back"]


def test_hand_below_bottom_band_counts_as_dropped():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3))
    events, _ = run(g, hold(pinch, 1.1, origin=(960, H * 0.95)), t)
    assert [e.kind for e in events] == ["back"]


def test_browse_goes_idle_when_the_hand_leaves():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3))
    run(g, [None] * 12, t)
    assert g.state.mode == "idle"


def test_level_follows_hand_shape():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3))
    _, t = run(g, hold(flat, 0.3), t)
    assert g.state.level == "paragraph"
    run(g, hold(two, 0.3), t)
    assert g.state.level == "sentence"
