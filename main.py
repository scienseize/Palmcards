"""PalmCards entry point.

  python main.py [NOTES_FILE]     (.txt, .md or .docx; defaults to the sample)

Mirrored webcam feed with the notes overlaid in the demo style. Prepare mode
gestures (milestone 3):
  point (index finger)       hover a word; its sentence turns orange
  pinch and release on word  select that word
  pinch and drag up/down     scroll the notes
  open palm held 1 s         cancel the selection

Dev keys: space/j next sentence, k previous, d toggle landmarks, q/Esc quit.
"""

import sys
import time
from pathlib import Path

import cv2

from palmcards.capture import Camera, CameraError
from palmcards.gestures import GestureEvent, HandTracker, PrepareGestures, draw_cursor, draw_landmarks
from palmcards.notes import load_notes
from palmcards.render import TextOverlay, ViewState

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
WINDOW = "PalmCards"


def apply_prepare_event(ev: GestureEvent, view: ViewState, overlay: TextOverlay) -> None:
    if ev.kind == "hover":
        hit = overlay.hit_test(ev.x, ev.y, view.scroll)
        view.hover = hit if hit and hit.word is not None else None
        if hit:
            view.current = hit.sentence
    elif ev.kind == "select":
        hit = overlay.hit_test(ev.x, ev.y, view.scroll)
        if hit and hit.word is not None:
            view.selected = hit
            view.current = hit.sentence
    elif ev.kind == "scroll":
        # Drag up moves the text up, like scrolling a touch screen.
        view.scroll = overlay.clamp_scroll(view.scroll - ev.dy / overlay.line_h)
    elif ev.kind == "cancel":
        view.selected = None


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
    gestures = PrepareGestures()
    with camera:
        frame = camera.read()
        h, w = frame.shape[:2]
        overlay = TextOverlay(sentences, (w, h))
        view = ViewState()
        show_landmarks = False
        fps, last = 0.0, time.perf_counter()
        t0 = last

        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, w, h)

        while True:
            frame = camera.read()
            t = time.perf_counter() - t0
            hands = tracker.detect(frame, t)
            hand = hands[0] if hands else None
            view.hover = None  # only shown while actively pointing at a word
            for ev in gestures.update(hand, t):
                apply_prepare_event(ev, view, overlay)

            overlay.draw(frame, view)
            if show_landmarks and hand:
                draw_landmarks(frame, hand)
            draw_cursor(frame, gestures, hand)

            if view.selected:
                word = sentences[view.selected.sentence].words[view.selected.word].text
                cv2.putText(frame, f"selected: {word}", (20, h - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 140, 255), 2, cv2.LINE_AA)
            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now
            cv2.putText(frame, f"{fps:4.1f} fps", (w - 130, h - 20),
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
                show_landmarks = not show_landmarks
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break

    tracker.close()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
