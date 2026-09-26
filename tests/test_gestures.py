import math

import numpy as np
import pytest

from palmcards.config import CURSOR, OPS, REHEARSE, TIMING
from palmcards.gestures import (
    FIST, FLAT, L, NONE, ONE, OPEN, PINCH, TWO,
    Grammar, Hand, HandTrack, ModeMachine, RelativeCursor, classify, features,
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


def ring_focus():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3) + hold(open_palm, 0.3))
    assert g.state.op == "ring"
    return g, t


def test_ring_knob_steps_with_l_hand_turn():
    g, t = ring_focus()
    step = OPS.knob_step_deg
    _, t = run(g, hold(l_hand, 0.3, rotate=-20), t)  # wherever the hand starts is zero
    assert g.state.pointing and g.state.knob == 0
    _, t = run(g, hold(l_hand, 0.1, rotate=-20 + 2 * step), t)
    assert g.state.knob == 2  # clockwise
    _, t = run(g, hold(l_hand, 0.1, rotate=-20 - step), t)
    assert g.state.knob == -1  # anticlockwise; the node is knob mod the node count


def test_ring_knob_hysteresis_and_continuity():
    g, t = ring_focus()
    step = OPS.knob_step_deg
    _, t = run(g, hold(l_hand, 0.3), t)
    _, t = run(g, hold(l_hand, 0.1, rotate=0.6 * step), t)  # just past the boundary
    assert g.state.knob == 0
    _, t = run(g, hold(l_hand, 0.1, rotate=0.8 * step), t)
    assert g.state.knob == 1
    _, t = run(g, hold(l_hand, 0.1, rotate=0.6 * step), t)  # back a little: stays
    assert g.state.knob == 1
    # Let go and pick the knob up again at a new angle: it continues from 1.
    _, t = run(g, hold(open_palm, 0.3) + hold(l_hand, 0.3, rotate=40) + hold(l_hand, 0.1, rotate=40 + step), t)
    assert g.state.knob == 2


def test_thumb_drifting_in_does_not_freeze_a_started_control():
    g, t = ring_focus()
    _, t = run(g, hold(l_hand, 0.3), t)
    _, t = run(g, hold(one, 0.3, rotate=2 * OPS.knob_step_deg), t)  # thumb in, still turning
    assert g.state.pointing and g.state.knob == 2


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


def prepare_sentence_focus():
    g = Grammar((W, H))
    _, t = run(g, hold(two, 0.3))
    events, t = run(g, lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 6), t)
    assert [e.kind for e in events] == ["focus"]
    return g, t


def test_open_palm_on_a_focused_sentence_spreads_marks():
    g, t = prepare_sentence_focus()
    _, t = run(g, hold(l_hand, 0.3, rotate=-10) + hold(l_hand, 0.3, rotate=12.5), t)
    assert g.state.op == "tone" and g.state.tone > 0.4
    _, t = run(g, hold(open_palm, 0.3), t)  # an open palm switches to marks; the tone preview is dropped
    assert g.state.op == "marks" and g.state.tone == 0.0
    assert [e["op"] for e in g.log.entries if e["kind"] == "op"] == ["tone", "marks"]
    _, t = run(g, hold(l_hand, 0.3, rotate=-30) + hold(l_hand, 0.3, rotate=30), t)
    assert g.state.op == "marks" and g.state.tone == 0.0  # the L-hand no longer turns the tone dial
    events, _ = run(g, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert [(e.kind, e.op) for e in events] == [("commit", "marks")]


def marks_spread():
    g, t = prepare_sentence_focus()
    _, t = run(g, hold(open_palm, 0.3), t)
    assert g.state.op == "marks"
    return g, t


def kinds_of(events):
    return [e.kind for e in events]


def test_the_l_hand_turns_a_knob_through_spread_marks():
    g, t = marks_spread()
    step = OPS.knob_step_deg
    _, t = run(g, hold(l_hand, 0.3, rotate=-20), t)  # wherever the hand starts is zero
    assert g.state.pointing and g.state.knob == 0
    _, t = run(g, hold(l_hand, 0.1, rotate=-20 + 0.6 * step), t)  # just past a boundary: hysteresis
    assert g.state.knob == 0
    _, t = run(g, hold(l_hand, 0.1, rotate=-20 + 2 * step), t)
    assert g.state.knob == 2 and g.state.tone == 0.0  # a knob, not the tone dial
    _, t = run(g, hold(l_hand, 0.1, rotate=-20 + step), t)
    assert g.state.knob == 1


def test_a_pinch_without_a_lift_toggles_once():
    g, t = marks_spread()
    _, t = run(g, hold(l_hand, 0.3, rotate=0) + hold(l_hand, 0.1, rotate=OPS.knob_step_deg), t)
    assert g.state.knob == 1
    events, t = run(g, hold(pinch, 0.3), t)
    assert events == [] and not g.state.pointing  # held: nothing yet, and the knob doesn't turn
    events, t = run(g, hold(l_hand, 0.3, rotate=40), t)  # let go (back to an L, at another angle)
    assert [(e.kind, e.op) for e in events] == [("toggle", "marks")]
    assert g.state.mode == "focus" and g.state.knob == 1  # still focused; the knob picked up where it was
    assert [e["knob"] for e in g.log.entries if e["kind"] == "toggle"] == [1]
    events, t = run(g, hold(pinch, 0.3) + hold(open_palm, 0.3), t)
    assert kinds_of(events) == ["toggle"]


def test_pinch_and_lift_commits_without_toggling():
    g, t = marks_spread()
    events, _ = run(g, hold(pinch, 0.2) + lift(0.4, 0.2 * H) + hold(pinch, 0.3, origin=(960, 600 - 0.2 * H))
                    + hold(one, 0.3), t)
    assert kinds_of(events) == ["commit"] and events[0].op == "marks"


def test_the_focusing_fold_and_a_dropped_pinch_do_not_toggle():
    g = Grammar((W, H))
    _, t = run(g, hold(two, 0.3))
    # A fold that ends pinched and stays pinched while the marks open: that pinch focused, it doesn't toggle.
    events, t = run(g, lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 6) + hold(pinch, 0.3), t)
    assert kinds_of(events) == ["focus"]
    g.open_marks(t)
    events, t = run(g, hold(pinch, 0.3) + hold(open_palm, 0.3), t)
    assert "toggle" not in kinds_of(events)
    # A pinch taken out of view (not long enough to back out) and brought back open: no toggle.
    events, t = run(g, hold(pinch, 0.3) + [None] * 10 + hold(open_palm, 0.3), t)
    assert kinds_of(events) == [] and g.state.mode == "focus"


def test_marks_open_only_on_a_focused_sentence_in_prepare():
    g = Grammar((W, H))
    assert not g.open_marks(0.0)  # nothing focused
    g, t = prepare_sentence_focus()
    g.operations = False  # Review
    assert not g.open_marks(t) and g.state.op is None
    _, t = run(g, hold(open_palm, 0.3), t)
    assert g.state.op is None
    g.operations = True
    assert g.open_marks(t) and g.state.op == "marks"  # the m key
    g2, t2 = ring_focus()  # a focused word: the open palm is the ring, as before
    assert not g2.open_marks(t2) and g2.state.op == "ring"


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


def test_whole_hand_below_bottom_band_counts_as_dropped():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3))
    events, _ = run(g, hold(pinch, 1.1, origin=(960, H + 150)), t)  # fingertips in the band too
    assert [e.kind for e in events] == ["back"]


def test_low_wrist_with_raised_fingers_stays_focused_and_operates():
    # Chest height on a laptop camera: wrist at or below the bottom edge.
    low = (960, H * 0.98)
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3, origin=low) + hold(pinch, 0.3, origin=low))
    events, t = run(g, hold(open_palm, 1.5, origin=low), t)
    assert events == [] and g.state.mode == "focus" and g.state.op == "ring"
    assert g.state.drop_progress == 0.0


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


# --- modes: fist to start, count-in, command zone ---------------------------------

ZONE = (1100, 250)  # palm centre well inside the command zone (top right)


def kinds(events):
    return [e.kind for e in events]


def test_fist_held_one_second_starts_the_count_in():
    m = ModeMachine((W, H))
    events, t = run(m, hold(fist, 0.9))
    assert events == [] and 0.5 < m.start_progress < 1.0
    events, t = run(m, hold(fist, 0.4), t)
    assert kinds(events) == ["count_in"] and m.mode == "count_in"
    assert m.tick(t) == []
    assert kinds(m.tick(t + REHEARSE.count_in_s)) == ["take_start"] and m.mode == "rehearse"


def test_fist_while_focused_does_not_start_a_take():
    m = ModeMachine((W, H))
    _, t = run(m, hold(one, 0.3) + hold(pinch, 0.3))
    assert m.state.mode == "focus"
    events, _ = run(m, hold(fist, 1.5), t)
    assert "count_in" not in kinds(events) and m.mode == "prepare"


def test_fist_formed_from_another_pose_does_not_start_a_take():
    # Browsing, then closing the hand (a slow pinch, a hand resting closed):
    # the fist wasn't raised as one, so it's not a request for a take.
    for before in (one, flat, pinch):
        m = ModeMachine((W, H))
        events, t = run(m, hold(before, 0.5) + hold(fist, 2.5))
        assert "count_in" not in kinds(events) and m.start_progress == 0.0, before.__name__


def test_drop_the_hand_then_raise_a_fist_starts_a_take():
    m = ModeMachine((W, H))
    _, t = run(m, hold(one, 0.5) + hold(fist, 1.5))
    assert m.mode == "prepare"
    _, t = run(m, [None] * 12, t)  # 0.4 s out of view: a new hand
    events, _ = run(m, hold(fist, 1.3), t)
    assert kinds(events) == ["count_in"]


def test_label_flip_is_not_a_new_hand():
    # MediaPipe relabels Left/Right mid-move; the curled hand keeps its history.
    m = ModeMachine((W, H))
    frames = hold(one, 0.5, label="Left") + hold(fist, 0.3, label="Left") + hold(fist, 1.5, label="Right")
    events, _ = run(m, frames)
    assert "count_in" not in kinds(events)


def test_a_fist_rising_through_none_still_counts():
    m = ModeMachine((W, H))
    v_sign = hand({8: (-70, -200), 12: (40, -200)})  # fingers spread: NONE
    assert classify(features(v_sign)) == NONE
    events, _ = run(m, [v_sign] * 8 + hold(fist, 1.3))
    assert kinds(events) == ["count_in"]


def rehearsing(m=None, t=0.0):
    m = m or ModeMachine((W, H))
    events, t = run(m, hold(fist, 1.3), t)
    assert kinds(events) == ["count_in"]
    t += REHEARSE.count_in_s
    assert kinds(m.tick(t)) == ["take_start"]
    return m, t


def test_rehearse_ignores_the_grammar_outside_the_zone():
    m, t = rehearsing()
    events, t = run(m, hold(one, 0.3) + hold(pinch, 0.3) + hold(fist, 1.5) + hold(open_palm, 2.0), t)
    assert events == [] and m.mode == "rehearse" and m.state.mode == "idle"


def test_open_palm_held_in_zone_stops_the_take_into_review():
    m, t = rehearsing()
    events, t = run(m, hold(open_palm, 1.2, origin=ZONE), t)
    assert events == [] and m.zone.active and 0.5 < m.zone.hold_progress < 1.0
    events, t = run(m, hold(open_palm, 0.6, origin=ZONE), t)
    assert kinds(events) == ["take_stop"] and m.mode == "review"
    assert not m.grammar.operations

    # Review browses and focuses like Prepare, without the operations.
    _, t = run(m, hold(one, 0.3) + hold(pinch, 0.3) + hold(open_palm, 0.5), t)
    assert m.state.mode == "focus" and m.state.op is None


def flick(dx, n=6, start=ZONE, make=one):
    return [make(origin=(start[0] + dx * i / n, start[1])) for i in range(1, n + 1)]


def test_flick_in_zone_is_next_section_once():
    m, t = rehearsing()
    events, t = run(m, hold(one, 0.4, origin=ZONE) + flick(-150) + hold(one, 0.3, origin=(ZONE[0] - 150, ZONE[1])), t)
    assert kinds(events) == ["next_section"]
    # Flicking straight back inside the cooldown does nothing.
    events, t = run(m, flick(150, start=(ZONE[0] - 150, ZONE[1])), t)
    assert events == []


def test_slow_moves_and_hands_arriving_in_the_zone_are_not_flicks():
    m, t = rehearsing()
    events, t = run(m, hold(one, 0.4, origin=ZONE) + flick(-150, n=30), t)  # 1 s
    assert events == []
    # Swept in from outside the zone: never settled there before moving.
    m, t = rehearsing()
    events, _ = run(m, flick(300, start=(760, 250)) + hold(one, 0.3, origin=(1060, 250)), t)
    assert events == []


def test_open_palm_in_zone_during_count_in_cancels():
    for back_to in ("prepare", "review"):
        m, t = ModeMachine((W, H)), 0.0
        if back_to == "review":
            m, t = rehearsing(m)
            _, t = run(m, hold(open_palm, 1.8, origin=ZONE), t)
            assert m.mode == "review"
        events, t = run(m, hold(fist, 1.3), t)
        assert m.mode == "count_in"
        events, t = run(m, hold(open_palm, 1.8, origin=ZONE), t)
        assert kinds(events) == ["count_in_cancel"] and m.mode == back_to
        assert m.tick(t + REHEARSE.count_in_s) == []


# Close to a laptop camera: palm ~270 px, as measured in the session traces.
BIG = 2.7
BIG_ZONE = (1100, 560)  # wrist; the palm centre sits ~200 px higher, inside the zone


def test_wrist_flick_of_a_big_close_hand_is_next_section():
    m, t = rehearsing()
    # Swing from the wrist: the palm centre barely moves, the fingertips travel.
    swing = [one(origin=BIG_ZONE, scale=BIG, rotate=-35 * i / 6) for i in range(1, 7)]  # 0.2 s
    events, t = run(m, hold(one, 0.3, origin=BIG_ZONE, scale=BIG) + swing, t)
    assert kinds(events) == ["next_section"]


def test_flick_survives_a_tracking_dropout_and_a_label_flip():
    m, t = rehearsing()
    frames = hold(one, 0.3, origin=ZONE, label="Left") + [
        one(origin=(ZONE[0] - 40, ZONE[1]), label="Left"), None, None,
        one(origin=(ZONE[0] - 120, ZONE[1]), label="Right"), one(origin=(ZONE[0] - 150, ZONE[1]), label="Right"),
    ]
    events, _ = run(m, frames, t)
    assert kinds(events) == ["next_section"]


def test_open_palm_hold_rides_out_label_flips():
    m, t = rehearsing()
    frames = [open_palm(origin=ZONE, label="Left" if (i // 5) % 2 else "Right") for i in range(round(1.7 / DT))]
    events, _ = run(m, frames, t)
    assert kinds(events) == ["take_stop"]


def test_open_palm_in_zone_goes_from_review_back_to_prepare():
    m, t = rehearsing()
    _, t = run(m, hold(open_palm, 1.7, origin=ZONE), t)
    assert m.mode == "review"
    _, t = run(m, [None] * 10, t)  # hand down between the two holds
    events, t = run(m, hold(open_palm, 1.7, origin=ZONE), t)
    assert kinds(events) == ["to_prepare"] and m.mode == "prepare" and m.grammar.operations
    # Prepare's operations are back, and a fist starts the next take.
    _, t = run(m, hold(one, 0.3) + hold(pinch, 0.3) + hold(open_palm, 0.3), t)
    assert m.state.op == "ring"
    _, t = run(m, [None] * 40, t)
    events, _ = run(m, hold(fist, 1.3), t)
    assert kinds(events) == ["count_in"]


# --- review: take dial and drills --------------------------------------------------

def reviewing():
    m, t = rehearsing()
    _, t = run(m, hold(open_palm, 1.7, origin=ZONE), t)
    assert m.mode == "review" and m.grammar.take_dial
    _, t = run(m, [None] * 10, t)
    return m, t


def sentence_focus(m, t):
    _, t = run(m, hold(two, 0.3), t)
    events, t = run(m, lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 6), t)
    assert kinds(events) == ["focus"] and m.state.level == "sentence"
    return t


def test_review_take_dial_steps_relative_to_the_start():
    m, t = reviewing()
    t = sentence_focus(m, t)
    step = OPS.take_step_deg
    _, t = run(m, hold(l_hand, 0.3, rotate=-15), t)  # wherever it starts is zero
    assert m.state.op == "take" and m.state.take_step == 0
    _, t = run(m, hold(l_hand, 0.2, rotate=-15 - step), t)
    assert m.state.take_step == -1
    _, t = run(m, hold(l_hand, 0.2, rotate=-15 + 2 * step), t)
    assert m.state.take_step == 2
    # Put down and picked up at another angle: continues from 2.
    _, t = run(m, hold(two, 0.3) + hold(l_hand, 0.3, rotate=30) + hold(l_hand, 0.2, rotate=30 - step), t)
    assert m.state.take_step == 1
    assert m.state.tone == 0.0  # not Prepare's tone dial


def test_no_take_dial_in_prepare_or_at_other_levels():
    m = ModeMachine((W, H))
    t = sentence_focus(m, 0.0)
    run(m, hold(l_hand, 0.3), t)
    assert m.state.op == "tone"
    m, t = reviewing()
    _, t = run(m, hold(one, 0.3) + hold(pinch, 0.3) + hold(l_hand, 0.5), t)
    assert m.state.mode == "focus" and m.state.op is None


def test_pinch_and_lift_on_a_focused_sentence_in_review_drills_it():
    m, t = reviewing()
    t = sentence_focus(m, t)
    events, t = run(m, hold(two, 0.2) + hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert kinds(events) == ["commit", "drill"] and m.mode == "count_in" and m.drill
    assert kinds(m.tick(t + REHEARSE.count_in_s)) == ["take_start"] and m.drill
    t += REHEARSE.count_in_s
    # No section flicks in a drill; the open palm stops it as usual.
    events, t = run(m, hold(one, 0.4, origin=ZONE) + flick(-150), t)
    assert "next_section" not in kinds(events)
    _, t = run(m, [None] * 40, t)
    events, t = run(m, hold(open_palm, 1.7, origin=ZONE), t)
    assert kinds(events) == ["take_stop"] and m.mode == "review" and not m.drill


def test_word_commit_in_review_is_not_a_drill():
    m, t = reviewing()
    _, t = run(m, hold(one, 0.3) + hold(pinch, 0.3) + hold(one, 0.2), t)
    events, _ = run(m, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert kinds(events) == ["commit"] and m.mode == "review"


# --- the keyboard fallback -------------------------------------------------------

def test_key_commands_make_the_same_transitions_as_the_gestures():
    from palmcards.gestures import GestureLog, ModeMachine

    log = GestureLog()
    m = ModeMachine((1280, 720), log)
    assert [e.kind for e in m.command("stop", 1.0)] == [] and m.mode == "prepare"  # nothing to stop
    assert [e.kind for e in m.command("start", 1.0)] == ["count_in"] and m.mode == "count_in"
    assert [e.kind for e in m.command("stop", 1.5)] == ["count_in_cancel"] and m.mode == "prepare"
    m.command("start", 2.0)
    assert [e.kind for e in m.tick(2.0 + 3.1)] == ["take_start"] and m.mode == "rehearse"
    assert [e.kind for e in m.command("next", 6.0)] == ["next_section"]
    assert [e.kind for e in m.command("start", 6.5)] == [] and m.mode == "rehearse"  # already recording
    assert [e.kind for e in m.command("stop", 7.0)] == ["take_stop"] and m.mode == "review"
    assert [e.kind for e in m.command("prepare", 8.0)] == ["to_prepare"] and m.mode == "prepare"
    m.drill = True
    m.command("start", 9.0)
    m.tick(12.5)
    assert m.command("next", 13.0) == []  # a drill has no next section
    keys = [e for e in log.entries if e["kind"] == "key"]
    assert [(k["command"], k["acted"]) for k in keys][:3] == [("stop", False), ("start", True), ("stop", True)]
