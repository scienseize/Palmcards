"""Reopening a saved session in Review, playing a sentence, and takes from
an earlier notes revision shown on the current notes by sentence id."""

import argparse
import json
from datetime import datetime

import numpy as np
import pytest

import main
from palmcards.notes import notes_from_bytes, parse_text
from palmcards.playback import sentence_clip, span_clip
from palmcards.review import Board
from palmcards.revisions import index_map, to_snapshot
from palmcards.session import Session, write_wav
from tests.test_app_lifecycle import NOTES, Rig

TEXT = "# One\n\nHello there friend. / Good night all.\n\n# Two\n\nSee you soon.\n"


def alignment(statuses):
    """A take's alignment with each sentence spoken or skipped."""
    return {"sentences": [{"sentence": i, "status": st, "coverage": 1.0 if st == "spoken" else 0.0,
                           "start": 5.0 + 2 * i if st == "spoken" else None,
                           "end": 6.5 + 2 * i if st == "spoken" else None,
                           "words": [10 * i + k if st == "spoken" else None for k in range(4)], "misheard": []}
                          for i, st in enumerate(statuses)],
            "fillers": [], "restarts": [], "extras": [], "unsure": []}


def test_takes_of_an_earlier_revision_show_on_the_current_notes_by_sentence_id():
    old = parse_text(TEXT, "md")
    new = parse_text(TEXT.replace("Good night all.", "Sleep well.").replace("# Two", "# Two\n\nIntro here."), "md")
    a, _ = to_snapshot(old)
    b, _ = to_snapshot(new, previous=a)
    mapping = index_map(a, b)
    assert mapping == {0: 0, 2: 3}  # "Good night all." was edited; "See you soon." moved down
    board = Board(new)
    board.add(1, alignment(["spoken", "spoken", "spoken"]), sentence_map=mapping)
    assert [board.said_in(i) for i in range(4)] == [[1], [], [], [1]]  # the edited one isn't claimed
    assert board.rows[1][3]["wpm"] == 160.0  # "See you soon.": 4 words in 1.5 s


def test_a_sentence_clip_is_its_words_padded(tmp_path):
    (tmp_path / "n.md").write_text(TEXT)
    session = Session.create(tmp_path / "n.md", root=tmp_path / "s")
    audio = np.linspace(-0.5, 0.5, 16000 * 4).astype(np.float32)
    take = session.add_take(audio, 16000, 10.0, datetime.now(), [(0.0, 0)])
    take.alignment = {"sentences": [{"start": 11.0, "end": 12.0}, {"start": None, "end": None}]}
    clip, rate = sentence_clip(session, take, 0)
    assert rate == 16000 and len(clip) == int(1.5 * 16000)  # 1 s of words, 0.25 s each side
    assert sentence_clip(session, take, 1) is None  # not said


def test_a_paragraph_clip_runs_from_its_first_word_to_its_last(tmp_path):
    (tmp_path / "n.md").write_text(TEXT)
    session = Session.create(tmp_path / "n.md", root=tmp_path / "s")
    audio = np.linspace(-0.5, 0.5, 16000 * 6).astype(np.float32)
    take = session.add_take(audio, 16000, 10.0, datetime.now(), [(0.0, 0)])
    take.alignment = {"sentences": [{"start": 11.0, "end": 12.0}, {"start": 12.5, "end": 14.0},
                                    {"start": None, "end": None}]}
    clip, _ = span_clip(session, take, [0, 1, 2])
    assert len(clip) == int(3.5 * 16000)  # 11.0 to 14.0, and 0.25 s each side
    assert span_clip(session, take, [2]) is None


def test_a_focused_paragraph_plays_from_the_take_it_shows(tmp_path, monkeypatch):
    from palmcards.gestures import GestureLog
    from tests.test_llm import FakeSupervisor

    monkeypatch.setattr(main, "Supervisor", FakeSupervisor)
    (tmp_path / "n.md").write_text(TEXT)
    session = Session.create(tmp_path / "n.md", root=tmp_path / "s")
    audio = np.full(16000 * 6, 0.1, np.float32)
    take = session.add_take(audio, 16000, 10.0, datetime.now(), [(0.0, 0)])
    aligned = {"sentences": [{"sentence": 0, "status": "spoken", "start": 11.0, "end": 12.0, "words": [0, 1, 2]},
                             {"sentence": 1, "status": "spoken", "start": 12.5, "end": 14.0, "words": [3, 4, 5]},
                             {"sentence": 2, "status": "skipped", "start": None, "end": None, "words": [None]}],
               "fillers": [], "restarts": [], "extras": [], "unsure": []}
    session.set_result(take.number, take.transcript_name, aligned)
    FakePlayer.played = []
    takes = main.Takes(session.current_notes(), session, GestureLog(), main.Devices(player=FakePlayer), follow=False)
    takes._fill_board()
    assert takes.paragraph(1) == [0, 1] and takes.paragraph(2) == [2]
    assert takes.play_paragraph([0, 1]) == "PLAYING TAKE 1"
    assert FakePlayer.played == [(int(3.5 * 16000), 16000)]
    assert takes.play_paragraph([2]) == "NO TAKE TO PLAY"
    takes.close()


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
    aligned = {"sentences": [{"sentence": 0, "status": "spoken", "start": 5.5, "end": 6.5, "words": [0]},
                             {"sentence": 1, "status": "skipped", "start": None, "end": None, "words": [None]},
                             {"sentence": 2, "status": "skipped", "start": None, "end": None, "words": [None]}],
               "fillers": [], "restarts": [], "extras": [], "unsure": []}
    session.set_result(1, judged.transcript_name, aligned)
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


def test_a_toggles_playback_and_x_stops_it(tmp_path, monkeypatch):
    saved = saved_session(tmp_path)
    FakePlayer.played = []
    keys = {3: ord("a"), 4: ord("a"), 5: ord("a"), 6: ord("x"), 7: ord("x"), 9: ord("q")}
    rig = Rig(tmp_path, monkeypatch, script={}, keys=keys)
    rig.devices.player = FakePlayer
    session = Session.load(saved.dir)
    session.acquire()
    notes = notes_from_bytes(NOTES, rig.notes_path)
    assert main.run(session.notes, notes, b"", devices=rig.devices, session=session, prefs_file=rig.prefs_file) == 0
    assert len(FakePlayer.played) == 2  # a plays, a stops, a plays again, x stops
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert [e["why"] for e in log if e["kind"] == "play_stop"] == ["key", "key"]
    assert [e["acted"] for e in log if e["kind"] == "key" and e["command"] == "stop"] == [False]  # nothing left: x as before


def replayed_frames(tmp_path, monkeypatch, prefs: str | None = None) -> list:
    """Reopen a session whose take 1 has a video (recorded mirrored: bright on
    its left half) and a transcript, press `a` in Review: every frame shown."""
    pytest.importorskip("av")
    from palmcards.video import VideoWriter

    saved = saved_session(tmp_path)
    w = VideoWriter(saved.dir, 1, (640, 360), codec="mpeg4")
    recorded = np.zeros((360, 640, 3), np.uint8)
    recorded[:, :320] = 200  # recorded mirrored: bright on the left
    for i in range(60):  # take 1's video: 3 s of it from t_first 5.0, its audio's start
        w.push(recorded.copy(), 5.0 + i * 0.05)
        __import__("time").sleep(0.005)
    w.stop()
    assert w.wait(10) and w.dropped == 0
    session = Session.load(saved.dir)
    session.take(1).video = w.summary()
    session.save()
    (saved.dir / session.take(1).transcript).write_text(json.dumps({"words": [
        {"text": "Hello", "start": 5.5, "end": 5.8}, {"text": "um", "start": 5.9, "end": 6.4}]}))
    session.release()

    rig = Rig(tmp_path, monkeypatch, script={}, keys={3: ord("a"), 260: ord("q")})
    if prefs:
        rig.prefs_file.write_text(prefs)
    rig.devices.player = FakePlayer
    frames = []
    rig.devices.show = lambda name, frame: frames.append(frame.copy())
    session = Session.load(saved.dir)
    session.acquire()
    notes = notes_from_bytes(NOTES, rig.notes_path)
    assert main.run(session.notes, notes, b"", devices=rig.devices, session=session, prefs_file=rig.prefs_file) == 0
    return frames


def test_a_take_with_video_replays_it_in_place_of_the_mirror_while_it_plays(tmp_path, monkeypatch):
    frames = replayed_frames(tmp_path, monkeypatch)
    # The face zone (0.36-0.55 of the width) has nothing drawn on it: either side of the video's middle.
    left = [int(f[50:300, 240:310].mean()) for f in frames]
    right = [int(f[50:300, 330:350].mean()) for f in frames]
    notes = [int(f[:, 38:230].max()) for f in frames]  # the text column
    assert right[:3] == [0, 0, 0] and notes[0] > 0  # the live (black) mirror with the notes, before playing
    replayed = [i for i, (a, b) in enumerate(zip(left, right)) if a <= 6 and abs(b - 200) <= 6]
    assert replayed and replayed[0] <= 10  # the take's video from the first frames of the clip, flipped back
    shown = frames[replayed[0]]
    expected = np.zeros_like(shown)
    expected[:, 320:] = 200  # as others see you: the bright half on the right, and no notes on it
    differs = np.argwhere((np.abs(shown.astype(int) - expected) > 8).any(axis=2))
    near_bottom = differs[:, 0] > 360 - 40
    assert len(differs) and all(near_bottom | (differs[:, 1] > 640 - 0.06 * 640 - 150) & (differs[:, 0] < 40))
    # nothing but the bar along the bottom (captions join it once a word is said) and "M: FLIP VIDEO" top right
    assert right[-1] == 0 and notes[-1] > 0  # the clip (1.5 s) over: the live mirror again, with the notes


def test_the_replay_can_be_mirrored_and_is_always_captioned(tmp_path, monkeypatch):
    frames = replayed_frames(tmp_path, monkeypatch, '{"replay_mirrored": true}\n')
    left = [int(f[50:300, 240:310].mean()) for f in frames]
    right = [int(f[50:300, 330:350].mean()) for f in frames]
    replayed = [i for i, (a, b) in enumerate(zip(left, right)) if abs(a - 200) <= 6 and b <= 6]
    assert replayed and replayed[0] <= 10  # as recorded, mirrored: the bright half on the left
    # Captions once a word has been said (0.25 s into the clip): a line above the bar, across the middle.
    captioned = [i for i in replayed if (frames[i][285:318, 330:350] != 0).any()]  # above the bar and its ticks
    assert captioned and captioned[0] > replayed[0]
