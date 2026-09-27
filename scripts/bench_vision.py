"""Face and pose budget (milestone 7, stage 0): which landmarker rates keep the camera near 30 fps?

  python scripts/bench_vision.py [SESSION_DIR --take N] [--seconds S] [--warm S]
                                 [--configs base,f3,p5,f3p5,...] [--face-side PX] [--video] [--json OUT]

Runs the load of a take, in one camera window:
  hands     the gesture recognizer on every frame, the mode machine in Rehearse,
            the notes overlay drawn and shown
  live      the voice follow: a transcribed take (default: the newest one)
            replayed through the live stream in real time, as in bench_live.py,
            looped so one stream runs through the whole benchmark
  recording the microphone streamed to a take file in a temporary folder
            (deleted afterwards), through the app's own writer
  video     with --video: the camera frames recorded too (palmcards.video,
            the app's writer and encoder), into the same temporary folder

and adds MediaPipe Face Landmarker and Pose Landmarker (palmcards.vision) at
frame strides: `f3` = face on every 3rd frame, `p5` = pose on every 5th,
`f3p5` both, `base` neither. Each runs for --seconds after --warm seconds
of warm-up. The MacBook Air has no fan and slows down after a few minutes of
this load whatever the setting, so settings are compared with a baseline at
the same heat: the default interleaves base, f3p5 and f6p15 three times
(about 6 min, warm for most of it). The report gives macOS's thermal state
per setting.

--face-side sets the size face frames are downscaled to (default
BODY.face_max_side; 1280 keeps a 1280x720 camera frame whole).

One face and one pose tracker serve every setting (only their stride
changes): MediaPipe keeps some threads and memory of a closed landmarker, so
making new ones per setting would pile them up.

Sit in front of the camera as you would for a take, face visible, and raise
a hand now and then: a face landmarker with no face only runs its detector,
which costs less, so the report says how often a face and a body were found.
q or Esc stops early. Needs Camera and Microphone permission.

Gate per setting (against the baseline run most recently before it): shown frame rate >= 27 fps
and >= 90% of the baseline's, p90 frame time <= 40 ms, hand latency at most
5 ms over the baseline's, live read p90 at most 25% over the baseline's (reads vary), no
dropped audio.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench_live import Replay, peak_rss_mb, percentile, take_notes  # noqa: E402
from palmcards.audio import resample  # noqa: E402
from palmcards.config import BODY, SPEECH  # noqa: E402
from palmcards.paths import data_dir  # noqa: E402
from palmcards.session import Session, read_wav  # noqa: E402

DEFAULT = "base,f3p5,f6p15,base,f3p5,f6p15,base,f3p5,f6p15,base"
MIN_FPS, BASE_SHARE, MAX_P90_MS, HANDS_OVER_MS, LIVE_OVER = 27.0, 0.9, 40.0, 5.0, 1.25
MIN_FOUND = 0.8  # below this share of results with a face / body, the run didn't have a person in view


THERMAL = ("nominal", "fair", "serious", "critical")


def thermal() -> str | None:
    """macOS's thermal state (NSProcessInfo): nominal, fair, serious or critical; None if unknown."""
    try:
        from Foundation import NSProcessInfo

        return THERMAL[NSProcessInfo.processInfo().thermalState()]
    except Exception:
        return None


def parse_config(name: str) -> tuple[int | None, int | None]:
    """'f3p5' -> (3, 5); 'base' -> (None, None)."""
    if name == "base":
        return None, None
    m = re.fullmatch(r"(?:f(\d+))?(?:p(\d+))?", name)
    if not m or not any(m.groups()):
        raise ValueError(f"not a setting: {name!r} (base, f3, p5, f3p5, ...)")
    return tuple(int(g) if g else None for g in m.groups())


def newest_take(root: Path) -> tuple[Path, int]:
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        try:
            session = Session.load(folder)
        except Exception:
            continue
        for take in sorted(session.takes, key=lambda t: t.number, reverse=True):
            if take.drill is None and take.transcript and (folder / take.transcript).exists():
                return folder, take.number
    raise SystemExit(f"no transcribed take under {root}: record one, or name a session and --take")


class Bench:
    def __init__(self, replay: Replay, recorder, writer, warm: float, seconds: float, face_side: int,
                 video_folder: Path | None = None):
        import cv2

        from palmcards.capture import Camera
        from palmcards.gestures import HandTracker, ModeMachine
        from palmcards.render import TextOverlay, ViewState

        self.cv2 = cv2
        self.replay, self.recorder, self.writer = replay, recorder, writer
        self.warm, self.seconds = warm, seconds
        self.camera = Camera()
        frame = self.camera.read()
        h, w = frame.shape[:2]
        self.size = (w, h)
        self.video = None
        if video_folder is not None:  # the app's video writer, recording every frame
            from palmcards.video import VideoWriter, unavailable

            if why := unavailable():
                raise SystemExit(f"--video: {why}")
            self.video = VideoWriter(video_folder, 1, (w, h))
        self.hands = HandTracker()
        self.modes = ModeMachine((w, h))
        self.modes.enter("rehearse", 0.0)
        self.overlay = TextOverlay(replay.follower.sentences, (w, h))
        self.view = ViewState()
        self.view.app = "rehearse"
        self.window = "PalmCards vision benchmark"
        cv2.namedWindow(self.window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window, w, h)
        self.t0 = time.perf_counter()
        self.stopped = False
        self.face = self.pose = None  # made at their first use, kept for every setting
        self.face_side = face_side
        self.index = 0  # camera frames so far, over every setting (the trackers' schedule counts them)

    def run(self, name: str) -> dict | None:
        """One setting: warm up, then measure. None if q/Esc stopped the benchmark."""
        from palmcards.render import draw_stats
        from palmcards.vision import FaceTracker, PoseTracker

        cv2 = self.cv2
        face_every, pose_every = parse_config(name)
        if face_every and self.face is None:
            self.face = FaceTracker(face_every, max_side=self.face_side)
        if pose_every and self.pose is None:
            self.pose = PoseTracker(pose_every)
        face = self.face if face_every else None
        pose = self.pose if pose_every else None
        if face:
            face.every = face_every
        if pose:
            pose.every = pose_every
        stream = self.replay.stream
        shown, work, cam, hands_ms, face_ms, pose_ms = [], [], [], [], [], []
        measuring, snap = False, {}
        begin = last = time.perf_counter()
        while True:
            frame = self.camera.read()
            start = time.perf_counter()
            t = start - self.t0
            if not measuring and start - begin >= self.warm:
                measuring, last = True, start
                snap = {"reads": len(stream.run_ms), "overflows": self.recorder.overflows,
                        "gaps": len(self.writer.manifest()["discontinuities"]), "t": start,
                        "video_frames": self.video.pushed if self.video else 0,
                        "video_dropped": self.video.dropped if self.video else 0,
                        "thermal": thermal(),
                        **{f"{k}_{a}": getattr(tr, a) for k, tr in (("face", face), ("pose", pose)) if tr
                           for a in ("results", "found", "skipped", "submitted")}}
                shown.clear()
            self.hands.submit(frame, t)
            if self.video is not None:
                self.video.push(frame.copy(), self.camera.last_t - self.t0)
            if (result := self.hands.poll()) is not None:
                self.modes.update(*result)
            self.modes.tick(t)
            for tracker in (face, pose):
                if tracker is not None:
                    tracker.maybe_submit(frame, t, self.index)
                    tracker.poll()
            self.replay.tick()
            self.view.section, self.view.current = self.replay.follower.section, self.replay.follower.sentence
            self.overlay.draw(frame, self.view)
            now = time.perf_counter()
            draw_stats(frame, f"{name}  cam {self.camera.fps:4.1f}  hands {self.hands.latency_ms:4.1f} ms"
                              + (f"  face {face.latency_ms:4.1f} ms" if face else "")
                              + (f"  pose {pose.latency_ms:4.1f} ms" if pose else "")
                              + ("" if measuring else "  warming up"))
            cv2.imshow(self.window, frame)
            key = cv2.waitKey(1) & 0xFF
            end = time.perf_counter()
            self.index += 1
            if measuring:
                shown.append(end - last)
                work.append((now - start) * 1000)
                cam.append(self.camera.fps)
                hands_ms.append(self.hands.latency_ms)
                if face:
                    face_ms.append(face.latency_ms)
                if pose:
                    pose_ms.append(pose.latency_ms)
            last = end
            if key in (ord("q"), 27):
                self.stopped = True
                return None
            if measuring and end - snap["t"] >= self.seconds:
                break
        span = time.perf_counter() - snap["t"]
        reads = stream.run_ms[snap["reads"]:]
        frame_ms = np.array(shown) * 1000
        out = {"config": name, "face_every": face_every, "pose_every": pose_every, "seconds": round(span, 1),
               "frames": len(shown), "shown_fps": round(len(shown) / max(sum(shown), 1e-6), 1),
               "frame_ms": {f"p{q}": round(float(np.percentile(frame_ms, q)), 1) for q in (50, 90, 99)},
               "cam_fps": round(float(np.median(cam)), 1),
               "work_ms_median": round(float(np.median(work)), 1), "work_ms_p90": round(float(np.percentile(work, 90)), 1),
               "hands_ms_median": round(float(np.median(hands_ms)), 1),
               "live": {"reads": len(reads), "ms_median": percentile(reads, 50), "ms_p90": percentile(reads, 90)},
               "audio": {"overflows": self.recorder.overflows - snap["overflows"],
                         "gaps": len(self.writer.manifest()["discontinuities"]) - snap["gaps"]},
               "peak_rss_mb": round(peak_rss_mb(), 1), "thermal": [snap["thermal"], thermal()]}
        if self.video is not None:
            out["video"] = {"codec": self.video.codec, "frames": self.video.pushed - snap["video_frames"],
                            "dropped": self.video.dropped - snap["video_dropped"]}
        for key, tracker, ms in (("face", face, face_ms), ("pose", pose, pose_ms)):
            if tracker is None:
                continue
            results = tracker.results - snap[f"{key}_results"]
            out[key] = {"ms_median": round(float(np.median(ms)), 1) if ms else None,
                        "per_s": round(results / span, 1),
                        "skipped": tracker.skipped - snap[f"{key}_skipped"],
                        "submitted": tracker.submitted - snap[f"{key}_submitted"],
                        "found_share": round((tracker.found - snap[f"{key}_found"]) / results, 2) if results else None}
        return out

    def close(self) -> None:
        if self.video is not None:
            self.video.stop()
            self.video.wait(10)
        for tracker in (self.face, self.pose):
            if tracker is not None:
                tracker.close()
        self.hands.close()
        self.camera.release()
        self.cv2.destroyWindow(self.window)
        self.cv2.waitKey(1)


def gate(r: dict, base: dict) -> list[str]:
    """What fails the budget; empty if it passes."""
    fails = []
    if r["shown_fps"] < MIN_FPS or r["shown_fps"] < BASE_SHARE * base["shown_fps"]:
        fails.append(f"fps {r['shown_fps']}")
    if r["frame_ms"]["p90"] > MAX_P90_MS:
        fails.append(f"p90 {r['frame_ms']['p90']} ms")
    if r["hands_ms_median"] > base["hands_ms_median"] + HANDS_OVER_MS:
        fails.append(f"hands {r['hands_ms_median']} ms")
    a, b = r["live"]["ms_p90"], base["live"]["ms_p90"]
    if a is not None and b is not None and a > LIVE_OVER * b:
        fails.append(f"live p90 {a} ms")
    if r["audio"]["overflows"] or r["audio"]["gaps"]:
        fails.append("audio dropped")
    if r.get("video", {}).get("dropped"):
        fails.append(f"video dropped {r['video']['dropped']}")
    return fails


def heat(r: dict) -> str:
    """The thermal state at the start and end of the measurement, e.g. "fair" or "nom>fair"."""
    a, b = (t or "?" for t in r.get("thermal", [None, None]))
    return a if a == b else f"{a[:3]}>{b[:4]}"


def report(results: list[dict]) -> str:
    base = next((r for r in results if r["config"] == "base"), results[0])
    lines = [f"{'setting':<8} {'fps':>5} {'p50':>5} {'p90':>5} {'p99':>5} {'cam':>5} {'hands':>6} "
             f"{'face ms':>8} {'face/s':>7} {'found':>6} {'pose ms':>8} {'pose/s':>7} {'found':>6} "
             f"{'live p90':>9} {'heat':>8}  gate"]
    failed = set()  # settings with a run that failed
    for r in results:
        if r["config"] == "base":
            base = r
        f, p = r.get("face", {}), r.get("pose", {})
        fails = gate(r, base)
        if fails:
            failed.add(r["config"])
        cell = lambda d, k: "-" if d.get(k) is None else f"{d[k]:g}"  # noqa: E731
        lines.append(f"{r['config']:<8} {r['shown_fps']:>5} {r['frame_ms']['p50']:>5} {r['frame_ms']['p90']:>5} "
                     f"{r['frame_ms']['p99']:>5} {r['cam_fps']:>5} {r['hands_ms_median']:>6} "
                     f"{cell(f, 'ms_median'):>8} {cell(f, 'per_s'):>7} {cell(f, 'found_share'):>6} "
                     f"{cell(p, 'ms_median'):>8} {cell(p, 'per_s'):>7} {cell(p, 'found_share'):>6} "
                     f"{cell(r['live'], 'ms_p90'):>9} {heat(r):>8}  {'ok' if not fails else 'NO: ' + ', '.join(fails)}")
    lines.append("(fps shown; frame times in ms; hands, face and pose: result latency in ms; "
                 "found: share of results with a face / body; heat: macOS thermal state)")
    finds = [r[k]["found_share"] for r in results for k in ("face", "pose") if k in r and r[k]["found_share"] is not None]
    if finds and min(finds) < MIN_FOUND:
        lines.append(f"WARNING: a face or body was found in only {min(finds):.0%} of some results; "
                     "sit in front of the camera and run again for numbers that count")
    bases = [r for r in results if r["config"] == "base"]
    if len(bases) > 1:
        lines.append("drift: baseline " + ", then ".join(
            f"{b['shown_fps']} fps / hands {b['hands_ms_median']} ms ({heat(b)})" for b in bases))
    passing = [r for r in results if r["config"] not in failed]

    def best(rows):
        return min(rows, key=lambda r: (r["face_every"] or math.inf, r["pose_every"] or math.inf), default=None)

    both = best([r for r in passing if r["face_every"] and r["pose_every"]])
    face_only = best([r for r in passing if r["face_every"] and not r["pose_every"]])
    pose_only = best([r for r in passing if r["pose_every"] and not r["face_every"]])
    lines.append("recommended: " + (f"face every {both['face_every']}, pose every {both['pose_every']} ({both['config']})"
                                    if both else "no face + pose setting passed")
                 + (f"; alone: face every {face_only['face_every']}" if face_only else "")
                 + (f", pose every {pose_only['pose_every']}" if pose_only else ""))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path, nargs="?", help="a folder under sessions/ (default: the newest with a "
                                                          "transcribed take)")
    ap.add_argument("--take", type=int, help="take number (default: that session's newest transcribed take)")
    ap.add_argument("--seconds", type=float, default=30.0, help="measured seconds per setting (default 30)")
    ap.add_argument("--warm", type=float, default=5.0, help="warm-up seconds per setting (default 5)")
    ap.add_argument("--configs", default=DEFAULT, help=f"settings in order (default {DEFAULT})")
    ap.add_argument("--face-side", type=int, default=BODY.face_max_side,
                    help=f"face frames downscaled to this many pixels on their long side (default {BODY.face_max_side})")
    ap.add_argument("--video", action="store_true", help="record the camera frames too, as a take with video on")
    ap.add_argument("--json", type=Path, help="save the results here")
    args = ap.parse_args()
    configs = args.configs.split(",")
    for name in configs:
        parse_config(name)
    if args.session is None:
        folder, number = newest_take(data_dir())
    else:
        folder = args.session
        session = Session.load(folder)
        number = args.take or max(t.number for t in session.takes if t.drill is None and t.transcript)
    session = Session.load(folder)
    take = session.take(number)
    audio, rate = read_wav(session.dir / take.wav)
    audio = resample(audio, rate)
    total = len(configs) * (args.seconds + args.warm) + 30
    audio = np.tile(audio, math.ceil(total * SPEECH.rate / max(len(audio), 1)) + 1)
    print(f"live follow: replaying take {take.number} of {folder.name}, looped; "
          f"{len(configs)} settings x {args.warm + args.seconds:.0f} s (about {total / 60:.0f} min)", flush=True)

    from palmcards.capture import AudioRecorder
    from palmcards.recording import TakeWriter

    replay = Replay(audio, take.t_start, take_notes(session, take), SPEECH.backend, None, SPEECH.live_where,
                    session.language)
    results: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        recorder = AudioRecorder(clock=lambda: time.perf_counter() - t0)
        writer = TakeWriter(Path(tmp), 1, recorder.rate, {"started": "", "requested_t": 0.0})
        bench = None
        try:
            load_s = replay.wait_ready()
            print(f"live model ready in {load_s:.1f} s", flush=True)
            recorder.open()
            recorder.start(writer)
            bench = Bench(replay, recorder, writer, args.warm, args.seconds, args.face_side,
                          Path(tmp) if args.video else None)
            replay.start()
            for name in configs:
                print(f"  {name} ...", flush=True)
                r = bench.run(name)
                if r is None:
                    print("stopped early", flush=True)
                    break
                results.append(r)
        finally:
            if bench is not None:
                bench.close()
            recorder.stop()
            recorder.close()
            writer.wait(5)
            replay.stream.close()
    if not results:
        return 1
    out = {"session": str(folder), "take": take.number, "live_model": replay.model, "where": replay.stream.where,
           "body": {"max_side": BODY.max_side, "face_max_side": args.face_side, "face_offset": BODY.face_offset,
                    "pose_offset": BODY.pose_offset},
           "warm_s": args.warm, "results": results}
    print(report(results))
    if args.json:
        args.json.write_text(json.dumps(out, indent=1) + "\n")
        print(f"saved {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
