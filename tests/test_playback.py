"""What plays (a take's clip, "hear it"): progress, the end, stopping at once
and playing again right after."""

import subprocess
import sys
import types
from concurrent.futures import Future

import numpy as np
import pytest

from palmcards import tts
from palmcards.playback import END_SLACK_S, SAY_HELD_AT, ClipPlayer, Playback
from palmcards.tts import MacSay


class FakePlayer:
    def __init__(self):
        self.calls = []

    def play(self, audio, rate):
        self.calls.append(("play", len(audio), rate))

    def stop(self):
        self.calls.append(("stop",))


class FakeSpeaker:
    def __init__(self):
        self.calls, self.speaking = [], False

    def say_words(self, words):
        self.calls.append(("say", list(words)))
        self.speaking = True

    def estimate_s(self, words):
        return 0.5 * len(words)

    def stop(self):
        self.calls.append(("stop",))
        self.speaking = False


CLIP = (np.zeros(16000 * 2, np.float32), 16000)  # 2 s


def test_a_clip_reports_progress_and_ends_by_itself():
    player, pb = FakePlayer(), Playback()
    pb.start_clip(player, CLIP, ("review", (1,), 2), now=10.0)
    assert pb.playing and pb.target == ("review", (1,), 2)
    assert pb.progress(11.0) == 0.5
    assert pb.poll(11.9) and pb.poll(12.0 + END_SLACK_S / 2)  # the device's latency
    assert not pb.poll(12.0 + END_SLACK_S) and not pb.playing and pb.progress(12.5) is None
    assert player.calls == [("play", 32000, 16000)]  # it ended by itself: nothing to stop


def test_stop_is_at_once_and_a_new_playback_right_after_works():
    player, pb = FakePlayer(), Playback()
    pb.start_clip(player, CLIP, ("review", (1,), 2), now=0.0)
    assert pb.stop() and not pb.playing
    assert not pb.stop()  # nothing left to stop
    pb.start_clip(player, CLIP, ("review", (1,), 2), now=0.1)
    assert pb.playing and pb.progress(1.1) == 0.5
    assert player.calls == [("play", 32000, 16000), ("stop",), ("play", 32000, 16000)]


def test_hear_it_progress_is_an_estimate_held_short_of_the_end_until_say_finishes():
    speaker, pb = FakeSpeaker(), Playback()
    pb.start_say(speaker, ["one", "two", "three", "four"], ("prepare", (0,), None), now=0.0)  # about 2 s
    assert pb.progress(1.0) == 0.5
    assert pb.poll(5.0) and pb.progress(5.0) == SAY_HELD_AT  # still speaking, past the estimate
    speaker.speaking = False  # say has finished
    assert not pb.poll(5.1) and not pb.playing


class RenderingSpeaker(FakeSpeaker):
    """A speaker that renders: its audio is a Future the test completes."""

    def __init__(self):
        super().__init__()
        self.futures = {}

    def render_async(self, words):
        return self.futures.setdefault(" ".join(words), Future())


def test_hear_it_plays_the_rendered_audio_with_exact_progress():
    player, speaker, pb = FakePlayer(), RenderingSpeaker(), Playback()
    speaker.render_async(["one", "two"]).set_result(CLIP)  # rendered when the sentence was focused
    pb.start_rendered(player, speaker, ["one", "two"], ("prepare", (0,), None), now=0.0)
    assert player.calls == [("stop",), ("play", 32000, 16000)] and speaker.calls == []  # not said live
    assert pb.progress(1.0) == 0.5 and pb.progress(2.0) == 1.0
    assert not pb.poll(2.0 + END_SLACK_S)


def test_hear_it_not_rendered_yet_starts_when_it_is():
    player, speaker, pb = FakePlayer(), RenderingSpeaker(), Playback()
    pb.start_rendered(player, speaker, ["one", "two"], ("prepare", (0,), None), now=0.0)
    assert pb.playing and pb.progress(0.5) == 0.0 and pb.poll(0.5)  # counts as playing: the focus is held
    assert ("play", 32000, 16000) not in player.calls
    speaker.futures["one two"].set_result(CLIP)
    assert pb.poll(0.8) and player.calls[-1] == ("play", 32000, 16000)
    assert pb.progress(1.8) == 0.5  # from when it started, not from the palm


def test_hear_it_says_the_words_live_if_rendering_failed():
    player, speaker, pb = FakePlayer(), RenderingSpeaker(), Playback()
    pb.start_rendered(player, speaker, ["one", "two"], ("prepare", (0,), None), now=0.0)
    speaker.futures["one two"].set_exception(OSError("say failed"))
    assert pb.poll(0.5) and speaker.calls == [("say", ["one", "two"])] and pb.current.kind == "say"


def test_hear_it_stopped_while_rendering_never_plays():
    player, speaker, pb = FakePlayer(), RenderingSpeaker(), Playback()
    pb.start_rendered(player, speaker, ["one", "two"], ("prepare", (0,), None), now=0.0)
    assert pb.stop() and not pb.playing
    speaker.futures["one two"].set_result(CLIP)
    assert not pb.poll(0.5) and ("play", 32000, 16000) not in player.calls


class FakeReader:
    def __init__(self):
        self.started, self.asked, self.closed = None, [], 0

    def start(self, t):
        self.started = t
        return self

    def frame_at(self, t):
        self.asked.append(t)
        return np.full((2, 2, 3), 7, np.uint8)

    def close(self):
        self.closed += 1


def test_a_clip_with_video_asks_for_the_moment_the_audio_has_reached():
    player, reader, pb = FakePlayer(), FakeReader(), Playback()
    pb.start_clip(player, CLIP, ("review", (1,), 2), now=10.0, video=reader, video_t0=55.25)
    assert reader.started == 55.25  # decoding from the clip's first moment
    assert pb.video_frame(10.5) is not None and reader.asked == [55.75]
    pb.video_frame(9.0), pb.video_frame(20.0)  # clamped to the clip
    assert reader.asked[1:] == [55.25, 57.25]
    assert not pb.poll(12.0 + END_SLACK_S) and reader.closed == 1  # ended by itself: closed
    assert pb.video_frame(12.5) is None


def test_stopping_a_clip_closes_its_video_and_audio_only_has_none():
    player, reader, pb = FakePlayer(), FakeReader(), Playback()
    pb.start_clip(player, CLIP, ("review", (1,), 2), now=0.0, video=reader, video_t0=1.0)
    assert pb.stop() and reader.closed == 1
    pb.start_clip(player, CLIP, ("review", (1,), 2), now=0.0)
    assert pb.video_frame(0.5) is None


def test_one_thing_plays_at_a_time():
    player, speaker, pb = FakePlayer(), FakeSpeaker(), Playback()
    pb.start_say(speaker, ["hello"], ("prepare", (0,), None), now=0.0)
    pb.start_clip(player, CLIP, ("review", (0,), 1), now=0.2)
    assert speaker.calls[-1] == ("stop",) and pb.target == ("review", (0,), 1)
    pb.start_say(speaker, ["hi"], ("prepare", (0,), None), now=0.3)
    assert player.calls[-1] == ("stop",)


def test_clip_player_stops_the_device_and_plays_again(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(play=lambda audio, rate: calls.append(("play", rate)),
                                 stop=lambda: calls.append(("stop",)))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    player = ClipPlayer()
    player.play(*CLIP)
    player.stop()
    player.play(*CLIP)
    assert calls == [("stop",), ("play", 16000), ("stop",), ("stop",), ("play", 16000)]


class SlowToDie:
    """A `say` process that takes a while to go after SIGTERM."""

    def __init__(self, argv, **kwargs):
        self.terminated = self.killed = False
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed, self.alive = True, False

    def wait(self, timeout=None):
        raise AssertionError("stop() must not wait for the process")


def test_say_stop_does_not_wait_and_kills_a_straggler(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", SlowToDie)
    clock = [100.0]
    monkeypatch.setattr(tts.time, "monotonic", lambda: clock[0])
    speaker = MacSay()
    speaker.say("a long sentence")
    proc = speaker._proc
    speaker.stop()  # returns at once
    assert proc.terminated and not proc.killed and not speaker.speaking
    clock[0] += tts.KILL_AFTER_S + 0.1
    assert not speaker.speaking and proc.killed  # reaped: killed, since it hadn't gone
    assert MacSay(rate_wpm=120).estimate_s(["w"] * 60) == 30.0 + tts.SAY_OVERHEAD_S


# --- what a replay's bar marks, and its captions ---------------------------------

def marked_take(tmp_path):
    from datetime import datetime

    from palmcards.session import Session

    (tmp_path / "n.md").write_text("Hello there friend. Good night all.\n")
    session = Session.create(tmp_path / "n.md", root=tmp_path / "s")
    take = session.add_take(np.zeros(16000 * 10, np.float32), 16000, 100.0, datetime.now(), [(0.0, 0)])
    words = [{"text": t, "start": a, "end": b, "probability": 0.9} for t, a, b in [
        ("Hello", 101.0, 101.3), ("um", 101.5, 101.7), ("there", 101.8, 102.0), ("friend.", 102.1, 102.5),
        ("Good", 104.0, 104.2), ("Good", 104.4, 104.6), ("night", 104.7, 105.0), ("all.", 105.1, 105.4)]]
    (session.dir / "take-01.transcript.json").write_text(__import__("json").dumps({"words": words}))
    alignment = {"sentences": [{"sentence": 0, "status": "spoken", "start": 101.0, "end": 102.5, "words": [0, 2, 3]},
                               {"sentence": 1, "status": "spoken", "start": 104.4, "end": 105.4, "words": [5, 6, 7]}],
                 "fillers": [{"word": 1, "text": "um", "t": 101.5}], "restarts": [{"sentence": 1, "words": [4]}],
                 "extras": [], "unsure": []}
    session.set_result(1, "take-01.transcript.json", alignment)
    return session, session.take(1)


def test_a_replays_bar_marks_fillers_restarts_and_long_pauses(tmp_path):
    from palmcards.playback import clip_marks

    session, take = marked_take(tmp_path)
    marks = clip_marks(session, take, (100.75, 105.65))
    assert ("filler", 101.5, 101.5) in marks and ("restart", 104.0, 104.0) in marks
    assert ("pause", 102.5, 104.0) in marks  # 1.5 s between "friend." and "Good"
    assert not any(k == "away" for k, _, _ in marks)  # no face features: nothing guessed
    assert clip_marks(session, take, (104.3, 105.65)) == []  # only what falls in the clip


def test_looking_away_is_marked_from_the_takes_calibration(tmp_path, monkeypatch):
    from palmcards import gaze, metrics
    from palmcards.playback import clip_marks

    session, take = marked_take(tmp_path)
    session.calibrations.append({"id": "c1", "status": "ok"})
    take.vision = {"state": "recorded", "calibration": "c1"}
    t = np.round(np.arange(101.0, 105.0, 0.1), 2)
    labels = np.array(["camera"] * len(t), dtype=object)
    labels[(t >= 102.0) & (t < 102.8)] = "away"  # 0.8 s away
    labels[(t >= 102.35) & (t < 102.45)] = "unclear"  # a blink inside it: bridged
    labels[(t >= 103.5) & (t < 103.8)] = "away"  # 0.3 s: too short to mark
    monkeypatch.setattr(metrics, "take_features", lambda vision, face: ({"face_t": t}, ""))
    monkeypatch.setattr(gaze, "usable", lambda calibration: "")
    monkeypatch.setattr(gaze, "classify", lambda arrays, calibration: labels)
    away = [m for m in clip_marks(session, take, (100.75, 105.65)) if m[0] == "away"]
    assert len(away) == 1 and away[0][1] == pytest.approx(102.0) and away[0][2] == pytest.approx(102.7)


def test_a_replays_captions_colour_the_words_as_the_player_does(tmp_path):
    from palmcards.playback import replay_words

    session, take = marked_take(tmp_path)
    words = replay_words(session, take, (100.75, 105.65))
    assert [(w[2], w[3]) for w in words] == [("Hello", "word"), ("um", "filler"), ("there", "word"),
                                             ("friend.", "word"), ("Good", "restart"), ("Good", "word"),
                                             ("night", "word"), ("all.", "word")]
