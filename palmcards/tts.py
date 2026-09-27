"""Text to speech behind one interface.

    get_speaker() -> Speaker        VOICE.backend picks the engine
    Speaker.say(text)               starts speaking and returns at once;
                                    a new say() cuts off the last one
    Speaker.stop()

Used by "hear it" on a selected sentence in Prepare. macOS `say` is the
only engine so far.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Protocol

from palmcards.config import VOICE


class Speaker(Protocol):
    def say(self, text: str, voice: str | None = None, rate_wpm: int | None = None) -> None: ...

    def say_words(self, words: list[str], stressed: set[int] = frozenset()) -> None:
        """Speak words, emphasising the ones at `stressed` where the engine can."""

    def stop(self) -> None: ...

    @property
    def speaking(self) -> bool: ...


class MacSay:
    """macOS `say`, one utterance at a time."""

    def __init__(self, voice: str | None = VOICE.voice, rate_wpm: int | None = VOICE.rate_wpm):
        self.voice, self.rate_wpm = voice, rate_wpm
        self._proc: subprocess.Popen | None = None

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
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    @property
    def speaking(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


BACKENDS: dict[str, Callable[[], Speaker]] = {"say": MacSay}


def get_speaker(backend: str | None = None) -> Speaker:
    name = backend or VOICE.backend
    if name not in BACKENDS:
        raise ValueError(f"unknown voice backend {name!r} (config.VOICE.backend); have: {', '.join(BACKENDS)}")
    return BACKENDS[name]()
