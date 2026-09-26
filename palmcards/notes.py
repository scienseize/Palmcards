"""File loading: sections, sentences, words.

Supported inputs: .txt, .md, .docx. Legacy .doc is rejected.

Notes are plain text. PalmCards used to read delivery marks written in the
file (/ // *word* [slow] [fast] [rise] [fall]); they were removed on
2026-09-26. Such markup is still recognised, only to be left out of the
text, with one warning saying how many marks were ignored. A slash inside a
word ("and/or") is text, as before.

Sections start at headings (markdown `#`, Word "Heading"/"Title" styles).
In .txt and .docx, two or more consecutive blank lines/paragraphs also start
a new section.

Run `python -m palmcards.notes FILE` to print the parsed structure.
"""

from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# Bump when parsing changes what a file turns into, so saved notes snapshots
# (palmcards.revisions) say which parser made them. 2: no delivery marks.
PARSER_VERSION = 2


@dataclass
class Word:
    text: str  # as displayed, with punctuation, e.g. "being,"
    norm: str  # lowercase letters/digits/apostrophes only, for alignment
    start: int  # char offsets into Sentence.text
    end: int


@dataclass
class Sentence:
    raw: str  # the sentence as written in the file (any old markup included)
    text: str  # as displayed: the words and punctuation, markup left out
    words: list[Word]
    section: int = 0
    paragraph: int = 0  # source paragraph, counted across the whole file
    index: int = 0  # position in Notes.sentences


@dataclass
class Section:
    title: str  # "" for untitled
    sentences: list[Sentence] = field(default_factory=list)


@dataclass
class Notes:
    sections: list[Section]
    source: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def sentences(self) -> list[Sentence]:
        return [s for sec in self.sections for s in sec.sentences]


# --- sentence parsing -------------------------------------------------------

# The delivery-mark markup of earlier versions, recognised only to leave it out.
BRACKET_MARK = re.compile(r"\[(slow|fast|rise|fall)\]", re.IGNORECASE)
# *word* or **several words**; must hug non-space text on both sides.
STRESS_SPAN = re.compile(r"(\*{1,2})(?=\S)(.+?)(?<=\S)\1")
EDGE_PAUSES = re.compile(r"^(/{1,2})?(.*?)(/{1,2})?$", re.DOTALL)
NORM_DROP = re.compile(r"[^\w']+")


def normalize(text: str) -> str:
    """A word as compared when aligning speech to notes: lowercase letters,
    digits and inner apostrophes. "Being," -> "being", "That’s" -> "that's"."""
    return NORM_DROP.sub("", text.lower().replace("\u2019", "'")).strip("'")


def parse_sentence(raw: str, ignored: list[int] | None = None) -> Sentence:
    """Parse one sentence. Old delivery-mark markup is left out of the text;
    how many marks were left out is appended to `ignored`."""
    marks = len(BRACKET_MARK.findall(raw))
    body = BRACKET_MARK.sub(" ", raw)
    # Stress spans (*word*, **several words**) lose their asterisks.
    marks += len(STRESS_SPAN.findall(body))
    body = STRESS_SPAN.sub(lambda m: m.group(2), body)

    # Slashes standing alone or at a token's edge were pauses; slashes inside
    # a token ("and/or") are text.
    words: list[Word] = []
    pieces: list[str] = []
    for token in body.split():
        if token.strip("/") == "":
            marks += 1
            continue
        lead, text, trail = EDGE_PAUSES.match(token).groups()
        marks += bool(lead) + bool(trail)
        norm = normalize(text)
        if norm:
            start = sum(len(p) + 1 for p in pieces)
            words.append(Word(text, norm, start, start + len(text)))
        pieces.append(text)  # a punctuation-only token (an em dash) is text, not a word
    if ignored is not None:
        ignored.append(marks)
    return Sentence(raw=raw.strip(), text=" ".join(pieces), words=words)


# --- sentence splitting -----------------------------------------------------

ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "e.g", "i.e", "approx", "no"}
SENTENCE_END = re.compile(r"[.?!]+[\"'”’)\]*]*(?=\s)")
# Old ending marks after the punctuation belong to the sentence they follow.
TRAILING_ENDING = re.compile(r"\s+\[(?:rise|fall)\]", re.IGNORECASE)


def split_sentences(paragraph: str) -> list[str]:
    text = " ".join(paragraph.split())
    out, start = [], 0
    for m in SENTENCE_END.finditer(text):
        end = m.end()
        prev_word = text[start:m.start() + 1].split()[-1] if text[start:m.start() + 1].split() else ""
        stem = prev_word.rstrip(".").lstrip("*/(\"'").lower()
        if m.group().startswith(".") and (stem in ABBREVIATIONS or re.fullmatch(r"[a-z]", stem)):
            continue
        while t := TRAILING_ENDING.match(text, end):
            end = t.end()
        if text[start:end].strip():
            out.append(text[start:end].strip())
        start = end
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


# --- file loading -----------------------------------------------------------

# Block stream shared by all formats: ("heading", text) | ("para", text) | ("break", "")
Block = tuple[str, str]


def _txt_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []
    blank_run = 0

    def flush() -> None:
        if para:
            blocks.append(("para", " ".join(para)))
            para.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
            blank_run += 1
            if blank_run == 2:
                blocks.append(("break", ""))
            continue
        blank_run = 0
        if h := re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line):
            flush()
            blocks.append(("heading", h.group(1)))
        else:
            para.append(line.strip())
    flush()
    return blocks


def _md_blocks(text: str) -> list[Block]:
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").parse(text)
    blocks: list[Block] = []
    for i, tok in enumerate(tokens):
        if tok.type != "inline":
            continue
        # Raw inline source: old *stress* and [marks] are left out by parse_sentence, not by markdown.
        kind = "heading" if tokens[i - 1].type == "heading_open" else "para"
        blocks.append((kind, " ".join(tok.content.split())))
    return blocks


def _docx_blocks(data: bytes) -> list[Block]:
    import docx

    blocks: list[Block] = []
    blank_run = 0
    for p in docx.Document(io.BytesIO(data)).paragraphs:
        text = p.text.strip()
        if not text:
            blank_run += 1
            if blank_run == 2:
                blocks.append(("break", ""))
            continue
        blank_run = 0
        style = (p.style.name if p.style is not None else "") or ""
        kind = "heading" if style.startswith("Heading") or style == "Title" else "para"
        blocks.append((kind, text))
    return blocks


def build_notes(blocks: list[Block], source: Path | None = None) -> Notes:
    notes = Notes(sections=[Section("")], source=source)
    paragraph = -1
    ignored: list[int] = []
    for kind, text in blocks:
        current = notes.sections[-1]
        if kind in ("heading", "break"):
            if current.sentences or current.title:
                notes.sections.append(Section(text))
            else:
                current.title = text
            continue
        paragraph += 1
        for raw in split_sentences(text):
            sentence = parse_sentence(raw, ignored)
            if not sentence.words:
                notes.warnings.append(f"Ignored {raw!r}: no words.")
                continue
            sentence.paragraph = paragraph
            current.sentences.append(sentence)

    if n := sum(ignored):
        notes.warnings.append(f"Ignored {n} delivery mark{'s' if n != 1 else ''} (/ // *word* [slow] [fast] "
                              "[rise] [fall]): PalmCards no longer uses them, and the text reads without them.")
    for sec in notes.sections:
        if not sec.sentences and sec.title:
            notes.warnings.append(f"Section {sec.title!r} has no text; dropped.")
    notes.sections = [s for s in notes.sections if s.sentences]
    for si, sec in enumerate(notes.sections):
        for s in sec.sentences:
            s.section = si
    for i, s in enumerate(notes.sentences):
        s.index = i
    return notes


def parse_text(text: str, fmt: str = "txt") -> Notes:
    """Parse notes from a string. fmt is "txt" or "md"."""
    blocks = _md_blocks(text) if fmt == "md" else _txt_blocks(text)
    return build_notes(blocks)


def check_format(path: str | Path) -> str:
    """The file's extension if it is a supported notes format; else ValueError."""
    ext = Path(path).suffix.lower()
    if ext == ".doc":
        raise ValueError("Legacy .doc files are not supported. Save the file as .docx and open that.")
    if ext not in (".docx", ".md", ".markdown", ".txt"):
        raise ValueError(f"Unsupported file type {ext or '(none)'}: use .txt, .md or .docx.")
    return ext


def notes_from_bytes(data: bytes, path: str | Path) -> Notes:
    """Parse a notes file's contents; `path` gives the format (and the name in messages).
    Parsing the bytes that are also saved in the session keeps the two in step."""
    path = Path(path)
    ext = check_format(path)
    if ext == ".docx":
        blocks = _docx_blocks(data)
    elif ext in (".md", ".markdown"):
        blocks = _md_blocks(data.decode("utf-8-sig"))
    else:
        blocks = _txt_blocks(data.decode("utf-8-sig"))
    notes = build_notes(blocks, source=path)
    if not notes.sentences:
        raise ValueError(f"No text found in {path.name}.")
    return notes


def load_notes(path: str | Path) -> Notes:
    path = Path(path)
    check_format(path)
    return notes_from_bytes(path.read_bytes(), path)


def _describe(notes: Notes) -> str:
    lines = []
    for si, sec in enumerate(notes.sections):
        lines.append(f"== Section {si}: {sec.title or '(untitled)'}")
        for s in sec.sentences:
            lines.append(f"  [{s.index}] {s.text}")
    for w in notes.warnings:
        lines.append(f"warning: {w}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m palmcards.notes FILE")
    print(_describe(load_notes(sys.argv[1])))
