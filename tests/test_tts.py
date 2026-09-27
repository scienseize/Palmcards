import subprocess

import pytest

from palmcards import tts
from palmcards.tts import MacSay, get_speaker


class FakeProc:
    def __init__(self, argv, **kwargs):
        self.argv, self.terminated = argv, False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_say_argv():
    assert MacSay().argv("hello") == ["say", "--", "hello"]
    assert MacSay(voice="Samantha", rate_wpm=180).argv("-dash first") == \
        ["say", "-v", "Samantha", "-r", "180", "--", "-dash first"]
    assert MacSay(voice="Samantha").argv("hi", voice="Daniel", rate_wpm=150.4) == \
        ["say", "-v", "Daniel", "-r", "150", "--", "hi"]


def test_say_cuts_off_the_last_utterance(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    speaker = MacSay()
    speaker.say("first")
    first = speaker._proc
    assert speaker.speaking and first.argv[-1] == "first"
    speaker.say("second")
    assert first.terminated and speaker._proc.argv[-1] == "second"
    speaker.stop()
    assert not speaker.speaking


def test_get_speaker_follows_config():
    assert isinstance(get_speaker(), MacSay)
    with pytest.raises(ValueError, match="unknown voice backend"):
        get_speaker("nope")
    assert "say" in tts.BACKENDS


def test_say_words_stresses_with_say_markup(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    speaker = MacSay()
    speaker.say_words(["Thank", "you", "for", "being", "here."], {3})
    assert speaker._proc.argv[-1] == "Thank you for [[emph +]] being here."


def fake_say_to_file(calls, seconds=0.5):
    """subprocess.run standing in for `say -o FILE`: writes that many seconds of 16-bit WAV."""
    import wave

    import numpy as np

    def run(argv, **kwargs):
        calls.append(argv)
        rate = int(argv[argv.index("-o") - 1].rpartition("@")[2])
        with wave.open(argv[argv.index("-o") + 1], "wb") as f:
            f.setnchannels(1), f.setsampwidth(2), f.setframerate(rate)
            f.writeframes(np.zeros(int(seconds * rate), "<i2").tobytes())
    return run


def test_render_says_to_a_wav_and_reads_it_back(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_say_to_file(calls))
    audio, rate = MacSay(voice="Samantha").render("-dash first")
    assert rate == tts.RENDER_RATE and len(audio) == rate // 2 and audio.dtype.name == "float32"
    argv = calls[0]
    assert argv[:3] == ["say", "-v", "Samantha"] and argv[-2:] == ["--", "-dash first"]
    assert f"--data-format=LEI16@{tts.RENDER_RATE}" in argv


def test_render_async_keeps_what_it_made_and_makes_a_failed_one_again(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_say_to_file(calls))
    speaker = MacSay()
    first = speaker.render_async(["Good", "evening."])
    assert first.result(timeout=5)[1] == tts.RENDER_RATE
    assert speaker.render_async(["Good", "evening."]) is first and len(calls) == 1  # kept

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(subprocess, "run", fail)
    broken = speaker.render_async(["Thank", "you."])
    with pytest.raises(subprocess.CalledProcessError):
        broken.result(timeout=5)
    monkeypatch.setattr(subprocess, "run", fake_say_to_file(calls))
    again = speaker.render_async(["Thank", "you."])
    assert again is not broken and again.result(timeout=5)[1] == tts.RENDER_RATE
    speaker.close()


def test_the_live_estimate_counts_says_start_up():
    assert MacSay(rate_wpm=120).estimate_s(["one", "two"]) == pytest.approx(tts.SAY_OVERHEAD_S + 1.0)
