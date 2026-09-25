"""Where PalmCards keeps its data.

    data_dir()   the sessions root: session folders, gesture-logs/, screens/

In order:
  1. $PALMCARDS_DATA, if set (created if missing);
  2. sessions/ in the source checkout, if it exists: development runs keep
     finding the sessions recorded so far;
  3. ~/Library/Application Support/PalmCards/sessions, the installed-app place.

Nothing is ever moved between them automatically; `python -m palmcards.data
export` copies a session out if you want it elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "PalmCards" / "sessions"


def data_dir() -> Path:
    if env := os.environ.get("PALMCARDS_DATA"):
        return Path(env).expanduser().resolve()
    dev = CHECKOUT / "sessions"
    if dev.is_dir():
        return dev
    return APP_SUPPORT
