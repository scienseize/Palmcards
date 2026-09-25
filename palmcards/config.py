"""Every gesture threshold in one place, and the speech, alignment and cue settings.

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
    browse_lost_s: float = 0.3  # hand gone this long while browsing = idle; its track is forgotten
    # A hand appearing within this many palms of one that just vanished is the
    # same hand under a flipped left/right label.
    handover_palms: float = 1.5


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
    take_step_deg: float = 20.0  # Review: L-hand turn per take on the take dial


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


@dataclass(frozen=True)
class Recording:
    # Takes are streamed to disk as they are recorded (palmcards.recording).
    block_frames: int = 1024  # microphone block size (21 ms at 48 kHz)
    queue_s: float = 2.0  # audio the callback can hand over before blocks are dropped (and recorded as a gap)
    flush_s: float = 1.0  # header rewritten and file fsynced this often
    finalize_timeout_s: float = 5.0  # waiting this long for a take to finish writing when the app closes


@dataclass(frozen=True)
class Speech:
    backend: str = "mlx-whisper"  # palmcards.asr BACKENDS: "mlx-whisper" or "apple"
    model: str = "mlx-community/whisper-large-v3-turbo"  # or "mlx-community/whisper-small-mlx"
    language: str = "en"  # default for `main.py --lang`
    rate: int = 16000  # Whisper's input rate
    # Leading/trailing silence is trimmed before transcription: a 20 ms frame
    # is sound if it is above both of these.
    trim_frame_s: float = 0.02
    trim_floor_db: float = -50.0  # dBFS
    trim_below_peak_db: float = 40.0  # below the loudest frame
    trim_pad_s: float = 0.3  # kept on each side of the sound
    # Skip text Whisper hallucinates into silences longer than this mid-take
    # (a speaker pausing to think).
    hallucination_silence_s: float = 2.0
    # Whisper drops "um"/"uh" unless the prompt shows it they belong.
    filler_prompts: tuple[tuple[str, str], ...] = (
        ("en", "Um, uh, so, like, I mean... okay, so, um, here's the thing."),
    )
    # Live recognition during a take (milestone 6b): a small model re-reads
    # the recent audio every step, from the last confirmed word but at most
    # live_window_s back; a word is confirmed once two consecutive readings
    # agree on it (palmcards.asr). Chosen by scripts/bench_live.py: base
    # every 0.3 s gave a median lag of 0.63-0.75 s on good takes (every 0.5 s:
    # over 1 s), in a separate process the frame rate held at 30 fps.
    live_model: str = "mlx-community/whisper-base-mlx"
    live_window_s: float = 4.0
    live_step_s: float = 0.3
    live_where: str = "process"  # where windows are read: "process" or "thread"
    live_anchor_pad_s: float = 0.05  # a window starts this long before the last confirmed word
    # Confirmed words before the window given as Whisper's prompt; 0 = none.
    # Off: in the stage 1 benchmark a prompt sent Whisper into loops
    # ("very, very, very, ...") and carried misheard words forward.
    live_prompt_words: int = 0
    # Apple's recogniser (backend "apple") wants a locale, not a language code.
    apple_locales: tuple[tuple[str, str], ...] = (("en", "en-US"),)
    apple_max_hints: int = 100  # contextual strings (note sentences) given to Apple's recogniser
    live_min_rms_db: float = -45.0  # quieter windows (dBFS) are not read
    live_edge_s: float = 0.2  # words starting this close to a full window's start may be cut off
    live_overlap_s: float = 0.1  # word edges move this much between readings
    live_agree_s: float = 0.5  # two readings' copies of a word start within this


@dataclass(frozen=True)
class Analysis:
    # The background worker that transcribes and judges takes (palmcards.analysis).
    max_queue: int = 20  # takes waiting or running; beyond this submit() refuses (the take stays unanalysed)
    job_timeout_s: float = 900.0  # a job running longer gets its worker stopped (Whisper on a long take is slow)
    max_attempts: int = 2  # runs of a job before a worker crash marks it failed
    shutdown_s: float = 20.0  # closing the app waits this long for running analysis, then defers it
    baseline_wait_s: float = 30.0  # a drill waits this long for its full take's analysis to be submitted


@dataclass(frozen=True)
class Align:
    # Score for pairing a note word with a transcript word of similarity s
    # (rapidfuzz ratio, 0..1): match_score - fuzzy_slope * (1 - s). Steep, so
    # a lone near-miss ("where" for "here") inside a skipped sentence isn't
    # worth splitting the gap for. Pairs below min_sim are never made; the
    # words become a skip and an extra instead.
    match_score: float = 2.0
    fuzzy_slope: float = 8.0
    min_sim: float = 0.5
    exact_sim: float = 0.999  # at or above: said as written, not misheard
    # Affine gaps: skipping note words / adding transcript words costs
    # open + extend per word, so a skipped sentence is one clean gap.
    gap_open: float = 2.5
    gap_extend: float = 0.3
    filler_cost: float = 0.05  # an unmatched filler barely costs anything
    # A word said in place of a note word and nothing like it: the note word
    # is missed and the said word is an extra, but it costs less than
    # opening two gaps, so one odd word doesn't derail the alignment.
    substitute_cost: float = 1.5
    join_min_sim: float = 0.9  # "every one" <-> "everyone"
    # Restarts: a repeated attempt must match the notes at least this well
    # (its last word may be cut off: "wh" for "where").
    restart_sim: float = 0.6  # every full word of the attempt
    restart_mean: float = 0.8  # ... and on average
    restart_prefix_min: int = 2  # letters a cut-off last word must have
    min_probability: float = 0.1  # Whisper word probability; below it the word is left out as unsure
    spoken_min: float = 0.8  # sentence coverage for "spoken"
    skipped_max: float = 0.25  # below this coverage the sentence is "skipped"
    fillers: tuple[str, ...] = ("um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "like", "so")


@dataclass(frozen=True)
class Cues:
    # Prosody: librosa pyin on the take resampled to 16 kHz (SPEECH.rate).
    fmin_hz: float = 65.0  # speech range, low male to high female voice
    fmax_hz: float = 400.0
    frame_length: int = 1024  # samples; 64 ms holds two periods of fmin
    hop_s: float = 0.01
    rms_frame_s: float = 0.025
    # Voiced frames this far below the take's loud speech (95th percentile of
    # voiced loudness) are ignored: breath, hum and creak after a word, which
    # pyin tracks at the bottom of its range.
    voiced_floor_db: float = 18.0
    # Pauses: silence between the words around the mark.
    short_pause_s: float = 0.3
    long_pause_s: float = 0.7
    # Pace: sentence words per minute against the take's average.
    slow_ratio: float = 0.85
    fast_ratio: float = 1.15
    pace_min_words: int = 4  # fewer aligned words: unclear
    # Stress: the word's peak against the other words' peaks. Pitch and
    # loudness drift down through a sentence, so with enough other words the
    # comparison is with a line fitted through them, else with their median.
    stress_pad_s: float = 0.05  # Whisper's word edges are approximate
    stress_min_s: float = 0.08  # shorter words: unclear
    stress_min_voiced: int = 3  # frames with a pitch, for the pitch measure
    stress_min_others: int = 2  # words to compare with
    stress_trend_min: int = 4  # other words needed to fit the drift
    stress_loud_db: float = 3.0  # louder by this much, or ...
    stress_pitch_st: float = 2.0  # ... higher by this many semitones
    peak_percentile: float = 90.0  # a word's "peak", robust to one bad frame
    # Ending intonation: line fitted to the last voiced stretch of the sentence.
    ending_window_s: float = 0.5
    ending_pad_s: float = 0.15  # looked at past the last word's end
    ending_min_voiced_s: float = 0.15  # less voiced sound: unclear
    ending_slope_st_s: float = 3.0  # semitones per second, up for rise, down for fall


@dataclass(frozen=True)
class Follow:
    # The notes following the voice during a take (palmcards.follow). Live
    # words are matched against the current section plus the opening of the
    # next only, so a stray match can't jump far.
    tail_words: int = 12  # newest live words aligned each time
    ahead_words: int = 8  # the next section's opening: whole sentences up to at least this many words
    forward_words: int = 3  # note words matched in a row to move on (next sentence or next section)
    back_words: int = 5  # ... to move back to an earlier sentence in the section
    run_gap: int = 1  # note words missing, or odd words heard, between two matches in a row


@dataclass(frozen=True)
class Voice:
    # Text to speech (palmcards.tts), for the options ring's "hear it" node.
    backend: str = "say"  # palmcards.tts BACKENDS; macOS `say`
    voice: str | None = None  # `say -v`; None: the system voice
    rate_wpm: int | None = None  # `say -r`; None: the system rate


TRACKING = Tracking()
POSE = Pose()
TIMING = Timing()
CURSOR = Cursor()
OPS = Ops()
REHEARSE = Rehearse()
RECORDING = Recording()
SPEECH = Speech()
ANALYSIS = Analysis()
ALIGN = Align()
CUES = Cues()
FOLLOW = Follow()
VOICE = Voice()
