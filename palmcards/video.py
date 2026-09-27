"""Video of a take, recorded like its audio (opt-in: preferences `video`,
or main.py --video).

    codec = probe()                                  # the first of VIDEO.codecs that opens, or None
    writer = VideoWriter(folder, number, (w, h))     # take-NN.mp4(.part)
    writer.push(frame, t)                            # every frame of the take, t on the app clock
    writer.stop(); writer.wait(); writer.summary()   # finishes in the background

The frame loop hands each clean, mirrored camera frame (nothing drawn on
it) to a writer thread through a bounded queue and never waits: when the
queue is full the frame is dropped and the gap is recorded, never hidden.
Each frame keeps its capture time on the app clock: its timestamp in the
file is the time since the first frame, in milliseconds, so the file has a
variable frame rate and a moment `p` into it is app time `t_first + p`, on
the same clock as the audio (`t_start`), the gesture log and the transcript.

The file is a fragmented MP4 (a fragment at each keyframe, one every
VIDEO.keyframe_s), written as take-NN.mp4.part and renamed on finish: if the
app dies mid-take, what was written up to the last keyframe is still a
playable file (Session.recover renames it and keeps the take's video).

States: recording -> finalizing -> saved, or failed (the error is kept; the
take's audio is unaffected). There is no video of a take the app didn't
record with video on.
"""

from __future__ import annotations

import functools
import io
import queue
import threading
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from palmcards.config import VIDEO

STOP = object()
TIME_BASE = Fraction(1, 1000)  # timestamps in ms since the first frame
# A fragment at each keyframe, handed to the system at once (not held in the
# muxer's buffer), so a crash leaves a playable file up to the last keyframe.
MP4_OPTIONS = {"movflags": "frag_keyframe+empty_moov+default_base_moof", "flush_packets": "1"}


def _open_stream(container, codec: str, size: tuple[int, int], bitrate: int):
    import av  # noqa: F401 - the caller has it

    stream = container.add_stream(codec, rate=int(VIDEO.fps_guess))
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    stream.bit_rate = bitrate
    stream.time_base = TIME_BASE
    stream.codec_context.time_base = TIME_BASE
    stream.codec_context.gop_size = max(1, round(VIDEO.keyframe_s * VIDEO.fps_guess))
    return stream


@functools.cache
def probe(codecs: tuple[str, ...] = VIDEO.codecs) -> str | None:
    """The first encoder in `codecs` that really encodes (a couple of tiny
    frames into memory), or None: no PyAV, or none of them works."""
    try:
        import av
    except ImportError:
        return None
    for name in codecs:
        try:
            with av.open(io.BytesIO(), "w", format="mp4") as box:
                stream = _open_stream(box, name, (64, 64), 200_000)
                for i in range(2):
                    frame = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), np.uint8), format="bgr24")
                    frame.pts, frame.time_base = i * 33, TIME_BASE
                    for packet in stream.encode(frame):
                        box.mux(packet)
                for packet in stream.encode():
                    box.mux(packet)
            return name
        except Exception:  # noqa: BLE001 - not built in, or no hardware for it: the next one
            continue
    return None


def unavailable() -> str:
    """Why there is no video, for the terminal and the take ("" if there is)."""
    try:
        import av  # noqa: F401
    except ImportError:
        return "PyAV is not installed (uv pip install --python .venv/bin/python av \"numpy<2\")"
    return "" if probe() else f"none of the encoders {', '.join(VIDEO.codecs)} works here"


def even(n: float) -> int:
    """H.264 wants even frame sizes."""
    return max(2, int(round(n)) // 2 * 2)


class VideoWriter:
    """Streams one take's video to take-NN.mp4(.part); see the module doc.

    push() is for the frame loop and never blocks; the rest is for the app's thread."""

    def __init__(self, folder: Path, number: int, size: tuple[int, int], codec: str | None = None,
                 bitrate: int = VIDEO.bitrate, scale: float = VIDEO.scale):
        self.folder, self.number = Path(folder), number
        self.file = f"take-{number:02d}.mp4"
        self.part = self.folder / f"{self.file}.part"
        self.codec = codec or probe()
        self.bitrate = bitrate
        self.size = (even(size[0] * scale), even(size[1] * scale))
        self.state = "recording"
        self.error: str | None = None
        self.t_first: float | None = None
        self.t_last: float | None = None
        self.pushed = 0  # frames handed over (kept or dropped)
        self.frames = 0  # frames in the file
        self.dropped = 0
        self.gaps: list[list[float]] = []  # [app time of the first frame dropped, frames] per run of drops
        self.done = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=max(2, round(VIDEO.queue_s * VIDEO.fps_guess)))
        self._thread = threading.Thread(target=self._run, name=f"take-{number:02d}-video", daemon=True)
        self._thread.start()

    # --- the frame loop's side ------------------------------------------------

    def push(self, frame: np.ndarray, t: float) -> None:
        """A frame and its capture time (app clock). Never blocks: a full queue
        drops the frame and records the gap. The caller hands over a frame it
        won't draw on (a copy)."""
        if self.state != "recording" or (self.t_last is not None and t <= self.t_last):
            return  # the same frame twice, or time running backwards: nothing to add
        if self.t_first is None:
            self.t_first = t
        self.t_last = t
        self.pushed += 1
        try:
            self._queue.put_nowait((t, frame))
        except queue.Full:
            self.dropped += 1
            if self.gaps and self.gaps[-1][2] == self.pushed - 1:
                self.gaps[-1][1] += 1  # one gap, however many frames in a row
                self.gaps[-1][2] = self.pushed
            else:
                self.gaps.append([round(t, 3), 1, self.pushed])

    # --- the app's side ---------------------------------------------------------

    def stop(self) -> None:
        """Finish in the background: drain, flush the encoder, publish. Never blocks."""
        if self.state == "recording":
            self.state = "finalizing"
            try:
                self._queue.put_nowait(STOP)
            except queue.Full:
                threading.Thread(target=self._queue.put, args=(STOP,), daemon=True).start()

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def summary(self) -> dict:
        """What to note on the take (TakeRecord.video)."""
        info = {"state": self.state, "file": self.file, "t_first": None if self.t_first is None else round(self.t_first, 3),
                "frames": self.frames, "dropped": self.dropped, "gaps": [g[:2] for g in self.gaps],
                "codec": self.codec, "width": self.size[0], "height": self.size[1], "bitrate": self.bitrate}
        if self.t_first is not None and self.t_last is not None:
            info["duration_s"] = round(self.t_last - self.t_first, 3)
        if self.error:
            info["error"] = self.error
        return info

    # --- the writer thread --------------------------------------------------------

    def _run(self) -> None:
        box = stream = None
        try:
            import av

            if self.codec is None:
                raise RuntimeError(unavailable() or "no video encoder")
            box = av.open(str(self.part), "w", format="mp4", options=MP4_OPTIONS)
            stream = _open_stream(box, self.codec, self.size, self.bitrate)
        except Exception as exc:  # noqa: BLE001 - no PyAV, no encoder, no disk: the take goes on without video
            self._fail(exc)
        last_key = None
        while True:
            item = self._queue.get()
            if item is STOP:
                break
            if self.error is not None:
                continue  # drain: the frame loop must never find the queue stuck full
            t, image = item
            try:
                if image.shape[1::-1] != self.size:
                    image = cv2.resize(image, self.size, interpolation=cv2.INTER_AREA)
                frame = av.VideoFrame.from_ndarray(image, format="bgr24")
                frame.pts, frame.time_base = round((t - self.t_first) * 1000), TIME_BASE
                if last_key is None or t - last_key >= VIDEO.keyframe_s:  # a keyframe by time, whatever the rate
                    frame.pict_type = av.video.frame.PictureType.I
                    last_key = t
                for packet in stream.encode(frame):
                    box.mux(packet)
                self.frames += 1
            except Exception as exc:  # noqa: BLE001 - encoder or disk: keep what was written
                self._fail(exc)
        self._finish(box, stream)

    def _fail(self, exc: BaseException) -> None:
        if self.error is None:
            self.error = f"{type(exc).__name__}: {exc}"

    def _finish(self, box, stream) -> None:
        try:
            if box is not None:
                try:
                    if self.error is None:
                        for packet in stream.encode():  # what the encoder still holds
                            box.mux(packet)
                except Exception as exc:  # noqa: BLE001
                    self._fail(exc)
                try:
                    box.close()
                except Exception as exc:  # noqa: BLE001
                    self._fail(exc)
            if self.part.exists():
                if self.frames:
                    self.part.replace(self.folder / self.file)
                else:
                    self.part.unlink()  # not a single frame: no file
            self.state = "failed" if self.error else "saved"
        finally:
            self.done.set()


def video_info(path: Path) -> dict:
    """Frames and length of a video file (a recovered take's), from its
    packets with PyAV (nothing decoded)."""
    import av

    with av.open(str(path)) as box:
        stream = box.streams.video[0]
        pts = [p.pts for p in box.demux(stream) if p.pts is not None]
        tb = float(stream.time_base)
        return {"frames": len(pts), "duration_s": round((max(pts) - min(pts)) * tb, 3) if pts else 0.0,
                "codec": stream.codec_context.name, "width": stream.width, "height": stream.height}


class VideoReader:
    """A take's video from an app time on (Review's replay), decoded ahead in
    a background thread so the frame loop never waits on a seek:

        reader = VideoReader(path, t_first, size)   # t_first: the take's video.t_first
        reader.start(t_app)                          # from here (seeks to the keyframe before it)
        reader.frame_at(t_app)                       # the newest frame at or before t_app, or None
        reader.close()

    Frames come back as BGR at `size` (the camera's, if the video was
    recorded smaller). While the seek is still decoding its way from the
    keyframe to `t_app`, frame_at gives the latest frame decoded so far, a
    moment early, rather than nothing. Past the video's end it keeps giving
    the last frame."""

    def __init__(self, path: Path, t_first: float, size: tuple[int, int] | None = None,
                 ahead_s: float = VIDEO.read_ahead_s):
        self.path, self.t_first, self.size, self.ahead_s = Path(path), t_first, size, ahead_s
        self.error: str | None = None
        self._cond = threading.Condition()
        self._frames: list[tuple[float, np.ndarray]] = []  # decoded, in time order, not yet passed
        self._last: np.ndarray | None = None  # the newest frame given out
        self._want = 0.0  # the app time the frame loop has asked for
        self._ended = False
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self, t_app: float) -> "VideoReader":
        self._want = t_app
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(t_app,), name="video-reader", daemon=True)
        self._thread.start()
        return self

    def frame_at(self, t_app: float) -> np.ndarray | None:
        with self._cond:
            self._want = t_app
            while len(self._frames) > 1 and self._frames[1][0] <= t_app:
                self._frames.pop(0)
            if self._frames and (self._frames[0][0] <= t_app or self._last is None):
                self._last = self._frames[0][1]  # at or before t_app; or, until one is, the first decoded
            self._cond.notify_all()
            return self._last

    def close(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self, t_app: float) -> None:
        try:
            import av

            with av.open(str(self.path)) as box:
                stream = box.streams.video[0]
                stream.thread_type = "AUTO"
                offset = max(0.0, t_app - self.t_first)
                box.seek(int(offset / stream.time_base), stream=stream, backward=True, any_frame=False)
                for frame in box.decode(stream):
                    if frame.pts is None:
                        continue
                    t = self.t_first + float(frame.pts * stream.time_base)
                    image = frame.to_ndarray(format="bgr24")
                    if self.size is not None and image.shape[1::-1] != tuple(self.size):
                        image = cv2.resize(image, self.size, interpolation=cv2.INTER_LINEAR)
                    with self._cond:
                        if t < t_app and self._frames and self._frames[-1][0] < t_app:
                            self._frames[-1] = (t, image)  # still seeking: keep only the latest before the start
                        else:
                            self._frames.append((t, image))
                        self._cond.notify_all()
                        # Decode ahead only so far: wait for the frame loop to catch up.
                        self._cond.wait_for(lambda: not self._running or t <= self._want + self.ahead_s)
                        if not self._running:
                            return
        except Exception as exc:  # noqa: BLE001 - a missing or broken file: no replay, the audio plays on
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._cond:
                self._ended = True
                self._cond.notify_all()
