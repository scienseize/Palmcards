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

import functools
import math
import random
import re
import textwrap
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from palmcards.config import CURSOR, KNOB, OPS
from palmcards.gestures import FINGER_TIPS, HAND_CONNECTIONS, INDEX_TIP, THUMB_TIP, TIPS
from palmcards.motion import Spring, rubberband
from palmcards.notes import Sentence, parse_sentence
from palmcards.preview import PreviewView
from palmcards.style import (
    BAR, CHIPS, COLORS, COUNT_IN, SMALL_ON_FILL, SUMMARY, DEBUG, DETAIL, FILL, GAUGE, HANDS, HIGH_CONTRAST, LABEL, LAYOUT,
    MOTION, OUTLINE, PLAYBAR, PLAYER, REC, RING, SCRIM, SHADOW, TEXT, TYPE, TypeStep, bgr,
)

if TYPE_CHECKING:
    from palmcards.gestures import GestureState, Hand, RelativeCursor

C = COLORS
CLEAR = (0, 0, 0, 0)


def set_contrast(high: bool) -> None:
    """High-contrast colours (preferences); build a new TextOverlay afterwards."""
    global C
    C = HIGH_CONTRAST if high else COLORS


REDUCED = False  # reduced motion (preferences, or macOS's setting): nothing moves or scales, things fade


def set_reduced_motion(on: bool) -> None:
    """Reduced motion: springs that move or scale things arrive at once, the
    focus and the ring fade in place (MOTION.fade), no glyph scramble, no
    rubber band; progress, dots and fades stay."""
    global REDUCED
    REDUCED = bool(on)


def _r(response: float) -> float:
    """A moving spring's response, or 0 (at once) with reduced motion."""
    return 0.0 if REDUCED else response
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
GROWN = 0.99  # a focus this far grown is drawn as focused (the last step is under 2 px)
REVIEW_SEP = " · "  # between Review's gesture hints (narrower than HINT_SEP: they share a row)
DONE_HINT = "THUMB UP: PREPARE"  # Review: a thumbs-up held goes back to Prepare
FIST_HINT = (f"RAISE A FIST: NEW TAKE · {DONE_HINT}", f"FIST: NEW TAKE · {DONE_HINT}")


def review_hint(state: "ViewState") -> tuple[str, str]:
    """Review's gesture hints for the label: what acts now, (long, short).
    The short form is used when the long one doesn't fit a row of the text
    column (TextOverlay.label_rows)."""
    if state.playing:  # an open palm stops it on a focus; a key while browsing
        return ("OPEN PALM: STOP", "PALM: STOP") if state.mode == "focus" else ("A: STOP", "A: STOP")
    if state.mode == "browse" and state.level == "sentence":
        return (f"FOLD: DETAILS · FIST: NEW TAKE · {DONE_HINT}",) * 2
    if state.mode == "browse" and state.level == "paragraph":
        return (f"FOLD: SUMMARY · FIST: NEW TAKE · {DONE_HINT}",) * 2
    if state.mode == "focus" and state.level == "paragraph":
        if not state.playable:
            return "DROP HAND: BACK", "DROP: BACK"
        return "OPEN PALM: PLAY PARAGRAPH · DROP HAND: BACK", "PALM: PLAY PARAGRAPH · DROP: BACK"
    if state.mode == "focus":
        long, short = [], []
        if state.playable:
            long.append("OPEN PALM: PLAY")
            short.append("PALM: PLAY")
        if len(state.takes) > 1:
            n, m = state.take_shown + 1, len(state.takes)
            long.append(f"L, POINT: TAKE {n} OF {m}")
            short.append(f"L: {n}/{m}")
        long.append("PINCH + LIFT: DRILL")
        short.append("PINCH+LIFT: DRILL")
        return REVIEW_SEP.join(long), REVIEW_SEP.join(short)
    return FIST_HINT
KEYS_HELP = (
    "KEYS (WHEN GESTURES WON'T DO)",
    "T  START A TAKE      X  STOP (AUDIO, TAKE)",
    "N B  NEXT / PREVIOUS SECTION     U  UNDO EDIT",
    "J K  NEXT / PREVIOUS SENTENCE, OR SCROLL",
    "A  PLAY / STOP       P  BACK TO PREPARE",
    "E  CALIBRATE EYES AT THE NEXT TAKE",
    "M  FLIP A REPLAYED VIDEO (MIRROR OR NOT)",
    "REPLAY BAR: YELLOW FILLER, RED RESTART,",
    "  WHITE LONG PAUSE, BLUE LOOKED AWAY",
    "R  RETRY ANALYSIS    H  HIDE    Q  QUIT",
)


@functools.cache
def load_font(size: int, weight: str = "medium") -> ImageFont.FreeTypeFont:
    """The notes' font at this size and weight (TEXT.fonts), or "fallback" (TEXT.fallback)."""
    path = TEXT.fallback if weight == "fallback" else TEXT.fonts[weight]
    if not path.exists():
        raise FileNotFoundError(f"font missing: {path} (it ships in the repo; restore it with git)")
    return ImageFont.truetype(path, size)


@functools.cache
def _covered(path: str) -> frozenset[int]:
    """The code points a font file has glyphs for."""
    from fontTools.ttLib import TTFont
    return frozenset(TTFont(path, lazy=True).getBestCmap())


@functools.cache
def _cell(font: ImageFont.FreeTypeFont, lh: int) -> tuple[int, int]:
    """Where glyphs sit in a row `lh` px tall, caps and descenders centred
    whatever the font's own ascent: (y from the row's top to draw at, with
    Pillow's default top-of-ascent anchor; px left under the descenders)."""
    cap = -font.getbbox("H", anchor="ls")[1]
    desc = font.getbbox("g", anchor="ls")[3]
    base = round((lh + cap - desc) / 2)
    return base - font.getmetrics()[0], lh - base - desc


@dataclass(frozen=True)
class Face:
    """Text set in a step of the type scale (style.TYPE) at one size: the
    font, the tracking (px between letters), a row's height and where the
    glyphs sit in a row."""
    font: ImageFont.FreeTypeFont
    tracking: float
    line_h: int
    dy: int  # from a row's top to draw at (Pillow's top-of-ascent anchor): caps centred in the row

    @property
    def size(self) -> int:
        return self.font.size

    @property
    def advance(self) -> float:
        """A character's cell: the text is monospace."""
        return self.font.getlength("M") + self.tracking


@functools.cache
def face(size: int, weight: str, tracking: float, leading: float, line_h: int | None = None) -> Face:
    """A face at this size and weight; `tracking` in em, `leading` a row's
    height in em (or `line_h` px, where rows are set by something else)."""
    font = load_font(size, weight)
    lh = line_h if line_h is not None else round(size * leading)
    return Face(font, tracking * size, lh, _cell(font, lh)[0])


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
    """Where the options ring is in its turn (nodes, unwrapped): sprung."""
    return ops.rot.at(now)


def ring_follow(offset: float) -> float:
    """How far the ring turns with the hand past its node (in nodes) for the
    knob turned `offset` steps past its step: not at all near the node, then
    MOTION.ring_gain of the way."""
    if REDUCED:
        return 0.0
    past = max(0.0, abs(offset) - MOTION.ring_flat)
    return math.copysign(past * MOTION.ring_gain, offset)


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
    # (+ clockwise), a spring (MOTION.ring) heading for rot_to, the node the
    # knob is on, plus the part of the way to the next the hand has turned
    # (`offset`, in steps: GestureState.ring_offset). `turn` is the grammar's
    # ring_turn it has followed. `preview` is
    # the word shown in the sentence (the picked word node, else the word
    # itself); it scrambles in from scrambled_t. changed_t: when the pick last changed.
    rot: Spring = field(default_factory=Spring)
    rot_to: float = 0.0
    offset: float = 0.0
    turn: int = 0
    nodes: int = 0
    pick_label: str | None = None
    changed_t: float | None = None
    preview: str | None = None
    scrambled_t: float | None = None
    # The ring opening out of the word (MOTION.ring_open, from follow_ring); None: open.
    opened: Spring | None = None
    tone: float = 0.0  # -1 cold .. 1 warm
    stretch: float = 1.0
    stretch_ends: tuple[tuple[float, float], tuple[float, float]] | None = None
    closing: bool = False  # the thumb is closing into a pinch: the dial is held where it was, drawn bolder
    # A hand drives the dial now (GestureState.dialing), and how far past its
    # ends: tone_over (tone units), stretch_raw (the ratio unclamped). The
    # gauge's knob as shown (follow_dials): 1:1 while driven, sprung back after.
    dialing: bool = False
    tone_over: float = 0.0
    stretch_raw: float = 1.0
    tone_shown: Spring | None = None


@dataclass
class FocusFrame:
    """What a focus drew (as focused, before any growing), kept to shrink it
    back into the notes on the way out."""
    key: tuple
    layers: list  # (x, y, colour, inverse alpha, grows) patches; `grows`: the unit's own, else context fading in place
    target: tuple[float, float, float, float]  # the unit (or word) on screen, focused
    scale: float  # its text's size over the notes'
    ring: tuple | None = None  # the options ring: (labels, picked, nodes, word box)


@dataclass
class FocusMotion:
    """The focus growing out of its unit's place in the notes and shrinking
    back into it (TextOverlay.follow_focus): a spring from 0 (in the notes) to
    1 (focused), and the unit's place in the notes."""
    spring: Spring = field(default_factory=Spring)
    key: tuple | None = None
    home: tuple[float, float, float, float] | None = None
    last: FocusFrame | None = None


@dataclass(frozen=True)
class Grow:
    """A focus part of the way from its unit's place in the notes (p 0) to
    focused (p 1): drawn `s` times its focused size, the unit's top left at
    (ax, ay) instead of (tx, ty)."""
    p: float
    s: float
    ax: float
    ay: float
    tx: float
    ty: float

    def at(self, x: float, y: float) -> tuple[float, float]:
        """Where a point of the focused drawing is now."""
        return self.ax + (x - self.tx) * self.s, self.ay + (y - self.ty) * self.s


def grow(home: tuple[float, float, float, float], target: tuple[float, float, float, float], scale: float,
         p: float) -> Grow:
    """The focus at p of the way from `home` (the unit in the notes) to
    `target` (focused, its text `scale` times the notes')."""
    if REDUCED:  # fades in place: nothing moves or scales
        return Grow(p, 1.0, target[0], target[1], target[0], target[1])
    k = 1.0 / scale
    return Grow(p, k + (1 - k) * p, home[0] + (target[0] - home[0]) * p, home[1] + (target[1] - home[1]) * p,
                target[0], target[1])


@dataclass
class PanelMotion:
    """The focus panel's scroll as shown: a spring (MOTION.scroll) heading for
    ViewState.panel_scroll, the unit it belongs to (TextOverlay.follow_panel_scroll)."""
    spring: Spring = field(default_factory=Spring)
    unit: tuple[int, ...] | None = None


@dataclass
class ViewState:
    current: int = 0  # sentence drawn in orange when no hand is up
    now: float = 0.0  # app clock, s: the options ring's animations
    mode: str = "idle"  # idle | browse | focus
    level: str | None = None  # word | sentence | paragraph
    hover: Hit | None = None
    focus: Hit | None = None
    scroll: float = 0.0  # in rows
    # Browsing pushed past the first or last row: how far (rows, the app's),
    # and the notes' give as drawn (follow_bounce). None: no give.
    scroll_push: float = 0.0
    scroll_bounce: Spring | None = None
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
    recording_video: bool = False  # the take's video is being recorded too (the REC chip says so)
    mic: float = 0.0  # microphone level, 0..1
    hold_progress: float = 0.0  # a thumbs-up held toward "done" (stop, cancel, back to Prepare), 0..1
    drill: int | None = None  # count_in, rehearse: the one sentence a drill rehearses
    detail: tuple[str, ...] = ()  # Review focus: the lines under the sentence (a take each)
    summary: tuple[str, ...] = ()  # Review, browsing: the take table (palmcards.review), a line each
    panel_scroll: float = 0.0  # px into the focus panel's content (clamped when drawn)
    # The app's: the panel scrolls to panel_scroll sprung (follow_panel_scroll). None: at once.
    panel_motion: PanelMotion | None = None
    # The app's: the focus grows out of its unit and shrinks back (follow_focus). None: at once.
    focus_motion: FocusMotion | None = None
    lift: float = 0.0  # pinch + lift: how far the pinched hand has risen toward a commit, 0..1
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
    edit_preview: PreviewView | None = None  # immutable snapshot of what this frame presents
    llm: str = ""  # the optional LLM in use: "cloud" (the text asked about leaves the Mac), "local", or ""
    llm_busy: bool = False  # a request is out
    # Review, a focused sentence: the takes that said it, as chips beside it to
    # point at (an L, then the fingertip), and which one it shows.
    takes: tuple[str, ...] = ()
    take_shown: int = 0
    playable: bool = False  # Review: the focused sentence or paragraph has a take to play
    # Playback (a take's clip in Review, "hear it" in Prepare): something plays,
    # how far it has got (0..1), and an open palm held toward stopping it (0..1).
    playing: bool = False
    play_progress: float | None = None
    palm_progress: float = 0.0
    tutorial: tuple[int, int, str] | None = None  # (step, of, what to do) on the first run, or after g


@dataclass
class Panel:
    """The focus panel laid out whole; drawn through a viewport."""
    height: int  # of the content, px
    color: np.ndarray
    inv: np.ndarray
    rows: dict[int, tuple[int, int]]  # sentence -> (top, bottom) of its enlarged rows, px into the content
    header_h: int = 0  # extra room for the edit target, status and gestures
    scale: float = 1.0  # the unit's text size over the notes' (TEXT.focus_scales)


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


def _text_metrics(size: int) -> tuple[int, float]:
    """(line height, character width) of the notes (TYPE.notes) at this size."""
    f = face(size, TYPE.notes.weight, TYPE.notes.tracking, TYPE.notes.leading)
    return f.line_h, f.advance


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
            line_h, char_w = _text_metrics(size)
            if (self.col_x1 - self.col_x0 - 2 * (line_h // 2)) / char_w >= TEXT.min_columns:
                break
            size -= 1
        self.font_size = size
        self.ui_size = max(TEXT.ui_min_size, h // TEXT.ui_rows_per_frame)  # labels, hints, pills, the take table
        self.notes = self._face(TYPE.notes)
        self.font = self.notes.font
        self.bold = self._face(TYPE.notes, "semibold").font  # the current sentence, the unit under the hand
        self.line_h, self.char_w = self.notes.line_h, self.notes.advance
        self.text_dy, self.row_gap = _cell(self.font, self.line_h)  # glyphs in the row; below them
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
            pill = self._face(TYPE.small)
            low = h - LABEL.min_top - (pill.line_h + 2 * max(CHIPS.pad_y[0], pill.size // CHIPS.pad_y[1]) + 2)
            visible_rows = max(3, (low - self.pad // 2 - self.y - 2 * self.pad) // self.line_h)
        self.visible_rows = visible_rows
        self.box_h = visible_rows * self.line_h + 2 * self.pad
        if self.y + self.box_h > h:  # a row count too tall for the frame: centred
            self.y = max(0, (h - self.box_h) // 2)

        self._count: tuple[int, float] | None = None  # the count-in's digit and when it came up
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
        self._drawn_rows: dict[int, int] = {}  # the last frame's panel: sentence -> top of its rows on screen
        self._ring_snap: tuple | None = None  # what the options ring drew this frame (FocusFrame.ring)

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
        return self._label(state)[:2]

    def label_progress(self, state: ViewState) -> float | None:
        """How far a hold the label names has got (0..1), drawn as a bar after
        its operation line; None when it names none."""
        return self._label(state)[2]

    def _label(self, state: ViewState) -> tuple[str, str, float | None]:
        if state.title and state.app not in ("count_in", "rehearse"):
            return state.title, state.note or state.status, None
        if state.app in ("count_in", "rehearse"):
            progress = None
            if state.hold_progress > 0:
                second, progress = f"{'CANCEL' if state.app == 'count_in' else 'STOP'}: HOLD", state.hold_progress
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
            if progress is None:  # the only command in a take: a thumbs-up, anywhere
                second += HINT_SEP + ("THUMB UP: CANCEL" if state.app == "count_in" else "THUMB UP: STOP")
            return state.title or ("DRILL" if state.drill is not None else "REHEARSE"), second, progress

        level = (state.level or "").upper()
        if state.mode == "focus":
            first = f"FOCUS BY {level}"
            if state.level == "word" and state.focus is not None and state.focus.word is not None:
                first += f'  "{self.focus_word(state)}"'
        elif state.mode == "browse":
            first = f"BROWSE BY {level}"
        else:
            first = state.app.upper()
        ops, progress = state.ops, None
        if state.note:
            second = state.note
        elif state.start_progress > 0:
            second, progress = "START A TAKE: HOLD FIST", state.start_progress
        elif state.hold_progress > 0:
            second, progress = "BACK TO PREPARE: HOLD", state.hold_progress
        elif state.palm_progress > 0:
            second, progress = "STOP: HOLD", state.palm_progress
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
        elif state.mode == "focus" and state.app == "prepare" and state.playing:
            second = "OPEN PALM: STOP"
        elif state.mode == "focus" and state.app == "prepare":  # nothing started yet: say what the next shape does
            second = (FOCUS_HINTS_LLM if state.llm else FOCUS_HINTS).get(state.level, "")
        elif state.app == "review":  # what the focus shows, then the gestures that act
            second = f"{state.status}{HINT_SEP}{review_hint(state)[0]}"
        elif state.mode == "focus":
            second = state.status or "DROP HAND: BACK"
        elif state.app == "prepare" and state.mode == "browse" and state.level == "sentence":
            second = "FOLD TO SELECT  /  THEN HOLD OPEN PALM: HEAR IT"
        else:
            second = state.status
        return first, second, progress

    def ring_words(self, state: ViewState) -> int:
        """Every node is the original word or a replacement."""
        return len(self.ring_labels(state)) if state.focus is not None and state.focus.word is not None else 0

    def follow_ring(self, state: ViewState, pick: str | None, turn: int) -> None:
        """The options ring follows the grammar's knob (GestureState.ring_pick,
        ring_turn, ring_offset): the ring turns with the hand between nodes and
        springs onto the node each step lands on, the way the hand went; the
        nodes changing under it (alternatives arriving) reflow it, no turn; a
        new word in the sentence scrambles in."""
        ops, now = state.ops, state.now
        labels = self.ring_labels(state)
        if not labels:
            return
        if ops.opened is None:  # just spread: it opens out of the word
            ops.opened = Spring()
            ops.opened.snap(0.0, now)
            ops.opened.retarget(1.0, now, MOTION.fade if REDUCED else MOTION.ring_open)
        picked = labels.index(pick) if pick in labels else 0
        n = len(labels)
        if n != ops.nodes:
            ops.rot_to, ops.nodes = float(picked), n
            ops.rot.snap(ops.rot_to, now)
        else:
            aim = ops.rot_to + (turn - ops.turn)  # where the knob's steps take it
            ops.rot_to = float(picked + n * round((aim - picked) / n))
            ops.rot.retarget(ops.rot_to + ring_follow(ops.offset), now, _r(MOTION.ring), MOTION.ring_damping)
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
        if not REDUCED and ops.scrambled_t is not None and state.now - ops.scrambled_t < KNOB.scramble_s:
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

    @staticmethod
    def _get_font(size: int, weight: str = "medium") -> ImageFont.FreeTypeFont:
        return load_font(size, weight)

    def _face(self, step: TypeStep, weight: str | None = None, scale: float = 1.0) -> Face:
        """A step of the type scale at this frame's size (x `scale`)."""
        base = self.font_size if step.base == "notes" else self.ui_size
        return face(max(1, round(base * step.scale * scale)), weight or step.weight, step.tracking, step.leading)

    def _fade(self, h: int, top: float = 1.0, bottom: float = 1.0) -> np.ndarray:
        """Alpha over a viewport `h` px tall: where the text meets its top or
        bottom it fades out over TEXT.edge_fade rows (smoothstep, half a row
        of it in the padding) instead of being cut. `top` and `bottom` are
        how much there is beyond each edge, in rows (half a row or more,
        enough to fill the padding: the full fade; 0: none, so the first row
        stays crisp until it scrolls)."""
        f = max(1.0, TEXT.edge_fade * self.line_h)
        y = np.arange(h, dtype=np.float32)
        out = self.pad - self.line_h / 2  # fully faded this far into the padding
        ramps = []
        for k, t in ((top, (y - out) / f), (bottom, (h - out - y) / f)):
            k = min(max(2.0 * k, 0.0), 1.0)
            t = np.clip(t, 0.0, 1.0)
            ramps.append(1.0 - k * (1.0 - t * t * (3 - 2 * t)))
        return np.minimum(*ramps)[:, None, None]

    def _faded(self, color: np.ndarray, inv: np.ndarray, top: float = 1.0,
               bottom: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        k = self._fade(color.shape[0], top, bottom)
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
                   under: ImageDraw.ImageDraw | None = None, tracking: float = 0.0) -> None:
        """A word or punctuation, its top of ascent at xy (`tracking` px
        between letters); its outline on `under` (text on a fill has none)."""
        if under is not None:
            _outline(under, xy, sp.text, font, tracking)
        _text(draw, xy, sp.text, font, tracking, fill=color)

    def _zoom_face(self, weight: str | None = None) -> Face:
        """A focused word's zoomed notes: TYPE.focus at TEXT.word_zoom times
        the notes' size, on rows TEXT.word_zoom times theirs."""
        st = TYPE.focus
        return face(round(self.font_size * TEXT.word_zoom), weight or st.weight, st.tracking, st.leading,
                    round(self.line_h * TEXT.word_zoom))

    def _fill_box(self, c0: int, c1: int, y: int) -> tuple[float, float, float, float]:
        """A tight fill behind columns c0..c1 of the row whose cell starts at y; rows' fills touch."""
        top = y
        return (self.pad + c0 * self.char_w - FILL.pad_x, top,
                self.pad + c1 * self.char_w + FILL.pad_x - 1, top + self.line_h - 1)

    def _span_color(self, kind: str, which, sp: Span):
        if kind == "browse":
            return C.unit_text if sp.sentence in which[0] else C.text
        return C.orange_text if sp.sentence == which else C.text  # idle: the current sentence

    def _span_font(self, kind: str, which, sp: Span) -> ImageFont.FreeTypeFont:
        """Semibold for the unit under the hand and the current sentence, else medium."""
        return self.bold if sp.sentence in (which[0] if kind == "browse" else (which,)) else self.font

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
                    top = y
                    fd.rectangle((FILL.bar_x, top, FILL.bar_x + FILL.bar_w - 1, top + self.line_h - 1), fill=C.orange)
            for col, sp in row.spans:
                on_unit = kind == "browse" and sp.sentence in which[0]
                self._draw_span(od if on_unit else td, (self.pad + col * self.char_w, y + self.text_dy), sp,
                                self._span_font(kind, which, sp), self._span_color(kind, which, sp),
                                None if on_unit else ud)
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
        zf = self._zoom_face()
        font, size, lh, cw = zf.font, zf.size, zf.line_h, zf.advance
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
        dy = zf.dy
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
                    self._draw_span(draw, (x, y + dy), sp, font, color, ud, zf.tracking)
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
        key, scroll = self._band_style(state), self.shown_scroll(state)
        if self._band_key != key or not self._band_valid(scroll):
            self._band_start = max(0, math.floor(scroll) - BAND_SLACK_ROWS)
            self._band = self._render_band(key, self._band_start)
            self._band_key = key
        off = round((scroll - self._band_start) * self.line_h)
        below = len(self.rows) - (scroll + self.visible_rows)
        return self._faded(*_window(*self._band, off, self.box_h), scroll, below)

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
                     current: int | None = None, preview_from: int | None = None, width: int | None = None,
                     replacement: str | None = None, editing: bool = False, header_h: int = 0) -> Panel:
        """The unit's sentences enlarged, then the detail lines (Review: a line
        per take that said the sentence), faint context rows around them when
        there is room.

        Laid out whole: when it is taller than the viewport, draw() shows a
        scrolled part of it. With `current`, that sentence is orange and the
        rest of the unit dimmed (Rehearse); sentences from `preview_from` on
        (the next section, previewed) are faint. `width` narrows it (Review's
        take chips beside it), and then it has no context rows."""
        width = width or self.box_w
        key = (unit, detail, current, preview_from, width, replacement, editing, header_h)
        if self._panel_key == key:
            return self._panel
        sents = [parse_sentence(replacement)] if replacement is not None else [self.sentences[i] for i in unit]
        ids = (unit[0],) if replacement is not None else unit
        st = TYPE.notes  # Review's take lines: the notes' step, a little smaller, a little looser
        df = face(round(self.font_size * DETAIL.scale), st.weight, st.tracking, st.leading,
                  round(self.line_h * DETAIL.scale * DETAIL.leading))
        dlh = df.line_h
        dcols = max(DETAIL.min_columns, int((width - 2 * self.pad) / df.advance))
        dlines = [piece for line in detail
                  for piece in textwrap.wrap(line, dcols, subsequent_indent=DETAIL.indent) or [""]]
        detail_h = len(dlines) * dlh + (self.pad if dlines else 0)  # below the unit: room for the playbar
        box_h = max(self.line_h * 3, self.box_h - header_h)
        avail = self.max_panel_h - header_h - 2 * self.pad - detail_h
        # The largest size that fits the viewport with enough columns; never smaller than normal.
        for scale in TEXT.focus_scales:
            # Enlarged: the focus step (tighter rows and letters); at the notes' size, the notes'.
            ff = self._face(TYPE.focus if scale > 1 else TYPE.notes, scale=scale)
            lh, cw = ff.line_h, ff.advance
            cols = int((width - 2 * self.pad) / cw)
            rows = layout(sents, cols, ids)
            if len(rows) * lh <= avail and cols >= TEXT.focus_min_columns:
                break
        big_h = len(rows) * lh
        panel_h = max(box_h, big_h + detail_h + 2 * self.pad)
        spare = panel_h - 2 * self.pad - big_h - detail_h
        first, last = self._first_row[unit[0]], self._last_row[unit[-1]]
        above = self.rows[max(0, first - int(spare / 2 // self.line_h)) : first]
        below = self.rows[last + 1 : last + 1 + int((spare - len(above) * self.line_h) // self.line_h)]
        if width < self.box_w:  # the context rows are laid out for the full width
            above, below = [], []
        content = (len(above) + len(below)) * self.line_h + big_h + detail_h
        y = self.pad + max(0, (panel_h - 2 * self.pad - content) // 2)
        if editing:
            # A fixed starting row for the selected unit: incoming wording grows down,
            # instead of recentering the entire passage whenever its length changes.
            anchor = self.pad + round(box_h * TEXT.preview_anchor)
            count = max(0, (anchor - self.pad) // self.line_h)
            above = above[-count:] if count else []
            below = below[:max(0, (box_h - anchor - big_h - detail_h - self.pad) // self.line_h)]
            y = anchor - len(above) * self.line_h
            panel_h = max(box_h, anchor + big_h + detail_h + self.pad)

        img, under = (Image.new("RGBA", (width, panel_h), CLEAR) for _ in range(2))
        draw, ud = ImageDraw.Draw(img), ImageDraw.Draw(under)
        where: dict[int, tuple[int, int]] = {}

        def draw_rows(rows_, f, colors, y_, bold_=None, strong=lambda si: False):
            for row in rows_:
                for col, sp in row.spans:
                    self._draw_span(draw, (self.pad + col * f.advance, y_ + f.dy), sp,
                                    bold_ if strong(sp.sentence) else f.font, colors(sp.sentence), ud, f.tracking)
                    top, bottom = where.get(sp.sentence, (y_, y_))
                    where[sp.sentence] = (min(top, y_), max(bottom, y_ + f.line_h))
                y_ += f.line_h
            return y_

        def unit_colors(si):
            if current is not None and si == current:  # orange, even in the previewed section
                return C.orange_text
            if preview_from is not None and si >= preview_from:
                return C.faint
            if current is None:
                return C.focus_text
            return C.orange_text if si == current else C.dim

        def unit_strong(si):  # semibold: the current sentence, or the focused unit
            return si == current if current is not None else preview_from is None or si < preview_from

        y = draw_rows(above, self.notes, lambda si: C.faint, y)
        where.clear()  # context rows are not the unit
        y = draw_rows(rows, ff, unit_colors, y, load_font(ff.size, "semibold"), unit_strong)
        rows_y = dict(where)
        if dlines:
            y += self.pad
            for piece in dlines:
                _outline(ud, (self.pad, y + df.dy), piece, df.font, df.tracking)
                _text(draw, (self.pad, y + df.dy), piece, df.font, df.tracking, fill=C.detail_text)
                y += dlh
        draw_rows(below, self.notes, lambda si: C.faint, y)
        color, inv = _premultiply(_halo(img, self.halo_r, under))
        self._panel_key, self._panel = key, Panel(panel_h, color, inv, rows_y, scale=scale)
        return self._panel

    # --- the panel's viewport ------------------------------------------------

    def _label_face(self, line: int) -> Face:
        """The label's lines: the state, the operation, the gesture hint."""
        return self._face((TYPE.label, TYPE.operation, TYPE.hint)[line])

    def _label_wrap(self, text: str, line: int, width: float, max_rows: int | None = None) -> list[str]:
        return self._wrap(text, self._label_face(line), width, max_rows)

    def _label_row_h(self, line: int) -> int:
        return self._label_face(line).line_h

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
        preview = state.edit_preview if state.app == "prepare" else None
        header_h = max(0, sum(self._label_row_h(i) for i, _ in self.label_rows(state)) - self.label_h) if preview else 0
        panel = self._focus_panel(unit, state.detail, self._panel_current(state, unit),
                                 self._preview_from(state, unit), width,
                                 preview.text if preview and not preview.original else None, preview is not None, header_h)
        panel.header_h = header_h
        return panel

    def panel_view_h(self, panel: Panel) -> int:
        return min(panel.height, max(self.line_h * 3, self.max_panel_h - panel.header_h))

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

    def follow(self, state: ViewState, snap: bool = False) -> None:
        """Once a frame, before drawing: the springs head for what the state
        says (the panel's scroll, the focus growing or shrinking, the tone
        knob). `snap`: the panel's scroll moved by a key, shown at once."""
        self.follow_panel_scroll(state, snap)
        self.follow_bounce(state)
        self.follow_focus(state)
        self.follow_dials(state)

    def _focus_key(self, state: ViewState) -> tuple | None:
        """What is focused, for the focus's growing: (level, sentence, word), or None."""
        if state.app not in ("prepare", "review") or state.mode != "focus" or state.focus is None:
            return None
        if state.level == "word":
            return None if state.focus.word is None else ("word", state.focus.sentence, state.focus.word)
        if state.level in ("sentence", "paragraph"):
            return (state.level, state.focus.sentence, None)
        return None

    def _home(self, key: tuple, scroll: float) -> tuple[float, float, float, float] | None:
        """Where a focused unit (or word) is in the notes on screen at this
        scroll; None when it is out of view, or gone."""
        level, sentence, word = key
        if sentence not in self._first_row:
            return None
        if level == "word":
            return self.word_box(Hit(sentence, word), scroll)
        unit = self.unit(level, sentence)
        first, last = self._first_row[unit[0]], self._last_row[unit[-1]]
        if last < scroll - 1 or first > scroll + self.visible_rows:
            return None
        x = self.x + self.margin + self.pad
        y = self.y + self.margin + self.pad + (first - scroll) * self.line_h
        return x, y, x + self.columns * self.char_w, y + (last - first + 1) * self.line_h

    def follow_focus(self, state: ViewState) -> None:
        """The focus grows out of its unit's place in the notes (MOTION.focus_in)
        and, backed out, shrinks back into it (focus_out). Focusing it again on
        the way out grows it again from where it is."""
        fm = state.focus_motion
        if fm is None:
            return
        key, now = self._focus_key(state), state.now
        if key == fm.key:
            if key is None and fm.last is not None and (fm.spring.settled(now) or state.app not in ("prepare", "review")):
                fm.last = None
            return
        if key is not None:
            if fm.last is None or fm.last.key != key:
                fm.spring.snap(0.0, now)
            fm.home = self._home(key, self.shown_scroll(state))
            fm.spring.retarget(1.0, now, MOTION.fade if REDUCED else MOTION.focus_in)
            if fm.home is None:  # focused from out of view: nowhere to grow from
                fm.spring.snap(1.0, now)
        else:
            fm.home = self._home(fm.last.key, self.shown_scroll(state)) if fm.last is not None else None
            fm.spring.retarget(0.0, now, MOTION.fade if REDUCED else MOTION.focus_out)
            if fm.home is None:
                fm.spring.snap(0.0, now)
                fm.last = None
        fm.key = key

    def _growing(self, state: ViewState) -> float | None:
        """How far the focus has grown (0..1) while it grows; None once grown, or without the app's spring."""
        fm = state.focus_motion
        key = self._focus_key(state)
        if fm is None or key is None or fm.key != key or fm.home is None:
            return None
        p = fm.spring.at(state.now)
        return p if p < GROWN else None

    def follow_dials(self, state: ViewState) -> None:
        """The tone knob as shown: 1:1 with the hand while it drives the dial,
        a little past the ends with rising resistance (rubberband); sprung
        (MOTION.dial) back to the value when the hand stops driving it (the
        thumb closing into a pinch rewinds it, the L dropped past an end)."""
        ops = state.ops
        if ops.kind != "tone":
            ops.tone_shown = None
            return
        target = ops.tone + self._tone_give(ops)
        if ops.tone_shown is None:
            ops.tone_shown = Spring()
            ops.tone_shown.snap(target, state.now)
        elif ops.dialing:
            ops.tone_shown.snap(target, state.now)
        else:
            ops.tone_shown.retarget(target, state.now, _r(MOTION.dial))

    @staticmethod
    def _tone_give(ops: OpsView) -> float:
        """How far past its end the tone knob is drawn for a hand gone past it (none with reduced motion)."""
        return 0.0 if REDUCED else rubberband(ops.tone_over, GAUGE.rubber_max, MOTION.rubber)

    def shown_tone(self, state: ViewState) -> float:
        ops = state.ops
        if ops.tone_shown is not None:
            return ops.tone_shown.at(state.now)
        return ops.tone + self._tone_give(ops)

    def shown_scroll(self, state: ViewState) -> float:
        """The notes' scroll as drawn: with the rubber band's give past an end (follow_bounce)."""
        b = state.scroll_bounce
        return state.scroll + (b.at(state.now) if b is not None else 0.0)

    def follow_bounce(self, state: ViewState) -> None:
        """Browsing pushed past the first or last row (ViewState.scroll_push,
        rows): the notes follow the push with rising resistance
        (rubberband, MOTION.give_rows at most), and spring back once the hand
        stops pushing. None with reduced motion."""
        b = state.scroll_bounce
        if b is None:
            return
        if state.scroll_push and not REDUCED:
            b.snap(rubberband(state.scroll_push, MOTION.give_rows, MOTION.rubber), state.now)
        else:
            b.retarget(0.0, state.now, _r(MOTION.scroll))

    def _put(self, frame: np.ndarray, x: float, y: float, color: np.ndarray, inv: np.ndarray,
             g: Grow | None = None, record: list | None = None, grows: bool = True) -> None:
        """A patch at (x, y), or where it is while the focus grows or shrinks
        (`g`: scaled and faded with it; not `grows`: faded in place, context
        around the unit); `record` keeps it for the way out."""
        if record is not None:
            record.append((x, y, color, inv, grows))
        if g is not None and not grows:
            g = Grow(g.p, 1.0, x, y, x, y)
        if g is None:
            _blend(frame, int(x), int(y), color, inv)
            return
        h, w = color.shape[:2]
        size = (max(1, round(w * g.s)), max(1, round(h * g.s)))
        if size != (w, h):
            color = cv2.resize(color, size, interpolation=cv2.INTER_LINEAR)
            inv = cv2.resize(inv, size, interpolation=cv2.INTER_LINEAR).reshape(size[1], size[0], 1)
        gx, gy = g.at(x, y)
        _blend(frame, round(gx), round(gy), color * g.p, 1.0 - g.p * (1.0 - inv))

    def _band_faded(self, frame: np.ndarray, state: ViewState, alpha: float) -> None:
        """The notes (as browsing them) at `alpha`, under a focus growing out of them or shrinking back."""
        alpha *= alpha  # gone sooner than the focus arrives: less of the two showing at once
        if alpha <= 0:
            return
        if state.mode == "focus" and state.focus is not None:
            state = replace(state, mode="browse",
                            hover=Hit(state.focus.sentence, state.focus.word if state.level == "word" else None))
        color, inv = self._band_crop(state)
        _blend(frame, self.x + self.margin, self.y + self.margin, color * alpha, 1.0 - alpha * (1.0 - inv))

    def shown_panel_scroll(self, state: ViewState) -> float:
        """The panel's scroll as drawn: its spring's, which may run a little
        past the ends while the text slides in; without one, panel_scroll clamped."""
        m = state.panel_motion
        if m is None or m.unit is None or m.unit != self._panel_unit(state):
            return self.clamp_panel_scroll(state, state.panel_scroll)
        return m.spring.at(state.now)

    def follow_panel_scroll(self, state: ViewState, snap: bool = False) -> None:
        """Once a frame, before drawing: the shown scroll heads for
        panel_scroll, sprung (MOTION.scroll), or at once with `snap` (keys
        never animate). A new unit in the panel (the section handed on, the
        next section's preview coming or going) starts with the current
        sentence where the last frame showed it, so the text slides into place
        instead of jumping."""
        m = state.panel_motion
        if m is None:
            return
        unit = self._panel_unit(state)
        if unit is None:
            m.unit = None
            return
        target = self.clamp_panel_scroll(state, state.panel_scroll)
        if unit != m.unit:
            m.unit, start = unit, target
            panel = self.panel(state)
            seen = self._drawn_rows.get(state.current)
            if not snap and not REDUCED and seen is not None and state.current in panel.rows:
                top = self._panel_top(self.panel_view_h(panel), panel.header_h)
                start = panel.rows[state.current][0] + top - seen
            m.spring.snap(start, state.now)
        elif snap:
            m.spring.snap(target, state.now)
        m.spring.retarget(target, state.now, _r(MOTION.scroll))

    def _panel_view(self, panel: Panel, scroll: int, view_h: int) -> tuple[np.ndarray, np.ndarray]:
        """The panel's content through its viewport at `scroll` px."""
        return _window(panel.color, panel.inv, scroll, view_h)

    @staticmethod
    def _receding(state: ViewState, color: np.ndarray, inv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The focus fading (MOTION.drop_fade) as the hand drop's timer runs toward backing out."""
        k = 1.0 - MOTION.drop_fade * state.drop_progress if state.mode == "focus" else 1.0
        if k >= 1.0:
            return color, inv
        return color * k, 1.0 - k * (1.0 - inv)

    def _panel_top(self, view_h: int, extra_header: int = 0) -> int:
        box_top = self.y + self.margin
        reserved = LABEL.min_top + self.label_h + self.pad // 2 + extra_header
        top = max(reserved, box_top + (self.box_h - view_h) // 2)
        return max(reserved, min(top, self.frame_h - self.line_h - self.pad - view_h))

    def _draw_playbar(self, frame: np.ndarray, state: ViewState, panel: Panel, top: int, bottom: int,
                      scroll: int) -> None:
        """A thin bar under the focused unit's last enlarged row: how far what
        plays has got. Kept inside the panel's viewport."""
        rows = [panel.rows[i] for i in self._panel_unit(state) or () if i in panel.rows]
        if not rows:
            return
        t = self._bar_w()
        y = min(top - scroll + max(b for _, b in rows) + self.pad * PLAYBAR.gap, bottom - t / 2)
        if y < top:
            return
        x0 = self.x + self.margin + self.pad
        x1 = self.x + self.margin + panel.color.shape[1] - self.pad
        self._draw_bar(frame, x0, x1, y, state.play_progress)

    def _draw_scrollbar(self, frame: np.ndarray, top: int, view_h: int, scroll: float, height: int,
                        width: int, left: bool = False) -> None:
        x = int(self.x + self.margin + (self.pad // 3 if left else width - self.pad // 3))
        cv2.line(frame, (x, top + self.pad), (x, top + view_h - self.pad), bgr(C.scroll_track), 2, cv2.LINE_AA)
        span = view_h - 2 * self.pad
        y0 = top + self.pad + int(span * scroll / height)
        y1 = top + self.pad + int(span * (scroll + view_h) / height)
        cv2.line(frame, (x, y0), (x, y1), bgr(C.scroll_thumb), 4, cv2.LINE_AA)
        f = self._face(TYPE.small, SMALL_ON_FILL)
        cx = self.x + self.margin + width / 2
        if scroll > 0:
            self._blend_centered(frame, self._chip("▲ MORE", f, C.node_text, C.dark_fill), cx, top)
        if scroll + view_h < height:
            self._blend_centered(frame, self._chip("▼ MORE", f, C.node_text, C.dark_fill), cx, top + view_h)

    def _chip(self, text: str, f: Face, fg, bg, outline=None) -> tuple[np.ndarray, np.ndarray]:
        """A row of text in face `f` on a rounded rectangle, cached."""
        key = (text, f, fg, bg, outline)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            size = f.size
            px, py = max(CHIPS.pad_x[0], size // CHIPS.pad_x[1]), max(CHIPS.pad_y[0], size // CHIPS.pad_y[1])
            w, h = int(_text_w(f.font, text, f.tracking)) + 2 * px, f.line_h + 2 * py
            img = Image.new("RGBA", (w + 2, h + 2), CLEAR)
            draw = ImageDraw.Draw(img)
            if bg is not None or outline is not None:
                radius = max(CHIPS.radius[0], size // CHIPS.radius[1])
                draw.rounded_rectangle((1, 1, w, h), radius=radius, fill=bg, outline=outline)
            _text(draw, (1 + px, 1 + py + f.dy), text, f.font, f.tracking, fill=fg)
            self._chips[key] = _premultiply(img)
        return self._chips[key]

    def _block(self, lines: tuple[str, ...], f: Face, colors: tuple, bg) -> tuple[np.ndarray, np.ndarray]:
        """Rows of text in face `f` on one rounded rectangle (the take table,
        the keys, the alert line), a colour per row, cached."""
        key = ("block", lines, f, colors, bg)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            size = f.size
            px, py = max(CHIPS.pad_x[0], size // CHIPS.pad_x[1]), max(CHIPS.pad_y[0], size // CHIPS.pad_y[1])
            w = int(max(_text_w(f.font, line, f.tracking) for line in lines)) + 2 * px
            h = len(lines) * f.line_h + 2 * py
            img = Image.new("RGBA", (w + 2, h + 2), CLEAR)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((1, 1, w, h), radius=max(CHIPS.radius[0], size // CHIPS.radius[1]), fill=bg)
            for i, (line, fg) in enumerate(zip(lines, colors)):
                _text(draw, (1 + px, 1 + py + i * f.line_h + f.dy), line, f.font, f.tracking, fill=fg)
            self._chips[key] = _premultiply(img)
        return self._chips[key]

    def _ink(self, text: str, f: Face, fg) -> tuple[np.ndarray, np.ndarray, int]:
        """A row of text in face `f` on a soft dark halo and outline, no box,
        cached: (colour, inverse alpha, margin), the row's top left `margin`
        px in from the patch's."""
        key = ("ink", text, f, fg, C.shadow, C.outline)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            r = max(1.0, f.size * SHADOW.blur)
            m = math.ceil(3 * r) + OUTLINE.width
            img, under = (Image.new("RGBA", (int(_text_w(f.font, text, f.tracking)) + 2 * m + 1, f.line_h + 2 * m),
                                    CLEAR) for _ in range(2))
            _outline(ImageDraw.Draw(under), (m, m + f.dy), text, f.font, f.tracking)
            _text(ImageDraw.Draw(img), (m, m + f.dy), text, f.font, f.tracking, fill=fg)
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

    def _wrap(self, text: str, f: Face, width: float, max_rows: int | None = None) -> list[str]:
        """`text` wrapped to `width` px in face `f`; past max_rows the last row ends in "…"."""
        cols = max(4, int((width + f.tracking) / f.advance))
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
        if (preview := state.edit_preview) is not None and state.app == "prepare" and state.mode == "focus":
            title = "TONE PREVIEW" if preview.kind == "tone" else "LENGTH PREVIEW"
            status = preview.error.partition(": PINCH")[0] if preview.error else ("UPDATING PREVIEW..." if preview.loading else
                                      "ORIGINAL UNCHANGED" if preview.original else "PREVIEW - NOT SAVED")
            hint = "PINCH + LIFT: RETRY" if preview.error else "PINCH + LIFT: COMMIT"
            lines = [(0, title), (1, f"TARGET: {preview.target_label}"),
                     (2, f"SHOWING {preview.shown_label}: {preview.actual_words} WORDS"),
                     (2, state.note or status), (2, hint), (2, "DROP HAND: CANCEL")]
            if preview.marks_warning:
                lines.append((2, "LEGACY MARKS NEED REVIEW; PREVIEW IS PLAIN TEXT"))
            width = self.col_x1 - self.x - self.pad
            return [(level, row) for level, line in lines for row in self._label_wrap(line, level, width)]
        first, second, progress = self._label(state)
        operation, _, hint = second.partition(HINT_SEP)
        width = self.col_x1 - self.x - self.pad
        # A hold's bar goes after the operation's text: leave it room on the row.
        room = (BAR.label_min_em + BAR.gap_em) * self._label_face(1).size if progress is not None else 0
        rows = [(i, row) for i, text in enumerate((first, operation)) if text
                for row in self._label_wrap(text, i, width - (room if i == 1 else 0), LABEL.max_rows[i])]
        if state.app == "review" and hint == (forms := review_hint(state))[0]:
            # Review's hints: the short form when the long one needs two rows;
            # if even that is too wide, its hints packed whole onto the rows.
            if len(self._label_wrap(hint, 2, width)) > 1:
                hint = forms[1]
            packed: list[str] = []
            for part in hint.split(REVIEW_SEP):
                joined = f"{packed[-1]}{REVIEW_SEP}{part}" if packed else part
                if packed and len(self._label_wrap(joined, 2, width)) == 1:
                    packed[-1] = joined
                else:
                    packed += self._label_wrap(part, 2, width)
            return rows + [(2, row) for row in packed[:LABEL.max_rows[2]]]
        if hint:  # too long for a row: a row per hint rather than breaking one in two
            hint_rows = self._label_wrap(hint, 2, width)
            if len(hint_rows) > 1:
                hint_rows = [row for part in hint.split(HINT_SEP) for row in self._label_wrap(part, 2, width)]
            if len(hint_rows) > LABEL.max_rows[2]:
                hint_rows = self._label_wrap(" ".join(hint_rows), 2, width, LABEL.max_rows[2])
            rows += [(2, row) for row in hint_rows]
        return rows

    def _draw_label(self, frame: np.ndarray, state: ViewState, top: int) -> None:
        """No box: orange on a halo, the state brightest and in semibold, the
        operation dimmer, the hint smallest and dimmest (TYPE.label,
        operation, hint; Colors.label_*), ending above `top`; a hold's bar
        after the operation. Its first row stays put while a hint comes and
        goes; only a label wrapped past three rows grows upward."""
        rows = self.label_rows(state)
        progress = self.label_progress(state)
        last_op = max((k for k, (line, _) in enumerate(rows) if line == 1), default=None)
        colors = (C.label_state, C.label_operation, C.label_hint)
        h = max(sum(self._label_row_h(i) for i in range(3)), sum(self._label_row_h(i) for i, _ in rows))
        y = max(LABEL.min_top, top - self.pad // 2 - h)
        for k, (line, text) in enumerate(rows):
            f = self._label_face(line)
            box = self._blend_ink(frame, self._ink(text, f, colors[line]), self.x + self.pad, y)
            if k == last_op and progress is not None:
                x0 = box[2] + BAR.gap_em * f.size
                x1 = min(x0 + BAR.label_em * f.size, self.col_x1)
                self._draw_bar(frame, x0, x1, y + f.line_h / 2, progress)
            y += f.line_h

    def _bar_w(self) -> int:
        return bar_thickness(self.ui_size)

    def _draw_bar(self, frame: np.ndarray, x0: float, x1: float, y: float, progress: float,
                  from_centre: bool = False) -> None:
        draw_bar(frame, x0, x1, y, progress, self._bar_w(), from_centre)

    def _node_size(self) -> int:
        return round(self.font_size * TEXT.word_zoom * RING.node_scale)

    def _node(self, text: str, fg, fill, outline, weight: str = "medium") -> tuple[np.ndarray, np.ndarray]:
        """An options-ring node, Kat's style: text in a square box, a solid
        outline, roomy padding (the notes' step at the node's size); cached."""
        size = self._node_size()
        key = ("node", text, size, fg, fill, outline, weight)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            f = face(size, weight, TYPE.notes.tracking, TYPE.notes.leading)
            font, lh = f.font, f.line_h
            px, py = round(size * RING.node_pad[0]), round(size * RING.node_pad[1])
            w, h = int(_text_w(font, text, f.tracking)) + 2 * px, lh + 2 * py
            img = Image.new("RGBA", (w + 2, h + 2), CLEAR)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((1, 1, w, h), radius=RING.node_radius, fill=fill, outline=outline,
                                   width=RING.node_outline_w if outline else 0)
            _text(draw, (1 + px, 1 + py + f.dy), text, font, f.tracking, fill=fg)
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
        k = 1.0 - (0.0 if REDUCED else MOTION.drop_pull) * state.drop_progress  # backing out: drawn in toward the word
        return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in pts]

    def _panel_origin(self, state: ViewState) -> tuple[Panel, float, float, int] | None:
        """The focus panel, where its content's (0, 0) is on screen, and its viewport's top."""
        panel = self.panel(state)
        if panel is None:
            return None
        view_h = self.panel_view_h(panel)
        top = self._panel_top(view_h, panel.header_h)
        return panel, self.x + self.margin, top - int(round(self.shown_panel_scroll(state))), top

    def _takes_w(self, state: ViewState) -> int:
        """Review: the width the take chips' column needs at the text column's right, gap included."""
        if not state.takes:
            return 0
        f = self._face(TYPE.small, SMALL_ON_FILL)
        return max(self._chip(label, f, C.node_text, C.dark_fill, C.node_outline)[0].shape[1]
                   for label in state.takes) + RING.take_gap

    def _take_chips(self, state: ViewState) -> list[tuple[tuple[np.ndarray, np.ndarray], float, float]]:
        """Review: the focused sentence's takes as chips in a column at the
        text column's right, beside the (narrowed) panel, from its top, a
        fixed pitch apart: (chip, centre x, centre y) each."""
        got = self._panel_origin(state) if state.takes else None
        if got is None:
            return []
        f, shown = self._face(TYPE.small, SMALL_ON_FILL), self._face(TYPE.small, "semibold")
        x0, y = self.col_x1 - (self._takes_w(state) - RING.take_gap), got[3] + self.pad
        out = []
        for i, label in enumerate(state.takes):
            chip = self._chip(label, shown, C.chip_text, C.chip_fill) if i == state.take_shown \
                else self._chip(label, f, C.node_text, C.dark_fill, C.node_outline)
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
        labels, picked = self.ring_labels(state), state.ops.picked
        nodes = self.ring_nodes(state)
        if 0 <= picked < len(nodes):  # rising with the pinched hand toward a commit
            nodes[picked] = (nodes[picked][0], nodes[picked][1] - MOTION.lift_px * state.lift)
        self._ring_snap = (labels, picked, nodes, box)
        opened = state.ops.opened.at(state.now) if state.ops.opened is not None else 1.0
        spread = 1.0 if REDUCED else 0.35 + 0.65 * opened
        self._draw_ring_at(frame, labels, picked, nodes, box, state.ops.closing, spread, opened)

    def _draw_ring_at(self, frame: np.ndarray, labels, picked: int, nodes, box, closing: bool,
                      spread: float = 1.0, alpha: float = 1.0) -> None:
        """The ring's spokes and nodes, `spread` of the way out from the word
        (opening out of it, or collapsing back in) at `alpha`."""
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        held = None
        for i, (label, (nx, ny)) in enumerate(zip(labels, nodes)):
            nx, ny = cx + (nx - cx) * spread, cy + (ny - cy) * spread
            if i == picked:
                chip = self._node(label, C.chip_text, C.chip_fill, None, "semibold")
            else:
                chip = self._node(label, C.node_text, C.node_fill, C.node_outline)
            h, w = chip[0].shape[:2]
            a = _box_edge((cx, cy), (box[2] - box[0]) / 2 + RING.spoke_gap, (box[3] - box[1]) / 2 + RING.spoke_gap, (nx, ny))
            b = _box_edge((nx, ny), w / 2 + RING.spoke_gap, h / 2 + RING.spoke_gap, (cx, cy))
            ctrl = ((a[0] + b[0]) / 2 - (b[1] - a[1]) * RING.bow, (a[1] + b[1]) / 2 + (b[0] - a[0]) * RING.bow)
            ts = np.linspace(0, 1, RING.curve_points)[:, None]
            curve = (1 - ts) ** 2 * np.array(a) + 2 * (1 - ts) * ts * np.array(ctrl) + ts ** 2 * np.array(b)
            _poly_blend(frame, curve.astype(np.int32), C.connector, RING.stroke, alpha)
            if alpha < 1.0:
                chip = (chip[0] * alpha, 1.0 - alpha * (1.0 - chip[1]))
            node = self._blend_centered(frame, chip, nx, ny)
            if i == picked:
                held = node
        if held is not None and closing:  # the pinch will take this: it is held
            g = RING.closing_box
            x0, y0, x1, y1 = (int(v) for v in held)
            cv2.rectangle(frame, (x0 - g, y0 - g), (x1 + g, y1 + g), bgr(C.yellow), g, cv2.LINE_AA)

    def _draw_stretch(self, frame: np.ndarray, ops: OpsView) -> None:
        """The line between the two index tips, under the text, faint. Past the
        length's limits the hands can't be followed: past the fullest, only
        the part of the line up to the limit is drawn solid, the rest fainter;
        past the shortest, faint ticks mark where the limit's length would end."""
        a, b = (np.array(p, float) for p in ops.stretch_ends)
        stroke = HANDS.closing_stretch_stroke if ops.closing else HANDS.stretch_stroke
        pt = lambda v: tuple(int(round(c)) for c in v)
        raw, mid, half = ops.stretch_raw, (a + b) / 2, (b - a) / 2
        if raw > OPS.stretch_max:
            _line_blend(frame, pt(a), pt(b), C.stretch_line, stroke, HANDS.stretch_over_alpha)
            k = OPS.stretch_max / raw
            _line_blend(frame, pt(mid - half * k), pt(mid + half * k), C.stretch_line, stroke, HANDS.stretch_alpha)
            return
        _line_blend(frame, pt(a), pt(b), C.stretch_line, stroke, HANDS.stretch_alpha)
        if 0 < raw < OPS.stretch_min and np.hypot(*half) > 0:
            k = OPS.stretch_min / raw
            normal = np.array([-half[1], half[0]]) / np.hypot(*half) * HANDS.stretch_tick / 2
            for end in (mid - half * k, mid + half * k):
                _line_blend(frame, pt(end - normal), pt(end + normal), C.stretch_line, stroke, HANDS.stretch_alpha)

    def _draw_gauge(self, frame: np.ndarray, tone: float, top: int, bottom: int, closing: bool = False,
                    sentence_bounds: tuple[float, float] | None = None) -> None:
        """Vertical tone dial in the text box's right padding, just right of
        the text: cold (blue, "formal") at the top, warm (orange,
        "conversational") at the bottom, each end labelled. Its compact track
        follows the sentence's height and centre, not the surrounding context."""
        edge = GAUGE.knob_r + GAUGE.closing_knob_outline  # the knob stays inside the text column
        x = int(min(self.x + self.margin + self.box_w - self.pad // 2, self.col_x1 - edge))
        f = self._face(TYPE.small)
        a, b = sentence_bounds or (top, bottom)
        label_room = f.line_h + GAUGE.knob_r + self.pad
        low, high = top + label_room, bottom - label_room
        height = min(max(b - a + GAUGE.sentence_pad * self.line_h, GAUGE.min_lines * self.line_h),
                     GAUGE.max_lines * self.line_h, max(1, high - low))
        center = max(low + height / 2, min((a + b) / 2, high - height / 2))
        y0, y1 = round(center - height / 2), round(center + height / 2)
        for text, color, above in ((GAUGE.labels[0], C.cold, True), (GAUGE.labels[1], C.warm, False)):
            ink = self._ink(text, f, (*color, 255))
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

    def _draw_rec(self, frame: np.ndarray, state: ViewState) -> None:
        """Rehearse: the recording clock and the microphone level, top right (REC)."""
        m, sec = divmod(int(state.rec_s), 60)
        rec = f"REC {m}:{sec:02d}" + (" · VIDEO" if state.recording_video else "")
        chip = self._chip(rec, self._face(TYPE.operation), C.node_text, C.dark_fill)
        top = REC.top * self.line_h
        bx0, by0, _, by1 = self._blend_centered(frame, chip, REC.x * self.frame_w, top + chip[0].shape[0] / 2)
        # The dot swells with the microphone level: a flat dot means no sound is arriving.
        r0, grow = REC.mic_r
        cv2.circle(frame, (bx0 - REC.mic_dx, (by0 + by1) // 2), r0 + round(grow * state.mic), bgr(C.rec), -1,
                   cv2.LINE_AA)

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
            self._draw_stretch(frame, state.ops)
        box_top = self.y + self.margin
        top, bottom = box_top, box_top + self.box_h
        panel, zoom = self.panel(state), None
        lift = round(MOTION.lift_px * state.lift) if state.mode == "focus" else 0
        self._drawn_rows = {}
        # The focus growing out of its unit's place in the notes (g), and what
        # it draws, kept (focused) to shrink back into the notes on the way out.
        key, growing, g = self._focus_key(state), self._growing(state), None
        layers: list | None = [] if key is not None and state.focus_motion is not None else None
        target, scale = None, 1.0
        self._ring_snap = None
        if panel is not None:
            view_h = self.panel_view_h(panel)
            scroll = int(round(self.shown_panel_scroll(state)))
            top = self._panel_top(view_h, panel.header_h)
            bottom = top + view_h
            color, inv = self._panel_view(panel, scroll, view_h)
            clamped = int(self.clamp_panel_scroll(state, scroll))
            if panel.height > view_h or clamped != scroll:
                color, inv = self._faded(color, inv, scroll / self.line_h,
                                         (panel.height - scroll - view_h) / self.line_h)
            rows = [panel.rows[i] for i in self._panel_unit(state) or () if i in panel.rows]
            if key is not None and rows:
                x0 = self.x + self.margin + self.pad
                target = (x0, top - scroll + min(a for a, _ in rows), x0 + panel.color.shape[1] - 2 * self.pad,
                          top - scroll + max(b for _, b in rows))
                scale = panel.scale
                if growing is not None:
                    g = grow(state.focus_motion.home, target, scale, growing)
                    self._band_faded(frame, state, 1.0 - growing)
            color, inv = self._receding(state, color, inv)
            if target is None:
                self._put(frame, self.x + self.margin, top - lift, color, inv)
            else:  # the unit's rows grow from their place in the notes; the context around them fades in where it is
                u0 = max(0, int(target[1] - top) - self.row_gap)
                u1 = min(view_h, int(target[3] - top) + self.row_gap)
                for a, b, grows in ((0, u0, False), (u0, u1, True), (u1, view_h, False)):
                    if b > a:
                        self._put(frame, self.x + self.margin, top - lift + a, color[a:b], inv[a:b], g, layers, grows)
            if panel.height > view_h and g is None:
                self._draw_scrollbar(frame, top, view_h, clamped, panel.height, panel.color.shape[1],
                                     left=state.ops.kind == "tone")
            self._drawn_rows = {si: top - scroll + y0 for si, (y0, _) in panel.rows.items()}
        elif (zoom := self.focus_word_box(state)) is not None:
            color, inv, _, _ = self._word_zoom(state)
            target, scale = zoom, TEXT.word_zoom
            if growing is not None:
                g = grow(state.focus_motion.home, target, scale, growing)
                self._band_faded(frame, state, 1.0 - growing)
            self._put(frame, 0, box_top, *self._receding(state, color, inv), g, layers)
        else:
            fm = state.focus_motion
            last = fm.last if fm is not None and fm.key is None and fm.home is not None else None
            p = fm.spring.at(state.now) if last is not None else 0.0
            if p > 0:  # backed out: the focus shrinks back into the notes
                self._band_faded(frame, state, 1.0 - p)
                shrink = grow(fm.home, last.target, last.scale, p)
                for x, y, color, inv, grows in last.layers:
                    self._put(frame, x, y, color, inv, shrink, grows=grows)
                if last.ring is not None:
                    labels, picked, nodes, box = last.ring
                    (bx0, by0), (bx1, by1) = shrink.at(box[0], box[1]), shrink.at(box[2], box[3])
                    self._draw_ring_at(frame, labels, picked, [shrink.at(*n) for n in nodes], (bx0, by0, bx1, by1),
                                       False, 1.0 if REDUCED else 0.35 + 0.65 * p, p)
            else:
                _blend(frame, self.x + self.margin, box_top, *self._band_crop(state))

        # Word chips are drawn over the cached band, so hovering never re-renders text.
        if state.mode == "browse" and state.level == "word" and state.hover and state.hover.word is not None:
            if (box := self.word_box(state.hover, self.shown_scroll(state))) is not None:
                st = TYPE.notes  # in semibold, as the unit under the hand is; on the word's row
                chip = self._chip(self.word_text(state.hover),
                                  face(round(self.font_size * CHIPS.hover_scale), "semibold", st.tracking, st.leading),
                                  C.chip_text, C.chip_fill)
                x0, y0, x1, y1 = self._blend_centered(frame, chip, (box[0] + box[2]) / 2, box[1] + self.line_h / 2)
                if state.ops.closing:  # pinching: this word is held, the pinch will focus it
                    g = RING.closing_box
                    cv2.rectangle(frame, (x0 - g, y0 - g), (x1 + g, y1 + g), bgr(C.yellow), g, cv2.LINE_AA)
        if panel is None and zoom is not None:
            if g is None and state.ops.kind == "ring":
                self._draw_ring(frame, state, zoom)
            elif g is None and state.app == "prepare" and state.meaning:
                self._draw_meaning(frame, state.meaning, zoom[3])
            text, ink = self.focus_word(state), C.orange_text
            if state.loading and REDUCED:  # the word is being rewritten: dimmed, still (the label says so)
                ink = C.dim
            elif state.loading:  # glyph scramble: the word is being rewritten
                text = "".join(SCRAMBLE[(ord(c) + int(state.now * 12)) % len(SCRAMBLE)] if c.isalpha() else c
                               for c in text)
            # Inline, in the gap the zoomed text left for it: orange, at the zoomed size (rising with a pinched hand).
            zf = self._zoom_face("semibold")
            color, inv, m = self._ink(text, zf, ink)
            dy = zf.dy  # on the zoomed rows' baseline
            self._put(frame, int(zoom[0]) - m, int(zoom[1] + dy - lift) - m, color, inv, g, layers)
        if layers is not None and target is not None:
            state.focus_motion.last = FocusFrame(key, layers, target, scale, self._ring_snap)

        if state.mode == "focus" and state.play_progress is not None and panel is not None and g is None:
            self._draw_playbar(frame, state, panel, top, bottom, scroll)
        if state.mode == "focus" and state.ops.kind == "tone" and g is None:
            bounds = None
            if panel is not None and state.focus is not None and state.focus.sentence in panel.rows:
                bounds = tuple(top - scroll + y for y in panel.rows[state.focus.sentence])
            self._draw_gauge(frame, self.shown_tone(state), top, bottom, state.ops.closing, bounds)
        if state.app == "rehearse":
            self._draw_rec(frame, state)
        if state.app == "review" and state.mode == "focus" and state.takes and g is None:
            self._draw_takes(frame, state)
        if state.app == "count_in" and state.count_in > 0 and state.calibration is None:
            # In the hand zone, below the command zone: the hand is down during
            # the count-in. A bar under the digit empties over each second.
            if self._count is None or self._count[0] != state.count_in:
                self._count = (state.count_in, state.now)
            f = self._face(TYPE.display)
            ink = self._ink(str(state.count_in), f, C.label_state)
            cx = sum(LAYOUT.hand) / 2 * self.frame_w
            box = self._blend_centered(frame, ink, cx, self.frame_h * COUNT_IN.y)
            half = f.advance * COUNT_IN.bar_em / 2
            self._draw_bar(frame, cx - half, cx + half, box[3] + BAR.count_em * self.ui_size,
                           1.0 - (state.now - self._count[1]))
        else:
            self._count = None
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
        """The selected word's meaning under it, on a navy box: the notes'
        step, made smaller (not below TEXT.meaning_min_size) until it fits
        above the box's bottom; its "MEANING" header dimmer."""
        st = TYPE.notes
        size = self.font_size
        x = self.x + self.margin
        pad = self.pad // 2
        width = int(self.col_x1 - x)
        y = round(word_bottom + pad)
        available = self.y + self.margin + self.box_h - y
        while True:
            f = face(size, st.weight, st.tracking, st.leading)
            rows = ["MEANING", *self._wrap(meaning, f, width - 2 * pad)]
            height = len(rows) * f.line_h + 2 * pad
            if height <= available or size <= TEXT.meaning_min_size:
                break
            size -= 1
        key = ("meaning", meaning, width, f)
        if key not in self._chips:
            if len(self._chips) >= CHIP_CACHE_MAX:
                self._chips.clear()
            img = Image.new("RGBA", (width, height), C.node_fill)
            draw = ImageDraw.Draw(img)
            for i, row in enumerate(rows):
                _text(draw, (pad, pad + i * f.line_h + f.dy), row, f.font, f.tracking,
                      fill=C.label_operation if i == 0 else C.detail_text)
            self._chips[key] = _premultiply(img)
        _blend(frame, x, y, *self._chips[key])

    def _draw_alert(self, frame: np.ndarray, alert: str, bottom: int, low: int) -> int:
        """The persistent alert line under the text (TYPE.operation, white on
        red), wrapped to the text column, above `low`; returns the y under it."""
        f = self._face(TYPE.operation)
        pad_x = 2 * max(CHIPS.pad_x[0], f.size // CHIPS.pad_x[1]) + 2
        rows = tuple(self._wrap(alert, f, self.col_x1 - self.x - pad_x))
        block = self._block(rows, f, (C.alert_text,) * len(rows), C.alert_fill)
        y = min(bottom + self.pad // 2, low - block[0].shape[0])
        _blend(frame, self.x + self.margin, y, *block)
        return y + block[0].shape[0]

    def _draw_pills(self, frame: np.ndarray, state: ViewState) -> int:
        """Bottom left (TYPE.small): the CLOUD LLM chip whenever the text
        asked about can leave the Mac, then the keys (or the keys help).
        Returns their top."""
        x, low = self.x + self.margin, self.frame_h - LABEL.min_top
        f = self._face(TYPE.small, SMALL_ON_FILL)
        top = low
        if state.llm == "cloud":
            text, fg = ("CLOUD LLM: SENDING", C.orange) if state.llm_busy else ("CLOUD LLM", C.dim)
            chip = self._chip(text, f, fg, C.label_fill)
            _blend(frame, x, low - chip[0].shape[0], *chip)
            top = low - chip[0].shape[0]
            if state.keys_help:
                low -= chip[0].shape[0]
            else:
                x += chip[0].shape[1] + self.pad // 2
        if state.keys_help:
            top = self._draw_keys(frame, low)
        elif state.app in ("prepare", "count_in", "rehearse", "review"):
            chip = self._chip("H: KEYS", f, C.dim, C.label_fill)
            _blend(frame, x, low - chip[0].shape[0], *chip)
            top = min(top, low - chip[0].shape[0])
        return top

    def _draw_summary(self, frame: np.ndarray, lines: tuple[str, ...]) -> None:
        """The take table at the bottom right (TYPE.small): one dark block,
        monospace so the columns line up, the first row (the takes) in orange."""
        colors = tuple(C.orange if i == 0 else C.node_text for i in range(len(lines)))
        color, inv = self._block(tuple(lines), self._face(TYPE.small, SMALL_ON_FILL), colors, C.dark_fill)
        h, w = color.shape[:2]
        _blend(frame, self.frame_w - SUMMARY.right - w, self.frame_h - SUMMARY.bottom - h, color, inv)

    def _draw_tutorial(self, frame: np.ndarray, step: int, of: int, text: str) -> None:
        """One gesture at a time, at the bottom right under the hand box (it
        teaches the hand), right-aligned: where it is in the steps and how to
        skip (the label's hint), then what to do (the label's state)."""
        small, big = self._label_face(2), self._label_face(0)
        dots = " ".join("●" if k < step else "○" for k in range(of))
        right = self.frame_w - SUMMARY.right
        width = right - LAYOUT.hand[0] * self.frame_w
        rows = [(row, small, C.label_hint) for row in self._wrap(f"{dots}   ENTER: SKIP  /  G: HIDE", small, width)] + \
            [(row, big, C.label_state) for row in self._wrap(text, big, width)]
        y = self.frame_h - SUMMARY.bottom - sum(f.line_h for _, f, _ in rows)
        for row, f, color in rows:
            ink = self._ink(row, f, color)
            self._blend_ink(frame, ink, right - (ink[0].shape[1] - 2 * ink[2]), y)
            y += f.line_h

    def _draw_keys(self, frame: np.ndarray, low: int) -> int:
        """The keyboard fallback (TYPE.small, smaller if need be), bottom left,
        ending at `low`, as large as fits the text column. Returns its top."""
        st = TYPE.small
        size = self._face(st).size
        widest = max(KEYS_HELP, key=len)
        while True:
            f = face(size, SMALL_ON_FILL, st.tracking, st.leading)
            px = max(CHIPS.pad_x[0], size // CHIPS.pad_x[1])
            if size <= 8 or _text_w(f.font, widest, f.tracking) + 2 * px + 2 <= self.col_x1 - self.x:
                break
            size -= 1
        block = self._block(KEYS_HELP, f, (C.node_text,) * len(KEYS_HELP), C.dark_fill)
        top = low - block[0].shape[0]
        _blend(frame, self.x + self.margin, top, *block)
        return top

    # --- drawing: the take player ------------------------------------------

    def draw_caption(self, frame: np.ndarray, words: list[tuple[str, str, bool]]) -> None:
        """A row of word chips under the text, left to right, until the frame's
        edge: (text, kind, being said now), kind as in PLAYER.caption."""
        st = TYPE.notes
        f = face(round(self.ui_size * PLAYER.caption_scale), st.weight, st.tracking, st.leading)
        x, y = self.x + self.margin, int(self.frame_h * PLAYER.caption_y)
        for text, kind, current in words:
            chip = self._chip(text, f, PLAYER.caption[kind], PLAYER.caption_current if current else None)
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

def bar_thickness(ui_size: int) -> int:
    return max(BAR.min_thickness, round(ui_size * BAR.thickness))


def draw_bar(frame: np.ndarray, x0: float, x1: float, y: float, progress: float, thickness: int,
             from_centre: bool = False) -> None:
    """Every progress bar in one style (BAR): a thin line with round ends
    from x0 to x1 centred on y, a faint track, `progress` of it filled in
    orange from the left (or out from the centre both ways)."""
    r = thickness / 2
    a, b, y = int(round(x0 + r)), int(round(x1 - r)), int(round(y))
    if b < a:
        return
    _line_blend(frame, (a, y), (b, y), C.bar_track[:3], thickness, C.bar_track[3] / 255)
    p = min(max(progress, 0.0), 1.0)
    if p <= 0:
        return
    if from_centre:
        c, half = (a + b) / 2, (b - a) / 2 * p
        a, b = int(round(c - half)), int(round(c + half))
    else:
        b = int(round(a + (b - a) * p))
    _line_blend(frame, (a, y), (b, y), C.bar_fill[:3], thickness, C.bar_fill[3] / 255)


def _replay_ui(h: int) -> int:
    return max(TEXT.ui_min_size, h // TEXT.ui_rows_per_frame)


def draw_replay_bar(frame: np.ndarray, progress: float, marks=(), span: tuple[float, float] | None = None) -> None:
    """Review's replay of a take's video: how far it has got, a bar along the
    bottom (PLAYBAR.replay_*), and where in the clip (`span`, app times) a
    filler or restart (a tick above), a long pause (a line above) or a look
    away (a line below) happened: `marks`, from playback.clip_marks."""
    h, w = frame.shape[:2]
    inset = PLAYBAR.replay_inset * w
    x0, x1, y = inset, w - inset, h - PLAYBAR.replay_bottom
    t = bar_thickness(_replay_ui(h))
    draw_bar(frame, x0, x1, y, progress, t)
    if not marks or span is None or span[1] <= span[0]:
        return
    at = lambda tt: int(round(x0 + (min(max(tt, span[0]), span[1]) - span[0]) / (span[1] - span[0]) * (x1 - x0)))
    above, below = int(y - t / 2 - PLAYBAR.replay_mark_gap), int(y + t / 2 + PLAYBAR.replay_mark_gap)
    for kind, a, b in marks:
        color = PLAYBAR.replay_marks[kind]
        rgb, alpha = color[:3], color[3] / 255
        if kind in ("filler", "restart"):
            _line_blend(frame, (at(a), above - PLAYBAR.replay_tick), (at(a), above), rgb, 2, alpha)
        elif kind == "pause":
            _line_blend(frame, (at(a), above - 1), (max(at(b), at(a) + 1), above - 1), rgb, 2, alpha)
        else:  # away
            _line_blend(frame, (at(a), below + 1), (max(at(b), at(a) + 1), below + 1), rgb, 3, alpha)


def draw_replay_hint(frame: np.ndarray) -> None:
    """On a replay, top right: that the m key flips the video (mirrored or as others see you)."""
    h, w = frame.shape[:2]
    st = TYPE.small
    f = face(round(_replay_ui(h) * st.scale), SMALL_ON_FILL, st.tracking, st.leading)
    text = PLAYBAR.flip_hint
    pad = max(4, f.size // 3)
    band_w, band_h = int(_text_w(f.font, text, f.tracking)) + 2 * pad, f.line_h + pad
    img = Image.new("RGBA", (band_w, band_h), CLEAR)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, band_w - 1, band_h - 1), radius=pad, fill=PLAYBAR.caption_band)
    _text(draw, (pad, pad // 2 + f.dy), text, f.font, f.tracking, fill=C.dim)
    inset = int(PLAYBAR.replay_inset * w)
    _blend(frame, w - inset - band_w, PLAYBAR.hint_top, *_premultiply(img))


def draw_replay_caption(frame: np.ndarray, words: list[tuple[str, str, bool]]) -> None:
    """Captions: what has been said so far in the replay, one line
    above its bar on a faint band, coloured as in the take player (a filler
    yellow, a restart red, an ad-lib cyan), the word being said on a lighter
    ground; older words scroll off the left. `words`: (text, kind, being said now)."""
    if not words:
        return
    h, w = frame.shape[:2]
    st = TYPE.operation
    f = face(round(_replay_ui(h) * PLAYBAR.caption_scale), "medium", st.tracking, st.leading)
    gap = f.advance
    widths = [_text_w(f.font, text, f.tracking) for text, _, _ in words]
    limit = PLAYBAR.caption_max * w
    first, total = len(words), 0.0  # the newest words that fit
    while first > 0:
        more = widths[first - 1] + (gap if total else 0.0)
        if total + more > limit:
            break
        first, total = first - 1, total + more
    words, widths = words[first:], widths[first:]
    pad = max(4, f.size // 3)
    band_w, band_h = int(total) + 2 * pad, f.line_h + pad
    img = Image.new("RGBA", (band_w, band_h), CLEAR)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, band_w - 1, band_h - 1), radius=pad, fill=PLAYBAR.caption_band)
    x = pad
    for (text, kind, current), width in zip(words, widths):
        if current:
            draw.rounded_rectangle((x - 2, 2, x + width + 1, band_h - 3), radius=3, fill=PLAYER.caption_current)
        _text(draw, (x, pad // 2 + f.dy), text, f.font, f.tracking,
              fill=PLAYER.caption.get(kind, PLAYER.caption["word"]))
        x += width + gap
    y = h - PLAYBAR.replay_bottom - PLAYBAR.caption_above - band_h
    _blend(frame, int((w - band_w) / 2), int(y), *_premultiply(img))


def draw_fingertips(frame: np.ndarray, state: GestureState) -> None:
    """Yellow dot on the active fingertip, small dots on the rest; cyan for a
    second hand. A curled finger's dot is smaller and faint, so the dots show
    the shape the hand makes from the first frame it is seen; while that shape
    doesn't count yet (TIMING.stable_s) the active dot is a ring, filled once it does."""
    for track, color in ((state.secondary, C.cyan), (state.primary, C.yellow)):
        if track is None or track.hand is None:
            continue
        f = track.feat
        out = {} if f is None else {THUMB_TIP: f.thumb_out, **dict(zip(FINGER_TIPS, f.extended))}
        pending = track.raw != track.stable
        for i in TIPS:
            c = tuple(int(v) for v in track.hand.points[i])
            if i == INDEX_TIP and pending:
                cv2.circle(frame, c, HANDS.active_tip_r, bgr(color), HANDS.pending_ring_w, cv2.LINE_AA)
            elif not out.get(i, True):
                _dot_blend(frame, c, HANDS.curled_tip_r, color, HANDS.curled_alpha)
            else:
                cv2.circle(frame, c, HANDS.active_tip_r if i == INDEX_TIP else HANDS.tip_r, bgr(color), -1,
                           cv2.LINE_AA)


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


def _text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, tracking: float = 0.0, **kw) -> None:
    """`text` with its top of ascent at xy; with `tracking` (px between
    letters) glyph by glyph at a fixed advance: the font is monospace (Pillow
    has no letter spacing). Characters the font lacks come from TEXT.fallback,
    in the same cell on the same baseline."""
    covered = _covered(font.path)
    missing = any(ord(ch) not in covered for ch in text if not ch.isspace())
    if not tracking and not missing:
        draw.text(xy, text, font=font, **kw)
        return
    step = font.getlength("M") + tracking
    base = xy[1] + font.getmetrics()[0]
    fallback = load_font(font.size, "fallback") if missing else font
    for i, ch in enumerate(text):
        if not ch.isspace():
            draw.text((xy[0] + i * step, base), ch, font=font if ord(ch) in covered else fallback,
                      anchor="ls", **kw)


def _text_w(font, text: str, tracking: float = 0.0) -> float:
    """Width of `text` with `tracking` px between letters."""
    return font.getlength(text) + tracking * max(0, len(text) - 1)


def _outline(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, tracking: float = 0.0) -> None:
    """The dark outline of `text`, on its own layer under the text (so a
    neighbouring word's outline never covers a glyph): OUTLINE.width px of
    Pillow's stroke in Colors.outline."""
    if OUTLINE.width > 0:
        _text(draw, xy, text, font, tracking, fill=C.outline, stroke_width=OUTLINE.width, stroke_fill=C.outline)


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


def _dot_blend(frame: np.ndarray, c: tuple[int, int], r: int, color, alpha: float) -> None:
    """A filled dot blended at `alpha`; only the patch around it is touched."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = max(0, c[0] - r - 1), max(0, c[1] - r - 1), min(w, c[0] + r + 2), min(h, c[1] + r + 2)
    if x0 >= x1 or y0 >= y1:
        return
    roi = frame[y0:y1, x0:x1]
    over = roi.copy()
    cv2.circle(over, (c[0] - x0, c[1] - y0), r, bgr(color), -1, cv2.LINE_AA)
    roi[:] = cv2.addWeighted(over, alpha, roi, 1 - alpha, 0)


def _window(color: np.ndarray, inv: np.ndarray, off: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Rows off..off+h of a patch; clear where that runs past either end."""
    if 0 <= off and off + h <= color.shape[0]:
        return color[off:off + h], inv[off:off + h]
    out_c = np.zeros((h, *color.shape[1:]), color.dtype)
    out_i = np.ones((h, *inv.shape[1:]), inv.dtype)
    a, b = max(0, off), min(color.shape[0], off + h)
    if b > a:
        out_c[a - off:b - off], out_i[a - off:b - off] = color[a:b], inv[a:b]
    return out_c, out_i


def _poly_blend(frame: np.ndarray, pts: np.ndarray, color, stroke: int, alpha: float) -> None:
    """An open polyline blended at `alpha`; only the patch around it is touched."""
    if alpha >= 1.0:
        cv2.polylines(frame, [pts], False, bgr(color), stroke, cv2.LINE_AA)
        return
    if alpha <= 0.0:
        return
    h, w = frame.shape[:2]
    pad = stroke + 2
    x0, y0 = max(0, int(pts[:, 0].min()) - pad), max(0, int(pts[:, 1].min()) - pad)
    x1, y1 = min(w, int(pts[:, 0].max()) + pad + 1), min(h, int(pts[:, 1].max()) + pad + 1)
    if x0 >= x1 or y0 >= y1:
        return
    roi = frame[y0:y1, x0:x1]
    over = roi.copy()
    cv2.polylines(over, [pts - np.array([x0, y0], np.int32)], False, bgr(color), stroke, cv2.LINE_AA)
    roi[:] = cv2.addWeighted(over, alpha, roi, 1 - alpha, 0)


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
