"""Every gesture threshold in one place.

Hand distances are divided by the palm size, dist(wrist, middle MCP), so they
hold at any distance from the camera. Screen positions are fractions of the
frame. Times are seconds. Starting values come from CLAUDE.md; tune here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tracking:
    num_hands: int = 2
    max_side: int = 640  # frames are downscaled to this before recognition
    busy_timeout_s: float = 0.5  # give up on a result that never arrived
    min_detection: float = 0.5
    min_presence: float = 0.5
    min_tracking: float = 0.5


@dataclass(frozen=True)
class Pose:
    extended_ratio: float = 1.15  # dist(wrist, tip) > this * dist(wrist, pip)
    thumb_out: float = 0.9  # dist(thumb tip, index MCP) > this
    pinch_on: float = 0.25  # dist(thumb tip, index tip) to enter a pinch
    pinch_off: float = 0.35  # ... and to leave it (hysteresis)
    pinch_min_reach: float = 1.1  # index tip this far from the wrist, so a fist isn't a pinch
    together: float = 0.30  # index and middle tips closer than this
    flat_spread_max: float = 0.30  # mean adjacent fingertip distance
    open_spread_min: float = 0.45
    l_angle_min: float = 50.0  # degrees between thumb and index
    l_angle_max: float = 130.0


@dataclass(frozen=True)
class Timing:
    stable_s: float = 0.15  # a pose must hold this long before it counts
    fold_window_s: float = 0.4  # fingertips must reach the thumb within this
    fold_start_dist: float = 0.6  # fold starts from fingertips at least this far from the thumb
    fold_dist: float = 0.35  # ... and ends closer than this
    commit_window_s: float = 0.6
    commit_rise: float = 0.15  # wrist rise while pinched, fraction of frame height
    drop_s: float = 1.0  # hand gone this long while focused = back out
    # The whole hand (its highest landmark) below this fraction of frame
    # height counts as gone. Not the wrist: at chest height the wrist is
    # often at or below the bottom edge of a laptop camera's frame.
    drop_band: float = 0.9
    browse_lost_s: float = 0.3  # hand gone this long while browsing = idle


@dataclass(frozen=True)
class Cursor:
    # Hand box (x0, y0, x1, y1) on the right half of the mirrored frame; it
    # maps onto the text box on the left.
    hand_box: tuple[float, float, float, float] = (0.55, 0.20, 0.95, 0.80)
    edge_band: float = 0.10  # top/bottom fraction of the hand box that scrolls
    edge_speed_rows_s: float = 6.0  # scroll speed at the very edge
    # One Euro filter on the fingertip.
    min_cutoff: float = 1.2
    beta: float = 0.02
    d_cutoff: float = 1.0


@dataclass(frozen=True)
class Ops:
    tone_range_deg: float = 45.0  # tilt from the start angle for full warm/cold
    knob_step_deg: float = 15.0  # L-hand turn per options-ring node
    knob_hysteresis: float = 0.2  # of a step, past the boundary before the node changes
    stretch_min: float = 0.5  # length ratio clamp
    stretch_max: float = 2.0


@dataclass(frozen=True)
class Rehearse:
    start_hold_s: float = 1.0  # closed fist held this long (Prepare, Review) starts a take
    count_in_s: float = 3.0  # 3-2-1 before recording
    # Command zone (x0, y0, x1, y1), top right of the mirrored frame. In
    # Rehearse, hands only act while their palm centre is inside it. Close to
    # a laptop camera a palm is ~270 px, so the zone must hold a whole hand.
    zone: tuple[float, float, float, float] = (0.66, 0.0, 1.0, 0.55)
    hold_s: float = 1.5  # open palm held in the zone: stop, cancel, or back to Prepare
    hold_grace_s: float = 0.3  # misread frames the hold forgives
    settle_s: float = 0.15  # a hand must be in the zone this long before it can flick
    flick_window_s: float = 0.3  # sideways travel must happen within this
    flick_dist: float = 0.8  # fingertip travel, palm units
    flick_straightness: float = 1.5  # sideways travel at least this times the vertical
    flick_cooldown_s: float = 1.0  # no zone command right after a flick
    follow_palms: float = 2.5  # frame-to-frame jump still counted as the same hand
    dropout_s: float = 0.3  # tracking gaps shorter than this don't lose the hand


TRACKING = Tracking()
POSE = Pose()
TIMING = Timing()
CURSOR = Cursor()
OPS = Ops()
REHEARSE = Rehearse()
