"""Text to speech behind one interface.

    get_speaker() -> Speaker        VOICE.backend picks the engine
    Speaker.say(text)               starts speaking and returns at once;
                                    a new say() cuts off the last one
    Speaker.stop()                  silences it at once, without waiting
    Speaker.estimate_s(words)       about how long saying them takes (say reports no position)
    Speaker.render_async(words)     a Future of the words as audio, (float32 samples, rate):
                                    made in the background, kept for the next time (optional)

Used by "hear it" on a selected sentence in Prepare. Spoken live, `say`
reports no position, so how far it has got can only be estimated; rendered
to audio first (started when the sentence is focused, ready by the time the
open palm has been held), it plays as a clip of known length, and its
progress bar is exact. macOS `say` is the only engine so far.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import wave
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Protocol

import numpy as np

from palmcards.config import VOICE


class Speaker(Protocol):
    def say(self, text: str, voice: str | None = None, rate_wpm: int | None = None) -> None: ...

    def say_words(self, words: list[str], stressed: set[int] = frozenset()) -> None:
        """Speak words, emphasising the ones at `stressed` where the engine can."""

    def stop(self) -> None: ...

    def estimate_s(self, words: list[str]) -> float: ...

    @property
    def speaking(self) -> bool: ...


DEFAULT_WPM = 175  # macOS `say` without -r
# Spoken live, `say` runs about this much longer than its speech (starting up
# before it, finishing after it): measured 1.0-1.1 s on sentences of 13-14 words.
SAY_OVERHEAD_S = 1.0
KILL_AFTER_S = 0.5  # a stopped `say` still running this long is killed
RENDER_RATE = 22050  # Hz: `say`'s own voices' rate
RENDERED_MAX = 16  # rendered utterances kept (about 0.4 MB for a sentence)


class MacSay:
    """macOS `say`, one utterance at a time."""

    def __init__(self, voice: str | None = VOICE.voice, rate_wpm: int | None = VOICE.rate_wpm):
        self.voice, self.rate_wpm = voice, rate_wpm
        self._proc: subprocess.Popen | None = None
        self._stopping: list[tuple[subprocess.Popen, float]] = []  # terminated, not yet reaped
        self._rendered: OrderedDict[str, Future] = OrderedDict()  # text -> its audio, newest last
        self._pool: ThreadPoolExecutor | None = None

    def argv(self, text: str, voice: str | None = None, rate_wpm: int | None = None) -> list[str]:
        args = ["say"]
        if voice := voice or self.voice:
            args += ["-v", voice]
        if rate_wpm := rate_wpm or self.rate_wpm:
            args += ["-r", str(int(rate_wpm))]
        return args + ["--", text]  # text starting with "-" is still text

    def say(self, text: str, voice: str | None = None, rate_wpm: int | None = None) -> None:
        self.stop()
        self._proc = subprocess.Popen(self.argv(text, voice, rate_wpm),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def say_words(self, words: list[str], stressed: set[int] = frozenset()) -> None:
        """`say` emphasises a word after its [[emph +]] command."""
        self.say(" ".join(f"[[emph +]] {w}" if i in stressed else w for i, w in enumerate(words)))

    def stop(self) -> None:
        """Silence it now (SIGTERM) without waiting for the process: it is reaped
        later, and killed if it is still there after KILL_AFTER_S."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            self._stopping.append((proc, time.monotonic()))
        self._reap()

    def _reap(self) -> None:
        still = []
        for proc, since in self._stopping:
            if proc.poll() is None:
                if time.monotonic() - since > KILL_AFTER_S:
                    proc.kill()
                still.append((proc, since))
        self._stopping = still

    def estimate_s(self, words: list[str]) -> float:
        """How long `say` runs for them, spoken live: the speech at its rate, and its start-up and finish."""
        return SAY_OVERHEAD_S + 60.0 * len(words) / (self.rate_wpm or DEFAULT_WPM)

    def render(self, text: str) -> tuple[np.ndarray, int]:
        """`text` as audio, (float32 mono samples, rate), through a temporary WAV."""
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="palmcards-say-")
        os.close(fd)
        try:
            argv = self.argv(text)
            subprocess.run([*argv[:-2], f"--data-format=LEI16@{RENDER_RATE}", "-o", path, *argv[-2:]],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with wave.open(path, "rb") as f:
                rate, channels = f.getframerate(), f.getnchannels()
                pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
            return pcm[::channels].astype(np.float32) / 32767, rate
        finally:
            os.unlink(path)

    def render_async(self, words: list[str]) -> Future:
        """The words as audio, made in the background (two at a time) and kept
        for the next time they are asked for (the RENDERED_MAX latest)."""
        text = " ".join(words)
        if (kept := self._rendered.get(text)) is not None and not (kept.done() and kept.exception()):
            self._rendered.move_to_end(text)
            return kept  # a failed one is made again
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="say-render")
        future = self._rendered[text] = self._pool.submit(self.render, text)
        while len(self._rendered) > RENDERED_MAX:
            self._rendered.popitem(last=False)
        return future

    def close(self) -> None:
        """Stop speaking; renders still being made finish on their own and are dropped."""
        self.stop()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    @property
    def speaking(self) -> bool:
        self._reap()
        return self._proc is not None and self._proc.poll() is None


BACKENDS: dict[str, Callable[[], Speaker]] = {"say": MacSay}


def get_speaker(backend: str | None = None) -> Speaker:
    name = backend or VOICE.backend
    if name not in BACKENDS:
        raise ValueError(f"unknown voice backend {name!r} (config.VOICE.backend); have: {', '.join(BACKENDS)}")
    return BACKENDS[name]()
