"""PalmCards entry point.

  python main.py [NOTES_FILE]     (.txt, .md or .docx; defaults to the sample)

Mirrored webcam feed with the notes overlaid in the demo style, marks shown
as written. Keyboard is a dev-only stand-in until gestures land in milestone 3:
  space / j  next sentence     k  previous sentence     q / Esc  quit
"""

import sys
import time
from pathlib import Path

import cv2

from palmcards.capture import Camera, CameraError
from palmcards.notes import load_notes
from palmcards.render import TextOverlay

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
WINDOW = "PalmCards"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE
    try:
        notes = load_notes(path)
    except (OSError, ValueError) as exc:
        print(f"Could not open notes: {exc}", file=sys.stderr)
        return 1
    for warning in notes.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    sentences = [s.raw for s in notes.sentences]

    try:
        camera = Camera()
    except CameraError as exc:
        print(exc, file=sys.stderr)
        return 1

    with camera:
        frame = camera.read()
        h, w = frame.shape[:2]
        overlay = TextOverlay(sentences, (w, h))
        current = 0
        fps, last = 0.0, time.perf_counter()

        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, w, h)

        while True:
            frame = camera.read()
            overlay.draw(frame, current)

            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now
            cv2.putText(frame, f"{fps:4.1f} fps", (w - 130, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)

            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (ord(" "), ord("j")):
                current = min(current + 1, len(sentences) - 1)
            elif key == ord("k"):
                current = max(current - 1, 0)
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
