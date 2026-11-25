"""The pure parts of palmcards/audio.py: resampling, trimming and loudness."""

import numpy as np

from palmcards.audio import prepare_audio, resample, rms_db, trim_silence
from palmcards.config import SPEECH
from palmcards.session import write_wav

RATE = SPEECH.rate


def tone(seconds, rate=RATE, amp=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_resample_keeps_the_length_in_seconds_and_returns_float32():
    out = resample(tone(1.0, 48000), 48000)
    assert out.dtype == np.float32 and len(out) == RATE
    assert resample(tone(0.5), RATE).dtype == np.float32


def test_trim_silence_keeps_the_sound_with_a_pad_and_finds_none_in_silence():
    audio = np.concatenate([np.zeros(RATE, np.float32), tone(1.0), np.zeros(RATE, np.float32)])
    start, end = trim_silence(audio, RATE)
    pad, frame = int(RATE * SPEECH.trim_pad_s), RATE * SPEECH.trim_frame_s
    assert abs(start - (RATE - pad)) <= frame
    assert abs(end - (2 * RATE + pad)) <= frame
    assert trim_silence(np.zeros(RATE, np.float32), RATE) == (0, 0)
    assert trim_silence(np.zeros(3, np.float32), RATE) == (0, 0)  # shorter than one frame


def test_prepare_audio_reports_the_seconds_cut_from_the_front(tmp_path):
    audio = np.concatenate([np.zeros(2 * RATE, np.float32), tone(1.0), np.zeros(2 * RATE, np.float32)])
    write_wav(tmp_path / "t.wav", audio, RATE)
    out, cut = prepare_audio(tmp_path / "t.wav")
    assert abs(cut - (2.0 - SPEECH.trim_pad_s)) < 0.05
    assert 1.0 < len(out) / RATE < 1.0 + 2 * SPEECH.trim_pad_s + 0.1


def test_rms_db_of_a_full_scale_sine_and_of_nothing():
    assert abs(rms_db(tone(1.0, amp=1.0)) - (-3.01)) < 0.05
    assert rms_db(np.zeros(0, np.float32)) == -120.0
