"""PalmCards entry point.

  python main.py [NOTES_FILE] [--trace]
      NOTES_FILE: .txt, .md or .docx; defaults to the sample
      --trace: also record every hand result's landmarks, for offline replay

Mirrored webcam feed with the notes overlaid in the demo style. Prepare mode
follows Kat's gesture grammar (see CLAUDE.md). Hold your hand in the box on
the right of the frame; it steers the highlight in the text on the left.

  one finger up / two fingers together / flat hand
                           browse by word / sentence / paragraph
  top or bottom of the box scroll
  pinch (word), fold fingers onto the thumb (sentence, paragraph)
                           focus
  open palm (word)         options ring (placeholders); turn an L-hand like a knob to pick
  L-hand tilt (sentence)   tone dial, warm to the right, cold to the left
  two L-hands (paragraph)  length stretch
  pinch + lift             commit (logged only, no text changes yet)
  drop the hand for 1 s    back out
  closed fist held 1 s     start a take after a 3-2-1 count-in

Rehearse listens only to the command zone, top right:
  flick                    next section
  open palm held 1.5 s     stop the take (or cancel the count-in), on to Review

Review browses and focuses like Prepare, without the operations (verdicts
come in milestone 6); a fist held 1 s starts a new take.

Operations are stubs until milestone 8. Poses and events are logged to
sessions/gesture-logs/; each take is saved as a WAV in its session folder
under sessions/, with session.json.

Dev keys: space/j next sentence, k previous, d toggle landmarks and hand box,
s save a screenshot to sessions/screens/, q/Esc quit.
"""

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

from palmcards.capture import AudioRecorder, Camera, CameraError
from palmcards.gestures import (
    GestureEvent, GestureLog, Grammar, HandTracker, ModeMachine,
    draw_fingertips, draw_hand_box, draw_landmarks, draw_zone,
)
from palmcards.notes import Notes, load_notes
from palmcards.render import Hit, OpsView, TextOverlay, ViewState
from palmcards.session import Session

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
SCREENS_DIR = Path(__file__).parent / "sessions" / "screens"
WINDOW = "PalmCards"
NOTE_S = 1.5  # how long a commit message stays in the label


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


def apply_event(ev: GestureEvent, view: ViewState, overlay: TextOverlay, log: GestureLog) -> float | None:
    """Grammar events. Returns the time a label note should expire, if one was set."""
    if ev.kind == "focus":
        if view.hover is None:  # focused before the cursor ever touched the text
            view.hover = Hit(view.current, 0 if ev.level == "word" else None)
        view.focus = view.hover
        view.ops = OpsView()
        return None
    if ev.kind == "commit" and view.app == "review":
        log(ev.t, "drill_stub", sentence=view.focus.sentence if view.focus else None)
        view.note = "DRILL (STUB, MILESTONE 6)"
    elif ev.kind == "commit":
        if ev.op == "ring":
            what = overlay.ring_labels(view)[view.ops.picked]
        elif ev.op == "tone":
            what = f"tone {ev.value:+.2f}"
        elif ev.op == "stretch":
            what = f"length x{ev.value:.2f}"
        else:
            what = "no change"
        log(ev.t, "commit_stub", level=ev.level, sentence=view.focus.sentence if view.focus else None,
            result=what)
        view.note = f"COMMITTED (STUB): {what.upper()}"
    view.focus = None
    view.ops = OpsView()
    return time.perf_counter() + NOTE_S if ev.kind == "commit" else None


class Takes:
    """The recording side of the modes: microphone, sections, saved takes."""

    def __init__(self, notes: Notes, session: Session, log: GestureLog):
        self.notes, self.session, self.log = notes, session, log
        self.recorder: AudioRecorder | None = None
        self.section = 0
        self.t_start = 0.0
        self.started = datetime.now()
        self.marks: list[tuple[float, int]] = []
        self.last_saved = ""

    def status(self, mode: str) -> str:
        """Second label line when nothing more pressing is shown."""
        if mode in ("count_in", "rehearse"):
            title = self.notes.sections[self.section].title or "untitled"
            return f"SECTION {self.section + 1}/{len(self.notes.sections)}: {title.upper()}"
        if mode == "review" and self.last_saved:
            return f"{self.last_saved}  /  HOLD FIST: NEW TAKE"
        return "HOLD FIST: START A TAKE"

    def handle(self, ev: GestureEvent, modes: ModeMachine, view: ViewState, overlay: TextOverlay) -> str:
        """Mode events. Returns a label note to show, or ""."""
        if ev.kind == "count_in":
            try:
                if self.recorder is None:
                    self.recorder = AudioRecorder()
                self.recorder.open()
            except Exception as exc:  # no input device, PortAudio error
                print(f"Could not open the microphone: {exc}. On macOS, allow Microphone access for "
                      "your terminal app in System Settings > Privacy & Security > Microphone.",
                      file=sys.stderr)
                self.log(ev.t, "mic_error", error=str(exc))
                modes.cancel_count_in(ev.t)
                return "MICROPHONE UNAVAILABLE"
            self.section = 0
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "count_in_cancel":
            self.recorder.close()
            return "TAKE CANCELLED"
        if ev.kind == "take_start":
            self.recorder.start()
            self.t_start, self.started, self.marks = ev.t, datetime.now(), [(0.0, 0)]
            self.log(ev.t, "take_start", take=len(self.session.takes) + 1)
            return ""
        if ev.kind == "next_section":
            if self.section + 1 >= len(self.notes.sections):
                return "LAST SECTION"
            self.section += 1
            self.marks.append((ev.t - self.t_start, self.section))
            self.log(ev.t, "section", section=self.section)
            return ""
        if ev.kind == "take_stop":
            audio = self.recorder.stop()
            self.recorder.close()
            take = self.session.add_take(audio, self.recorder.rate, self.t_start, self.started, self.marks)
            self.log(ev.t, "take_stop", take=take.number, duration_s=take.duration_s, wav=take.wav)
            print(f"saved {self.session.dir / take.wav} ({take.duration_s:.1f} s)")
            view.current = next(i for i, s in enumerate(overlay.sentences) if s.section == self.section)
            view.scroll = overlay.scroll_to(view.current)
            m, s = divmod(round(take.duration_s), 60)
            self.last_saved = f"TAKE {take.number} SAVED ({m}:{s:02d})"
            if take.silent:
                print("warning: the take is silent. On macOS, allow Microphone access for your terminal "
                      "app in System Settings > Privacy & Security > Microphone.", file=sys.stderr)
                return "TAKE IS SILENT: CHECK MICROPHONE ACCESS"
            return ""
        return ""

    def close(self) -> None:
        if self.recorder is not None:
            self.recorder.close()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0]) if args else SAMPLE
    try:
        notes = load_notes(path)
    except (OSError, ValueError) as exc:
        print(f"Could not open notes: {exc}", file=sys.stderr)
        return 1
    for warning in notes.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    sentences = notes.sentences

    try:
        camera = Camera()
    except CameraError as exc:
        print(exc, file=sys.stderr)
        return 1

    tracker = HandTracker()
    log = GestureLog.to_session_dir()
    trace = log.path.with_suffix(".trace.jsonl").open("w") if "--trace" in sys.argv else None
    takes = Takes(notes, Session.create(path, gesture_log=log.path), log)
    with camera:
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

        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, w, h)

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
            for ev in events:
                if ev.kind in ("focus", "commit", "back"):
                    until = apply_event(ev, view, overlay, log)
                elif note := takes.handle(ev, modes, view, overlay):
                    view.note, until = note, start + NOTE_S
                else:
                    until = None
                if until is not None:
                    note_until = until
            view.app, view.status = modes.mode, takes.status(modes.mode)
            if modes.mode in ("prepare", "review"):
                if result is not None:
                    sync_view(grammar, view, overlay)
                view.start_progress = modes.start_progress
            else:
                view.section = takes.section
                view.count_in = max(1, math.ceil(modes.count_in_end - (start - t0)))
                view.rec_s = takes.recorder.seconds if modes.mode == "rehearse" else 0.0
                view.mic = takes.recorder.level
                view.zone_active, view.stop_progress = modes.zone.active, modes.zone.stop_progress
                view.start_progress = 0.0
            # Edge scrolling advances every displayed frame so it stays smooth.
            if view.app in ("prepare", "review") and view.mode == "browse" and grammar.state.scroll_rate:
                view.scroll = overlay.clamp_scroll(view.scroll + grammar.state.scroll_rate * (start - prev_start))
            prev_start = start
            if note_until is not None and start > note_until:
                view.note, note_until = "", None

            overlay.draw(frame, view)
            if show_debug:
                if view.app in ("count_in", "rehearse"):
                    draw_zone(frame, modes.zone)
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
            cv2.putText(frame, stats, (w - 560, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)

            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (ord(" "), ord("j"), ord("k")) and view.app in ("prepare", "review"):
                step = -1 if key == ord("k") else 1
                view.current = min(max(view.current + step, 0), len(sentences) - 1)
                if not overlay.is_visible(view.current, view.scroll):
                    view.scroll = overlay.scroll_to(view.current)
            elif key == ord("d"):
                show_debug = not show_debug
            elif key == ord("s"):
                SCREENS_DIR.mkdir(parents=True, exist_ok=True)
                shot = SCREENS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.png"
                cv2.imwrite(str(shot), frame)
                log(time.perf_counter() - t0, "screenshot", path=shot.name)
                print(f"saved {shot}")
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break

        if modes.mode == "rehearse":  # quit mid-take: keep what was recorded
            takes.handle(GestureEvent("take_stop", time.perf_counter() - t0), modes, view, overlay)
    takes.close()
    log.close()
    if trace:
        trace.close()
    tracker.close()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
