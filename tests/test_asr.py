import json
import time

import numpy as np
import pytest

from palmcards import asr
from palmcards.asr import Agreement, LiveWord, MlxWhisper, Transcription, get_recognizer
from palmcards.config import SPEECH
from palmcards.session import write_wav
from palmcards.speech import run_job


def reading(*words: tuple[str, float]) -> list[LiveWord]:
    """Words at the given start times, each 0.3 s long."""
    return [LiveWord(text, start, start + 0.3, 0.9) for text, start in words]


def test_agreement_confirms_what_two_readings_share():
    a = Agreement()
    assert a.update(reading(("we", 1.0), ("know", 1.4), ("whe", 1.8)), now=2.5) == []
    got = a.update(reading(("We", 1.02), ("know,", 1.41), ("where", 1.8), ("and", 2.3)), now=3.0)
    assert [w.text for w in got] == ["We", "know,"]  # the newer reading's copies
    assert all(w.confirmed_at == 3.0 for w in got)
    # "whe" became "where": agreed only once two readings say "where".
    got = a.update(reading(("we", 1.0), ("know", 1.4), ("where", 1.81), ("and", 2.3), ("we", 2.7)), now=3.5)
    assert [w.text for w in got] == ["where", "and"]
    assert [w.text for w in a.confirmed] == ["We", "know,", "where", "and"]


def test_agreement_needs_the_same_place_not_just_the_same_word():
    a = Agreement()
    a.update(reading(("the", 1.0)), now=2.0)
    assert a.update(reading(("the", 2.0)), now=2.5) == []


def test_agreement_ignores_a_confirmed_word_read_again_a_little_later():
    a = Agreement()
    a.update(reading(("hello", 1.0), ("there", 1.5)), now=2.0)
    a.update(reading(("hello", 1.0), ("there", 1.5)), now=2.5)
    assert a.after == pytest.approx(1.8)
    # "there" is read again starting at 1.75, inside the slack: not a new word.
    a.update(reading(("there", 1.75), ("friend", 2.0)), now=3.0)
    got = a.update(reading(("there", 1.75), ("friend", 2.0)), now=3.5)
    assert [w.text for w in got] == ["friend"]


def test_agreement_lets_a_repeat_through_once_it_is_later():
    a = Agreement()
    for now in (2.0, 2.5):
        a.update(reading(("again", 1.0)), now=now)
    got = []
    for now in (3.0, 3.5):
        got += a.update(reading(("again", 1.0), ("again", 1.6)), now=now)
    assert [(w.text, w.start) for w in got] == [("again", 1.6)]


def test_get_recognizer_follows_config():
    r = get_recognizer()
    assert SPEECH.backend == "mlx-whisper" and isinstance(r, MlxWhisper)
    assert r.model == SPEECH.model and r.live_model == SPEECH.live_model
    with pytest.raises(ValueError, match="unknown speech backend"):
        get_recognizer("nope")


class FakeRecognizer:
    model = "fake-model"

    def transcribe(self, wav, language):
        return Transcription("Hello there.", 0.5, [{"words": [
            {"word": " Hello", "start": 0.1, "end": 0.4, "probability": 0.91},
            {"word": " there.", "start": 0.5, "end": 0.9, "probability": 0.87}]}], self.model)


def test_run_job_writes_what_the_recognizer_heard(tmp_path, monkeypatch):
    monkeypatch.setitem(asr.BACKENDS, SPEECH.backend, FakeRecognizer)
    write_wav(tmp_path / "take-01.wav", np.full(16000, 0.1, np.float32), 16000)
    job = {
        "take": 1, "wav": str(tmp_path / "take-01.wav"), "transcript": str(tmp_path / "take-01.transcript.json"),
        "prosody": str(tmp_path / "take-01.prosody.npz"), "verdicts": str(tmp_path / "take-01.verdicts.json"),
        "t_start": 3.0, "language": "en", "silent": False, "sentences": [["hello", "there"]],
        "texts": ["Hello there."], "words": [["Hello", "there."]], "marks": [[]], "baseline_from": [],
        "realign": False,
    }
    result = run_job(job)
    data = json.loads((tmp_path / "take-01.transcript.json").read_text())
    assert data["model"] == "fake-model" and data["offset_s"] == 0.5 and data["text"] == "Hello there."
    # On the app clock: t_start + offset_s + the recogniser's time.
    assert data["words"] == [{"text": "Hello", "start": 3.6, "end": 3.9, "probability": 0.91},
                             {"text": "there.", "start": 4.0, "end": 4.4, "probability": 0.87}]
    assert result["alignment"]["sentences"][0]["status"] == "spoken"


def test_live_stream_reads_windows_and_confirms(monkeypatch):
    """The live stream's plumbing, with a scripted reader instead of Whisper."""
    said = [("one", 0.2), ("two", 0.6), ("three", 1.0)]

    def fake_read(audio, language, model):
        heard = len(audio) / SPEECH.rate  # seconds of audio in this window
        return [{"text": t, "start": s, "end": s + 0.3, "probability": 0.9} for t, s in said if s + 0.3 <= heard]

    monkeypatch.setattr(asr, "_read_window", fake_read)
    clock = [0.0]
    live = MlxWhisper().live("en", lambda: clock[0], where="thread")
    try:
        deadline = time.time() + 2
        while not live.ready and time.time() < deadline:
            live.poll()
            time.sleep(0.01)
        assert live.ready
        loud = np.full(int(SPEECH.live_step_s * SPEECH.rate), 0.1, np.float32)
        got = []
        for step in range(1, 7):  # 3 s of sound, fed step by step
            t = step * SPEECH.live_step_s
            clock[0] = t
            live.feed(loud, t_end=10.0 + t)  # the take started at app time 10
            deadline = time.time() + 2
            while live._busy and time.time() < deadline:
                time.sleep(0.005)
            got += live.poll()
        assert [w.text for w in got] == ["one", "two", "three"]
        assert got[0].start == pytest.approx(10.2) and got[0].confirmed_at is not None
        assert live.runs == 6 and live.skipped_silent == 0 and not live.errors
        live.feed(np.zeros(int(SPEECH.live_window_s * SPEECH.rate), np.float32), t_end=20.0)
        assert live.skipped_silent == 1
    finally:
        live.close()
