"""What plays (a take's clip, "hear it"): progress, the end, stopping at once
and playing again right after."""

import subprocess
import sys
import types
from concurrent.futures import Future

import numpy as np

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
