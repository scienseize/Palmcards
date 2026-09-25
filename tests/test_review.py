from palmcards.align import align
from palmcards.cues import verdicts
from palmcards.notes import parse_text
from palmcards.review import Board
from tests.test_cues import speak

TEXT = "Good evening everyone. / Thank you for being here. [slow] We are so glad you came tonight."


def judged(script: str, notes=None):
    notes = notes or parse_text(TEXT)
    words = speak(script)
    alignment = align([[w.norm for w in s.words] for s in notes.sentences], words)
    return verdicts([[[m.kind, m.word] for m in s.marks] for s in notes.sentences], alignment, words)


FULL = "Good evening everyone. <0.1> Thank you for being here. <0.4> We are so glad you came tonight."


def test_each_sentence_shows_its_latest_take_and_the_dial_moves_through_them():
    b = Board(parse_text(TEXT))
    assert b.shown(0) is None and b.detail(0) == (("", "No take judged yet."),)
    b.add(1, judged(FULL))
    b.add(2, judged("Good evening everyone. <0.5> Thank you for being here."))
    b.add(3, judged("Thank you for being here.", ), drill=1)
    assert [b.shown(i) for i in range(3)] == [2, 3, 1]  # latest take that said each sentence
    assert b.said_in(1) == [1, 2, 3]
    assert b.take_label(1) == "TAKE 3 (DRILL)  3 OF 3"

    b.step(1, -1)
    assert b.shown(1) == 2 and b.take_label(1) == "TAKE 2  2 OF 3"
    b.step(1, -5)  # clamped
    assert b.shown(1) == 1
    b.step(2, 1)  # only one take said sentence 2
    assert b.shown(2) == 1

    # A newer take that says the sentence again shows instead of the pick.
    b.add(4, judged(FULL))
    assert b.shown(1) == 4


def test_mark_verdicts_and_detail_lines():
    b = Board(parse_text(TEXT))
    b.add(1, judged("Good evening everyone. <0.1> Thank you for being here. um <0.4> We are so glad you came tonight."))
    assert b.mark_verdicts() == ((), ("missed",), ("missed",))  # [slow] said at the usual pace
    lines = b.detail(1)
    assert lines[0] == ("missed", '/ before "Thank"  MISSED  no pause, 0.10 s (needs 0.30)')
    assert lines[1][1].startswith("Pace ") and lines[-1] == ("", "No fillers.")
    assert b.detail(2)[-1] == ("", "Fillers: um")  # before the sentence: its hesitation
    assert b.summary(1) == "0/2 MARKS HIT"


def test_a_sentence_not_said_in_any_take():
    b = Board(parse_text(TEXT))
    b.add(1, judged("Good evening everyone."))
    assert b.shown(2) == 1 and b.take_label(2) == "TAKE 1: NOT SAID"
    assert b.detail(2)[-1] == ("", "Not said in this take.")
    assert b.mark_verdicts()[2] == ("skipped",)
