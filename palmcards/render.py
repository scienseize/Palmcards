"""Pillow text overlay composited onto the mirrored frame.

Kat's style: a small, dense monospace block (at least TEXT.min_columns
characters a row) floating beside the user, a paragraph's sentences running
on, a blank row between paragraphs. The unit under the hand sits on an orange
fill in dark text, the rest of its paragraph on a slate-blue fill, with an
orange bar beside it; without a hand the current sentence is orange. The
video is darkened under the text column (the scrim) and each glyph has a
faint dark halo, so the text reads on a bright wall. Everything the user
reads stays in the text column on the left (style.LAYOUT); nothing is drawn
over the face; the hand zone on the right holds only the hand box, the
command zone and its hints.

The layout is a grid of monospace cells, so every word has a known row and
column range; a row can hold several sentences. hit_test() maps a point back
to (sentence, word), and
cursor_to_text() maps the relative hand-box cursor to such a point.

On top of the text: the state label (Kat's `BROWSE BY WORD`, then the
operation, then the gesture hint), and
Prepare's operations: the selected word's meaning, then an open palm spreads
the options ring (the original word and alternatives when the optional LLM is on; an L-hand turns it like a
knob, the picked node at 12 o'clock, its word scrambling into the sentence),
and the tone gauge and stretch line; without the LLM these two only preview,
and say so. While the
cloud LLM is on, a CLOUD LLM chip sits at the bottom left ("SENDING" while a
request is out).

In Rehearse the current section is shown in the focus panel, the current
sentence in orange and the rest dimmed, with the command zone (top right),
a recording clock and microphone level, and a large 3-2-1 during the
count-in. A drill shows just its one sentence.

The focus panel is laid out whole and shown through a viewport that never
covers the label and never runs off the frame: a long section or a long
list of takes scrolls (`ViewState.panel_scroll`, pixels), with a
scrollbar and more-above/below markers. Words too long for a row are split
across rows.

In Review the take table (palmcards.review) sits under the notes while
browsing, and a focused sentence lists every take that said it under it.

A persistent alert line (recording or analysis trouble) sits under the text,
apart from the label's transient hints; `h` shows the keyboard fallback.

Also here, so that every visual is drawn in one module: the fingertip dots,
the debug drawings (landmarks, hand box, zone outline, text readouts), the
stats line, and the take player's caption and timeline strip. How all of it
looks (colours, fonts, sizes, spacing, positions) is in palmcards/style.py.
"""

from __future__ import annotations

import math
import random
import re
import textwrap
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from palmcards.config import CURSOR, KNOB, REHEARSE
from palmcards.gestures import HAND_CONNECTIONS, INDEX_TIP, TIPS
from palmcards.notes import Sentence
from palmcards.style import (
    CHIPS, COLORS, COUNT_IN, SUMMARY, DEBUG, DETAIL, FILL, GAUGE, HANDS, HIGH_CONTRAST, LABEL, LAYOUT, OUTLINE, PLAYER,
    RING, SCRIM, SHADOW, TEXT, ZONE, bgr,
)

if TYPE_CHECKING:
    from palmcards.gestures import CommandZone, GestureState, Hand, RelativeCursor

C = COLORS
CLEAR = (0, 0, 0, 0)


def set_contrast(high: bool) -> None:
    """High-contrast colours (preferences); build a new TextOverlay afterwards."""
    global C
    C = HIGH_CONTRAST if high else COLORS
BAND_SLACK_ROWS = 6  # rows rendered beyond the window on each side
CHIP_CACHE_MAX = 256  # the recording clock makes a new chip every second
HINT_SEP = "  /  "  # in the label's second line: the operation, then the gesture hints
SCRAMBLE = "abcdefghijklmnopqrstuvwxyz#%&@$"
FOCUS_HINTS = {  # without the optional LLM
    "word": "MEANING AND ALTERNATIVES NEED THE OPTIONAL LLM  /  DROP HAND: BACK",
    "sentence": "HOLD OPEN PALM: HEAR IT  /  L-HAND: TONE (PREVIEW ONLY)  /  DROP HAND: BACK",
    "paragraph": "TWO L-HANDS: LENGTH (PREVIEW ONLY)  /  DROP HAND: BACK",
}
FOCUS_HINTS_LLM = {
    "word": "OPEN PALM: ALTERNATIVES  /  DROP HAND: BACK",
    "sentence": "HOLD OPEN PALM: HEAR IT  /  L-HAND: TONE  /  DROP HAND: BACK",
    "paragraph": "TWO L-HANDS: LENGTH  /  DROP HAND: BACK",
}
NEEDS_LLM = "(PREVIEW ONLY: NEEDS THE OPTIONAL LLM)"
KEYS_HELP = (
    "KEYS (WHEN GESTURES WON'T DO)",
    "T  START A TAKE      X  STOP / CANCEL",
    "N B  NEXT / PREVIOUS SECTION     U  UNDO EDIT",
    "J K  NEXT / PREVIOUS SENTENCE, OR SCROLL",
    "A  PLAY SENTENCE     P  BACK TO PREPARE",
    "E  CALIBRATE EYES AT THE NEXT TAKE",
    "R  RETRY ANALYSIS    H  HIDE    Q  QUIT",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    if not TEXT.font.exists():
        raise FileNotFoundError(f"font missing: {TEXT.font} (it ships in the repo; restore it with git)")
    return ImageFont.truetype(TEXT.font, size)


def resolve_scramble(text: str, progress: float, seed: int) -> str:
    """Random glyphs resolving into `text` left to right: at progress 0 every
    letter is scrambled, at 1 it is the text. Same length throughout (the
    font is monospace, so the width holds); `seed` picks the glyphs (a new one
    each frame makes them flicker)."""
    if progress >= 1.0:
        return text
    done = int(len(text) * max(progress, 0.0))
    rng = random.Random(seed)
    return "".join(c if i < done or not c.isalpha() else rng.choice(SCRAMBLE) for i, c in enumerate(text))


def ring_rotation(ops: "OpsView", now: float) -> float:
    """Where the options ring is in its turn (nodes, unwrapped): eased out."""
    if ops.turned_t is None:
        return ops.rot_to
    p = min(max((now - ops.turned_t) / KNOB.rotate_s, 0.0), 1.0)
    return ops.rot_from + (ops.rot_to - ops.rot_from) * (1 - (1 - p) ** 3)


@dataclass(frozen=True)
class Hit:
    sentence: int
    word: int | None  # None: sentence/paragraph level, or between words


@dataclass
class OpsView:
    """What the operation stubs show; filled from the gesture state."""

    kind: str | None = None  # ring | tone | stretch
    picked: int = 0  # ring node (ring_labels order), 0 = original word
    pointing: bool = False  # choosing: pointing at Review's takes, turning the ring
    # The ring turned like a knob (follow_ring): its rotation in nodes, unwrapped
    # (+ clockwise), eases from rot_from to rot_to over KNOB.rotate_s from
    # turned_t. `turn` is the grammar's ring_turn it has followed. `preview` is
    # the word shown in the sentence (the picked word node, else the word
    # itself); it scrambles in from scrambled_t. changed_t: when the pick last changed.
    rot_from: float = 0.0
    rot_to: float = 0.0
    turned_t: float | None = None
    turn: int = 0
    nodes: int = 0
    pick_label: str | None = None
    changed_t: float | None = None
    preview: str | None = None
    scrambled_t: float | None = None
    tone: float = 0.0  # -1 cold .. 1 warm
    stretch: float = 1.0
    stretch_ends: tuple[tuple[float, float], tuple[float, float]] | None = None
    closing: bool = False  # the thumb is closing into a pinch: the dial is held where it was, drawn bolder


@dataclass
class ViewState:
    current: int = 0  # sentence drawn in orange when no hand is up
    now: float = 0.0  # app clock, s: the options ring's animations
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
    title: str = ""  # replaces the first label line (the take player; "GAZE CHECK" in its takes)
    start_progress: float = 0.0  # fist held to start a take, 0..1
    section: int = 0  # count_in, rehearse: the section on screen
    count_in: int = 0  # 3, 2, 1
    calibration: str | None = None  # count_in: the calibration step, "camera" (look into the lens) or "notes"
    rec_s: float = 0.0  # length of the take so far
    mic: float = 0.0  # microphone level, 0..1
    zone_active: bool = False  # a hand is in the command zone
    hold_progress: float = 0.0  # open palm held in the zone, 0..1
    flick_progress: float = 0.0  # sideways swing toward a flick, 0..1
    drill: int | None = None  # count_in, rehearse: the one sentence a drill rehearses
    detail: tuple[str, ...] = ()  # Review focus: the lines under the sentence (a take each)
    summary: tuple[str, ...] = ()  # Review, browsing: the take table (palmcards.review), a line each
    panel_scroll: float = 0.0  # px into the focus panel's content (clamped when drawn)
    alert: str = ""  # persistent: recording or analysis trouble, until it is dealt with
    keys_help: bool = False  # the keyboard fallback, shown with `h`
    # Rehearse: the next section shown faint under the current one, while its
    # predecessor's last sentence is being said (hides the follow's lag).
    preview_next: bool = False
    # Prepare, the optional LLM: alternatives for the focused word (on the
    # ring), a request in flight (the word's glyphs scramble), and a proposal
    # for the focused unit (shown under it; pinch + lift uses it).
    alternatives: tuple[str, ...] = ()
    meaning: str = ""  # shown on word selection, before opening alternatives
    loading: bool = False
    proposal: str = ""
    llm: str = ""  # the optional LLM in use: "cloud" (the text asked about leaves the Mac), "local", or ""
    llm_busy: bool = False  # a request is out
    # Review, a focused sentence: the takes that said it, as chips beside it to
    # point at (an L, then the fingertip), and which one it shows.
    takes: tuple[str, ...] = ()
    take_shown: int = 0
    tutorial: tuple[int, int, str] | None = None  # (step, of, what to do) on the first run, or after g


@dataclass
class Panel:
    """The focus panel laid out whole; drawn through a viewport."""
    height: int  # of the content, px
    color: np.ndarray
    inv: np.ndarray
    rows: dict[int, tuple[int, int]]  # sentence -> (top, bottom) of its enlarged rows, px into the content


@dataclass(frozen=True)
class Span:
    text: str
    role: str  # "word" | "punct"
    word: int | None = None
    sentence: int = 0  # which sentence it is from


@dataclass
class Row:
    spans: list[tuple[int, Span]]  # (column, span); none: the blank row between paragraphs

    @property
    def sentence(self) -> int | None:
        """The row's first sentence; None for a blank row."""
        return self.spans[0][1].sentence if self.spans else None


def sentence_units(s: Sentence, sentence: int = 0) -> list[list[Span]]:
    """Display units (unbreakable, space-separated) for one sentence."""
    units: list[list[Span]] = []
    wi = 0
    for tok in re.finditer(r"\S+", s.text):
        if wi < len(s.words) and tok.start() == s.words[wi].start:
            units.append([Span(tok.group(), "word", wi, sentence)])
            wi += 1
        else:
            units.append([Span(tok.group(), "punct", None, sentence)])
    return units


def _split(unit: list[Span], columns: int) -> list[list[Span]]:
    """A unit wider than a row, cut into row-wide pieces; each piece keeps
    its spans' word, so hit-testing still finds the word."""
    pieces, piece, width = [], [], 0
    for sp in unit:
        text = sp.text
        while text:
            take = min(len(text), columns - width)
            piece.append(Span(text[:take], sp.role, sp.word, sp.sentence))
            text, width = text[take:], width + take
            if width == columns:
                pieces.append(piece)
                piece, width = [], 0
    if piece:
        pieces.append(piece)
    return pieces


def layout(sentences: list[Sentence], columns: int, ids=None) -> list[Row]:
    """Greedy wrap, Kat's way: a paragraph's sentences run on from one to the
    next, and a blank row separates paragraphs (and sections). `ids` names
    the sentences in the spans (default: their positions in `sentences`)."""
    rows: list[Row] = []
    row, col, paragraph = None, 0, None
    for si, s in zip(range(len(sentences)) if ids is None else ids, sentences):
        if row is None or (s.section, s.paragraph) != paragraph:
            if row is not None:
                rows += [row, Row([])]
            row, col, paragraph = Row([]), 0, (s.section, s.paragraph)
        units = []
        for unit in sentence_units(s, si):
            units += _split(unit, columns) if sum(len(sp.text) for sp in unit) > columns else [unit]
        for unit in units:
            width = sum(len(sp.text) for sp in unit)
            if row.spans and col + 1 + width > columns:
                rows.append(row)
                row, col = Row([]), 0
            elif row.spans:
                col += 1
            for sp in unit:
                row.spans.append((col, sp))
                col += len(sp.text)
    if row is not None:
        rows.append(row)
    return rows


def _text_metrics(size: int) -> tuple[int, int, float]:
    """(line height, height of the glyphs, character width) of the notes' font at this size."""
    font = load_font(size)
    glyphs = sum(font.getmetrics())
    return int(glyphs * TEXT.line_spacing), glyphs, font.getlength("M")


class TextOverlay:
    """Renders a scrollable window of sentences onto a BGR frame."""

    def __init__(
        self,
        sentences: list[Sentence],
        frame_size: tuple[int, int],
        columns: int | None = None,
        visible_rows: int | None = TEXT.visible_rows,
    ):
        self.sentences = sentences
        self.frame_w, self.frame_h = w, h = frame_size
        self.col_x0, self.col_x1 = (int(f * w) for f in LAYOUT.text)  # the text column
        # The notes' size: smaller until a row of the column holds TEXT.min_columns characters.
        size = max(TEXT.min_size, h // TEXT.rows_per_frame)
        while size > TEXT.min_size:
            line_h, _, char_w = _text_metrics(size)
            if (self.col_x1 - self.col_x0 - 2 * (line_h // 2)) / char_w >= TEXT.min_columns:
                break
            size -= 1
        self.font_size = size
        self.ui_size = max(TEXT.ui_min_size, h // TEXT.ui_rows_per_frame)  # labels, hints, pills, the take table
        self.font = load_font(self.font_size)
        self._fonts = {self.font_size: self.font}
        self.line_h, glyphs, self.char_w = _text_metrics(self.font_size)
        self.row_gap = self.line_h - glyphs  # below each row's glyphs
        self.pad = self.line_h // 2
        self.halo_r = max(1.0, self.font_size * SHADOW.blur)
        # The box fills the text column, with as many columns as fit.
        if columns is None:
            columns = max(1, int((self.col_x1 - self.col_x0 - 2 * self.pad) / self.char_w))
        self.columns = columns
        self.rows = layout(sentences, columns)
        self._first_row: dict[int, int] = {}
        self._last_row: dict[int, int] = {}
        self._word_pos: dict[Hit, tuple[int, int, int]] = {}  # -> (row, column, length)
        for i, r in enumerate(self.rows):
            for col, sp in r.spans:
                self._first_row.setdefault(sp.sentence, i)
                self._last_row[sp.sentence] = i
                if sp.role == "word":
                    self._word_pos.setdefault(Hit(sp.sentence, sp.word), (i, col, len(sp.text)))

        self.box_w = int(columns * self.char_w) + 2 * self.pad
        self.margin = 0  # the text box's inset from (x, y); no backing patch around it any more
        # At the text column's left, right under the label; with no count
        # given, as many rows as fit above the pills at the bottom (Kat's
        # block runs down the whole left side).
        self.x = self.col_x0
        self.y = LABEL.min_top + self.label_h + self.pad // 2
        if visible_rows is None:
            pill = round(self.ui_size * LABEL.pill_scale)
            low = h - LABEL.min_top - (sum(self._get_font(pill).getmetrics()) + 2 * max(CHIPS.pad_y[0], pill // CHIPS.pad_y[1]) + 2)
            visible_rows = max(3, (low - self.pad // 2 - self.y - 2 * self.pad) // self.line_h)
        self.visible_rows = visible_rows
        self.box_h = visible_rows * self.line_h + 2 * self.pad
        if self.y + self.box_h > h:  # a row count too tall for the frame: centred
            self.y = max(0, (h - self.box_h) // 2)

        self._fades: dict[int, np.ndarray] = {}
        self._scrim: np.ndarray | None = None  # its shape across the columns, 1 at full strength
        self._scrim_alpha: float | None = None  # its strength, following the room's brightness
        self._scrim_frames = 0
        # Text is rendered into a band of rows taller than the window, so
        # scrolling only moves a crop through it (see _band_crop).
        self.band_rows = visible_rows + 2 * BAND_SLACK_ROWS
        self._band_key = None
        self._band = None
        self._band_start = 0
        self._panel_key = None
        self._panel = None
        self._zoom_key = None
        self._zoom = None
        self._zoom_rows: dict[int, list[Row]] = {}
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
        if not self.rows[ri].spans and ri > 0:  # the gap between paragraphs: the one above
            ri -= 1
        row = self.rows[ri]
        if not row.spans:
            return None
        col = lx / self.char_w

        def dist(c: int, sp: Span) -> float:
            return 0.0 if c <= col <= c + len(sp.text) else min(abs(col - c), abs(col - c - len(sp.text)))

        best, best_d = None, math.inf if snap else 1.0  # else up to one cell outside the word
        for c, sp in row.spans:
            if sp.role == "word" and (d := dist(c, sp)) < best_d:
                best, best_d = sp, d
        if best is not None:
            return Hit(best.sentence, best.word)
        return Hit(min(row.spans, key=lambda cs: dist(*cs))[1].sentence, None)  # the sentence nearest the point

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
        return x0, y0, x0 + length * self.char_w, y0 + self.line_h * TEXT.word_box_h

    # --- labels ------------------------------------------------------------

    def label_lines(self, state: ViewState) -> tuple[str, str]:
        """Kat's two-line state label: mode and level, then the operation."""
        if state.title and state.app not in ("count_in", "rehearse"):
            return state.title, state.note or state.status
        if state.app in ("count_in", "rehearse"):
            if state.hold_progress > 0:
                second = f"{'CANCEL' if state.app == 'count_in' else 'STOP'}: HOLD  {_bar(state.hold_progress)}"
            elif state.note:
                second = state.note
            elif state.calibration == "camera":
                second = "LOOK INTO THE CAMERA ABOVE THE SCREEN"
            elif state.calibration == "notes":
                second = f"NOW READ THE ORANGE SENTENCE  {state.count_in}"
            elif state.app == "count_in":
                second = f"STARTING IN {state.count_in}"
            else:
                second = state.status
            return state.title or ("DRILL" if state.drill is not None else "REHEARSE"), second

        level = (state.level or "").upper()
        if state.mode == "focus":
            first = f"FOCUS BY {level}"
            if state.level == "word" and state.focus is not None and state.focus.word is not None:
                first += f'  "{self.focus_word(state)}"'
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
            second = "EXPLORE ALTERNATIVES: LOADING" if state.loading else "L-HAND, THEN TURN TO PICK"
            if not state.llm:
                second = "ALTERNATIVES NEED THE OPTIONAL LLM  /  DROP HAND: BACK"
            if ops.pointing or ops.picked:
                picked = self.ring_labels(state)[ops.picked]
                second = "KEEP THE WORD (NO CHANGE)" if ops.picked == 0 else f'PINCH + LIFT: USE "{picked.upper()}"'
        elif ops.kind == "tone":
            tone = "WARM" if ops.tone > 0.15 else "COLD" if ops.tone < -0.15 else "NEUTRAL"
            second = f"SENTENCE TONE: {tone}  " + ("/  PINCH + LIFT: ASK FOR A REWRITE" if state.llm else NEEDS_LLM)
        elif ops.kind == "stretch":
            pct = round((ops.stretch - 1) * 100)
            change = f"FULLER +{pct}%" if ops.stretch > 1.05 else f"SHORTER −{-pct}%" if ops.stretch < 0.95 else "SAME"
            second = f"PARAGRAPH LENGTH: {change}  " + ("/  PINCH + LIFT: ASK FOR A REWRITE" if state.llm else NEEDS_LLM)
        elif state.mode == "focus" and state.app == "prepare" and state.proposal:
            second = "PINCH + LIFT: USE THE PROPOSAL  /  DROP HAND: DISCARD IT"
        elif state.mode == "focus" and state.app == "prepare":  # nothing started yet: say what the next shape does
            second = (FOCUS_HINTS_LLM if state.llm else FOCUS_HINTS).get(state.level, "")
        elif state.mode == "focus":
            second = state.status or "DROP HAND: BACK"
        elif state.app == "prepare" and state.mode == "browse" and state.level == "sentence":
            second = "FOLD TO SELECT  /  THEN HOLD OPEN PALM: HEAR IT"
        else:
            second = state.status
        return first, second

    def ring_words(self, state: ViewState) -> int:
        """Every node is the original word or a replacement."""
        return len(self.ring_labels(state)) if state.focus is not None and state.focus.word is not None else 0

    def follow_ring(self, state: ViewState, pick: str | None, turn: int) -> None:
        """The options ring follows the grammar's knob (GestureState.ring_pick,
        ring_turn): the ring turns the way the hand went, the short way when
        the nodes changed under it (alternatives arriving: they reflow, no
        turn); a new word in the sentence scrambles in; a newly picked node's
        box empties while its word moves up."""
        ops, now = state.ops, state.now
        labels = self.ring_labels(state)
        if not labels:
            return
        picked = labels.index(pick) if pick in labels else 0
        n = len(labels)
        if n != ops.nodes:
            ops.rot_from = ops.rot_to = float(picked)
            ops.turned_t, ops.nodes = None, n
        else:
            aim = ops.rot_to + (turn - ops.turn)  # where the knob's steps take it
            target = picked + n * round((aim - picked) / n)
            if target != ops.rot_to:
                ops.rot_from, ops.rot_to, ops.turned_t = ring_rotation(ops, now), float(target), now
        ops.turn = turn
        if ops.pick_label is not None and labels[picked] != ops.pick_label:
            ops.changed_t = now
        ops.pick_label, ops.picked = labels[picked], picked
        preview = labels[picked] if picked < self.ring_words(state) else labels[0]
        if ops.preview is not None and preview != ops.preview:
            ops.scrambled_t = now
        ops.preview = preview

    def focus_word(self, state: ViewState) -> str:
        """The focused word as the sentence shows it: on the options ring, the
        picked word (scrambling in for KNOB.scramble_s after a change)."""
        ops = state.ops
        if ops.kind != "ring" or ops.preview is None:
            return self.word_text(state.focus)
        if ops.scrambled_t is not None and state.now - ops.scrambled_t < KNOB.scramble_s:
            return resolve_scramble(ops.preview, (state.now - ops.scrambled_t) / KNOB.scramble_s,
                                    int(state.now * 60))
        return ops.preview

    def ring_labels(self, state: ViewState) -> tuple[str, ...]:
        """The word as it is and its alternatives. Word alternatives
        need the optional LLM and are not offered without one."""
        if state.focus is None or state.focus.word is None:
            return ("original",)
        return (self.word_text(state.focus), *state.alternatives)

    # --- drawing: text -----------------------------------------------------

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        if size not in self._fonts:
            self._fonts[size] = load_font(size)
        return self._fonts[size]

    def _fade(self, h: int) -> np.ndarray:
        """Alpha over a viewport `h` px tall: 1 on its rows, ramping to 0 over
        TEXT.fade lines into the padding above and below, so a row partly
        scrolled out fades instead of being cut through its letters."""
        if h not in self._fades:
            f = max(1.0, TEXT.fade * self.line_h)
            y = np.arange(h, dtype=np.float32)
            top = (y - (self.pad - f)) / f
            bottom = (h - self.pad - self.row_gap + f - y) / f  # the last row's glyphs end row_gap above its cell
            self._fades[h] = np.clip(np.minimum(top, bottom), 0.0, 1.0)[:, None, None]
        return self._fades[h]

    def _faded(self, color: np.ndarray, inv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k = self._fade(color.shape[0])
        return color * k, 1.0 - (1.0 - inv) * k

    def _band_style(self, state: ViewState) -> tuple:
        """What the band's colours depend on (also its cache key). Browsing:
        the unit under the hand (none at word level: its chip is drawn over
        the band), the rest of its paragraph, and the paragraph. Otherwise the
        current sentence."""
        if state.mode == "browse" and state.hover is not None:
            paragraph = tuple(self.unit("paragraph", state.hover.sentence))
            unit = () if state.level == "word" else tuple(self.unit(state.level, state.hover.sentence))
            return ("browse", (unit, tuple(i for i in paragraph if i not in unit), paragraph))
        return ("idle", state.current)

    def _draw_span(self, draw: ImageDraw.ImageDraw, xy: tuple[float, float], sp: Span, font, color,
                   under: ImageDraw.ImageDraw | None = None) -> None:
        """A word or punctuation; its outline on `under` (text on a fill has none)."""
        if under is not None:
            _outline(under, xy, sp.text, font)
        draw.text(xy, sp.text, font=font, fill=color)

    def _fill_box(self, c0: int, c1: int, y: int) -> tuple[float, float, float, float]:
        """A tight fill behind columns c0..c1 of the row drawn at y; rows' fills touch."""
        top = y - self.row_gap // 2
        return (self.pad + c0 * self.char_w - FILL.pad_x, top,
                self.pad + c1 * self.char_w + FILL.pad_x - 1, top + self.line_h - 1)

    def _span_color(self, kind: str, which, sp: Span):
        if kind == "browse":
            return C.unit_text if sp.sentence in which[0] else C.text
        return C.orange_text if sp.sentence == which else C.text  # idle: the current sentence

    def _render_band(self, style: tuple, start: int) -> tuple[np.ndarray, np.ndarray]:
        """Rows start..start+band_rows, with row `start` at y = pad: the
        fills, the text on its halo and outline, then the text on the orange
        fill (neither: dark on orange)."""
        band_h = self.band_rows * self.line_h + 2 * self.pad
        fills, text, under, on_fill = (Image.new("RGBA", (self.box_w, band_h), CLEAR) for _ in range(4))
        fd, td, ud, od = (ImageDraw.Draw(img) for img in (fills, text, under, on_fill))
        kind, which = style
        for ri in range(start, min(len(self.rows), start + self.band_rows)):
            row = self.rows[ri]
            y = self.pad + (ri - start) * self.line_h
            if kind == "browse" and row.spans:
                unit, context, paragraph = which
                for group, color in ((context, C.context_fill), (unit, C.unit_fill)):
                    cols = [(c, c + len(sp.text)) for c, sp in row.spans if sp.sentence in group]
                    if cols:
                        fd.rectangle(self._fill_box(min(a for a, _ in cols), max(b for _, b in cols), y), fill=color)
                if any(sp.sentence in paragraph for _, sp in row.spans):  # Kat's bar beside the paragraph
                    top = y - self.row_gap // 2
                    fd.rectangle((FILL.bar_x, top, FILL.bar_x + FILL.bar_w - 1, top + self.line_h - 1), fill=C.orange)
            for col, sp in row.spans:
                on_unit = kind == "browse" and sp.sentence in which[0]
                self._draw_span(od if on_unit else td, (self.pad + col * self.char_w, y), sp, self.font,
                                self._span_color(kind, which, sp), None if on_unit else ud)
        img = Image.alpha_composite(Image.alpha_composite(fills, _halo(text, self.halo_r, under)), on_fill)
        return _premultiply(img)

    def _word_zoom(self, state: ViewState) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float, float], int]:
        """Word focus, Kat's way: the notes zoomed in (TEXT.word_zoom),
        re-wrapped to the text column and scrolled so the focused word's row
        is in the middle of the box, where the options ring spreads round the
        word. The word is left out (draw() puts it, or its preview, there) and
        the rest of its row moves over by the preview's change in length; the
        rest of the text stays readable, dimmed. Returns the patch (drawn at
        x 0, the box's top), the word's box on screen and the zoomed text size."""
        focus, ring, shown = state.focus, state.ops.kind == "ring", len(self.focus_word(state))
        key = (focus.sentence, focus.word, shown, ring)
        if self._zoom_key == key:
            return self._zoom
        size = round(self.font_size * TEXT.word_zoom)
        font = self._get_font(size)
        lh, cw = round(self.line_h * TEXT.word_zoom), font.getlength("M")
        cols = max(1, int((self.col_x1 - self.col_x0 - 2 * self.pad) / cw))
        if cols not in self._zoom_rows:
            self._zoom_rows[cols] = layout(self.sentences, cols)
        rows = self._zoom_rows[cols]
        word_row, word_col, word_len = next(
            ((ri, c, len(sp.text)) for ri, r in enumerate(rows) for c, sp in r.spans
             if sp.sentence == focus.sentence and sp.word == focus.word), (0, 0, shown))
        # The text where it wraps; the word's row in the middle of the box.
        x0, shift = self.col_x0 + self.pad, shown - word_len
        word_x, word_y = x0 + word_col * cw, (self.box_h - lh) / 2
        img, under = (Image.new("RGBA", (self.col_x1, self.box_h), CLEAR) for _ in range(2))
        draw, ud = ImageDraw.Draw(img), ImageDraw.Draw(under)
        above, below = int(word_y // lh) + 1, int((self.box_h - word_y) // lh) + 1
        for ri in range(max(0, word_row - above), min(len(rows), word_row + below)):
            y, after = word_y + (ri - word_row) * lh, False
            for c, sp in rows[ri].spans:
                if sp.sentence == focus.sentence and sp.word == focus.word:
                    after = ri == word_row
                    continue
                x = x0 + (c + (shift if after else 0)) * cw
                if x + len(sp.text) * cw > 0 and x < self.col_x1:
                    color = (C.ring_sentence if ring else C.dim) if sp.sentence == focus.sentence else \
                        (C.ring_context if ring else C.faint)
                    self._draw_span(draw, (x, y), sp, font, color, ud)
        color, inv = self._faded(*_premultiply(_halo(img, self.halo_r, under)))
        top = self.y + self.margin + word_y
        self._zoom_key = key
        self._zoom = (color, inv, (word_x, top, word_x + shown * cw, top + lh * TEXT.word_box_h), size)
        return self._zoom

    def focus_word_box(self, state: ViewState) -> tuple[float, float, float, float] | None:
        """Word focus: where the focused word is on screen (in the middle of the zoomed text)."""
        if state.mode == "focus" and state.focus is not None and state.focus.word is not None:
            return self._word_zoom(state)[2]
        return None

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
        return self._faded(color[off : off + self.box_h], inv[off : off + self.box_h])

    def _panel_unit(self, state: ViewState) -> tuple[int, ...] | None:
        """Sentences shown enlarged in the panel instead of the scrolling text, if any."""
        if state.app in ("count_in", "rehearse") and state.drill is not None:
            return (state.drill,)
        if state.app in ("count_in", "rehearse"):
            first = next((i for i, s in enumerate(self.sentences) if s.section == state.section), 0)
            unit = self.unit("section", first)
            if state.preview_next and unit[-1] + 1 < len(self.sentences):
                unit += self.unit("section", unit[-1] + 1)
            return tuple(unit)
        if state.mode == "focus" and state.focus is not None and state.level in ("sentence", "paragraph"):
            return tuple(self.unit(state.level, state.focus.sentence))
        return None

    def _preview_from(self, state: ViewState, unit: tuple[int, ...] | None) -> int | None:
        """First sentence of the previewed next section in the panel, if any."""
        if unit and state.app in ("count_in", "rehearse") and state.preview_next:
            return next((i for i in unit if self.sentences[i].section != state.section), None)
        return None

    def _panel_current(self, state: ViewState, unit: tuple[int, ...] | None) -> int | None:
        """Rehearse: the sentence drawn in orange inside the section panel."""
        if unit and state.app in ("count_in", "rehearse") and state.drill is None and state.current in unit:
            return state.current
        return None

    def _focus_panel(self, unit: tuple[int, ...], detail: tuple[str, ...] = (),
                     current: int | None = None, preview_from: int | None = None, width: int | None = None) -> Panel:
        """The unit's sentences enlarged, then the detail lines (Review: a line
        per take that said the sentence), faint context rows around them when
        there is room.

        Laid out whole: when it is taller than the viewport, draw() shows a
        scrolled part of it. With `current`, that sentence is orange and the
        rest of the unit dimmed (Rehearse); sentences from `preview_from` on
        (the next section, previewed) are faint. `width` narrows it (Review's
        take chips beside it), and then it has no context rows."""
        width = width or self.box_w
        key = (unit, detail, current, preview_from, width)
        if self._panel_key == key:
            return self._panel
        sents = [self.sentences[i] for i in unit]
        dfont = self._get_font(round(self.font_size * DETAIL.scale))
        dlh = round(self.line_h * DETAIL.scale)
        dcols = max(DETAIL.min_columns, int((width - 2 * self.pad) / dfont.getlength("M")))
        dlines = [piece for line in detail
                  for piece in textwrap.wrap(line, dcols, subsequent_indent=DETAIL.indent) or [""]]
        detail_h = len(dlines) * dlh + (self.pad // 2 if dlines else 0)
        avail = self.max_panel_h - 2 * self.pad - detail_h
        # The largest size that fits the viewport with enough columns; never smaller than normal.
        for scale in TEXT.focus_scales:
            font = self._get_font(round(self.font_size * scale))
            lh = round(self.line_h * scale)
            cw = font.getlength("M")
            cols = int((width - 2 * self.pad) / cw)
            rows = layout(sents, cols, unit)
            if len(rows) * lh <= avail and cols >= TEXT.focus_min_columns:
                break
        big_h = len(rows) * lh
        panel_h = max(self.box_h, big_h + detail_h + 2 * self.pad)
        spare = panel_h - 2 * self.pad - big_h - detail_h
        first, last = self._first_row[unit[0]], self._last_row[unit[-1]]
        above = self.rows[max(0, first - int(spare / 2 // self.line_h)) : first]
        below = self.rows[last + 1 : last + 1 + int((spare - len(above) * self.line_h) // self.line_h)]
        if width < self.box_w:  # the context rows are laid out for the full width
            above, below = [], []
        content = (len(above) + len(below)) * self.line_h + big_h + detail_h
        y = self.pad + max(0, (panel_h - 2 * self.pad - content) // 2)

        img, under = (Image.new("RGBA", (width, panel_h), CLEAR) for _ in range(2))
        draw, ud = ImageDraw.Draw(img), ImageDraw.Draw(under)
        where: dict[int, tuple[int, int]] = {}

        def draw_rows(rows_, font_, lh_, cw_, colors, y_):
            for row in rows_:
                for col, sp in row.spans:
                    self._draw_span(draw, (self.pad + col * cw_, y_), sp, font_, colors(sp.sentence), ud)
                    top, bottom = where.get(sp.sentence, (y_, y_))
                    where[sp.sentence] = (min(top, y_), max(bottom, y_ + lh_))
                y_ += lh_
            return y_

        def unit_colors(si):
            if current is not None and si == current:  # orange, even in the previewed section
                return C.orange_text
            if preview_from is not None and si >= preview_from:
                return C.faint
            if current is None:
                return C.focus_text
            return C.orange_text if si == current else C.dim

        y = draw_rows(above, self.font, self.line_h, self.char_w, lambda si: C.faint, y)
        where.clear()  # context rows are not the unit
        y = draw_rows(rows, font, lh, cw, unit_colors, y)
        rows_y = dict(where)
        if dlines:
            y += self.pad // 2
            for piece in dlines:
                _outline(ud, (self.pad, y), piece, dfont)
                draw.text((self.pad, y), piece, font=dfont, fill=C.detail_text)
                y += dlh
        draw_rows(below, self.font, self.line_h, self.char_w, lambda si: C.faint, y)
        color, inv = _premultiply(_halo(img, self.halo_r, under))
        self._panel_key, self._panel = key, Panel(panel_h, color, inv, rows_y)
        return self._panel

    # --- the panel's viewport ------------------------------------------------

    def _label_size(self, line: int) -> int:
        return round(self.ui_size * (LABEL.first_scale, LABEL.second_scale, LABEL.third_scale)[line])

    def _label_row_h(self, line: int) -> int:
        return int(sum(self._get_font(self._label_size(line)).getmetrics()) * 1.1)

    @property
    def label_h(self) -> int:
        """Height reserved for the label above the text: each line at its most rows."""
        return sum(n * self._label_row_h(i) for i, n in enumerate(LABEL.max_rows))

    @property
    def max_panel_h(self) -> int:
        """Tallest the panel's viewport gets: below the label, above the alert line."""
        room = self.frame_h - (LABEL.min_top + self.label_h + self.pad // 2) - self.line_h - self.pad
        return max(self.box_h, min(int(self.frame_h * TEXT.panel_max_h), room))

    def panel(self, state: ViewState) -> Panel | None:
        unit = self._panel_unit(state)
        if unit is None:
            return None
        takes_w = self._takes_w(state)
        width = min(self.box_w, self.col_x1 - takes_w - self.x + self.pad) if takes_w else None
        return self._focus_panel(unit, state.detail, self._panel_current(state, unit),
                                 self._preview_from(state, unit), width)

    def panel_view_h(self, panel: Panel) -> int:
        return min(panel.height, self.max_panel_h)

    def panel_max_scroll(self, state: ViewState) -> float:
        panel = self.panel(state)
        return float(max(0, panel.height - self.panel_view_h(panel))) if panel else 0.0

    def clamp_panel_scroll(self, state: ViewState, scroll: float) -> float:
        return min(max(scroll, 0.0), self.panel_max_scroll(state))

    def panel_scroll_to(self, state: ViewState, sentence: int) -> float:
        """Scroll that shows the sentence whole (with a line above it when it can)."""
        panel = self.panel(state)
        if panel is None or sentence not in panel.rows:
            return state.panel_scroll
        top, bottom = panel.rows[sentence]
        view = self.panel_view_h(panel)
        scroll = state.panel_scroll
        if top - self.line_h < scroll:
            scroll = top - self.line_h
        elif bottom + self.pad > scroll + view:
            scroll = bottom + self.pad - view
        return self.clamp_panel_scroll(state, scroll)

    def _panel_top(self, view_h: int) -> int:
        box_top = self.y + self.margin
        reserved = LABEL.min_top + self.label_h + self.pad // 2
        top = max(reserved, box_top + (self.box_h - view_h) // 2)
        return max(reserved, min(top, self.frame_h - self.line_h - self.pad - view_h))

    def _draw_scrollbar(self, frame: np.ndarray, top: int, view_h: int, scroll: float, height: int,
                        width: int) -> None:
        x = int(self.x + self.margin + width - self.pad // 3)
        cv2.line(frame, (x, top + self.pad), (x, top + view_h - self.pad), bgr(C.scroll_track), 2, cv2.LINE_AA)
        span = view_h - 2 * self.pad
        y0 = top + self.pad + int(span * scroll / height)
        y1 = top + self.pad + int(span * (scroll + view_h) / height)
        cv2.line(frame, (x, y0), (x, y1), bgr(C.scroll_thumb), 4, cv2.LINE_AA)
        size = round(self.ui_size * CHIPS.symbol_scale)
        cx = self.x + self.margin + width / 2
        if scroll > 0:
            self._blend_centered(frame, self._chip("▲ MORE", size, C.node_text, C.dark_fill), cx, top)
        if scroll + view_h < height:
            self._blend_centered(frame, self._chip("▼ MORE", size, C.node_text, C.dark_fill), cx, top + view_h)

    def _chip(self, text: str, size: int, fg, bg, outline=None) -> tuple[np.ndarray, np.ndarray]:
        """A text label on a rounded rectangle, cached."""
        key = (text, size, fg, bg, outline)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            font = self._get_font(size)
            ascent, descent = font.getmetrics()
            px, py = max(CHIPS.pad_x[0], size // CHIPS.pad_x[1]), max(CHIPS.pad_y[0], size // CHIPS.pad_y[1])
            w, h = int(font.getlength(text)) + 2 * px, ascent + descent + 2 * py
            img = Image.new("RGBA", (w + 2, h + 2), CLEAR)
            draw = ImageDraw.Draw(img)
            if bg is not None or outline is not None:
                radius = max(CHIPS.radius[0], size // CHIPS.radius[1])
                draw.rounded_rectangle((1, 1, w, h), radius=radius, fill=bg, outline=outline)
            draw.text((1 + px, 1 + py), text, font=font, fill=fg)
            self._chips[key] = _premultiply(img)
        return self._chips[key]

    def _ink(self, text: str, size: int, fg) -> tuple[np.ndarray, np.ndarray, int]:
        """Text on a soft dark halo and outline, no box, cached: (colour,
        inverse alpha, margin), the text's top left `margin` px in from the patch's."""
        key = ("ink", text, size, fg, C.shadow, C.outline)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            font = self._get_font(size)
            r = max(1.0, size * SHADOW.blur)
            m = math.ceil(3 * r) + OUTLINE.width
            img, under = (Image.new("RGBA", (int(font.getlength(text)) + 2 * m + 1, sum(font.getmetrics()) + 2 * m),
                                    CLEAR) for _ in range(2))
            _outline(ImageDraw.Draw(under), (m, m), text, font)
            ImageDraw.Draw(img).text((m, m), text, font=font, fill=fg)
            self._chips[key] = (*_premultiply(_halo(img, r, under)), m)
        return self._chips[key]

    def _blend_ink(self, frame: np.ndarray, ink, x: float, y: float) -> tuple[int, int, int, int]:
        """Ink with its text's top left at (x, y); returns the text's box."""
        color, inv, m = ink
        x, y = int(x), int(y)
        _blend(frame, x - m, y - m, color, inv)
        h, w = color.shape[:2]
        return x, y, x + w - 2 * m, y + h - 2 * m

    def _blend_centered(self, frame: np.ndarray, chip, cx: float, cy: float) -> tuple[int, int, int, int]:
        """A chip or ink centred on (cx, cy); returns its box (ink: the text's)."""
        color, inv = chip[:2]
        m = chip[2] if len(chip) > 2 else 0
        h, w = color.shape[:2]
        x, y = int(cx - w / 2), int(cy - h / 2)
        _blend(frame, x, y, color, inv)
        return x + m, y + m, x + w - m, y + h - m

    def _wrap(self, text: str, size: int, width: float, max_rows: int | None = None) -> list[str]:
        """`text` wrapped to `width` px at this size; past max_rows the last row ends in "…"."""
        cols = max(4, int(width / self._get_font(size).getlength("M")))
        rows = textwrap.wrap(text, cols) or [text]
        if max_rows is not None and len(rows) > max_rows:
            rows = rows[:max_rows]
            rows[-1] = rows[-1][:cols - 1].rstrip() + "…"
        return rows

    # --- drawing: HUD and operation stubs ----------------------------------

    def label_rows(self, state: ViewState) -> list[tuple[int, str]]:
        """The label as drawn, Kat's style: (line, text) per row. Line 0 is
        the state, 1 the operation, 2 the gesture hint (label_lines' second
        line after its first HINT_SEP); each wrapped to the text column."""
        first, second = self.label_lines(state)
        operation, _, hint = second.partition(HINT_SEP)
        width = self.col_x1 - self.x - self.pad
        rows = [(i, row) for i, text in enumerate((first, operation)) if text
                for row in self._wrap(text, self._label_size(i), width, LABEL.max_rows[i])]
        if hint:  # too long for a row: a row per hint rather than breaking one in two
            hint_rows = self._wrap(hint, self._label_size(2), width)
            if len(hint_rows) > 1:
                hint_rows = [row for part in hint.split(HINT_SEP) for row in self._wrap(part, self._label_size(2), width)]
            if len(hint_rows) > LABEL.max_rows[2]:
                hint_rows = self._wrap(" ".join(hint_rows), self._label_size(2), width, LABEL.max_rows[2])
            rows += [(2, row) for row in hint_rows]
        return rows

    def _draw_label(self, frame: np.ndarray, state: ViewState, top: int) -> None:
        """No box: orange on a halo, dimmer and smaller line by line, ending
        above `top`. Its first row stays put while a hint comes and goes; only
        a label wrapped past three rows grows upward."""
        rows = self.label_rows(state)
        colors = (C.orange, C.orange_soft, C.hint)
        h = max(sum(self._label_row_h(i) for i in range(3)), sum(self._label_row_h(i) for i, _ in rows))
        y = max(LABEL.min_top, top - self.pad // 2 - h)
        for line, text in rows:
            self._blend_ink(frame, self._ink(text, self._label_size(line), colors[line]), self.x + self.pad, y)
            y += self._label_row_h(line)

    def _node_size(self) -> int:
        return round(self.font_size * TEXT.word_zoom * RING.node_scale)

    def _node(self, text: str, fg, fill, outline) -> tuple[np.ndarray, np.ndarray]:
        """An options-ring node, Kat's style: text in a square box, a solid outline, roomy padding; cached."""
        size = self._node_size()
        key = ("node", text, size, fg, fill, outline)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            font = self._get_font(size)
            px, py = round(size * RING.node_pad[0]), round(size * RING.node_pad[1])
            w, h = int(font.getlength(text)) + 2 * px, sum(font.getmetrics()) + 2 * py
            img = Image.new("RGBA", (w + 2, h + 2), CLEAR)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((1, 1, w, h), radius=RING.node_radius, fill=fill, outline=outline,
                                   width=RING.node_outline_w if outline else 0)
            draw.text((1 + px, 1 + py), text, font=font, fill=fg)
            self._chips[key] = _premultiply(img)
        return self._chips[key]

    def ring_nodes(self, state: ViewState) -> list[tuple[float, float]]:
        """Where each of the focused word's options sits on screen (ring_labels
        order), a bubble map round the word: on an ellipse, the picked one at
        12 o'clock and the next ones counter-clockwise from it, so turning the
        ring clockwise (the knob, ring_rotation) brings the next one up. Nodes
        that would leave the text column or the box are pulled in, and
        overlapping ones (or one over the word) pushed apart; the picked node
        holds its place and the others make room. Empty when no word is focused."""
        if (box := self.focus_word_box(state)) is None:
            return []
        labels = self.ring_labels(state)
        n = len(labels)
        sizes = [self._node(label, C.node_text, C.node_fill, C.node_outline)[0].shape[1::-1] for label in labels]
        zoom_lh = self.line_h * TEXT.word_zoom
        rx, ry = RING.rx * zoom_lh, RING.ry * zoom_lh
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        rot = ring_rotation(state.ops, state.now) if state.ops.nodes == n else float(state.ops.picked)
        pts = []
        for i in range(n):
            a = math.radians(-90 - (i - rot) * 360 / n)
            pts.append([cx + rx * math.cos(a), cy + ry * math.sin(a)])
        top, bottom = self.y + self.margin, self.y + self.margin + self.box_h
        word = (cx, cy, box[2] - box[0], box[3] - box[1])
        gap, pinned = RING.node_gap, state.ops.picked
        for _ in range(RING.relax_steps):
            for (w, h), pt in zip(sizes, pts):  # inside the text column (and the frame's left edge), inside the box
                pt[0] = min(max(pt[0], RING.edge_px + w / 2), self.col_x1 - RING.edge_px - w / 2)
                pt[1] = min(max(pt[1], top + h / 2), bottom - h / 2)
            moved = False
            for i in range(n):
                for j in range(i + 1, n + 1):  # j == n: the word itself, which never moves
                    xi, yi, (wi, hi) = pts[i][0], pts[i][1], sizes[i]
                    xj, yj, wj, hj = (*pts[j], *sizes[j]) if j < n else word
                    dx = (wi + wj) / 2 + gap - abs(xi - xj)
                    dy = (hi + hj) / 2 + gap - abs(yi - yj)
                    if dx <= 0 or dy <= 0:
                        continue
                    moved = True
                    # How much of the push each takes: the word never moves; the picked node only off the word.
                    wi, wj = (1.0, 0.0) if j == n else (0.0, 1.0) if i == pinned else (1.0, 0.0) if j == pinned \
                        else (0.5, 0.5)
                    axis, over, sign = (1, dy, 1 if yi >= yj else -1) if dy < dx else (0, dx, 1 if xi >= xj else -1)
                    pts[i][axis] += over * wi * sign  # apart along the smaller overlap
                    if j < n:
                        pts[j][axis] -= over * wj * sign
            if not moved:
                break
        return [(x, y) for x, y in pts]

    def _panel_origin(self, state: ViewState) -> tuple[Panel, float, float, int] | None:
        """The focus panel, where its content's (0, 0) is on screen, and its viewport's top."""
        panel = self.panel(state)
        if panel is None:
            return None
        view_h = self.panel_view_h(panel)
        top = self._panel_top(view_h)
        return panel, self.x + self.margin, top - int(self.clamp_panel_scroll(state, state.panel_scroll)), top

    def _takes_w(self, state: ViewState) -> int:
        """Review: the width the take chips' column needs at the text column's right, gap included."""
        if not state.takes:
            return 0
        size = round(self.ui_size * RING.take_scale)
        return max(self._chip(label, size, C.node_text, C.dark_fill, C.node_outline)[0].shape[1]
                   for label in state.takes) + RING.take_gap

    def _take_chips(self, state: ViewState) -> list[tuple[tuple[np.ndarray, np.ndarray], float, float]]:
        """Review: the focused sentence's takes as chips in a column at the
        text column's right, beside the (narrowed) panel, from its top, a
        fixed pitch apart: (chip, centre x, centre y) each."""
        got = self._panel_origin(state) if state.takes else None
        if got is None:
            return []
        size = round(self.ui_size * RING.take_scale)
        x0, y = self.col_x1 - (self._takes_w(state) - RING.take_gap), got[3] + self.pad
        out = []
        for i, label in enumerate(state.takes):
            chip = self._chip(label, size, C.chip_text, C.chip_fill) if i == state.take_shown \
                else self._chip(label, size, C.node_text, C.dark_fill, C.node_outline)
            h, w = chip[0].shape[:2]
            out.append((chip, x0 + w / 2, y + h / 2 + i * RING.take_pitch * self.box_h))
        return out

    def take_points(self, state: ViewState) -> list[tuple[float, float]]:
        return [(x, y) for _, x, y in self._take_chips(state)]

    def _draw_takes(self, frame: np.ndarray, state: ViewState) -> None:
        for i, (chip, x, y) in enumerate(self._take_chips(state)):
            box = self._blend_centered(frame, chip, x, y)
            if i == state.take_shown and state.ops.closing:
                g = RING.closing_box
                cv2.rectangle(frame, (box[0] - g, box[1] - g), (box[2] + g, box[3] + g), bgr(C.yellow), g, cv2.LINE_AA)

    def _draw_ring(self, frame: np.ndarray, state: ViewState, box: tuple[float, float, float, float]) -> None:
        """Kat's bubble map: a short, slightly curved spoke from the word's
        edge to each node's edge, the nodes in square outlined boxes. The
        picked node is always orange, word and all, on its way to 12 o'clock
        and there, so the ring shows what is picked however fast it turns."""
        labels = self.ring_labels(state)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        held = None
        for i, (label, (nx, ny)) in enumerate(zip(labels, self.ring_nodes(state))):
            outline = C.node_outline
            if i == state.ops.picked:
                chip = self._node(label, C.chip_text, C.chip_fill, None)
            else:
                chip = self._node(label, C.node_text, C.node_fill, outline)
            h, w = chip[0].shape[:2]
            a = _box_edge((cx, cy), (box[2] - box[0]) / 2 + RING.spoke_gap, (box[3] - box[1]) / 2 + RING.spoke_gap, (nx, ny))
            b = _box_edge((nx, ny), w / 2 + RING.spoke_gap, h / 2 + RING.spoke_gap, (cx, cy))
            ctrl = ((a[0] + b[0]) / 2 - (b[1] - a[1]) * RING.bow, (a[1] + b[1]) / 2 + (b[0] - a[0]) * RING.bow)
            ts = np.linspace(0, 1, RING.curve_points)[:, None]
            curve = (1 - ts) ** 2 * np.array(a) + 2 * (1 - ts) * ts * np.array(ctrl) + ts ** 2 * np.array(b)
            cv2.polylines(frame, [curve.astype(np.int32)], False, bgr(C.connector), RING.stroke, cv2.LINE_AA)
            node = self._blend_centered(frame, chip, nx, ny)
            if i == state.ops.picked:
                held = node
        if held is not None and state.ops.closing:  # the pinch will take this: it is held
            g = RING.closing_box
            x0, y0, x1, y1 = (int(v) for v in held)
            cv2.rectangle(frame, (x0 - g, y0 - g), (x1 + g, y1 + g), bgr(C.yellow), g, cv2.LINE_AA)

    def _draw_gauge(self, frame: np.ndarray, tone: float, top: int, bottom: int, closing: bool = False,
                    sentence_bounds: tuple[float, float] | None = None) -> None:
        """Vertical tone dial in the text box's right padding, just right of
        the text: cold (blue, "formal") at the top, warm (orange,
        "conversational") at the bottom, each end labelled. Its compact track
        follows the sentence's height and centre, not the surrounding context."""
        edge = GAUGE.knob_r + GAUGE.closing_knob_outline  # the knob stays inside the text column
        x = int(min(self.x + self.margin + self.box_w - self.pad // 2, self.col_x1 - edge))
        size = round(self.ui_size * GAUGE.label_scale)
        a, b = sentence_bounds or (top, bottom)
        label_room = sum(self._get_font(size).getmetrics()) + GAUGE.knob_r + self.pad
        low, high = top + label_room, bottom - label_room
        height = min(max(b - a + GAUGE.sentence_pad * self.line_h, GAUGE.min_lines * self.line_h),
                     GAUGE.max_lines * self.line_h, max(1, high - low))
        center = max(low + height / 2, min((a + b) / 2, high - height / 2))
        y0, y1 = round(center - height / 2), round(center + height / 2)
        for text, color, above in ((GAUGE.labels[0], C.cold, True), (GAUGE.labels[1], C.warm, False)):
            ink = self._ink(text, size, (*color, 255))
            h, w = (n - 2 * ink[2] for n in ink[0].shape[:2])
            self._blend_ink(frame, ink, min(x + GAUGE.knob_r, self.col_x1 - ink[2]) - w,
                            y0 - h - GAUGE.knob_r // 2 if above else y1 + GAUGE.knob_r // 2)
        for y in range(y0, y1, GAUGE.step_px):
            k = (y - y0) / max(1, y1 - y0)
            color = tuple(int(c * (1 - k) + w * k) for c, w in zip(bgr(C.cold), bgr(C.warm)))
            cv2.line(frame, (x, y), (x, y + 1), color, GAUGE.width)
        ky = int(y0 + (tone + 1) / 2 * (y1 - y0))
        cv2.circle(frame, (x, ky), GAUGE.knob_r, bgr(C.knob_fill), -1, cv2.LINE_AA)
        cv2.circle(frame, (x, ky), GAUGE.knob_r, bgr(C.knob_outline),
                   GAUGE.closing_knob_outline if closing else GAUGE.knob_outline, cv2.LINE_AA)

    def _draw_zone(self, frame: np.ndarray, state: ViewState) -> None:
        """Command zone: corner marks, recording clock and mic level, hints, flick and hold progress."""
        zx0, zy0, zx1, zy1 = REHEARSE.zone
        x0, y0 = int(zx0 * self.frame_w), int(zy0 * self.frame_h)
        x1, y1 = int(zx1 * self.frame_w) - ZONE.inset_right, int(zy1 * self.frame_h)
        active = state.zone_active
        _corners(frame, (x0, y0 + ZONE.inset_top, x1, y1), int((x1 - x0) * ZONE.corner), C.yellow if active else C.zone,
                 ZONE.active_stroke if active else ZONE.stroke, 1.0 if active else ZONE.alpha)

        size = round(self.ui_size * ZONE.hint_scale)
        cx, y = (x0 + x1) / 2, y0 + self.pad
        if state.app == "rehearse":
            m, s = divmod(int(state.rec_s), 60)
            chip = self._chip(f"REC {m}:{s:02d}", round(self.ui_size * ZONE.rec_scale), C.node_text, C.dark_fill)
            bx0, by0, _, by1 = self._blend_centered(frame, chip, cx + ZONE.rec_dx, y + chip[0].shape[0] / 2)
            # The dot swells with the microphone level: a flat dot means no sound is arriving.
            r0, grow = ZONE.mic_r
            cv2.circle(frame, (bx0 - ZONE.mic_dx, (by0 + by1) // 2), r0 + round(grow * state.mic), bgr(C.rec), -1,
                       cv2.LINE_AA)
            y = by1 + self.pad // 2
            hints = ("HOLD OPEN PALM: STOP",) if state.drill is not None else \
                ("FLICK SIDEWAYS: NEXT SECTION", "HOLD OPEN PALM: STOP")
        elif state.app == "count_in":
            hints = ("HOLD OPEN PALM: CANCEL",)
        else:
            hints = ("HOLD OPEN PALM: BACK TO PREPARE",)
        for text in hints:  # one plain line each
            ink = self._ink(text, size, C.node_text if active else C.dim)
            y = self._blend_centered(frame, ink, cx, y + ink[0].shape[0] / 2 - ink[2])[3] + ZONE.hint_gap

        if state.app == "rehearse" and (active or state.flick_progress > 0):
            # Flick meter, while a hand is in the zone: fills as the hand swings sideways, so a near miss shows.
            my, half = y1 - ZONE.flick_dy, (x1 - x0) // 2 - ZONE.flick_inset
            cv2.line(frame, (int(cx - half), my), (int(cx + half), my), bgr(C.zone), ZONE.flick_stroke, cv2.LINE_AA)
            if state.flick_progress > 0:
                reach = int(half * state.flick_progress)
                cv2.line(frame, (int(cx - reach), my), (int(cx + reach), my), bgr(C.yellow), ZONE.flick_fill,
                         cv2.LINE_AA)
        if state.hold_progress > 0:
            inset = ZONE.hold_inset
            width = int((x1 - x0 - 2 * inset) * state.hold_progress)
            cv2.rectangle(frame, (x0 + inset, y1 - ZONE.hold_top), (x0 + inset + width, y1 - ZONE.hold_bottom),
                          bgr(C.yellow), -1, cv2.LINE_AA)

    def _scrim_level(self, frame: np.ndarray) -> float:
        """How dark the scrim should be for this frame: the brightness of the
        camera image under the text column (before anything is drawn on it)
        mapped onto SCRIM.alpha_min..alpha_max."""
        step = SCRIM.sample_step
        patch = frame[::step, self.col_x0 : self.col_x1 : step].astype(np.float32)
        luma = patch @ np.array([0.114, 0.587, 0.299], np.float32)  # BGR
        level = float(np.percentile(luma, SCRIM.percentile)) / 255.0 if luma.size else 0.0
        k = min(max((level - SCRIM.dark) / max(1e-6, SCRIM.bright - SCRIM.dark), 0.0), 1.0)
        return SCRIM.alpha_min + (SCRIM.alpha_max - SCRIM.alpha_min) * k

    def _draw_scrim(self, frame: np.ndarray) -> None:
        """Kat's dark left side: the video darkened from the frame's left
        edge, fading out by the text column's right edge, as dark as the room
        is bright (sampled every few frames, eased)."""
        if SCRIM.alpha_max <= 0:
            return
        if self._scrim is None:
            x = np.arange(self.col_x1, dtype=np.float32)
            full = SCRIM.full * self.frame_w
            ramp = np.clip((self.col_x1 - x) / max(1.0, self.col_x1 - full), 0.0, 1.0)
            self._scrim = ramp * ramp * (3 - 2 * ramp)  # smoothstep: no visible edge
        if self._scrim_frames % SCRIM.sample_every == 0:
            target = self._scrim_level(frame)
            prev = self._scrim_alpha
            self._scrim_alpha = target if prev is None else prev + SCRIM.smooth * (target - prev)
        self._scrim_frames += 1
        region = frame[:, : self.col_x1]
        region[:] = (region * (1.0 - self._scrim_alpha * self._scrim)[None, :, None]).astype(np.uint8)

    def draw(self, frame: np.ndarray, state: ViewState) -> np.ndarray:
        """Composite the overlay onto `frame` in place and return it."""
        self._draw_scrim(frame)
        if state.mode == "focus" and state.ops.stretch_ends is not None:  # under the text, faint
            a, b = (tuple(int(v) for v in p) for p in state.ops.stretch_ends)
            _line_blend(frame, a, b, C.stretch_line,
                        HANDS.closing_stretch_stroke if state.ops.closing else HANDS.stretch_stroke, HANDS.stretch_alpha)
        box_top = self.y + self.margin
        top, bottom = box_top, box_top + self.box_h
        panel, zoom = self.panel(state), None
        if panel is not None:
            view_h = self.panel_view_h(panel)
            scroll = int(self.clamp_panel_scroll(state, state.panel_scroll))
            top = self._panel_top(view_h)
            bottom = top + view_h
            color, inv = panel.color[scroll:scroll + view_h], panel.inv[scroll:scroll + view_h]
            if panel.height > view_h:
                color, inv = self._faded(color, inv)
            _blend(frame, self.x + self.margin, top, color, inv)
            if panel.height > view_h:
                self._draw_scrollbar(frame, top, view_h, scroll, panel.height, panel.color.shape[1])
        elif (zoom := self.focus_word_box(state)) is not None:
            color, inv, _, _ = self._word_zoom(state)
            _blend(frame, 0, box_top, color, inv)
        else:
            _blend(frame, self.x + self.margin, box_top, *self._band_crop(state))

        # Word chips are drawn over the cached band, so hovering never re-renders text.
        if state.mode == "browse" and state.level == "word" and state.hover and state.hover.word is not None:
            if (box := self.word_box(state.hover, state.scroll)) is not None:
                chip = self._chip(self.word_text(state.hover), round(self.font_size * CHIPS.hover_scale),
                                  C.chip_text, C.chip_fill)
                x0, y0, x1, y1 = self._blend_centered(frame, chip, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                if state.ops.closing:  # pinching: this word is held, the pinch will focus it
                    g = RING.closing_box
                    cv2.rectangle(frame, (x0 - g, y0 - g), (x1 + g, y1 + g), bgr(C.yellow), g, cv2.LINE_AA)
        if panel is None and zoom is not None:
            if state.ops.kind == "ring":
                self._draw_ring(frame, state, zoom)
            elif state.app == "prepare" and state.meaning:
                self._draw_meaning(frame, state.meaning, zoom[3])
            text = self.focus_word(state)
            if state.loading:  # glyph scramble: the word is being rewritten
                text = "".join(SCRAMBLE[(ord(c) + int(time.time() * 12)) % len(SCRAMBLE)] if c.isalpha() else c
                               for c in text)
            # Inline, in the gap the zoomed text left for it: orange, at the zoomed size.
            self._blend_ink(frame, self._ink(text, self._word_zoom(state)[3], C.orange_text), zoom[0], zoom[1])

        if state.mode == "focus" and state.ops.kind == "tone":
            bounds = None
            if panel is not None and state.focus is not None and state.focus.sentence in panel.rows:
                bounds = tuple(top - scroll + y for y in panel.rows[state.focus.sentence])
            self._draw_gauge(frame, state.ops.tone, top, bottom, state.ops.closing, bounds)
        if state.app in ("count_in", "rehearse", "review"):
            self._draw_zone(frame, state)
        if state.app == "review" and state.mode == "focus" and state.takes:
            self._draw_takes(frame, state)
        if state.app == "count_in" and state.count_in > 0 and state.calibration is None:
            # In the hand zone, below the command zone: the hand is down during the count-in.
            ink = self._ink(str(state.count_in), round(self.ui_size * COUNT_IN.scale), C.orange)
            self._blend_centered(frame, ink, sum(LAYOUT.hand) / 2 * self.frame_w, self.frame_h * COUNT_IN.y)
        self._draw_label(frame, state, top)
        low = self._draw_pills(frame, state)
        if state.alert:
            self._draw_alert(frame, state.alert, bottom, low)
        # Bottom right: the take table (Review, browsing), the first-run tutorial.
        if state.app == "review" and state.summary and state.mode != "focus":
            self._draw_summary(frame, state.summary)
        if state.tutorial is not None:
            self._draw_tutorial(frame, *state.tutorial)
        return frame

    def _draw_meaning(self, frame: np.ndarray, meaning: str, word_bottom: float) -> None:
        size = self.ui_size
        x = self.x + self.margin
        pad = self.pad // 2
        width = int(self.col_x1 - x)
        y = round(word_bottom + pad)
        available = self.y + self.margin + self.box_h - y
        while True:
            rows = ["MEANING", *self._wrap(meaning, size, width - 2 * pad)]
            line_h = sum(self._get_font(size).getmetrics()) + 2
            height = len(rows) * line_h + 2 * pad
            if height <= available or size <= TEXT.meaning_min_size:
                break
            size -= 1
        key = ("meaning", meaning, width, size)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            img = Image.new("RGBA", (width, height), C.node_fill)
            draw = ImageDraw.Draw(img)
            for i, row in enumerate(rows):
                draw.text((pad, pad + i * line_h), row, font=self._get_font(size),
                          fill=C.orange if i == 0 else C.detail_text)
            self._chips[key] = _premultiply(img)
        _blend(frame, x, y, *self._chips[key])

    def _draw_alert(self, frame: np.ndarray, alert: str, bottom: int, low: int) -> int:
        """The persistent alert line under the text, wrapped to the text
        column, above `low`; returns the y under it."""
        size = round(self.ui_size * LABEL.second_scale)
        pad_x = 2 * max(CHIPS.pad_x[0], size // CHIPS.pad_x[1]) + 2
        chips = [self._chip(row, size, C.alert_text, C.alert_fill)
                 for row in self._wrap(alert, size, self.col_x1 - self.x - pad_x)]
        y = min(bottom + self.pad // 2, low - sum(c[0].shape[0] for c in chips))
        for chip in chips:
            _blend(frame, self.x + self.margin, y, *chip)
            y += chip[0].shape[0]
        return y

    def _draw_pills(self, frame: np.ndarray, state: ViewState) -> int:
        """Bottom left: the CLOUD LLM chip whenever the text asked about can
        leave the Mac, then the keys (or the keys help). Returns their top."""
        x, low = self.x + self.margin, self.frame_h - LABEL.min_top
        size = round(self.ui_size * LABEL.pill_scale)
        top = low
        if state.llm == "cloud":
            text, fg = ("CLOUD LLM: SENDING", C.orange) if state.llm_busy else ("CLOUD LLM", C.dim)
            chip = self._chip(text, size, fg, C.label_fill)
            _blend(frame, x, low - chip[0].shape[0], *chip)
            top = low - chip[0].shape[0]
            if state.keys_help:
                low -= chip[0].shape[0]
            else:
                x += chip[0].shape[1] + self.pad // 2
        if state.keys_help:
            top = self._draw_keys(frame, low)
        elif state.app in ("prepare", "count_in", "rehearse", "review"):
            chip = self._chip("H: KEYS", size, C.dim, C.label_fill)
            _blend(frame, x, low - chip[0].shape[0], *chip)
            top = min(top, low - chip[0].shape[0])
        return top

    def _draw_summary(self, frame: np.ndarray, lines: tuple[str, ...]) -> None:
        """The take table at the bottom right: a chip per line (all the same
        width, monospace, so the columns line up), the first (the takes) in orange."""
        size = round(self.ui_size * SUMMARY.scale)
        chips = [self._chip(t, size, C.orange if i == 0 else C.node_text, C.dark_fill) for i, t in enumerate(lines)]
        y = self.frame_h - SUMMARY.bottom - sum(c[0].shape[0] + SUMMARY.gap for c in chips)
        for color, inv in chips:
            h, w = color.shape[:2]
            _blend(frame, self.frame_w - SUMMARY.right - w, y, color, inv)
            y += h + SUMMARY.gap

    def _draw_tutorial(self, frame: np.ndarray, step: int, of: int, text: str) -> None:
        """One gesture at a time, at the bottom right under the hand box (it
        teaches the hand), right-aligned, with where it is in the steps."""
        small, big = self._label_size(2), round(self.ui_size * LABEL.first_scale * 0.8)
        dots = " ".join("●" if k < step else "○" for k in range(of))
        right = self.frame_w - SUMMARY.right
        width = right - LAYOUT.hand[0] * self.frame_w
        rows = [(row, small, C.node_text) for row in self._wrap(f"{dots}   ENTER: SKIP  /  G: HIDE", small, width)] + \
            [(row, big, C.orange) for row in self._wrap(text, big, width)]
        heights = [int(sum(self._get_font(size).getmetrics()) * 1.15) for _, size, _ in rows]
        y = self.frame_h - SUMMARY.bottom - sum(heights)
        for (row, size, color), h in zip(rows, heights):
            ink = self._ink(row, size, color)
            self._blend_ink(frame, ink, right - (ink[0].shape[1] - 2 * ink[2]), y)
            y += h

    def _draw_keys(self, frame: np.ndarray, low: int) -> int:
        """The keyboard fallback, bottom left, ending at `low`, as large as fits
        the text column. Returns its top."""
        size = round(self.ui_size * LABEL.pill_scale)
        widest = max(KEYS_HELP, key=len)
        while size > 8 and self._chip(widest, size, C.node_text, C.dark_fill)[0].shape[1] > self.col_x1 - self.x:
            size -= 1
        chips = [self._chip(line, size, C.node_text, C.dark_fill) for line in KEYS_HELP]
        y = top = low - sum(c[0].shape[0] for c in chips)
        for chip in chips:
            _blend(frame, self.x + self.margin, y, *chip)
            y += chip[0].shape[0]
        return top

    # --- drawing: the take player ------------------------------------------

    def draw_caption(self, frame: np.ndarray, words: list[tuple[str, str, bool]]) -> None:
        """A row of word chips under the text, left to right, until the frame's
        edge: (text, kind, being said now), kind as in PLAYER.caption."""
        size = round(self.ui_size * PLAYER.caption_scale)
        x, y = self.x + self.margin, int(self.frame_h * PLAYER.caption_y)
        for text, kind, current in words:
            chip = self._chip(text, size, PLAYER.caption[kind], PLAYER.caption_current if current else None)
            w = chip[0].shape[1]
            if x + w > self.frame_w - PLAYER.caption_right:
                break
            self._blend_centered(frame, chip, x + w / 2, y)
            x += w + PLAYER.caption_gap


def draw_strip(frame: np.ndarray, spans: list[tuple[float, float, str]], sections: list[float],
               fillers: list[float], restarts: list[float], t: float, duration: float) -> None:
    """The take player's timeline along the bottom: sentence spans coloured by
    status, section changes, filler and restart ticks, the playhead. Times
    are seconds into the take."""
    P = PLAYER
    h, w = frame.shape[:2]
    x0, x1 = P.strip_x, w - P.strip_x
    y0, y1 = h - P.strip_top, h - P.strip_bottom

    def x_at(tt: float) -> int:
        return int(x0 + (x1 - x0) * min(max(tt / max(duration, 1e-6), 0.0), 1.0))

    cv2.rectangle(frame, (x0, y0), (x1, y1), bgr(P.strip_frame), 1)
    for start, end, status in spans:
        cv2.rectangle(frame, (x_at(start), y0 + P.span_inset), (max(x_at(end), x_at(start) + 1), y1 - P.span_inset),
                      bgr(P.status.get(status, P.status_other)), -1)
    above, below = P.section_over
    for tt in sections:
        cv2.line(frame, (x_at(tt), y0 - above), (x_at(tt), y1 + below), bgr(P.section), 1)
    for times, color in ((fillers, P.filler), (restarts, P.restart)):
        for tt in times:
            cv2.line(frame, (x_at(tt), y1), (x_at(tt), y1 + P.tick_h), bgr(color), P.tick_stroke)
    cv2.line(frame, (x_at(t), y0 - P.playhead_over), (x_at(t), y1 + P.playhead_over), bgr(P.playhead),
             P.playhead_stroke)


# --- drawing: hands, debug and stats (OpenCV, straight onto the frame) ------

def draw_fingertips(frame: np.ndarray, state: GestureState) -> None:
    """Yellow dot on the active fingertip, small dots on the rest; cyan for a second hand."""
    for track, color in ((state.secondary, C.cyan), (state.primary, C.yellow)):
        if track is None or track.hand is None:
            continue
        for i in TIPS:
            c = tuple(int(v) for v in track.hand.points[i])
            cv2.circle(frame, c, HANDS.active_tip_r if i == INDEX_TIP else HANDS.tip_r, bgr(color), -1, cv2.LINE_AA)


def draw_landmarks(frame: np.ndarray, hand: Hand) -> None:
    """Debug: the hand's skeleton."""
    pts = hand.points.astype(int)
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, tuple(pts[a]), tuple(pts[b]), bgr(C.landmark_line), HANDS.landmark_stroke, cv2.LINE_AA)
    for p in pts:
        cv2.circle(frame, tuple(p), HANDS.landmark_r, bgr(C.landmark_point), -1, cv2.LINE_AA)


def draw_hand_box(frame: np.ndarray, cursor: RelativeCursor) -> None:
    """Debug: the hand box, its scroll bands, and where the cursor sits in it."""
    x0, y0, x1, y1 = (int(v) for v in cursor.box)
    band = int((y1 - y0) * CURSOR.edge_band)
    cv2.rectangle(frame, (x0, y0), (x1, y1), bgr(C.debug_box), HANDS.box_stroke, cv2.LINE_AA)
    for y in (y0 + band, y1 - band):
        cv2.line(frame, (x0, y), (x1, y), bgr(C.debug_band), HANDS.box_stroke, cv2.LINE_AA)
    if cursor.uv is not None:
        u, v = cursor.uv
        cv2.circle(frame, (int(x0 + u * (x1 - x0)), int(y0 + v * (y1 - y0))), HANDS.cursor_r, bgr(C.debug_box),
                   HANDS.box_stroke, cv2.LINE_AA)


def draw_hand_area(frame: np.ndarray, cursor: RelativeCursor) -> None:
    """Where the hand steers the highlight: the hand box's corners, small and faint."""
    x0, y0, x1, y1 = (int(v) for v in cursor.box)
    arm = max(HANDS.area_min_arm, int((x1 - x0) * HANDS.area_arm))
    _corners(frame, (x0, y0, x1, y1), arm, C.hand_area, HANDS.area_stroke, HANDS.area_alpha)


def draw_zone_outline(frame: np.ndarray, zone: CommandZone) -> None:
    """Debug: the command zone as the gesture code sees it."""
    x0, y0, x1, y1 = (int(v) for v in zone.box)
    cv2.rectangle(frame, (x0, y0), (x1 - 1, y1), bgr(C.yellow if zone.active else C.debug_box), HANDS.box_stroke,
                  cv2.LINE_AA)


def draw_stats(frame: np.ndarray, text: str) -> None:
    """Frame rate and latency, bottom right; only while DEBUG.show_stats."""
    if not DEBUG.show_stats:
        return
    h, w = frame.shape[:2]
    cv2.putText(frame, text, (w - DEBUG.stats_from_right, h - DEBUG.stats_from_bottom), cv2.FONT_HERSHEY_SIMPLEX,
                DEBUG.stats_scale, bgr(C.stats), DEBUG.stats_thickness, cv2.LINE_AA)


def draw_debug_text(frame: np.ndarray, lines: list[str]) -> None:
    """Lines of readout from the top left (python -m palmcards.gestures)."""
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (DEBUG.text_x, DEBUG.text_y + DEBUG.text_dy * i), cv2.FONT_HERSHEY_SIMPLEX,
                    DEBUG.text_scale, bgr(C.debug_text), DEBUG.text_thickness, cv2.LINE_AA)


def _bar(progress: float, width: int = 10) -> str:
    filled = round(progress * width)
    return "[" + "=" * filled + " " * (width - filled) + "]"


def _outline(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font) -> None:
    """The dark outline of `text`, on its own layer under the text (so a
    neighbouring word's outline never covers a glyph): OUTLINE.width px of
    Pillow's stroke in Colors.outline."""
    if OUTLINE.width > 0:
        draw.text(xy, text, font=font, fill=C.outline, stroke_width=OUTLINE.width, stroke_fill=C.outline)


def _halo(img: Image.Image, radius: float, under: Image.Image | None = None) -> Image.Image:
    """`img` (text on a clear RGBA image) over its outline (`under`, from
    _outline) over a soft dark halo: the text's alpha blurred and
    strengthened (SHADOW.gain), in Colors.shadow. Fainter text casts a
    fainter halo."""
    shade = C.shadow
    alpha = img.getchannel("A").point(lambda v: int(255 * (v / 255) ** SHADOW.gamma))
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius))
    alpha = alpha.point(lambda v: min(255, int(v * SHADOW.gain)) * shade[3] // 255)
    halo = Image.new("RGBA", img.size, (*shade[:3], 0))
    halo.putalpha(alpha)
    if under is not None:
        halo = Image.alpha_composite(halo, under)
    return Image.alpha_composite(halo, img)


def _box_edge(center: tuple[float, float], half_w: float, half_h: float,
              toward: tuple[float, float]) -> tuple[float, float]:
    """Where the line from a box's centre toward a point leaves the box."""
    dx, dy = toward[0] - center[0], toward[1] - center[1]
    if dx == 0 and dy == 0:
        return center
    t = min(half_w / abs(dx) if dx else math.inf, half_h / abs(dy) if dy else math.inf)
    return center[0] + dx * min(t, 1.0), center[1] + dy * min(t, 1.0)


def _line_blend(frame: np.ndarray, a: tuple[int, int], b: tuple[int, int], color, stroke: int, alpha: float) -> None:
    """A line blended at `alpha`; only the patch around it is touched."""
    fh, fw = frame.shape[:2]
    m = stroke + 2
    x0, x1 = max(0, min(a[0], b[0]) - m), min(fw, max(a[0], b[0]) + m + 1)
    y0, y1 = max(0, min(a[1], b[1]) - m), min(fh, max(a[1], b[1]) + m + 1)
    if x1 <= x0 or y1 <= y0:
        return
    roi = frame[y0:y1, x0:x1]
    over = roi.copy()
    cv2.line(over, (a[0] - x0, a[1] - y0), (b[0] - x0, b[1] - y0), bgr(color), stroke, cv2.LINE_AA)
    roi[:] = over if alpha >= 1 else cv2.addWeighted(over, alpha, roi, 1 - alpha, 0)


def _corners(frame: np.ndarray, box: tuple[int, int, int, int], arm: int, color, stroke: int, alpha: float) -> None:
    """A box's four corner marks, blended at `alpha`."""
    x0, y0, x1, y1 = box
    for (x, y), (dx, dy) in (((x0, y0), (1, 1)), ((x1, y0), (-1, 1)), ((x0, y1), (1, -1)), ((x1, y1), (-1, -1))):
        _line_blend(frame, (x, y), (x + dx * arm, y), color, stroke, alpha)
        _line_blend(frame, (x, y), (x, y + dy * arm), color, stroke, alpha)


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
