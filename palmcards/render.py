"""Pillow text overlay composited onto the mirrored frame.

Demo style: monospace block floating beside the user, the unit under the
cursor in orange, the rest dimmed, a few lines visible, soft dark backing
behind it. Delivery marks are drawn as written, a shade dimmer than the words.

The layout is a grid of monospace cells, so every word has a known row and
column range; hit_test() maps a point back to (sentence, word), and
cursor_to_text() maps the relative hand-box cursor to such a point.

On top of the text: the two-line state label (Kat's `BROWSE BY WORD`), and
the operation stubs for milestone 3b (options ring, tone gauge, stretch line),
which move but never rewrite the text.

In Rehearse the current section is shown whole in the focus panel, with the
command zone (top right), a recording clock and microphone level, and a
large 3-2-1 during the count-in. A drill shows just its one sentence.

In Review each delivery mark is drawn as a chip coloured by its verdict in
the take that sentence shows (green hit, red missed, grey unclear; a skipped
mark stays faint text), and a focused sentence lists its verdicts under it.
"""

from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from palmcards.config import REHEARSE
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
FAINT = (220, 220, 220, 45)
FAINT_MARK = (220, 220, 220, 25)
FOCUS_TEXT = (245, 245, 245, 255)
FOCUS_MARK = (245, 245, 245, 140)
CHIP_FILL = (255, 140, 0, 235)
CHIP_TEXT = (20, 20, 20, 255)
DARK_FILL = (15, 15, 18, 215)
NODE_TEXT = (240, 240, 240, 255)
NODE_OUTLINE = (240, 240, 240, 200)
BACKING = (10, 10, 12, 150)
YELLOW_BGR = (0, 215, 255)
COLD_BGR = (255, 150, 60)
WARM_BGR = (0, 140, 255)
ZONE_BGR = (170, 170, 170)
REC_BGR = (60, 60, 230)
VERDICT_FILL = {"hit": (95, 205, 115), "missed": (240, 90, 75), "unclear": (160, 160, 160)}  # RGB
DETAIL_TEXT = (235, 235, 235, 235)
DETAIL_SCALE = 0.7  # verdict lines under a focused sentence, relative to the text

PAUSE_TEXT = {MarkKind.SHORT_PAUSE: "/", MarkKind.LONG_PAUSE: "//"}
BAND_SLACK_ROWS = 6  # rows rendered beyond the window on each side
FOCUS_SCALES = (1.3, 1.15, 1.0)  # largest that fits the text box wins
CHIP_CACHE_MAX = 256  # the recording clock makes a new chip every second
RING_PLACEHOLDERS = ("alt 1", "alt 2", "alt 3", "stress", "hear it")  # node 0 is the original word
FOCUS_HINTS = {
    "word": "OPEN PALM: ALTERNATIVES  /  DROP HAND: BACK",
    "sentence": "L-HAND, THEN TILT: TONE  /  DROP HAND: BACK",
    "paragraph": "TWO L-HANDS: LENGTH  /  DROP HAND: BACK",
}  # cursor this close to the hand-box centre keeps the pick


def load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


@dataclass(frozen=True)
class Hit:
    sentence: int
    word: int | None  # None: sentence/paragraph level, or between words


@dataclass
class OpsView:
    """What the operation stubs show; filled from the gesture state."""

    kind: str | None = None  # ring | tone | stretch
    picked: int = 0  # ring node, 0 = original word, clockwise from the top
    pointing: bool = False
    tone: float = 0.0  # -1 cold .. 1 warm
    stretch: float = 1.0
    stretch_ends: tuple[tuple[float, float], tuple[float, float]] | None = None


@dataclass
class ViewState:
    current: int = 0  # sentence drawn in orange when no hand is up
    mode: str = "idle"  # idle | browse | focus
    level: str | None = None  # word | sentence | paragraph
    hover: Hit | None = None
    focus: Hit | None = None
    scroll: float = 0.0  # in rows
    ops: OpsView = field(default_factory=OpsView)
    drop_progress: float = 0.0
    note: str = ""  # transient second label line, e.g. after a commit
    status: str = ""  # second label line when there is nothing more pressing
    app: str = "prepare"  # prepare | count_in | rehearse | review | player
    title: str = ""  # replaces the first label line (the take player)
    start_progress: float = 0.0  # fist held to start a take, 0..1
    section: int = 0  # count_in, rehearse: the section on screen
    count_in: int = 0  # 3, 2, 1
    rec_s: float = 0.0  # length of the take so far
    mic: float = 0.0  # microphone level, 0..1
    zone_active: bool = False  # a hand is in the command zone
    hold_progress: float = 0.0  # open palm held in the zone, 0..1
    flick_progress: float = 0.0  # sideways swing toward a flick, 0..1
    drill: int | None = None  # count_in, rehearse: the one sentence a drill rehearses
    # Review: per sentence, the verdict of each mark (Sentence.marks order) in
    # the take the sentence shows, "" where there is none.
    mark_verdicts: tuple[tuple[str, ...], ...] = ()
    detail: tuple[tuple[str, str], ...] = ()  # Review focus: (verdict or "", line) under the sentence


@dataclass(frozen=True)
class Span:
    text: str
    role: str  # "word" | "mark" | "punct"
    word: int | None = None
    mark: int | None = None  # index into Sentence.marks, for marks


@dataclass
class Row:
    sentence: int
    spans: list[tuple[int, Span]]  # (column, span)


def sentence_units(s: Sentence) -> list[list[Span]]:
    """Display units (unbreakable, space-separated) for one sentence."""
    pauses = {m.word: (i, m.kind) for i, m in enumerate(s.marks) if m in s.pauses()}
    stress = {m.word: i for i, m in enumerate(s.marks) if m.kind is MarkKind.STRESS}
    units: list[list[Span]] = []
    if s.pace:
        units.append([Span(f"[{s.pace}]", "mark", mark=_mark_index(s, s.pace))])
    wi = 0
    for tok in re.finditer(r"\S+", s.text):
        if wi < len(s.words) and tok.start() == s.words[wi].start:
            if wi in pauses:
                units.append([Span(PAUSE_TEXT[pauses[wi][1]], "mark", mark=pauses[wi][0])])
            word = Span(tok.group(), "word", wi)
            if s.words[wi].stressed:
                mi = stress.get(wi)
                units.append([Span("*", "mark", mark=mi), word, Span("*", "mark", mark=mi)])
            else:
                units.append([word])
            wi += 1
        else:
            units.append([Span(tok.group(), "punct")])
    if len(s.words) in pauses:
        i, kind = pauses[len(s.words)]
        units.append([Span(PAUSE_TEXT[kind], "mark", mark=i)])
    if s.ending:
        units.append([Span(f"[{s.ending}]", "mark", mark=_mark_index(s, s.ending))])
    return units


def _mark_index(s: Sentence, kind: MarkKind) -> int | None:
    return next((i for i, m in enumerate(s.marks) if m.kind is kind), None)


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
        self.frame_w, self.frame_h = w, h = frame_size
        self.font_size = max(18, h // 26)
        self.font = load_font(self.font_size)
        self._fonts = {self.font_size: self.font}
        self.rows = layout(sentences, columns)
        self._first_row: dict[int, int] = {}
        self._last_row: dict[int, int] = {}
        self._word_pos: dict[Hit, tuple[int, int, int]] = {}  # -> (row, column, length)
        for i, r in enumerate(self.rows):
            self._first_row.setdefault(r.sentence, i)
            self._last_row[r.sentence] = i
            for col, sp in r.spans:
                if sp.role == "word":
                    self._word_pos[Hit(r.sentence, sp.word)] = (i, col, len(sp.text))

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

        self._backings: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        # Text is rendered into a band of rows taller than the window, so
        # scrolling only moves a crop through it (see _band_crop).
        self.band_rows = visible_rows + 2 * BAND_SLACK_ROWS
        self._band_key = None
        self._band = None
        self._band_start = 0
        self._panel_key = None
        self._panel = None
        self._chips: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    # --- units -------------------------------------------------------------

    def unit(self, level: str | None, sentence: int) -> list[int]:
        """Sentence indices of the unit containing `sentence` at this level."""
        if level == "paragraph":
            p = self.sentences[sentence].paragraph
            return [i for i, s in enumerate(self.sentences) if s.paragraph == p]
        if level == "section":
            sec = self.sentences[sentence].section
            return [i for i, s in enumerate(self.sentences) if s.section == sec]
        return [sentence]

    def word_text(self, hit: Hit) -> str:
        return self.sentences[hit.sentence].words[hit.word].text

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

    def cursor_to_text(self, u: float, v: float) -> tuple[float, float]:
        """Hand-box cursor (0..1, 0..1) -> a point on the rows of the text box."""
        x0 = self.x + self.margin + self.pad
        y0 = self.y + self.margin + self.pad
        return x0 + u * (self.box_w - 2 * self.pad), y0 + v * (self.box_h - 2 * self.pad - 1)

    def hit_test(self, x: float, y: float, scroll: float, snap: bool = False) -> Hit | None:
        """Word under (x, y). With snap, the nearest word on the row, however far."""
        if not self.contains(x, y):
            return None
        lx = x - (self.x + self.margin + self.pad)
        ly = y - (self.y + self.margin + self.pad)
        ri = math.floor(ly / self.line_h + scroll)
        if not 0 <= ri < len(self.rows):
            return None
        row = self.rows[ri]
        col = lx / self.char_w
        best, best_d = None, math.inf if snap else 1.0  # else up to one cell outside the word
        for c, sp in row.spans:
            if sp.role != "word":
                continue
            d = 0.0 if c <= col <= c + len(sp.text) else min(abs(col - c), abs(col - c - len(sp.text)))
            if d < best_d:
                best, best_d = sp.word, d
        return Hit(row.sentence, best)

    def word_box(self, hit: Hit, scroll: float) -> tuple[float, float, float, float] | None:
        """Screen rectangle of a word, or None if it is scrolled out of view."""
        pos = self._word_pos.get(hit)
        if pos is None:
            return None
        ri, col, length = pos
        y = self.pad + (ri - scroll) * self.line_h
        if not 0 <= y + self.line_h * 0.5 < self.box_h:
            return None
        x0 = self.x + self.margin + self.pad + col * self.char_w
        y0 = self.y + self.margin + y
        return x0, y0, x0 + length * self.char_w, y0 + self.line_h * 0.8

    # --- labels ------------------------------------------------------------

    def label_lines(self, state: ViewState) -> tuple[str, str]:
        """Kat's two-line state label: mode and level, then the operation."""
        if state.title:
            return state.title, state.note or state.status
        if state.app in ("count_in", "rehearse"):
            if state.hold_progress > 0:
                second = f"{'CANCEL' if state.app == 'count_in' else 'STOP'}: HOLD  {_bar(state.hold_progress)}"
            elif state.note:
                second = state.note
            elif state.app == "count_in":
                second = f"STARTING IN {state.count_in}"
            else:
                second = state.status
            return "DRILL" if state.drill is not None else "REHEARSE", second

        level = (state.level or "").upper()
        if state.mode == "focus":
            first = f"FOCUS BY {level}"
            if state.level == "word" and state.focus is not None and state.focus.word is not None:
                first += f'  "{self.word_text(state.focus)}"'
        elif state.mode == "browse":
            first = f"BROWSE BY {level}"
        else:
            first = state.app.upper()
        ops = state.ops
        if state.note:
            second = state.note
        elif state.start_progress > 0:
            second = f"START A TAKE: HOLD FIST  {_bar(state.start_progress)}"
        elif state.hold_progress > 0:
            second = f"BACK TO PREPARE: HOLD  {_bar(state.hold_progress)}"
        elif state.drop_progress > 0:
            second = "DROP HAND TO BACK OUT"
        elif ops.kind == "ring":
            second = "EXPLORE WORD ALTERNATIVES: TURN AN L-HAND"
            if ops.pointing:
                second = f"PREVIEW: {self.ring_labels(state)[ops.picked].upper()}  (PINCH + LIFT TO COMMIT)"
        elif ops.kind == "tone":
            tone = "WARM" if ops.tone > 0.15 else "COLD" if ops.tone < -0.15 else "NEUTRAL"
            second = f"CHANGE SENTENCE TONE: {tone}"
        elif ops.kind == "stretch":
            change = "INCREASE" if ops.stretch > 1.05 else "DECREASE" if ops.stretch < 0.95 else "SAME"
            second = f"ADJUST PARAGRAPH LENGTH: {change}  x{ops.stretch:.2f}"
        elif state.mode == "focus" and state.app == "prepare":  # nothing started yet: say what the next shape does
            second = FOCUS_HINTS.get(state.level, "")
        elif state.mode == "focus":
            second = state.status or "DROP HAND: BACK"
        else:
            second = state.status
        return first, second

    def ring_labels(self, state: ViewState) -> tuple[str, ...]:
        original = self.word_text(state.focus) if state.focus and state.focus.word is not None else "original"
        return (original, *RING_PLACEHOLDERS)

    # --- drawing: text -----------------------------------------------------

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        if size not in self._fonts:
            self._fonts[size] = load_font(size)
        return self._fonts[size]

    def _backing(self, box_h: int) -> tuple[np.ndarray, np.ndarray]:
        if box_h not in self._backings:
            img = Image.new("RGBA", (self.patch_w, box_h + 2 * self.margin), (0, 0, 0, 0))
            m = self.margin
            ImageDraw.Draw(img).rounded_rectangle(
                (m, m, m + self.box_w, m + box_h), radius=self.pad, fill=BACKING
            )
            self._backings[box_h] = _premultiply(img.filter(ImageFilter.GaussianBlur(self.blur)))
        return self._backings[box_h]

    def _band_style(self, state: ViewState) -> tuple:
        """What the band's colours depend on (also its cache key)."""
        if state.mode == "focus" and state.focus is not None:
            return ("focus", state.focus.sentence, state.mark_verdicts)
        if state.mode == "browse" and state.hover is not None:
            return ("browse", tuple(self.unit(state.level, state.hover.sentence)), state.mark_verdicts)
        return ("browse", (state.current,), state.mark_verdicts)

    def _verdict(self, verdicts: tuple, sentence: int, sp: Span) -> str:
        if sp.mark is None or sentence >= len(verdicts) or sp.mark >= len(verdicts[sentence]):
            return ""
        return verdicts[sentence][sp.mark]

    def _draw_span(self, draw: ImageDraw.ImageDraw, xy: tuple[float, float], sp: Span, font, word_c, mark_c,
                   verdict: str = "") -> None:
        """A word, punctuation, or a mark; a judged mark on a chip of its verdict's colour."""
        if sp.role != "mark":
            draw.text(xy, sp.text, font=font, fill=word_c)
        elif verdict in VERDICT_FILL:
            x0, y0, x1, y1 = draw.textbbox(xy, sp.text, font=font)
            pad = max(2, font.size // 8)
            alpha = max(70, min(235, word_c[3] * 2))  # as prominent as the words around it
            draw.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=pad + 1,
                                   fill=(*VERDICT_FILL[verdict], alpha))
            draw.text(xy, sp.text, font=font, fill=(20, 20, 20, alpha))
        elif verdict == "skipped":
            draw.text(xy, sp.text, font=font, fill=FAINT_MARK)
        else:
            draw.text(xy, sp.text, font=font, fill=mark_c)

    def _render_band(self, style: tuple, start: int) -> tuple[np.ndarray, np.ndarray]:
        """Rows start..start+band_rows, with row `start` at y = pad."""
        band_h = self.band_rows * self.line_h + 2 * self.pad
        text = Image.new("RGBA", (self.box_w, band_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(text)
        kind, which, verdicts = style
        for ri in range(start, min(len(self.rows), start + self.band_rows)):
            row = self.rows[ri]
            y = self.pad + (ri - start) * self.line_h
            if kind == "focus":  # word focus: its sentence dim, the rest fainter
                word_c, mark_c = (DIM, DIM_MARK) if row.sentence == which else (FAINT, FAINT_MARK)
            else:
                word_c, mark_c = (ORANGE, ORANGE_MARK) if row.sentence in which else (DIM, DIM_MARK)
            for col, sp in row.spans:
                self._draw_span(draw, (self.pad + col * self.char_w, y), sp, self.font, word_c, mark_c,
                                self._verdict(verdicts, row.sentence, sp))
        return _premultiply(text)

    def _band_valid(self, scroll: float) -> bool:
        start, end = self._band_start, self._band_start + self.band_rows
        # One spare row each side so partly visible rows at the edges exist.
        top_ok = start == 0 or start <= scroll - 1
        bottom_ok = end >= len(self.rows) or scroll + self.visible_rows + 1 <= end
        return top_ok and bottom_ok

    def _band_crop(self, state: ViewState) -> tuple[np.ndarray, np.ndarray]:
        key = self._band_style(state)
        if self._band_key != key or not self._band_valid(state.scroll):
            self._band_start = max(0, math.floor(state.scroll) - BAND_SLACK_ROWS)
            self._band = self._render_band(key, self._band_start)
            self._band_key = key
        off = round((state.scroll - self._band_start) * self.line_h)
        color, inv = self._band
        return color[off : off + self.box_h], inv[off : off + self.box_h]

    def _panel_unit(self, state: ViewState) -> tuple[int, ...] | None:
        """Sentences shown enlarged in the panel instead of the scrolling text, if any."""
        if state.app in ("count_in", "rehearse") and state.drill is not None:
            return (state.drill,)
        if state.app in ("count_in", "rehearse"):
            first = next((i for i, s in enumerate(self.sentences) if s.section == state.section), 0)
            return tuple(self.unit("section", first))
        if state.mode == "focus" and state.focus is not None and state.level in ("sentence", "paragraph"):
            return tuple(self.unit(state.level, state.focus.sentence))
        return None

    def _focus_panel(self, unit: tuple[int, ...], detail: tuple[tuple[str, str], ...] = (),
                     verdicts: tuple = ()) -> tuple[int, tuple[np.ndarray, np.ndarray]]:
        """The unit's sentences enlarged, then the detail lines (Review's
        verdicts), faint context rows around them.

        Returns (panel box height, premultiplied text). The panel grows past
        the normal box only if the unit doesn't fit even at normal size.
        """
        key = (unit, detail, verdicts)
        if self._panel_key == key:
            return self._panel
        sents = [self.sentences[i] for i in unit]
        dfont = self._get_font(round(self.font_size * DETAIL_SCALE))
        dlh = round(self.line_h * DETAIL_SCALE)
        bullet = dlh // 2
        dcols = max(10, int((self.box_w - 2 * self.pad - bullet * 2) / dfont.getlength("M")))
        dlines = [(verdict if k == 0 else "", piece) for verdict, line in detail
                  for k, piece in enumerate(textwrap.wrap(line, dcols, subsequent_indent="  ") or [""])]
        detail_h = len(dlines) * dlh + (self.pad // 2 if dlines else 0)
        avail = self.box_h - 2 * self.pad - detail_h
        for scale in FOCUS_SCALES:
            font = self._get_font(round(self.font_size * scale))
            lh = round(self.line_h * scale)
            cw = font.getlength("M")
            rows = layout(sents, int((self.box_w - 2 * self.pad) / cw))
            if len(rows) * lh <= avail:
                break
        big_h = len(rows) * lh
        panel_h = max(self.box_h, min(big_h + detail_h + 2 * self.pad, int(self.frame_h * 0.9)))
        spare = panel_h - 2 * self.pad - big_h - detail_h
        first, last = self._first_row[unit[0]], self._last_row[unit[-1]]
        above = self.rows[max(0, first - int(spare / 2 // self.line_h)) : first]
        below = self.rows[last + 1 : last + 1 + int((spare - len(above) * self.line_h) // self.line_h)]
        content = (len(above) + len(below)) * self.line_h + big_h + detail_h
        y = self.pad + max(0, (panel_h - 2 * self.pad - content) // 2)

        img = Image.new("RGBA", (self.box_w, panel_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        def draw_rows(rows_, font_, lh_, cw_, word_c, mark_c, y_, sentence_of=lambda r: r.sentence):
            for row in rows_:
                for col, sp in row.spans:
                    self._draw_span(draw, (self.pad + col * cw_, y_), sp, font_, word_c, mark_c,
                                    self._verdict(verdicts, sentence_of(row), sp))
                y_ += lh_
            return y_

        y = draw_rows(above, self.font, self.line_h, self.char_w, FAINT, FAINT_MARK, y)
        # The enlarged rows were laid out afresh: their sentence numbers count from the unit's first.
        y = draw_rows(rows, font, lh, cw, FOCUS_TEXT, FOCUS_MARK, y, lambda r: unit[r.sentence])
        if dlines:
            y += self.pad // 2
            for verdict, piece in dlines:
                if verdict in VERDICT_FILL:
                    cy = y + dlh * 0.45
                    draw.ellipse((self.pad, cy - bullet / 3, self.pad + bullet * 2 / 3, cy + bullet / 3),
                                 fill=(*VERDICT_FILL[verdict], 255))
                draw.text((self.pad + bullet, y), piece, font=dfont, fill=DETAIL_TEXT)
                y += dlh
        draw_rows(below, self.font, self.line_h, self.char_w, FAINT, FAINT_MARK, y)
        self._panel_key, self._panel = key, (panel_h, _premultiply(img))
        return self._panel

    def _chip(self, text: str, size: int, fg, bg, outline=None) -> tuple[np.ndarray, np.ndarray]:
        """A text label on a rounded rectangle, cached."""
        key = (text, size, fg, bg, outline)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            font = self._get_font(size)
            ascent, descent = font.getmetrics()
            px, py = max(4, size // 4), max(2, size // 8)
            w, h = int(font.getlength(text)) + 2 * px, ascent + descent + 2 * py
            img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            if bg is not None or outline is not None:
                draw.rounded_rectangle((1, 1, w, h), radius=max(3, size // 5), fill=bg, outline=outline)
            draw.text((1 + px, 1 + py), text, font=font, fill=fg)
            self._chips[key] = _premultiply(img)
        return self._chips[key]

    def _blend_centered(self, frame: np.ndarray, chip, cx: float, cy: float) -> tuple[int, int, int, int]:
        color, inv = chip
        h, w = color.shape[:2]
        x, y = int(cx - w / 2), int(cy - h / 2)
        _blend(frame, x, y, color, inv)
        return x, y, x + w, y + h

    # --- drawing: HUD and operation stubs ----------------------------------

    def _draw_label(self, frame: np.ndarray, state: ViewState, top: int) -> None:
        first, second = self.label_lines(state)
        big, small = round(self.font_size * 0.9), round(self.font_size * 0.7)
        c1 = self._chip(first, big, ORANGE, None)
        c2 = self._chip(second, small, ORANGE_MARK, None) if second else None
        h = c1[0].shape[0] + (c2[0].shape[0] if c2 else 0)
        x, y = self.x + self.margin + self.pad // 2, max(4, top - h - self.pad // 2)
        _blend(frame, x, y, *c1)
        if c2:
            _blend(frame, x, y + c1[0].shape[0], *c2)

    def _draw_ring(self, frame: np.ndarray, state: ViewState, center: tuple[float, float]) -> None:
        labels = self.ring_labels(state)
        n = len(labels)
        rx, ry = 4.4 * self.line_h, 2.6 * self.line_h
        size = round(self.font_size * 0.85)
        # Centre the ring on the word, shifted so every node stays on screen;
        # the connectors still start at the word.
        widest = max(self._chip(label, size, NODE_TEXT, DARK_FILL, NODE_OUTLINE)[0].shape[1] for label in labels)
        edge = rx + widest / 2 + 8
        rcx = min(max(center[0], edge), self.frame_w - edge)
        rcy = min(max(center[1], ry + self.line_h), self.frame_h - ry - self.line_h)
        cx, cy = center
        for i, label in enumerate(labels):
            a = math.radians(-90 + i * 360 / n)
            nx, ny = rcx + rx * math.cos(a), rcy + ry * math.sin(a)
            # Curved connector: quadratic Bezier bowed to one side.
            mx, my = (cx + nx) / 2, (cy + ny) / 2
            ctrl = (mx - (ny - cy) * 0.25, my + (nx - cx) * 0.25)
            ts = np.linspace(0, 1, 16)[:, None]
            curve = (1 - ts) ** 2 * np.array(center) + 2 * (1 - ts) * ts * np.array(ctrl) + ts ** 2 * np.array((nx, ny))
            cv2.polylines(frame, [curve.astype(np.int32)], False, YELLOW_BGR, 2 if i == state.ops.picked else 1, cv2.LINE_AA)
            if i == state.ops.picked:
                chip = self._chip(label, size, CHIP_TEXT, CHIP_FILL)
            else:
                chip = self._chip(label, size, NODE_TEXT, DARK_FILL, NODE_OUTLINE)
            self._blend_centered(frame, chip, nx, ny)

    def _draw_gauge(self, frame: np.ndarray, tone: float, top: int, bottom: int) -> None:
        """Vertical tone dial: cold (blue) at the top, warm (orange) at the bottom."""
        x = int(self.x + self.margin + self.box_w + self.pad)
        y0, y1 = top + self.pad, bottom - self.pad
        for y in range(y0, y1, 2):
            k = (y - y0) / max(1, y1 - y0)
            color = tuple(int(c * (1 - k) + w * k) for c, w in zip(COLD_BGR, WARM_BGR))
            cv2.line(frame, (x, y), (x, y + 1), color, 4)
        ky = int(y0 + (tone + 1) / 2 * (y1 - y0))
        cv2.circle(frame, (x, ky), 8, (20, 20, 20), -1, cv2.LINE_AA)
        cv2.circle(frame, (x, ky), 8, (245, 245, 245), 2, cv2.LINE_AA)

    def _draw_zone(self, frame: np.ndarray, state: ViewState) -> None:
        """Command zone: outline, recording clock and mic level, hints, flick and hold progress."""
        zx0, zy0, zx1, zy1 = REHEARSE.zone
        x0, y0 = int(zx0 * self.frame_w), int(zy0 * self.frame_h)
        x1, y1 = int(zx1 * self.frame_w) - 2, int(zy1 * self.frame_h)
        active = state.zone_active
        cv2.rectangle(frame, (x0, y0 + 1), (x1, y1), YELLOW_BGR if active else ZONE_BGR, 2 if active else 1, cv2.LINE_AA)

        size = round(self.font_size * 0.6)
        cx, y = (x0 + x1) / 2, y0 + self.pad
        if state.app == "rehearse":
            m, s = divmod(int(state.rec_s), 60)
            chip = self._chip(f"REC {m}:{s:02d}", round(self.font_size * 0.75), NODE_TEXT, DARK_FILL)
            bx0, by0, _, by1 = self._blend_centered(frame, chip, cx + 10, y + chip[0].shape[0] / 2)
            # The dot swells with the microphone level: a flat dot means no sound is arriving.
            cv2.circle(frame, (bx0 - 14, (by0 + by1) // 2), 4 + round(8 * state.mic), REC_BGR, -1, cv2.LINE_AA)
            y = by1 + self.pad // 2
            hints = ("HOLD OPEN PALM: STOP",) if state.drill is not None else \
                ("FLICK SIDEWAYS: NEXT SECTION", "HOLD OPEN PALM: STOP")
        elif state.app == "count_in":
            hints = ("HOLD OPEN PALM: CANCEL",)
        else:
            hints = ("HOLD OPEN PALM: BACK TO PREPARE",)
        for text in hints:
            chip = self._chip(text, size, NODE_TEXT if active else DIM, DARK_FILL)
            y = self._blend_centered(frame, chip, cx, y + chip[0].shape[0] / 2)[3] + 4

        if state.app == "rehearse":
            # Flick meter: fills as the hand swings sideways, so a near miss shows.
            my, half = y1 - 30, (x1 - x0) // 2 - 12
            cv2.line(frame, (int(cx - half), my), (int(cx + half), my), ZONE_BGR, 1, cv2.LINE_AA)
            if state.flick_progress > 0:
                reach = int(half * state.flick_progress)
                cv2.line(frame, (int(cx - reach), my), (int(cx + reach), my), YELLOW_BGR, 4, cv2.LINE_AA)
        if state.hold_progress > 0:
            cv2.rectangle(frame, (x0 + 6, y1 - 12), (x0 + 6 + int((x1 - x0 - 12) * state.hold_progress), y1 - 6),
                          YELLOW_BGR, -1, cv2.LINE_AA)

    def draw(self, frame: np.ndarray, state: ViewState) -> np.ndarray:
        """Composite the overlay onto `frame` in place and return it."""
        box_top = self.y + self.margin
        top, bottom = box_top, box_top + self.box_h
        unit = self._panel_unit(state)
        if unit is not None:
            panel_h, text = self._focus_panel(unit, state.detail, state.mark_verdicts)
            top = max(0, box_top + (self.box_h - panel_h) // 2)
            bottom = top + panel_h
            _blend(frame, self.x, top - self.margin, *self._backing(panel_h))
            _blend(frame, self.x + self.margin, top, *text)
        else:
            _blend(frame, self.x, self.y, *self._backing(self.box_h))
            _blend(frame, self.x + self.margin, box_top, *self._band_crop(state))

        # Word chips are drawn over the cached band, so hovering never re-renders text.
        if state.mode == "browse" and state.level == "word" and state.hover and state.hover.word is not None:
            if (box := self.word_box(state.hover, state.scroll)) is not None:
                chip = self._chip(self.word_text(state.hover), self.font_size, CHIP_TEXT, CHIP_FILL)
                self._blend_centered(frame, chip, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if state.mode == "focus" and state.level == "word" and state.focus and state.focus.word is not None:
            if (box := self.word_box(state.focus, state.scroll)) is not None:
                center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                if state.ops.kind == "ring":
                    self._draw_ring(frame, state, center)
                text = self.ring_labels(state)[state.ops.picked] if state.ops.kind == "ring" else self.word_text(state.focus)
                chip = self._chip(text, round(self.font_size * 1.3), ORANGE, DARK_FILL)
                self._blend_centered(frame, chip, *center)

        if state.mode == "focus" and state.ops.kind == "tone":
            self._draw_gauge(frame, state.ops.tone, top, bottom)
        if state.mode == "focus" and state.ops.stretch_ends is not None:
            a, b = (tuple(int(v) for v in p) for p in state.ops.stretch_ends)
            cv2.line(frame, a, b, (245, 245, 245), 2, cv2.LINE_AA)
        if state.app in ("count_in", "rehearse", "review"):
            self._draw_zone(frame, state)
        if state.app == "count_in" and state.count_in > 0:
            # In the clear space between the notes and the right edge, below the zone.
            chip = self._chip(str(state.count_in), self.font_size * 5, ORANGE, None)
            right = self.x + self.margin + self.box_w
            self._blend_centered(frame, chip, (right + self.frame_w) / 2, self.frame_h * 0.65)
        self._draw_label(frame, state, top)
        return frame


def _bar(progress: float, width: int = 10) -> str:
    filled = round(progress * width)
    return "[" + "=" * filled + " " * (width - filled) + "]"


def _premultiply(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """RGBA image -> (premultiplied BGR float32, inverse alpha)."""
    arr = np.asarray(img, dtype=np.float32)
    alpha = arr[..., 3:4] / 255.0
    return arr[..., 2::-1] * alpha, 1.0 - alpha


def _blend(frame: np.ndarray, x: int, y: int, color: np.ndarray, inv_alpha: np.ndarray) -> None:
    """Alpha-composite a premultiplied patch at (x, y), clipped to the frame."""
    fh, fw = frame.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + color.shape[1], fw), min(y + color.shape[0], fh)
    if x1 <= x0 or y1 <= y0:
        return
    c = color[y0 - y : y1 - y, x0 - x : x1 - x]
    a = inv_alpha[y0 - y : y1 - y, x0 - x : x1 - x]
    region = frame[y0:y1, x0:x1]
    region[:] = (region * a + c).astype(np.uint8)
