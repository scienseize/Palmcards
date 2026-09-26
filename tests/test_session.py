import json
import os
from datetime import datetime

import numpy as np
import pytest

from palmcards.notes import load_notes, parse_text
from palmcards.revisions import content_hash, from_snapshot, to_snapshot
from palmcards.session import LegacyNotes, Session, SessionBusy, SessionError, read_wav

NOTES = "# One\n\nHello there friend. / Good *night* all. [fall]\n\n# Two\n\nSee you soon.\n"


def notes_file(tmp_path, name="talk.md", text=NOTES):
    path = tmp_path / "src" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def silence(n=800):
    return np.zeros(n, np.float32)


def test_add_take_writes_wav_and_json(tmp_path):
    log = tmp_path / "gesture-logs" / "20260925-120000.jsonl"
    session = Session.create(notes_file(tmp_path), root=tmp_path, gesture_log=log,
                             now=datetime(2026, 9, 25, 12, 0, 0))
    assert not session.dir.exists()  # nothing on disk until the first take

    rate = 16000
    audio = (0.5 * np.sin(np.linspace(0, 2 * np.pi * 440, rate * 2))).astype(np.float32)
    take = session.add_take(audio, rate, t_start=12.3456, started=datetime(2026, 9, 25, 12, 0, 5),
                            sections=[(0.0, 0), (1.25, 1)])

    assert session.dir.parent == tmp_path and session.dir.name.startswith("20260925-120000-talk-")
    assert (take.number, take.wav, take.duration_s, take.t_start) == (1, "take-01.wav", 2.0, 12.346)
    assert take.peak > 0.49 and not take.silent
    back, back_rate = read_wav(session.dir / "take-01.wav")
    assert back_rate == rate and np.allclose(back, audio, atol=1e-4)
    assert not list(session.dir.glob("*.tmp"))  # published, not left half-written

    data = json.loads((session.dir / "session.json").read_text())
    assert data["schema"] == 3 and data["id"]
    assert data["gesture_log"] == "gesture-logs/20260925-120000.jsonl"
    assert data["takes"][0]["sections"] == [{"section": 0, "t": 0.0}, {"section": 1, "t": 1.25}]
    assert data["takes"][0]["revision"] == data["revisions"][0]["id"]


def test_takes_number_up_and_reload(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    for _ in range(2):
        session.add_take(silence(), 8000, 0.0, datetime.now(), [(0.0, 0)])
    loaded = Session.load(session.dir)
    assert [t.wav for t in loaded.takes] == ["take-01.wav", "take-02.wav"]
    assert loaded.takes[1].silent  # all zeros: the mic delivered nothing


def test_language_transcript_and_alignment_round_trip(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path, language="de")
    take = session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    assert take.transcript_name == "take-01.transcript.json"
    data = json.loads((session.dir / "session.json").read_text())
    assert data["language"] == "de"
    assert "transcript" not in data["takes"][0]  # not there until transcribed

    alignment = {"sentences": [], "fillers": [], "restarts": [], "extras": [], "unsure": []}
    session.set_result(1, "take-01.transcript.json", alignment)
    loaded = Session.load(session.dir)
    assert loaded.language == "de"
    assert loaded.take(1).transcript == "take-01.transcript.json"
    assert loaded.take(1).alignment == alignment


def test_results_and_drill_round_trip_and_old_verdicts_are_kept(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    drill = session.add_take(silence(), 8000, 2.0, datetime.now(), [(0.0, 1)], drill=2)
    assert drill.prosody_name == "take-02.prosody.npz"
    session.set_result(2, "take-02.transcript.json", {"sentences": []}, {"version": 4})
    data = json.loads((session.dir / "session.json").read_text())
    assert "drill" not in data["takes"][0] and "verdicts" not in data["takes"][1]  # never written now
    loaded = Session.load(session.dir)
    assert (loaded.take(2).drill, loaded.take(2).metrics) == (2, {"version": 4})
    # A session from when marks were judged keeps its verdict fields as found.
    data["takes"][0] |= {"verdicts": "take-01.verdicts.json", "marks": {"hit": 2, "missed": 1}}
    (session.dir / "session.json").write_text(json.dumps(data))
    session.release()
    old = Session.load(session.dir)
    old.acquire()
    old.save()
    assert json.loads((session.dir / "session.json").read_text())["takes"][0]["verdicts"] == "take-01.verdicts.json"


# --- the notes a take was recorded with ------------------------------------------

def texts(notes):
    return [s.text for s in notes.sentences]


def test_the_imported_file_and_its_notes_are_kept(tmp_path):
    path = notes_file(tmp_path)
    session = Session.create(path, root=tmp_path / "sessions")
    take = session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    assert (session.dir / session.source["file"]).read_bytes() == path.read_bytes()
    assert session.notes == path.resolve()  # absolute: provenance only
    notes = Session.load(session.dir).notes_for(take)
    assert texts(notes) == texts(load_notes(path))
    assert [s.raw for s in notes.sentences] == [s.raw for s in load_notes(path).sentences]


@pytest.mark.parametrize("change", ["edit_same_count", "delete", "move"])
def test_changing_the_imported_file_leaves_the_take_alone(tmp_path, change):
    path = notes_file(tmp_path)
    session = Session.create(path, root=tmp_path / "sessions")
    take = session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    before = texts(session.notes_for(take))
    if change == "edit_same_count":  # same number of sentences: a count check wouldn't notice
        path.write_text(NOTES.replace("Hello there friend", "Goodbye here enemy"))
    elif change == "delete":
        path.unlink()
    else:
        path.rename(path.with_name("moved.md"))
    assert texts(Session.load(session.dir).notes_for(take)) == before


def test_loads_from_another_working_directory(tmp_path, monkeypatch):
    session = Session.create(notes_file(tmp_path), root=tmp_path / "sessions")
    take = session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    loaded = Session.load(os.path.relpath(session.dir, elsewhere))
    assert texts(loaded.notes_for(loaded.take(take.number))) == texts(session.notes_for(take))


def test_two_sessions_in_the_same_second_never_share_a_folder(tmp_path):
    path = notes_file(tmp_path)
    now = datetime(2026, 9, 25, 10, 0, 0)
    a = Session.create(path, root=tmp_path / "s", now=now)
    b = Session.create(path, root=tmp_path / "s", now=now)
    b.dir = a.dir  # even if the random suffixes collided
    a.add_take(np.full(800, 0.2, np.float32), 8000, 1.0, now, [(0.0, 0)])
    b.add_take(np.full(800, 0.8, np.float32), 8000, 1.0, now, [(0.0, 0)])
    assert a.dir != b.dir
    assert read_wav(a.dir / "take-01.wav")[0][0] == pytest.approx(0.2, abs=1e-3)
    assert read_wav(b.dir / "take-01.wav")[0][0] == pytest.approx(0.8, abs=1e-3)
    assert Session.load(a.dir).id != Session.load(b.dir).id


def test_a_second_writer_is_refused_until_the_first_lets_go(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    other = Session.load(session.dir)  # reading is fine
    with pytest.raises(SessionBusy, match="pid"):
        other.set_result(1, "take-01.transcript.json", {"sentences": []})
    session.release()
    other.set_result(1, "take-01.transcript.json", {"sentences": []})
    assert Session.load(session.dir).take(1).transcript == "take-01.transcript.json"


def test_a_stray_take_file_is_reported_and_never_overwritten(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    (session.dir / "take-02.wav").write_bytes(b"left by a crash")
    (session.dir / "session.json.tmp").write_text("{")
    session.release()
    loaded = Session.load(session.dir)
    assert loaded.orphans == ["take-02.wav", "session.json.tmp"]
    take = loaded.add_take(silence(), 8000, 2.0, datetime.now(), [(0.0, 0)])
    assert take.wav == "take-03.wav" and (loaded.dir / "take-02.wav").read_bytes() == b"left by a crash"


def test_a_damaged_revision_is_refused(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    take = session.add_take(silence(), 8000, 1.0, datetime.now(), [(0.0, 0)])
    path = session.dir / session.revision(take.revision)["file"]
    path.write_text(path.read_text().replace("Hello", "Jello"))
    with pytest.raises(SessionError, match="hash"):
        Session.load(session.dir).notes_for(take)


# --- older and newer session files -----------------------------------------------

def legacy(tmp_path, notes_path):
    folder = tmp_path / "20260925-101345-talk"
    folder.mkdir()
    data = {"notes": str(notes_path), "gesture_log": None, "takes": [
        {"number": 1, "wav": "take-01.wav", "started": "2026-09-25T10:14:34.435", "t_start": 48.787,
         "duration_s": 53.323, "sample_rate": 48000, "peak": 0.4, "sections": [{"section": 0, "t": 0.0}]}]}
    (folder / "session.json").write_text(json.dumps(data))
    return folder, data


def test_loads_session_from_before_milestone_5(tmp_path):
    folder, _ = legacy(tmp_path, "/x/notes.md")
    loaded = Session.load(folder)
    assert loaded.language == "en"
    assert loaded.take(1).transcript is None and loaded.take(1).alignment is None
    assert loaded.take(1).revision is None


def test_an_old_session_needs_an_explicit_rebind_and_says_it_is_unverified(tmp_path):
    folder, original = legacy(tmp_path, notes_file(tmp_path))
    loaded = Session.load(folder)
    with pytest.raises(LegacyNotes, match="--rebind"):
        loaded.notes_for(loaded.take(1))
    rid = loaded.rebind_legacy()
    assert loaded.revision(rid)["provenance"] == "legacy-unverified" and not loaded.verified(loaded.take(1))
    # The old file is kept, untouched, before the first rewrite.
    assert json.loads((folder / "session.v1.json").read_text()) == original
    loaded.release()
    again = Session.load(folder)
    assert again.take(1).revision == rid and texts(again.notes_for(again.take(1)))
    assert again.rebind_legacy() == rid and len(again.revisions) == 1  # repeatable: nothing new
    assert json.loads((folder / "session.v1.json").read_text()) == original


def test_rebinding_needs_the_notes_file(tmp_path):
    folder, _ = legacy(tmp_path, tmp_path / "gone.md")
    with pytest.raises(SessionError, match="does not exist"):
        Session.load(folder).rebind_legacy()
    assert not (folder / "session.v1.json").exists()


def test_calibrations_and_take_vision_round_trip(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    take = session.add_take(silence(), 8000, 0.0, datetime.now(), [(0.0, 0)])
    assert session.calibration is None
    assert session.add_calibration({"status": "failed", "reason": "no face"}) == "c1"
    assert session.add_calibration({"status": "ok", "camera": {"n": 12}}) == "c2"
    take.vision = {"state": "recorded", "file": "take-01.face.npz", "calibration": "c2"}
    session.save()
    session.release()
    loaded = Session.load(session.dir)
    assert [c["id"] for c in loaded.calibrations] == ["c1", "c2"] and loaded.calibration["id"] == "c2"
    assert loaded.take(1).vision["calibration"] == "c2" and loaded.take(1).face_name == "take-01.face.npz"


def test_a_schema_2_session_is_read_and_rewritten_as_3_without_a_v1_copy(tmp_path):
    session = Session.create(notes_file(tmp_path), root=tmp_path)
    session.add_take(silence(), 8000, 0.0, datetime.now(), [(0.0, 0)])
    session.release()
    path = session.dir / "session.json"
    data = json.loads(path.read_text())
    data["schema"] = 2
    del data["calibrations"]
    path.write_text(json.dumps(data))
    loaded = Session.load(session.dir)
    assert loaded.calibrations == [] and loaded.take(1).vision is None
    loaded.save()
    assert json.loads(path.read_text())["schema"] == 3 and not (session.dir / "session.v1.json").exists()


def test_a_newer_schema_is_refused(tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"schema": 99, "notes": "x", "takes": []}))
    with pytest.raises(SessionError, match="schema 99"):
        Session.load(tmp_path)


def test_a_folder_without_session_json_is_refused(tmp_path):
    with pytest.raises(SessionError, match="not a session folder"):
        Session.load(tmp_path)


# --- revisions and stable identities ---------------------------------------------

def test_snapshot_round_trip_rebuilds_the_notes_without_parsing():
    notes = parse_text(NOTES, "md")
    snap, ancestry = to_snapshot(notes)
    back = from_snapshot(snap)
    assert ancestry == {} and [s["id"] for s in snap["sentences"]] == ["s1", "s2", "s3"]
    assert texts(back) == texts(notes) and [sec.title for sec in back.sections] == ["One", "Two"]
    for a, b in zip(back.sentences, notes.sentences):
        assert (a.raw, a.section, a.paragraph, a.index) == (b.raw, b.section, b.paragraph, b.index)
        assert [(w.text, w.norm, w.start) for w in a.words] == [(w.text, w.norm, w.start) for w in b.words]
    assert snap["sentences"][1]["words"][1]["id"] == "s2.w1" and "marks" not in snap["sentences"][1]
    assert snap["parser"] == 2


def test_a_snapshot_from_parser_1_with_marks_loads_and_keeps_its_hash():
    from palmcards.revisions import content_hash

    snap, _ = to_snapshot(parse_text(NOTES, "md"))
    old = json.loads(json.dumps(snap)) | {"parser": 1}
    for s in old["sentences"]:  # as parser 1 saved them: marks, and a stressed flag per word
        s["marks"] = [{"id": f"{s['id']}.m0", "kind": "short_pause", "word": 0}]
        for w in s["words"]:
            w["stressed"] = False
    assert content_hash(old) != content_hash(snap)  # the marks are part of what it holds
    back = from_snapshot(old)
    assert texts(back) == texts(from_snapshot(snap)) and not hasattr(back.sentences[0], "marks")


def test_unchanged_sentences_keep_their_ids_when_others_change():
    first, _ = to_snapshot(parse_text(NOTES, "md"))
    edited = NOTES.replace("Good *night* all.", "Good *morning* all.").replace("See you soon.", "Intro. See you soon.")
    second, ancestry = to_snapshot(parse_text(edited, "md"), previous=first)
    ids = {s["raw"]: s["id"] for s in second["sentences"]}
    assert ids["Hello there friend."] == "s1" and ids["See you soon."] == "s3"  # unchanged, even when moved
    assert ids["/ Good *morning* all. [fall]"] == "s4" and ancestry == {"s4": ["s2"]}  # edited: new id, replaces s2
    assert ids["Intro."] == "s5"  # new
    assert content_hash(first) != content_hash(second)


def test_the_content_hash_ignores_ids():
    notes = parse_text(NOTES, "md")
    snap, _ = to_snapshot(notes)
    renamed = json.loads(json.dumps(snap).replace('"s1', '"s9'))
    assert content_hash(renamed) == content_hash(snap)
