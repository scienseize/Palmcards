"""Edits to the notes, each making a new Notes (the old one is left alone).

    replace_word(notes, sentence, word, text) -> Notes      (an LLM alternative)
    replace_text(notes, sentences, text) -> Notes            (a tone or length rewrite)

An edited sentence is written out as text and parsed again, so its words
are exactly what the file format would give. Sessions keep every version as
a notes revision (palmcards.session: edit, undo).
"""

from __future__ import annotations

from palmcards.notes import Notes, parse_sentence
from palmcards.revisions import from_snapshot, to_snapshot


def _copy(notes: Notes) -> Notes:
    return from_snapshot(to_snapshot(notes)[0])


def _replace(notes: Notes, index: int, text: str) -> Notes:
    """`notes` with sentence `index` parsed afresh from `text`."""
    new = _copy(notes)
    old = new.sentences[index]
    fresh = parse_sentence(text)
    fresh.section, fresh.paragraph, fresh.index = old.section, old.paragraph, old.index
    section = new.sections[old.section].sentences
    section[section.index(old)] = fresh
    return new


def replace_word(notes: Notes, sentence: int, word: int, text: str) -> Notes:
    """A word (or short phrase) in place of another, keeping the word's
    punctuation."""
    s = notes.sentences[sentence]
    pieces = text.split()
    old = s.words[word]
    punct = old.text[len(old.text.rstrip(".,;:!?\"')")):]  # keep the word's trailing punctuation
    lead = old.text[: len(old.text) - len(old.text.lstrip("\"'("))]
    pieces[0], pieces[-1] = lead + pieces[0], pieces[-1] + punct
    return _replace(notes, sentence, s.text[:old.start] + " ".join(pieces) + s.text[old.end:])


def replace_text(notes: Notes, sentences: list[int], text: str) -> Notes:
    """Sentences (a sentence, or a paragraph's) replaced by new text, as one
    sentence per sentence the text splits into."""
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
