"""Recording to disk as it happens, and recovering what a crash left."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from palmcards.capture import AudioRecorder
from palmcards.config import RECORDING
from palmcards.recording import TakeWriter, first_sample_time, repair_wav, wav_header
from palmcards.session import Session, read_wav, recover_all

RATE = 16000
BLOCK = 1024
ROOT = Path(__file__).resolve().parent.parent


def notes_session(tmp_path) -> Session:
    path = tmp_path / "talk.md"
    path.write_text("# One\n\nHello there friend. Good night all.\n")
    return Session.create(path, root=tmp_path / "sessions")


def ramp(n: int, start: int = 0) -> np.ndarray:
    """Blocks whose samples say where they are in the take (exactly representable in 16-bit)."""
    return (((np.arange(start, start + n) % 1000) - 500) / 32767 * 20).astype(np.float32)


def writer_for(session: Session, **kw) -> TakeWriter:
    number = session.begin_take()
    return TakeWriter(session.dir, number, RATE, {"started": "2026-09-25T10:00:00.000", "requested_t": 5.0,
                                                  "drill": None, "revision": session.current_revision}, **kw)


def test_blocks_stream_to_a_wav_and_the_take_is_added(tmp_path):
    session = notes_session(tmp_path)
    w = writer_for(session)
    w.push(ramp(BLOCK), first_t=5.02, source="adc")
    w.mark_section(5.0, 0)
    for k in range(1, 20):
        w.push(ramp(BLOCK, k * BLOCK))
    w.mark_section(5.9, 1, "voice")
    w.stop()
    w.stop()  # twice is fine
    assert w.wait(5) and w.state == "saved" and not w.part.exists()
    audio, rate = read_wav(session.dir / w.wav)
    assert rate == RATE and len(audio) == 20 * BLOCK
    assert np.allclose(audio, ramp(20 * BLOCK), atol=1 / 32767)

    take = session.finish_take(w.manifest())
    assert (take.number, take.status, take.t_start, take.duration_s) == (1, "saved", 5.02, round(20 * BLOCK / RATE, 3))
    assert take.sections == [{"section": 0, "t": 0.0, "source": "start"},
                             {"section": 1, "t": 0.88, "source": "voice"}]  # from the first sample
    assert take.capture == {"clock": "adc", "dropped_samples": 0, "discontinuities": []}
    assert not (session.dir / "take-01.recording.json").exists()
    assert Session.load(session.dir).take(1).status == "saved"


def test_first_sample_time_uses_the_device_clock_when_it_has_one():
    info = SimpleNamespace(inputBufferAdcTime=100.00, currentTime=100.03)
    assert first_sample_time(10.0, info, 1024, 48000) == (pytest.approx(9.97), "adc")
    for bad in (SimpleNamespace(inputBufferAdcTime=0.0, currentTime=0.0), SimpleNamespace(),
                SimpleNamespace(inputBufferAdcTime=100.0, currentTime=99.0)):  # missing, or nonsense
        assert first_sample_time(10.0, bad, 480, 48000) == (pytest.approx(9.99), "callback")


class FakeWriter:
    def __init__(self):
        self.blocks, self.firsts, self.overflows, self.started = [], [], 0, False

    def push(self, block, first_t=None, source=None):
        self.blocks.append(block)
        if first_t is not None:
            self.firsts.append((first_t, source))
            self.started = True

    def mark_overflow(self):
        self.overflows += 1


def test_the_callback_hands_blocks_over_and_stamps_the_first():
    rec = object.__new__(AudioRecorder)  # no sounddevice: just the callback's logic
    rec.clock, rec.rate, rec.level, rec.overflows, rec.writer, rec.tap = (lambda: 20.0), 48000, 0.0, 0, None, None
    block = np.full((1024, 1), 0.1, np.float32)
    ok = SimpleNamespace(input_overflow=False)
    rec._callback(block, 1024, SimpleNamespace(inputBufferAdcTime=5.0, currentTime=5.02), ok)
    assert rec.level > 0  # metered even when not recording
    w = FakeWriter()
    w.first_sample_t, w.enqueued = None, 0
    tapped = []
    rec.tap = lambda block, t_end: tapped.append(t_end)
    rec.start(w)
    rec._callback(block, 1024, SimpleNamespace(inputBufferAdcTime=5.0, currentTime=5.02), ok)
    rec._callback(block, 1024, SimpleNamespace(), SimpleNamespace(input_overflow=True))
    assert len(w.blocks) == 2 and w.firsts == [(pytest.approx(19.98), "adc")] and w.overflows == 1
    assert rec.overflows == 1
    assert tapped == []  # FakeWriter has no first sample time: nothing to place the block on
    w.first_sample_t, w.enqueued = 19.98, 2048
    rec._callback(block, 1024, SimpleNamespace(), ok)
    assert tapped == [pytest.approx(19.98 + 2048 / 48000)]  # the block's end, on the app clock


class SlowFile:
    """A file whose writes wait until released: a stalled disk."""

    def __init__(self, path, mode):
        self.f = open(path, mode)
        self.release = threading.Event()

    def write(self, data):
        if len(data) > 44:
            self.release.wait()
        return self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)


def test_a_full_queue_drops_blocks_without_blocking_and_records_the_gap(tmp_path):
    session = notes_session(tmp_path)
    files = []

    def slow_open(path, mode):
        files.append(SlowFile(path, mode))
        return files[-1]

    w = writer_for(session, open_file=slow_open)
    capacity = w._queue.maxsize
    assert capacity == int(RECORDING.queue_s * RATE / BLOCK)  # bounded: no audio piles up in memory
    worst = 0.0
    pushed = capacity + 40
    for k in range(pushed):
        t = time.perf_counter()
        w.push(ramp(BLOCK, k * BLOCK), first_t=1.0 if k == 0 else None, source="adc")
        worst = max(worst, time.perf_counter() - t)
    assert worst < 0.005  # the audio callback never waits for the disk
    gaps = w.manifest()["discontinuities"]
    assert gaps and all(g["why"] == "queue_full" for g in gaps)
    dropped = sum(g["samples"] for g in gaps)
    files[0].release.set()
    time.sleep(0.2)  # the writer catches up; later audio lands after the gap
    later = 5
    for k in range(pushed, pushed + later):
        w.push(ramp(BLOCK, k * BLOCK))
    w.stop()
    assert w.wait(10) and w.state == "saved"
    # The file keeps the take's timeline: the dropped stretch is silence, later audio is where it was said.
    audio, _ = read_wav(session.dir / w.wav)
    assert len(audio) == (pushed + later) * BLOCK == w.samples
    for g in gaps:  # every dropped stretch is silence, in its place
        assert not audio[g["at"]:g["at"] + g["samples"]].any()
    kept = np.ones(len(audio), bool)
    for g in gaps:
        kept[g["at"]:g["at"] + g["samples"]] = False
    assert np.allclose(audio[kept], ramp((pushed + later) * BLOCK)[kept], atol=1 / 32767)  # the rest where it was said
    take = session.finish_take(w.manifest())
    assert take.capture["dropped_samples"] == dropped and take.capture["discontinuities"][0]["why"] == "queue_full"
    assert take.duration_s == round((pushed + later) * BLOCK / RATE, 3)


class FullDisk:
    """Audio writes succeed until `limit` bytes, then fail like a full disk
    (rewriting the header in place takes no new space)."""

    def __init__(self, path, mode, limit):
        self.f, self.left = open(path, mode), limit

    def write(self, data):
        if len(data) > 44:
            if len(data) > self.left:
                raise OSError(28, "No space left on device")
            self.left -= len(data)
        return self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)


def test_a_disk_error_keeps_what_was_written_and_marks_the_take_failed(tmp_path):
    session = notes_session(tmp_path)
    w = writer_for(session, open_file=lambda p, m: FullDisk(p, m, 5 * BLOCK * 2))
    for k in range(10):
        w.push(ramp(BLOCK, k * BLOCK), first_t=1.0 if k == 0 else None, source="adc")
    w.stop()
    assert w.wait(5) and w.state == "failed" and "No space" in w.error
    audio, _ = read_wav(session.dir / w.wav)
    assert len(audio) == 5 * BLOCK and np.allclose(audio, ramp(5 * BLOCK), atol=1 / 32767)
    take = session.finish_take(w.manifest())
    assert take.status == "failed" and take.capture["dropped_samples"] == 5 * BLOCK
    assert take.capture["discontinuities"][0] == {"at_s": round(5 * BLOCK / RATE, 3), "samples": 5 * BLOCK,
                                                  "why": "write_error"}
    assert "No space" in take.capture["error"]


def test_a_file_that_cannot_be_opened_fails_without_a_take(tmp_path):
    session = notes_session(tmp_path)

    def refuse(path, mode):
        raise OSError(28, "No space left on device")

    w = writer_for(session, open_file=refuse)
    w.push(ramp(BLOCK), first_t=1.0, source="adc")
    w.stop()
    assert w.wait(5) and w.state == "failed" and w.samples == 0 and not (session.dir / w.wav).exists()


def test_repair_wav_fixes_a_header_left_stale_by_a_crash(tmp_path):
    path = tmp_path / "x.wav.part"
    pcm = (ramp(3000) * 32767).round().astype("<i2").tobytes()
    path.write_bytes(wav_header(RATE, 1000) + pcm + b"\x01")  # header says 1000; 3000 and a half sample on disk
    assert repair_wav(path) == 3000
    audio, rate = read_wav(path)
    assert rate == RATE and len(audio) == 3000
    empty = tmp_path / "y.wav.part"
    empty.write_bytes(b"RIFF")  # died before the header was written
    assert repair_wav(empty) == 0


CRASHER = """
import sys, time
sys.path.insert(0, {root!r})
import numpy as np
from pathlib import Path
from palmcards.session import Session
from palmcards.recording import TakeWriter
path = Path({tmp!r}) / "talk.md"
session = Session.create(path, root=Path({tmp!r}) / "sessions")
n = session.begin_take()
w = TakeWriter(session.dir, n, 16000, {{"started": "2026-09-25T10:00:00.000", "requested_t": 3.0, "drill": None,
                                         "revision": session.current_revision}})
w.mark_section(3.0, 0)
k = 0
while True:
    w.push(np.full(1024, 0.25, np.float32), first_t=3.01 if k == 0 else None, source="adc")
    k += 1
    if k == 16 * 3:  # three seconds in: tell the parent, then keep recording until killed
        print(session.dir, flush=True)
    time.sleep(1024 / 16000)
"""


def test_a_killed_recording_is_salvaged_at_the_next_start(tmp_path):
    (tmp_path / "talk.md").write_text("# One\n\nHello there friend. Good night all.\n")
    proc = subprocess.Popen([sys.executable, "-c", CRASHER.format(root=str(ROOT), tmp=str(tmp_path))],
                            stdout=subprocess.PIPE, text=True)
    folder = Path(proc.stdout.readline().strip())
    time.sleep(0.3)
    os.kill(proc.pid, signal.SIGKILL)  # no chance to finish anything
    proc.wait()
    assert (folder / "take-01.wav.part").exists() and (folder / "take-01.recording.json").exists()

    lines = recover_all(tmp_path / "sessions")
    assert len(lines) == 1 and "take 1 recovered" in lines[0] and "interrupted" in lines[0]
    session = Session.load(folder)
    take = session.take(1)
    assert take.status == "interrupted" and take.capture["recovered"]
    # Everything but the queue's last moments survives: at least the three seconds before the signal.
    assert take.duration_s >= 3.0 - RECORDING.flush_s and take.peak == pytest.approx(0.25, abs=1e-3)
    assert take.t_start == 3.01 and take.sections == [{"section": 0, "t": 0.0, "source": "start"}]
    assert not (folder / "take-01.wav.part").exists() and not (folder / "take-01.recording.json").exists()
    assert recover_all(tmp_path / "sessions") == []  # nothing left to do: repeatable


def test_recovery_leaves_a_session_that_is_still_open_alone(tmp_path):
    session = notes_session(tmp_path)
    w = writer_for(session)
    w.push(ramp(BLOCK), first_t=1.0, source="adc")
    time.sleep(RECORDING.flush_s + 0.2)
    assert recover_all(tmp_path / "sessions") == []  # this process still holds the session
    w.stop()
    assert w.wait(5)
    session.finish_take(w.manifest())


def test_a_manifest_whose_take_was_already_added_is_just_cleaned_up(tmp_path):
    session = notes_session(tmp_path)
    w = writer_for(session)
    w.push(ramp(BLOCK), first_t=1.0, source="adc")
    w.stop()
    assert w.wait(5)
    manifest = w.manifest()
    session.finish_take(manifest)
    (session.dir / "take-01.recording.json").write_text(json.dumps(manifest))  # crash before its removal
    session.release()
    assert recover_all(tmp_path / "sessions") == []
    assert not (session.dir / "take-01.recording.json").exists()
    assert len(Session.load(session.dir).takes) == 1


def test_a_take_with_no_audio_uses_the_moment_it_was_asked_for(tmp_path):
    session = notes_session(tmp_path)
    w = writer_for(session)
    w.mark_section(5.0, 0)
    w.stop()
    assert w.wait(5)
    m = w.manifest()
    assert m["first_sample_t"] is None and m["samples"] == 0
    take = session.finish_take(m)
    assert (take.t_start, take.duration_s, take.sections) == (5.0, 0.0, [{"section": 0, "t": 0.0, "source": "start"}])
