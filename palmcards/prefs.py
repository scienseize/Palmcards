"""Your preferences: kept in prefs.json in the data directory, apart from the
sessions (which are evidence and never change with your settings).

  python -m palmcards.prefs                 show them
  python -m palmcards.prefs set KEY VALUE   e.g. set reach 0.8, set high_contrast true

  reach          the hand box's size (0.6 .. 1.4): smaller, less movement to cross the notes
  start_hold_s   how long a fist is held to start a take
  count_in_again_s  the count-in once the session has a take (0 .. 3 s; the first one is 3 s)
  done_hold_s    how long the OK sign is held to stop a take or cancel a count-in
                 (was stop_hold_s: an old prefs.json with it still works)
  high_contrast  brighter dimmed text and a darker backing (key c in the app)
  show_hand_box  the hand box drawn faintly while a hand is up
  reduced_motion nothing moves or scales, things fade (true / false; auto: as macOS's
                 Accessibility > Display > Reduce motion)
  sounds         soft sound cues in Prepare and Review (focus, back, commit, the ring's steps)
  video          record video of each take, replayed in Review (on by default: you want to watch
                 your take; off: python -m palmcards.prefs set video false, and then
                 main.py --video records one run). Video is far more personal than the audio: it stays in the session
                 folder; python -m palmcards.data drop-video deletes it
  replay_mirrored  Review replays a take's video mirrored, as in the app (key m); by default
                 flipped back, as others see you
  fullscreen     the window fills the screen (key f in the app); on by default
  fade           fading cards: in a take, words said take after take dim, then blank
                 (key w in the app; main.py --fade for one run)
  qa             the Q&A round: practice questions after each full take (key y)
  tutorial_done  the first-run gesture tutorial has been seen (key g shows it again)
  last_notes     the notes file opened last (kept by the app); main.py with no file opens it again
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from palmcards.config import CURSOR, REHEARSE
from palmcards.paths import data_dir, write_atomic


@dataclass
class Prefs:
    tutorial_done: bool = False
    high_contrast: bool = False
    show_hand_box: bool = True
    reduced_motion: bool | None = None  # None: as macOS's setting
    sounds: bool = False
    video: bool = True  # record video of each take (palmcards.video), to watch it in Review
    replay_mirrored: bool = False  # Review replays a take's video mirrored (key m); else as others see you
    fullscreen: bool = True  # the window fills the screen (key f): the teleprompter sits under the notch
    fade: bool = False  # fading cards (palmcards.fade; key w); main.py --fade for one run
    qa: bool = False  # the Q&A round after each full take (key y)
    reach: float = 1.0
    start_hold_s: float = REHEARSE.start_hold_s
    done_hold_s: float = REHEARSE.done_hold_s
    count_in_again_s: float = REHEARSE.count_in_again_s
    last_notes: str = ""  # absolute path of the notes file opened last; "" until one was

    def checked(self) -> "Prefs":
        """Values clamped to what the app can use."""
        return replace(self, reach=min(max(float(self.reach), 0.6), 1.4),
                       start_hold_s=min(max(float(self.start_hold_s), 0.3), 3.0),
                       done_hold_s=min(max(float(self.done_hold_s), 0.5), 4.0),
                       count_in_again_s=min(max(float(self.count_in_again_s), 0.0), 3.0))


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
    if "stop_hold_s" in data and "done_hold_s" not in data:  # its old name (it clashed with OPS.stop_hold_s)
        data["done_hold_s"] = data["stop_hold_s"]
    return Prefs(**{k: v for k, v in data.items() if k in known}).checked()


def save(prefs: Prefs, where: Path | None = None) -> None:
    """Only what differs from the defaults is written, so a default changed later reaches everyone
    who never changed it (an old file holding every default keeps them, as written)."""
    where = where or path()
    where.parent.mkdir(parents=True, exist_ok=True)
    default = asdict(Prefs())
    changed = {k: v for k, v in asdict(prefs.checked()).items() if v != default[k]}
    write_atomic(where, (json.dumps(changed, indent=1) + "\n").encode())


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
    gestures.REHEARSE = replace(gestures.REHEARSE, start_hold_s=prefs.start_hold_s, done_hold_s=prefs.done_hold_s,
                                count_in_again_s=prefs.count_in_again_s)
    render.set_contrast(prefs.high_contrast)
    render.set_reduced_motion(system_reduce_motion() if prefs.reduced_motion is None else prefs.reduced_motion)


def system_reduce_motion() -> bool:
    """macOS's Reduce motion setting (Accessibility > Display); False where it can't be read."""
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


def main(argv: list[str]) -> int:
    prefs = load()
    if argv[:1] == ["set"] and len(argv) == 3:
        key, raw = argv[1], argv[2]
        kinds = {f.name: f.type for f in fields(Prefs)}
        if key not in kinds:
            print(f"unknown preference {key!r}; one of: {', '.join(kinds)}", file=sys.stderr)
            return 1
        truth = raw.lower() in ("1", "true", "yes", "on")
        if kinds[key] in (bool, "bool"):
            value = truth
        elif kinds[key] == "bool | None":  # auto: follow the system
            value = None if raw.lower() == "auto" else truth
        elif kinds[key] in (str, "str"):
            value = raw
        else:
            value = float(raw)
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
