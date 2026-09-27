"""Text to speech behind one interface.

    get_speaker() -> Speaker        VOICE.backend picks the engine
    Speaker.say(text)               starts speaking and returns at once;
                                    a new say() cuts off the last one
    Speaker.stop()                  silences it at once, without waiting
    Speaker.estimate_s(words)       about how long saying them takes (say reports no position)

Used by "hear it" on a selected sentence in Prepare. macOS `say` is the
only engine so far.
"""

from __future__ import annotations

import subprocess
import time
from typing import Callable, Protocol

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
KILL_AFTER_S = 0.5  # a stopped `say` still running this long is killed


class MacSay:
    """macOS `say`, one utterance at a time."""

    def __init__(self, voice: str | None = VOICE.voice, rate_wpm: int | None = VOICE.rate_wpm):
        self.voice, self.rate_wpm = voice, rate_wpm
        self._proc: subprocess.Popen | None = None
        self._stopping: list[tuple[subprocess.Popen, float]] = []  # terminated, not yet reaped

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
        return 60.0 * len(words) / (self.rate_wpm or DEFAULT_WPM)

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
