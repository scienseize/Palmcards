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

def test_word_flow_browse_focus_ring_turn_commit():
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
    g.set_ring_labels(t, RING_NODES)
    _, t = run(g, hold(l_hand, 0.3, rotate=-10), t)  # the knob starts wherever the L is
    assert g.state.turning and g.state.ring_pick == "imparted"
    _, t = run(g, hold(l_hand, 0.4, rotate=-10 + 32), t)  # two steps clockwise (26.5 deg needed)
    assert (g.state.ring_pick, g.state.ring_k, g.state.ring_turn) == ("inflicted", 2, 2)
    steps = [e for e in g.log.entries if e.get("op") == "ring_step"]
    assert [(e["node"], e["word"], e["dir"]) for e in steps] == [(1, "stamped", 1), (2, "inflicted", 1)]

    events, t = run(g, hold(pinch, 0.2, rotate=22) + lift(0.4, 0.2 * H, make=lambda **kw: pinch(rotate=22, **kw)), t)
    assert [(e.kind, e.op) for e in events] == [("commit", "ring")]
    assert (g.state.mode, g.state.level, g.state.op) == ("browse", "word", None)


RING_NODES = ("imparted", "stamped", "inflicted", "imposed", "hear it")


def ring_turned(steps_deg=(0.0,)):
    """A focused word, its ring open with RING_NODES, an L turned through the given tilts."""
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3) + lift(0.4, 0.2 * H) + hold(open_palm, 0.3))
    g.set_ring_labels(t, RING_NODES)
    for deg in steps_deg:
        _, t = run(g, hold(l_hand, 0.3, rotate=deg), t)
    return g, t


def test_turning_left_wraps_round_the_ring():
    g, _ = ring_turned((0.0, -14.0))
    assert g.state.ring_pick == "hear it" and g.state.ring_turn == -1


def test_turning_back_to_the_start_is_the_original():
    g, _ = ring_turned((0.0, 30.0, 3.0))
    assert g.state.ring_pick == "imparted" and g.state.ring_turn == 0
    words = [e["word"] for e in g.log.entries if e.get("op") == "ring_step"]
    assert words == ["stamped", "inflicted", "stamped", "imparted"]


def test_alternatives_arriving_mid_turn_keep_the_pick():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3) + lift(0.4, 0.2 * H) + hold(open_palm, 0.3))
    g.set_ring_labels(t, ("imparted", "imposed", "hear it"))
    _, t = run(g, hold(l_hand, 0.3) + hold(l_hand, 0.3, rotate=16), t)
    assert g.state.ring_pick == "imposed"
    g.set_ring_labels(t, RING_NODES)
    _, t = run(g, hold(l_hand, 0.2, rotate=16), t)
    assert g.state.ring_pick == "imposed"
    assert any(e["kind"] == "ring_nodes" and e["nodes"] == list(RING_NODES) for e in g.log.entries)
    _, t = run(g, hold(l_hand, 0.3, rotate=31), t)
    assert g.state.ring_pick == "hear it"


def test_a_thumb_drifting_in_keeps_turning_the_ring():
    g, t = ring_turned((0.0,))
    _, t = run(g, hold(drifted, 0.3, rotate=16), t)
    assert g.state.turning and g.state.ring_pick == "stamped"


def test_closing_into_a_pinch_keeps_the_ring_node():
    g, t = ring_turned((0.0, 16.0))
    assert g.state.ring_pick == "stamped"
    curl = approach(start=16.0) + hold(pinch, 0.2, rotate=-24)
    events, _ = run(g, curl + lift(0.4, 0.2 * H, make=lambda **kw: pinch(rotate=-24, **kw)), t)
    assert [(e.kind, e.op) for e in events] == [("commit", "ring")]
    assert g.state.ring_pick == "stamped"  # not stepped back by the curl's -40 deg (or put back if it was)


def test_dropping_the_hand_backs_out_of_the_ring():
    g, t = ring_turned((0.0, 16.0))
    events, _ = run(g, [None] * round((TIMING.drop_s + 0.2) / DT), t)
    assert [(e.kind, e.op) for e in events] == [("back", "ring")]
    assert g.state.op is None and not g.state.turning


def test_a_new_focus_starts_the_ring_again():
    g, t = ring_turned((0.0, 16.0))
    _, t = run(g, [None] * round((TIMING.drop_s + 0.2) / DT), t)
    _, t = run(g, hold(one, 0.3) + hold(pinch, 0.3) + lift(0.4, 0.2 * H) + hold(open_palm, 0.3), t)
    assert g.state.op == "ring" and g.state.ring_pick == "imparted" and g.state.ring_turn == 0


def curl_into_pinch(n=6, drop=45):
    """A pointing hand closing into a pinch as people do it: the thumb comes in
    and the index tip sinks toward it (the cursor would follow it down)."""
    out = []
    for i in range(1, n + 1):
        k = i / n
        tip = (-30, -200 + drop * k)
        thumb = tuple(np.add(np.multiply(THUMB_IN, 1 - k), np.multiply((tip[0] + 3, tip[1] + 3), k)))
        out.append(hand({8: tip}, thumb=thumb))
    return out + [hand({8: (-30, -200 + drop)}, thumb=(-27, -197 + drop))] * 8


def test_pinching_a_word_focuses_the_word_pointed_at_before_the_curl():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.6))
    before = g.state.cursor
    events, t = run(g, curl_into_pinch(), t)
    assert [e.kind for e in events] == ["focus"]
    assert g.state.cursor == before  # not dragged down by the curl
    assert any(e["kind"] == "rewind" and e["op"] == "cursor" for e in g.log.entries)


def test_a_pointing_thumb_resting_near_the_index_does_not_hold_the_cursor():
    g = Grammar((W, H))
    _, t = run(g, hold(one, 0.4))
    assert features(one()).pinch_dist < 0.9  # the thumb tucked in, near the index tip
    _, t = run(g, hold(one, 0.4, origin=(1000, 560)), t)  # browsing on
    assert not g.state.closing and g.state.cursor != (0.0, 0.0)
    moved = g.state.cursor
    _, t = run(g, hold(one, 0.4, origin=(900, 640)), t)
    assert g.state.cursor != moved  # it still follows


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


DRIFTED_THUMB = (-60, -110)  # in, near the index knuckle: 0.95 palms from the index tip (as in real L-hands)


def drifted(**kw):
    return hand({8: (-30, -200)}, thumb=DRIFTED_THUMB, **kw)


def approach(n=6, turn=-40.0, start=0.0, **kw):
    """An L closing into a pinch: the thumb moves onto the index tip while the
    index curls, which turns the angle the dials read (by `turn` degrees)."""
    out = []
    for i in range(1, n + 1):
        k = i / n
        thumb = tuple(np.add(np.multiply(THUMB_OUT, 1 - k), np.multiply((-27, -197), k)))
        out.append(hand({8: (-30, -200)}, thumb=thumb, rotate=start + turn * k, **kw))
    return out


def test_closing_into_a_pinch_keeps_the_tone():
    g, t = prepare_sentence_focus()
    _, t = run(g, hold(l_hand, 0.3) + hold(l_hand, 0.3, rotate=22.5), t)
    assert g.state.tone == pytest.approx(0.5, abs=0.02)
    events, _ = run(g, approach(start=22.5) + hold(pinch, 0.2, rotate=22.5 - 40) + lift(0.4, 0.2 * H), t)
    assert [(e.kind, e.op) for e in events] == [("commit", "tone")]
    assert events[0].value == pytest.approx(0.5, abs=0.02)  # not the curl's -0.4


def test_closing_into_a_pinch_keeps_the_stretch():
    g = Grammar((W, H))
    _, t = run(g, hold(flat, 0.3))
    _, t = run(g, lerp_frames(hand, FLAT_TIPS, FLAT_FOLDED, 6), t)

    def pair(gap, right=None):
        return [right or l_hand(origin=(960, 500), label="Right"), l_hand(origin=(960 - gap, 500), label="Left")]

    _, t = run(g, [pair(300)] * 10 + [pair(450)] * 10, t)
    assert g.state.stretch == pytest.approx(1.5, abs=0.01)
    closing = [pair(450, right=h) for h in approach(origin=(960, 500), label="Right")]
    _, t = run(g, closing + [pair(450, right=pinch(origin=(960, 500), rotate=-40, label="Right"))] * 5, t)
    assert g.state.stretch == pytest.approx(1.5, abs=0.01) and g.state.closing


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


def test_an_open_palm_on_a_focused_sentence_changes_nothing():
    g, t = prepare_sentence_focus()
    _, t = run(g, hold(open_palm, 0.3), t)
    assert g.state.op is None  # no suggested marks any more
    _, t = run(g, hold(l_hand, 0.3, rotate=-10) + hold(l_hand, 0.3, rotate=12.5), t)
    assert g.state.op == "tone" and g.state.tone > 0.4
    tone = g.state.tone
    _, t = run(g, hold(open_palm, 0.3), t)
    assert g.state.op == "tone" and g.state.tone == pytest.approx(tone)  # the tone preview stays
    assert [e["op"] for e in g.log.entries if e["kind"] == "op"] == ["tone"]
    events, _ = run(g, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert [(e.kind, e.op) for e in events] == [("commit", "tone")]


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


def paragraph_focus(m, t):
    _, t = run(m, hold(flat, 0.3), t)
    events, t = run(m, lerp_frames(hand, FLAT_TIPS, FLAT_FOLDED, 6), t)
    assert kinds(events) == ["focus"] and m.state.level == "paragraph"
    return t


def test_no_take_dial_in_prepare_or_at_other_levels():
    m = ModeMachine((W, H))
    t = sentence_focus(m, 0.0)
    run(m, hold(l_hand, 0.3), t)
    assert m.state.op == "tone"
    m, t = reviewing()
    t = paragraph_focus(m, t)
    _, t = run(m, hold(l_hand, 0.5), t)
    assert m.state.mode == "focus" and m.state.op is None


def test_one_finger_in_review_browses_sentences_and_a_pinch_focuses_one():
    m, t = reviewing()
    _, t = run(m, hold(one, 0.4), t)
    assert m.state.mode == "browse" and m.state.level == "sentence"
    before = m.state.cursor
    events, t = run(m, curl_into_pinch(), t)
    assert kinds(events) == ["focus"] and m.state.level == "sentence"
    assert m.state.cursor == before  # the curl doesn't drag the highlight to the next line
    # Two fingers are the same level: no new browse entry, and a fold still focuses.
    m, t = reviewing()
    _, t = run(m, hold(one, 0.3) + hold(two, 0.3), t)
    assert [e["level"] for e in m.log.entries if e["kind"] == "browse"] == ["sentence"]
    events, t = run(m, lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 6), t)
    assert kinds(events) == ["focus"] and m.state.level == "sentence"


def test_one_finger_in_prepare_still_browses_words():
    m, t = reviewing()
    _, t = run(m, hold(open_palm, 1.7, origin=ZONE), t)
    assert m.mode == "prepare"
    _, t = run(m, [None] * 10 + hold(one, 0.4), t)
    assert m.state.level == "word"
    events, t = run(m, hold(pinch, 0.3), t)
    assert kinds(events) == ["focus"] and m.state.level == "word"


def test_a_held_open_palm_on_a_focused_unit_plays_it_once_per_hold():
    m, t = reviewing()
    t = sentence_focus(m, t)
    events, t = run(m, hold(open_palm, OPS.play_hold_s - 0.2), t)
    assert "palm_hold" not in kinds(events)  # not held long enough yet
    events, t = run(m, hold(open_palm, 1.5), t)
    assert [(e.kind, e.level) for e in events] == [("palm_hold", "sentence")]  # once, however long it's held
    events, t = run(m, hold(two, 0.3) + hold(open_palm, 1.0), t)
    assert [(e.kind, e.level) for e in events] == [("palm_hold", "sentence")]  # let go and hold again: again
    _, t = run(m, [None] * 40, t)  # back out
    t = paragraph_focus(m, t)
    events, t = run(m, hold(open_palm, 1.0), t)
    assert [(e.kind, e.level) for e in events] == [("palm_hold", "paragraph")]
    assert [e["level"] for e in m.log.entries if e["kind"] == "palm_hold"] == ["sentence", "sentence", "paragraph"]


def playing(m, t):
    """Review, a sentence focused and playing: the palm that started it still up."""
    t = sentence_focus(m, t)
    events, t = run(m, hold(open_palm, 1.0), t)
    assert kinds(events) == ["palm_hold"]
    m.grammar.set_focus_hold(t, "play")  # the app, as the audio starts
    return t


def low(**kw):
    return two(origin=(960, 0.95 * H + 220), **kw)  # every landmark below the drop band


def test_while_it_plays_a_dropped_or_lowered_hand_keeps_the_focus():
    m, t = reviewing()
    t = playing(m, t)
    focused_at = next(e["t"] for e in reversed(m.log.entries) if e["kind"] == "focus")
    events, t = run(m, [None] * 90 + hold(low, 2.0), t)  # 3 s out of frame, then low
    assert events == [] and m.state.mode == "focus" and m.state.drop_progress == 0.0
    events, t = run(m, hold(two, 0.5), t)  # back: the same focus carries on
    assert events == [] and m.state.mode == "focus"
    assert next(e["t"] for e in reversed(m.log.entries) if e["kind"] == "focus") == focused_at


def test_when_playback_ends_with_the_hand_down_the_drop_timer_starts_afresh():
    m, t = reviewing()
    t = playing(m, t)
    events, t = run(m, [None] * 60, t)  # 2 s down: held
    assert events == [] and m.state.mode == "focus"
    m.grammar.set_focus_hold(t, None)  # the audio ends
    events, t2 = run(m, [None] * round((TIMING.drop_s - 0.2) / DT), t)
    assert events == [] and m.state.mode == "focus"  # not at once: a full drop_s from the end
    events, _ = run(m, [None] * 12, t2)
    assert kinds(events) == ["back"]
    assert [e["reason"] for e in m.log.entries if e["kind"] == "focus_hold"] == ["play", None]


def test_a_new_palm_stops_playback_and_the_palm_that_started_it_does_not():
    m, t = reviewing()
    t = playing(m, t)
    events, t = run(m, hold(open_palm, 3.0), t)  # the starting palm, held on
    assert events == [] and m.grammar.focus_hold == "play"
    events, t = run(m, hold(two, 0.3) + hold(open_palm, 0.3), t)  # (0.15 s of it to settle)
    assert events == [] and 0 < m.state.palm_progress < 1  # the STOP bar filling
    events, t = run(m, hold(open_palm, 0.3), t)
    assert [(e.kind, e.level) for e in events] == [("palm_stop", "sentence")]
    assert m.grammar.focus_hold is None and m.state.mode == "focus" and m.state.palm_progress == 0.0
    events, t = run(m, hold(open_palm, 1.0), t)  # held on after the stop: it doesn't play again
    assert events == []
    events, t = run(m, hold(two, 0.3) + hold(open_palm, 1.0), t)  # a new palm: plays again
    assert kinds(events) == ["palm_hold"]


def test_a_palm_held_through_the_end_does_not_start_it_again():
    # Live (trace 20260927-165358, 136.45 s) a palm held from the start through the end
    # started the same sentence again 0.6 s after it ended.
    m, t = reviewing()
    t = playing(m, t)
    _, t = run(m, hold(open_palm, 2.0), t)
    m.grammar.set_focus_hold(t, None)  # it ends by itself, the palm still up
    events, t = run(m, hold(open_palm, 2.0), t)
    assert events == []
    events, t = run(m, hold(two, 0.3) + hold(open_palm, 1.0), t)  # a new palm
    assert kinds(events) == ["palm_hold"]


def test_a_palm_raised_again_after_leaving_the_frame_is_new():
    m, t = reviewing()
    t = playing(m, t)
    events, t = run(m, [None] * 10 + hold(open_palm, 0.6), t)
    assert kinds(events) == ["palm_stop"]


def test_in_prepare_a_held_palm_hears_a_sentence_but_not_a_paragraph_or_word():
    m = ModeMachine((W, H))
    t = sentence_focus(m, 0.0)
    events, t = run(m, hold(open_palm, 1.0), t)
    assert [(e.kind, e.level) for e in events] == [("palm_hold", "sentence")]
    _, t = run(m, [None] * 40, t)
    t = paragraph_focus(m, t)
    events, t = run(m, hold(open_palm, 1.0), t)
    assert "palm_hold" not in kinds(events)
    _, t = run(m, [None] * 40 + hold(one, 0.3) + hold(pinch, 0.3), t)
    assert m.state.level == "word" and m.state.mode == "focus"
    events, t = run(m, hold(open_palm, 1.0), t)  # the options ring, not a play
    assert "palm_hold" not in kinds(events) and m.state.op == "ring"


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


def test_paragraph_commit_in_review_is_not_a_drill():
    m, t = reviewing()
    t = paragraph_focus(m, t)
    _, t = run(m, hold(flat, 0.2), t)
    events, _ = run(m, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert kinds(events) == ["commit"] and m.mode == "review"


def test_one_finger_sentence_in_review_drills_with_pinch_and_lift():
    m, t = reviewing()
    _, t = run(m, hold(one, 0.3) + hold(pinch, 0.3) + hold(one, 0.2), t)
    assert m.state.mode == "focus" and m.state.level == "sentence"
    events, _ = run(m, hold(pinch, 0.2) + lift(0.4, 0.2 * H), t)
    assert kinds(events) == ["commit", "drill"] and m.mode == "count_in"


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


# --- choosing by pointing (the marks, Review's takes) ------------------------------

def takes_focus():
    """Review, a sentence focused: an L points at its take chips."""
    m, t = reviewing()
    return m, sentence_focus(m, t)


def test_the_pointer_starts_with_an_l_and_follows_the_fingertip():
    g, t = takes_focus()
    _, t = run(g, hold(one, 0.3), t)
    assert not g.state.pointing and g.state.point is None  # pointing alone doesn't start it: an L does
    _, t = run(g, hold(l_hand, 0.3), t)
    assert g.state.pointing and g.state.point == pytest.approx((0.0, 0.0), abs=0.01)
    _, t = run(g, hold(l_hand, 1.0, origin=(960 + 100, 600)), t)  # (smoothed like the browse cursor)
    box_w = g.grammar.cursor.box[2] - g.grammar.cursor.box[0]  # px per hand-box width (the reach preference sets it)
    assert g.state.point == pytest.approx((100 / box_w, 0.0), abs=0.01)  # the fingertip moved right
    _, t = run(g, hold(drifted, 0.5, origin=(960 + 100, 600 - 60)), t)  # thumb in: still pointing
    assert g.state.pointing and g.state.point[1] < -0.05
    kept = g.state.point
    _, t = run(g, hold(open_palm, 0.3), t)
    assert not g.state.pointing and g.state.point == kept  # put down: the point stays
    _, t = run(g, hold(l_hand, 0.3, origin=(800, 500)) + hold(l_hand, 0.3, origin=(800, 500)), t)
    assert g.state.point == pytest.approx(kept, abs=0.01)  # picked up elsewhere: it carries on, no jump
    assert "rewind" not in kinds(run(g, [], t)[0])


def test_a_pinch_takes_the_point_back_to_before_the_curl():
    g, t = takes_focus()
    _, t = run(g, hold(l_hand, 0.3) + hold(l_hand, 0.5, origin=(1000, 600)), t)
    before = g.state.point
    events, t = run(g, curl_into_pinch(drop=60, n=8)[:12], t)  # the index tip sinks as it curls
    assert "rewind" in kinds(events)
    (ev,) = [e for e in events if e.kind == "rewind"]
    assert ev.value < ev.t and g.state.point == pytest.approx(before, abs=0.02) and g.state.closing
    events, t = run(g, lift(0.4, 0.2 * H), t)
    assert ("commit", "take") in [(e.kind, e.op) for e in events]  # a drill of the sentence, from the take picked

def test_review_an_l_starts_pointing_at_the_takes():
    m, t = reviewing()
    t = sentence_focus(m, t)
    assert m.state.op is None
    _, t = run(m, hold(l_hand, 0.3) + hold(l_hand, 0.4, origin=(960, 600 + 80)), t)
    assert m.state.op == "take" and m.state.pointing and m.state.point[1] > 0.05
    assert m.state.tone == 0.0  # not Prepare's tone dial
