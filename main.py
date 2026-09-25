"""PalmCards entry point.

  python main.py [NOTES_FILE] [--lang xx] [--trace] [--no-follow]
      NOTES_FILE: .txt, .md or .docx; defaults to the sample
      --lang: language spoken in the takes, for Whisper (default en)
      --trace: also record every hand result's landmarks, for offline replay

Mirrored webcam feed with the notes overlaid in the demo style. Prepare mode
follows Kat's gesture grammar (see CLAUDE.md). Hold your hand in the box on
the right of the frame; it steers the highlight in the text on the left.

  one finger up / two fingers together / flat hand
                           browse by word / sentence / paragraph
  top or bottom of the box scroll
  pinch (word), fold fingers onto the thumb (sentence, paragraph)
                           focus
  open palm (word)         options ring: the word, and "hear it"; turn an L-hand like a knob to pick
  L-hand tilt (sentence)   tone dial, warm to the right, cold to the left (preview only)
  two L-hands (paragraph)  length stretch (preview only)
  pinch + lift             commit: "hear it" speaks the sentence with the word stressed;
                           tone and length say they are not available yet (milestone 8)
  drop the hand for 1 s    back out
  fist raised into view, held 1 s
                           start a take after a 3-2-1 count-in

Rehearse listens only to the command zone, top right:
  flick sideways           next section
  open palm held 1.5 s     stop the take (or cancel the count-in), on to Review
The notes follow your voice (the current sentence in orange, the next
section shown faint as you start the last sentence of one); a flick, n or b
moves by hand and the voice carries on from there. Off with --no-follow.

Review browses and focuses like Prepare, without Prepare's operations.
Each take is transcribed, measured and judged in the background as soon as
it stops; the label shows TRANSCRIBING, then how many marks were hit, and
the terminal prints the full report. Every delivery mark is then drawn as a
chip coloured by its verdict (green hit, red missed, grey unclear):

  two fingers together     browse sentences
  fold onto the thumb      focus: each mark's verdict and why, pace, fillers
  L-hand turned (focused)  dial through the takes that said this sentence
  pinch + lift (focused)   drill the sentence: count-in, then just that
                           sentence; open palm in the zone to stop
  fist raised, held 1 s    new full take
  open palm held 1.5 s in the command zone
                           back to Prepare, to edit before the next take

Word alternatives, tone and length edits come with milestone 8; until then
they preview and say so, and never report a change. Poses and events are
logged to sessions/gesture-logs/; each take is saved as a WAV in its session
folder under sessions/, with its transcript, pitch and verdicts and
session.json.

Keys, the fallback when gestures won't do (h shows them in the app):
  t start a take, x stop it (or cancel the count-in), n next section, b previous section,
  p back to Prepare from Review, space/j next sentence, k previous (in
  Rehearse within the section; in a focused panel they scroll it),
  r retry failed analysis, h keys, q/Esc quit.
Dev keys: d toggle landmarks and hand box, s save a screenshot to sessions/screens/.
"""

import argparse
import json
import math
import sys
import time
import traceback
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2

from palmcards.align import counts
from palmcards.capture import AudioRecorder, Camera, CameraError
from palmcards.config import ANALYSIS, FOLLOW, RECORDING, SPEECH
from palmcards.gestures import GestureEvent, GestureLog, Grammar, HandTracker, ModeMachine
from palmcards.notes import Notes, notes_from_bytes
from palmcards.render import (
    HEAR_IT, Hit, OpsView, TextOverlay, ViewState,
    draw_fingertips, draw_hand_box, draw_landmarks, draw_stats, draw_zone_outline,
)
from palmcards.review import Board
from palmcards.recording import TakeWriter
from palmcards.session import SESSIONS_DIR, Session, SessionError, recover_all
from palmcards.style import TEXT
from palmcards.tts import get_speaker
from palmcards.analysis import Supervisor
from palmcards.asr import get_recognizer
from palmcards.follow import LiveFollow
from palmcards.speech import make_job

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
SCREENS_DIR = SESSIONS_DIR / "screens"
WINDOW = "PalmCards"
NOTE_S = 1.5  # how long a commit message stays in the label
KEY_COMMANDS = {ord("t"): "start", ord("x"): "stop", ord("n"): "next", ord("b"): "previous",
                ord("p"): "prepare"}  # ModeMachine.command


def sync_view(grammar: Grammar, view: ViewState, overlay: TextOverlay) -> None:
    """Copy the gesture state into the view after each hand result."""
    gs = grammar.state
    view.mode, view.level, view.drop_progress = gs.mode, gs.level, gs.drop_progress
    if gs.mode == "browse" and gs.cursor is not None:
        word_level = gs.level == "word"
        hit = overlay.hit_test(*overlay.cursor_to_text(*gs.cursor), view.scroll, snap=word_level)
        if hit is not None:
            view.hover = hit if word_level else Hit(hit.sentence, None)
            view.current = hit.sentence
    elif gs.mode == "idle":
        view.hover = None
    ops = view.ops
    ops.kind, ops.pointing, ops.tone, ops.stretch = gs.op, gs.pointing, gs.tone, gs.stretch
    ops.stretch_ends = gs.stretch_ends
    if gs.op == "ring":
        ops.picked = gs.knob % len(overlay.ring_labels(view))


def apply_event(ev: GestureEvent, view: ViewState, overlay: TextOverlay, log: GestureLog,
                speaker=None) -> float | None:
    """Grammar events. Returns the time a label note should expire, if one was set.
    A commit only ever reports what really happened."""
    if ev.kind == "focus":
        if view.hover is None:  # focused before the cursor ever touched the text
            view.hover = Hit(view.current, 0 if ev.level == "word" else None)
        view.focus = view.hover
        view.ops = OpsView()
        view.panel_scroll = 0.0
        return None
    if ev.kind == "commit" and view.app == "review":
        if ev.level != "sentence":  # a sentence commit is a drill, which the mode events start
            view.note = "TO DRILL: FOCUS A SENTENCE, PINCH + LIFT"
    elif ev.kind == "commit":
        sentence = view.focus.sentence if view.focus else None
        if ev.op == "ring" and overlay.ring_labels(view)[view.ops.picked] == HEAR_IT and view.focus is not None:
            s = overlay.sentences[view.focus.sentence]
            stressed = {m.word for m in s.marks if m.kind == "stress"} | {view.focus.word}
            try:
                (speaker or get_speaker()).say_words([w.text for w in s.words], stressed)
                view.note = f'SPEAKING, STRESSING "{overlay.word_text(view.focus).upper()}"'
            except OSError as exc:
                view.note = "COULD NOT SPEAK (SEE TERMINAL)"
                print(f"text to speech failed: {exc}", file=sys.stderr)
            log(ev.t, "hear", sentence=sentence, word=view.focus.word)
        elif ev.op in ("tone", "stretch"):
            what = "TONE" if ev.op == "tone" else "LENGTH"
            log(ev.t, "commit_stub", level=ev.level, sentence=sentence, op=ev.op, value=ev.value)
            view.note = f"{what} EDITS ARE NOT AVAILABLE YET: NOTHING CHANGED"
        else:
            view.note = "NO CHANGE"
    view.focus = None
    view.ops = OpsView()
    return time.perf_counter() + NOTE_S if ev.kind == "commit" and view.note else None


class Takes:
    """The recording side of the modes: microphone, sections, saved takes.

    A take is streamed to disk while it is recorded (palmcards.recording).
    Stopping hands the writer over to finish in the background; poll()
    adds the finished take to the session and starts its analysis. close()
    is safe to call more than once and after a failure: a take still being
    recorded is finished and kept ("interrupted" if the app is closing
    because of an error or Ctrl-C).
    """

    def __init__(self, notes: Notes, session: Session, log: GestureLog, devices: "Devices | None" = None,
                 follow: bool = FOLLOW.enabled):
        self.notes, self.session, self.log = notes, session, log
        self.devices = devices or Devices()
        self.follow_enabled = follow
        self.follow: LiveFollow | None = None  # the voice follow, made at the first count-in
        self._follow_reported = False
        self.recorder: AudioRecorder | None = None
        self.analysis = Supervisor()
        self.deferred: list[int] = []  # takes the analysis queue had no room for yet
        self._log_seen = 0
        self.t0 = time.perf_counter()  # the app clock's zero; main() sets it
        self.section = 0
        self.writer: TakeWriter | None = None  # the take being recorded
        self.finalizing: list[TakeWriter] = []  # stopped, still writing
        self.alert = ""  # a recording problem, shown until the next take starts
        self.interrupted = False  # the app is closing because of an error or Ctrl-C
        self._closed = False
        self.last_saved = ""
        self.board = Board(notes)  # verdicts of the judged takes, for Review
        self.drill: int | None = None  # the sentence the current count-in or take drills
        self.dial_seen = 0  # take-dial steps already applied

    def status(self, mode: str, view: ViewState) -> str:
        """Second label line when nothing more pressing is shown."""
        if mode in ("count_in", "rehearse") and self.drill is not None:
            return f"SENTENCE {self.drill + 1}"
        if mode in ("count_in", "rehearse"):
            title = self.notes.sections[self.section].title or "untitled"
            return f"SECTION {self.section + 1}/{len(self.notes.sections)}: {title.upper()}"
        if mode == "review" and view.mode == "focus" and view.level == "sentence" and view.focus is not None:
            return f"{self.board.take_label(view.focus.sentence)}  /  L-HAND: TAKES  /  PINCH + LIFT: DRILL"
        if self.finalizing:
            return f"TAKE {self.finalizing[0].number}: SAVING..."
        if self.analysis.pending or self.deferred:
            dots = "." * (int(time.perf_counter() * 2) % 4)
            take = (self.analysis.pending or self.deferred)[0]
            return f"TAKE {take}: TRANSCRIBING{dots:<3}"
        if mode == "review" and self.last_saved:
            return f"{self.last_saved}  /  RAISE A FIST: NEW TAKE"
        return "RAISE A FIST: START A TAKE"

    def alert_line(self, mode: str = "") -> str:
        """Persistent trouble (recording, analysis, voice follow), shown until it is dealt with."""
        if self.alert:
            return self.alert
        if mode in ("count_in", "rehearse") and self.follow is not None and self.follow.state == "failed" \
                and self.drill is None:
            return "VOICE FOLLOW OFF (SEE TERMINAL)  /  FLICK OR N: NEXT SECTION"
        if failed := self.analysis.failed():
            return f"TAKE {failed[0]}: ANALYSIS FAILED (SEE TERMINAL)  /  R: RETRY"
        return ""

    def sync_review(self, grammar: Grammar, view: ViewState) -> None:
        """Review: turn the take dial, and show the chosen takes' verdicts."""
        gs = grammar.state
        focused = view.focus.sentence if view.mode == "focus" and view.focus is not None else None
        if gs.op == "take" and focused is not None and view.level == "sentence":
            self.board.step(focused, gs.take_step - self.dial_seen)
            self.dial_seen = gs.take_step
        view.mark_verdicts = self.board.mark_verdicts()
        view.detail = self.board.detail(focused) if focused is not None and view.level == "sentence" else ()

    def on_transcribed(self, result: dict) -> str:
        """A take's transcript and alignment arrived. Returns a label note."""
        n = result["take"]
        if not result["ok"]:
            print(f"take {n}: analysis failed: {result.get('error')}. Press r to retry, or run: "
                  f"python -m palmcards.speech {self.session.dir} --take {n}", file=sys.stderr)
            return ""
        self.session.set_result(n, result["transcript"], result["alignment"], result["verdicts"], result["marks"])
        take = self.session.take(n)
        self.board.add(n, result["verdict_data"], take.drill)
        self.log(time.perf_counter() - self.t0, "transcribed", take=n, seconds=result["seconds"])
        print(f"take {n} (transcribed and judged in {result['seconds']:.1f} s)\n{result['report']}")
        if take.drill is not None:
            self.last_saved = f"TAKE {n} (DRILL): {self.board.summary(n)}"
            return ""
        c = counts(result["alignment"])
        parts = [f"{c['spoken']}/{len(result['alignment']['sentences'])} SPOKEN", self.board.summary(n)]
        parts += [f"{c[k]} {k.upper()}" for k in ("fillers", "restarts") if c[k]]
        self.last_saved = f"TAKE {n}: {', '.join(parts)}"
        return ""

    def handle(self, ev: GestureEvent, modes: ModeMachine, view: ViewState, overlay: TextOverlay) -> str:
        """Mode events. Returns a label note to show, or ""."""
        if ev.kind in ("count_in", "drill"):
            self.drill = ev.sentence if ev.kind == "drill" else None
            if self.drill is not None:
                self.log(ev.t, "drill", sentence=self.drill)
            try:
                if self.recorder is None:
                    self.recorder = self.devices.recorder(clock=lambda: time.perf_counter() - self.t0)
                self.recorder.open()
            except Exception as exc:  # no input device, PortAudio error
                print(f"Could not open the microphone: {exc}. On macOS, allow Microphone access for "
                      "your terminal app in System Settings > Privacy & Security > Microphone.",
                      file=sys.stderr)
                self.log(ev.t, "mic_error", error=str(exc))
                modes.cancel_count_in(ev.t)
                self.drill = None
                return "MICROPHONE UNAVAILABLE"
            self.section = 0 if self.drill is None else self.notes.sentences[self.drill].section
            view.current = self.drill if self.drill is not None else self.first_of(self.section)
            view.panel_scroll, view.preview_next = 0.0, False
            if self.drill is None and self._ensure_follow():
                self.follow.prepare()  # the model loads during the count-in
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "to_prepare":
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "count_in_cancel":
            self.recorder.close()
            self.drill = None
            return "TAKE CANCELLED"
        if ev.kind == "take_start":
            self.alert = ""
            try:
                number = self.session.begin_take()
                self.writer = TakeWriter(self.session.dir, number, self.recorder.rate, {
                    "started": datetime.now().isoformat(timespec="milliseconds"), "requested_t": round(ev.t, 3),
                    "drill": self.drill, "revision": self.session.current_revision})
            except (OSError, SessionError) as exc:
                print(f"cannot record: {exc}", file=sys.stderr)
                self.log(ev.t, "record_error", error=str(exc))
                self.alert = "CANNOT RECORD (SEE TERMINAL): HOLD OPEN PALM TO STOP"
                return ""
            self.writer.mark_section(ev.t, self.section, "start")
            self.recorder.start(self.writer)
            if self.drill is None and self.follow is not None:
                self.follow.start_take(self.section)
                self.recorder.tap = self.follow.tap
            self.log(ev.t, "take_start", take=number)
            return ""
        if ev.kind == "next_section":
            if self.section + 1 >= len(self.notes.sections):
                return "LAST SECTION"
            source = "key" if ev.source == "key" else "flick"
            self.section += 1
            view.current, view.panel_scroll, view.preview_next = self.first_of(self.section), 0.0, False
            if self.writer is not None:
                self.writer.mark_section(ev.t, self.section, source)
            if self.follow is not None:
                self.follow.flick(ev.t)  # the voice carries on from the new section
            self.log(ev.t, "section", section=self.section, source=source)
            return ""
        if ev.kind == "previous_section":  # a key: undo a wrong move
            if self.section == 0:
                return "FIRST SECTION"
            self.section -= 1
            view.current, view.panel_scroll, view.preview_next = self.first_of(self.section), 0.0, False
            if self.writer is not None:
                self.writer.mark_section(ev.t, self.section, "key")
            if self.follow is not None:
                self.follow.jump(self.section, view.current)
            self.log(ev.t, "section", section=self.section, source="key")
            return ""
        if ev.kind == "take_stop":
            writer = self._stop_recording()
            if writer is not None:
                self.log(ev.t, "take_stop", take=writer.number, duration_s=round(writer.seconds, 3), wav=writer.wav)
            if self.drill is not None:
                view.current = self.drill
            else:
                view.current = next(i for i, s in enumerate(overlay.sentences) if s.section == self.section)
            self.drill = None
            view.scroll = overlay.scroll_to(view.current)
            return ""
        return ""

    def first_of(self, section: int) -> int:
        return next(i for i, s in enumerate(self.notes.sentences) if s.section == section)

    def section_sentences(self) -> list[int]:
        return [i for i, s in enumerate(self.notes.sentences) if s.section == self.section]

    def _ensure_follow(self) -> bool:
        """The voice follow, made once the microphone's rate is known."""
        if self.follow is None and self.follow_enabled and self.recorder is not None:
            clock = lambda: time.perf_counter() - self.t0  # noqa: E731
            hints = tuple(s.text for s in self.notes.sentences)
            model = SPEECH.live_model if SPEECH.backend == "mlx-whisper" else SPEECH.backend
            self.follow = LiveFollow(self.notes, lambda: self.devices.live(self.session.language, clock, hints),
                                     self.recorder.rate, SPEECH.backend, model)
        return self.follow is not None

    def poll_follow(self, view: ViewState, overlay: TextOverlay) -> None:
        """Move the display with the voice: the sentence, the section (recorded
        with source "voice"), and the preview of the next section."""
        if self.follow is None or self.writer is None or self.drill is not None:
            return
        for ev in self.follow.poll():
            if ev.kind == "section" and ev.index == self.section + 1:
                self.section = ev.index
                self.writer.mark_section(ev.t, ev.index, "voice")
                self.log(ev.t, "section", section=ev.index, source="voice")
            elif ev.kind == "sentence":
                view.current = ev.index
        if self.follow.state == "failed" and not self._follow_reported:
            print(f"voice follow off: {self.follow.error}", file=sys.stderr)
            self._follow_reported = True
        section = self.section_sentences()
        view.section = self.section
        view.preview_next = view.current == section[-1] and self.section + 1 < len(self.notes.sections)
        view.panel_scroll = overlay.panel_scroll_to(view, view.current)

    def _stop_recording(self) -> "TakeWriter | None":
        """Stop the microphone; the take finishes writing in the background."""
        writer, self.writer = self.writer, None
        if self.follow is not None and self.follow.follower is not None:
            live = self.follow.stop_take()
            if writer is not None:
                writer.meta["live"] = live
        if self.recorder is not None:
            self.recorder.tap = None
            self.recorder.stop()
            self.recorder.close()
        if writer is not None:
            self.finalizing.append(writer)
        return writer

    def recording_problem(self) -> None:
        """Surface a disk error while a take is still being recorded."""
        if self.writer is not None and self.writer.error and not self.alert:
            print(f"take {self.writer.number}: recording failed: {self.writer.error}; the audio up to here is kept",
                  file=sys.stderr)
            self.alert = "RECORDING FAILED: AUDIO SO FAR KEPT (SEE TERMINAL)"

    def poll(self) -> str:
        """Add takes that finished writing to the session. Returns a label note."""
        note = ""
        for writer in [w for w in self.finalizing if w.done.is_set()]:
            self.finalizing.remove(writer)
            note = self._finish(writer, "saved") or note
        return note

    def _finish(self, writer: TakeWriter, status: str) -> str:
        manifest = writer.manifest()
        if manifest["samples"] == 0:
            print(f"take {writer.number}: no audio was written ({writer.error or 'none arrived'})", file=sys.stderr)
            self.alert = "TAKE NOT SAVED: NO AUDIO (SEE TERMINAL)"
            return ""
        take = self.session.finish_take(manifest, None if status == "saved" else status)
        m, s = divmod(round(take.duration_s), 60)
        gaps = take.capture["dropped_samples"] / take.sample_rate if take.capture else 0.0
        print(f"saved {self.session.dir / take.wav} ({take.duration_s:.1f} s, {take.status}"
              + (f", {gaps:.2f} s of audio lost" if gaps else "") + ")")
        if take.status != "saved":
            self.alert = f"TAKE {take.number} {take.status.upper()}: KEPT, NOT ANALYSED (SEE TERMINAL)"
            print(f"take {take.number} is {take.status}; analyse it anyway with: python -m palmcards.speech "
                  f"{self.session.dir} --take {take.number} --incomplete", file=sys.stderr)
            return ""
        self._submit(take.number)
        self.last_saved = f"TAKE {take.number} SAVED ({m}:{s:02d})"
        if take.silent:
            print("warning: the take is silent. On macOS, allow Microphone access for your terminal "
                  "app in System Settings > Privacy & Security > Microphone.", file=sys.stderr)
            return "TAKE IS SILENT: CHECK MICROPHONE ACCESS"
        return ""

    def _submit(self, number: int) -> None:
        take = self.session.take(number)
        if self.analysis.submit(self.session.dir, make_job(self.session, take, self.session.notes_for(take))):
            if number in self.deferred:
                self.deferred.remove(number)
        elif number not in self.deferred:
            self.deferred.append(number)  # queue full: tried again every frame

    def poll_analysis(self) -> str:
        """Results in, deferred takes resubmitted, worker trouble to the terminal."""
        note = ""
        for result in self.analysis.poll():
            note = self.on_transcribed(result) or note
        for number in list(self.deferred):
            self._submit(number)
        for line in self.analysis.log[self._log_seen:]:
            print(f"analysis: {line}", file=sys.stderr)
        self._log_seen = len(self.analysis.log)
        return note

    def retry(self) -> str:
        n = self.analysis.retry_failed()
        return f"RETRYING ANALYSIS OF {n} TAKE{'S' if n != 1 else ''}" if n else "NOTHING TO RETRY"

    def close(self) -> None:
        """Finish any take (kept as "interrupted" when closing on an error), release
        the microphone, then let pending transcriptions finish unless Ctrl-C.
        Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        active = self._stop_recording()  # the take being recorded right now, if any
        for writer in self.finalizing:
            if writer.wait(RECORDING.finalize_timeout_s):
                try:
                    self._finish(writer, "interrupted" if writer is active and self.interrupted else "saved")
                except Exception:  # keep closing; the recording is on disk and recovered at the next start
                    traceback.print_exc()
            else:
                print(f"take {writer.number} is still being written; it will be recovered at the next start",
                      file=sys.stderr)
        self.finalizing = []
        # Bounded: wait a while for analysis already running, then leave it for later.
        if self.analysis.pending and not self.interrupted:
            print(f"finishing analysis of take {', '.join(map(str, self.analysis.pending))} "
                  f"(up to {ANALYSIS.shutdown_s:.0f} s; Ctrl-C to leave it for later)")
            deadline = time.monotonic() + ANALYSIS.shutdown_s
            try:
                while self.analysis.pending and time.monotonic() < deadline:
                    self.poll_analysis()
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
        if self.follow is not None:
            self.follow.close()
        left = self.analysis.close(timeout=1.0)
        self.poll_analysis()
        if left or self.deferred:
            takes = sorted(set(left) | set(self.deferred))
            print(f"analysis of take {', '.join(map(str, takes))} left for later: python -m palmcards.speech "
                  f"{self.session.dir}")
        self.session.release()


@dataclass
class Devices:
    """What the app opens. Tests pass fakes, so failures can be injected
    without a camera, a microphone or a window."""
    camera: Callable = Camera
    tracker: Callable = HandTracker
    recorder: Callable = AudioRecorder
    log: Callable = GestureLog.to_session_dir
    speaker: Callable = get_speaker
    live: Callable = lambda language, clock, hints: get_recognizer().live(language, clock, SPEECH.live_where, hints)
    named_window: Callable = lambda name, w, h: (cv2.namedWindow(name, cv2.WINDOW_NORMAL), cv2.resizeWindow(name, w, h))
    show: Callable = cv2.imshow
    wait_key: Callable = lambda: cv2.waitKey(1) & 0xFF
    window_open: Callable = lambda name: cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) >= 1
    destroy_windows: Callable = cv2.destroyAllWindows


def guarded(fn: Callable[[], None], what: str) -> Callable[[], None]:
    """A cleanup step that reports its own failure instead of raising, so the
    other steps still run and the error that started the shutdown survives."""
    def run() -> None:
        try:
            fn()
        except BaseException:
            print(f"error while closing {what}:", file=sys.stderr)
            traceback.print_exc()
    return run


def main() -> int:
    ap = argparse.ArgumentParser(description="PalmCards rehearsal mirror")
    ap.add_argument("notes", nargs="?", type=Path, default=SAMPLE, help=".txt, .md or .docx")
    ap.add_argument("--lang", default=SPEECH.language, help="language of the takes, for Whisper")
    ap.add_argument("--trace", action="store_true", help="record hand landmarks for offline replay")
    ap.add_argument("--no-follow", action="store_true", help="don't follow the voice during takes")
    args = ap.parse_args()
    path = args.notes
    try:
        source = path.read_bytes()  # parsed once and kept byte for byte in the session
        notes = notes_from_bytes(source, path)
    except (OSError, ValueError) as exc:
        print(f"Could not open notes: {exc}", file=sys.stderr)
        return 1
    for warning in notes.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for line in recover_all():
        print(f"recovered: {line}")
    try:
        return run(path, notes, source, lang=args.lang, trace=args.trace, follow=not args.no_follow)
    except CameraError as exc:
        print(exc, file=sys.stderr)
        return 1


def run(path: Path, notes: Notes, source: bytes, lang: str = SPEECH.language, trace: bool = False,
        devices: Devices | None = None, sessions_root: Path = SESSIONS_DIR, follow: bool = FOLLOW.enabled) -> int:
    """Open everything, run the frame loop, close everything.

    Every resource is registered for cleanup as soon as it exists, so a
    failure while opening the next one, a camera or drawing error mid-loop,
    or Ctrl-C still closes what was opened, in reverse order, each step on
    its own; a take being recorded is kept."""
    devices = devices or Devices()
    sentences = notes.sentences
    with ExitStack() as stack:
        camera = devices.camera()
        stack.callback(guarded(camera.release, "the camera"))
        tracker = devices.tracker()
        stack.callback(guarded(tracker.close, "hand tracking"))
        log = devices.log()
        stack.callback(guarded(log.close, "the gesture log"))
        trace_file = log.path.with_suffix(".trace.jsonl").open("w") if trace and log.path else None
        if trace_file:
            stack.callback(guarded(trace_file.close, "the trace"))
        session = Session.create(path, root=sessions_root, gesture_log=log.path, language=lang, parsed=notes,
                                 source=source)
        takes = Takes(notes, session, log, devices, follow)
        stack.callback(guarded(takes.close, "the takes"))
        stack.callback(guarded(devices.destroy_windows, "the window"))
        try:
            frame_loop(camera, tracker, log, trace_file, takes, sentences, devices)
        except BaseException:
            takes.interrupted = True
            raise
    return 0


def frame_loop(camera, tracker, log: GestureLog, trace, takes: "Takes", sentences, devices: Devices) -> None:
    frame = camera.read()
    h, w = frame.shape[:2]
    overlay = TextOverlay(sentences, (w, h))
    modes = ModeMachine((w, h), log)
    grammar = modes.grammar
    view = ViewState()
    show_debug = False
    note_until = None
    fps, work_ms, last = 0.0, 0.0, time.perf_counter()
    t0 = prev_start = last
    takes.t0 = t0
    devices.named_window(WINDOW, w, h)
    speaker = devices.speaker()
    queued: list[GestureEvent] = []  # from keys, handled with the next frame's events
    page_t, page_pause_until = last, 0.0

    while True:
        frame = camera.read()
        start = time.perf_counter()
        tracker.submit(frame, start - t0)
        # Hand results arrive asynchronously, usually one frame behind.
        if (result := tracker.poll()) is not None:
            if trace:
                hands, t_hand = result
                trace.write(json.dumps({"t": round(t_hand, 3), "hands": [
                    {"label": hd.handedness, "points": hd.points.round(1).tolist()} for hd in hands]}) + "\n")
            events = modes.update(*result)
        else:
            events = []
        events += modes.tick(start - t0)
        events, queued = queued + events, []
        if note := takes.poll_analysis():
            view.note, note_until = note, start + NOTE_S
        if note := takes.poll():
            view.note, note_until = note, start + NOTE_S
        takes.recording_problem()
        for ev in events:  # a drill rehearses the sentence focused now, before any handler clears the focus
            if ev.kind == "drill" and ev.sentence is None:
                ev.sentence = view.focus.sentence if view.focus else view.current
        for ev in events:
            if ev.kind == "focus":
                takes.dial_seen = 0
            if ev.kind in ("focus", "commit", "back"):
                until = apply_event(ev, view, overlay, log, speaker)
            elif note := takes.handle(ev, modes, view, overlay):
                view.note, until = note, start + NOTE_S
            else:
                until = None
            if until is not None:
                note_until = until
        view.app = modes.mode
        zone = modes.zone
        view.zone_active, view.hold_progress, view.flick_progress = zone.active, zone.hold_progress, zone.flick_progress
        view.drill = takes.drill if modes.mode in ("count_in", "rehearse") else None
        if modes.mode in ("prepare", "review"):
            if result is not None:
                sync_view(grammar, view, overlay)
            view.start_progress = modes.start_progress
            if modes.mode == "review":
                takes.sync_review(grammar, view)
            else:
                view.mark_verdicts, view.detail = (), ()
        else:
            if modes.mode == "rehearse":
                takes.poll_follow(view, overlay)
            view.section = takes.section
            view.count_in = max(1, math.ceil(modes.count_in_end - (start - t0)))
            view.rec_s = takes.recorder.seconds if modes.mode == "rehearse" and takes.recorder else 0.0
            view.mic = takes.recorder.level if takes.recorder else 0.0
            view.start_progress = 0.0
            view.mark_verdicts, view.detail = (), ()
        view.status = takes.status(modes.mode, view)
        view.alert = takes.alert_line(modes.mode)
        # A focused panel taller than the frame turns its own pages, so every
        # verdict line is reachable without keys; a key pauses it.
        if view.app in ("prepare", "review") and view.mode == "focus" and (most := overlay.panel_max_scroll(view)):
            if start >= page_pause_until and start - page_t >= TEXT.page_s:
                page = overlay.panel_view_h(overlay.panel(view)) - overlay.line_h
                view.panel_scroll = 0.0 if view.panel_scroll >= most else min(most, view.panel_scroll + page)
                page_t = start
        elif view.app in ("prepare", "review"):
            view.panel_scroll, page_t = 0.0, start
        # Edge scrolling advances every displayed frame so it stays smooth.
        if view.app in ("prepare", "review") and view.mode == "browse" and grammar.state.scroll_rate:
            view.scroll = overlay.clamp_scroll(view.scroll + grammar.state.scroll_rate * (start - prev_start))
        prev_start = start
        if note_until is not None and start > note_until:
            view.note, note_until = "", None

        overlay.draw(frame, view)
        if show_debug:
            if view.app != "prepare":
                draw_zone_outline(frame, modes.zone)
            else:
                draw_hand_box(frame, grammar.cursor)
            for track in (grammar.state.primary, grammar.state.secondary):
                if track is not None:
                    draw_landmarks(frame, track.hand)
        draw_fingertips(frame, grammar.state)

        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
        work_ms = 0.9 * work_ms + 0.1 * (now - start) * 1000
        last = now
        # cam: rate the camera delivers; shown: rate we display;
        # hands: tracker latency; work: our per-frame processing.
        stats = (f"cam {camera.fps:4.1f}  shown {fps:4.1f} fps  "
                 f"hands {tracker.latency_ms:4.1f} ms  work {work_ms:4.1f} ms")
        draw_stats(frame, stats)

        devices.show(WINDOW, frame)
        key = devices.wait_key()
        if key in (ord("q"), 27):
            break
        now_t = time.perf_counter() - t0
        if key in KEY_COMMANDS:
            queued += modes.command(KEY_COMMANDS[key], now_t)
        elif key in (ord(" "), ord("j"), ord("k")):
            step = -1 if key == ord("k") else 1
            if view.app in ("count_in", "rehearse") and view.drill is None:  # within the section
                unit = takes.section_sentences()
                i = unit.index(view.current) if view.current in unit else 0
                view.current = unit[min(max(i + step, 0), len(unit) - 1)]
                view.panel_scroll = overlay.panel_scroll_to(view, view.current)
                if takes.follow is not None:
                    takes.follow.jump(takes.section, view.current)  # the voice carries on from here
            elif view.mode == "focus" and overlay.panel_max_scroll(view):  # scroll the focused panel
                view.panel_scroll = overlay.clamp_panel_scroll(view, view.panel_scroll + step * 3 * overlay.line_h)
                page_pause_until = time.perf_counter() + TEXT.page_pause_s
            elif view.app in ("prepare", "review"):
                view.current = min(max(view.current + step, 0), len(sentences) - 1)
                if not overlay.is_visible(view.current, view.scroll):
                    view.scroll = overlay.scroll_to(view.current)
        elif key == ord("h"):
            view.keys_help = not view.keys_help
        elif key == ord("d"):
            show_debug = not show_debug
        elif key == ord("r"):
            view.note, note_until = takes.retry(), time.perf_counter() + NOTE_S
        elif key == ord("s"):
            SCREENS_DIR.mkdir(parents=True, exist_ok=True)
            shot = SCREENS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.png"
            cv2.imwrite(str(shot), frame)
            log(time.perf_counter() - t0, "screenshot", path=shot.name)
            print(f"saved {shot}")
        if not devices.window_open(WINDOW):
            break

    if modes.mode == "rehearse":  # quit mid-take: keep what was recorded, as a normal take
        takes.handle(GestureEvent("take_stop", time.perf_counter() - t0), modes, view, overlay)


if __name__ == "__main__":
    sys.exit(main())
