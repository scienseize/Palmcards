"""Automatic hardware checks: the parts of the smoke test that need no one at the Mac.

  python scripts/hardware_check.py [--quiet] [--json OUT]

  camera     opens, delivers frames; its rate; hand tracking and drawing cost per frame
  microphone records 3 s through the real take writer (to a temporary folder, deleted);
             whether PortAudio gives the device clock (adc) or the fallback is used; level; overflows
  live       the live model loads in its own process; how long it takes
  speaker    a short click played while recording: the round trip output -> input, an upper
             bound on how far audio and the app clock can disagree (skipped with --quiet)
  voice      `say` speaks one word (skipped with --quiet)

Nothing is kept: no frames are saved, the recording is deleted. The interactive
checks (gestures, a real take, the clap test, recovery, reopening) are in
docs/hardware-smoke-test.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check_camera() -> dict:
    from palmcards.capture import Camera
    from palmcards.gestures import HandTracker
    from palmcards.notes import load_notes
    from palmcards.render import TextOverlay, ViewState

    out = {}
    with Camera() as camera:
        frame = camera.read()
        h, w = frame.shape[:2]
        out["size"] = [w, h]
        tracker = HandTracker()
        overlay = TextOverlay(load_notes(Path(__file__).resolve().parent.parent / "samples" / "sample_notes.md")
                              .sentences, (w, h))
        t0 = time.perf_counter()
        draw_ms, frames = [], 0
        while time.perf_counter() - t0 < 5.0:
            frame = camera.read()
            tracker.submit(frame, time.perf_counter() - t0)
            tracker.poll()
            t = time.perf_counter()
            overlay.draw(frame, ViewState())
            draw_ms.append((time.perf_counter() - t) * 1000)
            frames += 1
        out["camera_fps"] = round(camera.fps, 1)
        out["loop_fps"] = round(frames / (time.perf_counter() - t0), 1)
        out["hands_ms"] = round(tracker.latency_ms, 1)
        out["draw_ms_median"] = round(float(np.median(draw_ms)), 1)
        tracker.close()
    out["ok"] = out["camera_fps"] >= 25 and out["loop_fps"] >= 25
    return out


def check_microphone(click: bool) -> dict:
    from palmcards.capture import AudioRecorder
    from palmcards.recording import TakeWriter
    from palmcards.session import read_wav

    t0 = time.perf_counter()
    clock = lambda: time.perf_counter() - t0  # noqa: E731
    rec = AudioRecorder(clock=clock)
    out = {"rate": rec.rate}
    with tempfile.TemporaryDirectory() as tmp:
        writer = TakeWriter(Path(tmp), 1, rec.rate, {"started": "", "requested_t": clock()})
        rec.open()
        time.sleep(0.5)  # the count-in's warm-up
        rec.start(writer)
        played_at = None
        if click:
            import sounddevice as sd

            time.sleep(1.0)
            tone = (0.5 * np.sin(2 * np.pi * 1000 * np.arange(int(0.03 * 48000)) / 48000)).astype(np.float32)
            played_at = clock()
            sd.play(tone, 48000)
        time.sleep(3.0 if not click else 2.0)
        rec.stop()
        rec.close()
        writer.wait(5)
        m = writer.manifest()
        out.update(clock=m["clock"], seconds=round(m["samples"] / rec.rate, 2), peak=m["peak"],
                   overflows=rec.overflows, gaps=len(m["discontinuities"]), state=m["state"])
        audio, rate = read_wav(Path(tmp) / writer.wav)
        if played_at is not None and m["first_sample_t"] is not None:
            env = np.abs(audio)
            quiet = float(np.percentile(env, 50))
            loud = np.flatnonzero(env > max(0.05, quiet * 20))
            after = loud[loud / rate + m["first_sample_t"] > played_at - 0.05]
            if len(after):
                heard_at = m["first_sample_t"] + after[0] / rate
                out["click_round_trip_ms"] = round((heard_at - played_at) * 1000, 1)
            else:
                out["click_round_trip_ms"] = None  # not heard: speaker muted or too quiet
    out["ok"] = out["state"] == "saved" and out["peak"] >= 1e-3 and out["gaps"] == 0
    if out["peak"] < 1e-3:
        out["problem"] = "silence: Microphone permission for the terminal app is probably off"
    return out


def check_live() -> dict:
    from palmcards.asr import get_recognizer

    t0 = time.perf_counter()
    live = get_recognizer().live("en", time.perf_counter, "process")
    try:
        while live.state == "starting" and time.perf_counter() - t0 < 90:
            live.poll()
            time.sleep(0.05)
        return {"state": live.state, "load_s": round(time.perf_counter() - t0, 1), "errors": live.errors,
                "ok": live.state == "ready"}
    finally:
        live.close()


def check_voice() -> dict:
    from palmcards.tts import get_speaker

    speaker = get_speaker()
    speaker.say("PalmCards check")
    t0 = time.time()
    while speaker.speaking and time.time() - t0 < 10:
        time.sleep(0.1)
    return {"ok": not speaker.speaking, "seconds": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quiet", action="store_true", help="no click, no speech")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    results = {}
    checks = [("camera", check_camera), ("microphone", lambda: check_microphone(not args.quiet)),
              ("live", check_live)]
    if not args.quiet:
        checks.append(("voice", check_voice))
    for name, check in checks:
        try:
            results[name] = check()
        except Exception as exc:
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        r = results[name]
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {name:<11} " + ", ".join(f"{k}={v}" for k, v in r.items() if k != "ok"),
              flush=True)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1) + "\n")
    return 0 if all(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
