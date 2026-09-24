"""Pillow text overlay composited onto the mirrored frame.

Demo style: monospace block floating beside the user, current sentence in
orange, the rest dimmed, a few lines visible, soft dark backing behind it.
"""

from dataclasses import dataclass
from pathlib import Path
import textwrap

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/Courier New.ttf",
]

ORANGE = (255, 140, 0, 255)
DIM = (220, 220, 220, 110)
BACKING = (10, 10, 12, 150)


def load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


@dataclass
class Row:
    text: str
    sentence: int


class TextOverlay:
    """Renders a scrolling window of sentences onto a BGR frame."""

    def __init__(
        self,
        sentences: list[str],
        frame_size: tuple[int, int],
        columns: int = 34,
        visible_rows: int = 5,
    ):
        self.sentences = sentences
        self.visible_rows = visible_rows
        w, h = frame_size
        self.font = load_font(max(18, h // 26))
        self.rows = [
            Row(line, i)
            for i, s in enumerate(sentences)
            for line in textwrap.wrap(s, columns) or [""]
        ]

        ascent, descent = self.font.getmetrics()
        self.line_h = int((ascent + descent) * 1.45)
        self.char_w = self.font.getlength("M")
        self.pad = self.line_h // 2
        self.blur = self.pad // 2

        # Patch size: text box plus margin so the blurred backing fades out.
        self.box_w = int(columns * self.char_w) + 2 * self.pad
        self.box_h = visible_rows * self.line_h + 2 * self.pad
        self.patch_w = self.box_w + 4 * self.blur
        self.patch_h = self.box_h + 4 * self.blur

        # Float on the left third, vertically centered.
        self.x = max(0, int(w * 0.05) - 2 * self.blur)
        self.y = max(0, (h - self.patch_h) // 2)

        self._cache_key = None
        self._cache = None

    def _window(self, current: int) -> list[Row]:
        first = next((i for i, r in enumerate(self.rows) if r.sentence == current), 0)
        # Keep the start of the current sentence on the second visible row.
        start = max(0, min(first - 1, len(self.rows) - self.visible_rows))
        return self.rows[start : start + self.visible_rows]

    def _render_patch(self, current: int) -> tuple[np.ndarray, np.ndarray]:
        backing = Image.new("RGBA", (self.patch_w, self.patch_h), (0, 0, 0, 0))
        m = 2 * self.blur
        ImageDraw.Draw(backing).rounded_rectangle(
            (m, m, m + self.box_w, m + self.box_h), radius=self.pad, fill=BACKING
        )
        backing = backing.filter(ImageFilter.GaussianBlur(self.blur))

        text = Image.new("RGBA", backing.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text)
        for i, row in enumerate(self._window(current)):
            color = ORANGE if row.sentence == current else DIM
            draw.text((m + self.pad, m + self.pad + i * self.line_h), row.text, font=self.font, fill=color)

        patch = Image.alpha_composite(backing, text)
        arr = np.asarray(patch, dtype=np.float32)
        bgr = arr[..., 2::-1]  # RGBA -> BGR
        alpha = arr[..., 3:4] / 255.0
        return bgr * alpha, 1.0 - alpha  # premultiplied colour, inverse alpha

    def draw(self, frame: np.ndarray, current: int) -> np.ndarray:
        """Composite the overlay onto `frame` in place and return it."""
        if self._cache_key != current:
            self._cache = self._render_patch(current)
            self._cache_key = current
        color, inv_alpha = self._cache

        fh, fw = frame.shape[:2]
        h = min(self.patch_h, fh - self.y)
        w = min(self.patch_w, fw - self.x)
        region = frame[self.y : self.y + h, self.x : self.x + w]
        blended = region * inv_alpha[:h, :w] + color[:h, :w]
        region[:] = blended.astype(np.uint8)
        return frame
