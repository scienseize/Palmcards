"""Headless screens: replay recorded hands through the real app onto a still
background, and save a PNG at every state change and every hint change.

    .venv/bin/python scripts/ux_screens.py SOURCE ... [--background IMAGE ...] [--out DIR]
    .venv/bin/python scripts/ux_screens.py --samples          (every samples/gestures/*.json)

SOURCE is a `main.py --trace` recording (sessions/gesture-logs/<stamp>.trace.jsonl, or just
<stamp>) or a gesture replay sample (samples/gestures/<name>.json, or just <name>).

No camera, microphone, model or window: main.run with fake devices (the seam the tests
and the film rig use), on a virtual clock that moves 1/--fps s a frame, so holds, springs
and the count-in run at their real pace however long a frame takes to draw.
- camera: the background, the same frame every time (a still wall in place of the video);
- hands: the recording's landmarks, each handed over on the first frame after it, as live;
- keys: the ones the live run logged (t, x, n, b, p);
- microphone and voice follow: the live run's takes (their audio, their transcripts), where
  its session is still on disk; otherwise silence;
- analysis: the live take's own results, handed back 3 s after the take stops (no Whisper);
- LLM: a stand-in that answers after --llm-latency s: the alternatives the live run's ring
  showed where the log has them, labelled stand-ins otherwise;
- face: a stand-in face that is always found looking at the camera, so the eye calibration
  runs in the first count-in as it did live;
- preferences: yours (reach, hold times, contrast, ...), with sounds off and the tutorial done.

A recording made before a behaviour changed can leave today's app in another mode than
the live one (a take stopped by the old command zone keeps going today). For a trace,
where the live run changed mode and the replay didn't within NUDGE_S, the key that makes
the change is pressed (t, x or p) and the index says so; --no-nudge turns it off. A
sample is never nudged: its `expected` entries are already today's behaviour.

Where does a screen get saved: a change of what the app is doing (mode, focus level,
operation, the focused unit, count-in digit, calibration step, playing, the tutorial
step, the replay of a take's video) or of any text in the label, the alert line or the
tutorial card. A state held --settle s is saved at that moment (its animations about
done); one that changes sooner is saved on its last frame and marked brief.

Written to OUT/<source>/<background>/NNN-<time>-<state>.png, with OUT/<source>/index.html
(every screen, the backgrounds side by side) and screens.json; OUT/index.html lists the
sources. OUT defaults to docs/local/ux (gitignored: the frames are drawn on whatever
background you give it). Backgrounds: --background IMAGE (repeatable; each also gets a
dark variant unless --no-dark), else docs/local/polish/wall.{jpg,png} if there is one,
else a synthetic bright wall and a dark room.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_now = [0.0]
time.perf_counter = lambda: _now[0]  # before the app is imported: default clock arguments bind at import

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import main  # noqa: E402
from palmcards import prefs as preferences  # noqa: E402
from palmcards.asr import LiveWord  # noqa: E402
from palmcards.config import BODY, REHEARSE  # noqa: E402
from palmcards.gestures import GestureLog, Hand  # noqa: E402
from palmcards.llm import FakeProvider, Reply  # noqa: E402
from palmcards.notes import notes_from_bytes  # noqa: E402
from palmcards.render import TextOverlay  # noqa: E402
from palmcards.replay import KINDS, differences, entry  # noqa: E402
from palmcards.revisions import from_snapshot  # noqa: E402
from palmcards.session import Session  # noqa: E402
from palmcards.tts import MacSay  # noqa: E402
from tests.test_vision import watcher  # noqa: E402  the tests' stand-in face and pose

LOGS = ROOT / "sessions" / "gesture-logs"
SESSIONS = ROOT / "sessions"
SAMPLES = ROOT / "samples" / "gestures"
FRAME = (1280, 720)  # the traces' frame size (mirrored-frame pixels)
KEYS = {"start": "t", "stop": "x", "next": "n", "previous": "b", "prepare": "p"}  # logged key commands
NUDGE_S = 0.7  # how long after a live mode change the replay may still be behind
ANALYSIS_S = 3.0  # a take's results arrive this long after it stops
TAIL_S = 2.0  # frames after the last hand result
RUN_NAME = re.compile(r"^\d{8}-\d{6}-.+?(-[0-9a-f]{6})?$")
FILM_COPY = re.compile(r"-(tone|length|takes|test|prepare)-\d{8}-\d{6}")


# --- what to replay -------------------------------------------------------------------------

@dataclass
class Source:
    """Hand frames on the live clock, and where the live run left its traces."""
    name: str
    frames: list[tuple[float, list[dict]]]  # (live app time, hands)
    stamp: str  # the live run: sessions/gesture-logs/<stamp>.jsonl
    start: float  # live time of the replay's first frame (its app time 0)
    end: float  # live time of its last frame
    start_mode: str = "prepare"  # prepare | review | rehearse
    nudge: bool = True
    window: tuple[float, float] | None = None  # a sample's stretch of the recording
    expected: list[dict] | None = None  # a sample's grammar entries
    ring_words: dict[str, list[str]] = field(default_factory=dict)  # word -> the alternatives the live ring showed
    notes: list[str] = field(default_factory=list)  # for the index: what stands in for what


def live_log(stamp: str) -> list[dict]:
    path = LOGS / f"{stamp}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def ring_words(entries: list[dict]) -> dict[str, list[str]]:
    """The alternatives each word's options ring showed live (the first node is the word itself)."""
    out: dict[str, list[str]] = {}
    for e in entries:
        nodes = e.get("nodes") or e.get("ring_nodes")
        if e.get("kind", "ring_nodes") == "ring_nodes" and nodes and len(nodes) > 1:
            alts = [n for n in nodes[1:] if n not in ("hear it", "stress")]
            if alts:
                out[nodes[0].lower()] = alts
    return out


def from_trace(path: Path) -> Source:
    stamp = path.name.removesuffix(".trace.jsonl")
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    frames = [(e["t"], e["hands"]) for e in lines]
    return Source(stamp, frames, stamp, start=0.0, end=frames[-1][0] if frames else 0.0,
                  ring_words=ring_words(live_log(stamp)))


def from_sample(path: Path) -> Source:
    s = json.loads(path.read_text())
    stamp = s["source"].removesuffix(".trace.jsonl")
    w0, w1 = s["window"]
    frames = [(w0 + f["t"], [{"label": h["label"], "points": np.array(h["points"], np.float32).reshape(21, 2).tolist()}
                             for h in f["hands"]]) for f in s["frames"]]
    # Rehearse: a key starts a take first, timed so the replay's take starts when the live one
    # did (its audio is the live take's), and the recording's hands arrive once it runs
    # (the first count-in calibrates the eyes, as live).
    lead = 2.0
    if s["start_mode"] == "rehearse":
        count_in = 0.3 + BODY.calib_camera_s + REHEARSE.count_in_s + 1 / 30
        live = live_session(stamp)
        takes = json.loads((live / "session.json").read_text())["takes"] if live else []
        take = next((k for k in takes if k["t_start"] - 1.0 <= w0 <= k["t_start"] + (k.get("duration_s") or 0)), None)
        lead = w0 - (take["t_start"] - count_in) if take else count_in + 0.9
        lead = max(lead, count_in - 0.1)
    words = ring_words(s.get("inputs", [])) or ring_words([e for e in live_log(stamp) if w0 - 60 <= e["t"] <= w1])
    return Source(s["name"], frames, stamp, start=w0 - lead, end=w1, start_mode=s["start_mode"], nudge=False,
                  window=(w0, w1), expected=s["expected"], ring_words=words)


def resolve(arg: str) -> Source:
    p = Path(arg)
    for cand in (p, LOGS / f"{arg}.trace.jsonl", SAMPLES / f"{arg}.json", SAMPLES / arg):
        if cand.is_file():
            return from_sample(cand) if cand.suffix == ".json" and not cand.name.endswith(".jsonl") else from_trace(cand)
    sys.exit(f"no trace or sample called {arg!r}")


def live_session(stamp: str) -> Path | None:
    """The session the live run wrote (not a film rig's copy of it)."""
    found = [j.parent for j in SESSIONS.glob("2*/session.json")
             if json.loads(j.read_text()).get("gesture_log") == f"gesture-logs/{stamp}.jsonl"]
    found = [f for f in found if not FILM_COPY.search(f.name)] or found
    return min(found, key=lambda f: len(f.name)) if found else None


def revisions(folder: Path) -> list:
    return json.loads((folder / "session.json").read_text()).get("revisions") or []


def stand_in_session(stamp: str) -> Path | None:
    """For a Review sample whose run left no session: the latest run before it with analysed takes."""
    best = None
    for j in sorted(SESSIONS.glob("2*/session.json")):
        name = j.parent.name
        if not RUN_NAME.match(name) or FILM_COPY.search(name) or name[:15] > stamp:
            continue
        data = json.loads(j.read_text())
        if data.get("revisions") and any(t.get("alignment") for t in data.get("takes", [])):
            best = j.parent
    return best


# --- the devices ------------------------------------------------------------------------------

def frame_times(hand_times: list[float], end: float, fps: float) -> list[float]:
    """App times of the frames: one at each hand result's time, as live (every result is
    handed over, none merged into another frame's), and every 1/fps s where there are none."""
    out, t, step = [], 0.0, 1.0 / fps
    for h in sorted(x for x in hand_times if x > 0.0) + [end]:
        while h - t > 1.5 * step:
            t += step
            out.append(t)
        if h > t:
            t = h
            out.append(t)
    return [0.0] + out


class StillCamera:
    """The background, frame after frame; the virtual clock moves to each frame's time."""

    def __init__(self, background: np.ndarray, times: list[float], fps: float, on_frame):
        self.background, self.times, self.fps, self.on_frame = background, times, fps, on_frame
        self.n, self.last_t = 0, 0.0

    def read(self):
        t = self.times[min(self.n, len(self.times) - 1)] + max(0, self.n - len(self.times) + 1) / self.fps
        _now[0] = 100.0 + t  # the app clock is this minus the first frame's
        self.last_t = _now[0]
        self.n += 1
        self.on_frame()
        return self.background.copy()

    def release(self):
        pass


class TraceHands:
    """The recording's hands, each on the first frame after it (live results came one frame late)."""
    latency_ms = 33.0

    def __init__(self, frames, start: float, clock):
        self.frames, self.start, self.clock, self.i = frames, start, clock, 0

    def submit(self, frame, t):
        return True

    def poll(self):
        now, got = self.clock(), None
        while self.i < len(self.frames) and self.frames[self.i][0] - self.start < now - 1e-6:
            got = self.frames[self.i]
            self.i += 1
        if got is None:
            return None
        return [Hand(np.array(h["points"], np.float32).reshape(21, 2), handedness=h["label"]) for h in got[1]], \
            got[0] - self.start

    def close(self):
        pass


class LiveMic:
    """The microphone: the live take playing at this moment of the live run, sample for sample; else silence."""
    overflows = 0

    def __init__(self, takes: list[dict], start: float, clock):
        self.takes, self.offset, self.clock = takes, start, clock  # live time = replay time + offset
        self.level, self.writer, self.tap, self.rate, self._audio = 0.0, None, None, 48000, {}
        if takes:
            import soundfile as sf
            self.rate = sf.info(str(takes[0]["wav"])).samplerate

    def audio(self, wav: Path) -> np.ndarray:
        if wav not in self._audio:
            import soundfile as sf
            a, r = sf.read(str(wav), dtype="float32")
            self._audio[wav] = a if r == self.rate else np.zeros(0, np.float32)
        return self._audio[wav]

    def open(self):
        pass

    def start(self, writer):
        self.writer, self.first, self.started, self.fed = writer, True, self.clock(), 0

    def pump(self):
        if self.writer is None:
            return
        live_start = self.started + self.offset
        take = next((k for k in self.takes if k["t_start"] - 1 <= live_start <= k["t_start"] + k["duration_s"]), None)
        audio = self.audio(take["wav"]) if take else np.zeros(0, np.float32)
        n_to = int(round((self.clock() - self.started) * self.rate))
        while self.fed < n_to:
            k = min(n_to - self.fed, self.rate // 50)
            i0 = int(round((live_start - take["t_start"]) * self.rate)) + self.fed if take else -1
            block = audio[i0:i0 + k] if 0 <= i0 and i0 + k <= len(audio) else np.zeros(k, np.float32)
            if self.first:
                self.writer.push(block, self.started, "adc")
                self.first = False
            else:
                self.writer.push(block)
            rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
            self.level = min(1.0, max(0.0, (20 * np.log10(max(rms, 1e-9)) + 60) / 60))
            if self.tap is not None:
                self.tap(block, self.writer.first_sample_t + self.writer.enqueued / self.rate)
            self.fed += k

    @property
    def seconds(self):
        return self.writer.seconds if self.writer else 0.0

    def stop(self):
        writer, self.writer = self.writer, None
        if writer:
            writer.stop()
        return writer

    def close(self):
        pass


class TranscriptLive:
    """The voice follow's live words: the live takes' transcripts, each word confirmed `lag` s after it ends."""
    errors, where = [], "replay"

    def __init__(self, words, lag, clock):
        self.words, self.lag, self.clock, self.state, self.i = words, lag, clock, "ready", 0

    @property
    def ready(self):
        return True

    def reset(self):
        now = self.clock()
        self.i = next((i for i, w in enumerate(self.words) if w[2] >= now - 1.0), len(self.words))

    def feed(self, samples, t_end):
        pass

    def poll(self):
        out, now = [], self.clock()
        while self.i < len(self.words) and self.words[self.i][2] + self.lag <= now:
            text, s, e = self.words[self.i]
            out.append(LiveWord(text, s, e, 0.9, now))
            self.i += 1
        return out

    def close(self):
        self.state = "closed"


class CassetteAnalysis:
    """In place of the analysis worker: the live take's transcript, alignment and metrics, moved onto
    the replay's clock and handed back ANALYSIS_S after the take stops."""

    def __init__(self, live_dir: Path | None, start: float, clock, run: "Run"):
        self.live_dir, self.start, self.clock, self.run = live_dir, start, clock, run
        self.live = json.loads((live_dir / "session.json").read_text())["takes"] if live_dir else []
        self.pending, self.log, self._due = [], [], []

    def submit(self, session_dir, job):
        live_t = job["t_start"] + self.start
        take = min((k for k in self.live if k.get("alignment") and k.get("transcript")),
                   key=lambda k: abs(k["t_start"] - live_t), default=None)
        if take is None or abs(take["t_start"] - live_t) > max(10.0, take.get("duration_s", 0)):
            self.run.notes.append(f"take {job['take']}: no live take to replay the analysis from (left analysing)")
            self.pending.append(job["take"])
            return True
        shift = -self.start
        tr = json.loads((self.live_dir / take["transcript"]).read_text())
        tr["t_start"] = job["t_start"]
        tr["take"], tr["wav"] = job["take"], Path(job["wav"]).name
        for w in tr["words"]:
            w["start"], w["end"] = w["start"] + shift, w["end"] + shift
        Path(job["transcript"]).write_text(json.dumps(tr))
        al = json.loads(json.dumps(take["alignment"]))
        for s in al.get("sentences", []):
            for k in ("start", "end"):
                if s.get(k) is not None:
                    s[k] += shift
        for f in al.get("fillers", []):
            f["t"] += shift
        self.run.notes.append(f"take {job['take']}: analysis replayed from live take {take['number']}")
        self.pending.append(job["take"])
        self._due.append((self.clock() + ANALYSIS_S, {
            "take": job["take"], "ok": True, "transcript": Path(job["transcript"]).name, "alignment": al,
            "metrics": take.get("metrics"), "seconds": ANALYSIS_S, "report": f"  (live take {take['number']})"}))
        return True

    def poll(self):
        now, out = self.clock(), []
        for due, result in list(self._due):
            if due <= now or self.run.closing:
                self._due.remove((due, result))
                self.pending.remove(result["take"])
                out.append(result)
        if self.run.closing:
            self.pending.clear()  # never answered: nothing to wait for at exit
        return out

    def failed(self):
        return []

    def retry_failed(self):
        return 0

    def close(self, timeout=None):
        return []


class StandInLLM(FakeProvider):
    """Answers after `latency` s of the replay's clock: the live ring's alternatives where known."""
    key_source = "the screen harness"

    def __init__(self, run: "Run", cloud: bool, latency: float):
        super().__init__(self.answer, cloud=cloud)
        self.run, self.latency = run, latency
        self.name = "anthropic" if cloud else "ollama"

    def answer(self, system: str, user: str) -> str:
        word = (m.group(1) if (m := re.search(r'The word: "(.*?)"', user)) else "").strip()
        text = user.split("<notes>\n", 1)[-1].rsplit("\n</notes>", 1)[0]
        if "meaning of the selected word" in system:
            return json.dumps({"meaning": f'A stand-in meaning of "{word}": the screen harness asks no LLM.'})
        if "alternatives for one word" in system:
            alts = self.run.source.ring_words.get(word.lower()) or ["stand-in one", "stand-in two", "stand-in three"]
            return json.dumps({"alternatives": alts})
        words = text.split()
        if "warmer" in system:
            out = "So, honestly, " + text[:1].lower() + text[1:]
        elif "more formal" in system:
            out = "Indeed, " + text[:1].lower() + text[1:]
        elif "shorter" in system:
            n = int(m.group(1)) if (m := re.search(r"about (\d+) words", system)) else max(1, len(words) // 2)
            out = " ".join(words[:max(1, n)]).rstrip(",;:") + "."
            if re.findall(r"\d", out) != re.findall(r"\d", " ".join(words[:max(1, n)])) or \
                    sorted(re.findall(r"\d+", out)) != sorted(re.findall(r"\d+", text)):
                out = text
        else:
            out = text + " That is the whole point, and it matters."
        return json.dumps({"text": out})

    def complete(self, system, user, timeout_s, schema=None):
        due = _now[0] + self.latency
        while _now[0] < due and not self.run.closing:
            time.sleep(0.002)  # the replay's clock is the frames': wait for it, not for the wall
        reply = super().complete(system, user, timeout_s, schema)
        return Reply(reply.text, 120, 40, model="stand-in")


class QuietSpeaker(MacSay):
    """Hear it, without a sound: `say` renders the sentence to a file as in the app (so it lasts
    exactly as long), and the silent player never plays it; nothing is ever said live."""

    def say(self, text, voice=None, rate_wpm=None):
        pass

    def say_words(self, words, stressed=frozenset()):
        pass

    def stop(self):
        pass

    @property
    def speaking(self):
        return False

    def render_async(self, words):
        """Rendered at once, not on a thread: the replay's clock runs faster than the wall's, so
        a thread would finish at a different moment of it each run (the app renders on focus,
        well before the palm that plays it)."""
        from concurrent.futures import Future
        text = " ".join(words)
        cache = self.__dict__.setdefault("_kept", {})  # the app asks every frame while a sentence is focused
        if text not in cache:
            f = cache[text] = Future()
            try:
                f.set_result(self.render(text))
            except Exception as exc:  # noqa: BLE001 - as the app's: the speaker says it live instead
                f.set_exception(exc)
        return cache[text]

    def close(self):
        pass


class SilentPlayer:
    def play(self, audio, rate):
        pass

    def stop(self):
        pass


# --- what is on screen -------------------------------------------------------------------------

_frame_sig: list = [None]  # the signature of what the app drew this frame (set by the draw wrappers)
_real_draw = TextOverlay.draw
_real_draw_replay = main.Takes.draw_replay


def _norm(text: str) -> str:
    """Label text without what moves continuously: a waiting line's dots, a stretch's percent,
    and the focused word in the state line, which scrambles through random glyphs each frame
    while the ring turns (the pick itself shows on the operation line)."""
    text = re.sub(r"\.{1,3}$", "", text.strip())
    text = re.sub(r'^(FOCUS BY WORD)\s+".*$', r"\1", text)
    return re.sub(r"[+−-]?\d+%", "N%", text)


def _draw(self, frame, state):
    ops = state.ops
    focus = (state.focus.sentence, state.focus.word) if state.focus is not None and state.mode == "focus" else None
    what = {"app": state.app, "mode": state.mode, "level": state.level, "op": ops.kind, "focus": focus,
            "count_in": state.count_in if state.app == "count_in" else 0, "calibration": state.calibration,
            "playing": state.playing, "tutorial": state.tutorial[0] if state.tutorial else None,
            "keys_help": state.keys_help, "preview": bool(state.edit_preview), "loading": state.loading}
    label = [text for _, text in self.label_rows(state)]
    said = {"label": label, "alert": state.alert, "tutorial_text": state.tutorial[2] if state.tutorial else ""}
    _frame_sig[0] = (what, said)
    return _real_draw(self, frame, state)


def _draw_replay(self, frame, progress):
    _frame_sig[0] = ({"app": "review", "mode": "replay", "playing": True}, {"label": ["(a take's video replays)"]})
    return _real_draw_replay(self, frame, progress)


TextOverlay.draw = _draw
main.Takes.draw_replay = _draw_replay


def signature(sig) -> tuple:
    what, said = sig
    return (json.dumps(what, sort_keys=True),
            json.dumps({k: ([_norm(x) for x in v] if isinstance(v, list) else _norm(v)) for k, v in said.items()},
                       sort_keys=True))


def slug(what: dict) -> str:
    parts = [what.get("app"), what.get("mode") if what.get("mode") != "idle" else None, what.get("level"), what.get("op")]
    if what.get("count_in"):
        parts.append(f"count{what['count_in']}")
    if what.get("calibration"):
        parts.append(f"calib-{what['calibration']}")
    if what.get("playing"):
        parts.append("playing")
    return "-".join(str(p) for p in parts if p)


def changed(old, new) -> str:
    """In words, what differs between two frames' signatures."""
    if old is None:
        return "first screen"
    out = []
    for k, v in new[0].items():
        if old[0].get(k) != v:
            out.append(f"{k}: {old[0].get(k)} → {v}")
    for k, v in new[1].items():
        a = old[1].get(k)
        if (a if not isinstance(a, list) else [_norm(x) for x in a]) != (v if not isinstance(v, list) else [_norm(x) for x in v]):
            out.append(f"{'hint' if k == 'label' else k} changed")
    return "; ".join(out) or "changed"


# --- one run ----------------------------------------------------------------------------------

@dataclass
class Run:
    source: Source
    background: np.ndarray
    bg_name: str
    out: Path
    fps: float
    settle: float
    llm: str
    llm_latency: float
    closing: bool = False
    notes: list[str] = field(default_factory=list)
    shots: list[dict] = field(default_factory=list)
    nudges: list[dict] = field(default_factory=list)

    def clock(self) -> float:
        return _now[0] - 100.0  # the app clock: the first frame is 0

    def go(self) -> None:
        src = self.source
        work = Path(tempfile.mkdtemp(prefix="ux-screens-"))
        self.work = work
        self.out.mkdir(parents=True, exist_ok=True)
        for old in self.out.glob("*.png"):
            old.unlink()
        _now[0] = 0.0

        live_dir = live_session(src.stamp)
        review_dir = live_dir
        if src.start_mode == "review" and (live_dir is None or not revisions(live_dir)):
            review_dir = stand_in_session(src.stamp)
            why = "left no session" if live_dir is None else "kept no notes revision (schema 1; the app can't open it either)"
            self.notes.append(f"Review opened on {review_dir.name if review_dir else 'nothing'} "
                              f"(stand-in: the live run {why})")
        live_takes = []
        if live_dir:
            for k in json.loads((live_dir / "session.json").read_text())["takes"]:
                if (live_dir / k["wav"]).exists():
                    live_takes.append({"wav": live_dir / k["wav"], "t_start": k["t_start"],
                                       "duration_s": k.get("duration_s") or 0.0, "transcript": k.get("transcript"),
                                       "lag": (k.get("live") or {}).get("lag_median_s"), "video": k.get("video")})
        else:
            self.notes.append("the live run left no session: takes are silent and never analysed")

        # keys: the live run's, and (for a trace) the nudges decided as it goes
        entries = live_log(src.stamp)
        self.keys = sorted((e["t"] - src.start, KEYS[e["command"]]) for e in entries
                           if e["kind"] == "key" and e.get("command") in KEYS and e.get("acted", True)
                           and src.start <= e["t"] <= src.end)
        if src.start_mode == "rehearse":
            self.keys.insert(0, (0.3, "t"))
        key_times = [e["t"] for e in entries if e["kind"] == "key"]
        self.live_modes = [(e["t"] - src.start, e["mode"]) for e in entries
                           if e["kind"] == "mode" and src.start < e["t"] <= src.end
                           and not any(0 <= e["t"] - k <= 0.2 for k in key_times)] if src.nudge else []
        self.app_mode = src.start_mode if src.start_mode != "rehearse" else "prepare"

        mics: list[LiveMic] = []

        def make_mic(clock):
            m = LiveMic(live_takes, src.start, clock)
            mics.append(m)
            return m

        def make_live(language, clock, hints):
            words = []
            for k in live_takes:
                if k["transcript"] and (live_dir / k["transcript"]).exists():
                    tr = json.loads((live_dir / k["transcript"]).read_text())
                    words += [(w["text"], w["start"] - src.start, w["end"] - src.start) for w in tr["words"]]
            lag = next((k["lag"] for k in live_takes if k["lag"]), 0.8)
            return TranscriptLive(sorted(words, key=lambda w: w[1]), lag, clock)

        self.last_t = src.end - src.start + TAIL_S
        times = frame_times([t - src.start for t, _ in src.frames], self.last_t + 1.0, self.fps)
        camera = StillCamera(self.background, times, self.fps, lambda: [m.pump() for m in mics])
        self.prev_frame, self.cur, self.shown_sig, self.pending = None, None, None, None
        prefs_file = work / "prefs.json"
        mine = preferences.load()
        mine.sounds, mine.tutorial_done = False, True
        preferences.save(mine, prefs_file)
        video = any((k.get("video") or {}).get("state") in ("saved", "interrupted") for k in live_takes)

        llm = (lambda: None) if self.llm == "off" else (lambda: StandInLLM(self, self.llm == "cloud", self.llm_latency))
        devices = main.Devices(
            camera=lambda: camera, tracker=lambda: TraceHands(src.frames, src.start, self.clock),
            log=lambda: GestureLog(work / "gesture.jsonl"), recorder=make_mic, player=SilentPlayer,
            speaker=QuietSpeaker, live=make_live, llm=llm, vision=watcher,
            named_window=lambda *a: None, show=self.show, wait_key=self.wait_key,
            window_open=lambda n: True, destroy_windows=lambda: None)
        real_supervisor = main.Supervisor
        main.Supervisor = lambda: CassetteAnalysis(live_dir, src.start, self.clock, self)
        try:
            if src.start_mode == "review" and review_dir is not None:
                dst = work / "sessions" / review_dir.name
                shutil.copytree(review_dir, dst, ignore=shutil.ignore_patterns("session.lock", "*.mp4", "*.npz"))
                data = json.loads((dst / "session.json").read_text())
                before = [k for k in data["takes"] if review_dir != live_dir or k["t_start"] < src.window[0]]
                if not before:  # recorded while its first take was: that take
                    before = [k for k in data["takes"] if k["t_start"] < src.window[1]]
                if review_dir == live_dir and len(before) < len(data["takes"]):
                    self.notes.append(f"Review holds the {len(before)} takes recorded before the sample")
                data["takes"] = before
                for k in before:
                    k.pop("video", None)  # the copy leaves the videos behind
                (dst / "session.json").write_text(json.dumps(data))
                session = Session.load(dst)
                session.acquire()
                notes = from_snapshot(session.snapshot(session.current_revision))
                main.run(session.notes, notes, b"", lang=session.language, devices=devices, session=session,
                         sessions_root=work / "sessions", prefs_file=prefs_file, follow=True, video=video)
            else:
                if src.start_mode == "review":
                    self.notes.append("no session to open in Review: it starts in Prepare")
                if live_dir and (live_dir / "source").is_dir() and any((live_dir / "source").iterdir()):
                    path = next((live_dir / "source").iterdir())
                else:
                    path = ROOT / "samples" / "sample_notes.md"
                    self.notes.append("notes: samples/sample_notes.md (the live run's notes aren't on disk)")
                source = path.read_bytes()
                main.run(path, notes_from_bytes(source, path), source, devices=devices,
                         sessions_root=work / "sessions", prefs_file=prefs_file, follow=True, video=video)
        finally:
            main.Supervisor = real_supervisor
        self.flush()
        self.grammar = [json.loads(line) for line in (work / "gesture.jsonl").read_text().splitlines() if line.strip()]

    # the window: what the app shows, and the keys it reads

    def show(self, name, frame):
        t = self.clock()
        sig = _frame_sig[0]
        if sig is not None:
            self.app_mode = sig[0].get("app", self.app_mode)
            key = signature(sig)
            if self.cur is None or key != self.cur["key"]:
                if self.cur is not None and not self.cur["saved"]:
                    self.save(self.cur, self.prev_frame, brief=True)
                self.cur = {"key": key, "sig": sig, "t": t, "saved": False, "why": changed(self.shown_sig, sig)}
                self.shown_sig = sig
            elif not self.cur["saved"] and t - self.cur["t"] >= self.settle:
                self.save(self.cur, frame, brief=False)
        self.prev_frame = frame.copy()
        self.prev_t = t

    def save(self, cur, frame, brief):
        if frame is None:
            return
        what, said = cur["sig"]
        i = len(self.shots) + 1
        t_shown = self.prev_t if brief else self.clock()
        name = f"{i:03d}-{cur['t']:07.2f}s-{slug(what)}.png"
        cv2.imwrite(str(self.out / name), frame, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        cur["saved"] = True
        self.shots.append({"i": i, "t": round(cur["t"], 3), "shown_at": round(t_shown, 3), "file": name,
                           "brief": brief, "why": cur["why"], "state": what, "label": said["label"],
                           "alert": said.get("alert", ""), "tutorial": said.get("tutorial_text", ""),
                           "nudged": [n for n in self.nudges if abs(n["t"] - cur["t"]) < 0.5]})

    def flush(self):
        if self.cur is not None and not self.cur["saved"]:
            self.save(self.cur, self.prev_frame, brief=True)

    def wait_key(self):
        t = self.clock()
        if t >= self.last_t:
            self.closing = True
            return ord("q")
        while self.live_modes and self.live_modes[0][0] + NUDGE_S <= t:
            lt, mode = self.live_modes.pop(0)
            key = {("prepare", "count_in"): "t", ("review", "count_in"): "t", ("rehearse", "review"): "x",
                   ("count_in", "review"): "x", ("review", "prepare"): "p"}.get((self.app_mode, mode))
            if key:
                self.keys.append((t, key))
                self.keys.sort()
                self.nudges.append({"t": round(t, 3), "key": key, "live_mode": mode, "was": self.app_mode})
        if self.keys and self.keys[0][0] <= t:
            return ord(self.keys.pop(0)[1])
        return 255


# --- backgrounds --------------------------------------------------------------------------------

def room(wall: float, person: float) -> np.ndarray:
    """A plain wall with a soft light falloff and a person-shaped blob in the face zone
    (as docs/local/polish/readability/shots.py), without sensor noise so the PNGs stay small."""
    w, h = FRAME
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = wall * (1.0 - 0.10 * ((x / w - 0.3) ** 2 + (y / h - 0.3) ** 2))
    head = ((x - 0.49 * w) / 120) ** 2 + ((y - 0.40 * h) / 160) ** 2 < 1
    body = ((x - 0.49 * w) / 330) ** 2 + ((y - 1.05 * h) / 300) ** 2 < 1
    img = np.where(head | body, person, img)
    img = img[..., None] * np.array([0.97, 1.0, 1.03], np.float32)  # BGR: a little warm
    return np.clip(img, 0, 255).astype(np.uint8)


def darker(img: np.ndarray) -> np.ndarray:
    """The same scene in a dim room: a tenth of the light, a little blue, crushed shadows."""
    f = (img.astype(np.float32) / 255) ** 1.6 * 0.16
    f = f * np.array([1.12, 1.0, 0.9], np.float32)
    return np.clip(f * 255, 0, 255).astype(np.uint8)


def backgrounds(paths: list[str], dark: bool) -> dict[str, np.ndarray]:
    found = paths or [str(p) for p in sorted((ROOT / "docs" / "local" / "polish").glob("wall.*"))
                      if p.suffix.lower() in (".jpg", ".jpeg", ".png")][:1]
    out = {}
    for p in found:
        img = cv2.imread(p)
        if img is None:
            sys.exit(f"can't read the background {p}")
        img = cv2.resize(img, FRAME, interpolation=cv2.INTER_AREA)
        out[Path(p).stem] = img
        if dark:
            out[f"{Path(p).stem}-dark"] = darker(img)
    if not out:
        out = {"wall": room(242, 150)}
        if dark:
            out["dark"] = room(22, 55)
    return out


# --- the index ----------------------------------------------------------------------------------

def check_sample(src: Source, grammar: list[dict]) -> list[str] | None:
    """A sample's grammar entries, from the run's own log, against what the replay tests expect."""
    if src.expected is None:
        return None
    lead = src.window[0] - src.start
    got = [entry(e, lead) for e in grammar if e["kind"] in KINDS and e["t"] >= lead - 0.05]
    if src.start_mode == "rehearse" and got and got[0]["kind"] == "mode" and got[0].get("mode") == "rehearse":
        got = got[1:]  # the replay's own take starting (timed to the live one's start)
    return differences(src.expected, got)


def write_index(folder: Path, src: Source, runs: list[Run], elapsed: float) -> None:
    data = {"source": src.name, "stamp": src.stamp, "start": src.start, "end": src.end, "window": src.window,
            "notes": sorted({n for r in runs for n in r.notes}),
            "runs": {r.bg_name: {"shots": r.shots, "nudges": r.nudges, "sample_check": r.check} for r in runs}}
    (folder / "screens.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    first = runs[0]
    same = all([s["why"] for s in r.shots] == [s["why"] for s in first.shots] for r in runs)
    rows = []
    n = max(len(r.shots) for r in runs)
    for i in range(n):
        s = next((r.shots[i] for r in runs if i < len(r.shots)), None)
        cells = "".join(
            f'<td><a href="{r.bg_name}/{r.shots[i]["file"]}"><img loading="lazy" src="{r.bg_name}/{r.shots[i]["file"]}"></a></td>'
            if i < len(r.shots) else "<td></td>" for r in runs)
        label = "<br>".join(html.escape(x) for x in s["label"])
        extra = "".join(f"<div class=alert>{html.escape(v)}</div>" for v in (s["alert"], s["tutorial"]) if v)
        nudge = "".join(f'<div class=nudge>nudged: key {n["key"]} (live went to {n["live_mode"]})</div>'
                        for n in s["nudged"])
        rows.append(f'<tr><td class=meta><b>{s["i"]:03d}</b> · {s["t"]:.2f} s{" · <i>brief</i>" if s["brief"] else ""}'
                    f'<div class=state>{html.escape(slug(s["state"]))}</div><div class=label>{label}</div>{extra}'
                    f'<div class=why>{html.escape(s["why"])}</div>{nudge}</td>{cells}</tr>')
    check = first.check
    verdict = "" if check is None else (
        "<p class=ok>Grammar entries match the sample's expected ones.</p>" if not check else
        "<p class=bad>Grammar entries differ from the sample's expected ones:<br>" +
        "<br>".join(html.escape(c) for c in check) + "</p>")
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in data["notes"])
    nudges = sum(len(r.nudges) for r in runs[:1])
    page = f"""<!doctype html><meta charset=utf-8><title>{html.escape(src.name)}: screens</title>
<style>
body{{font:13px/1.4 -apple-system,system-ui,sans-serif;margin:16px;background:#111;color:#ddd}}
table{{border-collapse:collapse}} td{{vertical-align:top;padding:6px;border-top:1px solid #333}}
img{{width:480px;display:block}} .meta{{width:300px}} .state{{color:#fb0;font-family:ui-monospace,monospace}}
.label{{font-family:ui-monospace,monospace;color:#fff;margin:4px 0}} .why{{color:#888}} .alert{{color:#f77}}
.nudge{{color:#6cf}} .ok{{color:#7d7}} .bad{{color:#f77}} a{{color:#9cf}}
</style>
<h1>{html.escape(src.name)}</h1>
<p>{len(first.shots)} screens · live {src.start:.1f}–{src.end:.1f} s of {html.escape(src.stamp)} · replayed at
{first.fps:g} fps in {elapsed:.0f} s · {nudges} nudge{"s" if nudges != 1 else ""}
{"" if same else " · <b class=bad>the backgrounds' sequences differ</b>"}</p>
{verdict}<ul>{notes}</ul>
<table><tr><th></th>{"".join(f"<th>{html.escape(r.bg_name)}</th>" for r in runs)}</tr>{"".join(rows)}</table>
"""
    (folder / "index.html").write_text(page)


def write_top(out: Path) -> None:
    items = []
    for j in sorted(out.glob("*/screens.json")):
        d = json.loads(j.read_text())
        runs = d["runs"]
        first = next(iter(runs.values()))
        check = first.get("sample_check")
        flag = "" if check is None else (" ✓" if not check else " ✗ grammar differs")
        items.append(f'<li><a href="{j.parent.name}/index.html">{html.escape(d["source"])}</a> '
                     f'({len(first["shots"])} screens{flag})</li>')
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>UX screens</title><style>body{font:14px -apple-system,sans-serif;"
        "margin:16px;background:#111;color:#ddd}a{color:#9cf}</style><h1>UX screens</h1><ul>"
        + "".join(items) + "</ul>")


def main_cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", help="traces (path or stamp) or gesture samples (path or name)")
    ap.add_argument("--samples", action="store_true", help="every samples/gestures/*.json")
    ap.add_argument("--background", action="append", default=[], help="a background image (repeatable)")
    ap.add_argument("--no-dark", action="store_true", help="no dark variant of each background")
    ap.add_argument("--out", default=str(ROOT / "docs" / "local" / "ux"))
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--settle", type=float, default=0.4, help="seconds a state is held before it is saved")
    ap.add_argument("--llm", choices=("cloud", "local", "off"), default="cloud",
                    help="the stand-in LLM, shown as the cloud (default), on the Mac, or off")
    ap.add_argument("--llm-latency", type=float, default=0.8)
    ap.add_argument("--no-nudge", action="store_true", help="never press keys the live run didn't")
    args = ap.parse_args(argv)
    names = list(args.sources) + ([str(p) for p in sorted(SAMPLES.glob("*.json"))] if args.samples else [])
    if not names:
        ap.error("give a trace or a sample, or --samples")
    bgs = backgrounds(args.background, not args.no_dark)
    out = Path(args.out)
    for name in names:
        src = resolve(name)
        src.nudge = src.nudge and not args.no_nudge
        folder = out / src.name
        folder.mkdir(parents=True, exist_ok=True)
        wall = time.monotonic()
        runs = []
        for bg_name, img in bgs.items():
            run = Run(src, img, bg_name, folder / bg_name, args.fps, args.settle, args.llm, args.llm_latency)
            run.go()
            run.check = check_sample(src, run.grammar)
            runs.append(run)
            shutil.rmtree(run.work, ignore_errors=True)
        elapsed = time.monotonic() - wall
        write_index(folder, src, runs, elapsed)
        c = runs[0].check
        print(f"{src.name}: {len(runs[0].shots)} screens × {len(runs)} backgrounds in {elapsed:.0f} s"
              + ("" if c is None else ("; grammar matches the sample" if not c else f"; GRAMMAR DIFFERS: {c}"))
              + (f"; {len(runs[0].nudges)} nudges" if runs[0].nudges else "")
              + f" -> {folder / 'index.html'}")
    write_top(out)
    return 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv[1:]))
