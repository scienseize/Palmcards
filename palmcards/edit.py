"""Edits to the notes, each making a new Notes (the old one is left alone).

    toggle_stress(notes, sentence, word) -> Notes

An edited sentence is written out as marked-up text and parsed again, so
its words and marks are exactly what the file format would give (marks in
their usual order, raw text to match). Sessions keep every version as a
notes revision (palmcards.session: edit, undo).
"""

from __future__ import annotations

from palmcards.export import marked
from palmcards.notes import Mark, MarkKind, Notes, parse_sentence
from palmcards.revisions import from_snapshot, to_snapshot


def _copy(notes: Notes) -> Notes:
    return from_snapshot(to_snapshot(notes)[0])


def _replace(notes: Notes, index: int, sentence) -> Notes:
    """`notes` with sentence `index` re-parsed from `sentence`'s markup."""
    new = _copy(notes)
    old = new.sentences[index]
    fresh = parse_sentence(marked(sentence))
    fresh.section, fresh.paragraph, fresh.index = old.section, old.paragraph, old.index
    section = new.sections[old.section].sentences
    section[section.index(old)] = fresh
    return new


def toggle_stress(notes: Notes, sentence: int, word: int) -> Notes:
    s = _copy(notes).sentences[sentence]
    if not 0 <= word < len(s.words):
        raise IndexError(f"sentence {sentence} has no word {word}")
    stressed = any(m.kind == MarkKind.STRESS and m.word == word for m in s.marks)
    s.marks = [m for m in s.marks if not (m.kind == MarkKind.STRESS and m.word == word)]
    if not stressed:
        s.marks.append(Mark(MarkKind.STRESS, word))
    s.words[word].stressed = not stressed
    s.raw = ""  # force a rebuild from the words and marks
    return _replace(notes, sentence, s)


def is_stressed(notes: Notes, sentence: int, word: int) -> bool:
    return any(m.kind == MarkKind.STRESS and m.word == word for m in notes.sentences[sentence].marks)
