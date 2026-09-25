"""Live-follow benchmark (milestone 6b, stage 1): is live recognition fast enough?

  python scripts/bench_live.py SESSION_DIR [--take N] [--engine mlx-whisper|apple]
                               [--model REPO] [--where thread|process]
                               [--step S] [--hints] [--camera SECONDS] [--json OUT] [--words]
  python scripts/bench_live.py --rescore RESULT.json [...]

Replays a recorded take's WAV through the live pipeline in real time, with
no microphone: palmcards.asr's live stream re-reads the last
SPEECH.live_window_s of audio every SPEECH.live_step_s and confirms the words
two readings agree on, and palmcards.follow follows them through the notes.
It then compares with the take's transcript made after the take (the record):

  lag        for each note word said and confirmed live: when it was
             confirmed minus when it ended in the post-take transcript
  sections   each voice section change against when the speaker actually
             got there: late by how much, early (a wrong jump), or missed
  sentences  share of speaking time the follow was on the sentence being said
  reads      time per window read, windows skipped as silent

--camera S also runs the app's frame loop (camera, hand tracking, gesture
machine, notes overlay in Rehearse, window) for S seconds without the live
pipeline, then through the whole replay with it, and compares the frame
rates. Needs Camera permission; q or Esc ends early.

The saved JSON keeps every confirmed live word with its times, so --rescore
recomputes the lag, section and sentence figures (after tuning
palmcards/follow.py or config.FOLLOW) without replaying the audio.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from palmcards.align import align  # noqa: E402
from palmcards.asr import LiveWord, get_recognizer  # noqa: E402
from palmcards.audio import resample  # noqa: E402
from palmcards.config import SPEECH  # noqa: E402
from palmcards.follow import FollowEvent, Follower  # noqa: E402
from palmcards.notes import Notes, load_notes, normalize  # noqa: E402
from palmcards.session import Session, read_wav  # noqa: E402

BLOCK_S = 0.1  # audio is fed in blocks this long, as the app's feeder will
GRACE_S = 2.0  # keep listening this long after the audio ends
SAMPLE_S = 0.1  # sentence tracking is scored at this interval
# A live word is a post-take word if it is this alike (rapidfuzz ratio) and
# was confirmed between MATCH_EARLY_S before the word started and
# MATCH_LATE_S after it ended.
MATCH_SIM = 0.8
MATCH_EARLY_S = 0.5  # word edges are approximate
MATCH_LATE_S = 4.0


class Replay:
    """Feeds a take's audio to a live stream at wall-clock pace, and the
    confirmed words to a follower. Call tick() often; it returns False when done."""

    def __init__(self, audio: np.ndarray, t_start: float, notes: Notes, engine: str, model: str | None, where: str,
                 language: str, hints: bool = False):
        self.audio, self.t_start = audio, t_start
        self.follower = Follower(notes)
        self.words: list[LiveWord] = []
        self.events: list[FollowEvent] = []
        self._t0: float | None = None
        self._pos = 0
        recognizer = get_recognizer(engine)
        if model:
            recognizer.live_model = model
        self.model = recognizer.live_model
        self.hints = tuple(s.text for s in notes.sentences) if hints else ()
        self.stream = recognizer.live(language, self.clock, where=where, hints=self.hints)

    def clock(self) -> float:
        """App time: the take's own clock, running from its first sample."""
        return self.t_start + (time.perf_counter() - self._t0 if self._t0 is not None else 0.0)

    def wait_ready(self) -> float:
        t = time.perf_counter()
        while not self.stream.ready:
            self.stream.poll()
            time.sleep(0.01)
        return time.perf_counter() - t

    def start(self) -> None:
        self._t0 = time.perf_counter()

    @property
    def duration(self) -> float:
        return len(self.audio) / SPEECH.rate

    def tick(self) -> bool:
        elapsed = time.perf_counter() - self._t0
        block = int(BLOCK_S * SPEECH.rate)
        while self._pos < len(self.audio) and min(self._pos + block, len(self.audio)) / SPEECH.rate <= elapsed:
            chunk = self.audio[self._pos:self._pos + block]
            self._pos += len(chunk)
            self.stream.feed(chunk, self.t_start + self._pos / SPEECH.rate)
        new = self.stream.poll()
        self.words += new
        self.events += self.follower.update(new)
        return elapsed < self.duration + GRACE_S

    def run_headless(self) -> None:
        self.start()
        while self.tick():
            time.sleep(0.005)


# --- scoring -------------------------------------------------------------------

def percentile(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 3) if values else None


def follow(notes: Notes, live: list[LiveWord]) -> list[FollowEvent]:
    """The follower's moves, fed the live words in the batches they were confirmed in."""
    follower, events, batch = Follower(notes), [], []
    for w in live:
        if batch and w.confirmed_at != batch[-1].confirmed_at:
            events += follower.update(batch)
            batch = []
        batch.append(w)
    return events + follower.update(batch)


def score(notes: Notes, post: list[dict], live: list[LiveWord], t_start: float) -> dict:
    sents = [[w.norm for w in s.words] for s in notes.sentences]
    events = follow(notes, live)
    post_al = align(sents, post)

    # Lag, per note word said in the take and confirmed live. A live word is
    # the same word if it sounds alike and was confirmed soon after the word
    # was said, in order. (Not by the live word's own times: Apple's live
    # words have none. Nor by aligning all the live words to the notes at
    # once: on a noisy run leaving everything unmatched can score better.)
    lags, said, last = [], 0, -1
    for ps in post_al["sentences"]:
        for pw in ps["words"]:
            if pw is None:
                continue
            said += 1
            word = post[pw]
            target = normalize(word["text"])
            i = next((i for i in range(last + 1, len(live))
                      if word["start"] - MATCH_EARLY_S <= live[i].confirmed_at <= word["end"] + MATCH_LATE_S
                      and fuzz.ratio(normalize(live[i].text), target) >= 100 * MATCH_SIM), None)
            if i is not None:
                last = i
                lags.append(live[i].confirmed_at - word["end"])

    # When the speaker reached each section and sentence (post-take transcript).
    starts = {s["sentence"]: s["start"] for s in post_al["sentences"] if s["status"] != "skipped"}
    ends = {s["sentence"]: s["end"] for s in post_al["sentences"] if s["status"] != "skipped"}
    reached: dict[int, float] = {}
    for i, t in starts.items():
        sec = notes.sentences[i].section
        reached[sec] = min(t, reached.get(sec, t))
    changes = []
    for e in (e for e in events if e.kind == "section"):
        truth = reached.get(e.index)
        changes.append({"section": e.index, "t": round(e.t - t_start, 2),
                        "delay_s": round(e.t - truth, 2) if truth is not None else None,
                        "verdict": "never said" if truth is None else "early" if e.t < truth else "late"})
    moved = {c["section"] for c in changes}
    missed = [sec for sec in sorted(reached) if sec > 0 and sec not in moved]

    # Sentence tracking over the speaking time.
    on = behind = ahead = 0
    if starts:
        order = sorted(starts, key=starts.get)
        timeline = [e for e in events if e.kind == "sentence"]
        for t in np.arange(min(starts.values()), max(ends.values()), SAMPLE_S):
            truth = max((i for i in order if starts[i] <= t), key=starts.get, default=None)
            if truth is None:
                continue
            ours = next((e.index for e in reversed(timeline) if e.t <= t), 0)
            on += ours == truth
            behind += ours < truth
            ahead += ours > truth
    total = max(1, on + behind + ahead)

    return {
        "note_words_said": said,
        "note_words_confirmed": len(lags),
        "lag_s": {"median": percentile(lags, 50), "p90": percentile(lags, 90),
                  "max": round(max(lags), 3) if lags else None, "min": round(min(lags), 3) if lags else None},
        "sections": {"changes": changes, "wrong": sum(c["verdict"] != "late" for c in changes), "missed": missed},
        "sentences": {"on": round(on / total, 3), "behind": round(behind / total, 3), "ahead": round(ahead / total, 3),
                      "moves": sum(e.kind == "sentence" for e in events)},
        "live_text": " ".join(w.text for w in live),
        "post_text": " ".join(w["text"] for w in post),
    }


# --- camera --------------------------------------------------------------------

def frame_loop(notes: Notes, seconds: float, replay: Replay | None) -> dict:
    """The app's per-frame work, with or without a replay ticking alongside.
    Stops after `seconds` (without a replay) or when the replay is done."""
    import cv2

    from palmcards.capture import Camera
    from palmcards.gestures import HandTracker, ModeMachine
    from palmcards.render import TextOverlay, ViewState, draw_stats

    window = "PalmCards live benchmark"
    shown, cam, work, hands = [], [], [], []
    with Camera() as camera:
        frame = camera.read()
        h, w = frame.shape[:2]
        tracker = HandTracker()
        modes = ModeMachine((w, h))
        overlay = TextOverlay(notes.sentences, (w, h))
        view = ViewState()
        view.app = "rehearse"
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window, w, h)
        t0 = last = time.perf_counter()
        if replay is not None:
            replay.start()
        while True:
            frame = camera.read()
            start = time.perf_counter()
            tracker.submit(frame, start - t0)
            if (result := tracker.poll()) is not None:
                modes.update(*result)
            modes.tick(start - t0)
            if replay is not None:
                alive = replay.tick()
                view.section, view.current = replay.follower.section, replay.follower.sentence
                view.rec_s = min(time.perf_counter() - t0, replay.duration)
            else:
                alive = start - t0 < seconds
            overlay.draw(frame, view)
            now = time.perf_counter()
            work.append((now - start) * 1000)
            draw_stats(frame, f"cam {camera.fps:4.1f}  live {'on' if replay else 'off'}  "
                              f"hands {tracker.latency_ms:4.1f} ms  work {work[-1]:4.1f} ms")
            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            now = time.perf_counter()
            shown.append(now - last)
            last = now
            cam.append(camera.fps)
            hands.append(tracker.latency_ms)
            if not alive or key in (ord("q"), 27):
                break
        tracker.close()
    cv2.destroyWindow(window)
    cv2.waitKey(1)
    skip = 10  # the first frames include start-up
    return {"seconds": round(sum(shown), 1), "frames": len(shown),
            "shown_fps": round((len(shown) - skip) / max(sum(shown[skip:]), 1e-6), 1),
            "cam_fps": round(float(np.median(cam[skip:])), 1),
            "work_ms_median": round(float(np.median(work[skip:])), 1),
            "work_ms_p90": round(float(np.percentile(work[skip:], 90)), 1),
            "hands_ms_median": round(float(np.median(hands[skip:])), 1)}


# --- report --------------------------------------------------------------------

def report(r: dict) -> str:
    s = r["score"]
    lag = s["lag_s"]
    said, conf = s["note_words_said"], s["note_words_confirmed"]
    reads = r["reads"]
    if r["where"] == "apple":
        how = "streaming on-device"
        cost = f"{reads['runs']} partial results; {len(reads['errors'])} errors {reads['errors'][:3]}"
    else:
        how = f"reader in a {r['where']}, a read every {r['step_s']} s, model loaded in {r['load_s']:.1f} s"
        cost = (f"{reads['runs']} windows, median {reads['ms_median']} ms, p90 {reads['ms_p90']} ms; "
                f"{reads['skipped_silent']} skipped as silent; {len(reads['errors'])} errors")
    lines = [
        f"take {r['take']} of {r['session']} ({r['duration_s']:.1f} s), live model {r['model']}, {how}",
        f"  words      {conf} of {said} note words said were confirmed live ({conf / max(said, 1):.0%})",
        f"  lag        median {lag['median']} s, p90 {lag['p90']} s, max {lag['max']} s "
        f"(confirmed minus the word's end)",
        f"  reads      {cost}",
    ]
    for c in s["sections"]["changes"]:
        when = f"{c['delay_s']:+.2f} s after the speaker got there" if c["verdict"] == "late" else \
            f"WRONG: {-c['delay_s']:.2f} s before the speaker got there" if c["verdict"] == "early" else \
            "WRONG: the speaker never said that section"
        lines.append(f"  section    -> {c['section'] + 1} at {c['t']:.1f} s into the take, {when}")
    if not s["sections"]["changes"]:
        lines.append("  section    no voice section changes")
    if s["sections"]["missed"]:
        lines.append(f"  section    MISSED: {', '.join(str(k + 1) for k in s['sections']['missed'])} "
                     "(the speaker got there, the follow didn't)")
    st = s["sentences"]
    lines.append(f"  sentences  on the sentence being said {st['on']:.0%} of the speaking time "
                 f"(behind {st['behind']:.0%}, ahead {st['ahead']:.0%}), {st['moves']} moves")
    if "camera" in r:
        a, b = r["camera"]["without"], r["camera"]["with"]
        for name, c in (("without", a), ("with", b)):
            lines.append(f"  frames     {name:<7} live: shown {c['shown_fps']} fps, camera {c['cam_fps']} fps, "
                         f"work {c['work_ms_median']} ms (p90 {c['work_ms_p90']}), hands {c['hands_ms_median']} ms "
                         f"[{c['seconds']} s]")
    gate = [f"median lag < 1 s: {'yes' if lag['median'] is not None and lag['median'] < 1.0 else 'NO'}"]
    if "camera" in r:
        ok = r["camera"]["with"]["shown_fps"] >= 0.9 * r["camera"]["without"]["shown_fps"]
        gate.append(f"frame rate within 10% of without: {'yes' if ok else 'NO'}")
    lines.append("  gate       " + "; ".join(gate))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path, nargs="?", help="a folder under sessions/")
    ap.add_argument("--take", type=int, default=1, help="take number (default 1)")
    ap.add_argument("--engine", choices=("mlx-whisper", "apple"), default=SPEECH.backend, help="speech engine")
    ap.add_argument("--model", help=f"live Whisper model (default {SPEECH.live_model})")
    ap.add_argument("--where", choices=("thread", "process"), default=SPEECH.live_where,
                    help="where Whisper reads windows")
    ap.add_argument("--step", type=float, help=f"seconds between window reads (default {SPEECH.live_step_s})")
    ap.add_argument("--hints", action="store_true", help="give the engine the notes as phrases to expect (Apple)")
    ap.add_argument("--camera", type=float, metavar="SECONDS", help="also measure the frame rate, S s without live")
    ap.add_argument("--json", type=Path, help="save the results here")
    ap.add_argument("--words", action="store_true", help="print the live and post-take words")
    ap.add_argument("--rescore", type=Path, nargs="+", metavar="JSON", help="score saved results again")
    args = ap.parse_args()
    if args.rescore:
        for path in args.rescore:
            result = json.loads(path.read_text())
            session = Session.load(result["session"])
            take = session.take(result["take"])
            live = [LiveWord(*w) for w in result["live_words"]]
            post = json.loads((session.dir / take.transcript).read_text())["words"]
            result["score"] = score(load_notes(session.notes), post, live, take.t_start)
            path.write_text(json.dumps(result, indent=1) + "\n")
            print(f"{path}\n{report(result)}")
        return 0
    if args.session is None:
        ap.error("a session folder, or --rescore")

    if args.step:  # for this run only; the live stream reads it from palmcards.asr's config
        import palmcards.asr

        palmcards.asr.SPEECH = replace(SPEECH, live_step_s=args.step)
    session = Session.load(args.session)
    take = session.take(args.take)
    if take.transcript is None or not (session.dir / take.transcript).exists():
        print(f"take {take.number} has no transcript yet: python -m palmcards.speech {session.dir}", file=sys.stderr)
        return 1
    if take.drill is not None:
        print(f"take {take.number} is a drill; the notes don't follow the voice in drills", file=sys.stderr)
        return 1
    notes = load_notes(session.notes)
    post = json.loads((session.dir / take.transcript).read_text())["words"]
    audio, rate = read_wav(session.dir / take.wav)
    audio = resample(audio, rate)

    replay = Replay(audio, take.t_start, notes, args.engine, args.model, args.where, session.language, args.hints)
    load_s = replay.wait_ready()
    result = {"session": str(args.session), "take": take.number, "duration_s": replay.duration,
              "model": replay.model + (" +hints" if args.hints else ""), "where": replay.stream.where, "step_s": args.step or SPEECH.live_step_s,
              "load_s": round(load_s, 2)}
    try:
        if args.camera:
            print(f"frame loop without live for {args.camera:.0f} s, then with live for {replay.duration:.0f} s ...")
            without = frame_loop(notes, args.camera, None)
            with_live = frame_loop(notes, 0.0, replay)
            result["camera"] = {"without": without, "with": with_live}
        else:
            print(f"replaying {replay.duration:.0f} s in real time ...")
            replay.run_headless()
    finally:
        replay.stream.close()
    stream = replay.stream
    result["reads"] = {"runs": stream.runs, "skipped_silent": stream.skipped_silent, "errors": stream.errors,
                       "ms_median": percentile(stream.run_ms, 50), "ms_p90": percentile(stream.run_ms, 90)}
    result["live_words"] = [[w.text, w.start, w.end, w.probability, w.confirmed_at] for w in replay.words]
    result["score"] = score(notes, post, replay.words, take.t_start)
    print(report(result))
    if args.words:
        print(f"\nlive:      {result['score']['live_text']}\npost-take: {result['score']['post_text']}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
        print(f"saved {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
