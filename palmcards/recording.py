"""Recording a take to disk as it happens.

The audio callback hands each block to a TakeWriter through a bounded queue
and never waits: when the queue is full the block is dropped and the gap is
recorded, never hidden. The writer fills a dropped stretch with silence, so
a moment x seconds into the file is still x seconds into the take (the
transcript, pitch and sections stay on the right clock); the gap list says
where the silence is not the speaker's. A writer thread appends the blocks to
take-NN.wav.part, a WAV whose header it rewrites every RECORDING.flush_s
(so the file is a valid WAV even after a crash) before fsyncing. Beside it,
take-NN.recording.json (the manifest) says what is being recorded: rate,
the first sample's app time, sections, gaps, state. It is rewritten
atomically at every flush.

States: recording -> finalizing -> saved, or failed if the disk refused a
write (what was written is kept; later audio is recorded as a gap). On stop
the writer drains the queue, fixes the header, fsyncs and renames the part
file to take-NN.wav; the session then adds the take (Session.finish_take)
and deletes the manifest. A manifest still on disk at startup means the app
died mid-take: Session.recover salvages the audio as an "interrupted" take.

Worst-case loss: audio still in the queue (at most RECORDING.queue_s) if the
app dies, plus what the system had not yet written since the last fsync (at
most RECORDING.flush_s) if the whole machine loses power.

The first sample's app time comes from the device's own clock where
PortAudio reports it (the ADC time of the block), else from the callback's
arrival less the block's length.
"""

from __future__ import annotations

import json
import os
import queue
import struct
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from palmcards.config import RECORDING

HEADER_BYTES = 44
STOP = object()


def wav_header(rate: int, samples: int) -> bytes:
    """44-byte header of a 16-bit mono PCM WAV holding `samples` samples."""
    data = samples * 2
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + data, b"WAVE", b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
                       b"data", data)


def repair_wav(path: Path) -> int:
    """Make a partly written WAV's header match what is in it; returns its samples."""
    size = path.stat().st_size
    if size < HEADER_BYTES:  # died before the header was written: nothing usable
        return 0
    samples = (size - HEADER_BYTES) // 2
    with open(path, "r+b") as f:
        f.seek(24)
        rate = struct.unpack("<I", f.read(4))[0]
        f.truncate(HEADER_BYTES + samples * 2)
        f.seek(0)
        f.write(wav_header(rate, samples))
    return samples


def first_sample_time(now: float, time_info, frames: int, rate: int) -> tuple[float, str]:
    """App time of a block's first sample, and how it was worked out: from
    the device clock ("adc") when PortAudio gives it, else from the
    callback's arrival ("callback"). `now` is the app clock in the callback."""
    adc = getattr(time_info, "inputBufferAdcTime", 0.0) or 0.0
    current = getattr(time_info, "currentTime", 0.0) or 0.0
    if adc > 0 and current > 0 and 0.0 <= current - adc < 1.0:
        return now - (current - adc), "adc"
    return now - frames / rate, "callback"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.replace(path)


class TakeWriter:
    """Streams one take to take-NN.wav(.part); see the module doc.

    push(), mark_overflow() and started are for the audio callback: they
    never block. Everything else is for the app's thread.
    """

    def __init__(self, folder: Path, number: int, rate: int, meta: dict, block_frames: int = RECORDING.block_frames,
                 open_file: Callable = open):
        self.folder, self.number, self.rate = Path(folder), number, rate
        self.wav = f"take-{number:02d}.wav"
        self.part = self.folder / f"{self.wav}.part"
        self.manifest_path = self.folder / f"take-{number:02d}.recording.json"
        self.meta = dict(meta)  # started, drill, revision, requested_t ...
        self.state = "recording"
        self.error: str | None = None
        self.first_sample_t: float | None = None
        self.clock_source: str | None = None
        self.enqueued = 0  # the stream so far: samples queued or dropped (a sample's place in the file)
        self.samples = 0  # samples in the file, silence filling dropped stretches included
        self.peak = 0.0
        self.discontinuities: list[dict] = []  # {"at": sample in the file, "samples": n or None, "why": ...}
        self.sections: list[tuple[float, int]] = []  # (app time, section)
        self.done = threading.Event()
        self._open = open_file
        self._queue: queue.Queue = queue.Queue(maxsize=max(2, int(RECORDING.queue_s * rate / block_frames)))
        self._lock = threading.Lock()  # guards discontinuities/sections for the manifest copy
        self._thread = threading.Thread(target=self._run, name=f"take-{number:02d}-writer", daemon=True)
        self._thread.start()

    # --- the audio callback's side ----------------------------------------

    @property
    def started(self) -> bool:
        return self.first_sample_t is not None

    def push(self, block: np.ndarray, first_t: float | None = None, source: str | None = None) -> None:
        if self.first_sample_t is None and first_t is not None:
            self.first_sample_t, self.clock_source = first_t, source
        if self.state not in ("recording",):
            return
        try:
            self._queue.put_nowait((self.enqueued, block))
        except queue.Full:
            self._gap(self.enqueued, len(block), "queue_full")
        self.enqueued += len(block)

    def mark_overflow(self) -> None:
        """The device dropped input before we saw it (PortAudio input overflow)."""
        self._gap(self.enqueued, None, "input_overflow")

    def _gap(self, at: int, samples: int | None, why: str) -> None:
        with self._lock:
            last = self.discontinuities[-1] if self.discontinuities else None
            if last and last["why"] == why and samples is not None and last["samples"] is not None \
                    and last["at"] + last["samples"] == at:
                last["samples"] += samples  # one gap, however many blocks in a row
            else:
                self.discontinuities.append({"at": at, "samples": samples, "why": why})

    # --- the app's side -----------------------------------------------------

    def mark_section(self, t: float, section: int) -> None:
        with self._lock:
            self.sections.append((t, section))

    def stop(self) -> None:
        """Finish in the background: drain, fix the header, publish. Never blocks."""
        if self.state == "recording":
            self.state = "finalizing"
            try:
                self._queue.put_nowait(STOP)
            except queue.Full:
                threading.Thread(target=self._queue.put, args=(STOP,), daemon=True).start()

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    @property
    def seconds(self) -> float:
        return self.enqueued / self.rate

    def manifest(self) -> dict:
        with self._lock:
            gaps = [dict(g) for g in self.discontinuities]
            sections = list(self.sections)
        return {
            "take": self.number, "wav": self.wav, "part": self.part.name, "rate": self.rate,
            "state": self.state, "error": self.error,
            "first_sample_t": self.first_sample_t, "clock": self.clock_source,
            "samples": self.samples, "peak": round(self.peak, 5),
            "discontinuities": gaps, "sections": [[round(t, 3), s] for t, s in sections],
            **self.meta,
        }

    # --- the writer thread ----------------------------------------------------

    def _run(self) -> None:
        f = None
        try:
            f = self._open(self.part, "wb")
            f.write(wav_header(self.rate, 0))
            self._flush(f)
        except OSError as exc:
            self._fail(exc)
        last_flush = time.monotonic()
        while True:
            try:
                item = self._queue.get(timeout=RECORDING.flush_s)
            except queue.Empty:
                item = None
            if item is STOP:
                break
            if item is not None:
                at, block = item
                if self.error is None and f is not None:
                    try:
                        self._fill_to(f, at)
                        pcm = (np.clip(block, -1.0, 1.0) * 32767).round().astype("<i2")
                        f.write(pcm.tobytes())
                        self.samples += len(block)
                        self.peak = max(self.peak, float(np.abs(block).max()) if len(block) else 0.0)
                    except OSError as exc:
                        self._fail(exc)
                        self._gap(at, len(block), "write_error")
                else:
                    self._gap(at, len(block), "write_error")
            if f is not None and self.error is None and time.monotonic() - last_flush >= RECORDING.flush_s:
                try:
                    self._flush(f)
                except OSError as exc:
                    self._fail(exc)
                last_flush = time.monotonic()
        self._finish(f)

    def _fill_to(self, f, at: int) -> None:
        """Silence for samples dropped before stream position `at`."""
        while self.samples < at:
            n = min(at - self.samples, self.rate)
            f.write(bytes(2 * n))
            self.samples += n

    def _flush(self, f) -> None:
        """Header up to date, bytes on disk, manifest rewritten."""
        f.flush()
        pos = f.tell()
        f.seek(0)
        f.write(wav_header(self.rate, self.samples))
        f.seek(pos)
        f.flush()
        os.fsync(f.fileno())
        _write_json(self.manifest_path, self.manifest())

    def _fail(self, exc: OSError) -> None:
        if self.error is None:
            self.error = f"{type(exc).__name__}: {exc}"

    def _finish(self, f) -> None:
        try:
            if f is not None:
                try:
                    if self.error is None:
                        self._fill_to(f, self.enqueued)  # dropped at the very end: the take keeps its length
                    self._flush(f)
                except OSError as exc:
                    self._fail(exc)
                f.close()
                if self.part.exists():
                    self.samples = repair_wav(self.part)  # what is really on disk
                    self.part.replace(self.folder / self.wav)
            self.state = "failed" if self.error else "saved"
            try:
                _write_json(self.manifest_path, self.manifest())
            except OSError as exc:
                self._fail(exc)
        finally:
            self.done.set()
