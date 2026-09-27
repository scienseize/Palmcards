"""Prepare's operations report only what really happened, and the keyboard
fallback runs a whole take with no hand ever seen."""

from dataclasses import replace

import pytest

import main
from palmcards import gestures
from palmcards.config import REHEARSE
from palmcards.gestures import GestureEvent, GestureLog
from palmcards.notes import parse_text
from palmcards.render import Hit, OpsView, TextOverlay, ViewState
from tests.test_app_lifecycle import Rig

TEXT = "Thank you for *being* here. We started with one question."


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say_words(self, words, stressed=frozenset()):
        self.said.append((words, set(stressed)))


def focused(ops: OpsView, level="word", hit=Hit(0, 2)):
    return ViewState(app="prepare", mode="focus", level=level, focus=hit, hover=hit, ops=ops)


def test_hear_it_speaks_the_selected_sentence():
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    speaker, log = FakeSpeaker(), GestureLog()
    assert main.hear_sentence(1, ov, speaker, log, 1.0) == "SPEAKING SENTENCE"
    assert speaker.said == [(["We", "started", "with", "one", "question."], set())]
    assert log.entries[-1]["kind"] == "hear" and log.entries[-1]["sentence"] == 1


def test_hear_it_reports_failure():
    class BrokenSpeaker:
        def say_words(self, words):
            raise OSError("unavailable")

    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    log = GestureLog()
    assert main.hear_sentence(0, ov, BrokenSpeaker(), log, 1.0).startswith("COULD NOT SPEAK")
    assert not log.entries


@pytest.mark.parametrize("app", ["prepare", "review"])
def test_sentence_palm_requires_selection_and_a_hold_and_rearms(app):
    palm = main.SentencePalm()
    view = focused(OpsView(), level="sentence", hit=Hit(0, None))
    view.app = app
    view.mode = "browse"
    assert not palm.update(view, True, 0)
    assert not palm.update(view, True, 2)
    view.mode = "focus"
    assert not palm.update(view, True, 3)
    assert not palm.update(view, True, 3.3)
    assert palm.update(view, True, 3.7)
    assert not palm.update(view, True, 5)
    assert not palm.update(view, False, 6)
    assert not palm.update(view, True, 7)
    assert palm.update(view, True, 7.7)
    view.focus = Hit(1, None)
    assert not palm.update(view, True, 8)
    assert palm.update(view, True, 8.7)
    view.level = "word"
    assert not palm.update(view, True, 9)
    assert not palm.update(view, True, 10)


def test_keeping_the_word_is_no_change():
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    view = focused(OpsView(kind="ring", picked=0))
    speaker = FakeSpeaker()
    main.apply_event(GestureEvent("commit", 1.0, "word", "ring"), view, ov, GestureLog(), speaker)
    assert view.note == "NO CHANGE" and speaker.said == []


@pytest.mark.parametrize("op, level, what", [("tone", "sentence", "TONE"), ("stretch", "paragraph", "LENGTH")])
def test_unavailable_edits_never_claim_a_commit(op, level, what):
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    view = focused(OpsView(kind=op), level=level, hit=Hit(0, None))
    log = GestureLog()
    main.apply_event(GestureEvent("commit", 1.0, level, op, value=0.4), view, ov, log, FakeSpeaker())
    assert view.note == f"{what} EDITS ARE NOT AVAILABLE YET: NOTHING CHANGED"
    assert "COMMITTED" not in view.note and log.entries[-1]["kind"] == "commit_stub"


def test_the_keyboard_runs_a_take_while_tracking_sees_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(gestures, "REHEARSE", replace(REHEARSE, count_in_s=0.3))
    rig = Rig(tmp_path, monkeypatch, keys={2: ord("t"), 60: ord("n"), 80: ord("x"), 100: ord("q")})
    monkeypatch.setattr(main, "ModeMachine", gestures.ModeMachine)  # the real machine, not a script
    assert rig.run() == 0
    take = rig.session().take(1)
    assert take.status == "saved" and take.duration_s > 0.1
    assert [s["section"] for s in take.sections] == [0, 1]
    rig.assert_closed_once()
