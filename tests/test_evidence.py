"""Audio the recording lost is placed on the app clock, the pitch cache knows
how it was made, and a take's metrics record what made them."""

import json
from dataclasses import replace
from datetime import datetime

import numpy as np
import pytest

from palmcards import prosody, speech
from palmcards.config import PROSODY
from palmcards.prosody import analyse
from palmcards.session import Session, write_wav
from tests.synth import RATE, voice


def test_capture_gaps_are_placed_on_the_app_clock(tmp_path):
    (tmp_path / "n.txt").write_text("Hello there friend.")
    session = Session.create(tmp_path / "n.txt", root=tmp_path / "s")
    take = session.add_take(np.zeros(16000, np.float32), 16000, 10.0, datetime.now(), [(0.0, 0)])
    take.capture = {"discontinuities": [{"at_s": 0.5, "samples": 1600, "why": "queue_full"},
                                        {"at_s": 0.8, "samples": None, "why": "input_overflow"}]}
    assert speech.capture_gaps(take) == [[10.5, 10.6], [10.8, 10.85]]


# --- the pitch cache -----------------------------------------------------------------

def take_files(tmp_path):
    audio = np.concatenate([voice(150, 150, 0.5), np.zeros(8000, np.float32)])
    wav = tmp_path / "take-01.wav"
    write_wav(wav, audio, RATE)
    return wav, tmp_path / "take-01.prosody.npz"


def counting(monkeypatch):
    calls = []
    real = prosody.analyse

    def analyse_(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(prosody, "analyse", analyse_)
    return calls


def test_the_cache_is_reused_only_when_it_was_made_the_same_way(tmp_path, monkeypatch):
    wav, cache = take_files(tmp_path)
    calls = counting(monkeypatch)
    p, prov = speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 1 and prov["status"] == "verified" and prov["wav_sha256"] and prov["frame_hop_s"] == 0.01
    speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 1  # same settings: the cache

    monkeypatch.setattr(prosody, "PROSODY", replace(PROSODY, voiced_floor_db=12.0))
    speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 1  # applied when read: no need to measure again

    monkeypatch.setattr(prosody, "PROSODY", replace(PROSODY, fmin_hz=80.0))
    _, prov = speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 2 and prov["fmin_hz"] == 80.0  # extraction changed: measured again

    write_wav(wav, np.zeros(RATE, np.float32), RATE)  # a different recording under the same name
    speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 3


def test_re_measuring_uses_a_stale_cache_and_says_so_with_its_own_hop(tmp_path, monkeypatch):
    wav, cache = take_files(tmp_path)
    speech.take_prosody(wav, 5.0, cache, False)
    calls = counting(monkeypatch)
    monkeypatch.setattr(prosody, "PROSODY", replace(PROSODY, hop_s=0.02))  # today's hop differs from the cache's
    p, prov = speech.take_prosody(wav, 5.0, cache, False, reuse_stale=True)
    assert calls == [] and prov["status"] == "stale" and "hop_s" in prov["changed"]
    assert p.hop == 0.01 and np.allclose(np.diff(p.t), 0.01)  # the cache's own timing


def test_an_old_cache_has_unknown_provenance_and_its_hop_is_read_off_its_frames(tmp_path):
    wav, cache = take_files(tmp_path)
    p = analyse(np.concatenate([voice(150, 150, 0.5)]), RATE, 5.0)
    np.savez_compressed(cache, t=p.t, f0=p.f0, rms_db=p.rms_db)  # as before provenance
    loaded, prov = speech.take_prosody(wav, 5.0, cache, False, reuse_stale=True)
    assert prov["status"] == "unknown" and loaded.hop == pytest.approx(0.01)


def test_metrics_record_what_made_them(tmp_path):
    wav, cache = take_files(tmp_path)
    job = {"take": 1, "job": "j1", "revision": "r1", "config": "c1", "wav": str(wav),
           "transcript": str(tmp_path / "take-01.transcript.json"), "prosody": str(cache),
           "t_start": 5.0, "language": "en", "silent": True,
           "sentences": [["hello"]], "texts": ["Hello."], "words": [["Hello."]],
           "gaps": [[5.1, 5.2]], "realign": False}
    result = speech.run_job(job)
    prov = result["metrics"]["provenance"]
    assert (prov["notes_revision"], prov["analysis_config"], prov["gaps"]) == ("r1", "c1", [[5.1, 5.2]])
    assert prov["align"]["version"] >= 1 and prov["prosody"]["status"] == "verified"
    assert result["metrics"]["version"] >= 4 and not (tmp_path / "take-01.verdicts.json").exists()
    transcript = json.loads((tmp_path / "take-01.transcript.json").read_text())
    assert transcript["asr"]["model"] and "revision" in transcript["asr"]
    assert result["ok"] and result["revision"] == "r1"
