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
