"""The notes file, chosen in macOS's own Open dialog. osascript shows it, so no
Cocoa event loop of ours is needed and the OpenCV window is left alone."""

from __future__ import annotations

import subprocess
from pathlib import Path

PDF = "com.adobe.pdf"
# Plain text (.txt; Markdown conforms to it too), Markdown, Word (.docx), and slides as a PDF.
TYPES = ("public.plain-text", "net.daringfireball.markdown", "org.openxmlformats.wordprocessingml.document", PDF)
PROMPT = "Choose your notes (.txt, .md or .docx) or your slides (.pdf)"


def pick_notes() -> Path | None:
    """The notes file picked, or None if the dialog was cancelled or could not be shown."""
    return pick(TYPES, PROMPT)


def pick_slides() -> Path | None:
    """Slides to show beside the notes in a take: a PDF."""
    return pick((PDF,), "Choose your slides (a PDF from Keynote, PowerPoint or Google Slides)")


def pick(types: tuple[str, ...], prompt: str) -> Path | None:
    """The file picked in macOS's Open dialog, or None if it was cancelled or could not be shown."""
    kinds = ", ".join(f'"{t}"' for t in types)
    script = f'POSIX path of (choose file with prompt "{prompt}" of type {{{kinds}}})'
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = out.stdout.strip()
    return Path(path) if out.returncode == 0 and path else None
