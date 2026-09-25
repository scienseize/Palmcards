"""Camera and microphone capture.

Frames are mirrored once here, at capture. Everything downstream (rendering,
hit-testing) works in this flipped coordinate space.

A background thread reads the camera continuously, so the camera's own rate
(`Camera.fps`) is measured independently of how long each frame takes to
process. macOS webcams drop to ~15 fps on their own in dim light.

Audio is buffered in memory per take (AudioRecorder) and saved as WAV by
`palmcards.session`.
"""

import math
import threading
import time
from collections import deque

import cv2
import numpy as np


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
    start() begins keeping blocks, stop() returns the take, close() releases
    the microphone so macOS's recording indicator goes off between takes.
    Without Microphone permission macOS delivers silence, not an error; the
    take's peak shows it.
    """

    def __init__(self, device: int | str | None = None):
        import sounddevice as sd

        self._sd = sd
        self.device = device
        self.rate = int(sd.query_devices(device, kind="input")["default_samplerate"])
        self.level = 0.0  # last block's loudness, 0 (-60 dBFS or less) .. 1 (full scale)
        self.overflows = 0  # blocks the callback reported as dropped
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._frames = 0
        self._recording = False
        self._stream = None

    def open(self) -> None:
        """Raises sounddevice.PortAudioError if the microphone can't be opened."""
        if self._stream is None:
            stream = self._sd.InputStream(device=self.device, samplerate=self.rate, channels=1,
                                          dtype="float32", callback=self._callback)
            stream.start()
            self._stream = stream

    def _callback(self, indata, frames, _time, status) -> None:
        if status.input_overflow:
            self.overflows += 1
        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block * block))) if len(block) else 0.0
        self.level = min(1.0, max(0.0, (20 * math.log10(max(rms, 1e-9)) + 60) / 60))
        with self._lock:
            if self._recording:
                self._blocks.append(block)
                self._frames += len(block)

    def start(self) -> None:
        with self._lock:
            self._blocks, self._frames, self._recording = [], 0, True
        self.overflows = 0

    @property
    def seconds(self) -> float:
        """Length of the take so far."""
        return self._frames / self.rate

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            blocks, self._blocks, self._frames = self._blocks, [], 0
        return np.concatenate(blocks) if blocks else np.zeros(0, np.float32)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.level = 0.0
