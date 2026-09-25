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
