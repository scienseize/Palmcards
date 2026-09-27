"""Preferences, the first-run tutorial, hints for gestures that don't act,
high contrast and the visible hand area."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

import main
from palmcards import gestures, prefs, render
from palmcards.config import CURSOR, REHEARSE
from palmcards.gestures import FIST, GestureEvent, OPEN, PINCH
from palmcards.notes import parse_text
from palmcards.render import TextOverlay, ViewState, draw_hand_area
from palmcards.tutorial import STEPS, Tutorial
from tests.test_app_lifecycle import Rig


@pytest.fixture
def restore(monkeypatch):
    """Whatever apply() and set_contrast() change is put back after the test."""
    for module, name in ((gestures, "CURSOR"), (gestures, "REHEARSE"), (render, "C")):
        monkeypatch.setattr(module, name, getattr(module, name))


def test_preferences_load_save_and_clamp(tmp_path, capsys):
    path = tmp_path / "prefs.json"
    assert prefs.load(path) == prefs.Prefs()  # nothing saved yet: defaults
    prefs.save(prefs.Prefs(reach=5.0, start_hold_s=0.01, high_contrast=True), path)
    back = prefs.load(path)
    assert (back.reach, back.start_hold_s, back.high_contrast) == (1.4, 0.3, True)  # clamped to usable values
    path.write_text(json.dumps({"reach": 0.8, "from_a_newer_version": 1}))
    assert prefs.load(path).reach == 0.8  # unknown keys ignored
    path.write_text("{broken")
    assert prefs.load(path) == prefs.Prefs() and "ignoring" in capsys.readouterr().err


def test_reach_scales_the_hand_box_about_its_centre():
    assert prefs.hand_box(1.0) == pytest.approx(CURSOR.hand_box)
    x0, y0, x1, y1 = prefs.hand_box(0.6)
    assert (x1 - x0) == pytest.approx((CURSOR.hand_box[2] - CURSOR.hand_box[0]) * 0.6)
    assert x0 >= 0.5  # stays on the right half, clear of the notes


def test_apply_changes_only_what_preferences_own(restore):
    prefs.apply(prefs.Prefs(reach=0.7, start_hold_s=0.5, stop_hold_s=2.0, high_contrast=True))
    assert gestures.CURSOR.hand_box == pytest.approx(prefs.hand_box(0.7))
    assert gestures.CURSOR.edge_band == CURSOR.edge_band
    assert (gestures.REHEARSE.start_hold_s, gestures.REHEARSE.done_hold_s) == (0.5, 2.0)
    assert gestures.REHEARSE.count_in_s == REHEARSE.count_in_s and render.C.dim != render.COLORS.dim


def state(stable=None, first=None, mode="idle", level=None):
    primary = SimpleNamespace(stable=stable, first_pose=first) if stable else None
    return SimpleNamespace(primary=primary, mode=mode, level=level)


def test_the_tutorial_moves_on_as_each_gesture_is_done():
    t = Tutorial()
    assert t.card == (1, len(STEPS), STEPS[0][1])
    t.update(state("ONE"), [], 0.0)
    assert not t.update(state("ONE"), [], 0.3) and t.update(state("ONE"), [], 0.7)  # hand held up 0.5 s
    assert t.update(state("ONE", mode="browse", level="word"), [], 1.0)
    assert not t.update(state("TWO", mode="browse", level="word"), [], 1.1)  # still words
    assert t.update(state("TWO", mode="browse", level="sentence"), [], 1.2)
    assert t.update(state(), [GestureEvent("focus", 2.0, "sentence")], 2.0)
    assert t.update(state(), [GestureEvent("back", 3.0, "sentence")], 3.0)
    t.skip()  # Enter
    assert t.done and t.card is None
    t.restart()
    assert t.card[0] == 1


def test_hints_say_why_a_gesture_did_nothing():
    formed = state(FIST, first=PINCH, mode="browse")
    assert "RAISED CLOSED" in main.nonactivation_hint("prepare", formed, 0.0)
    assert main.nonactivation_hint("prepare", state(FIST, first=FIST), 0.0) == ""  # a real start
    assert main.nonactivation_hint("rehearse", state(OPEN, first=OPEN), 0.5) == ""  # not held long yet
    # An open palm stopped takes before the thumbs-up did: held on, it says what does.
    assert main.nonactivation_hint("rehearse", state(OPEN, first=OPEN), 1.0) == "TO STOP: THUMB UP, HELD"
    assert main.nonactivation_hint("count_in", state(OPEN, first=OPEN), 1.0) == "TO CANCEL: THUMB UP, HELD"


def test_contrast_tutorial_card_and_hand_area_are_drawn(restore):
    sentences = parse_text("Hello there. Good night all.").sentences
    base = np.full((720, 1280, 3), 128, np.uint8)
    TextOverlay(sentences, (1280, 720)).draw(base, ViewState())
    render.set_contrast(True)
    high = np.full((720, 1280, 3), 128, np.uint8)
    TextOverlay(sentences, (1280, 720)).draw(high, ViewState())
    assert (base != high).any()
    card = np.full((720, 1280, 3), 128, np.uint8)
    TextOverlay(sentences, (1280, 720)).draw(card, ViewState(tutorial=(2, 6, STEPS[1][1])))
    assert (card != high).any()
    area = np.full((720, 1280, 3), 128, np.uint8)
    draw_hand_area(area, gestures.RelativeCursor((1280, 720)))
    assert (area != 128).any()


def test_keys_in_the_app_save_preferences_to_its_file(tmp_path, monkeypatch, restore):
    keys = {2: ord("c"), **{3 + k: main.ENTER for k in range(len(STEPS))}, 20: ord("q")}
    rig = Rig(tmp_path, monkeypatch, script={}, keys=keys)
    assert rig.run() == 0
    saved = json.loads(rig.prefs_file.read_text())
    assert saved["high_contrast"] is True and saved["tutorial_done"] is True


def test_reduced_motion_and_sounds_are_preferences(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prefs, "path", lambda: tmp_path / "prefs.json")
    assert prefs.Prefs().reduced_motion is None and prefs.Prefs().sounds is False  # as macOS; silent
    prefs.apply(prefs.Prefs())
    assert render.REDUCED is False  # macOS's setting (off in tests)
    monkeypatch.setattr(prefs, "system_reduce_motion", lambda: True)
    prefs.apply(prefs.Prefs())
    assert render.REDUCED is True  # follows it
    prefs.apply(prefs.Prefs(reduced_motion=False))
    assert render.REDUCED is False  # unless set
    assert prefs.main(["set", "reduced_motion", "true"]) == 0 and prefs.load().reduced_motion is True
    assert prefs.main(["set", "reduced_motion", "auto"]) == 0 and prefs.load().reduced_motion is None
    assert prefs.main(["set", "sounds", "on"]) == 0 and prefs.load().sounds is True
