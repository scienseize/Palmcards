"""Regression: recorded hand landmarks (samples/gestures/) replayed through the
gesture code must give what the app recognised live. See palmcards.replay.

A sample with a `known_issue` is an expected failure (strict): when the bug
is fixed it starts passing, the test fails, and the note should be removed
(re-cut it with scripts/cut_gesture_samples.py).
"""

import pytest

from palmcards.replay import SAMPLES_DIR, differences, load, replay


def _params():
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        issue = load(path).get("known_issue")
        marks = [pytest.mark.xfail(strict=True, reason=issue)] if issue else []
        yield pytest.param(path, id=path.stem, marks=marks)


def test_there_are_samples():
    assert len(list(SAMPLES_DIR.glob("*.json"))) >= 9


@pytest.mark.parametrize("path", list(_params()))
def test_replay_matches_live(path):
    sample = load(path)
    assert differences(sample["expected"], replay(sample)) == []


def test_differences_reports_order_and_timing():
    a = [{"t": 1.0, "kind": "focus", "level": "word"}, {"t": 2.0, "kind": "commit", "level": "word"}]
    assert differences(a, a) == []
    late = [a[0], {**a[1], "t": 2.5}]
    assert differences(a, late) == ["commit word at 2.50 s, expected 2.00 s"]
    assert differences(a, a[:1]) and differences(a, [a[1], a[0]])


def test_the_focus_hold_is_given_back_from_the_inputs():
    from tests.test_gestures import DT, TWO_FOLDED, TWO_TIPS, hand, hold, lerp_frames, open_palm, two

    hands = hold(two, 0.3) + lerp_frames(hand, TWO_TIPS, TWO_FOLDED, 6) + hold(open_palm, 1.0) + [None] * 150
    frames = [{"t": round(i * DT, 3), "hands": [] if h is None else
               [{"label": "", "points": [round(float(v)) for v in h.points.ravel()]}]} for i, h in enumerate(hands)]
    sample = {"frame_size": [1280, 720], "start_mode": "review", "frames": frames}
    plain = replay(sample)
    played = next(e["t"] for e in plain if e["kind"] == "palm_hold")
    assert [e["kind"] for e in plain] == ["focus", "palm_hold", "back"]  # no hold: the drop backs out
    sample["inputs"] = [{"t": played + 0.01, "focus_hold": "play"}, {"t": 3.5, "focus_hold": None}]
    held = replay(sample)
    assert [e["kind"] for e in held] == ["focus", "palm_hold", "back"]
    assert held[-1]["t"] == pytest.approx(3.5 + 1.0, abs=2 * DT)  # a full drop_s after the audio ended
