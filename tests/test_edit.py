"""Edits make notes revisions; undo steps back; takes keep the notes they
were recorded with."""

import json
from datetime import datetime

import numpy as np

import main
from palmcards.edit import replace_word
from palmcards.gestures import GestureLog
from palmcards.notes import parse_text
from palmcards.session import Session
from tests.test_app_lifecycle import FakeSupervisor

TEXT = "# One\n\nThank you for being here tonight. Second one here.\n"


def test_replacing_a_word_rebuilds_just_that_sentence():
    notes = parse_text(TEXT, "md")
    new = replace_word(notes, 0, 5, "this evening")  # "tonight."
    s = new.sentences[0]
    assert s.text == s.raw == "Thank you for being here this evening."
    assert [w.text for w in s.words][-2:] == ["this", "evening."] and s.section == 0 and s.index == 0
    assert new.sentences[1].raw == notes.sentences[1].raw
    assert notes.sentences[0].text == "Thank you for being here tonight."  # the original is left alone


def new_session(tmp_path) -> Session:
    path = tmp_path / "talk.md"
    path.write_text(TEXT)
    return Session.create(path, root=tmp_path / "sessions")


def test_edits_are_revisions_and_undo_steps_back(tmp_path):
    session = new_session(tmp_path)
    assert not session.dir.exists()
    edited = session.edit(replace_word(session._parsed, 0, 5, "today."), note="alternative")
    imported = session.revision(edited)["parent"]
    assert session.dir.exists() and [r["provenance"] for r in session.revisions] == ["imported", "edited"]
    assert session.current_revision == edited
    take = session.add_take(np.zeros(800, np.float32), 8000, 1.0, datetime.now(), [(0.0, 0)])
    assert take.revision == edited
    assert session.undo() == imported and session.current_revision == imported
    assert session.undo() is None  # nothing before the import
    again = session.add_take(np.zeros(800, np.float32), 8000, 2.0, datetime.now(), [(0.0, 0)])
    assert again.revision == imported
    branch = session.edit(replace_word(session.current_notes(), 1, 0, "Another"))
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
    note = takes.use_alternative(0, 5, "today")
    assert note == '"TONIGHT." → "TODAY"  /  CROSS YOUR FINGERS: UNDO' and takes.notes_version == 1
    assert takes.notes.sentences[0].text.endswith("today.") and takes.board.notes is takes.notes
    assert takes.log.entries[-1]["kind"] == "edit" and takes.log.entries[-1]["op"] == "alternative"
    assert takes.undo() == "UNDONE" and takes.notes.sentences[0].text.endswith("tonight.")
    assert takes.notes_version == 2
    data = json.loads((takes.session.dir / "session.json").read_text())
    assert data["current"] == data["revisions"][0]["id"] and len(data["revisions"]) == 2
