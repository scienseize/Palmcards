import json

import numpy as np

from datetime import datetime

from palmcards.config import SPEECH
from palmcards.notes import parse_text
from palmcards.session import Session, write_wav
from palmcards.speech import (
    baseline_wpm, initial_prompt, make_job, prepare_audio, resample, run_job, trim_silence, words_on_clock,
)


def tone(seconds: float, rate: int, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_resample_length():
    audio = tone(2.0, 48000)
    out = resample(audio, 48000)
    assert out.dtype == np.float32 and len(out) == 32000
    assert len(resample(out, 16000)) == 32000  # already 16 kHz


def test_trim_silence():
    rate = 16000
    audio = np.concatenate([np.zeros(rate * 2, np.float32), tone(1.0, rate), np.zeros(rate * 3, np.float32)])
    audio += np.random.default_rng(0).normal(0, 1e-4, len(audio)).astype(np.float32)  # room noise, ~-80 dBFS
    start, end = trim_silence(audio, rate)
    pad = SPEECH.trim_pad_s
    assert abs(start / rate - (2.0 - pad)) < 0.03
    assert abs(end / rate - (3.0 + pad)) < 0.03


def test_trim_silence_all_quiet():
    assert trim_silence(np.zeros(16000, np.float32), 16000) == (0, 0)
    assert trim_silence(np.zeros(10, np.float32), 16000) == (0, 0)


def test_prepare_audio_offset(tmp_path):
    rate = 48000
    audio = np.concatenate([np.zeros(rate, np.float32), tone(0.5, rate), np.zeros(rate, np.float32)])
    write_wav(tmp_path / "take-01.wav", audio, rate)
    out, offset = prepare_audio(tmp_path / "take-01.wav")
    assert abs(offset - (1.0 - SPEECH.trim_pad_s)) < 0.03
    assert abs(len(out) / SPEECH.rate - (0.5 + 2 * SPEECH.trim_pad_s)) < 0.05


def test_words_on_clock():
    result = {"segments": [
        {"words": [{"word": " Good", "start": 0.5, "end": 0.8, "probability": 0.91234},
                   {"word": " evening,", "start": 0.8, "end": 1.2, "probability": 0.8}]},
        {"words": [{"word": " ", "start": 1.2, "end": 1.3, "probability": 0.1},
                   {"word": " um", "start": 2.0, "end": 2.2, "probability": 0.5}]},
    ]}
    words = words_on_clock(result, t_start=48.787, offset_s=1.25)
    assert [w["text"] for w in words] == ["Good", "evening,", "um"]
    assert words[0] == {"text": "Good", "start": 50.537, "end": 50.837, "probability": 0.912}
    assert words[2]["start"] == round(48.787 + 1.25 + 2.0, 3)


def test_initial_prompt_has_fillers():
    assert "um" in initial_prompt("en").lower() and "uh" in initial_prompt("en").lower()
    assert initial_prompt("xx") is None


def job_for(tmp_path, **kw) -> dict:
    job = {"take": 1, "wav": str(tmp_path / "take-01.wav"), "transcript": str(tmp_path / "take-01.transcript.json"),
           "prosody": str(tmp_path / "take-01.prosody.npz"), "verdicts": str(tmp_path / "take-01.verdicts.json"),
           "t_start": 3.0, "language": "en", "silent": False, "sentences": [["hello", "there"]],
           "texts": ["Hello there /."], "words": [["Hello", "there"]], "marks": [[["short_pause", 2]]],
           "baseline_from": [], "realign": False}
    return job | kw


def test_silent_take_skips_whisper(tmp_path):
    result = run_job(job_for(tmp_path, silent=True))
    assert result["ok"] and result["transcript"] == "take-01.transcript.json"
    assert result["alignment"]["sentences"][0]["status"] == "skipped"
    data = json.loads((tmp_path / "take-01.transcript.json").read_text())
    assert data["words"] == [] and data["t_start"] == 3.0
    # Judged too: the only mark is in a sentence that wasn't said.
    assert result["verdicts"] == "take-01.verdicts.json" and result["marks"]["skipped"] == 1
    saved = json.loads((tmp_path / "take-01.verdicts.json").read_text())
    assert saved["take"] == 1 and saved["sentences"][0]["marks"][0]["verdict"] == "skipped"
    assert (tmp_path / "take-01.prosody.npz").exists()


def test_realign_uses_saved_transcript(tmp_path):
    path = tmp_path / "take-01.transcript.json"
    path.write_text(json.dumps({"words": [
        {"text": "Hello", "start": 4.0, "end": 4.3, "probability": 0.9},
        {"text": "there.", "start": 4.4, "end": 4.7, "probability": 0.9}]}))
    write_wav(tmp_path / "take-01.wav", np.zeros(16000 * 2, np.float32), 16000)
    result = run_job(job_for(tmp_path, realign=True, marks=[[]]))
    s = result["alignment"]["sentences"][0]
    assert (s["status"], s["start"], s["end"]) == ("spoken", 4.0, 4.7)
    assert "0:01.0-0:01.7" in result["report"]
    assert "no delivery marks" in result["summary"]


def test_drill_job_matches_only_its_sentence_and_borrows_the_pace(tmp_path):
    text = "Hello there my friend. [slow] Good evening to you all."
    notes = parse_text(text)
    (tmp_path / "notes.txt").write_text(text)
    session = Session.create(tmp_path / "notes.txt", root=tmp_path / "sessions")
    full = session.add_take(np.zeros(800, np.float32), 8000, 1.0, datetime.now(), [(0.0, 0)])
    drill = session.add_take(np.zeros(800, np.float32), 8000, 5.0, datetime.now(), [(0.0, 0)], drill=1)
    job = make_job(session, drill, notes)
    assert job["sentences"] == [[], ["good", "evening", "to", "you", "all"]]
    assert job["marks"] == [[], [["slow", None]]]
    assert job["baseline_from"] == [str(session.dir / "take-01.verdicts.json")]
    assert make_job(session, full, notes)["baseline_from"] == []
    # Take 1 is judged after the drill was submitted, before the worker reaches it.
    assert baseline_wpm(job["baseline_from"]) is None
    (session.dir / full.verdicts_name).write_text(json.dumps({"take_wpm": 150.0}))
    assert baseline_wpm(job["baseline_from"]) == 150.0
