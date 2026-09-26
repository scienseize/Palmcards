"""How the app looks: every colour, font, size, spacing and position the
drawing code (palmcards/render.py) uses, in one place.

Colours are RGB, or RGBA where they are drawn with Pillow and blended;
OpenCV calls convert them with bgr(). Sizes that grow with the text are
multiples of the text size (`TEXT.size`, "x text") or of its line height
("lines"); the rest are pixels. Positions are fractions of the frame unless
they say px.

Where the hand box and the command zone sit is behaviour, not style: the
gesture code hit-tests against them, so they stay in palmcards/config.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


def bgr(c: RGB | RGBA) -> tuple[int, int, int]:
    """An RGB(A) colour as OpenCV's BGR."""
    return c[2], c[1], c[0]


@dataclass(frozen=True)
class Colors:
    # Text: the highlighted unit, the rest dimmed, context fainter still; marks a shade dimmer.
    orange: RGBA = (255, 140, 0, 255)
    orange_mark: RGBA = (255, 140, 0, 150)
    dim: RGBA = (220, 220, 220, 110)
    dim_mark: RGBA = (220, 220, 220, 60)
    faint: RGBA = (220, 220, 220, 45)
    faint_mark: RGBA = (220, 220, 220, 25)
    focus_text: RGBA = (245, 245, 245, 255)  # enlarged unit in the focus panel
    focus_mark: RGBA = (245, 245, 245, 140)
    suggest_mark: RGBA = (255, 215, 0, 95)  # a mark the LLM suggests, not yet in the notes: faded yellow
    accepted_mark: RGBA = (255, 215, 0, 245)  # a suggestion accepted with a pinch: solid yellow
    pick_outline: RGBA = (255, 215, 0, 255)  # around the suggestion the knob is on (thicker while pinching)
    backing: RGBA = (10, 10, 12, 150)  # soft dark box behind the text
    # Chips and nodes.
    chip_fill: RGBA = (255, 140, 0, 235)  # word under the cursor, picked ring node
    chip_text: RGBA = (20, 20, 20, 255)
    dark_fill: RGBA = (15, 15, 18, 215)
    node_text: RGBA = (240, 240, 240, 255)
    node_outline: RGBA = (240, 240, 240, 200)
    # Review.
    verdict: dict[str, RGB] = field(default_factory=lambda: {
        "hit": (95, 205, 115), "missed": (240, 90, 75), "unclear": (160, 160, 160)})
    detail_text: RGBA = (235, 235, 235, 235)  # verdict lines under a focused sentence
    # Persistent alert line (recording or analysis trouble) and the panel's scrollbar.
    alert_fill: RGBA = (170, 40, 30, 230)
    label_fill: RGBA = (10, 10, 12, 190)  # behind the state label and small hints, for busy scenes
    alert_text: RGBA = (255, 255, 255, 255)
    scroll_track: RGB = (90, 90, 90)
    scroll_thumb: RGB = (235, 235, 235)
    # Accents drawn with OpenCV.
    yellow: RGB = (255, 215, 0)  # active fingertip, ring connectors, active zone, progress
    cyan: RGB = (0, 230, 255)  # second hand's fingertips
    cold: RGB = (60, 150, 255)  # tone gauge, top
    warm: RGB = (255, 140, 0)  # tone gauge, bottom
    knob_fill: RGB = (20, 20, 20)
    knob_outline: RGB = (245, 245, 245)
    stretch_line: RGB = (245, 245, 245)
    zone: RGB = (170, 170, 170)
    rec: RGB = (230, 60, 60)  # recording dot
    # Debug drawings (`d` key, python -m palmcards.gestures).
    landmark_line: RGB = (200, 200, 200)
    landmark_point: RGB = (255, 255, 255)
    debug_box: RGB = (160, 160, 160)  # hand box, zone outline, cursor ring
    debug_band: RGB = (100, 100, 100)  # hand box scroll bands
    stats: RGB = (200, 200, 200)
    hand_area: RGB = (140, 140, 140)  # the hand box, drawn faintly while a hand is up
    debug_text: RGB = (255, 255, 255)


@dataclass(frozen=True)
class Text:
    # Shipped with the app (Menlo, the old default, is derived from it); licence beside it.
    font: Path = FONTS_DIR / "DejaVuSansMono.ttf"
    rows_per_frame: int = 26  # text size = frame height / this ...
    min_size: int = 18  # ... but at least this, px
    line_spacing: float = 1.45  # line height, x the font's own
    columns: int = 34
    visible_rows: int = 5
    left: float = 0.05  # text box's left edge; it is centred vertically
    # Everything below scales with the line height: padding inside the box is
    # half a line, the backing's blur half that, the margin around it twice the blur.
    focus_scales: tuple[float, ...] = (1.3, 1.15, 1.0)  # focus panel: largest that fits the box wins
    panel_max_h: float = 0.9  # a tall focus panel grows up to this share of the frame
    word_box_h: float = 0.8  # lines: a word's box, whose middle its chip is centred on
    # A focused panel too tall for the frame turns its pages by itself, so its
    # last lines are reachable without keys; a key pauses that for a while.
    page_s: float = 5.0
    page_pause_s: float = 12.0


@dataclass(frozen=True)
class Chips:
    """Text on a rounded rectangle: labels, word chips, ring nodes."""
    pad_x: tuple[int, int] = (4, 4)  # (min px, text size // this)
    pad_y: tuple[int, int] = (2, 8)
    radius: tuple[int, int] = (3, 5)
    hover_scale: float = 1.0  # x text: word under the cursor
    focus_scale: float = 1.3  # x text: the focused word
    # Verdict chips on marks: padding (min px, text size // this), and an
    # opacity that follows the words around them (x their alpha, clamped).
    verdict_pad: tuple[int, int] = (2, 8)
    verdict_alpha_gain: float = 2
    verdict_alpha: tuple[int, int] = (70, 235)
    symbol_scale: float = 0.7  # x text: verdict symbols (✓ ✗ ? –) and the panel's more markers


@dataclass(frozen=True)
class Label:
    """Kat's two-line state label, above the text box."""
    first_scale: float = 0.9  # x text
    second_scale: float = 0.7
    min_top: int = 4  # px from the frame's top edge


@dataclass(frozen=True)
class Detail:
    """Review: verdict lines under the focused sentence."""
    scale: float = 0.7  # x text and x line height
    min_columns: int = 10
    indent: str = "  "  # wrapped continuation lines
    bullet_y: float = 0.45  # of a detail line, where the verdict dot sits


@dataclass(frozen=True)
class Summary:
    """Review, while browsing: the latest take's summary card, bottom right."""
    scale: float = 0.6  # x text
    right: int = 16  # px from the frame's right edge
    bottom: int = 16  # px from the frame's bottom edge
    gap: int = 4  # px between lines


@dataclass(frozen=True)
class Ring:
    """The options of a focused word: a row over it, with curved connectors."""
    row_dy: float = 1.9  # lines above (or below) the word
    row_gap: int = 14  # px between options
    node_scale: float = 0.85  # x text
    edge_px: int = 8  # kept clear of the frame's sides
    bow: float = 0.25  # how far the curved connectors bow to one side
    curve_points: int = 16
    stroke: int = 1
    picked_stroke: int = 2
    closing_box: int = 3  # px: the box around the picked node while the thumb closes into a pinch


@dataclass(frozen=True)
class Gauge:
    """Vertical tone dial, right of the text box."""
    step_px: int = 2
    width: int = 4
    knob_r: int = 8
    knob_outline: int = 2
    closing_knob_outline: int = 4  # while the thumb closes into a pinch: the value is held


@dataclass(frozen=True)
class Zone:
    """Command zone contents (the zone's place is REHEARSE.zone in config.py)."""
    stroke: int = 1
    active_stroke: int = 2
    inset_right: int = 2  # px, so the outline's right edge stays on screen
    inset_top: int = 1
    hint_scale: float = 0.6  # x text
    hint_gap: int = 4  # px between hints
    rec_scale: float = 0.75
    rec_dx: int = 10  # px right of the zone's centre
    mic_dx: int = 14  # px left of the REC chip
    mic_r: tuple[int, int] = (4, 8)  # (radius when silent, growth at full level)
    flick_dy: int = 30  # px above the zone's bottom
    flick_inset: int = 12
    flick_stroke: int = 1
    flick_fill: int = 4
    hold_inset: int = 6  # hold bar: inset from the zone's sides, px
    hold_top: int = 12  # px above the zone's bottom
    hold_bottom: int = 6


@dataclass(frozen=True)
class CountIn:
    scale: float = 5  # x text
    y: float = 0.65  # centred between the text box and the right edge, at this height


@dataclass(frozen=True)
class Hands:
    tip_r: int = 4  # fingertip dots
    active_tip_r: int = 9  # index fingertip
    stretch_stroke: int = 2  # line between the two index tips
    closing_stretch_stroke: int = 5  # ... while a thumb closes into a pinch: the length is held
    # Debug.
    landmark_stroke: int = 2
    landmark_r: int = 3
    box_stroke: int = 1
    cursor_r: int = 5


@dataclass(frozen=True)
class Debug:
    """OpenCV text: the stats line, and the gesture debug view's readout."""
    stats_from_right: int = 560  # px
    stats_from_bottom: int = 20
    stats_scale: float = 0.6
    stats_thickness: int = 1
    text_x: int = 20
    text_y: int = 32
    text_dy: int = 26
    text_scale: float = 0.6
    text_thickness: int = 2


@dataclass(frozen=True)
class Player:
    """The take player's window (python -m palmcards.player)."""
    size: tuple[int, int] = (1280, 720)
    background: RGB = (24, 24, 28)
    # Caption of what Whisper heard, coloured by what the aligner made of it.
    caption: dict[str, RGBA] = field(default_factory=lambda: {
        "word": (240, 240, 240, 255),
        "filler": (255, 215, 0, 255),
        "restart": (255, 90, 90, 255),
        "extra": (80, 220, 255, 255),
        "unsure": (150, 150, 150, 200),
    })
    caption_current: RGBA = (60, 60, 60, 230)  # behind the word being said
    caption_scale: float = 0.8  # x text
    caption_y: float = 0.83
    caption_gap: int = 4  # px between words
    caption_right: int = 20  # px kept clear at the right
    # Strip: the whole take along the bottom.
    strip_x: int = 40  # px inset from each side
    strip_top: int = 44  # px above the bottom
    strip_bottom: int = 24
    strip_frame: RGB = (70, 70, 70)
    status: dict[str, RGB] = field(default_factory=lambda: {"spoken": (90, 190, 90), "partial": (255, 140, 0)})
    status_other: RGB = (90, 90, 90)
    span_inset: int = 3
    section: RGB = (200, 200, 200)
    section_over: tuple[int, int] = (8, 4)  # px the section line sticks out above, below
    filler: RGB = (255, 215, 0)
    restart: RGB = (255, 90, 90)
    tick_h: int = 6
    tick_stroke: int = 2
    playhead: RGB = (255, 255, 255)
    playhead_over: int = 6
    playhead_stroke: int = 2


COLORS = Colors()
# Preferences > high contrast (key c): dimmed text much brighter, context
# readable, a darker backing; the highlight stays orange.
HIGH_CONTRAST = replace(COLORS, dim=(245, 245, 245, 200), dim_mark=(245, 245, 245, 150), faint=(235, 235, 235, 130),
                        faint_mark=(235, 235, 235, 100), backing=(0, 0, 0, 215), label_fill=(0, 0, 0, 230),
                        focus_mark=(255, 255, 255, 200), detail_text=(255, 255, 255, 255),
                        suggest_mark=(255, 215, 0, 170))
TEXT = Text()
CHIPS = Chips()
LABEL = Label()
DETAIL = Detail()
RING = Ring()
GAUGE = Gauge()
ZONE = Zone()
COUNT_IN = CountIn()
SUMMARY = Summary()
HANDS = Hands()
DEBUG = Debug()
PLAYER = Player()
