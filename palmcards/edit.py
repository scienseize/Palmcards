"""Edits to the notes, each making a new Notes (the old one is left alone).

    toggle_stress(notes, sentence, word) -> Notes
    replace_word(notes, sentence, word, text) -> Notes      (an LLM alternative)
    replace_text(notes, sentences, text) -> Notes            (a tone or length rewrite)
    add_marks(notes, sentence, marks) -> Notes               (suggested marks)
    new_marks(sentence, marks) -> [Mark]                     (the suggestions that would add something)

An edited sentence is written out as marked-up text and parsed again, so
its words and marks are exactly what the file format would give (marks in
their usual order, raw text to match). Sessions keep every version as a
notes revision (palmcards.session: edit, undo).
"""

from __future__ import annotations

from palmcards.export import marked
from palmcards.notes import (
    ENDING_KINDS, PACE_KINDS, Mark, MarkKind, Notes, Sentence, normalize, parse_sentence, reading_place,
)
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


def replace_word(notes: Notes, sentence: int, word: int, text: str) -> Notes:
    """A word (or short phrase) in place of another; the marks on and around it
    stay where they were (a phrase's stress falls on its first word)."""
    s = _copy(notes).sentences[sentence]
    pieces = text.split()
    old = s.words[word]
    punct = old.text[len(old.text.rstrip(".,;:!?\"')")):]  # keep the word's trailing punctuation
    lead = old.text[: len(old.text) - len(old.text.lstrip("\"'("))]
    pieces[0], pieces[-1] = lead + pieces[0], pieces[-1] + punct
    extra = len(pieces) - 1
    s.words[word].text = pieces[0]
    for k, piece in enumerate(pieces[1:], 1):
        s.words.insert(word + k, type(old)(piece, normalize(piece), 0, 0, False))
    s.marks = [Mark(m.kind, m.word + extra if m.word is not None and m.word > word else m.word) for m in s.marks]
    s.raw = ""
    return _replace(notes, sentence, s)


def replace_text(notes: Notes, sentences: list[int], text: str) -> Notes:
    """Sentences (a sentence, or a paragraph's) replaced by new text, as one
    sentence per sentence the text splits into; their marks are dropped (they
    were for other words): the user marks the new text again."""
    from palmcards.notes import build_notes, split_sentences

    new = _copy(notes)
    first = new.sentences[sentences[0]]
    section = new.sections[first.section].sentences
    at = section.index(first)
    for i in sorted(sentences, reverse=True):
        section.remove(new.sentences[i])
    fresh = [parse_sentence(raw) for raw in split_sentences(text)]
    for k, s in enumerate(fresh):
        s.section, s.paragraph = first.section, first.paragraph
        section.insert(at + k, s)
    for i, s in enumerate(new.sentences):
        s.index = i
    return new


def add_marks(notes: Notes, sentence: int, marks: list) -> Notes:
    """Suggested marks added to a sentence (ones it has already are kept once)."""
    s = _copy(notes).sentences[sentence]
    for kind, word in marks:
        kind = MarkKind(kind)
        if kind in (MarkKind.SLOW, MarkKind.FAST, MarkKind.RISE, MarkKind.FALL):
            pair = (MarkKind.SLOW, MarkKind.FAST) if kind in (MarkKind.SLOW, MarkKind.FAST) else \
                (MarkKind.RISE, MarkKind.FALL)
            s.marks = [m for m in s.marks if m.kind not in pair]
        if Mark(kind, word) not in s.marks:
            s.marks.append(Mark(kind, word))
            if kind == MarkKind.STRESS:
                s.words[word].stressed = True
    s.raw = ""
    return _replace(notes, sentence, s)


def new_marks(sentence: Sentence, marks: list) -> list[Mark]:
    """The suggested marks that would add something to the sentence, in the
    order they read (pace, then word by word, the ending last). Left out:
    marks it has already, a second pause in the same gap, stress on a word
    already stressed, and a pace or ending that would replace its own
    (suggestions only add)."""
    pauses = {m.word for m in sentence.pauses()}
    out: list[Mark] = []
    for kind, word in marks:
        mark = Mark(MarkKind(kind), word)
        if mark in sentence.marks or mark in out:
            continue
        if mark.kind in (MarkKind.SHORT_PAUSE, MarkKind.LONG_PAUSE):
            if word in pauses:
                continue
            pauses.add(word)
        elif mark.kind in PACE_KINDS and (sentence.pace or any(m.kind in PACE_KINDS for m in out)):
            continue
        elif mark.kind in ENDING_KINDS and (sentence.ending or any(m.kind in ENDING_KINDS for m in out)):
            continue
        out.append(mark)
    return sorted(out, key=lambda m: reading_place(m, len(sentence.words)))

