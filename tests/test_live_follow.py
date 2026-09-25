"""The voice follow: live stream states (review finding F8), LiveFollow's
failure containment, and the app moving the display with the voice while
recording sections with their source."""

import importlib.util
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import main
from palmcards import asr
from palmcards.asr import LiveWord, MlxWhisper
from palmcards.config import SPEECH
from palmcards.follow import LiveFollow
from palmcards.notes import parse_text
from tests.test_app_lifecycle import TAKE, FakeLive, Rig

ROOT = Path(__file__).resolve().parent.parent


def wait(live, until, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        live.poll()
        if until():
            return
        time.sleep(0.01)
    raise AssertionError(f"state {live.state}, errors {live.errors}")


# --- live stream states ---------------------------------------------------------------

def test_a_model_that_fails_to_load_is_failed_not_ready(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(asr, "_read_window", boom)
    live = MlxWhisper().live("en", time.perf_counter, where="thread")
    wait(live, lambda: live.state != "starting")
    assert live.state == "failed" and not live.ready and "model unavailable" in live.errors[0]  # was ready, no error
    live.close()


def test_a_reader_that_never_answers_fails_on_the_deadline(monkeypatch):
    monkeypatch.setattr(asr, "_read_window", lambda *a, **k: time.sleep(10))
    monkeypatch.setattr(asr, "SPEECH", replace(SPEECH, live_start_timeout_s=0.3))
    live = MlxWhisper().live("en", time.perf_counter, where="thread")
    wait(live, lambda: live.state == "failed")
    assert "did not load within" in live.errors[0]
    live.state = "closed"  # the reader is stuck in its sleep; don't wait for it


def test_a_reader_that_dies_fails_the_stream(monkeypatch):
    monkeypatch.setattr(asr, "_serve_windows", lambda inbox, outbox, model, language: None)  # exits at once
    live = MlxWhisper().live("en", time.perf_counter, where="thread")
    wait(live, lambda: live.state == "failed")
    assert "reader stopped" in live.errors[0]


def test_reset_drops_answers_to_windows_from_before(monkeypatch):
    replies = []

    def slow_read(audio, language, model, prompt=None):
        time.sleep(0.2)
        replies.append(len(audio))
        return [{"text": "old", "start": 0.1, "end": 0.3, "probability": 0.9}]

    monkeypatch.setattr(asr, "_read_window", slow_read)
    live = MlxWhisper().live("en", time.perf_counter, where="thread")
    wait(live, lambda: live.ready)
    live.feed(np.full(int(SPEECH.live_step_s * SPEECH.rate), 0.1, np.float32), 1.0)
    live.reset()  # a new take while that window is being read
    time.sleep(0.4)
    assert live.poll() == [] and live.runs == 1 and len(replies) == 2
    live.close()


# --- LiveFollow ---------------------------------------------------------------------------

NOTES = parse_text("# One\n\nHello there friend. Good night all.\n\n# Two\n\nSee you soon.\n", "md")


class ScriptedLive(FakeLive):
    """Confirms the given words, a few per poll."""

    def __init__(self, words=(), fail_after=None):
        self.words, self.n, self.fail_after, self.errors, self.fed = list(words), 0, fail_after, [], 0

    def feed(self, samples, t_end):
        self.fed += len(samples)

    def poll(self):
        self.n += 1
        if self.fail_after is not None and self.n > self.fail_after:
            self.state = "failed"
            self.errors.append("reader stopped")
            return []
        out, self.words = self.words[:3], self.words[3:]
        return out


def spoken(text, t0=1.0):
    return [LiveWord(w, t0 + 0.4 * i, t0 + 0.4 * i + 0.3, 0.9, confirmed_at=t0 + 0.4 * i + 1.0)
            for i, w in enumerate(text.split())]


def test_live_follow_turns_words_into_moves_and_keeps_stats():
    follow = LiveFollow(NOTES, lambda: ScriptedLive(spoken("hello there friend good night all see you soon")),
                        48000, "mlx-whisper", "base")
    follow.start_take(0)
    follow.tap(np.full(4800, 0.1, np.float32), 1.0)
    deadline = time.time() + 5
    while follow.stream.fed == 0 and time.time() < deadline:  # the feeder hands it on
        time.sleep(0.02)
    assert follow.stream.fed == 1600  # 4800 samples at 48 kHz -> 16 kHz
    moves = [e for _ in range(4) for e in follow.poll()]
    assert [(e.kind, e.index) for e in moves] == [("sentence", 1), ("section", 1), ("sentence", 2)]
    stats = follow.stop_take()
    assert (stats["state"], stats["words"], stats["engine"]) == ("following", 9, "mlx-whisper")
    assert stats["lag_median_s"] == pytest.approx(0.7)


def test_a_failing_live_stream_only_turns_the_follow_off():
    follow = LiveFollow(NOTES, lambda: ScriptedLive(spoken("hello there"), fail_after=1), 48000)
    follow.start_take(0)
    follow.poll()
    assert follow.poll() == [] and follow.state == "failed" and follow.error == "reader stopped"
    follow.tap(np.zeros(10, np.float32), 1.0)  # the callback's side still never fails
    assert follow.stop_take()["error"] == "reader stopped"

    def cannot():
        raise RuntimeError("no Speech Recognition permission")

    broken = LiveFollow(NOTES, cannot, 48000)
    broken.start_take(0)
    assert broken.state == "failed" and "permission" in broken.error and broken.poll() == []


# --- in the app ----------------------------------------------------------------------------

def test_the_voice_moves_the_section_and_it_is_recorded_as_such(tmp_path, monkeypatch):
    words = spoken("hello there friend good night all see you soon")
    rig = Rig(tmp_path, monkeypatch, keys={60: ord("q")}, live=lambda language, clock, hints: ScriptedLive(words))
    assert rig.run() == 0
    take = rig.session().take(1)
    assert [(s["section"], s["source"]) for s in take.sections] == [(0, "start"), (1, "voice")]
    assert take.live["state"] == "following" and take.live["words"] == 9
    rig.assert_closed_once()


def test_a_flick_and_the_previous_key_are_recorded_as_such(tmp_path, monkeypatch):
    script = {**TAKE, 10: ("next_section", "rehearse")}
    rig = Rig(tmp_path, monkeypatch, script=script, keys={20: ord("b"), 40: ord("q")})
    monkeypatch.setattr(main, "ModeMachine", _with_real_commands(main.ModeMachine))
    assert rig.run() == 0
    assert [(s["section"], s["source"]) for s in rig.session().take(1).sections] == \
        [(0, "start"), (1, "flick"), (0, "key")]


def _with_real_commands(cls):
    """The scripted machine, with the real keyboard commands (for "b")."""
    from palmcards.gestures import ModeMachine

    class Machine(cls):
        def command(self, name, t):
            return ModeMachine.command(self, name, t)

    return Machine


def test_a_broken_live_stream_leaves_recording_alone(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, keys={40: ord("q")},
              live=lambda language, clock, hints: ScriptedLive(spoken("hello"), fail_after=2))
    assert rig.run() == 0
    take = rig.session().take(1)
    assert take.status == "saved" and take.duration_s > 0.1
    assert take.live["state"] == "failed" and take.live["error"] == "reader stopped"


def test_a_drill_rehearses_the_sentence_chosen_when_it_was_decided(tmp_path, monkeypatch):
    script = {3: ("drill", "count_in"), 4: ("take_start", "rehearse")}
    rig = Rig(tmp_path, monkeypatch, script=script, keys={1: ord("j"), 30: ord("q")})
    assert rig.run() == 0
    take = rig.session().take(1)
    assert take.drill == 1 and take.live is None  # the follow stays off in a drill


# --- the benchmark ---------------------------------------------------------------------------

def bench():
    spec = importlib.util.spec_from_file_location("bench_live", ROOT / "scripts" / "bench_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_benchmark_reports_a_stream_that_never_starts():
    b = bench()
    replay = object.__new__(b.Replay)
    replay.stream = ScriptedLive(fail_after=0)
    replay.stream.state = "starting"
    type(replay.stream).ready = property(lambda self: self.state == "ready")
    with pytest.raises(RuntimeError, match="reader stopped"):
        replay.wait_ready(timeout=1)


def test_benchmark_tracking_ignores_silence_between_sentences():
    b = bench()
    notes = parse_text("One two three four. Five six seven eight.")
    post = [{"text": w, "start": t, "end": t + 0.3, "probability": 0.9}
            for w, t in zip("one two three four five six seven eight".split(), [1, 1.4, 1.8, 2.2, 20, 20.4, 20.8, 21.2])]
    live = [LiveWord(w["text"], w["start"], w["end"], 0.9, confirmed_at=w["end"] + 0.5) for w in post]
    score = b.score(notes, post, live, 0.0)
    # 17 s of silence between the sentences no longer counts as being behind.
    assert score["sentences"]["on"] > 0.6
