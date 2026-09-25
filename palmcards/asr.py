"""Speech recognition behind one interface.

    get_recognizer() -> Recognizer          SPEECH.backend picks the engine

    Recognizer.transcribe(wav, language) -> Transcription
        a whole take after it stops: silence trimmed, word timestamps
    Recognizer.live(language, clock) -> LiveStream
        during a take: feed() it audio as it arrives, poll() for the words
        that are confirmed

Live words are confirmed by agreement: every SPEECH.live_step_s the engine
re-reads the recent audio, and a word counts once two consecutive readings
agree on it (same word, same place). The newest word in a window is often
cut off and read differently each time; waiting for agreement keeps those
guesses off the screen.

A window starts at the last confirmed word, never more than
SPEECH.live_window_s back. Starting mid-word would make Whisper garble the
opening words, and two windows cut in the same place garble them the same
way, so the garbage would be confirmed.

Live words are display only. The transcript of record is made after the
take (palmcards.speech).

Engines: mlx-whisper (Whisper on Apple silicon), and Apple's on-device
recogniser (palmcards.asr_apple, SFSpeechRecognizer).
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from palmcards.audio import prepare_audio, rms_db
from palmcards.config import ALIGN, SPEECH
from palmcards.notes import normalize


@dataclass
class Transcription:
    text: str
    offset_s: float  # silence trimmed from the front of the WAV, seconds
    segments: list[dict]  # Whisper-style; word times are into the trimmed audio
    model: str
    revision: str | None = None  # the model's exact revision (Hugging Face snapshot), where known


@dataclass(frozen=True)
class LiveWord:
    text: str
    start: float  # app clock
    end: float
    probability: float
    confirmed_at: float | None = None  # app clock when two readings agreed on it


class LiveStream(Protocol):
    # "starting" (loading the model), "ready", "failed" (see errors) or "closed".
    # A failed stream never recovers by itself; the take goes on without it.
    state: str
    errors: list[str]

    @property
    def ready(self) -> bool: ...

    def reset(self) -> None:
        """Forget the last take's audio and words (keeps the model loaded)."""

    def feed(self, samples: np.ndarray, t_end: float) -> None:
        """16 kHz mono float32 samples; t_end is the app time of the last one."""

    def poll(self) -> list[LiveWord]:
        """Words confirmed since the last poll, in order."""

    def close(self) -> None: ...


class Recognizer(Protocol):
    model: str

    def transcribe(self, wav: Path, language: str) -> Transcription: ...

    def live(self, language: str, clock: Callable[[], float], where: str = SPEECH.live_where,
             hints: tuple[str, ...] = ()) -> LiveStream:
        """hints: phrases the speaker is likely to say (the notes), for engines that take them."""


def initial_prompt(language: str) -> str | None:
    return dict(SPEECH.filler_prompts).get(language)


# --- agreement ---------------------------------------------------------------

class Agreement:
    """Confirms the words two consecutive readings agree on (LocalAgreement-2).

    Readings overlap, so a reading is first cut down to what is new: words
    starting before the last confirmed word ended are dropped (with a little
    slack, as Whisper's word edges move between readings). Then the readings
    are compared word by word from the front; the run that matches, same
    normalised text within SPEECH.live_agree_s of each other, is confirmed.
    """

    def __init__(self):
        self.prev: list[LiveWord] = []
        self.confirmed: list[LiveWord] = []

    @property
    def after(self) -> float:
        return self.confirmed[-1].end if self.confirmed else float("-inf")

    def _fresh(self, reading: list[LiveWord]) -> list[LiveWord]:
        after = self.after
        words = [w for w in reading if w.start > after - SPEECH.live_overlap_s and normalize(w.text)]
        # The last confirmed word read again, its start moved a little later.
        if self.confirmed:
            last = normalize(self.confirmed[-1].text)
            while words and words[0].start < after and normalize(words[0].text) == last:
                words.pop(0)
        return words

    def update(self, reading: list[LiveWord], now: float) -> list[LiveWord]:
        """Take the next reading; returns the words newly confirmed."""
        out = []
        for a, b in zip(self._fresh(self.prev), self._fresh(reading)):
            if normalize(a.text) != normalize(b.text) or abs(a.start - b.start) > SPEECH.live_agree_s:
                break
            out.append(replace(b, confirmed_at=round(now, 3)))
        self.confirmed += out
        self.prev = reading
        return out


# --- mlx-whisper -------------------------------------------------------------

def _read_window(audio: np.ndarray, language: str, model: str, prompt: str | None = None) -> list[dict]:
    import mlx_whisper  # heavy; only the reader needs it

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=prompt or None,
        temperature=0.0,
        verbose=None,
    )
    return [{"text": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"]),
             "probability": float(w.get("probability", 0.0))}
            for seg in result.get("segments", []) for w in seg.get("words", []) if w["word"].strip()]


def _serve_windows(inbox, outbox, model: str, language: str) -> None:
    """Window reader loop, in a thread or a process: (n, audio, prompt) in,
    (n, words, ms, error) out, until None."""
    while (job := inbox.get()) is not None:
        n, audio, prompt = job
        t = time.perf_counter()
        try:
            words, error = _read_window(audio, language, model, prompt), None
        except Exception as exc:
            words, error = [], f"{type(exc).__name__}: {exc}"
        outbox.put((n, words, round((time.perf_counter() - t) * 1000, 1), error))
    outbox.put(None)


class MlxWhisperLive:
    """Live stream on mlx-whisper: re-reads the recent audio every step.

    Windows are read one at a time; while one is being read, new audio just
    accumulates and the next window goes as soon as the reader is free (a
    stale window is never queued). `where="process"` reads in a separate
    process, so the reader never competes with the camera loop for the GIL.
    """

    def __init__(self, model: str, language: str, clock: Callable[[], float], where: str = "thread"):
        if where not in ("thread", "process"):
            raise ValueError(f"where must be 'thread' or 'process', not {where!r}")
        self.model, self.language, self.clock, self.where = model, language, clock, where
        self.rate = SPEECH.rate
        self.state = "starting"
        self._started_at = time.monotonic()
        self._epoch = 0  # bumped by reset(): replies to older windows are dropped
        self.runs = 0  # windows read
        self.skipped_silent = 0  # windows too quiet to read
        self.run_ms: list[float] = []
        self.errors: list[str] = []
        self._size = int(SPEECH.live_window_s * self.rate)
        self._step = int(SPEECH.live_step_s * self.rate)
        self._window = np.zeros(0, np.float32)
        self._fed = 0  # samples fed in total
        self._since = self._step  # samples fed since the last window was sent
        self._busy = False
        self._sent: dict[int, tuple[float, bool]] = {}  # run -> (window start, window cut mid-speech)
        self._agree = Agreement()
        self._done: queue.Queue = queue.Queue()
        if where == "process":
            ctx = mp.get_context("spawn")
            self._inbox, results = ctx.Queue(), ctx.Queue()
            self._reader = ctx.Process(target=_serve_windows, args=(self._inbox, results, model, language),
                                       name="live-reader", daemon=True)
        else:
            self._inbox, results = queue.Queue(), queue.Queue()
            self._reader = threading.Thread(target=_serve_windows, args=(self._inbox, results, model, language),
                                            name="live-reader", daemon=True)
        self._reader.start()
        threading.Thread(target=self._collect, args=(results,), name="live-collect", daemon=True).start()
        self._send(-1, np.zeros(self.rate, np.float32), 0.0, False, None)  # loads the model

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def _fail(self, why: str) -> None:
        if self.state not in ("failed", "closed"):
            self.state = "failed"
            self.errors.append(why)

    def reset(self) -> None:
        self._epoch += 1
        self._window = np.zeros(0, np.float32)
        self._fed = 0
        self._since = self._step
        self._agree = Agreement()
        self._sent = {k: v for k, v in self._sent.items() if k == -1}  # keep only the warm-up

    def _send(self, n: int, audio: np.ndarray, start: float, cut: bool, prompt: str | None) -> None:
        self._busy = True
        self._sent[(self._epoch, n) if n >= 0 else n] = (start, cut)
        self._inbox.put(((self._epoch, n) if n >= 0 else n, audio, prompt))

    def _collect(self, results) -> None:
        while (item := results.get()) is not None:
            self._done.put((*item, self.clock()))
            self._busy = False

    def feed(self, samples: np.ndarray, t_end: float) -> None:
        self._window = np.concatenate([self._window, samples.astype(np.float32, copy=False)])[-self._size:]
        self._fed += len(samples)
        self._since += len(samples)
        if self._since < self._step or self._busy or self.state != "ready":
            return
        self._since = 0
        start = t_end - len(self._window) / self.rate
        confirmed = self._agree.confirmed
        anchor = confirmed[-1].start - SPEECH.live_anchor_pad_s if confirmed else None
        if anchor is not None and anchor > start:  # start at the last confirmed word
            window, start, cut = self._window[int((anchor - start) * self.rate):], anchor, False
        else:  # nothing confirmed lately: the last live_window_s, cut wherever it falls
            window, cut = self._window, self._fed > len(self._window)
        if rms_db(window) < SPEECH.live_min_rms_db:
            self.skipped_silent += 1
            return
        n = SPEECH.live_prompt_words
        prompt = " ".join(w.text for w in confirmed[-n - 1:] if w.start < start) if n else None
        self.runs += 1
        self._send(self.runs, window.copy(), start, cut, prompt)

    def _check(self) -> None:
        """Fail a reader that never became ready, or died."""
        if self.state == "starting" and time.monotonic() - self._started_at > SPEECH.live_start_timeout_s:
            self._fail(f"the live model did not load within {SPEECH.live_start_timeout_s:.0f} s")
        alive = self._reader.is_alive()
        if not alive and self.state in ("starting", "ready"):
            self._fail("the live reader stopped")

    def poll(self) -> list[LiveWord]:
        out = []
        while True:
            try:
                n, words, ms, error, arrived = self._done.get_nowait()
            except queue.Empty:
                self._check()
                return out
            sent = self._sent.pop(n, None)
            if n == -1:  # the warm-up: it loaded the model, or it didn't
                if error:
                    self._fail(f"the live model failed to load: {error}")
                elif self.state == "starting":
                    self.state = "ready"
                continue
            if sent is None or n[0] != self._epoch:  # a window from before reset()
                continue
            start, cut = sent
            if error:
                self.errors.append(error)
                continue
            self.run_ms.append(ms)
            # A word starting right at the edge of a window cut mid-speech may be cut off.
            edge = SPEECH.live_edge_s if cut else -1.0
            reading = [LiveWord(w["text"], round(start + w["start"], 3), round(start + w["end"], 3),
                                round(w["probability"], 3))
                       for w in words if w["start"] >= edge and w["probability"] >= ALIGN.min_probability]
            out += self._agree.update(reading, arrived)

    def close(self) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        self._inbox.put(None)
        self._reader.join(timeout=2.0)
        if isinstance(self._reader, mp.process.BaseProcess) and self._reader.is_alive():
            self._reader.terminate()
            self._reader.join(timeout=1.0)


PINNED = {SPEECH.model: SPEECH.model_revision, SPEECH.live_model: SPEECH.live_model_revision}


def model_revision(repo: str) -> str | None:
    """The commit of the model snapshot used for `repo`: the pinned one if
    config pins it, else the cached one; None if it can't be told."""
    if PINNED.get(repo):
        return PINNED[repo]
    try:
        from huggingface_hub import snapshot_download

        return Path(snapshot_download(repo, local_files_only=True)).name
    except Exception:
        return None


def model_path(repo: str) -> str:
    """Where mlx-whisper should load `repo` from: the pinned revision's local
    snapshot. Falls back to the repo name (mlx-whisper then fetches the
    latest) only when nothing is pinned."""
    revision = PINNED.get(repo)
    if not revision:
        return repo
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(repo, revision=revision, local_files_only=True)
    except Exception:
        raise RuntimeError(f"model {repo} at revision {revision[:12]} is not downloaded; "
                           "run: python scripts/download_models.py") from None


class MlxWhisper:
    """Whisper on Apple silicon (mlx-whisper)."""

    def __init__(self, model: str = SPEECH.model, live_model: str = SPEECH.live_model):
        self.model, self.live_model = model, live_model

    def transcribe(self, wav: Path, language: str) -> Transcription:
        audio, offset_s = prepare_audio(wav)
        if not len(audio):
            return Transcription("", offset_s, [], self.model, model_revision(self.model))
        print(f"transcribing {wav.name} ({len(audio) / SPEECH.rate:.1f} s of sound)...", file=sys.stderr, flush=True)
        import mlx_whisper  # heavy; only the worker needs it

        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=model_path(self.model),
            language=language,
            word_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt(language),
            hallucination_silence_threshold=SPEECH.hallucination_silence_s,
            verbose=None,
        )
        return Transcription(result.get("text", "").strip(), offset_s, result.get("segments", []), self.model,
                             model_revision(self.model))

    def live(self, language: str, clock: Callable[[], float], where: str = SPEECH.live_where,
             hints: tuple[str, ...] = ()) -> MlxWhisperLive:
        return MlxWhisperLive(model_path(self.live_model), language, clock, where)  # hints: no (a prompt made it loop)


def _apple() -> Recognizer:
    from palmcards.asr_apple import AppleSpeech  # pyobjc; only loaded when picked

    return AppleSpeech()


BACKENDS: dict[str, Callable[[], Recognizer]] = {"mlx-whisper": MlxWhisper, "apple": _apple}


def get_recognizer(backend: str | None = None) -> Recognizer:
    name = backend or SPEECH.backend
    if name not in BACKENDS:
        raise ValueError(f"unknown speech backend {name!r} (config.SPEECH.backend); have: {', '.join(BACKENDS)}")
    return BACKENDS[name]()
