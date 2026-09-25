"""Reopening a saved session in Review, playing a sentence, and takes from
an earlier notes revision shown on the current notes by sentence id."""

import argparse
import json
from datetime import datetime

import numpy as np
import pytest

import main
from palmcards.notes import notes_from_bytes, parse_text
from palmcards.playback import sentence_clip
from palmcards.review import Board
from palmcards.revisions import index_map, to_snapshot
from palmcards.session import Session, write_wav
from tests.test_app_lifecycle import NOTES, Rig

TEXT = "# One\n\nHello there friend. / Good night all.\n\n# Two\n\nSee you soon.\n"


def verdicts(marks_per_sentence):
    return {"take_wpm": 150.0, "counts": {"hit": 1, "missed": 0, "unclear": 0, "skipped": 0}, "sentences": [
        {"sentence": i, "status": "spoken", "wpm": 150.0, "fillers": [],
         "marks": [{"kind": "short_pause", "word": 0, "verdict": v, "reason": "r"} for v in marks]}
        for i, marks in enumerate(marks_per_sentence)]}


def test_takes_of_an_earlier_revision_show_on_the_current_notes_by_sentence_id():
    old = parse_text(TEXT, "md")
    new = parse_text(TEXT.replace("Good night all.", "Sleep well.").replace("# Two", "# Two\n\nIntro here."), "md")
    a, _ = to_snapshot(old)
    b, _ = to_snapshot(new, previous=a)
    mapping = index_map(a, b)
    assert mapping == {0: 0, 2: 3}  # "Good night all." was edited; "See you soon." moved down
    board = Board(new)
    board.add(1, verdicts([["hit"], ["missed"], []]), sentence_map=mapping)
    shown = board.takes[1]["sentences"]
    assert [s["status"] for s in shown] == ["spoken", "skipped", "skipped", "spoken"]
    assert board.mark_verdicts()[0] == ("hit",) and board.said_in(1) == []  # the edited one isn't claimed


def test_a_sentence_clip_is_its_words_padded(tmp_path):
    (tmp_path / "n.md").write_text(TEXT)
    session = Session.create(tmp_path / "n.md", root=tmp_path / "s")
    audio = np.linspace(-0.5, 0.5, 16000 * 4).astype(np.float32)
    take = session.add_take(audio, 16000, 10.0, datetime.now(), [(0.0, 0)])
    take.alignment = {"sentences": [{"start": 11.0, "end": 12.0}, {"start": None, "end": None}]}
    clip, rate = sentence_clip(session, take, 0)
    assert rate == 16000 and len(clip) == int(1.5 * 16000)  # 1 s of words, 0.25 s each side
    assert sentence_clip(session, take, 1) is None  # not said


class FakePlayer:
    played = []

    def play(self, audio, rate):
        FakePlayer.played.append((len(audio), rate))

    def stop(self):
        pass


def saved_session(tmp_path):
    notes_path = tmp_path / "talk.md"
    notes_path.write_bytes(NOTES)
    session = Session.create(notes_path, root=tmp_path / "sessions")
    audio = np.full(16000 * 3, 0.1, np.float32)
    judged = session.add_take(audio, 16000, 5.0, datetime.now(), [(0.0, 0)])
    session.add_take(audio, 16000, 20.0, datetime.now(), [(0.0, 0)])  # its analysis never finished
    (session.dir / judged.verdicts_name).write_text(json.dumps(verdicts([[], [], []])))
    alignment = {"sentences": [{"sentence": 0, "status": "spoken", "start": 5.5, "end": 6.5, "words": [0]},
                               {"sentence": 1, "status": "skipped", "start": None, "end": None, "words": [None]},
                               {"sentence": 2, "status": "skipped", "start": None, "end": None, "words": [None]}],
                 "fillers": [], "restarts": [], "extras": [], "unsure": []}
    session.set_result(1, judged.transcript_name, alignment, judged.verdicts_name, {})
    session.release()
    return session


def test_reopening_goes_to_review_plays_a_sentence_and_resumes_analysis(tmp_path, monkeypatch):
    saved = saved_session(tmp_path)
    FakePlayer.played = []
    rig = Rig(tmp_path, monkeypatch, script={}, keys={3: ord("a"), 8: ord("q")})
    rig.devices.player = FakePlayer
    session = Session.load(saved.dir)
    session.acquire()
    notes = notes_from_bytes(NOTES, rig.notes_path)
    assert main.run(session.notes, notes, b"", devices=rig.devices, session=session, prefs_file=rig.prefs_file) == 0
    assert FakePlayer.played == [(int(1.5 * 16000), 16000)]  # sentence 1 of take 1
    (supervisor,) = rig.transcribers
    assert [j["take"] for j in supervisor.jobs] == [2]  # the unfinished take, submitted again
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert {"kind": "mode", "mode": "review"}.items() <= next(e for e in log if e["kind"] == "mode").items()
    assert any(e["kind"] == "play" and e["take"] == 1 for e in log)
    assert len(Session.load(saved.dir).takes) == 2  # nothing recorded, nothing lost


def test_reopening_refuses_an_old_session_without_notes(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "20260925-101345-talk"
    folder.mkdir()
    (folder / "session.json").write_text(json.dumps({"notes": "/x.md", "gesture_log": None, "takes": []}))
    args = argparse.Namespace(open=str(folder), trace=False, no_follow=True)
    assert main.reopen(args) == 1
    assert "--rebind" in capsys.readouterr().err
