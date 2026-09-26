"""Pillow text overlay composited onto the mirrored frame.

Demo style: monospace block floating beside the user, the unit under the
cursor in orange, the rest dimmed, a few lines visible, soft dark backing
behind it. Delivery marks are drawn as written, a shade dimmer than the words.

The layout is a grid of monospace cells, so every word has a known row and
column range; hit_test() maps a point back to (sentence, word), and
cursor_to_text() maps the relative hand-box cursor to such a point.

On top of the text: the two-line state label (Kat's `BROWSE BY WORD`), and
Prepare's operations: the options ring (the original word, its alternatives
when the optional LLM is on, stress and "hear it"; an L-hand turned like a
knob picks one), and the tone gauge and
stretch line; without the LLM these two only preview, and say so. An open
palm on a focused sentence spreads the LLM's suggested marks in it, faded
(style COLORS.suggest_mark), where they would go; the one the L-hand knob is
on is outlined, and the ones accepted with a pinch are solid. While the
cloud LLM is on, a CLOUD LLM chip sits at the bottom left ("SENDING" while a
request is out).

In Rehearse the current section is shown in the focus panel, the current
sentence in orange and the rest dimmed, with the command zone (top right),
a recording clock and microphone level, and a large 3-2-1 during the
count-in. A drill shows just its one sentence.

The focus panel is laid out whole and shown through a viewport that never
covers the label and never runs off the frame: a long section or a long
list of verdicts scrolls (`ViewState.panel_scroll`, pixels), with a
scrollbar and more-above/below markers. Words too long for a row are split
across rows.

In Review each delivery mark is drawn as a chip coloured by its verdict in
the take that sentence shows (green hit, red missed, grey unclear; a skipped
mark stays faint text), with a symbol beside it (✓ ✗ ? –) so the verdict
does not rest on colour alone, and a focused sentence lists its verdicts
under it.

A persistent alert line (recording or analysis trouble) sits under the text,
apart from the label's transient hints; `h` shows the keyboard fallback.

Also here, so that every visual is drawn in one module: the fingertip dots,
the debug drawings (landmarks, hand box, zone outline, text readouts), the
stats line, and the take player's caption and timeline strip. How all of it
looks (colours, fonts, sizes, spacing, positions) is in palmcards/style.py.
"""

from __future__ import annotations

import math
import re
import textwrap
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from palmcards.config import CURSOR, REHEARSE
from palmcards.gestures import HAND_CONNECTIONS, INDEX_TIP, TIPS
from palmcards.notes import MarkKind, Sentence, reading_place
from palmcards.style import (
    CHIPS, COLORS, COUNT_IN, SUMMARY, DEBUG, DETAIL, GAUGE, HANDS, HIGH_CONTRAST, LABEL, PLAYER, RING, TEXT, ZONE, bgr,
)

if TYPE_CHECKING:
    from palmcards.gestures import CommandZone, GestureState, Hand, RelativeCursor

C = COLORS
CLEAR = (0, 0, 0, 0)


def set_contrast(high: bool) -> None:
    """High-contrast colours (preferences); build a new TextOverlay afterwards."""
    global C
    C = HIGH_CONTRAST if high else COLORS
PAUSE_TEXT = {MarkKind.SHORT_PAUSE: "/", MarkKind.LONG_PAUSE: "//"}
BAND_SLACK_ROWS = 6  # rows rendered beyond the window on each side
CHIP_CACHE_MAX = 256  # the recording clock makes a new chip every second
HEAR_IT, STRESS, UNSTRESS = "hear it", "stress", "unstress"  # ring nodes after the original word (and alternatives)
SCRAMBLE = "abcdefghijklmnopqrstuvwxyz#%&@$"
FOCUS_HINTS = {  # without the optional LLM
    "word": "OPEN PALM: STRESS, HEAR IT  /  DROP HAND: BACK",
    "sentence": "L-HAND, THEN TILT: TONE (PREVIEW ONLY)  /  DROP HAND: BACK",
    "paragraph": "TWO L-HANDS: LENGTH (PREVIEW ONLY)  /  DROP HAND: BACK",
}
FOCUS_HINTS_LLM = {
    "word": "OPEN PALM: ALTERNATIVES, STRESS, HEAR IT  /  DROP HAND: BACK",
    "sentence": "OPEN PALM: SUGGEST MARKS  /  L-HAND: TONE  /  DROP HAND: BACK",
    "paragraph": "TWO L-HANDS: LENGTH  /  DROP HAND: BACK",
}
NEEDS_LLM = "(PREVIEW ONLY: NEEDS THE OPTIONAL LLM)"
VERDICT_SYMBOL = {"hit": "✓", "missed": "✗", "unclear": "?", "skipped": "–"}
KEYS_HELP = (
    "KEYS (WHEN GESTURES WON'T DO)",
    "T  START A TAKE      X  STOP / CANCEL",
    "N B  NEXT / PREVIOUS SECTION     U  UNDO EDIT",
    "J K  NEXT / PREVIOUS SENTENCE, OR SCROLL",
    "A  PLAY SENTENCE     P  BACK TO PREPARE",
    "M  SUGGEST MARKS (FOCUSED SENTENCE)",
    "E  CALIBRATE EYES AT THE NEXT TAKE",
    "R  RETRY ANALYSIS    H  HIDE    Q  QUIT",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    if not TEXT.font.exists():
        raise FileNotFoundError(f"font missing: {TEXT.font} (it ships in the repo; restore it with git)")
    return ImageFont.truetype(TEXT.font, size)


@dataclass(frozen=True)
class Hit:
    sentence: int
    word: int | None  # None: sentence/paragraph level, or between words


@dataclass
class OpsView:
    """What the operation stubs show; filled from the gesture state."""

    kind: str | None = None  # ring | tone | marks | stretch
    picked: int = 0  # ring node, 0 = original word, clockwise from the top
    pointing: bool = False
    tone: float = 0.0  # -1 cold .. 1 warm
    stretch: float = 1.0
    stretch_ends: tuple[tuple[float, float], tuple[float, float]] | None = None
    closing: bool = False  # the thumb is closing into a pinch: the dial is held where it was, drawn bolder


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
    # Review: per sentence, the verdict of each mark (Sentence.marks order) in
    # the take the sentence shows, "" where there is none.
    mark_verdicts: tuple[tuple[str, ...], ...] = ()
    detail: tuple[tuple[str, str], ...] = ()  # Review focus: (verdict or "", line) under the sentence
    summary: tuple[str, ...] = ()  # Review, browsing: the latest take's summary card (palmcards.review)
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
    loading: bool = False
    proposal: str = ""
    llm: str = ""  # the optional LLM in use: "cloud" (the text asked about leaves the Mac), "local", or ""
    llm_busy: bool = False  # a request is out
    # Prepare, marks spread on a focused sentence: "off" (no LLM), "asking",
    # "failed", "empty" or "ready"; when ready, the sentence with every
    # suggestion added, and per mark of it "suggested" or "" (as written).
    suggest: str = ""
    suggest_sentence: Sentence | None = None
    suggest_marks: tuple[str, ...] = ()  # per mark of suggest_sentence: "suggested", "accepted" or ""
    suggest_current: int | None = None  # the mark (of suggest_sentence) pointed at: outlined
    # Review, a focused sentence: the takes that said it, as chips beside it to
    # point at (an L, then the fingertip), and which one it shows.
    takes: tuple[str, ...] = ()
    take_shown: int = 0
    point_at: tuple[float, float] | None = None  # choosing by pointing (ring, marks): where the point is
    tutorial: tuple[int, int, str] | None = None  # (step, of, what to do) on the first run, or after g


@dataclass
class Panel:
    """The focus panel laid out whole; drawn through a viewport."""
    height: int  # of the content, px
    color: np.ndarray
    inv: np.ndarray
    rows: dict[int, tuple[int, int]]  # sentence -> (top, bottom) of its enlarged rows, px into the content
    marks: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)  # suggested mark -> box


@dataclass(frozen=True)
class Span:
    text: str
    role: str  # "word" | "mark" | "punct"
    word: int | None = None
    mark: int | None = None  # index into Sentence.marks, for marks
    badge: bool = True  # carries its mark's verdict symbol (a stress mark's opening * does not)


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
                units.append([Span("*", "mark", mark=mi, badge=False), word, Span("*", "mark", mark=mi)])
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


def mark_label(s: Sentence, i: int) -> str:
    """A mark of the sentence in a few words for the label: [SLOW], *YOU*, // BEFORE "BEING"."""
    m = s.marks[i]
    if m.kind in (MarkKind.SHORT_PAUSE, MarkKind.LONG_PAUSE):
        where = f'BEFORE "{s.words[m.word].text.upper()}"' if m.word < len(s.words) else "AT THE END"
        return f"{PAUSE_TEXT[m.kind]} {where}"
    if m.kind is MarkKind.STRESS:
        return f"*{s.words[m.word].text.upper()}*"
    return f"[{m.kind.upper()}]"


def _mark_index(s: Sentence, kind: MarkKind) -> int | None:
    return next((i for i, m in enumerate(s.marks) if m.kind is kind), None)


def _split(unit: list[Span], columns: int) -> list[list[Span]]:
    """A unit wider than a row, cut into row-wide pieces; each piece keeps
    its spans' word and mark, so hit-testing still finds the word."""
    pieces, piece, width = [], [], 0
    for sp in unit:
        text = sp.text
        while text:
            take = min(len(text), columns - width)
            piece.append(Span(text[:take], sp.role, sp.word, sp.mark, sp.badge and len(text) == take))
            text, width = text[take:], width + take
            if width == columns:
                pieces.append(piece)
                piece, width = [], 0
    if piece:
        pieces.append(piece)
    return pieces


def layout(sentences: list[Sentence], columns: int) -> list[Row]:
    """Greedy wrap; each sentence starts on a new row."""
    rows: list[Row] = []
    for si, s in enumerate(sentences):
        row = Row(si, [])
        col = 0
        units = []
        for unit in sentence_units(s):
            units += _split(unit, columns) if sum(len(sp.text) for sp in unit) > columns else [unit]
        for unit in units:
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
        columns: int = TEXT.columns,
        visible_rows: int = TEXT.visible_rows,
    ):
        self.sentences = sentences
        self.visible_rows = visible_rows
        self.frame_w, self.frame_h = w, h = frame_size
        self.font_size = max(TEXT.min_size, h // TEXT.rows_per_frame)
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
                    self._word_pos.setdefault(Hit(r.sentence, sp.word), (i, col, len(sp.text)))

        ascent, descent = self.font.getmetrics()
        self.line_h = int((ascent + descent) * TEXT.line_spacing)
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
        self.x = max(0, int(w * TEXT.left) - self.margin)
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
            second = "EXPLORE ALTERNATIVES: LOADING" if state.loading else "L-HAND, THEN POINT TO PICK"
            if ops.pointing:
                picked = self.ring_labels(state)[ops.picked]
                word = self.word_text(state.focus).upper() if state.focus and state.focus.word is not None else ""
                second = {HEAR_IT: "PINCH + LIFT: HEAR IT", STRESS: f'PINCH + LIFT: STRESS "{word}"',
                          UNSTRESS: f'PINCH + LIFT: UNSTRESS "{word}"'}.get(picked)
                if second is None:
                    second = "KEEP THE WORD (NO CHANGE)" if ops.picked == 0 else f'PINCH + LIFT: USE "{picked.upper()}"'
        elif ops.kind == "tone":
            tone = "WARM" if ops.tone > 0.15 else "COLD" if ops.tone < -0.15 else "NEUTRAL"
            second = f"SENTENCE TONE: {tone}  " + ("/  PINCH + LIFT: ASK FOR A REWRITE" if state.llm else NEEDS_LLM)
        elif ops.kind == "marks":
            second = {
                "off": "MARK SUGGESTIONS NEED THE OPTIONAL LLM: NOTHING SENT",
                "asking": "SUGGESTING MARKS" + "." * (int(time.time() * 2) % 4),
                "failed": "NO SUGGESTIONS  /  DROP HAND, FOCUS AGAIN TO RETRY",
                "empty": "NO NEW MARKS SUGGESTED  /  DROP HAND: BACK",
            }.get(state.suggest, "")
            if state.suggest == "ready":
                second = self._pick_line(state)
        elif ops.kind == "stretch":
            change = "LONGER" if ops.stretch > 1.05 else "SHORTER" if ops.stretch < 0.95 else "SAME"
            second = f"PARAGRAPH LENGTH: {change}  x{ops.stretch:.2f}  " + \
                ("/  PINCH + LIFT: ASK FOR A REWRITE" if state.llm else NEEDS_LLM)
        elif state.mode == "focus" and state.app == "prepare" and state.proposal:
            second = "PINCH + LIFT: USE THE PROPOSAL  /  DROP HAND: DISCARD IT"
        elif state.mode == "focus" and state.app == "prepare":  # nothing started yet: say what the next shape does
            second = (FOCUS_HINTS_LLM if state.llm else FOCUS_HINTS).get(state.level, "")
        elif state.mode == "focus":
            second = state.status or "DROP HAND: BACK"
        else:
            second = state.status
        return first, second

    def _pick_line(self, state: ViewState) -> str:
        """Spread marks: which one the knob is on, whether it is accepted, what a
        pinch and a pinch + lift would do."""
        s = state.suggest_sentence
        picks = sorted((i for i, st in enumerate(state.suggest_marks) if st),
                       key=lambda i: reading_place(s.marks[i], len(s.words)))  # the knob's order
        if not picks:
            return ""
        accepted = sum(1 for st in state.suggest_marks if st == "accepted")
        cur = state.suggest_current if state.suggest_current in picks else picks[0]
        on = state.suggest_marks[cur] == "accepted"
        where = f"{picks.index(cur) + 1}/{len(picks)} {mark_label(state.suggest_sentence, cur)}"
        commit = f"PINCH + LIFT: ADD {accepted}" if accepted else "L-HAND, THEN POINT: ANOTHER"
        return f"{where}: {'ACCEPTED' if on else 'NOT ACCEPTED'}  /  PINCH: {'REJECT' if on else 'ACCEPT'}  /  {commit}"

    def ring_labels(self, state: ViewState) -> tuple[str, ...]:
        """The word as it is, stress (or unstress) it, hear it. Word alternatives
        need the optional LLM and are not offered without one."""
        if state.focus is None or state.focus.word is None:
            return ("original", HEAR_IT)
        stressed = any(m.kind == MarkKind.STRESS and m.word == state.focus.word
                       for m in self.sentences[state.focus.sentence].marks)
        return (self.word_text(state.focus), *state.alternatives, UNSTRESS if stressed else STRESS, HEAR_IT)

    # --- drawing: text -----------------------------------------------------

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        if size not in self._fonts:
            self._fonts[size] = load_font(size)
        return self._fonts[size]

    def _backing(self, box_h: int) -> tuple[np.ndarray, np.ndarray]:
        if box_h not in self._backings:
            img = Image.new("RGBA", (self.patch_w, box_h + 2 * self.margin), CLEAR)
            m = self.margin
            ImageDraw.Draw(img).rounded_rectangle(
                (m, m, m + self.box_w, m + box_h), radius=self.pad, fill=C.backing
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
        elif verdict in C.verdict:
            x0, y0, x1, y1 = draw.textbbox(xy, sp.text, font=font)
            pad = max(CHIPS.verdict_pad[0], font.size // CHIPS.verdict_pad[1])
            lo, hi = CHIPS.verdict_alpha  # as prominent as the words around it
            alpha = max(lo, min(hi, word_c[3] * CHIPS.verdict_alpha_gain))
            draw.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=pad + 1,
                                   fill=(*C.verdict[verdict], alpha))
            draw.text(xy, sp.text, font=font, fill=(*C.chip_text[:3], alpha))
            if sp.badge:
                self._draw_symbol(draw, verdict, x1 + pad, y0 - pad, font, (*C.verdict[verdict], alpha))
        elif verdict == "skipped":
            draw.text(xy, sp.text, font=font, fill=C.faint_mark)
            if sp.badge:
                x0, y0, x1, _ = draw.textbbox(xy, sp.text, font=font)
                self._draw_symbol(draw, verdict, x1, y0, font, C.faint_mark)
        else:
            draw.text(xy, sp.text, font=font, fill=mark_c)

    def _draw_symbol(self, draw: ImageDraw.ImageDraw, verdict: str, x: float, y: float, font, fill) -> None:
        """The verdict's symbol on a small badge at the chip's top right corner,
        so the verdict reads without its colour, on any background."""
        small = self._get_font(max(8, round(font.size * CHIPS.symbol_scale)))
        r = small.size * 0.6
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill[:3] + (max(fill[3], 200),))
        draw.text((x, y), VERDICT_SYMBOL[verdict], font=small, fill=C.chip_text, anchor="mm")

    def _render_band(self, style: tuple, start: int) -> tuple[np.ndarray, np.ndarray]:
        """Rows start..start+band_rows, with row `start` at y = pad."""
        band_h = self.band_rows * self.line_h + 2 * self.pad
        text = Image.new("RGBA", (self.box_w, band_h), CLEAR)
        draw = ImageDraw.Draw(text)
        kind, which, verdicts = style
        for ri in range(start, min(len(self.rows), start + self.band_rows)):
            row = self.rows[ri]
            y = self.pad + (ri - start) * self.line_h
            if kind == "focus":  # word focus: its sentence dim, the rest fainter
                word_c, mark_c = (C.dim, C.dim_mark) if row.sentence == which else (C.faint, C.faint_mark)
            else:
                word_c, mark_c = (C.orange, C.orange_mark) if row.sentence in which else (C.dim, C.dim_mark)
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

    def _focus_panel(self, unit: tuple[int, ...], detail: tuple[tuple[str, str], ...] = (),
                     verdicts: tuple = (), current: int | None = None, preview_from: int | None = None,
                     suggest: tuple | None = None) -> Panel:
        """The unit's sentences enlarged, then the detail lines (Review's
        verdicts), faint context rows around them when there is room.
        With `suggest` (sentence, that sentence with the suggested marks added,
        each mark's state, the outlined mark), it is drawn that way: the
        suggestions faded, the accepted ones solid, the outlined one boxed.

        Laid out whole: when it is taller than the viewport, draw() shows a
        scrolled part of it. With `current`, that sentence is orange and the
        rest of the unit dimmed (Rehearse); sentences from `preview_from` on
        (the next section, previewed) are faint."""
        key = (unit, detail, verdicts, current, preview_from, suggest)
        if self._panel_key == key:
            return self._panel
        sents = [suggest[1] if suggest and i == suggest[0] else self.sentences[i] for i in unit]
        states = suggest[2] if suggest else ()
        dfont = self._get_font(round(self.font_size * DETAIL.scale))
        dlh = round(self.line_h * DETAIL.scale)
        bullet = dlh // 2
        dcols = max(DETAIL.min_columns, int((self.box_w - 2 * self.pad - bullet * 2) / dfont.getlength("M")))
        dlines = []
        for verdict, line in detail:
            text = f"{VERDICT_SYMBOL[verdict]} {line}" if verdict in VERDICT_SYMBOL else line
            dlines += [(verdict if k == 0 else "", piece)
                       for k, piece in enumerate(textwrap.wrap(text, dcols, subsequent_indent=DETAIL.indent) or [""])]
        detail_h = len(dlines) * dlh + (self.pad // 2 if dlines else 0)
        avail = self.max_panel_h - 2 * self.pad - detail_h
        for scale in TEXT.focus_scales:  # the largest size that fits the viewport; never smaller than normal
            font = self._get_font(round(self.font_size * scale))
            lh = round(self.line_h * scale)
            cw = font.getlength("M")
            rows = layout(sents, int((self.box_w - 2 * self.pad) / cw))
            if len(rows) * lh <= avail:
                break
        big_h = len(rows) * lh
        panel_h = max(self.box_h, big_h + detail_h + 2 * self.pad)
        spare = panel_h - 2 * self.pad - big_h - detail_h
        first, last = self._first_row[unit[0]], self._last_row[unit[-1]]
        above = self.rows[max(0, first - int(spare / 2 // self.line_h)) : first]
        below = self.rows[last + 1 : last + 1 + int((spare - len(above) * self.line_h) // self.line_h)]
        content = (len(above) + len(below)) * self.line_h + big_h + detail_h
        y = self.pad + max(0, (panel_h - 2 * self.pad - content) // 2)

        img = Image.new("RGBA", (self.box_w, panel_h), CLEAR)
        draw = ImageDraw.Draw(img)
        where: dict[int, tuple[int, int]] = {}
        mark_boxes: dict[int, tuple[float, float, float, float]] = {}

        def draw_rows(rows_, font_, lh_, cw_, colors, y_, sentence_of=lambda r: r.sentence):
            for row in rows_:
                si = sentence_of(row)
                word_c, mark_c = colors(si)
                picked = None  # the outlined suggestion's box on this row
                for col, sp in row.spans:
                    mc, xy = mark_c, (self.pad + col * cw_, y_)
                    if suggest and si == suggest[0] and sp.mark is not None and sp.mark < len(states) \
                            and states[sp.mark]:
                        mc = C.accepted_mark if states[sp.mark] == "accepted" else C.suggest_mark
                        box = draw.textbbox(xy, sp.text, font=font_)
                        was = mark_boxes.get(sp.mark)
                        mark_boxes[sp.mark] = box if was is None else (min(was[0], box[0]), min(was[1], box[1]),
                                                                       max(was[2], box[2]), max(was[3], box[3]))
                        if sp.mark == suggest[3]:
                            picked = box if picked is None else (min(picked[0], box[0]), min(picked[1], box[1]),
                                                                 max(picked[2], box[2]), max(picked[3], box[3]))
                    self._draw_span(draw, xy, sp, font_, word_c, mc, self._verdict(verdicts, si, sp))
                if picked is not None:
                    pad = max(3, font_.size // 6)
                    draw.rounded_rectangle((picked[0] - pad, picked[1] - pad, picked[2] + pad, picked[3] + pad),
                                           radius=pad + 1, outline=C.pick_outline, width=4 if suggest[4] else 2)
                top, bottom = where.get(si, (y_, y_))
                where[si] = (min(top, y_), max(bottom, y_ + lh_))
                y_ += lh_
            return y_

        def unit_colors(si):
            if current is not None and si == current:  # orange, even in the previewed section
                return C.orange, C.orange_mark
            if preview_from is not None and si >= preview_from:
                return C.faint, C.faint_mark
            if current is None:
                return C.focus_text, C.focus_mark
            return (C.orange, C.orange_mark) if si == current else (C.dim, C.dim_mark)

        y = draw_rows(above, self.font, self.line_h, self.char_w, lambda si: (C.faint, C.faint_mark), y)
        where.clear()  # context rows are not the unit
        # The enlarged rows were laid out afresh: their sentence numbers count from the unit's first.
        y = draw_rows(rows, font, lh, cw, unit_colors, y, lambda r: unit[r.sentence])
        rows_y = dict(where)
        if dlines:
            y += self.pad // 2
            for verdict, piece in dlines:
                if verdict in C.verdict:
                    cy = y + dlh * DETAIL.bullet_y
                    draw.ellipse((self.pad, cy - bullet / 3, self.pad + bullet * 2 / 3, cy + bullet / 3),
                                 fill=(*C.verdict[verdict], 255))
                draw.text((self.pad + bullet, y), piece, font=dfont, fill=C.detail_text)
                y += dlh
        draw_rows(below, self.font, self.line_h, self.char_w, lambda si: (C.faint, C.faint_mark), y)
        color, inv = _premultiply(img)
        self._panel_key, self._panel = key, Panel(panel_h, color, inv, rows_y, mark_boxes)
        return self._panel

    # --- the panel's viewport ------------------------------------------------

    @property
    def label_h(self) -> int:
        """Height the two-line label needs above the text."""
        big, small = round(self.font_size * LABEL.first_scale), round(self.font_size * LABEL.second_scale)
        return self._chip("X", big, C.orange, C.label_fill)[0].shape[0] + \
            self._chip("X", small, C.orange_mark, C.label_fill)[0].shape[0]

    @property
    def max_panel_h(self) -> int:
        """Tallest the panel's viewport gets: below the label, above the alert line."""
        room = self.frame_h - (LABEL.min_top + self.label_h + self.pad // 2) - self.line_h - self.pad
        return max(self.box_h, min(int(self.frame_h * TEXT.panel_max_h), room))

    def panel(self, state: ViewState) -> Panel | None:
        unit = self._panel_unit(state)
        if unit is None:
            return None
        suggest = (state.focus.sentence, state.suggest_sentence, state.suggest_marks, state.suggest_current,
                   state.ops.closing) if state.suggest_sentence is not None and state.focus is not None else None
        return self._focus_panel(unit, state.detail, state.mark_verdicts, self._panel_current(state, unit),
                                 self._preview_from(state, unit), suggest)

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

    def _draw_scrollbar(self, frame: np.ndarray, top: int, view_h: int, scroll: float, height: int) -> None:
        x = int(self.x + self.margin + self.box_w - self.pad // 3)
        cv2.line(frame, (x, top + self.pad), (x, top + view_h - self.pad), bgr(C.scroll_track), 2, cv2.LINE_AA)
        span = view_h - 2 * self.pad
        y0 = top + self.pad + int(span * scroll / height)
        y1 = top + self.pad + int(span * (scroll + view_h) / height)
        cv2.line(frame, (x, y0), (x, y1), bgr(C.scroll_thumb), 4, cv2.LINE_AA)
        size = round(self.font_size * CHIPS.symbol_scale)
        cx = self.x + self.margin + self.box_w / 2
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

    def _blend_centered(self, frame: np.ndarray, chip, cx: float, cy: float) -> tuple[int, int, int, int]:
        color, inv = chip
        h, w = color.shape[:2]
        x, y = int(cx - w / 2), int(cy - h / 2)
        _blend(frame, x, y, color, inv)
        return x, y, x + w, y + h

    # --- drawing: HUD and operation stubs ----------------------------------

    def _draw_label(self, frame: np.ndarray, state: ViewState, top: int) -> None:
        first, second = self.label_lines(state)
        big, small = round(self.font_size * LABEL.first_scale), round(self.font_size * LABEL.second_scale)
        c1 = self._chip(first, big, C.orange, C.label_fill)
        c2 = self._chip(second, small, C.orange_mark, C.label_fill) if second else None
        h = c1[0].shape[0] + (c2[0].shape[0] if c2 else 0)
        x, y = self.x + self.margin + self.pad // 2, max(LABEL.min_top, top - h - self.pad // 2)
        _blend(frame, x, y, *c1)
        if c2:
            _blend(frame, x, y + c1[0].shape[0], *c2)

    def ring_nodes(self, state: ViewState) -> list[tuple[float, float]]:
        """Where each of the focused word's options sits on screen (ring_labels
        order): around the word, clockwise from the top, shifted so every
        node stays on screen. Empty when the word is not on screen."""
        if state.focus is None or state.focus.word is None or (box := self.word_box(state.focus, state.scroll)) is None:
            return []
        labels = self.ring_labels(state)
        n = len(labels)
        rx, ry = RING.rx * self.line_h, RING.ry * self.line_h
        size = round(self.font_size * RING.node_scale)
        widest = max(self._chip(label, size, C.node_text, C.dark_fill, C.node_outline)[0].shape[1] for label in labels)
        edge = rx + widest / 2 + RING.edge_px
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        rcx = min(max(cx, edge), self.frame_w - edge)
        rcy = min(max(cy, ry + self.line_h), self.frame_h - ry - self.line_h)
        out = []
        for i in range(n):
            a = math.radians(-90 + i * 360 / n)
            out.append((rcx + rx * math.cos(a), rcy + ry * math.sin(a)))
        return out

    def _panel_origin(self, state: ViewState) -> tuple[Panel, float, float, int] | None:
        """The focus panel, where its content's (0, 0) is on screen, and its viewport's top."""
        panel = self.panel(state)
        if panel is None:
            return None
        view_h = self.panel_view_h(panel)
        top = self._panel_top(view_h)
        return panel, self.x + self.margin, top - int(self.clamp_panel_scroll(state, state.panel_scroll)), top

    def mark_points(self, state: ViewState) -> dict[int, tuple[float, float]]:
        """Where each suggested mark of the focused sentence is on screen (by its
        index among suggest_sentence's marks)."""
        got = self._panel_origin(state)
        if got is None:
            return {}
        panel, ox, oy, _ = got
        return {m: (ox + (b[0] + b[2]) / 2, oy + (b[1] + b[3]) / 2) for m, b in panel.marks.items()}

    def _take_chips(self, state: ViewState) -> list[tuple[tuple[np.ndarray, np.ndarray], float, float]]:
        """Review: the focused sentence's takes as chips in a column right of the
        panel, from its top: (chip, centre x, centre y) each."""
        got = self._panel_origin(state) if state.takes else None
        if got is None:
            return []
        size = round(self.font_size * RING.node_scale)
        x0, y = self.x + self.margin + self.box_w + self.pad, got[3] + self.pad
        out = []
        for i, label in enumerate(state.takes):
            chip = self._chip(label, size, C.chip_text, C.chip_fill) if i == state.take_shown \
                else self._chip(label, size, C.node_text, C.dark_fill, C.node_outline)
            h, w = chip[0].shape[:2]
            out.append((chip, x0 + w / 2, y + h / 2))
            y += h + RING.take_gap
        return out

    def take_points(self, state: ViewState) -> list[tuple[float, float]]:
        return [(x, y) for _, x, y in self._take_chips(state)]

    def _draw_takes(self, frame: np.ndarray, state: ViewState) -> None:
        for i, (chip, x, y) in enumerate(self._take_chips(state)):
            box = self._blend_centered(frame, chip, x, y)
            if i == state.take_shown and state.ops.closing:
                g = RING.closing_box
                cv2.rectangle(frame, (box[0] - g, box[1] - g), (box[2] + g, box[3] + g), bgr(C.yellow), g, cv2.LINE_AA)

    def _draw_ring(self, frame: np.ndarray, state: ViewState, center: tuple[float, float]) -> None:
        labels = self.ring_labels(state)
        size = round(self.font_size * RING.node_scale)
        cx, cy = center
        for i, (label, (nx, ny)) in enumerate(zip(labels, self.ring_nodes(state))):
            # Curved connector: quadratic Bezier bowed to one side.
            mx, my = (cx + nx) / 2, (cy + ny) / 2
            ctrl = (mx - (ny - cy) * RING.bow, my + (nx - cx) * RING.bow)
            ts = np.linspace(0, 1, RING.curve_points)[:, None]
            curve = (1 - ts) ** 2 * np.array(center) + 2 * (1 - ts) * ts * np.array(ctrl) + ts ** 2 * np.array((nx, ny))
            stroke = RING.picked_stroke if i == state.ops.picked else RING.stroke
            cv2.polylines(frame, [curve.astype(np.int32)], False, bgr(C.yellow), stroke, cv2.LINE_AA)
            if i == state.ops.picked:
                chip = self._chip(label, size, C.chip_text, C.chip_fill)
            else:
                chip = self._chip(label, size, C.node_text, C.dark_fill, C.node_outline)
            box = self._blend_centered(frame, chip, nx, ny)
            if i == state.ops.picked and state.ops.closing:  # the pinch will take this node: it is held
                g = RING.closing_box
                cv2.rectangle(frame, (box[0] - g, box[1] - g), (box[2] + g, box[3] + g), bgr(C.yellow), g, cv2.LINE_AA)

    def _draw_gauge(self, frame: np.ndarray, tone: float, top: int, bottom: int, closing: bool = False) -> None:
        """Vertical tone dial: cold (blue) at the top, warm (orange) at the bottom."""
        x = int(self.x + self.margin + self.box_w + self.pad)
        y0, y1 = top + self.pad, bottom - self.pad
        for y in range(y0, y1, GAUGE.step_px):
            k = (y - y0) / max(1, y1 - y0)
            color = tuple(int(c * (1 - k) + w * k) for c, w in zip(bgr(C.cold), bgr(C.warm)))
            cv2.line(frame, (x, y), (x, y + 1), color, GAUGE.width)
        ky = int(y0 + (tone + 1) / 2 * (y1 - y0))
        cv2.circle(frame, (x, ky), GAUGE.knob_r, bgr(C.knob_fill), -1, cv2.LINE_AA)
        cv2.circle(frame, (x, ky), GAUGE.knob_r, bgr(C.knob_outline),
                   GAUGE.closing_knob_outline if closing else GAUGE.knob_outline, cv2.LINE_AA)

    def _draw_zone(self, frame: np.ndarray, state: ViewState) -> None:
        """Command zone: outline, recording clock and mic level, hints, flick and hold progress."""
        zx0, zy0, zx1, zy1 = REHEARSE.zone
        x0, y0 = int(zx0 * self.frame_w), int(zy0 * self.frame_h)
        x1, y1 = int(zx1 * self.frame_w) - ZONE.inset_right, int(zy1 * self.frame_h)
        active = state.zone_active
        cv2.rectangle(frame, (x0, y0 + ZONE.inset_top), (x1, y1), bgr(C.yellow if active else C.zone),
                      ZONE.active_stroke if active else ZONE.stroke, cv2.LINE_AA)

        size = round(self.font_size * ZONE.hint_scale)
        cx, y = (x0 + x1) / 2, y0 + self.pad
        if state.app == "rehearse":
            m, s = divmod(int(state.rec_s), 60)
            chip = self._chip(f"REC {m}:{s:02d}", round(self.font_size * ZONE.rec_scale), C.node_text, C.dark_fill)
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
        for text in hints:
            chip = self._chip(text, size, C.node_text if active else C.dim, C.dark_fill)
            y = self._blend_centered(frame, chip, cx, y + chip[0].shape[0] / 2)[3] + ZONE.hint_gap

        if state.app == "rehearse":
            # Flick meter: fills as the hand swings sideways, so a near miss shows.
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

    def draw(self, frame: np.ndarray, state: ViewState) -> np.ndarray:
        """Composite the overlay onto `frame` in place and return it."""
        box_top = self.y + self.margin
        top, bottom = box_top, box_top + self.box_h
        panel = self.panel(state)
        if panel is not None:
            view_h = self.panel_view_h(panel)
            scroll = int(self.clamp_panel_scroll(state, state.panel_scroll))
            top = self._panel_top(view_h)
            bottom = top + view_h
            _blend(frame, self.x, top - self.margin, *self._backing(view_h))
            _blend(frame, self.x + self.margin, top, panel.color[scroll:scroll + view_h],
                   panel.inv[scroll:scroll + view_h])
            if panel.height > view_h:
                self._draw_scrollbar(frame, top, view_h, scroll, panel.height)
        else:
            _blend(frame, self.x, self.y, *self._backing(self.box_h))
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
        if state.mode == "focus" and state.level == "word" and state.focus and state.focus.word is not None:
            if (box := self.word_box(state.focus, state.scroll)) is not None:
                center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                if state.ops.kind == "ring":
                    self._draw_ring(frame, state, center)
                text = self.ring_labels(state)[state.ops.picked] if state.ops.kind == "ring" else self.word_text(state.focus)
                if state.loading:  # glyph scramble: the word is being rewritten
                    text = "".join(SCRAMBLE[(ord(c) + int(time.time() * 12)) % len(SCRAMBLE)] if c.isalpha() else c
                                   for c in text)
                chip = self._chip(text, round(self.font_size * CHIPS.focus_scale), C.orange, C.dark_fill)
                self._blend_centered(frame, chip, *center)

        if state.point_at is not None and state.mode == "focus":  # the pointer, so a move shows at once
            x, y = (int(v) for v in state.point_at)
            cv2.circle(frame, (x, y), HANDS.point_r, bgr(C.yellow), -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), HANDS.point_r + 2, bgr(C.knob_outline), 1, cv2.LINE_AA)
        if state.mode == "focus" and state.ops.kind == "tone":
            self._draw_gauge(frame, state.ops.tone, top, bottom, state.ops.closing)
        if state.mode == "focus" and state.ops.stretch_ends is not None:
            a, b = (tuple(int(v) for v in p) for p in state.ops.stretch_ends)
            cv2.line(frame, a, b, bgr(C.stretch_line),
                     HANDS.closing_stretch_stroke if state.ops.closing else HANDS.stretch_stroke, cv2.LINE_AA)
        if state.app in ("count_in", "rehearse", "review"):
            self._draw_zone(frame, state)
        if state.app == "review" and state.summary and state.mode != "focus":
            self._draw_summary(frame, state.summary)
        if state.app == "review" and state.mode == "focus" and state.takes:
            self._draw_takes(frame, state)
        if state.app == "count_in" and state.count_in > 0 and state.calibration is None:
            # In the clear space between the notes and the right edge, below the zone.
            chip = self._chip(str(state.count_in), round(self.font_size * COUNT_IN.scale), C.orange, None)
            right = self.x + self.margin + self.box_w
            self._blend_centered(frame, chip, (right + self.frame_w) / 2, self.frame_h * COUNT_IN.y)
        self._draw_label(frame, state, top)
        if state.alert:
            size = round(self.font_size * LABEL.second_scale)
            chip = self._chip(state.alert, size, C.alert_text, C.alert_fill)
            _blend(frame, self.x + self.margin, min(bottom + self.pad // 2, self.frame_h - chip[0].shape[0]), *chip)
        if state.tutorial is not None:
            self._draw_tutorial(frame, *state.tutorial)
        # Bottom left: the CLOUD LLM chip whenever the text asked about can leave the Mac, then the keys.
        x, low = self.x + self.margin, self.frame_h - LABEL.min_top
        size = round(self.font_size * ZONE.hint_scale)
        if state.llm == "cloud":
            text, fg = ("CLOUD LLM: SENDING", C.orange) if state.llm_busy else ("CLOUD LLM", C.dim)
            chip = self._chip(text, size, fg, C.label_fill)
            _blend(frame, x, low - chip[0].shape[0], *chip)
            if state.keys_help:
                low -= chip[0].shape[0]
            else:
                x += chip[0].shape[1] + self.pad // 2
        if state.keys_help:
            self._draw_keys(frame, low)
        elif state.app in ("prepare", "count_in", "rehearse", "review"):
            chip = self._chip("H: KEYS", size, C.dim, C.label_fill)
            _blend(frame, x, low - chip[0].shape[0], *chip)
        return frame

    def _draw_summary(self, frame: np.ndarray, lines: tuple[str, ...]) -> None:
        """The take's summary card: a line per chip, right-aligned at the bottom right,
        clear of the text box; the first line (the take) in orange."""
        size = round(self.font_size * SUMMARY.scale)
        chips = [self._chip(t, size, C.orange if i == 0 else C.node_text, C.dark_fill) for i, t in enumerate(lines)]
        y = self.frame_h - SUMMARY.bottom - sum(c[0].shape[0] + SUMMARY.gap for c in chips)
        clear = self.x + self.margin + self.box_w + self.pad
        for color, inv in chips:
            h, w = color.shape[:2]
            _blend(frame, max(self.frame_w - SUMMARY.right - w, clear), y, color, inv)
            y += h + SUMMARY.gap

    def _draw_tutorial(self, frame: np.ndarray, step: int, of: int, text: str) -> None:
        """One gesture at a time, bottom centre, with where it is in the steps."""
        size = round(self.font_size * LABEL.second_scale)
        dots = " ".join("●" if k < step else "○" for k in range(of))
        for i, line in enumerate((f"{dots}   ENTER: SKIP  /  G: HIDE", text)):
            chip = self._chip(line, size if i == 0 else round(self.font_size * LABEL.first_scale * 0.8),
                              C.node_text if i == 0 else C.orange, C.dark_fill)
            h = chip[0].shape[0]
            self._blend_centered(frame, chip, self.frame_w / 2, self.frame_h - (2.4 - i * 1.1) * h - LABEL.min_top)

    def _draw_keys(self, frame: np.ndarray, low: int) -> None:
        """The keyboard fallback, bottom left, ending at `low`."""
        size = round(self.font_size * ZONE.hint_scale)
        chips = [self._chip(line, size, C.node_text, C.dark_fill) for line in KEYS_HELP]
        y = low - sum(c[0].shape[0] for c in chips)
        for chip in chips:
            _blend(frame, self.x + self.margin, y, *chip)
            y += chip[0].shape[0]

    # --- drawing: the take player ------------------------------------------

    def draw_caption(self, frame: np.ndarray, words: list[tuple[str, str, bool]]) -> None:
        """A row of word chips under the text, left to right, until the frame's
        edge: (text, kind, being said now), kind as in PLAYER.caption."""
        size = round(self.font_size * PLAYER.caption_scale)
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
    """Where the hand steers the highlight: the hand box, faint, with its corners marked."""
    x0, y0, x1, y1 = (int(v) for v in cursor.box)
    arm = max(10, (x1 - x0) // 10)
    for (x, y), (dx, dy) in (((x0, y0), (1, 1)), ((x1, y0), (-1, 1)), ((x0, y1), (1, -1)), ((x1, y1), (-1, -1))):
        cv2.line(frame, (x, y), (x + dx * arm, y), bgr(C.hand_area), 2, cv2.LINE_AA)
        cv2.line(frame, (x, y), (x, y + dy * arm), bgr(C.hand_area), 2, cv2.LINE_AA)


def draw_zone_outline(frame: np.ndarray, zone: CommandZone) -> None:
    """Debug: the command zone as the gesture code sees it."""
    x0, y0, x1, y1 = (int(v) for v in zone.box)
    cv2.rectangle(frame, (x0, y0), (x1 - 1, y1), bgr(C.yellow if zone.active else C.debug_box), HANDS.box_stroke,
                  cv2.LINE_AA)


def draw_stats(frame: np.ndarray, text: str) -> None:
    """Frame rate and latency, bottom right."""
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
