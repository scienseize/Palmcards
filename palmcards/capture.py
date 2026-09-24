"""Camera capture (audio capture arrives in milestone 4).

Frames are mirrored once here, at capture. Everything downstream (rendering,
hit-testing) works in this flipped coordinate space.
"""

import cv2
import numpy as np


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(self, index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            raise CameraError(
                "Could not open the camera. On macOS, allow Camera access for your "
                "terminal app in System Settings > Privacy & Security > Camera, "
                "then quit and reopen the terminal."
            )
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

    def read(self) -> np.ndarray:
        """Return the next frame as mirrored BGR."""
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise CameraError("Camera stopped delivering frames.")
        return cv2.flip(frame, 1)

    def release(self) -> None:
        self.cap.release()

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
