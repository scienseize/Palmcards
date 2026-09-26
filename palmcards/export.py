"""Write a notes revision back out as .txt, .md or .docx.

  python -m palmcards.export RUN [--revision ID] [--format txt|md|docx] [--out PATH]

Always a new file: by default in the session's exports/ folder, never the
file the notes were imported from (--out to choose; an existing file is
never overwritten). Each sentence is written as it reads in PalmCards:
delivery-mark markup from an older file (/ // *word* [slow] ...) is not
written back.

What round-trips: section titles, sentences, paragraphs, in all three
formats (re-importing gives the same sentences and words). What doesn't,
and is reported when it applies: fonts, styles,
lists and other formatting of an imported .docx; in .md, a section without
a title after the first (markdown has no untitled break PalmCards reads),
which gets the title "Section N".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from palmcards.notes import Notes
from palmcards.revisions import from_snapshot

FORMATS = ("txt", "md", "docx")


def blocks(notes: Notes) -> list[tuple[str, str]]:
    """("heading", title) | ("break", "") | ("para", text), in order."""
    out: list[tuple[str, str]] = []
    for k, sec in enumerate(notes.sections):
        if sec.title:
            out.append(("heading", sec.title))
        elif k > 0:
            out.append(("break", ""))
        paragraph, text = None, []
        for s in sec.sentences:
            if s.paragraph != paragraph and text:
                out.append(("para", " ".join(text)))
                text = []
            paragraph = s.paragraph
            text.append(s.text)
        if text:
            out.append(("para", " ".join(text)))
    return out


def write(notes: Notes, dest: Path, fmt: str, imported_from: Path | None = None) -> list[str]:
    """Write `notes` to a new file; returns what couldn't be kept (for the user)."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; choose another name")
    if imported_from is not None and dest.resolve() == Path(imported_from).resolve():
        raise FileExistsError("refusing to write over the file the notes were imported from")
    lost = []
    if imported_from is not None and Path(imported_from).suffix.lower() == ".docx":
        lost.append("fonts, styles, lists and other formatting of the original .docx are not kept")
    items = blocks(notes)
    if fmt == "docx":
        import docx

        doc = docx.Document()
        for kind, text in items:
            if kind == "heading":
                doc.add_heading(text, level=1)
            elif kind == "break":
                doc.add_paragraph("")
                doc.add_paragraph("")
            else:
                doc.add_paragraph(text)
        doc.save(str(dest))
        return lost
    lines: list[str] = []
    n = 0
    for kind, text in items:
        if kind == "heading":
            n += 1
            lines += [f"# {text}", ""]
        elif kind == "break":
            n += 1
            if fmt == "md":
                lines += [f"# Section {n + 1}", ""]
                lost.append(f"an untitled section is written as '# Section {n + 1}' (markdown has no untitled break)")
            else:
                lines += ["", ""]  # two blank lines: a new section in .txt
        else:
            lines += [text, ""]
    dest.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return lost


def main(argv: list[str] | None = None) -> int:
    from palmcards.data import find
    from palmcards.session import SESSIONS_DIR, Session, SessionError

    ap = argparse.ArgumentParser(prog="python -m palmcards.export", description=__doc__.split("\n\n")[0])
    ap.add_argument("run", help="a session folder, or its name under the data directory")
    ap.add_argument("--revision", help="a notes revision id (default: the current one)")
    ap.add_argument("--format", choices=FORMATS, help="default: the imported file's")
    ap.add_argument("--out", type=Path, help="default: the session's exports/ folder")
    args = ap.parse_args(argv)
    try:
        folder = Path(args.run) if (Path(args.run) / "session.json").exists() else find(SESSIONS_DIR, args.run)
        session = Session.load(folder)
        rid = args.revision or session.current_revision
        if rid is None:
            raise SessionError(f"{folder.name} has no saved notes; bind them first: "
                               f"python -m palmcards.speech {folder} --rebind")
        notes = from_snapshot(session.snapshot(rid))
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    fmt = args.format or session.notes.suffix.lower().lstrip(".").replace("markdown", "md")
    if fmt not in FORMATS:
        fmt = "txt"
    dest = args.out or folder / "exports" / f"{session.notes.stem}-{rid}.{fmt}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        lost = write(notes, dest, fmt, session.notes)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {dest}")
    for line in dict.fromkeys(lost):
        print(f"note: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
