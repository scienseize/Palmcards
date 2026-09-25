"""File loading, sections/sentences/words, delivery-mark parsing.

Supported inputs: .txt, .md, .docx. Legacy .doc is rejected.

Delivery marks (plain ASCII in the file):
  /  short pause       //  long pause        *word*  stress
  [slow] [fast]  pace (anywhere in the sentence, usually at the start)
  [rise] [fall]  ending intonation (usually at the end)

Pause marks are stored as the gap *before* word i, so i ranges over
0..len(words): 0 is the gap before the first word (after the previous
sentence), len(words) is the gap after the last word.

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
from enum import StrEnum
from pathlib import Path


# Bump when parsing changes what a file turns into, so saved notes snapshots
# (palmcards.revisions) say which parser made them.
PARSER_VERSION = 1


class MarkKind(StrEnum):
    SHORT_PAUSE = "short_pause"
    LONG_PAUSE = "long_pause"
    STRESS = "stress"
    SLOW = "slow"
    FAST = "fast"
    RISE = "rise"
    FALL = "fall"


PACE_KINDS = (MarkKind.SLOW, MarkKind.FAST)
ENDING_KINDS = (MarkKind.RISE, MarkKind.FALL)


@dataclass(frozen=True)
class Mark:
    kind: MarkKind
    # Stress: index of the stressed word. Pause: gap before this word index.
    # Pace and ending: None (they apply to the whole sentence).
    word: int | None = None


@dataclass
class Word:
    text: str  # as displayed, with punctuation, e.g. "being,"
    norm: str  # lowercase letters/digits/apostrophes only, for alignment
    start: int  # char offsets into Sentence.text
    end: int
    stressed: bool = False


@dataclass
class Sentence:
    raw: str  # original text with marks
    text: str  # display text with marks removed
    words: list[Word]
    marks: list[Mark]
    section: int = 0
    paragraph: int = 0  # source paragraph, counted across the whole file
    index: int = 0  # position in Notes.sentences

    @property
    def pace(self) -> MarkKind | None:
        return next((m.kind for m in self.marks if m.kind in PACE_KINDS), None)

    @property
    def ending(self) -> MarkKind | None:
        return next((m.kind for m in self.marks if m.kind in ENDING_KINDS), None)

    def pauses(self) -> list[Mark]:
        return [m for m in self.marks if m.kind in (MarkKind.SHORT_PAUSE, MarkKind.LONG_PAUSE)]


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

BRACKET_MARK = re.compile(r"\[(slow|fast|rise|fall)\]", re.IGNORECASE)
# *word* or **several words**; must hug non-space text on both sides.
STRESS_SPAN = re.compile(r"(\*{1,2})(?=\S)(.+?)(?<=\S)\1")
STRESS_ON, STRESS_OFF = "\x01", "\x02"
EDGE_PAUSES = re.compile(r"^(/{1,2})?(.*?)(/{1,2})?$", re.DOTALL)
NORM_DROP = re.compile(r"[^\w']+")


def normalize(text: str) -> str:
    """A word as compared when aligning speech to notes: lowercase letters,
    digits and inner apostrophes. "Being," -> "being", "That’s" -> "that's"."""
    return NORM_DROP.sub("", text.lower().replace("\u2019", "'")).strip("'")


def _pause(slashes: str) -> MarkKind:
    return MarkKind.LONG_PAUSE if len(slashes) == 2 else MarkKind.SHORT_PAUSE


def parse_sentence(raw: str, warnings: list[str] | None = None) -> Sentence:
    """Parse one sentence of marked-up text."""
    warnings = warnings if warnings is not None else []
    marks: list[Mark] = []

    # 1. Sentence-level bracket marks. If both of a pair appear, the last wins.
    found = [MarkKind(m.group(1).lower()) for m in BRACKET_MARK.finditer(raw)]
    for kinds in (PACE_KINDS, ENDING_KINDS):
        picked = [k for k in found if k in kinds]
        if len(set(picked)) > 1:
            warnings.append(f"Conflicting marks {', '.join('[' + k + ']' for k in picked)} "
                            f"in {raw!r}; using [{picked[-1]}].")
        if picked:
            marks.append(Mark(picked[-1]))
    body = BRACKET_MARK.sub(" ", raw)

    # 2. Stress spans become sentinel characters that survive tokenisation.
    body = STRESS_SPAN.sub(lambda m: STRESS_ON + m.group(2) + STRESS_OFF, body)

    # 3. Tokenise on whitespace; slashes standing alone or at a token's edge
    #    are pauses, slashes inside a token ("and/or") are text.
    words: list[Word] = []
    pieces: list[str] = []
    stressed = False
    pending_pause: MarkKind | None = None

    def add_pause(kind: MarkKind) -> None:
        nonlocal pending_pause
        # Two marks in the same gap: keep the longer one.
        if pending_pause != MarkKind.LONG_PAUSE:
            pending_pause = kind

    for token in body.split():
        if token.strip(STRESS_ON + STRESS_OFF) in ("/", "//"):
            add_pause(_pause(token.strip(STRESS_ON + STRESS_OFF)))
            continue
        if token.strip("/" + STRESS_ON + STRESS_OFF) == "":
            continue  # "///" or stray sentinels: nothing to say
        lead, core, trail = EDGE_PAUSES.match(token).groups()
        if lead:
            add_pause(_pause(lead))

        starts_stress = STRESS_ON in core
        text = core.replace(STRESS_ON, "").replace(STRESS_OFF, "")
        is_stressed = stressed or starts_stress
        if STRESS_ON in core:
            stressed = core.rfind(STRESS_ON) > core.rfind(STRESS_OFF)
        elif STRESS_OFF in core:
            stressed = False

        norm = normalize(text)
        if not norm:
            pieces.append(text)  # punctuation-only token, e.g. an em dash
        else:
            if pending_pause:
                marks.append(Mark(pending_pause, len(words)))
                pending_pause = None
            start = sum(len(p) + 1 for p in pieces)
            words.append(Word(text, norm, start, start + len(text), is_stressed))
            if is_stressed:
                marks.append(Mark(MarkKind.STRESS, len(words) - 1))
            pieces.append(text)
        if trail:
            add_pause(_pause(trail))

    if pending_pause:
        marks.append(Mark(pending_pause, len(words)))

    text = " ".join(pieces)
    return Sentence(raw=raw.strip(), text=text, words=words, marks=marks)


# --- sentence splitting -----------------------------------------------------

ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "e.g", "i.e", "approx", "no"}
SENTENCE_END = re.compile(r"[.?!]+[\"'”’)\]*]*(?=\s)")
# Ending marks after the punctuation belong to the sentence they follow.
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
        # Raw inline source keeps *stress* asterisks and [marks] intact.
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
            sentence = parse_sentence(raw, notes.warnings)
            if not sentence.words:
                notes.warnings.append(f"Ignored {raw!r}: no words, only marks.")
                continue
            sentence.paragraph = paragraph
            current.sentences.append(sentence)

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
            for m in s.marks:
                where = ""
                if m.kind is MarkKind.STRESS:
                    where = f" on {s.words[m.word].text!r}"
                elif m.word is not None:
                    before = s.words[m.word - 1].text if m.word > 0 else "(start)"
                    after = s.words[m.word].text if m.word < len(s.words) else "(end)"
                    where = f" between {before!r} and {after!r}"
                lines.append(f"      {m.kind}{where}")
    for w in notes.warnings:
        lines.append(f"warning: {w}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m palmcards.notes FILE")
    print(_describe(load_notes(sys.argv[1])))
