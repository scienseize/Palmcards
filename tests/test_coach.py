"""The coach card's rules (palmcards.coach) on synthetic takes."""

from palmcards.align import align
from palmcards.coach import NOTHING, advise, opening
from palmcards.metrics import speech
from palmcards.notes import parse_text
from palmcards.review import Board
from tests.test_align import transcript

TEXT = """# Problem

Most founders rehearse their pitch silently in their heads. They walk into the room having never said it aloud.

# Product

PalmCards puts your notes beside your mirrored camera image. Each take measures pace, fillers and pauses.
"""
NOTES = parse_text(TEXT, "md")
SENTENCES = [[w.norm for w in s.words] for s in NOTES.sentences]
ALL = ("Most founders rehearse their pitch silently in their heads. | They walk into the room having never "
       "said it aloud. | PalmCards puts your notes beside your mirrored camera image. | Each take measures pace, "
       "fillers and pauses.")


def add(board: Board, number: int, said: str, drill: int | None = None) -> None:
    words = transcript(said)
    al = align(SENTENCES, words)
    duration = words[-1]["end"] - words[0]["start"] if words else 0.0
    board.add(number, al, {"speech": speech(al, words, duration)}, drill, duration_s=duration)


def test_opening_quotes_the_first_words():
    assert opening("We started this project with one question.") == "“We started this project…”"
    assert opening("Thank you.") == "“Thank you”"


def test_a_sentence_left_out_is_the_thing_to_work_on_and_to_practise():
    b = Board(NOTES)
    add(b, 1, "Most founders rehearse their pitch silently in their heads. | "
              "PalmCards puts your notes beside your mirrored camera image. | Each take measures pace, fillers and pauses.")
    a = advise(b, 1)
    assert a.work == "You left out “They walk into the…”" and a.sentence == 1
    assert a.next.startswith("Practise it")


def test_stopping_early_says_how_much_was_not_reached():
    b = Board(NOTES)
    add(b, 1, "Most founders rehearse their pitch silently in their heads. | They walk into the room having never said it aloud.")
    a = advise(b, 1)
    assert a.work == "You stopped after “They walk into the…”: 2 sentences not reached"
    assert a.sentence is None and a.next == "Run it through again, to the end"


def test_a_long_pause_before_a_section_is_a_blank_to_practise():
    b = Board(NOTES)
    add(b, 1, ALL.replace("aloud. |", "aloud. | | |"))  # 3 s more before the second section
    a = advise(b, 1)
    assert a.work.startswith("A 3.") and "before “PalmCards puts your notes…” at the start of a section" in a.work
    assert a.sentence == 2


def test_a_restart_is_named():
    b = Board(NOTES)
    add(b, 1, ALL.replace("Each take measures", "Each take | Each take measures"))
    a = advise(b, 1)
    assert a.work == "You restarted “Each take measures pace…” once" and a.sentence == 3


def test_went_well_is_the_gain_over_the_take_before():
    b = Board(NOTES)
    add(b, 1, ALL.replace("They walk", "um uh like They walk").replace("Each take", "um Each take"))
    add(b, 2, ALL)
    assert advise(b, 2).well.startswith("Fewer fillers: 0 a minute, down from")


def test_a_clean_take_says_so_and_suggests_memory():
    b = Board(NOTES)
    add(b, 1, ALL)
    a = advise(b, 1)
    assert a.well == "You said all 4 key points"
    assert a.work in (NOTHING,) or a.work.startswith("Your pace")  # synthetic words run at ~170 wpm
    if a.work == NOTHING:
        assert "fading cards" in a.next


def test_a_drill_is_coached_against_the_takes_before_it():
    b = Board(NOTES)
    add(b, 1, ALL.replace("They walk", "They walk | They walk"))
    add(b, 2, "They walk into the room having never said it aloud.", drill=1)
    a = advise(b, 2)
    assert a.sentence == 1 and a.well == "Said straight through"
    assert a.work == "It is ready: put it back in a full run" and a.next == "Run it through again"


def test_nothing_is_advised_before_the_take_is_analysed():
    assert advise(Board(NOTES), 1) is None
