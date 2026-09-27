"""How the app looks: every colour, font, size, spacing and position the
drawing code (palmcards/render.py) uses, in one place.

Colours are RGB, or RGBA where they are drawn with Pillow and blended;
OpenCV calls convert them with bgr(). Sizes that grow with the text are
multiples of the notes' size or of their line height ("lines"), or of the UI
size (labels, hints, pills: `TEXT.ui_rows_per_frame`); the rest are pixels. Positions are fractions of the frame unless
they say px.

The frame is split into three vertical zones (`LAYOUT`): the text column on
the left holds everything the user reads, nothing is drawn over the face in
the middle, and the hand zone on the right holds only what belongs to the
hand. Where the hand box and the command zone sit is behaviour, not style:
the gesture code hit-tests against them, so they stay in palmcards/config.py.
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
    # Text, Kat's style: off-white notes; the unit under the hand on an orange
    # fill in dark text, the rest of its paragraph on a slate-blue fill, one
    # tight fill per row; an orange bar beside the paragraph. Without a hand
    # the current sentence is orange. Dimmed while something else has focus.
    text: RGBA = (238, 238, 238, 245)
    unit_fill: RGBA = (255, 140, 0, 235)
    unit_text: RGBA = (20, 20, 20, 255)
    context_fill: RGBA = (62, 74, 122, 165)
    orange: RGBA = (255, 140, 0, 255)
    # Highlighted text (the current sentence, the focused word): a little
    # deeper than the fills' orange, so it holds up on a bright wall.
    orange_text: RGBA = (240, 112, 0, 255)
    orange_soft: RGBA = (255, 140, 0, 200)  # the label's second line: the operation
    # Dimmed text is white made see-through, not grey: grey vanishes on a pale wall.
    dim: RGBA = (255, 255, 255, 179)  # Rehearse: the section's other sentences; a focused word's sentence
    faint: RGBA = (255, 255, 255, 60)  # context around a focused unit
    focus_text: RGBA = (245, 245, 245, 255)  # enlarged unit in the focus panel
    # While the options ring is open the text stays readable, dimmed, under the bubble map.
    ring_sentence: RGBA = (255, 255, 255, 179)
    ring_context: RGBA = (255, 255, 255, 95)
    # A soft dark halo around every glyph instead of a box behind the text:
    # the video stays visible between the lines. Its alpha is the halo's strength.
    shadow: RGBA = (0, 0, 0, 110)
    # A dark outline round every glyph drawn straight on the video (its width: OUTLINE).
    outline: RGBA = (0, 0, 0, 150)
    hint: RGBA = (255, 140, 0, 150)  # the label's third line: the gesture hint
    # Chips and nodes.
    chip_fill: RGBA = (255, 140, 0, 235)  # word under the cursor, picked ring node
    chip_text: RGBA = (20, 20, 20, 255)
    dark_fill: RGBA = (15, 15, 18, 215)
    node_text: RGBA = (240, 240, 240, 255)
    node_outline: RGBA = (240, 240, 240, 200)
    node_outline_dim: RGBA = (240, 240, 240, 90)  # the ring's action node (hear it)
    node_fill: RGBA = (12, 14, 34, 235)  # the options ring's nodes: Kat's navy boxes
    connector: RGB = (255, 196, 40)  # the options ring's spokes
    # Review.
    detail_text: RGBA = (235, 235, 235, 235)  # the takes' lines under a focused sentence
    # Persistent alert line (recording or analysis trouble) and the panel's scrollbar.
    alert_fill: RGBA = (170, 40, 30, 230)
    label_fill: RGBA = (10, 10, 12, 190)  # behind the bottom-left pills
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
    hand_area: RGB = (235, 235, 235)  # the hand box's corners while a hand is up (blended, HANDS.area_alpha)
    debug_text: RGB = (255, 255, 255)


@dataclass(frozen=True)
class Layout:
    """The frame's three vertical zones, as fractions of its width. `hand`
    only documents where the hand box is (CURSOR.hand_box in config.py)."""
    text: tuple[float, float] = (0.06, 0.36)  # labels, notes, ring, gauge, Review's take chips
    face: tuple[float, float] = (0.36, 0.55)  # nothing drawn here but fingertip dots
    hand: tuple[float, float] = (0.55, 0.95)  # hand box, command zone, their hints, Review's take table, the tutorial


@dataclass(frozen=True)
class Scrim:
    """Kat's dark left side: the video is darkened from the frame's left
    edge, fully up to `full`, then fading out to nothing by the text
    column's right edge (LAYOUT.text), so nothing darkens the face.

    How dark follows the room: every `sample_every` frames the camera image
    under the text column is measured (the `percentile` of its brightness,
    every `sample_step` px), mapped from `dark`..`bright` onto
    `alpha_min`..`alpha_max`, and the scrim moves `smooth` of the way there,
    so it settles in about a second without flickering. Strong on a white
    wall, faint in a dark room. alpha_max 0 turns it off."""
    alpha_min: float = 0.15  # darkest room: a faint scrim
    alpha_max: float = 0.72  # white wall
    dark: float = 0.20  # brightness 0..1 at or below which the scrim is alpha_min
    bright: float = 0.80  # ... at or above which it is alpha_max
    percentile: float = 75  # of the pixels' brightness: bright patches under the text count
    sample_every: int = 5  # frames
    sample_step: int = 8  # px between sampled pixels
    smooth: float = 0.15  # share of the way to the new level per sample
    full: float = 0.12  # x frame width


@dataclass(frozen=True)
class Fill:
    """The tight fills behind a highlighted unit's rows, and the bar beside its paragraph."""
    pad_x: int = 2  # px beyond the row's text
    bar_w: int = 2  # px
    bar_x: int = 1  # px from the text box's left edge


@dataclass(frozen=True)
class Outline:
    """The dark outline round text drawn straight on the video (Pillow's
    stroke; colour and opacity: Colors.outline). Text on a fill has none."""
    width: int = 2  # px; 0 turns it off


@dataclass(frozen=True)
class Shadow:
    """The halo behind text (colour and strength: Colors.shadow)."""
    blur: float = 0.06  # x the text size: the halo's Gaussian radius
    gain: float = 2.0  # the blurred alpha is scaled by this, so thin strokes still cast a solid halo
    gamma: float = 0.5  # the text's alpha to this power first: dim text keeps more of the halo than its own opacity


@dataclass(frozen=True)
class Text:
    # Shipped with the app (Menlo, the old default, is derived from it); licence beside it.
    font: Path = FONTS_DIR / "DejaVuSansMono.ttf"
    # The notes: frame height / rows_per_frame (20 px at 720p, as big as in
    # Kat's frames: 12 px a character), made smaller (not below min_size)
    # only if a row of the text column would hold fewer than min_columns.
    rows_per_frame: int = 36
    min_columns: int = 28
    min_size: int = 12  # px
    # Everything else the user reads (labels, pills, hints, the take table): frame height / this.
    ui_rows_per_frame: int = 28
    ui_min_size: int = 16  # px
    line_spacing: float = 1.0  # line height, x the font's own: Kat's rows are packed (24 px at 720p)
    # The text box fills the text column (LAYOUT.text): as many columns as
    # fit, and from under the label down to the pills as many rows as fit
    # (None), like Kat's block down the whole left side.
    visible_rows: int | None = None
    # A focused word: the notes zoomed by this, the word moved to the middle of the box.
    word_zoom: float = 1.6
    meaning_min_size: int = 9  # smallest definition text in a narrow window; shrink to fit below the word
    preview_anchor: float = 0.2  # fixed starting row for live rewrites, fraction of the text viewport
    fade: float = 0.5  # lines: rows partly scrolled out fade out over this at the box's top and bottom
    # Everything below scales with the line height: padding inside the box is half a line.
    focus_scales: tuple[float, ...] = (1.4, 1.2, 1.0)  # focus panel: largest that fits the box wins
    focus_min_columns: int = 16  # ... with at least this many columns (Review's take chips narrow the panel)
    panel_max_h: float = 0.9  # a tall focus panel grows up to this share of the frame
    word_box_h: float = 0.8  # lines: a word's box, whose middle its chip is centred on
    # A focused panel too tall for the frame turns its pages by itself, so its
    # last lines are reachable without keys; a key pauses that for a while.
    page_s: float = 5.0
    page_pause_s: float = 12.0


@dataclass(frozen=True)
class Chips:
    """Text on a rounded rectangle: word chips, ring nodes, pills. Scales
    are x the notes' size (word chip, ring nodes) or x the UI size (the rest)."""
    pad_x: tuple[int, int] = (4, 4)  # (min px, text size // this)
    pad_y: tuple[int, int] = (2, 8)
    radius: tuple[int, int] = (3, 5)
    hover_scale: float = 1.0  # x the notes' size: word under the cursor
    symbol_scale: float = 0.7  # x the UI size: the panel's more markers


@dataclass(frozen=True)
class Label:
    """Kat's state label above the text box, no box: the state, the
    operation (dimmer), the gesture hint (smallest, dimmest). Each wraps to
    the text column, up to max_rows rows. Scales x the UI size."""
    first_scale: float = 0.9
    second_scale: float = 0.7
    third_scale: float = 0.6
    max_rows: tuple[int, int, int] = (1, 2, 2)
    min_top: int = 4  # px from the frame's top edge
    pill_scale: float = 0.6  # the pills at the bottom left (CLOUD LLM, H: KEYS) and the keys help


@dataclass(frozen=True)
class Detail:
    """Review: a line per take under the focused sentence."""
    scale: float = 0.9  # x the notes' size and line height
    min_columns: int = 10
    indent: str = "    "  # wrapped continuation lines (past the line's "▸ " marker)


@dataclass(frozen=True)
class Summary:
    """Review, while browsing: the take table (the last few full takes side by side), bottom right."""
    scale: float = 0.6  # x the UI size
    right: int = 16  # px from the frame's right edge
    bottom: int = 16  # px from the frame's bottom edge
    gap: int = 0  # px between lines: one block


@dataclass(frozen=True)
class Ring:
    """Options ring around a focused word, Kat's bubble map: nodes on an
    ellipse round the word (pulled into the text column, pushed apart where
    they overlap), short curved spokes from the word's edge to each node's."""
    rx: float = 4.4  # lines of the zoomed text (TEXT.word_zoom)
    ry: float = 3.4
    node_scale: float = 0.8  # x the zoomed text's size
    node_pad: tuple[float, float] = (0.45, 0.22)  # x the node's text size
    node_radius: int = 1  # px: nearly square
    node_outline_w: int = 2  # px
    node_gap: int = 6  # px kept between nodes, and between a node and the word
    relax_steps: int = 30
    edge_px: int = 8  # kept inside the frame's left edge and the text column's right
    spoke_gap: int = 4  # px between a spoke's ends and the word or node
    bow: float = 0.12  # how far the spokes bow to one side
    curve_points: int = 16
    stroke: int = 3
    closing_box: int = 3  # px: the box around the picked node while the thumb closes into a pinch
    # Review's take chips: a column at the text column's right edge.
    take_scale: float = 0.6  # x the UI size
    take_pitch: float = 0.142  # x the text box's height, between chip centres (pointing at them depends on it)
    take_gap: int = 8  # px between the chips and the panel's text


@dataclass(frozen=True)
class Gauge:
    """Vertical tone dial, in the text box's right padding."""
    min_lines: float = 3.0  # track height in note line heights, sized to the focused sentence
    max_lines: float = 7.0
    sentence_pad: float = 0.5  # extra line height around the sentence
    step_px: int = 2
    width: int = 2
    knob_r: int = 6
    knob_outline: int = 2
    closing_knob_outline: int = 3  # while the thumb closes into a pinch: the value is held
    label_scale: float = 0.55  # x the UI size: the ends' labels, cold above, warm below
    labels: tuple[str, str] = ("formal", "conversational")


@dataclass(frozen=True)
class Zone:
    """Command zone contents (the zone's place is REHEARSE.zone in config.py):
    corner marks like the hand box, hints as plain lines. Scales x the UI size."""
    stroke: int = 1
    active_stroke: int = 2
    corner: float = 0.1  # the corner marks' arms, x the zone's width
    alpha: float = 0.45  # the corner marks' opacity; full while a hand is in the zone
    inset_right: int = 2  # px, so the outline's right edge stays on screen
    inset_top: int = 1
    hint_scale: float = 0.55
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
    scale: float = 5  # x the UI size
    y: float = 0.65  # centred in the hand zone (LAYOUT.hand), below the command zone, at this height


@dataclass(frozen=True)
class Hands:
    tip_r: int = 4  # fingertip dots
    active_tip_r: int = 9  # index fingertip
    stretch_stroke: int = 2  # line between the two index tips, drawn under the text
    closing_stretch_stroke: int = 5  # ... while a thumb closes into a pinch: the length is held
    stretch_alpha: float = 0.45
    # The hand box's corners while a hand is up: small and faint.
    area_arm: float = 0.06  # x the box's width
    area_min_arm: int = 8  # px
    area_stroke: int = 1
    area_alpha: float = 0.3
    # Debug.
    landmark_stroke: int = 2
    landmark_r: int = 3
    box_stroke: int = 1
    cursor_r: int = 5


@dataclass(frozen=True)
class Debug:
    """OpenCV text: the stats line, and the gesture debug view's readout."""
    show_stats: bool = False  # the frame rate and latency line (hidden unless debugging)
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
    caption_scale: float = 0.8  # x the UI size
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
LAYOUT = Layout()
SCRIM = Scrim()
FILL = Fill()
SHADOW = Shadow()
OUTLINE = Outline()
# Preferences > high contrast (key c): dimmed text much brighter, context
# readable, a stronger halo; the highlight stays orange.
HIGH_CONTRAST = replace(COLORS, text=(255, 255, 255, 255), context_fill=(50, 62, 115, 215),
                        dim=(255, 255, 255, 215), faint=(255, 255, 255, 130),
                        ring_sentence=(255, 255, 255, 215), ring_context=(255, 255, 255, 55),
                        shadow=(0, 0, 0, 240), outline=(0, 0, 0, 220), hint=(255, 140, 0, 200), orange_soft=(255, 140, 0, 220),
                        label_fill=(0, 0, 0, 230), detail_text=(255, 255, 255, 255),
                        node_outline_dim=(245, 245, 245, 160))
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
