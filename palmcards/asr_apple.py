"""Apple's on-device speech recogniser (SFSpeechRecognizer), behind palmcards.asr.

Picked with SPEECH.backend = "apple". Needs pyobjc (pyobjc-framework-Speech,
pyobjc-framework-AVFoundation) and Speech Recognition permission for the
terminal app (System Settings > Privacy & Security > Speech Recognition).
Recognition stays on the Mac (requiresOnDeviceRecognition).

Live: audio is streamed into one recognition request; Apple sends a partial
transcript (everything heard so far, revised as it goes) several times a
second. A word is confirmed once two consecutive partials agree on it, as
with Whisper, matched by position in the transcript. After a long pause
Apple starts a new utterance: the partials start over from its first word.
Partial results carry no word times (only the final one does), so a live
word's start and end are the app time it first appeared, not when it was
said.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from palmcards.asr import LiveWord, Transcription
from palmcards.config import SPEECH
from palmcards.notes import normalize

AUTHORIZED = 3  # SFSpeechRecognizerAuthorizationStatusAuthorized
FLOAT32 = 1  # AVAudioPCMFormatFloat32


def _locale(language: str) -> str:
    return dict(SPEECH.apple_locales).get(language, language)


def _recognizer(language: str):
    import Foundation
    import Speech

    status = Speech.SFSpeechRecognizer.authorizationStatus()
    if status == 0:  # not asked yet: ask, and wait for the answer
        done, answer = threading.Event(), {}

        def handler(s):
            answer["s"] = s
            done.set()

        Speech.SFSpeechRecognizer.requestAuthorization_(handler)
        done.wait(120)
        status = answer.get("s", status)
    if status != AUTHORIZED:
        raise RuntimeError("Speech Recognition is not allowed. On macOS, allow it for your terminal app in "
                           "System Settings > Privacy & Security > Speech Recognition.")
    rec = Speech.SFSpeechRecognizer.alloc().initWithLocale_(
        Foundation.NSLocale.localeWithLocaleIdentifier_(_locale(language)))
    if rec is None or not rec.isAvailable() or not rec.supportsOnDeviceRecognition():
        raise RuntimeError(f"no on-device speech recogniser for {_locale(language)}")
    rec.setQueue_(Foundation.NSOperationQueue.alloc().init())  # results on a worker thread, not the main loop
    return rec


def _words(result) -> list[str]:
    """The words of a result's best transcription (a segment can hold several)."""
    return [w for seg in result.bestTranscription().segments() for w in str(seg.substring()).split()]


class PositionAgreement:
    """Confirms the words two consecutive partial transcripts agree on.

    Partials hold everything heard in the utterance so far, so words are
    compared by position after the ones already confirmed. Apple sometimes
    revises earlier words ("to" appears in "good evening everyone"), which
    shifts positions; the confirmed point is found again in each partial by
    its last words. A partial much shorter than the last one, opening with
    other words, starts a new utterance (after a pause): positions start over.
    """

    def __init__(self):
        self.prev: list[str] = []
        self.confirmed: list[str] = []  # this utterance's confirmed words
        self.utterance = 0

    def _resume(self, words: list[str]) -> int:
        """Index in `words` just past the confirmed words."""
        n = len(self.confirmed)
        if n == 0:
            return 0
        for size in (2, 1):  # the last two confirmed words, else the last one, near where they were
            tail = [normalize(w) for w in self.confirmed[-size:]]
            for k in sorted(range(max(len(tail), n - 3), min(len(words), n + 3) + 1), key=lambda k: abs(k - n)):
                if [normalize(w) for w in words[k - len(tail):k]] == tail:
                    return k
        return n

    def update(self, words: list[str], final: bool = False) -> list[tuple[int, str]]:
        """Take the next partial; returns (position, word) newly confirmed.
        A final transcript is Apple's last word: all of it is confirmed."""
        # A new utterance starts short, with other opening words; a revised
        # opening word ("Evening" to "Good evening") keeps the length.
        head = [normalize(w) for w in words[:2]]
        if len(words) < len(self.prev) - 2 and head != [normalize(w) for w in self.prev[:len(head)]]:
            self.prev, self.confirmed = [], []
            self.utterance += 1
        if final:
            self.prev = words
        start = self._resume(words)
        out = []
        for i, (x, y) in enumerate(zip(self.prev[self._resume(self.prev):], words[start:])):
            if normalize(x) != normalize(y):
                break
            out.append((start + i, y))
        self.confirmed += [w for _, w in out]
        self.prev = words
        return out


class AppleLive:
    """Live stream on SFSpeechRecognizer; see the module doc."""

    where = "apple"

    def __init__(self, language: str, clock: Callable[[], float], hints: tuple[str, ...] = ()):
        import AVFoundation

        self.clock = clock
        self.hints = list(hints)[:SPEECH.apple_max_hints]
        self.state = "ready"  # _recognizer() below raises if it can't be
        self.runs = 0  # partial results received
        self.skipped_silent = 0
        self.run_ms: list[float] = []  # no reads to time: recognition streams
        self.errors: list[str] = []
        self._av = AVFoundation
        self._rec = _recognizer(language)
        self._format = AVFoundation.AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(
            FLOAT32, float(SPEECH.rate), 1, False)
        self._results: queue.Queue = queue.Queue()
        self._request = None
        self._task = None
        self._generation = 0  # a new request after one ends; results of old ones are ignored
        self._agree = PositionAgreement()
        self._seen: dict[tuple[int, int, str], float] = {}  # (utterance, position, word) -> app time first seen

    def _start(self) -> None:
        import Speech

        request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(True)
        if hasattr(request, "setAddsPunctuation_"):
            request.setAddsPunctuation_(False)
        if self.hints:  # phrases to expect: the notes
            request.setContextualStrings_(self.hints)
        self._generation += 1
        generation = self._generation

        def handler(result, error):
            now = self.clock()
            if error is not None:
                self._results.put((generation, now, None, str(error.localizedDescription())))
            elif result is not None:
                self._results.put((generation, now, (_words(result), bool(result.isFinal())), None))

        self._request = request
        self._task = self._rec.recognitionTaskWithRequest_resultHandler_(request, handler)
        self._agree = PositionAgreement()
        self._seen = {}

    def feed(self, samples: np.ndarray, t_end: float) -> None:
        if self._request is None:
            self._start()
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        buf = self._av.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(self._format, len(samples))
        buf.setFrameLength_(len(samples))
        np.frombuffer(buf.floatChannelData()[0].as_buffer(len(samples)), dtype=np.float32)[:] = samples
        self._request.appendAudioPCMBuffer_(buf)

    def poll(self) -> list[LiveWord]:
        out = []
        while True:
            try:
                generation, now, result, error = self._results.get_nowait()
            except queue.Empty:
                return out
            if generation != self._generation:
                continue
            if error is not None:
                self.errors.append(error)
                self._request = None  # the next feed starts a new request
                continue
            words, final = result
            self.runs += 1
            confirmed = self._agree.update(words, final)  # first: it may start a new utterance
            u = self._agree.utterance
            for i, w in enumerate(words):
                self._seen.setdefault((u, i, normalize(w)), now)
            for i, w in confirmed:
                t = round(self._seen[(u, i, normalize(w))], 3)
                out.append(LiveWord(w, t, t, 1.0, confirmed_at=round(now, 3)))
            if final:  # the request is over (Apple ended it); carry on in a new one
                self._request = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def reset(self) -> None:
        """End the current request; the next feed starts a new one."""
        if self._request is not None:
            self._request.endAudio()
        if self._task is not None:
            self._task.cancel()
        self._request = self._task = None
        self._generation += 1  # results of the old request are dropped

    def close(self) -> None:
        self.reset()
        self.state = "closed"


class AppleSpeech:
    """Recognizer on Apple's on-device speech recognition."""

    def __init__(self):
        self.model = self.live_model = "apple-on-device"

    def transcribe(self, wav: Path, language: str) -> Transcription:
        import Foundation
        import Speech

        rec = _recognizer(language)
        request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(
            Foundation.NSURL.fileURLWithPath_(str(wav.resolve())))
        request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(False)
        done, got = threading.Event(), {}

        def handler(result, error):
            if error is not None:
                got["error"] = str(error.localizedDescription())
                done.set()
            elif result is not None and result.isFinal():
                got["result"] = result
                done.set()

        rec.recognitionTaskWithRequest_resultHandler_(request, handler)
        while not done.wait(0.1):
            pass
        if "error" in got:
            raise RuntimeError(f"Apple speech recognition failed: {got['error']}")
        best = got["result"].bestTranscription()
        words = []
        for seg in best.segments():
            parts = str(seg.substring()).split()
            step = seg.duration() / max(1, len(parts))
            for k, part in enumerate(parts):  # a segment holding several words: share out its time
                words.append({"word": " " + part, "start": seg.timestamp() + k * step,
                              "end": seg.timestamp() + (k + 1) * step, "probability": seg.confidence()})
        return Transcription(str(best.formattedString()), 0.0, [{"words": words}], self.model)

    def live(self, language: str, clock: Callable[[], float], where: str = SPEECH.live_where,
             hints: tuple[str, ...] = ()) -> AppleLive:
        return AppleLive(language, clock, hints)
