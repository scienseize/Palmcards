"""The notes file, chosen in macOS's own Open dialog. osascript shows it, so no
Cocoa event loop of ours is needed and the OpenCV window is left alone."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Plain text (.txt; Markdown conforms to it too), Markdown, Word (.docx).
TYPES = ("public.plain-text", "net.daringfireball.markdown", "org.openxmlformats.wordprocessingml.document")
PROMPT = "Choose your notes (.txt, .md or .docx)"


def pick_notes() -> Path | None:
    """The file picked, or None if the dialog was cancelled or could not be shown."""
    types = ", ".join(f'"{t}"' for t in TYPES)
    script = f'POSIX path of (choose file with prompt "{PROMPT}" of type {{{types}}})'
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = out.stdout.strip()
    return Path(path) if out.returncode == 0 and path else None
