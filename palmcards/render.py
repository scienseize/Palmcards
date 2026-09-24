"""Pillow text overlay composited onto the mirrored frame.

Demo style: monospace block floating beside the user, current sentence in
orange, the rest dimmed, a few lines visible, soft dark backing behind it.
Delivery marks are drawn as written, a shade dimmer than the words.

The layout is a grid of monospace cells, so every word has a known row and
column range; hit_test() maps a fingertip position back to (sentence, word).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from palmcards.notes import MarkKind, Sentence

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/Courier New.ttf",
]

ORANGE = (255, 140, 0, 255)
ORANGE_MARK = (255, 140, 0, 150)
DIM = (220, 220, 220, 110)
DIM_MARK = (220, 220, 220, 60)
HOVER_LINE = (255, 255, 255, 230)
SELECT_FILL = (255, 140, 0, 235)
SELECT_TEXT = (20, 20, 20, 255)
BACKING = (10, 10, 12, 150)

PAUSE_TEXT = {MarkKind.SHORT_PAUSE: "/", MarkKind.LONG_PAUSE: "//"}


def load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


@dataclass(frozen=True)
class Hit:
    sentence: int
    word: int | None  # None: on the sentence's row but between words


@dataclass
class ViewState:
    current: int = 0  # sentence drawn in orange
    hover: Hit | None = None
    selected: Hit | None = None  # always has a word
    scroll: float = 0.0  # in rows


@dataclass(frozen=True)
class Span:
    text: str
    role: str  # "word" | "mark" | "punct"
    word: int | None = None


@dataclass
class Row:
    sentence: int
    spans: list[tuple[int, Span]]  # (column, span)


def sentence_units(s: Sentence) -> list[list[Span]]:
    """Display units (unbreakable, space-separated) for one sentence."""
    pauses = {m.word: m.kind for m in s.pauses()}
    units: list[list[Span]] = []
    if s.pace:
        units.append([Span(f"[{s.pace}]", "mark")])
    wi = 0
    for tok in re.finditer(r"\S+", s.text):
        if wi < len(s.words) and tok.start() == s.words[wi].start:
            if wi in pauses:
                units.append([Span(PAUSE_TEXT[pauses[wi]], "mark")])
            word = Span(tok.group(), "word", wi)
            if s.words[wi].stressed:
                units.append([Span("*", "mark"), word, Span("*", "mark")])
            else:
                units.append([word])
            wi += 1
        else:
            units.append([Span(tok.group(), "punct")])
    if len(s.words) in pauses:
        units.append([Span(PAUSE_TEXT[pauses[len(s.words)]], "mark")])
    if s.ending:
        units.append([Span(f"[{s.ending}]", "mark")])
    return units


def layout(sentences: list[Sentence], columns: int) -> list[Row]:
    """Greedy wrap; each sentence starts on a new row."""
    rows: list[Row] = []
    for si, s in enumerate(sentences):
        row = Row(si, [])
        col = 0
        for unit in sentence_units(s):
            width = sum(len(sp.text) for sp in unit)
            if row.spans and col + 1 + width > columns:
                rows.append(row)
                row, col = Row(si, []), 0
            elif row.spans:
                col += 1
            for sp in unit:
                row.spans.append((col, sp))
                col += len(sp.text)
        rows.append(row)
    return rows


class TextOverlay:
    """Renders a scrollable window of sentences onto a BGR frame."""

    def __init__(
        self,
        sentences: list[Sentence],
        frame_size: tuple[int, int],
        columns: int = 34,
        visible_rows: int = 5,
    ):
        self.sentences = sentences
        self.visible_rows = visible_rows
        w, h = frame_size
        self.font = load_font(max(18, h // 26))
        self.rows = layout(sentences, columns)
        self._first_row = {}
        for i, r in enumerate(self.rows):
            self._first_row.setdefault(r.sentence, i)

        ascent, descent = self.font.getmetrics()
        self.line_h = int((ascent + descent) * 1.45)
        self.char_w = self.font.getlength("M")
        self.pad = self.line_h // 2
        self.blur = self.pad // 2

        # Patch size: text box plus margin so the blurred backing fades out.
        self.box_w = int(columns * self.char_w) + 2 * self.pad
        self.box_h = visible_rows * self.line_h + 2 * self.pad
        self.margin = 2 * self.blur
        self.patch_w = self.box_w + 2 * self.margin
        self.patch_h = self.box_h + 2 * self.margin

        # Float on the left third, vertically centered.
        self.x = max(0, int(w * 0.05) - self.margin)
        self.y = max(0, (h - self.patch_h) // 2)

        self._backing = self._render_backing()
        self._cache_key = None
        self._cache = None

    # --- scrolling ---------------------------------------------------------

    @property
    def max_scroll(self) -> float:
        return float(max(0, len(self.rows) - self.visible_rows))

    def clamp_scroll(self, scroll: float) -> float:
        return min(max(scroll, 0.0), self.max_scroll)

    def scroll_to(self, sentence: int) -> float:
        """Scroll that puts the sentence's first row on the second visible row."""
        return self.clamp_scroll(self._first_row.get(sentence, 0) - 1)

    def is_visible(self, sentence: int, scroll: float) -> bool:
        r = self._first_row.get(sentence, 0)
        return scroll <= r <= scroll + self.visible_rows - 1

    # --- hit testing -------------------------------------------------------

    def contains(self, x: float, y: float) -> bool:
        bx, by = self.x + self.margin, self.y + self.margin
        return bx <= x <= bx + self.box_w and by <= y <= by + self.box_h

    def hit_test(self, x: float, y: float, scroll: float) -> Hit | None:
        if not self.contains(x, y):
            return None
        lx = x - (self.x + self.margin + self.pad)
        ly = y - (self.y + self.margin + self.pad)
        ri = math.floor(ly / self.line_h + scroll)
        if not 0 <= ri < len(self.rows):
            return None
        row = self.rows[ri]
        col = lx / self.char_w
        best, best_d = None, 1.0  # allow up to one cell outside the word
        for c, sp in row.spans:
            if sp.role != "word":
                continue
            d = 0.0 if c <= col <= c + len(sp.text) else min(abs(col - c), abs(col - c - len(sp.text)))
            if d < best_d:
                best, best_d = sp.word, d
        return Hit(row.sentence, best)

    # --- drawing -----------------------------------------------------------

    def _render_backing(self) -> Image.Image:
        backing = Image.new("RGBA", (self.patch_w, self.patch_h), (0, 0, 0, 0))
        m = self.margin
        ImageDraw.Draw(backing).rounded_rectangle(
            (m, m, m + self.box_w, m + self.box_h), radius=self.pad, fill=BACKING
        )
        return backing.filter(ImageFilter.GaussianBlur(self.blur))

    def _render_patch(self, state: ViewState) -> tuple[np.ndarray, np.ndarray]:
        text = Image.new("RGBA", (self.box_w, self.box_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(text)
        first = max(0, math.floor(state.scroll) - 1)
        last = min(len(self.rows), math.ceil(state.scroll) + self.visible_rows + 1)
        for ri in range(first, last):
            row = self.rows[ri]
            y = self.pad + (ri - state.scroll) * self.line_h
            current = row.sentence == state.current
            for col, sp in row.spans:
                x = self.pad + col * self.char_w
                color = (ORANGE_MARK if current else DIM_MARK) if sp.role == "mark" else (ORANGE if current else DIM)
                if sp.role == "word" and state.selected == Hit(row.sentence, sp.word):
                    x1 = x + len(sp.text) * self.char_w
                    draw.rounded_rectangle((x - 1, y - 2, x1 + 1, y + self.line_h * 0.72),
                                           radius=4, fill=SELECT_FILL)
                    color = SELECT_TEXT
                draw.text((x, y), sp.text, font=self.font, fill=color)
                if sp.role == "word" and state.hover == Hit(row.sentence, sp.word):
                    uy = y + self.line_h * 0.74
                    draw.line((x, uy, x + len(sp.text) * self.char_w, uy), fill=HOVER_LINE, width=2)

        patch = self._backing.copy()
        patch.alpha_composite(text, (self.margin, self.margin))
        arr = np.asarray(patch, dtype=np.float32)
        bgr = arr[..., 2::-1]  # RGBA -> BGR
        alpha = arr[..., 3:4] / 255.0
        return bgr * alpha, 1.0 - alpha  # premultiplied colour, inverse alpha

    def draw(self, frame: np.ndarray, state: ViewState) -> np.ndarray:
        """Composite the overlay onto `frame` in place and return it."""
        key = (state.current, state.hover, state.selected, round(state.scroll, 2))
        if self._cache_key != key:
            self._cache = self._render_patch(state)
            self._cache_key = key
        color, inv_alpha = self._cache

        fh, fw = frame.shape[:2]
        h = min(self.patch_h, fh - self.y)
        w = min(self.patch_w, fw - self.x)
        region = frame[self.y : self.y + h, self.x : self.x + w]
        blended = region * inv_alpha[:h, :w] + color[:h, :w]
        region[:] = blended.astype(np.uint8)
        return frame
