"""Your preferences: kept in prefs.json in the data directory, apart from the
sessions (which are evidence and never change with your settings).

  python -m palmcards.prefs                 show them
  python -m palmcards.prefs set KEY VALUE   e.g. set reach 0.8, set high_contrast true

  reach          the hand box's size (0.6 .. 1.4): smaller, less movement to cross the notes
  start_hold_s   how long a fist is held to start a take
  stop_hold_s    how long an open palm is held in the zone (stop, cancel, back to Prepare)
  high_contrast  brighter dimmed text and a darker backing (key c in the app)
  show_hand_box  the hand box drawn faintly while a hand is up
  tutorial_done  the first-run gesture tutorial has been seen (key g shows it again)
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from palmcards.config import CURSOR, REHEARSE
from palmcards.paths import data_dir


@dataclass
class Prefs:
    tutorial_done: bool = False
    high_contrast: bool = False
    show_hand_box: bool = True
    reach: float = 1.0
    start_hold_s: float = REHEARSE.start_hold_s
    stop_hold_s: float = REHEARSE.hold_s

    def checked(self) -> "Prefs":
        """Values clamped to what the app can use."""
        return replace(self, reach=min(max(float(self.reach), 0.6), 1.4),
                       start_hold_s=min(max(float(self.start_hold_s), 0.3), 3.0),
                       stop_hold_s=min(max(float(self.stop_hold_s), 0.5), 4.0))


def path() -> Path:
    return data_dir() / "prefs.json"


def load(where: Path | None = None) -> Prefs:
    where = where or path()
    try:
        data = json.loads(where.read_text())
    except FileNotFoundError:
        return Prefs()
    except (OSError, ValueError) as exc:
        print(f"warning: ignoring {where}: {exc}", file=sys.stderr)
        return Prefs()
    known = {f.name for f in fields(Prefs)}
    return Prefs(**{k: v for k, v in data.items() if k in known}).checked()


def save(prefs: Prefs, where: Path | None = None) -> None:
    where = where or path()
    where.parent.mkdir(parents=True, exist_ok=True)
    tmp = where.with_name(where.name + ".tmp")
    tmp.write_text(json.dumps(asdict(prefs.checked()), indent=1) + "\n")
    tmp.replace(where)


def hand_box(reach: float) -> tuple[float, float, float, float]:
    """CURSOR.hand_box scaled about its centre, kept on the frame's right half."""
    x0, y0, x1, y1 = CURSOR.hand_box
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 * reach, (y1 - y0) / 2 * reach
    return (max(0.5, cx - hw), max(0.0, cy - hh), min(1.0, cx + hw), min(1.0, cy + hh))


def apply(prefs: Prefs) -> None:
    """Make the gesture code and the drawing use them (call before the app builds its machines)."""
    from palmcards import gestures, render

    prefs = prefs.checked()
    # Only the fields preferences own; the rest of each setting stays as it is.
    gestures.CURSOR = replace(gestures.CURSOR, hand_box=hand_box(prefs.reach))
    gestures.REHEARSE = replace(gestures.REHEARSE, start_hold_s=prefs.start_hold_s, hold_s=prefs.stop_hold_s)
    render.REHEARSE = replace(render.REHEARSE, start_hold_s=prefs.start_hold_s, hold_s=prefs.stop_hold_s)
    render.set_contrast(prefs.high_contrast)


def main(argv: list[str]) -> int:
    prefs = load()
    if argv[:1] == ["set"] and len(argv) == 3:
        key, raw = argv[1], argv[2]
        kinds = {f.name: f.type for f in fields(Prefs)}
        if key not in kinds:
            print(f"unknown preference {key!r}; one of: {', '.join(kinds)}", file=sys.stderr)
            return 1
        value = raw.lower() in ("1", "true", "yes", "on") if kinds[key] in (bool, "bool") else float(raw)
        prefs = replace(prefs, **{key: value}).checked()
        save(prefs)
    elif argv:
        print(__doc__, file=sys.stderr)
        return 1
    print(f"{path()}")
    for k, v in asdict(prefs).items():
        print(f"  {k} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
