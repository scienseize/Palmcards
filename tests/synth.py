"""Made-up speech for tests: transcript words from a script, and buzzy
vowel-like tones for pitch and loudness."""

import re

import numpy as np

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
