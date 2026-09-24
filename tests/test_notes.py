from pathlib import Path

import docx
import pytest

from palmcards.notes import (
    Mark,
    MarkKind,
    load_notes,
    parse_sentence,
    parse_text,
    split_sentences,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def words(s):
    return [w.text for w in s.words]


# --- marks within a sentence -------------------------------------------------

def test_plain_sentence_has_no_marks():
    s = parse_sentence("Good evening, everyone.")
    assert words(s) == ["Good", "evening,", "everyone."]
    assert s.marks == []
    assert s.text == "Good evening, everyone."


def test_short_and_long_pause_mark_gap_before_word():
    s = parse_sentence("One / two // three")
    assert words(s) == ["One", "two", "three"]
    assert s.marks == [Mark(MarkKind.SHORT_PAUSE, 1), Mark(MarkKind.LONG_PAUSE, 2)]
    assert s.text == "One two three"


def test_pause_at_start_and_end():
    s = parse_sentence("/ Thank you //")
    assert s.marks == [Mark(MarkKind.SHORT_PAUSE, 0), Mark(MarkKind.LONG_PAUSE, 2)]


def test_pause_attached_to_word_edges():
    s = parse_sentence("question:// what/ now")
    assert words(s) == ["question:", "what", "now"]
    assert s.pauses() == [Mark(MarkKind.LONG_PAUSE, 1), Mark(MarkKind.SHORT_PAUSE, 2)]


def test_slash_inside_word_is_text():
    s = parse_sentence("Bring pens and/or pencils.")
    assert "and/or" in words(s)
    assert s.marks == []


def test_two_pauses_in_one_gap_keep_the_longer():
    s = parse_sentence("wait // / here")
    assert s.marks == [Mark(MarkKind.LONG_PAUSE, 1)]


def test_stress_single_word_with_punctuation():
    s = parse_sentence("Thank you for *being*, here.")
    assert words(s) == ["Thank", "you", "for", "being,", "here."]
    assert s.marks == [Mark(MarkKind.STRESS, 3)]
    assert s.words[3].stressed
    assert s.text == "Thank you for being, here."


def test_stress_span_over_several_words():
    s = parse_sentence("This is *really very* important.")
    assert [w.stressed for w in s.words] == [False, False, True, True, False]


def test_double_asterisk_counts_as_stress():
    s = parse_sentence("It **matters** now.")
    assert s.marks == [Mark(MarkKind.STRESS, 1)]


def test_lone_asterisk_is_text():
    s = parse_sentence("Five * three is fifteen.")
    assert s.marks == []


def test_pace_and_ending_marks_case_insensitive():
    s = parse_sentence("[SLOW] Most of us rehearse. [Rise]")
    assert s.pace is MarkKind.SLOW
    assert s.ending is MarkKind.RISE
    assert words(s) == ["Most", "of", "us", "rehearse."]


def test_conflicting_marks_last_wins_with_warning():
    warnings = []
    s = parse_sentence("[slow] Hurry [fast] up.", warnings)
    assert s.pace is MarkKind.FAST
    assert len(warnings) == 1


def test_unknown_bracket_is_text():
    s = parse_sentence("As shown [citation needed] here.")
    assert "[citation" in words(s)
    assert s.marks == []


def test_word_offsets_and_norm():
    s = parse_sentence("/ Don't *stop*, Now!")
    for w in s.words:
        assert s.text[w.start:w.end] == w.text
    assert [w.norm for w in s.words] == ["don't", "stop", "now"]


def test_punctuation_only_token_is_not_a_word():
    s = parse_sentence("Wait — / listen.")
    assert words(s) == ["Wait", "listen."]
    assert s.text == "Wait — listen."
    assert s.marks == [Mark(MarkKind.SHORT_PAUSE, 1)]


# --- sentence splitting ------------------------------------------------------

def test_split_basic_and_trailing_ending_mark():
    assert split_sentences("Is it? [rise] Yes. [slow] Now go!") == [
        "Is it? [rise]", "Yes.", "[slow] Now go!",
    ]


def test_split_keeps_abbreviations_and_initials():
    assert split_sentences("Dr. Smith met J. Doe, e.g. at noon. Then left.") == [
        "Dr. Smith met J. Doe, e.g. at noon.", "Then left.",
    ]


def test_split_after_closing_quote_and_stress():
    assert split_sentences('He said "stop." Then *go.* Done') == [
        'He said "stop."', "Then *go.*", "Done",
    ]


def test_split_decimal_number():
    assert split_sentences("It costs 3.50 today. Really.") == ["It costs 3.50 today.", "Really."]


# --- sections and files ------------------------------------------------------

def test_txt_sections_from_headings_and_double_blank_lines():
    text = "Intro line wraps\nonto the next line.\n\nSecond para.\n\n\nNew section.\n# Titled\nLast one."
    notes = parse_text(text, "txt")
    assert [s.title for s in notes.sections] == ["", "", "Titled"]
    assert [s.text for s in notes.sections[0].sentences] == [
        "Intro line wraps onto the next line.", "Second para.",
    ]
    assert [s.index for s in notes.sentences] == [0, 1, 2, 3]
    assert [s.section for s in notes.sentences] == [0, 0, 1, 2]


def test_md_keeps_marks_that_markdown_would_eat():
    notes = parse_text("# Opening\n\nThank you for *being* here. [rise]\n\n- A *list* item.\n", "md")
    assert notes.sections[0].title == "Opening"
    first, second = notes.sentences
    assert first.ending is MarkKind.RISE
    assert Mark(MarkKind.STRESS, 3) in first.marks
    assert second.words[1].stressed


def test_empty_heading_is_dropped_with_warning():
    notes = parse_text("# Empty\n\n# Real\n\nText here.", "md")
    assert [s.title for s in notes.sections] == ["Real"]
    assert any("Empty" in w for w in notes.warnings)


def test_marks_only_sentence_is_ignored_with_warning():
    notes = parse_text("Hello. // [rise]\n\nBye.", "txt")
    # "// [rise]" has no words; the [rise] attaches to "Hello." via the splitter.
    assert [s.text for s in notes.sentences] == ["Hello.", "Bye."]


def test_load_sample_md():
    notes = load_notes(SAMPLES / "sample_notes.md")
    assert [s.title for s in notes.sections] == ["Opening", "The idea"]
    assert len(notes.sentences) == 9
    kinds = {m.kind for s in notes.sentences for m in s.marks}
    assert kinds == set(MarkKind)


def test_load_txt(tmp_path):
    p = tmp_path / "talk.txt"
    p.write_text("﻿Hello / there. [fall]\n", encoding="utf-8")
    (s,) = load_notes(p).sentences
    assert s.text == "Hello there."
    assert s.ending is MarkKind.FALL


def test_load_docx_headings_and_marks(tmp_path):
    d = docx.Document()
    d.add_heading("Opening", level=1)
    d.add_paragraph("Thank you for *being* here. / Truly.")
    d.add_paragraph("")
    d.add_paragraph("")
    d.add_paragraph("[slow] After a break.")
    p = tmp_path / "talk.docx"
    d.save(p)

    notes = load_notes(p)
    assert [s.title for s in notes.sections] == ["Opening", ""]
    a, b, c = notes.sentences
    assert Mark(MarkKind.STRESS, 3) in a.marks
    assert b.marks == [Mark(MarkKind.SHORT_PAUSE, 0)]
    assert c.pace is MarkKind.SLOW and c.section == 1


def test_doc_rejected(tmp_path):
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ValueError, match=r"\.docx"):
        load_notes(p)


def test_unsupported_and_empty_files(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        load_notes(tmp_path / "x.pdf")
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n\n")
    with pytest.raises(ValueError, match="No text"):
        load_notes(empty)
