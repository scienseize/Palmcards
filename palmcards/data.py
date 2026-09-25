"""Your recordings: list, export, delete, prune. Nothing is ever deleted
automatically or without --yes.

  python -m palmcards.data list
  python -m palmcards.data export RUN DEST.zip       copy a session out (audio, notes, results)
  python -m palmcards.data delete RUN [--yes]        without --yes: says what would go
  python -m palmcards.data prune --older-than DAYS [--yes]

RUN is a session folder name (or a unique prefix of one) under the data
directory (palmcards.paths). A session another PalmCards has open is never
deleted. Gesture logs are listed and pruned with the sessions that refer to
them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from palmcards.session import SESSIONS_DIR, Session, SessionBusy, SessionError


def sessions(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("2*") if (p / "session.json").exists())


def find(root: Path, name: str) -> Path:
    exact = root / name
    if (exact / "session.json").exists():
        return exact
    hits = [p for p in sessions(root) if p.name.startswith(name)]
    if len(hits) != 1:
        raise SessionError(f"{'no' if not hits else 'more than one'} session matches {name!r} in {root}")
    return hits[0]


def size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def started(folder: Path) -> datetime:
    return datetime.strptime(folder.name[:15], "%Y%m%d-%H%M%S")


def describe(folder: Path) -> str:
    s = Session.load(folder)
    return (f"{folder.name}  {len(s.takes)} take{'s' if len(s.takes) != 1 else ''}  "
            f"{size(folder) / 1e6:.1f} MB  notes: {s.notes.name}")


def export(folder: Path, dest: Path) -> Path:
    """The whole session folder as a zip (the lock file left out). Never overwrites."""
    dest = Path(dest)
    if dest.exists():
        raise SessionError(f"{dest} already exists")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.name != "session.lock":
                z.write(p, Path(folder.name) / p.relative_to(folder))
    return dest


def delete(folder: Path, root: Path, yes: bool) -> list[Path]:
    """The session folder and its gesture log (+ trace), if `yes`; returns what
    goes (or would go). Refuses a session open elsewhere."""
    session = Session.load(folder)
    session.acquire()  # SessionBusy if another PalmCards has it open
    doomed = [folder]
    if session.gesture_log:
        log = root / session.gesture_log
        doomed += [p for p in (log, log.with_suffix(".trace.jsonl")) if p.exists()]
    if yes:
        session.release()
        for p in doomed:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    else:
        session.release()
    return doomed


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    root = root or SESSIONS_DIR
    ap = argparse.ArgumentParser(prog="python -m palmcards.data", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("export")
    e.add_argument("run")
    e.add_argument("dest", type=Path)
    d = sub.add_parser("delete")
    d.add_argument("run")
    d.add_argument("--yes", action="store_true")
    pr = sub.add_parser("prune")
    pr.add_argument("--older-than", type=float, required=True, metavar="DAYS")
    pr.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            print(f"sessions in {root}")
            for folder in sessions(root):
                print("  " + describe(folder))
        elif args.cmd == "export":
            print(f"exported to {export(find(root, args.run), args.dest)}")
        elif args.cmd == "delete":
            gone = delete(find(root, args.run), root, args.yes)
            verb = "deleted" if args.yes else "would delete (add --yes)"
            print("\n".join(f"{verb}: {p}" for p in gone))
        else:
            cutoff = datetime.now() - timedelta(days=args.older_than)
            old = [f for f in sessions(root) if started(f) < cutoff]
            if not old:
                print(f"no sessions older than {args.older_than:g} days")
            for folder in old:
                try:
                    gone = delete(folder, root, args.yes)
                except SessionBusy as exc:
                    print(f"kept (open): {exc}")
                    continue
                verb = "deleted" if args.yes else "would delete (add --yes)"
                print("\n".join(f"{verb}: {p}" for p in gone))
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
