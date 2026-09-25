"""Listing, exporting and deleting recordings: nothing goes without --yes,
and an open session is never deleted. Temporary folders only."""

import json
import zipfile
from datetime import datetime

import numpy as np

from palmcards import data
from palmcards.session import Session


def recorded(tmp_path, when: datetime):
    notes = tmp_path / "talk.md"
    notes.write_text("Hello there friend.")
    root = tmp_path / "sessions"
    log = root / "gesture-logs" / f"{when:%Y%m%d-%H%M%S}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("{}\n")
    session = Session.create(notes, root=root, gesture_log=log, now=when)
    session.add_take(np.zeros(800, np.float32), 8000, 1.0, when, [(0.0, 0)])
    return session, root, log


def test_list_and_export(tmp_path, capsys):
    session, root, _ = recorded(tmp_path, datetime(2026, 9, 1, 10, 0, 0))
    session.release()
    assert data.main(["list"], root) == 0
    assert session.dir.name in capsys.readouterr().out
    dest = tmp_path / "out.zip"
    assert data.main(["export", session.dir.name[:20], str(dest)], root) == 0
    names = zipfile.ZipFile(dest).namelist()
    assert f"{session.dir.name}/take-01.wav" in names and f"{session.dir.name}/session.json" in names
    assert not any(n.endswith("session.lock") for n in names)
    assert data.main(["export", session.dir.name, str(dest)], root) == 1  # never overwrites


def test_delete_needs_yes_and_takes_the_log_with_it(tmp_path, capsys):
    session, root, log = recorded(tmp_path, datetime(2026, 9, 1, 10, 0, 0))
    session.release()
    assert data.main(["delete", session.dir.name], root) == 0
    assert "would delete" in capsys.readouterr().out and session.dir.exists() and log.exists()
    assert data.main(["delete", session.dir.name, "--yes"], root) == 0
    assert not session.dir.exists() and not log.exists()


def test_an_open_session_is_never_deleted(tmp_path, capsys):
    session, root, _ = recorded(tmp_path, datetime(2026, 9, 1, 10, 0, 0))  # still holds its lock
    assert data.main(["delete", session.dir.name, "--yes"], root) == 1
    assert session.dir.exists() and "open" in capsys.readouterr().err


def test_prune_only_old_sessions_and_only_with_yes(tmp_path, capsys):
    old, root, _ = recorded(tmp_path, datetime(2020, 1, 1, 10, 0, 0))
    old.release()
    new_notes = tmp_path / "new.md"
    new_notes.write_text("Fresh words here.")
    new = Session.create(new_notes, root=root)
    new.add_take(np.zeros(800, np.float32), 8000, 1.0, datetime.now(), [(0.0, 0)])
    new.release()
    assert data.main(["prune", "--older-than", "30"], root) == 0
    assert old.dir.exists()
    assert data.main(["prune", "--older-than", "30", "--yes"], root) == 0
    assert not old.dir.exists() and new.dir.exists()
    assert json.loads((new.dir / "session.json").read_text())["takes"]
