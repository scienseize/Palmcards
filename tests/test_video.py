"""Video of a take, recorded like its audio: never blocks, keeps real
timestamps on the app clock, records its gaps, survives a crash. Temporary
folders only; the software encoder (mpeg4), which every PyAV build has."""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av")

from palmcards import data  # noqa: E402
from palmcards.session import Session, recover_all  # noqa: E402
from palmcards.video import VideoWriter, probe, video_info  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CODEC = "mpeg4"
SIZE = (64, 48)


def frame(i: int) -> np.ndarray:
    """A frame whose brightness says which it is."""
    return np.full((SIZE[1], SIZE[0], 3), (i * 16) % 256, np.uint8)


def notes_session(tmp_path) -> Session:
    path = tmp_path / "talk.md"
    path.write_text("# One\n\nHello there friend. Good night all.\n")
    return Session.create(path, root=tmp_path / "sessions")


def pts_of(path: Path) -> list[int]:
    """Each frame's time in the file, in ms (the muxer keeps its own time base)."""
    with av.open(str(path)) as box:
        stream = box.streams.video[0]
        return sorted(round(p.pts * stream.time_base * 1000) for p in box.demux(stream) if p.pts is not None)


def test_frames_keep_their_real_times_on_the_app_clock(tmp_path):
    times = [10.0, 10.033, 10.071, 10.2, 10.234, 10.5, 11.1]  # an uneven frame rate, as the loop gives
    w = VideoWriter(tmp_path, 1, SIZE, codec=CODEC)
    for i, t in enumerate(times):
        w.push(frame(i), t)
    w.push(frame(99), 11.1)  # the same frame again: nothing added
    w.stop()
    assert w.wait(10) and w.state == "saved" and not w.part.exists()
    assert pts_of(tmp_path / w.file) == [round((t - 10.0) * 1000) for t in times]
    s = w.summary()
    assert (s["frames"], s["dropped"], s["t_first"], s["duration_s"], s["codec"]) == (7, 0, 10.0, 1.1, CODEC)
    assert video_info(tmp_path / w.file)["frames"] == 7


def test_a_full_queue_drops_frames_and_records_the_gap_without_waiting(tmp_path, monkeypatch):
    w = VideoWriter(tmp_path, 1, SIZE, codec=CODEC)
    real_put = w._queue.put_nowait
    full = [False]

    def put_nowait(item):
        if full[0]:
            raise __import__("queue").Full
        real_put(item)
    monkeypatch.setattr(w._queue, "put_nowait", put_nowait)
    w.push(frame(0), 1.0)
    full[0] = True
    t0 = time.perf_counter()
    for i in range(1, 4):
        w.push(frame(i), 1.0 + i / 30)
    assert time.perf_counter() - t0 < 0.05  # never waits
    full[0] = False
    w.push(frame(4), 1.2)
    w.stop()
    assert w.wait(10)
    s = w.summary()
    assert s["frames"] == 2 and s["dropped"] == 3 and s["gaps"] == [[round(1 + 1 / 30, 3), 3]]


def test_no_encoder_means_a_failed_video_and_no_file(tmp_path):
    w = VideoWriter(tmp_path, 1, SIZE, codec="no_such_codec")
    w.push(frame(0), 1.0)
    w.stop()
    assert w.wait(10) and w.state == "failed" and w.error
    assert not list(tmp_path.glob("take-01.mp4*"))


def test_sizes_are_even_and_scaled():
    w = VideoWriter(Path("/nonexistent"), 1, (1281, 721), codec=CODEC, scale=0.75)
    assert w.size == (960, 540)
    w.stop()
    w.wait(10)


def test_probe_finds_a_working_encoder():
    assert probe((CODEC,)) == CODEC
    assert probe(("no_such_codec",)) is None


def test_take_numbers_skip_an_existing_video(tmp_path):
    session = notes_session(tmp_path)
    session._ensure_written()
    (session.dir / "take-03.mp4").write_bytes(b"")
    assert session.begin_take() == 4


CRASHER = """
import sys, time
sys.path.insert(0, {root!r})
import numpy as np
from pathlib import Path
from palmcards.session import Session
from palmcards.recording import TakeWriter
from palmcards.video import VideoWriter
path = Path({tmp!r}) / "talk.md"
session = Session.create(path, root=Path({tmp!r}) / "sessions")
n = session.begin_take()
w = TakeWriter(session.dir, n, 16000, {{"started": "2026-09-25T10:00:00.000", "requested_t": 3.0, "drill": None,
                                         "revision": session.current_revision}})
v = VideoWriter(session.dir, n, (64, 48), codec="mpeg4")
w.mark_section(3.0, 0)
k = 0
while True:
    t = 3.0 + k / 30
    v.push(np.full((48, 64, 3), k % 256, np.uint8), t)
    if k == 0:
        w.meta["video"] = {{"state": "recording", "file": v.file, "t_first": 3.0, "codec": v.codec}}
    if k % 2 == 0:
        w.push(np.full(1066, 0.25, np.float32), first_t=3.0 if k == 0 else None, source="adc")
    k += 1
    if k == 30 * 3:  # three seconds in: tell the parent, then keep recording until killed
        print(session.dir, flush=True)
    time.sleep(1 / 30)
"""


def test_a_killed_take_keeps_its_video_up_to_the_last_keyframe(tmp_path):
    (tmp_path / "talk.md").write_text("# One\n\nHello there friend. Good night all.\n")
    proc = subprocess.Popen([sys.executable, "-c", CRASHER.format(root=str(ROOT), tmp=str(tmp_path))],
                            stdout=subprocess.PIPE, text=True)
    folder = Path(proc.stdout.readline().strip())
    time.sleep(0.3)
    os.kill(proc.pid, signal.SIGKILL)  # no chance to finish anything
    proc.wait()
    assert (folder / "take-01.mp4.part").exists()

    lines = recover_all(tmp_path / "sessions")
    assert len(lines) == 1 and "take 1 recovered" in lines[0]
    take = Session.load(folder).take(1)
    video = take.video
    assert video["state"] == "interrupted" and video["recovered"] and video["t_first"] == 3.0
    assert video["frames"] >= 60 and video["duration_s"] >= 2.0  # everything up to the last keyframe
    assert (folder / "take-01.mp4").exists() and not (folder / "take-01.mp4.part").exists()


def test_finish_take_keeps_the_video(tmp_path):
    from palmcards.recording import TakeWriter

    session = notes_session(tmp_path)
    n = session.begin_take()
    w = TakeWriter(session.dir, n, 16000, {"started": "2026-09-25T10:00:00.000", "requested_t": 1.0, "drill": None,
                                           "revision": session.current_revision})
    w.push(np.zeros(1024, np.float32), first_t=1.0, source="adc")
    w.meta["video"] = {"state": "saved", "file": "take-01.mp4", "frames": 30}
    w.stop()
    assert w.wait(5)
    take = session.finish_take(w.manifest())
    assert take.video == {"state": "saved", "file": "take-01.mp4", "frames": 30}
    assert Session.load(session.dir).take(1).video["frames"] == 30


def test_drop_video_deletes_only_videos_and_the_take_says_so(tmp_path, capsys):
    notes = tmp_path / "talk.md"
    notes.write_text("Hello there friend.")
    root = tmp_path / "sessions"
    when = datetime(2026, 9, 1, 10, 0, 0)
    session = Session.create(notes, root=root, now=when)
    session.add_take(np.zeros(800, np.float32), 8000, 1.0, when, [(0.0, 0)])
    session.take(1).video = {"state": "saved", "file": "take-01.mp4", "frames": 3}
    session.save()
    (session.dir / "take-01.mp4").write_bytes(b"x" * 1000)
    session.release()

    assert data.main(["drop-video", session.dir.name], root) == 0
    assert "would delete" in capsys.readouterr().out and (session.dir / "take-01.mp4").exists()
    assert data.main(["drop-video", session.dir.name, "--yes"], root) == 0
    assert not (session.dir / "take-01.mp4").exists() and (session.dir / "take-01.wav").exists()
    assert Session.load(session.dir).take(1).video["state"] == "deleted"
    with pytest.raises(SystemExit):
        data.main(["drop-video"], root)  # a RUN or --older-than, not neither


# --- replay (stage 2) ----------------------------------------------------------

from palmcards.video import VideoReader  # noqa: E402


def recorded_video(folder: Path, t_first: float = 100.0, n: int = 30, step: float = 0.1, size=SIZE) -> Path:
    """n flat frames `step` s apart from t_first, frame i at brightness i * 8."""
    w = VideoWriter(folder, 1, size, codec=CODEC)
    for i in range(n):
        w.push(np.full((size[1], size[0], 3), i * 8, np.uint8), t_first + i * step)
        time.sleep(0.005)  # paced like a camera: the queue never fills
    w.stop()
    assert w.wait(10) and w.state == "saved" and w.dropped == 0
    return folder / w.file


def wait_frame(reader: VideoReader, t: float, want: int, timeout: float = 5.0) -> np.ndarray:
    """frame_at(t) once the reader has decoded that far (it decodes in the background)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        image = reader.frame_at(t)
        if image is not None and abs(int(image.mean()) - want * 8) <= 4:
            return image
        time.sleep(0.01)
    raise AssertionError(f"no frame {want} at {t}: got {None if image is None else image.mean()}")


def test_a_reader_gives_the_frame_for_a_moment_from_a_seek_in_the_middle(tmp_path):
    path = recorded_video(tmp_path)
    reader = VideoReader(path, 100.0).start(101.05)
    wait_frame(reader, 101.05, 10)  # the newest frame at or before it (frame 10 is at 101.0)
    wait_frame(reader, 101.52, 15)
    wait_frame(reader, 102.29, 22)
    wait_frame(reader, 150.0, 29)  # past the end: the last frame stays
    reader.close()
    assert reader.error is None


def test_a_reader_scales_to_the_camera_frame(tmp_path):
    path = recorded_video(tmp_path, size=(64, 48))
    reader = VideoReader(path, 100.0, size=(128, 96)).start(100.0)
    assert wait_frame(reader, 100.0, 0).shape == (96, 128, 3)
    reader.close()


def test_a_missing_video_gives_no_frames_and_says_why(tmp_path):
    reader = VideoReader(tmp_path / "take-09.mp4", 0.0).start(1.0)
    reader._thread.join(5)
    assert reader.frame_at(1.0) is None and reader.error
    reader.close()
