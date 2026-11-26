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
hand. Where the hand box sits is behaviour, not style: the gesture code
hit-tests against it, so it stays in palmcards/config.py.
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
    # Apple's dark-mode system colours, on glass (GLASS): labels in white at
    # Apple's label opacities (primary, secondary 60%, tertiary 30%), the
    # accent in system orange. The unit under the hand on an orange fill in
    # white, the rest of its paragraph on a faint white fill, an orange bar
    # beside it; with no hand up the current sentence is orange.
    text: RGBA = (255, 255, 255, 240)
    unit_fill: RGBA = (230, 110, 0, 245)  # deeper than system orange: white semibold on it holds 3:1
    unit_text: RGBA = (255, 255, 255, 255)
    context_fill: RGBA = (255, 255, 255, 36)
    orange: RGBA = (255, 159, 10, 255)  # system orange (dark)
    orange_text: RGBA = (255, 159, 10, 255)  # the current sentence, the focused word
    # The label: a title, the status in the accent, the hints secondary
    # (with weight and size: TYPE.label, operation, hint).
    label_state: RGBA = (255, 255, 255, 255)  # what you are doing: "Browsing sentences"
    label_operation: RGBA = (255, 159, 10, 255)  # the status of what you are doing
    label_hint: RGBA = (235, 235, 245, 165)  # what you can do next: secondary label
    # Dimmed text is white made see-through, not grey.
    dim: RGBA = (235, 235, 245, 160)  # secondary: Rehearse's other sentences; a focused word's sentence
    faint: RGBA = (235, 235, 245, 80)  # tertiary: context around a focused unit
    focus_text: RGBA = (245, 245, 245, 255)  # enlarged unit in the focus panel
    # While the options ring is open the text stays readable, dimmed, under the bubble map.
    ring_sentence: RGBA = (255, 255, 255, 150)
    ring_context: RGBA = (255, 255, 255, 70)  # what peeks out between the nodes stays in the background
    # A soft dark halo around every glyph instead of a box behind the text:
    # the video stays visible between the lines. Its alpha is the halo's strength.
    shadow: RGBA = (0, 0, 0, 70)
    # A dark outline round every glyph drawn straight on the video (its width: OUTLINE),
    # as strong as the text is opaque (OUTLINE.gamma), so dimmed text stays dim on a pale wall.
    outline: RGBA = (0, 0, 0, 150)
    # A dark rim under the light marks drawn straight on the video (the hand box's
    # corners, fingertip dots, progress tracks, the scrollbar, the tone knob): on a
    # pale wall they would vanish, in a dark room it doesn't show.
    rim: RGBA = (0, 0, 0, 110)
    # Chips and nodes.
    chip_fill: RGBA = (230, 110, 0, 245)  # word under the cursor, picked ring node
    chip_text: RGBA = (255, 255, 255, 255)
    dark_fill: RGBA = (28, 28, 30, 225)  # system gray 6 (dark): the take table, the keys
    node_text: RGBA = (255, 255, 255, 240)
    node_outline: RGBA = (255, 255, 255, 46)  # a hairline: light catching the edge
    node_fill: RGBA = (44, 44, 46, 240)  # the options ring's nodes: system gray 5 (dark)
    connector: RGB = (255, 159, 10)  # the options ring's spokes, in the accent
    # Review.
    detail_text: RGBA = (235, 235, 235, 235)  # the takes' lines under a focused sentence
    # Persistent alert line (recording or analysis trouble) and the panel's scrollbar.
    alert_fill: RGBA = (215, 0, 21, 235)  # system red, darkened for white text
    label_fill: RGBA = (28, 28, 30, 200)  # behind the bottom-left pills
    alert_text: RGBA = (255, 255, 255, 255)
    scroll_track: RGB = (160, 160, 160)
    # Every progress bar (BAR): a faint white track, filled in orange.
    bar_track: RGBA = (120, 120, 128, 92)  # system fill (dark)
    bar_fill: RGBA = (255, 159, 10, 255)
    scroll_thumb: RGB = (235, 235, 235)
    # Accents drawn with OpenCV.
    yellow: RGB = (255, 214, 10)  # system yellow: the active fingertip
    cyan: RGB = (100, 210, 255)  # system teal: the second hand's fingertips
    cold: RGB = (10, 132, 255)  # system blue: the tone gauge, top
    warm: RGB = (255, 159, 10)  # the tone gauge, bottom
    knob_fill: RGB = (20, 20, 20)
    knob_outline: RGB = (245, 245, 245)
    stretch_line: RGB = (245, 245, 245)
    zone: RGB = (170, 170, 170)
    rec: RGB = (255, 69, 58)  # system red: the recording dot
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
    hand: tuple[float, float] = (0.55, 0.95)  # hand box, REC (Rehearse), Review's take table, the tutorial


@dataclass(frozen=True)
class Scrim:
    """How bright the room is under the text column, which sets how dark the
    glass panels are tinted (GLASS.alpha_min..alpha_max). `full` is unused
    since the glass replaced the dark left side.

    How it is measured: every `sample_every` frames the camera image
    under the text column is measured (the `percentile` of its brightness,
    every `sample_step` px), mapped from `dark`..`bright` onto
    `alpha_min`..`alpha_max`, and the scrim moves `smooth` of the way there,
    so it settles in about a second without flickering. Strong on a white
    wall, faint in a dark room. alpha_max 0 turns it off."""
    alpha_min: float = 0.15  # darkest room: a faint scrim
    alpha_max: float = 0.76  # white wall
    dark: float = 0.20  # brightness 0..1 at or below which the scrim is alpha_min
    bright: float = 0.80  # ... at or above which it is alpha_max
    percentile: float = 75  # of the pixels' brightness: bright patches under the text count
    sample_every: int = 5  # frames
    sample_step: int = 8  # px between sampled pixels
    smooth: float = 0.15  # share of the way to the new level per sample
    full: float = 0.28  # x frame width: most of the text column (it ends at 0.36)


@dataclass(frozen=True)
class Glass:
    """Apple's material under the text: rounded panels of the camera image
    blurred (shrunk by `shrink`, Gaussian `sigma` px there) and tinted dark
    (Colors' `tint`), as dark as the room is bright (SCRIM's sampling, mapped
    onto alpha_min..alpha_max), edged with a hairline of light (`edge`).
    One panel holds the label, one the notes (its column, LAYOUT.text); the
    take table, the tutorial and the pills have their own."""
    tint: RGB = (22, 22, 26)
    alpha_min: float = 0.42  # dark room
    alpha_max: float = 0.78  # white wall
    shrink: int = 6
    sigma: float = 2.5
    radius: float = 0.75  # x the notes' line height
    edge: RGBA = (255, 255, 255, 34)
    pad: float = 0.6  # x the notes' line height: the label panel round its text
    gap: int = 8  # px between the label's panel and the notes'


@dataclass(frozen=True)
class Fill:
    """The tight fills behind a highlighted unit's rows, and the bar beside its paragraph."""
    pad_x: int = 3  # px beyond the row's text
    radius: int = 4  # px: each row's fill is a rounded rectangle
    bar_w: int = 3  # px
    bar_x: int = 1  # px from the text box's left edge


@dataclass(frozen=True)
class Outline:
    """The dark outline round text drawn straight on the video (Pillow's
    stroke; colour and opacity: Colors.outline). Text on a fill has none.
    Its opacity follows the text's: x (text alpha) ** gamma, so faint context
    is not a dark outline round nothing on a pale wall."""
    width: int = 0  # px; 0 turns it off (text sits on glass now)
    gamma: float = 0.75


@dataclass(frozen=True)
class Shadow:
    """The halo behind text (colour and strength: Colors.shadow)."""
    blur: float = 0.06  # x the text size: the halo's Gaussian radius
    gain: float = 2.0  # the blurred alpha is scaled by this, so thin strokes still cast a solid halo
    gamma: float = 0.5  # the text's alpha to this power first: dim text keeps more of the halo than its own opacity


@dataclass(frozen=True)
class TypeStep:
    """A step of the type scale: its size (x the notes' size or x the UI
    size, TEXT), weight (TEXT.fonts), tracking (em between letters) and
    leading (a row's height, em)."""
    base: str  # "notes" | "ui"
    scale: float
    weight: str
    tracking: float
    leading: float
    family: str = "mono"  # "mono": SF Mono, the notes' grid; "ui": SF Pro, the interface (TEXT.fonts)


@dataclass(frozen=True)
class TypeScale:
    """Every piece of text is set in one of these steps. Hierarchy comes from
    size, weight and brightness together (Colors.label_*). Tracking tightens as
    text grows and is slightly positive on the small all-caps labels and hints;
    leading is tight on the enlarged focus text, comfortable on the notes."""
    display: TypeStep = TypeStep("ui", 5.0, "semibold", -0.03, 1.0, "ui")  # the count-in's 3-2-1
    # The focus panel's enlarged unit and a focused word's zoomed notes: x the
    # size the panel fits (TEXT.focus_scales) or TEXT.word_zoom. Its current
    # sentence (the focused unit, Rehearse's orange one) in semibold.
    focus: TypeStep = TypeStep("notes", 1.0, "medium", -0.01, 1.1)
    label: TypeStep = TypeStep("ui", 1.0, "semibold", -0.01, 1.25, "ui")  # the state: BROWSE BY WORD; the tutorial's step
    # The notes (the current sentence and the unit under the hand in semibold);
    # rows 24 px apart at 720p. Review's take lines are these at DETAIL.scale.
    notes: TypeStep = TypeStep("notes", 1.0, "medium", 0.0, 1.2)
    operation: TypeStep = TypeStep("ui", 0.8, "medium", 0.0, 1.25, "ui")  # the label's operation, the alert line, REC
    # The label's gesture hint. Review's short hint (39 characters) must fit a
    # row of the text column at 1080p: at +0.01 em it does (+0.02 em pushes it
    # onto two: Plex is 14 px a character there).
    hint: TypeStep = TypeStep("ui", 0.7, "regular", 0.01, 1.3, "ui")
    # Pills, the keys, the take table, take chips, zone hints, gauge ends, more
    # markers: medium over the video, regular on a solid fill (SMALL_ON_FILL).
    small: TypeStep = TypeStep("ui", 0.62, "medium", 0.01, 1.2, "ui")


SMALL_ON_FILL = "regular"


@dataclass(frozen=True)
class Text:
    # Apple's system fonts, variable, from macOS (not shipped): SF Mono for the
    # notes' grid (every weight the same width), SF Pro for the interface (its
    # optical size follows the text size). Where macOS lacks them, IBM Plex Mono
    # (shipped, licence and sources beside it) stands in for both.
    fonts: dict[str, Path] = field(default_factory=lambda: {
        "mono": Path("/System/Library/Fonts/SFNSMono.ttf"),
        "ui": Path("/System/Library/Fonts/SFNS.ttf"),
    })
    weights: dict[str, int] = field(default_factory=lambda: {"regular": 400, "medium": 510, "semibold": 590,
                                                              "bold": 700})
    plex: dict[str, Path] = field(default_factory=lambda: {
        "regular": FONTS_DIR / "IBMPlexMono-Regular.ttf",
        "medium": FONTS_DIR / "IBMPlexMono-Medium.ttf",
        "semibold": FONTS_DIR / "IBMPlexMono-SemiBold.ttf",
        "bold": FONTS_DIR / "IBMPlexMono-SemiBold.ttf",
    })
    # Characters Plex lacks (▸ ▲ ▼ ● ○, scripts it doesn't cover) come from
    # DejaVu Sans Mono, drawn in the same cell on the same baseline.
    fallback: Path = FONTS_DIR / "DejaVuSansMono.ttf"
    # The notes (TYPE.notes; caps and descenders centred in each row, rows
    # set by its leading, not the font's own ascent and descent): frame
    # height / rows_per_frame (20 px at 720p, as big as in
    # Kat's frames: 12 px a character), made smaller (not below min_size)
    # only if a row of the text column would hold fewer than min_columns.
    rows_per_frame: int = 36
    min_columns: int = 28
    min_size: int = 12  # px
    # The UI size, the base of TYPE's "ui" steps (labels, pills, hints, the take table): frame height / this.
    ui_rows_per_frame: int = 28
    ui_min_size: int = 16  # px
    # The text box fills the text column (LAYOUT.text): as many columns as
    # fit, and from under the label down to the pills as many rows as fit
    # (None), like Kat's block down the whole left side.
    visible_rows: int | None = None
    # A focused word: the notes zoomed by this, the word moved to the middle of the box.
    word_zoom: float = 1.6
    meaning_min_size: int = 9  # smallest definition text in a narrow window; shrink to fit below the word
    preview_anchor: float = 0.2  # fixed starting row for live rewrites, fraction of the text viewport
    # Where the text meets its viewport's top or bottom with more beyond it,
    # it fades out over this many rows (half a row of it in the padding)
    # instead of being cut; at full strength once a row lies beyond, none
    # with nothing beyond (the first row at the top stays crisp).
    edge_fade: float = 1.25
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
    radius: tuple[int, int] = (5, 2)  # (min px, text size // this): nearly a capsule
    hover_scale: float = 1.0  # x the notes' size: word under the cursor


@dataclass(frozen=True)
class Label:
    """Kat's state label above the text box, no box: the state (TYPE.label),
    the operation (TYPE.operation, dimmer), the gesture hint (TYPE.hint,
    smallest, dimmest). Each wraps to the text column, up to max_rows rows.

    Its first row is always at `top` (x the UI size, from the frame's top
    edge), whatever follows it: the room reserved under it holds the state,
    two rows of operation and a row of hint, and a hint's second row fits
    whenever the operation takes one. A label longer than that pushes a
    focused unit's panel down (an edit preview) or, anywhere else, starts
    higher, never above min_top."""
    max_rows: tuple[int, int, int] = (1, 2, 2)
    reserve: tuple[int, int, int] = (1, 2, 1)  # rows reserved under `top`, per line
    top: float = 1.0  # x the UI size: 26 px at 720p
    min_top: int = 4  # px from the frame's top (and the pills' from its bottom)


@dataclass(frozen=True)
class Detail:
    """Review: a line per take under the focused sentence."""
    scale: float = 0.9  # x the notes' size and line height
    leading: float = 1.05  # smaller text, a little more room between its rows
    min_columns: int = 10
    indent: str = "    "  # wrapped continuation lines (past the line's "▸ " marker)


@dataclass(frozen=True)
class Summary:
    """Review, while browsing: the take table (the last few full takes side by
    side), bottom right, in TYPE.small on one dark block."""
    right: int = 16  # px from the frame's right edge
    bottom: int = 16  # px from the frame's bottom edge


@dataclass(frozen=True)
class Ring:
    """Options ring around a focused word, Kat's bubble map: nodes on an
    ellipse round the word (pulled into the text column, pushed apart where
    they overlap), short curved spokes from the word's edge to each node's."""
    rx: float = 4.4  # lines of the zoomed text (TEXT.word_zoom)
    ry: float = 3.4
    node_scale: float = 0.8  # x the zoomed text's size
    node_pad: tuple[float, float] = (0.45, 0.22)  # x the node's text size
    node_radius: int = 7  # px: rounded
    node_outline_w: int = 2  # px
    node_gap: int = 6  # px kept between nodes, and between a node and the word
    relax_steps: int = 30
    edge_px: int = 8  # kept inside the frame's left edge and the text column's right
    spoke_gap: int = 4  # px between a spoke's ends and the word or node
    bow: float = 0.12  # how far the spokes bow to one side
    curve_points: int = 16
    stroke: int = 3
    closing_box: int = 3  # px: the box around the picked node while the thumb closes into a pinch
    # Review's take chips (TYPE.small): a column at the text column's right edge.
    take_pitch: float = 0.142  # x the text box's height, between chip centres (pointing at them depends on it)
    take_gap: int = 8  # px between the chips and the panel's text


@dataclass(frozen=True)
class Bar:
    """Every progress bar (a hold, what plays, the count-in):
    a thin line with round ends, a faint track (Colors.bar_track) filled in
    orange (Colors.bar_fill)."""
    thickness: float = 0.12  # x the UI size (3 px at 720p)
    min_thickness: int = 2  # px
    label_em: float = 6.0  # a hold's bar after the label's text: at most this long, x the operation's size
    label_min_em: float = 3.0  # ... and at least this (the text wraps sooner to leave it room)
    gap_em: float = 0.8  # ... this far after the text
    count_em: float = 0.5  # the count-in's bar under its digit: this far below it, x the UI size


@dataclass(frozen=True)
class Playbar:
    """What plays (a take's clip): a bar (BAR) under the focused unit;
    while a take's video replays, the only thing drawn: a bar along the bottom."""
    replay_inset: float = 0.06  # x the frame width, from each side
    replay_bottom: int = 24  # px from the frame's bottom edge to the bar's centre
    # What the replay's bar marks (playback.clip_marks), in the take player's
    # colours: a filler or a restart as a tick above the bar, a long pause as
    # a line above it for its length, looking away as a line below it.
    replay_marks: dict[str, RGBA] = field(default_factory=lambda: {
        "filler": (255, 215, 0, 255),
        "restart": (255, 90, 90, 255),
        "pause": (255, 255, 255, 170),
        "away": (60, 150, 255, 235),
    })
    replay_tick: int = 9  # px: a filler's or a restart's tick
    replay_mark_gap: int = 4  # px between the bar and a mark
    # Captions (always, on a replay): one line of what was said so far, above the bar.
    caption_scale: float = 0.72  # x the UI size
    caption_above: int = 26  # px from the bar's centre up to the line's bottom
    caption_band: RGBA = (0, 0, 0, 140)  # behind the line, for any video
    caption_max: float = 0.8  # x the frame width: older words scroll off the left
    flip_hint: str = "M  Flip video"  # top right of a replay: the key that mirrors it or not
    hint_top: int = 16  # px from the frame's top edge
    gap: float = 0.5  # its centre below the unit's last enlarged row, x the padding (Review's take lines start a padding below)


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
    rubber_max: float = 0.3  # past an end the knob goes at most this far on, x the half track
    labels: tuple[str, str] = ("Formal", "Warm")  # the label's words for the same ends (render.TONE_NAMES)


@dataclass(frozen=True)
class Motion:
    """How things move (palmcards/motion.py springs). A spring's response is
    roughly how long it takes to get there, in seconds; damping 1.0 means no
    overshoot. What the hand drives directly follows it 1:1, no spring; springs
    carry the rest (a node settling, a scroll the voice asked for) and pick up
    from where they are when interrupted. Keys never animate."""
    # The options ring: between the knob's steps it turns with the hand
    # (ring_gain of the way, nothing within ring_flat steps of a node, so it
    # rests on the node through the index's wobble), and it springs onto the
    # node a step lands on.
    ring: float = 0.10
    ring_damping: float = 1.0
    ring_gain: float = 0.5
    ring_flat: float = 0.1  # steps
    ring_open: float = 0.18  # the ring opening out of the word (from 0.35 of the way out, faded in)
    # The focus grows out of its unit's place in the notes and shrinks back
    # into it on the way out, a little faster (the notes crossfading under it).
    focus_in: float = 0.22
    focus_out: float = 0.16
    # The tone knob springs back to its value when the hand stops driving it
    # (a pinch's rewind, the L dropped past an end); past an end it follows the
    # hand with rising resistance (motion.rubberband, this constant; how far:
    # GAUGE.rubber_max).
    dial: float = 0.10
    rubber: float = 0.55
    # Browsing pushed past the first or last row: the notes give, up to give_rows.
    give_rows: float = 2.0
    # Reduced motion (preferences): the focus and the ring fade in place over this, nothing moves.
    fade: float = 0.12
    # The focus panel's scroll (Rehearse following the voice, the pages a tall
    # panel turns by itself, the section handed on).
    scroll: float = 0.30
    # Pinch + lift: what the commit will act on rises with the pinched hand, up
    # to lift_px at the commit's height.
    lift_px: int = 6
    # Backing out (the hand dropped): the focus fades by up to drop_fade and the
    # ring's nodes are drawn in toward the word by up to drop_pull, as the timer runs.
    drop_fade: float = 0.4
    drop_pull: float = 0.3


@dataclass(frozen=True)
class Sound:
    """Soft cues in Prepare and Review (optional: preferences `sounds`;
    palmcards/sounds.py): macOS's own sounds, quiet. Never during a take or
    while something plays."""
    cues: dict[str, tuple[str, float]] = field(default_factory=lambda: {
        "focus": ("Tink", 0.25),   # a unit focused
        "back": ("Bottle", 0.18),  # backed out
        "commit": ("Pop", 0.3),    # pinch + lift: an edit made
        "step": ("Tink", 0.1),     # the options ring onto the next node
    })
    step_gap_s: float = 0.08  # at most one step tick this often, however fast the ring turns


@dataclass(frozen=True)
class Rec:
    """Rehearse: the recording clock (TYPE.operation) and microphone level,
    top right of the frame, in the hand zone. Nothing else is drawn there
    during a take: its only command, the OK sign, works anywhere."""
    x: float = 0.83  # the chip's centre, x the frame width
    top: float = 0.5  # its top, x the notes' line height below the frame's top edge
    mic_dx: int = 14  # px left of the REC chip
    mic_r: tuple[int, int] = (4, 8)  # (radius when silent, growth at full level)


@dataclass(frozen=True)
class CountIn:
    """The 3-2-1 (TYPE.display), with a bar under it emptying each second."""
    bar_em: float = 1.6  # the bar's length, x the digit's width
    y: float = 0.65  # centred in the hand zone (LAYOUT.hand), at this height


@dataclass(frozen=True)
class Hands:
    tip_r: int = 4  # fingertip dots
    active_tip_r: int = 9  # index fingertip
    # A curled finger's dot: smaller and faint, so the dots show the shape the
    # hand is making from the first frame, before it counts (TIMING.stable_s).
    curled_tip_r: int = 3
    curled_alpha: float = 0.35
    # While the hand's shape has changed but doesn't count yet, the active dot is a ring.
    pending_ring_w: int = 2
    stretch_stroke: int = 2  # line between the two index tips, drawn under the text
    closing_stretch_stroke: int = 5  # ... while a thumb closes into a pinch: the length is held
    stretch_alpha: float = 0.45
    # Past the length's limits: the part of the line beyond the fullest this
    # faint; past the shortest, ticks this long where the limit would end.
    stretch_over_alpha: float = 0.15
    stretch_tick: int = 10  # px
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
GLASS = Glass()
FILL = Fill()
SHADOW = Shadow()
OUTLINE = Outline()
# Preferences > high contrast (key c): dimmed text much brighter, context
# readable, a stronger halo; the highlight stays orange.
HIGH_CONTRAST = replace(COLORS, text=(255, 255, 255, 255),
                        dim=(255, 255, 255, 215), faint=(255, 255, 255, 130),
                        ring_sentence=(255, 255, 255, 215), ring_context=(255, 255, 255, 55),
                        shadow=(0, 0, 0, 240), outline=(0, 0, 0, 220),
                        label_hint=(255, 255, 255, 230), context_fill=(255, 255, 255, 60),
                        bar_track=(255, 255, 255, 110),
                        label_fill=(0, 0, 0, 230), detail_text=(255, 255, 255, 255))
TEXT = Text()
CHIPS = Chips()
LABEL = Label()
DETAIL = Detail()
RING = Ring()
GAUGE = Gauge()
PLAYBAR = Playbar()
BAR = Bar()
TYPE = TypeScale()
MOTION = Motion()
SOUND = Sound()
REC = Rec()
COUNT_IN = CountIn()
SUMMARY = Summary()
HANDS = Hands()
DEBUG = Debug()
PLAYER = Player()
