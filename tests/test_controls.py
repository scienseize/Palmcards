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


def test_hear_it_speaks_the_sentence_stressing_the_word():
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    view = focused(OpsView(kind="ring", picked=2))  # the word, stress, hear it
    speaker, log = FakeSpeaker(), GestureLog()
    main.apply_event(GestureEvent("commit", 1.0, "word", "ring"), view, ov, log, speaker)
    assert speaker.said == [(["Thank", "you", "for", "being", "here."], {2, 3})]  # its own stress mark too
    assert view.note.startswith("SPEAKING") and log.entries[-1]["kind"] == "hear"


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
