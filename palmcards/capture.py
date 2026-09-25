"""Camera and microphone capture.

Frames are mirrored once here, at capture. Everything downstream (rendering,
hit-testing) works in this flipped coordinate space.

A background thread reads the camera continuously, so the camera's own rate
(`Camera.fps`) is measured independently of how long each frame takes to
process. macOS webcams drop to ~15 fps on their own in dim light.

Audio is streamed to disk as it is recorded (AudioRecorder hands blocks
to palmcards.recording.TakeWriter); nothing accumulates in memory.
"""

import math
import threading
import time
from collections import deque
from typing import Callable

import cv2
import numpy as np

from palmcards.config import RECORDING
from palmcards.recording import first_sample_time


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(self, index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            raise CameraError(
                "Could not open the camera. On macOS, allow Camera access for your "
                "terminal app in System Settings > Privacy & Security > Camera, "
                "then quit and reopen the terminal."
            )
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._taken = 0
        self._arrivals: deque[float] = deque(maxlen=31)
        self._failed = False
        self._running = True
        self._thread = threading.Thread(target=self._reader, name="camera", daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while self._running:
            ok, frame = self.cap.read()
            with self._cond:
                if not ok or frame is None:
                    self._failed = True
                    self._cond.notify_all()
                    return
                self._frame = cv2.flip(frame, 1)
                self._seq += 1
                self._arrivals.append(time.perf_counter())
                self._cond.notify_all()

    def read(self, timeout: float = 2.0) -> np.ndarray:
        """Wait for a frame newer than the last one returned; mirrored BGR.

        If processing falls behind, older frames are skipped, never queued.
        """
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq > self._taken or self._failed, timeout):
                raise CameraError("Camera stopped delivering frames.")
            if self._failed and self._seq <= self._taken:
                raise CameraError("Camera stopped delivering frames.")
            self._taken = self._seq
            return self._frame

    @property
    def fps(self) -> float:
        """Rate at which the camera itself delivers frames (last ~second)."""
        with self._cond:
            a = list(self._arrivals)
        if len(a) < 2 or a[-1] == a[0]:
            return 0.0
        return (len(a) - 1) / (a[-1] - a[0])

    def release(self) -> None:
        self._running = False
        self._thread.join(timeout=1.0)
        self.cap.release()

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class AudioRecorder:
    """Default microphone, mono float32 at the device's own rate.

    open() starts the input stream (the count-in doubles as its warm-up),
    start(writer) streams blocks to a palmcards.recording.TakeWriter,
    stop() hands the writer back (it finishes writing in the background),
    close() releases the microphone so macOS's recording indicator goes off
    between takes. The callback only copies the block and hands it over: no
    disk, no locks, no waiting. Without Microphone permission macOS delivers
    silence, not an error; the take's peak shows it.

    `clock` is the app clock (seconds since the app started); the first
    sample of a take is placed on it with the device's timing.
    """

    def __init__(self, device: int | str | None = None, clock: Callable[[], float] = time.perf_counter):
        import sounddevice as sd

        self._sd = sd
        self.device = device
        self.clock = clock
        self.rate = int(sd.query_devices(device, kind="input")["default_samplerate"])
        self.level = 0.0  # last block's loudness, 0 (-60 dBFS or less) .. 1 (full scale)
        self.overflows = 0  # blocks the device reported as dropped during the take
        self.writer = None  # TakeWriter while recording
        self.tap = None  # also gets each block while recording (the voice follow); must never block
        self._stream = None

    def open(self) -> None:
        """Raises sounddevice.PortAudioError if the microphone can't be opened."""
        if self._stream is None:
            stream = self._sd.InputStream(device=self.device, samplerate=self.rate, channels=1, dtype="float32",
                                          blocksize=RECORDING.block_frames, callback=self._callback)
            stream.start()
            self._stream = stream

    def _callback(self, indata, frames, time_info, status) -> None:
        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0
        self.level = min(1.0, max(0.0, (20 * math.log10(max(rms, 1e-9)) + 60) / 60))
        writer = self.writer
        if writer is None:
            return
        if getattr(status, "input_overflow", False):
            self.overflows += 1
            writer.mark_overflow()
        if writer.started:
            writer.push(block)
        else:
            writer.push(block, *first_sample_time(self.clock(), time_info, frames, self.rate))
        tap = self.tap
        if tap is not None and writer.first_sample_t is not None:
            tap(block, writer.first_sample_t + writer.enqueued / self.rate)

    def start(self, writer) -> None:
        self.overflows = 0
        self.writer = writer

    @property
    def seconds(self) -> float:
        """Length of the take so far."""
        return self.writer.seconds if self.writer is not None else 0.0

    def stop(self):
        """Stop streaming to the writer; it finishes in the background. Returns it."""
        writer, self.writer = self.writer, None
        if writer is not None:
            writer.stop()
        return writer

    def close(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.stop()
            stream.close()
        self.level = 0.0
