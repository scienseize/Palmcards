import numpy as np

from palmcards.gestures import (
    INDEX_PIP, INDEX_TIP, MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP, PINKY_PIP, PINKY_TIP,
    RING_PIP, RING_TIP, THUMB_TIP, WRIST, Hand, PrepareGestures,
)

FINGERS = {  # (pip, tip, x offset)
    "index": (INDEX_PIP, INDEX_TIP, -30),
    "middle": (MIDDLE_PIP, MIDDLE_TIP, 0),
    "ring": (RING_PIP, RING_TIP, 25),
    "pinky": (PINKY_PIP, PINKY_TIP, 50),
}


def make_hand(origin=(400, 500), extended=("index",), thumb=None, gesture="None", score=0.9):
    """Upright synthetic hand; hand size (wrist to middle knuckle) is 100 px."""
    pts = np.zeros((21, 2), np.float32)
    pts[WRIST] = (0, 0)
    pts[MIDDLE_MCP] = (0, -100)
    for name, (pip, tip, x) in FINGERS.items():
        pts[pip] = (x, -140)
        pts[tip] = (x, -200) if name in extended else (x, -110)
    pts[THUMB_TIP] = thumb if thumb is not None else (70, -60)
    return Hand(pts + np.array(origin, np.float32), gesture, score)


def pinched(origin=(400, 500)):
    return make_hand(origin, thumb=(-26, -200))  # 4 px from the index tip


def run(g, frames, t0=0.0, dt=1 / 30):
    events = []
    for i, hand in enumerate(frames):
        events += g.update(hand, t0 + i * dt)
    return events


def test_pose_checks():
    assert make_hand().is_pointing
    assert not make_hand(extended=("index", "middle", "ring", "pinky")).is_pointing
    assert not make_hand(extended=()).is_pointing
    assert make_hand().pinch_ratio > 1.0
    assert pinched().pinch_ratio < 0.1


def test_pointing_emits_hover_at_index_tip():
    g = PrepareGestures()
    (ev,) = g.update(make_hand(), 0.0)
    assert ev.kind == "hover"
    assert (ev.x, ev.y) == (370, 300)
    assert g.state == "point"


def test_pinch_tap_selects_where_pinch_started():
    g = PrepareGestures()
    events = run(g, [make_hand()] + [pinched()] * 5 + [make_hand()])
    kinds = [e.kind for e in events]
    assert kinds.count("select") == 1 and "scroll" not in kinds
    sel = next(e for e in events if e.kind == "select")
    assert abs(sel.x - 372) < 1 and abs(sel.y - 300) < 1


def test_pinch_drag_scrolls_and_does_not_select():
    g = PrepareGestures()
    frames = [pinched((400, 500 - 8 * i)) for i in range(15)] + [make_hand((400, 380))]
    events = run(g, frames)
    scrolls = [e for e in events if e.kind == "scroll"]
    assert scrolls and not any(e.kind == "select" for e in events)
    assert sum(e.dy for e in scrolls) < -40  # moved up


def test_hysteresis_keeps_a_loose_pinch():
    g = PrepareGestures()
    loose = make_hand(thumb=(-30 + 35, -200))  # ratio 0.35: between on and off
    events = run(g, [pinched(), loose, loose, pinched()])
    assert g.pinching and not any(e.kind == "select" for e in events)


def test_losing_the_hand_mid_pinch_does_not_select():
    g = PrepareGestures()
    events = run(g, [pinched(), pinched(), None, make_hand()])
    assert not any(e.kind == "select" for e in events)


def test_open_palm_hold_cancels_once():
    g = PrepareGestures()
    palm = make_hand(extended=("index", "middle", "ring", "pinky"), gesture="Open_Palm")
    events = run(g, [palm] * 60)  # 2 s at 30 fps
    assert [e.kind for e in events] == ["cancel"]


def test_open_palm_released_early_does_nothing_and_blip_is_tolerated():
    g = PrepareGestures()
    palm = make_hand(extended=("index", "middle", "ring", "pinky"), gesture="Open_Palm")
    other = make_hand(extended=("index", "middle", "ring", "pinky"))
    # A 3-frame (0.1 s) dropout inside the hold does not restart it.
    events = run(g, [palm] * 15 + [other] * 3 + [palm] * 15)
    assert [e.kind for e in events] == ["cancel"]
    g2 = PrepareGestures()
    assert run(g2, [palm] * 20 + [other] * 20) == []
