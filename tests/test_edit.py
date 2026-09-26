"""Stress edits make notes revisions; undo steps back; takes keep the notes
they were recorded with."""

import json
from datetime import datetime

import numpy as np
import pytest

import main
from palmcards.edit import is_stressed, new_marks, toggle_stress
from palmcards.gestures import GestureLog
from palmcards.notes import Mark, MarkKind, parse_text
from palmcards.session import Session
from tests.test_app_lifecycle import FakeSupervisor

TEXT = "# One\n\n/ Thank you for *being* here tonight. [fall] Second one here.\n"


def test_toggling_stress_rebuilds_just_that_sentence():
    notes = parse_text(TEXT, "md")
    on = toggle_stress(notes, 0, 5)  # "tonight."
    s = on.sentences[0]
    assert s.raw == "/ Thank you for *being* here *tonight.* [fall]"
    assert s.marks == [Mark(MarkKind.FALL), Mark(MarkKind.SHORT_PAUSE, 0), Mark(MarkKind.STRESS, 3),
                       Mark(MarkKind.STRESS, 5)]  # the parser's order: bracket marks first
    assert on.sentences[1].raw == notes.sentences[1].raw and s.section == 0 and s.index == 0
    assert not is_stressed(notes, 0, 5)  # the original is left alone
    off = toggle_stress(on, 0, 3)
    assert off.sentences[0].raw == "/ Thank you for being here *tonight.* [fall]" and not is_stressed(off, 0, 3)
    with pytest.raises(IndexError):
        toggle_stress(notes, 0, 99)


def new_session(tmp_path) -> Session:
    path = tmp_path / "talk.md"
    path.write_text(TEXT)
    return Session.create(path, root=tmp_path / "sessions")


def test_edits_are_revisions_and_undo_steps_back(tmp_path):
    session = new_session(tmp_path)
    assert not session.dir.exists()
    imported = None
    edited = session.edit(toggle_stress(session._parsed, 0, 5), note="stress on")
    imported = session.revision(edited)["parent"]
    assert session.dir.exists() and [r["provenance"] for r in session.revisions] == ["imported", "edited"]
    assert session.current_revision == edited
    take = session.add_take(np.zeros(800, np.float32), 8000, 1.0, datetime.now(), [(0.0, 0)])
    assert take.revision == edited
    assert session.undo() == imported and session.current_revision == imported
    assert session.undo() is None  # nothing before the import
    again = session.add_take(np.zeros(800, np.float32), 8000, 2.0, datetime.now(), [(0.0, 0)])
    assert again.revision == imported
    branch = session.edit(toggle_stress(session.current_notes(), 1, 0))
    assert session.revision(branch)["parent"] == imported  # a new edit follows the undo, not the last one
    session.release()
    loaded = Session.load(session.dir)
    assert loaded.current_revision == branch and len(loaded.revisions) == 3
    # Ids never collide across branches: take 1 (the undone edit) shares nothing with "branch"
    # (its sentence 1 was edited there, its sentence 2 in "branch"); take 2 keeps sentence 1.
    ids = [s["id"] for r in loaded.revisions for s in loaded.snapshot(r["id"])["sentences"]]
    assert len(set(ids)) == 4  # s1, s2, then one new id per edited sentence, none reused
    assert loaded.sentence_map(loaded.take(1)) == {}
    assert loaded.sentence_map(loaded.take(2)) == {0: 0}


def takes_for(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "Supervisor", FakeSupervisor)
    session = new_session(tmp_path)
    return main.Takes(session._parsed, session, GestureLog(), main.Devices(), follow=False)


def test_the_app_edits_and_undoes_and_rebuilds_its_board(tmp_path, monkeypatch):
    takes = takes_for(tmp_path, monkeypatch)
    assert takes.undo() == "NOTHING TO UNDO"
    note = takes.edit_stress(0, 5)
    assert note == 'STRESSED "TONIGHT."  /  U: UNDO' and takes.notes_version == 1
    assert is_stressed(takes.notes, 0, 5) and takes.board.notes is takes.notes
    assert takes.log.entries[-1]["kind"] == "edit" and takes.log.entries[-1]["on"] is True
    assert takes.undo() == "UNDONE" and not is_stressed(takes.notes, 0, 5) and takes.notes_version == 2
    data = json.loads((takes.session.dir / "session.json").read_text())
    assert data["current"] == data["revisions"][0]["id"] and len(data["revisions"]) == 2


def test_suggested_marks_only_add_and_read_in_order():
    s = parse_text("[slow] Thank you for *being* here / tonight.", "txt").sentences[0]
    assert Mark(MarkKind.SHORT_PAUSE, 5) in s.marks  # "/" before "tonight."
    got = new_marks(s, [("fall", None), ("stress", 3), ("fast", None), ("long_pause", 5), ("stress", 1),
                        ("short_pause", 2), ("long_pause", 2), ("stress", 1), ("rise", None)])
    # Dropped: stress on "being" (there), fast (it has a pace), a pause where there is one, a second
    # pause in the same gap, the repeat, a second ending. The rest in reading order.
    assert got == [Mark(MarkKind.STRESS, 1), Mark(MarkKind.SHORT_PAUSE, 2), Mark(MarkKind.FALL)]
    assert new_marks(s, [("slow", None), ("stress", 3)]) == []

