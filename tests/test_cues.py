"""Delivery-mark verdicts: pauses and pace from made-up word timestamps,
stress and ending intonation from synthetic audio."""

import re

import numpy as np
import pytest

from palmcards.align import align
from palmcards.config import CUES
from palmcards.cues import mark_label, summary, verdicts
from palmcards.notes import parse_text
from palmcards.prosody import analyse

RATE = 16000


def speak(script: str, word_s: float = 0.25, gap_s: float = 0.05, t0: float = 10.0) -> list[dict]:
    """Transcript words from a script: words said back to back, "<0.4>" makes
    the gap before the next word 0.4 s, "word@0.02" gives it that probability."""
    words, t, gap = [], t0, 0.0
    for tok in script.split():
        if m := re.fullmatch(r"<([\d.]+)>", tok):
            gap = float(m.group(1))
            continue
        text, _, prob = tok.partition("@")
        t += gap
        words.append({"text": text, "start": round(t, 3), "end": round(t + word_s, 3),
                      "probability": float(prob) if prob else 0.9})
        t += word_s
        gap = gap_s
    return words


def judge(text: str, words: list[dict], prosody=None, **kw) -> dict:
    notes = parse_text(text)
    alignment = align([[w.norm for w in s.words] for s in notes.sentences], words)
    marks = [[[m.kind, m.word] for m in s.marks] for s in notes.sentences]
    return verdicts(marks, alignment, words, prosody, **kw)


def only(v: dict, sentence: int = None) -> dict:
    """The one mark of the take (or of one sentence)."""
    marks = [m for s in v["sentences"] if sentence is None or s["sentence"] == sentence for m in s["marks"]]
    assert len(marks) == 1, marks
    return marks[0]


# --- pauses ----------------------------------------------------------------------------

NOTES = "Good evening everyone. / Thank you for being here."


def test_short_pause_hit_and_missed():
    hit = only(judge(NOTES, speak("Good evening everyone. <0.4> Thank you for being here.")))
    assert (hit["kind"], hit["verdict"], hit["value"], hit["threshold"]) == ("short_pause", "hit", 0.4, 0.3)
    assert hit["reason"] == "0.40 s pause"
    miss = only(judge(NOTES, speak("Good evening everyone. <0.1> Thank you for being here.")))
    assert miss["verdict"] == "missed" and miss["reason"].startswith("no pause, 0.10 s")


def test_long_pause_needs_the_long_gap():
    text = "We started with one question: // what if practice felt easy?"
    v = only(judge(text, speak("We started with one question <0.5> what if practice felt easy")))
    assert v["verdict"] == "missed" and v["reason"].startswith("only a short pause")
    v = only(judge(text, speak("We started with one question <0.8> what if practice felt easy")))
    assert v["verdict"] == "hit" and v["value"] == 0.8


def test_a_filler_in_the_pause_is_a_miss():
    v = only(judge(NOTES, speak("Good evening everyone. <0.3> um <0.3> Thank you for being here.")))
    assert v["verdict"] == "missed" and v["reason"] == 'filled with "um"'


def test_unsure_words_in_the_silence_are_ignored():
    # Whisper hallucinates into silences; a word it was unsure of is noise.
    v = only(judge(NOTES, speak("Good evening everyone. <0.3> Thanks@0.02 <0.3> Thank you for being here.")))
    assert v["verdict"] == "hit" and v["value"] == pytest.approx(0.85)  # the whole silence


def test_pause_is_measured_on_the_final_attempt_of_a_restart():
    text = "We know / where we are going."
    # First attempt runs on without the pause; the final one makes it.
    words = speak("We know where <0.3> we know <0.5> where we are going.")
    v = only(judge(text, words))
    assert v["verdict"] == "hit" and v["value"] == 0.5


def test_pause_in_a_skipped_sentence_is_skipped_not_missed():
    text = "First we say this. Then we say / that other thing here."
    v = only(judge(text, speak("First we say this.")))
    assert v["verdict"] == "skipped"


def test_pause_next_to_an_unheard_word_is_unclear():
    text = "One two three four five / six seven eight nine ten."
    v = only(judge(text, speak("One two three four <0.6> six seven eight nine ten.")))
    assert v["verdict"] == "unclear" and v["reason"] == "the word before it wasn't heard"
    # A pause at the end of a sentence is judged against the next one's first word.
    text = "One two three four five /. Six seven eight. Nine ten eleven twelve."
    v = only(judge(text, speak("One two three four five. <1.0> Nine ten eleven twelve.")))
    assert v["verdict"] == "unclear" and v["reason"] == "the word after it wasn't heard"


def test_pause_at_the_very_start_or_end_is_unclear():
    assert only(judge("/ Hello there my friends.", speak("Hello there my friends.")))["verdict"] == "unclear"
    assert only(judge("Hello there my friends //", speak("Hello there my friends.")))["verdict"] == "unclear"


# --- pace ------------------------------------------------------------------------------

PACE = ("One two three four five. [slow] Six seven eight nine ten. "
        "Eleven twelve thirteen fourteen fifteen. [fast] Sixteen seventeen eighteen nineteen twenty.")


def paced(per_word: list[float]) -> list[dict]:
    """Four five-word sentences, each at its own seconds per word."""
    names = PACE.replace("[slow] ", "").replace("[fast] ", "").split()
    words, t = [], 0.0
    for i, text in enumerate(names):
        d = per_word[i // 5]
        words.append({"text": text, "start": round(t, 3), "end": round(t + d * 0.8, 3), "probability": 0.9})
        t += d + (0.5 if i % 5 == 4 else 0.0)
    return words


def test_slow_and_fast_against_the_take_average():
    v = judge(PACE, paced([0.3, 0.5, 0.3, 0.2]))
    slow, fast = only(v, 1), only(v, 3)
    assert slow["verdict"] == "hit" and slow["value"] < CUES.slow_ratio
    assert fast["verdict"] == "hit" and fast["value"] > CUES.fast_ratio
    # The average leaves the marked sentences out: 5 words in 1.44 s each.
    assert v["take_wpm"] == pytest.approx(60 * 5 / 1.44, abs=0.1)
    assert v["sentences"][0]["wpm"] == pytest.approx(v["take_wpm"], abs=0.1)

    v = judge(PACE, paced([0.3, 0.3, 0.3, 0.3]))
    assert only(v, 1)["verdict"] == "missed" and "needs under 85%" in only(v, 1)["reason"]
    assert only(v, 3)["verdict"] == "missed"


def test_pace_with_too_few_words_is_unclear():
    text = "One two three four five. [slow] Go now please."
    v = only(judge(text, speak("One two three four five. <0.5> Go now please.")))
    assert v["verdict"] == "unclear" and v["reason"] == "only 3 words heard"


def test_pace_without_an_unmarked_sentence_is_unclear_unless_given_one():
    text = "[slow] One two three four five."
    words = speak("One two three four five.", word_s=0.5)
    assert only(judge(text, words))["reason"] == "no unmarked sentence to compare with"
    v = only(judge(text, words, baseline_wpm=180.0))  # a drill: the last full take's pace
    assert v["verdict"] == "hit" and "last full take" in v["reason"]


def test_pace_in_a_skipped_sentence_is_skipped():
    v = judge(PACE, paced([0.3, 0.5, 0.3, 0.2])[:5])
    assert only(v, 1)["verdict"] == "skipped" and only(v, 3)["verdict"] == "skipped"


# --- synthetic voice -------------------------------------------------------------------

def voice(f0_start: float, f0_end: float, seconds: float, amp: float = 0.2) -> np.ndarray:
    """A buzzy vowel-like tone (a few harmonics) gliding from f0_start to f0_end."""
    n = int(seconds * RATE)
    f = np.linspace(f0_start, f0_end, n)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    tone = sum(np.sin(k * phase) / k for k in range(1, 5))
    ramp = np.minimum(1.0, np.minimum(np.arange(n), np.arange(n)[::-1]) / (0.01 * RATE))
    return (amp * tone * ramp / 1.5).astype(np.float32)


def utter(parts: list[tuple[str, np.ndarray]], gap_s: float = 0.08, t0: float = 5.0):
    """Audio of words separated by short silences, with their transcript words."""
    audio, words, t = [np.zeros(int(0.2 * RATE), np.float32)], [], 0.2
    for text, sound in parts:
        d = len(sound) / RATE
        words.append({"text": text, "start": round(t0 + t, 3), "end": round(t0 + t + d, 3), "probability": 0.9})
        audio += [sound, np.zeros(int(gap_s * RATE), np.float32)]
        t += d + gap_s
    audio.append(np.zeros(int(0.3 * RATE), np.float32))
    return np.concatenate(audio), words


STRESS = "Thank you for *being* here tonight."


def stressed(amp: float = 0.2, f0: float = 150.0):
    parts = [(w, voice(150, 150, 0.25)) for w in ("Thank", "you", "for")]
    parts += [("being", voice(f0, f0, 0.3, amp=amp))]
    parts += [(w, voice(150, 150, 0.25)) for w in ("here", "tonight.")]
    audio, words = utter(parts)
    return words, analyse(audio, RATE, 5.0)


def test_stress_by_loudness():
    words, p = stressed(amp=0.6)  # ~ +9.5 dB
    v = only(judge(STRESS, words, p))
    assert v["kind"] == "stress" and v["verdict"] == "hit"
    assert v["value"]["loud_db"] > CUES.stress_loud_db


def test_stress_by_pitch():
    words, p = stressed(f0=200.0)  # ~ +5 st, same loudness
    v = only(judge(STRESS, words, p))
    assert v["verdict"] == "hit"
    assert v["value"]["loud_db"] < CUES.stress_loud_db < 4 < v["value"]["pitch_st"]


def test_no_stress_is_missed():
    words, p = stressed()
    v = only(judge(STRESS, words, p))
    assert v["verdict"] == "missed" and "needs +3 dB or +2 st" in v["reason"]


def test_stress_on_an_unvoiced_or_unheard_word_is_unclear():
    parts = [(w, voice(150, 150, 0.25)) for w in ("Thank", "you", "for")]
    hiss = (0.05 * np.random.default_rng(1).standard_normal(int(0.3 * RATE))).astype(np.float32)
    parts += [("being", hiss)] + [(w, voice(150, 150, 0.25)) for w in ("here", "tonight.")]
    audio, words = utter(parts)
    v = only(judge(STRESS, words, analyse(audio, RATE, 5.0)))
    assert v["verdict"] == "unclear" and "pitch" in v["reason"]
    # No prosody at all (it failed): never guess.
    assert only(judge(STRESS, words, None))["verdict"] == "unclear"


RISE = "Are you coming with us? [rise]"
FALL = "That is all we have. [fall]"


def ending(text: str, f_start: float, f_end: float):
    names = text.split("?")[0].split(".")[0].split()
    parts = [(w, voice(160, 160, 0.22)) for w in names[:-1]] + [(names[-1], voice(f_start, f_end, 0.5))]
    audio, words = utter(parts)
    return only(judge(text, words, analyse(audio, RATE, 5.0)))


def test_rising_ending():
    v = ending(RISE, 150, 230)  # +7.4 st in 0.5 s
    assert v["kind"] == "rise" and v["verdict"] == "hit" and v["value"] > CUES.ending_slope_st_s
    assert v["reason"].startswith("pitch rose")
    assert ending(RISE, 230, 150)["verdict"] == "missed"
    flat = ending(RISE, 160, 160)
    assert flat["verdict"] == "missed" and flat["reason"].startswith("pitch stayed level")


def test_falling_ending():
    v = ending(FALL, 230, 150)
    assert v["kind"] == "fall" and v["verdict"] == "hit" and v["value"] < -CUES.ending_slope_st_s
    assert ending(FALL, 150, 230)["verdict"] == "missed"


def test_ending_without_voice_is_unclear():
    hiss = (0.05 * np.random.default_rng(2).standard_normal(int(0.4 * RATE))).astype(np.float32)
    audio, words = utter([(w, hiss) for w in "Are you coming with us?".split()])
    v = only(judge(RISE, words, analyse(audio, RATE, 5.0)))
    assert v["verdict"] == "unclear"


def test_pitch_is_in_semitones_from_the_speakers_median():
    low = analyse(np.concatenate([voice(110, 110, 0.5), voice(110, 165, 0.5)]), RATE, 0.0)
    high = analyse(np.concatenate([voice(220, 220, 0.5), voice(220, 330, 0.5)]), RATE, 0.0)
    assert low.median == pytest.approx(110, rel=0.05) and high.median == pytest.approx(220, rel=0.05)
    both = np.isfinite(low.st) & np.isfinite(high.st)
    assert both.sum() > 50
    assert np.nanmax(np.abs(low.st[both] - high.st[both])) < 0.5


# --- the whole take --------------------------------------------------------------------

def test_counts_summary_and_labels():
    text = "Good evening everyone. / Thank you for *being* here. [slow] Go now please."
    v = judge(text, speak("Good evening everyone. <0.4> Thank you for being here."))
    assert v["counts"] == {"hit": 1, "missed": 0, "unclear": 1, "skipped": 1}  # no prosody: stress unclear
    assert summary(v) == "1 hit, 1 unclear, 1 skipped (3 marks)"
    texts = ["Thank", "you", "for", "being", "here."]
    assert mark_label("short_pause", 0, texts) == '/ before "Thank"'
    assert mark_label("long_pause", 5, texts) == "// at the end"
    assert mark_label("stress", 3, texts) == "*being*"
    assert mark_label("slow", None, texts) == "[slow]"
