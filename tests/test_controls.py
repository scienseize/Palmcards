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


def test_a_held_palm_hears_the_focused_sentence_in_prepare_and_keeps_the_focus():
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    speaker, log = FakeSpeaker(), GestureLog()
    view = focused(OpsView(), level="sentence", hit=Hit(1, None))
    until = main.apply_event(GestureEvent("palm_hold", 1.0, "sentence"), view, ov, log, speaker)
    assert view.note == "SPEAKING SENTENCE" and until is not None
    assert speaker.said[0][0][0] == "We"
    assert view.focus == Hit(1, None)  # still focused: hold again to hear it again


class FakeTakes:
    def __init__(self):
        self.played = []

    def play_sentence(self, sentence):
        self.played.append(("sentence", sentence))
        return "PLAYING SENTENCE"

    def play_paragraph(self, sentences):
        self.played.append(("paragraph", list(sentences)))
        return "PLAYING PARAGRAPH"


def test_a_held_palm_plays_the_focused_sentence_or_paragraph_in_review():
    notes = parse_text("One two three. Four five six.\n\nSeven eight nine.")
    ov = TextOverlay(notes.sentences, (1280, 720))
    takes, log = FakeTakes(), GestureLog()
    view = focused(OpsView(), level="sentence", hit=Hit(1, None))
    view.app = "review"
    assert main.play_focus(view, ov, log, None, takes, 1.0) == "PLAYING SENTENCE"
    view.level = "paragraph"
    assert main.play_focus(view, ov, log, None, takes, 2.0) == "PLAYING PARAGRAPH"
    view.focus = Hit(2, None)
    main.play_focus(view, ov, log, None, takes, 3.0)
    assert takes.played == [("sentence", 1), ("paragraph", [0, 1]), ("paragraph", [2])]


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
    assert view.note == "PREVIEW NOT READY: SET UP THE OPTIONAL LLM"
    assert view.focus is not None and view.mode == "focus"
    assert "COMMITTED" not in view.note and not log.entries


def test_the_keyboard_runs_a_take_while_tracking_sees_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(gestures, "REHEARSE", replace(REHEARSE, count_in_s=0.3))
    rig = Rig(tmp_path, monkeypatch, keys={2: ord("t"), 60: ord("n"), 80: ord("x"), 100: ord("q")})
    monkeypatch.setattr(main, "ModeMachine", gestures.ModeMachine)  # the real machine, not a script
    assert rig.run() == 0
    take = rig.session().take(1)
    assert take.status == "saved" and take.duration_s > 0.1
    assert [s["section"] for s in take.sections] == [0, 1]
    rig.assert_closed_once()


# --- playback: stopped when the screen no longer shows it; the focus held while it plays ---

def review_takes(tmp_path, monkeypatch):
    from palmcards.gestures import Grammar
    from palmcards.review import Board
    from tests.test_llm import takes_with
    from tests.test_review import FULL, TEXT as REVIEW_TEXT, analysed

    takes = takes_with(tmp_path, monkeypatch, None)
    notes = parse_text(REVIEW_TEXT)
    takes.board = Board(notes)
    takes.board.add(1, analysed(FULL))
    takes.board.add(2, analysed(FULL))
    ov = TextOverlay(notes.sentences, (1280, 720))
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), hover=Hit(1, None))
    return takes, ov, view, Grammar((1280, 720))


class Silent:
    def play(self, audio, rate):
        pass

    def stop(self):
        pass


CLIP = (__import__("numpy").zeros(16000 * 30, "float32"), 16000)  # longer than any test


def start(takes, view, ov):
    takes.playback.start_clip(Silent(), CLIP, main.play_target(view, ov, takes.board), main.time.perf_counter())


def test_the_focus_is_held_while_its_audio_plays_and_another_take_stops_it(tmp_path, monkeypatch):
    takes, ov, view, g = review_takes(tmp_path, monkeypatch)
    g.state.mode, g.state.level = "focus", "sentence"
    start(takes, view, ov)
    takes.sync_playback(g, view, ov, 1.0)
    assert view.playing and g.focus_hold == "play" and 0.0 <= view.play_progress < 0.1
    takes.board.picked[1] = 1  # the take dial picked take 1: not what plays
    takes.sync_playback(g, view, ov, 1.1)
    assert not view.playing and view.play_progress is None and g.focus_hold is None
    assert [e["reason"] for e in g.log.entries if e["kind"] == "focus_hold"] == ["play", None]
    assert any(e["kind"] == "play_stop" and e["why"] == "target" for e in takes.log.entries)


def test_browsing_to_another_sentence_or_leaving_review_stops_it(tmp_path, monkeypatch):
    takes, ov, view, g = review_takes(tmp_path, monkeypatch)
    view.mode, view.focus, view.current = "browse", None, 0
    start(takes, view, ov)  # `a` while browsing: the current sentence
    takes.sync_playback(g, view, ov, 1.0)
    assert view.playing and g.focus_hold is None  # nothing focused: nothing to hold
    view.mode = "idle"  # the hand rests: it plays on
    takes.sync_playback(g, view, ov, 1.1)
    assert view.playing
    view.mode, view.current = "browse", 2
    takes.sync_playback(g, view, ov, 1.2)
    assert not view.playing
    view.current = 0
    start(takes, view, ov)
    view.app = "prepare"  # back to Prepare
    takes.sync_playback(g, view, ov, 1.3)
    assert not view.playing


def test_a_new_palm_stops_it_and_keeps_the_focus(tmp_path, monkeypatch):
    takes, ov, view, g = review_takes(tmp_path, monkeypatch)
    start(takes, view, ov)
    until = main.apply_event(GestureEvent("palm_stop", 2.0, "sentence"), view, ov, takes.log, None, takes)
    assert view.note == "STOPPED" and until is not None and not takes.playback.playing
    assert view.focus == Hit(1, None)
    assert main.apply_event(GestureEvent("palm_stop", 2.1, "sentence"), view, ov, takes.log, None, takes) is None


def test_a_take_starting_stops_it(tmp_path, monkeypatch):
    takes, ov, view, g = review_takes(tmp_path, monkeypatch)
    start(takes, view, ov)

    def no_mic(**kw):
        raise OSError("no input device")

    takes.devices.recorder = no_mic
    takes.handle(GestureEvent("count_in", 3.0), gestures.ModeMachine((1280, 720)), view, ov)
    assert not takes.playback.playing
    assert any(e["kind"] == "play_stop" and e["why"] == "take" for e in takes.log.entries)


def test_hear_it_goes_through_playback(tmp_path, monkeypatch):
    from tests.test_llm import takes_with

    takes = takes_with(tmp_path, monkeypatch, None)
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720))
    speaker = FakeSpeaker()
    view = focused(OpsView(), level="sentence", hit=Hit(1, None))
    assert main.play_focus(view, ov, takes.log, speaker, takes, 1.0) == "SPEAKING SENTENCE"
    assert takes.playback.target == ("prepare", (1,), None) and speaker.said[0][0][0] == "We"
