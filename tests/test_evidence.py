"""Each mark is judged only on evidence it has (review finding F4), audio the
recording lost makes a mark unclear, uncalibrated languages are not judged
by English standards, and the pitch cache knows how it was made."""

import json
from dataclasses import replace
from datetime import datetime

import numpy as np
import pytest

from palmcards import prosody, speech
from palmcards.config import CUES
from palmcards.prosody import analyse
from palmcards.session import Session, write_wav
from tests.test_cues import RATE, judge, only, speak, utter, voice

TRUNCATED = "We need to act before the opportunity disappears. [rise]"


def glide(names, f_start, f_end, last_s=0.5, probs=None):
    """Words said evenly, pitch level until the last word, which glides."""
    parts = [(w, voice(160, 160, 0.22)) for w in names[:-1]] + [(names[-1], voice(f_start, f_end, last_s))]
    audio, words = utter(parts)
    for w, p in zip(words, probs or []):
        w["probability"] = p
    return words, analyse(audio, RATE, 5.0)


def test_a_rise_on_a_sentence_cut_short_is_unclear_not_hit():
    # The review's reproduction: only "we need to act", pitch rising over "act".
    words, p = glide("we need to act".split(), 150, 230)
    v = only(judge(TRUNCATED, words, p))
    assert v["kind"] == "rise" and v["verdict"] == "unclear" and v["reason"] == "the end of the sentence wasn't heard"


def test_a_fall_on_a_sentence_cut_short_is_unclear_too():
    words, p = glide("That is all".split(), 230, 150)
    v = only(judge("That is all we have. [fall]", words, p))
    assert v["verdict"] == "unclear"


def test_a_sentence_missing_its_middle_is_still_judged_on_its_ending():
    names = "We need to act the opportunity disappears".split()  # "before" skipped
    words, p = glide(names, 150, 230)
    assert only(judge(TRUNCATED, words, p))["verdict"] == "hit"


def test_a_misheard_last_word_still_counts_as_the_ending():
    names = "We need to act before the opportunity disappear".split()  # approximate match
    words, p = glide(names, 150, 230)
    assert only(judge(TRUNCATED, words, p))["verdict"] == "hit"


def test_a_last_word_split_in_two_still_counts_as_the_ending():
    names = "We need to act before the opportunity dis appears".split()
    parts = [(w, voice(160, 160, 0.22)) for w in names[:-2]]
    parts += [("dis", voice(150, 180, 0.25)), ("appears", voice(180, 230, 0.3))]
    audio, words = utter(parts)
    v = only(judge(TRUNCATED, words, analyse(audio, RATE, 5.0)))
    assert v["verdict"] == "hit"


def test_a_last_word_heard_unsurely_leaves_the_ending_unclear():
    names = "We need to act before the opportunity disappears".split()
    words, p = glide(names, 150, 230, probs=[0.9] * 7 + [0.2])  # noisy tail
    v = only(judge(TRUNCATED, words, p))
    assert v["verdict"] == "unclear" and "heard unclearly" in v["reason"]


# --- audio the recording lost ------------------------------------------------------

def test_lost_audio_inside_a_pause_makes_it_unclear():
    text = "Good evening everyone. / Thank you for being here."
    words = speak("Good evening everyone. <0.5> Thank you for being here.")
    gap = (words[2]["end"] + 0.1, words[2]["end"] + 0.2)
    v = only(judge(text, words, gaps=[gap]))
    assert v["verdict"] == "unclear" and "lost 0.10 s" in v["reason"]
    assert only(judge(text, words, gaps=[(0.0, 1.0)]))["verdict"] == "hit"  # elsewhere: no matter


def test_lost_audio_inside_a_stressed_word_or_an_ending_makes_it_unclear():
    parts = [(w, voice(150, 150, 0.25)) for w in ("Thank", "you", "for")]
    parts += [("being", voice(150, 150, 0.3, amp=0.6))] + [(w, voice(150, 150, 0.25)) for w in ("here", "tonight.")]
    audio, words = utter(parts)
    p = analyse(audio, RATE, 5.0)
    stress = only(judge("Thank you for *being* here tonight.", words, p, gaps=[(words[3]["start"], words[3]["start"] + 0.05)]))
    assert stress["verdict"] == "unclear" and "lost" in stress["reason"]
    words, p = glide("Are you coming with us".split(), 150, 230)
    end = only(judge("Are you coming with us? [rise]", words, p, gaps=[(words[-1]["end"] - 0.1, words[-1]["end"])]))
    assert end["verdict"] == "unclear" and "lost" in end["reason"]


def test_capture_gaps_are_placed_on_the_app_clock(tmp_path):
    (tmp_path / "n.txt").write_text("Hello there friend.")
    session = Session.create(tmp_path / "n.txt", root=tmp_path / "s")
    take = session.add_take(np.zeros(16000, np.float32), 16000, 10.0, datetime.now(), [(0.0, 0)])
    take.capture = {"discontinuities": [{"at_s": 0.5, "samples": 1600, "why": "queue_full"},
                                        {"at_s": 0.8, "samples": None, "why": "input_overflow"}]}
    assert speech.capture_gaps(take) == [[10.5, 10.6], [10.8, 10.85]]


# --- languages ---------------------------------------------------------------------

def test_stress_and_endings_are_not_judged_by_english_standards_elsewhere():
    words, p = glide("Are you coming with us".split(), 150, 230)
    v = judge("Are you coming with us? [rise]", words, p, language="de", calibrated=False)
    mark = only(v)
    assert mark["verdict"] == "unclear" and "calibrated for English" in mark["reason"] and mark["value"] > 3
    assert v["language"] == {"code": "de", "calibrated": False}
    pause = only(judge("Good evening everyone. / Thank you for being here.",
                       speak("Good evening everyone. <0.4> Thank you for being here."), calibrated=False))
    assert pause["verdict"] == "hit"  # pauses compare the speaker with themselves


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

    monkeypatch.setattr(prosody, "CUES", replace(CUES, stress_loud_db=9.0, ending_slope_st_s=1.0))
    speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 1  # thresholds only: no need to measure again

    monkeypatch.setattr(prosody, "CUES", replace(CUES, fmin_hz=80.0))
    _, prov = speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 2 and prov["fmin_hz"] == 80.0  # extraction changed: measured again

    write_wav(wav, np.zeros(RATE, np.float32), RATE)  # a different recording under the same name
    speech.take_prosody(wav, 5.0, cache, False)
    assert len(calls) == 3


def test_re_judging_uses_a_stale_cache_and_says_so_with_its_own_hop(tmp_path, monkeypatch):
    wav, cache = take_files(tmp_path)
    speech.take_prosody(wav, 5.0, cache, False)
    calls = counting(monkeypatch)
    monkeypatch.setattr(prosody, "CUES", replace(CUES, hop_s=0.02))  # today's hop differs from the cache's
    p, prov = speech.take_prosody(wav, 5.0, cache, False, reuse_stale=True)
    assert calls == [] and prov["status"] == "stale" and "hop_s" in prov["changed"]
    assert p.hop == 0.01 and np.allclose(np.diff(p.t), 0.01)  # the cache's own timing


def test_an_old_cache_has_unknown_provenance_and_its_hop_is_read_off_its_frames(tmp_path):
    wav, cache = take_files(tmp_path)
    p = analyse(np.concatenate([voice(150, 150, 0.5)]), RATE, 5.0)
    np.savez_compressed(cache, t=p.t, f0=p.f0, rms_db=p.rms_db)  # as before provenance
    loaded, prov = speech.take_prosody(wav, 5.0, cache, False, reuse_stale=True)
    assert prov["status"] == "unknown" and loaded.hop == pytest.approx(0.01)


def test_verdicts_record_what_made_them(tmp_path):
    wav, cache = take_files(tmp_path)
    job = {"take": 1, "job": "j1", "revision": "r1", "config": "c1", "wav": str(wav),
           "transcript": str(tmp_path / "take-01.transcript.json"), "prosody": str(cache),
           "verdicts": str(tmp_path / "take-01.verdicts.json"), "t_start": 5.0, "language": "en", "silent": True,
           "sentences": [["hello"]], "texts": ["Hello."], "words": [["Hello."]], "marks": [[]],
           "gaps": [[5.1, 5.2]], "realign": False}
    result = speech.run_job(job)
    saved = json.loads((tmp_path / "take-01.verdicts.json").read_text())
    prov = saved["provenance"]
    assert (prov["notes_revision"], prov["analysis_config"], prov["gaps"]) == ("r1", "c1", [[5.1, 5.2]])
    assert prov["align"]["version"] >= 1 and prov["scoring"]["version"] >= 2 and prov["prosody"]["status"] == "verified"
    assert saved["version"] >= 2 and saved["language"] == {"code": "en", "calibrated": True}
    transcript = json.loads((tmp_path / "take-01.transcript.json").read_text())
    assert transcript["asr"]["model"] and "revision" in transcript["asr"]
    assert result["ok"] and result["revision"] == "r1"
