"""PalmCards entry point.

  python main.py [NOTES_FILE]     (.txt, .md or .docx; defaults to the sample)

Mirrored webcam feed with the notes overlaid in the demo style. Prepare mode
follows Kat's gesture grammar (see CLAUDE.md). Hold your hand in the box on
the right of the frame; it steers the highlight in the text on the left.

  one finger up / two fingers together / flat hand
                           browse by word / sentence / paragraph
  top or bottom of the box scroll
  pinch (word), fold fingers onto the thumb (sentence, paragraph)
                           focus
  open palm (word)         options ring (placeholders); L-hand points at a node
  L-hand tilt (sentence)   tone dial, warm to the right, cold to the left
  two L-hands (paragraph)  length stretch
  pinch + lift             commit (logged only, no text changes yet)
  drop the hand for 1 s    back out

Operations are stubs until milestone 8. Poses and events are logged to
sessions/gesture-logs/.

Dev keys: space/j next sentence, k previous, d toggle landmarks and hand box, q/Esc quit.
"""

import sys
import time
from pathlib import Path

import cv2

from palmcards.capture import Camera, CameraError
from palmcards.gestures import (
    GestureEvent, GestureLog, Grammar, HandTracker, draw_fingertips, draw_hand_box, draw_landmarks,
)
from palmcards.notes import load_notes
from palmcards.render import Hit, OpsView, TextOverlay, ViewState, ring_pick

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
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
    if gs.op == "ring" and gs.pointing and gs.cursor is not None:
        ops.picked = ring_pick(*gs.cursor, len(overlay.ring_labels(view)), ops.picked)


def apply_event(ev: GestureEvent, view: ViewState, overlay: TextOverlay, log: GestureLog) -> float | None:
    """Returns the time a label note should expire, if one was set."""
    if ev.kind == "focus":
        if view.hover is None:  # focused before the cursor ever touched the text
            view.hover = Hit(view.current, 0 if ev.level == "word" else None)
        view.focus = view.hover
        view.ops = OpsView()
        return None
    if ev.kind == "commit":
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


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE
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
    with camera:
        frame = camera.read()
        h, w = frame.shape[:2]
        overlay = TextOverlay(sentences, (w, h))
        grammar = Grammar((w, h), log)
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
                for ev in grammar.update(*result):
                    if (until := apply_event(ev, view, overlay, log)) is not None:
                        note_until = until
                sync_view(grammar, view, overlay)
            # Edge scrolling advances every displayed frame so it stays smooth.
            if view.mode == "browse" and grammar.state.scroll_rate:
                view.scroll = overlay.clamp_scroll(view.scroll + grammar.state.scroll_rate * (start - prev_start))
            prev_start = start
            if note_until is not None and start > note_until:
                view.note, note_until = "", None

            overlay.draw(frame, view)
            if show_debug:
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
            if key in (ord(" "), ord("j"), ord("k")):
                step = -1 if key == ord("k") else 1
                view.current = min(max(view.current + step, 0), len(sentences) - 1)
                if not overlay.is_visible(view.current, view.scroll):
                    view.scroll = overlay.scroll_to(view.current)
            elif key == ord("d"):
                show_debug = not show_debug
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break

    log.close()
    tracker.close()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
