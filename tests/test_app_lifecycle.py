"""The app's resource lifetimes and take lifecycle, with fake devices:
failures mid-take keep the audio, everything opened is closed once, and
the error that caused the shutdown is the one raised."""

import threading
import time

import numpy as np
import pytest

import main
from palmcards.capture import CameraError
from palmcards.gestures import GestureEvent, GestureLog, ModeMachine
from palmcards.notes import notes_from_bytes
from palmcards.session import Session

NOTES = b"# One\n\nHello there friend. Good night all.\n\n# Two\n\nSee you soon.\n"


class FakeCamera:
    fps = 30.0

    def __init__(self, fail_at=None, error=None):
        self.frames, self.released, self.fail_at, self.error = 0, 0, fail_at, error

    def read(self):
        self.frames += 1
        time.sleep(0.01)
        if self.fail_at and self.frames >= self.fail_at:
            raise self.error or CameraError("Camera stopped delivering frames.")
        return np.zeros((360, 640, 3), np.uint8)

    def release(self):
        self.released += 1


class FakeTracker:
    latency_ms = 0.0

    def __init__(self, close_error=None):
        self.closed, self.close_error = 0, close_error

    def submit(self, frame, t):
        return True

    def poll(self):
        return None

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


class FakeRecorder:
    """Pushes 20 ms blocks from a thread, like the sounddevice callback."""
    rate = 16000

    def __init__(self, clock):
        self.clock, self.level, self.writer = clock, 0.5, None
        self.opened = self.closed = 0
        self._thread = None
        self._stop = threading.Event()

    def open(self):
        self.opened += 1

    def start(self, writer):
        self.writer = writer
        self._stop.clear()
        self._thread = threading.Thread(target=self._feed, args=(writer,), daemon=True)
        self._thread.start()

    def _feed(self, writer):
        first = True
        while not self._stop.is_set():
            block = np.full(320, 0.2, np.float32)
            writer.push(block, *((self.clock(), "adc") if first else ()))
            first = False
            if (tap := getattr(self, "tap", None)) is not None:
                tap(block, writer.first_sample_t + writer.enqueued / self.rate)
            time.sleep(0.02)

    @property
    def seconds(self):
        return self.writer.seconds if self.writer else 0.0

    def stop(self):
        writer, self.writer = self.writer, None
        self._stop.set()
        if self._thread:
            self._thread.join()
        if writer:
            writer.stop()
        return writer

    def close(self):
        self.closed += 1


class FakeLive:
    """A live stream that is ready at once and hears nothing."""
    state, errors, where = "ready", [], "fake"

    @property
    def ready(self):
        return True

    def reset(self):
        pass

    def feed(self, samples, t_end):
        pass

    def poll(self):
        return []

    def close(self):
        self.state = "closed"


class FakeSupervisor:
    def __init__(self):
        self.jobs, self.pending, self.log = [], [], []

    def submit(self, session_dir, job):
        self.jobs.append(job)
        return True

    def poll(self):
        return []

    def failed(self):
        return []

    def retry_failed(self):
        return 0

    def warm(self):
        self.warmed = True

    def close(self, timeout=None):
        return []


def scripted(script):
    class Modes(ModeMachine):
        """The real machine, but mode changes come from the script: {tick: (event, mode)}."""

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.n = 0

        def update(self, hands, t):
            return []

        def tick(self, t):
            self.n += 1
            if self.n in script:
                kind, self.mode = script[self.n]
                return [GestureEvent(kind, t)]
            return []

    return Modes


TAKE = {2: ("count_in", "count_in"), 3: ("take_start", "rehearse")}


class Rig:
    def __init__(self, tmp_path, monkeypatch, script=TAKE, camera=None, tracker=None, keys=None,
                 tracker_factory=None, live=None, vision=None, gaze_check=False, video=False, video_writer=None):
        self.tmp = tmp_path
        self.gaze_check, self.video = gaze_check, video
        self.camera = camera or FakeCamera()
        self.tracker = tracker or FakeTracker()
        self.recorders = []
        self.transcribers = []
        self.destroyed = 0
        keys = keys or {}
        frame = {"n": 0}

        def wait_key():
            frame["n"] += 1
            k = keys.get(frame["n"], 255)
            if isinstance(k, BaseException):
                raise k
            return k

        def recorder(clock):
            self.recorders.append(FakeRecorder(clock))
            return self.recorders[-1]

        def destroy():
            self.destroyed += 1

        def supervisor():
            self.transcribers.append(FakeSupervisor())
            return self.transcribers[-1]

        monkeypatch.setattr(main, "ModeMachine", scripted(script))
        monkeypatch.setattr(main, "Supervisor", supervisor)
        self.devices = main.Devices(
            camera=lambda: self.camera, tracker=tracker_factory or (lambda: self.tracker), recorder=recorder,
            log=lambda: GestureLog(tmp_path / "log.jsonl"), named_window=lambda *a: None, show=lambda *a: None,
            live=live or (lambda language, clock, hints: FakeLive()), vision=vision or (lambda: None), backdrop=lambda strength: None,
            wait_key=wait_key, window_open=lambda name: True, destroy_windows=destroy, pump=lambda: None,
            pick=lambda: None, fullscreen=lambda name, on: None)
        if video_writer is not None:
            self.devices.video = video_writer
        self.notes_path = tmp_path / "talk.md"
        self.notes_path.write_bytes(NOTES)

    def run(self):
        return main.run(self.notes_path, notes_from_bytes(NOTES, self.notes_path), NOTES, devices=self.devices,
                        sessions_root=self.tmp / "sessions", prefs_file=self.prefs_file, gaze_check=self.gaze_check,
                        video=self.video)

    @property
    def prefs_file(self):
        return self.tmp / "prefs.json"  # never the real preferences

    def session(self) -> Session:
        (folder,) = (self.tmp / "sessions").glob("2*")
        return Session.load(folder)

    def assert_closed_once(self):
        assert self.camera.released == 1 and self.tracker.closed == 1 and self.destroyed == 1
        assert all(r.closed >= 1 and r.writer is None for r in self.recorders)
        session = self.session()
        session.acquire()  # the lock was let go
        assert not list(session.dir.glob("*.recording.json")) and not list(session.dir.glob("*.part"))


def test_camera_failure_mid_take_keeps_the_take_as_interrupted(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, camera=FakeCamera(fail_at=30))
    with pytest.raises(CameraError):
        rig.run()
    take = rig.session().take(1)
    assert take.status == "interrupted" and take.duration_s > 0.1 and take.peak == pytest.approx(0.2, abs=1e-3)
    assert rig.transcribers[0].jobs == []  # an interrupted take is not analysed as if it were whole
    rig.assert_closed_once()


def test_a_drawing_error_mid_take_is_raised_and_the_take_kept(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = main.TextOverlay.draw

    def draw(self, frame, state):
        calls["n"] += 1
        if calls["n"] == 25:
            raise RuntimeError("font exploded")
        return real(self, frame, state)

    monkeypatch.setattr(main.TextOverlay, "draw", draw)
    rig = Rig(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="font exploded"):
        rig.run()
    assert rig.session().take(1).status == "interrupted"
    rig.assert_closed_once()


def test_ctrl_c_mid_take_keeps_the_take_as_interrupted(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, keys={25: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        rig.run()
    assert rig.session().take(1).status == "interrupted"
    rig.assert_closed_once()


def test_quitting_mid_take_saves_it_normally_and_analyses_it(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, keys={25: ord("q")})
    assert rig.run() == 0
    take = rig.session().take(1)
    assert take.status == "saved" and take.capture["clock"] == "adc"
    (job,) = rig.transcribers[0].jobs
    assert job["take"] == 1
    rig.assert_closed_once()


def test_a_failing_cleanup_step_does_not_skip_the_others_or_hide_the_cause(tmp_path, monkeypatch, capsys):
    rig = Rig(tmp_path, monkeypatch, camera=FakeCamera(fail_at=30), tracker=FakeTracker(RuntimeError("stuck")))
    with pytest.raises(CameraError):  # the original error, not the cleanup's
        rig.run()
    assert "error while closing hand tracking" in capsys.readouterr().err
    assert rig.session().take(1).status == "interrupted"
    rig.assert_closed_once()


def test_an_opening_failure_closes_what_was_already_open(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("hand model missing")

    rig = Rig(tmp_path, monkeypatch, tracker_factory=broken)
    with pytest.raises(RuntimeError, match="hand model missing"):
        rig.run()
    assert rig.camera.released == 1 and not (tmp_path / "sessions").exists()


def test_stop_twice_makes_one_take(tmp_path, monkeypatch):
    script = {**TAKE, 20: ("take_stop", "review"), 21: ("take_stop", "review")}
    rig = Rig(tmp_path, monkeypatch, script=script, keys={40: ord("q")})
    assert rig.run() == 0
    assert [t.number for t in rig.session().takes] == [1]
    assert len(rig.transcribers[0].jobs) == 1
    rig.assert_closed_once()


def test_a_cancelled_count_in_records_nothing(tmp_path, monkeypatch):
    script = {2: ("count_in", "count_in"), 3: ("count_in_cancel", "prepare")}
    rig = Rig(tmp_path, monkeypatch, script=script, keys={10: ord("q")})
    assert rig.run() == 0
    assert rig.recorders[0].opened == 1 and rig.recorders[0].closed >= 1
    assert not (tmp_path / "sessions").exists()


def software_video(folder, number, size):
    from palmcards.video import VideoWriter

    return VideoWriter(folder, number, size, codec="mpeg4")  # every PyAV build has it


def test_with_video_on_a_take_keeps_its_video_beside_the_audio(tmp_path, monkeypatch):
    pytest.importorskip("av")
    rig = Rig(tmp_path, monkeypatch, keys={25: ord("q")}, video=True, video_writer=software_video)
    assert rig.run() == 0
    take = rig.session().take(1)
    video = take.video
    assert take.status == "saved" and video["state"] == "saved" and video["codec"] == "mpeg4"
    assert video["frames"] >= 15 and video["dropped"] == 0 and video["file"] == "take-01.mp4"
    assert (rig.session().dir / "take-01.mp4").exists()
    assert abs(video["t_first"] - take.t_start) < 0.5  # on the same clock as the audio
    rig.assert_closed_once()


def test_a_camera_failure_mid_take_keeps_the_video_too(tmp_path, monkeypatch):
    pytest.importorskip("av")
    rig = Rig(tmp_path, monkeypatch, camera=FakeCamera(fail_at=30), video=True, video_writer=software_video)
    with pytest.raises(CameraError):
        rig.run()
    take = rig.session().take(1)
    assert take.status == "interrupted" and take.video["state"] == "saved" and take.video["frames"] > 0
    rig.assert_closed_once()


def test_without_video_a_take_has_none(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, keys={25: ord("q")})
    assert rig.run() == 0
    assert rig.session().take(1).video is None and not list(rig.session().dir.glob("*.mp4"))


def test_video_asked_for_but_unavailable_is_said_on_the_take(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "video_unavailable", lambda: "PyAV is not installed")
    rig = Rig(tmp_path, monkeypatch, keys={25: ord("q")}, video=True)
    assert rig.run() == 0
    assert rig.session().take(1).video == {"state": "off", "reason": "PyAV is not installed"}
    assert "video off: PyAV is not installed" in capsys.readouterr().err


def test_the_video_preference_turns_it_on_without_the_flag(tmp_path, monkeypatch):
    pytest.importorskip("av")
    rig = Rig(tmp_path, monkeypatch, keys={25: ord("q")}, video_writer=software_video)
    rig.prefs_file.write_text('{"video": true}\n')
    assert rig.run() == 0
    assert rig.session().take(1).video["state"] == "saved"


def test_a_missing_model_file_is_one_line_and_closes_what_was_open(tmp_path, monkeypatch, capsys):
    def missing():
        raise FileNotFoundError("models/gesture_recognizer.task missing: run python scripts/download_models.py")

    rig = Rig(tmp_path, monkeypatch, tracker_factory=missing, keys={1: ord("q")})  # q closes the error card
    code = main.run_reporting(rig.notes_path, notes_from_bytes(NOTES, rig.notes_path), NOTES, devices=rig.devices,
                              sessions_root=rig.tmp / "sessions", prefs_file=rig.prefs_file)
    err = capsys.readouterr().err
    assert code == 1 and "gesture_recognizer.task" in err and "download_models.py" in err
    assert "Traceback" not in err and rig.camera.released == 1


def test_no_camera_says_so_on_a_card_in_the_window_until_q(tmp_path, monkeypatch, capsys):
    def no_camera():
        raise CameraError("Could not open the camera.")

    rig = Rig(tmp_path, monkeypatch, keys={1: 255, 2: ord("q")})
    rig.devices.camera = no_camera
    shown = []
    rig.devices.show = lambda name, frame: shown.append(frame.copy())
    code = main.run_reporting(rig.notes_path, notes_from_bytes(NOTES, rig.notes_path), NOTES, devices=rig.devices,
                              sessions_root=rig.tmp / "sessions", prefs_file=rig.prefs_file)
    assert code == 1 and "Could not open the camera" in capsys.readouterr().err
    assert len(shown) >= 3 and shown[-1].any()  # OPENING THE CAMERA, then the error card twice (q on the 2nd key)
    assert rig.destroyed == 2  # run's window, then the card's


def test_a_slow_collection_is_seen_and_handed_over_once():
    import main

    watch = main.GcWatch()
    watch("start", {"generation": 2})
    import time
    time.sleep(main.GC_SLOW_MS / 1000 + 0.002)
    watch("stop", {"generation": 2})
    watch("start", {"generation": 0})
    watch("stop", {"generation": 0})  # quick: not kept
    slow = watch.drain()
    assert len(slow) == 1 and slow[0][1] == 2 and slow[0][0] >= main.GC_SLOW_MS
    assert watch.drain() == []


class FakeLiveReading:
    """A live stream that hears the whole of NOTES read, once, then nothing."""
    state, errors, where = "ready", [], "fake"

    def __init__(self, clock):
        self.clock, self.said = clock, False

    @property
    def ready(self):
        return True

    def reset(self):
        pass

    def feed(self, samples, t_end):
        pass

    def poll(self):
        from palmcards.asr import LiveWord
        if self.said:
            return []
        self.said = True
        now = self.clock()
        words = "Hello there friend. Good night all. See you soon.".split()
        return [LiveWord(w, now - 3 + 0.3 * i, now - 2.8 + 0.3 * i, 0.9, confirmed_at=now) for i, w in enumerate(words)]

    def close(self):
        self.state = "closed"


class QuietingRecorder(FakeRecorder):
    """Speech for the take's first second, then the room's quiet."""

    @property
    def level(self):
        return 0.8 if self.writer is not None and self.writer.seconds < 1.0 else 0.1

    @level.setter
    def level(self, value):
        pass


def test_a_take_ends_itself_in_quiet_after_the_last_sentence(tmp_path, monkeypatch):
    from dataclasses import replace

    from palmcards import follow
    monkeypatch.setattr(follow, "REHEARSE", replace(follow.REHEARSE, end_silence_s=0.3))
    rig = Rig(tmp_path, monkeypatch, live=lambda language, clock, hints: FakeLiveReading(clock), keys={200: ord("q")})
    rig.devices.recorder = lambda clock: rig.recorders.append(QuietingRecorder(clock)) or rig.recorders[-1]
    assert rig.run() == 0
    (take,) = rig.session().takes
    assert take.duration_s < 3.0  # stopped well before q, at frame 200
    log = (tmp_path / "log.jsonl").read_text()
    assert '"auto_stop"' in log


def test_a_take_without_the_end_reached_does_not_end_itself(tmp_path, monkeypatch):
    from dataclasses import replace

    from palmcards import follow
    monkeypatch.setattr(follow, "REHEARSE", replace(follow.REHEARSE, end_silence_s=0.3))
    rig = Rig(tmp_path, monkeypatch, keys={120: ord("q")})  # FakeLive hears nothing
    assert rig.run() == 0
    assert '"auto_stop"' not in (tmp_path / "log.jsonl").read_text()
