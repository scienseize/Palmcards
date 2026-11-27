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
class Body:
    # Face and pose landmarkers (palmcards.vision), only during calibration and
    # takes. Each runs on every Nth camera frame, offset so face, pose and the
    # hands (every frame) don't all start on the same frame. Rates from
    # scripts/bench_vision.py (2026-09-26): cool, every rate down to face every
    # 2nd and pose every 3rd frame kept 30 fps; once the Mac was warm (after
    # 2.5-3.5 min of take load) all rates cost frames, and these, the lowest
    # tried (face 5/s, pose 2/s), were the only ones level with hands alone.
    face_every: int = 6
    face_offset: int = 1
    pose_every: int = 15
    pose_offset: int = 2
    max_side: int = 640  # frames are downscaled to this before pose landmarking
    # ... and before face landmarking: the whole 1280 x 720 frame, as the iris
    # needs the detail (at 640 px an eye is ~30 px wide). Benchmarked warm at
    # face every 6th / pose every 15th frame: 30 fps, level with hands alone.
    face_max_side: int = 1280
    busy_timeout_s: float = 0.5  # give up on a result that never arrived
    min_detection: float = 0.5
    min_presence: float = 0.5
    min_tracking: float = 0.5
    # A frame shown later than this after the previous one puts face and pose
    # off to a later frame, so they never add to a frame that is already late.
    late_ms: float = 45.0
    # Calibration, inside the first count-in of a session: look into the
    # camera's lens (any window size or place), then read the orange sentence
    # (during the 3-2-1). Face and pose run faster meanwhile. The first
    # calib_settle_s of each step are left out: time to read the instruction
    # and move the eyes.
    calib_camera_s: float = 2.5
    calib_notes_s: float = 2.0  # at most REHEARSE.count_in_s
    calib_settle_s: float = 0.8
    calib_face_every: int = 2
    calib_pose_every: int = 5
    calib_min_frames: int = 8  # face frames needed in each step
    closed_eye: float = 0.15  # lid gap / eye width below this: a blink, left out of the calibration
    min_visibility: float = 0.5  # pose: nose and shoulders at least this visible to count
    hand_match_palms: float = 3.0  # a hand this close to one in the previous result is the same hand
    face_max_age_s: float = 1.5  # hand_tip_face/_tip_oval use a face seen no longer ago (a hand over it can hide it)


@dataclass(frozen=True)
class Pose:
    extended_ratio: float = 1.15  # dist(wrist, tip) > this * dist(wrist, pip)
    thumb_out: float = 0.9  # dist(thumb tip, index MCP) > this
    pinch_on: float = 0.25  # dist(thumb tip, index tip) to enter a pinch
    pinch_off: float = 0.35  # ... and to leave it (hysteresis)
    pinch_min_reach: float = 1.1  # index tip this far from the wrist, so a fist isn't a pinch
    together: float = 0.30  # index and middle tips closer than this
    flat_spread_max: float = 0.30  # mean adjacent fingertip distance
    # An open palm, relaxed: from the user's calibration trace (2026-09-27), a
    # relaxed palm's spread is 0.34-0.39 with the thumb resting 0.55-0.58 palms
    # from the index knuckle; a flat hand's 0.20-0.24 and 0.31-0.40. The old
    # rule (spread > 0.45, thumb > 0.9) made people stretch their hand.
    open_spread_min: float = 0.30
    open_thumb_min: float = 0.47  # dist(thumb tip, index MCP): clear of the palm, not stuck out
    open_spread_off: float = 0.27  # once open, it stays open down to these (hysteresis)
    open_thumb_off: float = 0.42
    # A thumbs-up (no command; a class of its own so a fist is never read as one):
    # no finger out, the thumb up. The user's thumbs-ups: thumb
    # 0.79-1.0 palms from the index knuckle, within 17 deg of vertical, its
    # tip 0.52-0.75 palms above the knuckle. Near misses in older traces
    # (an L collapsing, tracking glitches) had the thumb much further out.
    thumb_up_dist: tuple[float, float] = (0.65, 1.35)
    thumb_up_deg: float = 30.0
    thumb_up_height: tuple[float, float] = (0.3, 1.1)
    # A thumbs-down (no command): no finger out, the thumb pointing down, the mirror of the
    # thumbs-up: within thumb_up_deg of straight down, its tip thumb_up_height below the index knuckle.
    # A V sign (back to Prepare from Review): index and middle out and apart, their tips at least
    # this far apart (palm widths); two fingers together (TWO) are under `together`, and the gap
    # between the two is read as neither.
    victory_gap: float = 0.45
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
    # TAP: the browsing fingers (the shape's: one, two or four) curl and straighten
    # again quickly, like clicking in the air: their mean wrist-to-tip over
    # wrist-to-PIP ratio drops below tap_down for tap_down_frames frames and is back
    # above tap_up within tap_window_s of leaving it. From the recorded traces:
    # straight fingers 1.22-1.39 (5th-95th percentile), a fist's index 0.6-1.12.
    tap_up: float = 1.18
    tap_down: float = 1.05
    tap_down_frames: int = 3  # about 0.1 s: a 2-frame dip was a pinch flickering, in a recording
    tap_window_s: float = 0.6
    # A tap starts with the finger bending at the knuckle while still straight, so
    # the tip drops a row or more before the ratio above moves. The fingertip's
    # height over the index knuckle (palms) says when: the cursor doesn't follow
    # while it has dropped more than tap_bend within tap_bend_s, and a tap selects
    # where the cursor was when it was last within tap_plateau of its highest in
    # the tap_rewind_s before the curl. (On the user's recording 2026-11-27, the
    # old rewind to the last straight frame landed 3-4 rows low.)
    tap_bend: float = 0.2
    tap_bend_s: float = 0.15
    tap_plateau: float = 0.06  # steady pointing wobbles ~0.05 in the recordings; a bend starts above that
    tap_rewind_s: float = 0.6
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
    # The cursor point: the index knuckle moved up the palm's axis by this many palms,
    # about where a straight index tip is. Curling the index (a tap, a pinch, a fold)
    # moves none of the joints it reads, so selecting doesn't drag the cursor
    # (on four traces of 2026-11-27: a median 1.5 rows of drift, 7 for the fingertip).
    reach: float = 0.85
    # One Euro filter on the cursor point.
    min_cutoff: float = 1.2
    beta: float = 0.02
    d_cutoff: float = 1.0


@dataclass(frozen=True)
class Ops:
    tone_range_deg: float = 45.0  # tilt from the start angle for full warm/cold
    # Choosing by pointing (Review's takes): after an L, the index fingertip
    # moves a point from the item picked, at the scale of browsing; the
    # nearest item is picked, but only once the point is nearer to it than to
    # the current one by pick_margin of the gap between them (no flicker
    # between neighbours). The word's options ring is a knob (KNOB).
    pick_margin: float = 0.2
    stretch_min: float = 0.5  # length ratio clamp
    stretch_max: float = 2.0
    # An L-hand closing into a pinch curls the index toward the thumb, which
    # turns the angle the dials read (a median 41 deg in the recorded traces,
    # nearly 3 knob steps). So once the thumb tip comes within closing_enter
    # palms of the index tip (leaving again past closing_leave), or a pinch
    # registers, the dials (the ring's knob, tone, stretch) go back to their value from just
    # before the thumb started closing (the latest moment in the last
    # rewind_max_s with the thumb within rewind_plateau palms of its farthest
    # out) and hold there until the thumb opens again. Pointing (the takes,
    # browsing words) goes back the same way, but only when
    # the pinch registers: pointing, the thumb often rests near the index tip. A steady L has the thumb 1.3-1.9 palms from the index
    # tip; a thumb drifting in while turning rarely (about 2% of the time)
    # comes within 0.9. On the 7 recorded L-to-pinch moments this left 1 knob
    # step off (4 without it) and the dial angle 2 deg off (median; 41 without).
    closing_enter: float = 0.9
    closing_leave: float = 1.0
    rewind_max_s: float = 0.5
    rewind_plateau: float = 0.1
    # An open palm held this long: Review plays the focused sentence or paragraph
    # from the take it shows; Prepare opens a focused word's options ring.
    play_hold_s: float = 0.6
    # While it plays, a new open palm (the hand has left the palm or the frame
    # since playback started) held this long stops it.
    stop_hold_s: float = 0.3


@dataclass(frozen=True)
class Preview:
    debounce_s: float = 0.25
    tone_targets: tuple[float, ...] = (-1.0, 0.0, 1.0)
    tone_hysteresis: float = 0.1
    length_targets: tuple[float, ...] = (0.7, 1.0, 1.3)
    length_hysteresis: float = 0.035
    max_inflight: int = 2  # includes obsolete requests still running on the provider


@dataclass(frozen=True)
class Knob:
    # The word's options ring turned like a knob (Kat's "spin synonyms";
    # palmcards.knob). The angle is the L-hand's index tilt (landmarks 5 -> 8
    # from vertical, + toward screen right), One Euro smoothed, relative to
    # its angle when the L appears. Step k's centre is k * step_deg from there;
    # the knob moves to k +/- 1 once the angle is step_deg / 2 + hyst_deg past
    # k's centre (11.5 deg), so it doesn't flicker at a boundary. Within
    # dead_deg of the start angle it is back on the node it started on.
    # Earlier knobs (10 and 15 deg a step, hysteresis 0.2 of a step) followed
    # the index's wobble: 85 changes in 21 s on a recording (2026-09-26).
    step_deg: float = 15.0
    hyst_deg: float = 4.0
    dead_deg: float = 5.0
    # One Euro filter on the angle, in degrees (not the cursor's pixels).
    min_cutoff: float = 1.0
    beta: float = 0.05
    d_cutoff: float = 1.0
    # The previewed word's glyphs resolve over scramble_s. (The picked node's
    # box used to stay empty for a moment; turning steadily, the pick was never
    # shown, so it is always drawn, highlighted.) How the ring turns (with the
    # hand, springing onto a node) is style.MOTION.
    scramble_s: float = 0.15


@dataclass(frozen=True)
class Rehearse:
    start_hold_s: float = 1.0  # closed fist held this long (Prepare, Review) starts a take
    count_in_s: float = 3.0  # 3-2-1 before recording
    # Once the session has a take (and the eyes are calibrated): a short count-in, a take is
    # practice, not a ceremony. The calibration's count-in and a gaze check keep the full one.
    count_in_again_s: float = 1.0
    # A take ends itself (palmcards.follow.AutoStop): on the notes' last sentence (a drill: once
    # it has heard speech for end_heard_s), end_silence_s of quiet stops it. Quiet: the
    # microphone level under the take's noise floor (its end_floor_pct percentile so far)
    # plus end_margin (of the 0..1 level scale: 60 dB) for that long. Speech cancels it.
    end_silence_s: float = 2.5
    end_heard_s: float = 1.0
    end_floor_pct: float = 10.0
    end_margin: float = 0.2
    # The OK sign held this long, anywhere in the frame ("done"): stops a take
    # or drill, cancels the count-in. In Rehearse nothing else a hand does is
    # a command.
    done_hold_s: float = 1.5
    done_grace_s: float = 0.3  # misread frames the hold forgives


@dataclass(frozen=True)
class Coach:
    """Review's coach card (palmcards.coach): one point per take."""
    quote_words: int = 4  # a sentence is named by its first words
    pause_s: float = 2.0  # a silence this long between two sentences is worth working on (a blank)
    fillers_in_one: int = 2  # fillers in one sentence before it is worth working on
    # Pace: a talk to an audience is usually 130-170 words a minute; outside, say so.
    pace_low: int = 130
    pace_high: int = 170
    away_share: float = 0.3  # looking away from the screen more than this share of the speaking time
    few_fillers: float = 2.0  # fillers a minute under this are worth saying went well
    # Over the target length by more than this (seconds, or share of the target, the larger) is worth working on;
    # within it either way is on time.
    over_s: float = 5.0
    over_share: float = 0.05
    # Ready (palmcards.progress.readiness): the last ready_takes full takes in a row each said
    # ready_points of the key points, were on time (with a target) and had at most ready_fillers a minute.
    ready_takes: int = 3
    ready_points: float = 0.9
    ready_fillers: float = 3.0


@dataclass(frozen=True)
class QA:
    """The Q&A round (key y, preference qa): after a full take, practice questions one
    at a time, each answered aloud as a take of its own (an answer)."""
    per_round: int = 3  # questions after each full take, the least answered first
    gap_s: float = 1.5  # after a take stops, before the first question (the take finishes saving)
    read_s: float = 4.0  # the question on screen before recording starts: time to read it, not to prepare
    answer_max_s: float = 60.0  # an answer longer than this is worth working on: the answer first, then one reason
    slow_start_s: float = 3.0  # silence before the first word longer than this is worth working on
    max_questions: int = 8  # the most the AI suggests


@dataclass(frozen=True)
class Slides:
    """Slides in a take (palmcards.slides): a presentation clicker moves them. Clickers send
    arrow keys or Page Up / Page Down; cv2.waitKeyEx's codes on macOS (63232-63235 the arrows,
    63276 / 63277 Page Up / Down), and on Linux and Windows where they differ. A button that does
    nothing: its code is in the gesture log (a "key" line with `code`), and goes here."""
    next_keys: frozenset = frozenset((63235, 63233, 63277, 65363, 65364, 65366, 2555904, 2621440, 2228224))
    prev_keys: frozenset = frozenset((63234, 63232, 63276, 65361, 65362, 65365, 2424832, 2490368, 2162688))


@dataclass(frozen=True)
class Fade:
    """Fading cards (palmcards.fade; preference `fade`, key w): in a take, a note
    word said in the newest takes that said its sentence, that many in a row,
    is dimmed (dim_after) or hidden (hide_after, drawn as a blank). When the
    voice follow has confirmed no new word for prompt_after_s, the next
    prompt_words hidden words come back for the rest of the take."""
    dim_after: int = 1
    hide_after: int = 2
    prompt_after_s: float = 1.5
    prompt_words: int = 3


@dataclass(frozen=True)
class Recording:
    # Takes are streamed to disk as they are recorded (palmcards.recording).
    block_frames: int = 1024  # microphone block size (21 ms at 48 kHz)
    queue_s: float = 2.0  # audio the callback can hand over before blocks are dropped (and recorded as a gap)
    flush_s: float = 1.0  # header rewritten and file fsynced this often
    finalize_timeout_s: float = 5.0  # waiting this long for a take to finish writing when the app closes


@dataclass(frozen=True)
class Video:
    # Video of each take (palmcards.video), opt-in: preferences `video`, or
    # main.py --video. The clean mirrored camera frame, on the app clock.
    enabled: bool = False
    # Encoders tried in order; the first that opens is used (and named on the take).
    codecs: tuple[str, ...] = ("h264_videotoolbox", "libx264", "mpeg4")
    bitrate: int = 1_500_000  # bit/s: about 11 MB a minute at 720p
    scale: float = 1.0  # x the camera frame (0.75: 960x540, about 6 MB a minute at a lower bitrate)
    keyframe_s: float = 1.0  # a keyframe (and a fragment of the file) this often: what a crash can lose
    queue_s: float = 0.5  # frames the loop can hand over before they are dropped (and recorded as a gap)
    fps_guess: float = 30.0  # for sizing the queue and the encoder's rate hint; timestamps are real
    read_ahead_s: float = 0.5  # Review's replay decodes this far ahead of what it shows


@dataclass(frozen=True)
class Speech:
    backend: str = "mlx-whisper"  # palmcards.asr BACKENDS: "mlx-whisper" or "apple"
    model: str = "mlx-community/whisper-large-v3-turbo"  # or "mlx-community/whisper-small-mlx"
    # Model revisions (Hugging Face commits) are pinned, so a model update
    # upstream never changes results silently; scripts/download_models.py
    # fetches exactly these. None: whatever snapshot is cached.
    model_revision: str | None = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
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
    live_model_revision: str | None = "1e3e249fb8d01c655324bd6841b1deadffd6d04c"
    live_window_s: float = 4.0
    live_step_s: float = 0.3
    live_where: str = "process"  # where windows are read: "process" or "thread"
    live_start_timeout_s: float = 60.0  # the live model must load within this (first run: a download)
    live_anchor_pad_s: float = 0.05  # a window starts this long before the last confirmed word
    # Confirmed words before the window given as Whisper's prompt; 0 = none.
    # Off: in the stage 1 benchmark a prompt sent Whisper into loops
    # ("very, very, very, ...") and carried misheard words forward.
    live_prompt_words: int = 0
    # The filler words (ALIGN.fillers) are these languages'; in others no
    # fillers are detected (the other metrics compare the speaker with themselves).
    filler_languages: tuple[str, ...] = ("en",)
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
    # Said in your own words (a pitch is rarely word for word): a skipped sentence becomes
    # "own_words" when this share of its content words (no stopwords, stemmed) was said,
    # in any order, in one stretch of the transcript between the sentences said as written.
    cover_min: float = 0.5
    cover_sim: float = 0.8  # rapidfuzz ratio for two stems to count as the same word
    cover_span: float = 2.0  # the stretch may run this many times the sentence's words, plus 4
    fillers: tuple[str, ...] = ("um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "like", "so")


@dataclass(frozen=True)
class Prosody:
    # Pitch and loudness of a take (palmcards.prosody, for the voice metric):
    # librosa pyin on the take resampled to 16 kHz (SPEECH.rate).
    fmin_hz: float = 65.0  # speech range, low male to high female voice
    fmax_hz: float = 400.0
    frame_length: int = 1024  # samples; 64 ms holds two periods of fmin
    hop_s: float = 0.01
    rms_frame_s: float = 0.025
    # Voiced frames this far below the take's loud speech (95th percentile of
    # voiced loudness) are ignored: breath, hum and creak after a word, which
    # pyin tracks at the bottom of its range.
    voiced_floor_db: float = 18.0


@dataclass(frozen=True)
class Follow:
    # The notes following the voice during a take (palmcards.follow). Live
    # words are matched against the current section plus the opening of the
    # next only, so a stray match can't jump far.
    enabled: bool = True  # main.py --no-follow turns it off
    tail_words: int = 12  # newest live words aligned each time
    ahead_words: int = 8  # the next section's opening: whole sentences up to at least this many words
    forward_words: int = 3  # note words matched in a row to move on (next sentence or next section)
    back_words: int = 5  # ... to move back to an earlier sentence in the section
    # The highlight moves to the next sentence when the voice is probably on
    # the current one's last word (see Follower), but never while more than
    # this many of its words are still unconfirmed.
    handoff_words: int = 2
    run_gap: int = 1  # note words missing, or odd words heard, between two matches in a row
    # Said in your own words: when no run moves it, the newest content_tail live words'
    # content words (palmcards.align.content_words) are matched, in any order, against the
    # later sentences of this section and every sentence of the next one. A sentence with
    # content_words of its content words heard (content_words_section in the next section),
    # and more than the current sentence has, takes the highlight. Forward only.
    content_tail: int = 8
    content_words: int = 3
    content_words_section: int = 4
    tap_blocks: int = 256  # microphone blocks waiting for the feeder (~5 s at 48 kHz, 1024-frame blocks)
    feed_s: float = 0.1  # the feeder hands the live stream what arrived this often


@dataclass(frozen=True)
class Metrics:
    # Take metrics (palmcards.metrics): observations, each with what it rests on.
    min_speaking_s: float = 5.0  # less speaking time: no pace
    min_words: int = 10  # fewer words: no pace
    min_take_s: float = 10.0  # a shorter take: no fillers per minute
    long_pause_s: float = 1.5  # a silence between words this long is a long pause
    # Per sentence: pace needs this many aligned words (fewer: no pace for it).
    sentence_min_words: int = 4
    gap_unknown_s: float = 0.05  # a lost stretch of audio of unknown length counts as this long
    # Voice (pitch and loudness, palmcards.prosody), over voiced sound while a sentence is being said.
    min_voiced_s: float = 5.0  # less voiced sound in the take: no voice metric
    sentence_min_voiced_s: float = 0.5  # less in a sentence: no pitch range for it
    # Hands, from the take's features (palmcards.features), without --trace.
    min_hand_s: float = 1.0  # less time with a hand in view: no movement rate
    move_max_gap_s: float = 0.2  # movement is summed between hand results at most this far apart
    # A face touch: a fingertip within touch_margin face widths of the face's outline, the hand
    # touch_scale_min..max times the face's width (at the face's depth, not in front of it),
    # for touch_min_s or longer (gaps up to touch_gap_s bridged). One scripted take (2026-09-26,
    # one person): 3 touches at a scale of 0.42-0.55, hands held nearer the camera 0.78-0.94;
    # touch_scale_max is set between them (was 1.0). Not yet checked on another take.
    touch_margin: float = 0.05
    touch_scale_min: float = 0.3
    touch_scale_max: float = 0.7
    touch_min_s: float = 0.3
    touch_gap_s: float = 0.2
    # Posture against the calibration's baseline: readings with the nose and shoulders visible.
    min_pose_readings: int = 10  # fewer: no posture (pose runs about 2 a second)
    tilt_deg: float = 5.0  # shoulder line this far from the baseline: tilted
    head_drop: float = 0.08  # head this much lower than the baseline, in shoulder widths: dropped


@dataclass(frozen=True)
class Gaze:
    # Each face reading is compared with the calibration's two baselines
    # (palmcards.gaze): a distance over head yaw, pitch and iris position, each
    # feature divided by its scale (mad_scale x the larger of the two steps'
    # MADs, at least its floor). Set from two gaze-check takes (2026-09-26, one
    # person; kappa 0.74 and 0.88, up from 0.48 and 0.52): the vertical cues get
    # twice the floor of the sideways ones because head pitch drifted 3-8 degrees
    # between the calibration and the prompts; the radii are wider than a
    # 2-second calibration's spread suggests. Not yet confirmed on a take
    # recorded after the change.
    floor_yaw: float = 1.5  # degrees
    floor_pitch: float = 3.0  # degrees
    floor_iris_x: float = 0.012  # eye widths
    floor_iris_y: float = 0.020  # eye widths
    mad_scale: float = 2.0
    camera_radius: float = 4.0  # nearer the camera baseline than the notes' and within this: camera
    notes_radius: float = 6.0  # ... the notes' (wider: the notes are more than one line)
    min_separation: float = 2.0  # baselines closer than this can't tell camera from notes: all unclear
    min_frames: int = 20  # judged readings while speaking needed for a take's shares
    speech_pad_s: float = 0.2  # a sentence's time, widened by this on each side
    # The gaze check: timed prompts, each target check_each times, shuffled.
    check_each: int = 3
    check_step_s: float = 4.0
    check_settle_s: float = 1.0  # the first second of a prompt is left out (reading it, moving the eyes)


@dataclass(frozen=True)
class Review:
    min_gaze_readings: int = 3  # a sentence needs this many judged face readings for its gaze line
    # A replay's progress bar marks where a filler, a restart, a long pause
    # (METRICS.long_pause_s) or a look away from the screen happened
    # (playback.clip_marks). A look away counts from this long, with gaps of
    # up to away_gap_s (a blink, a missed reading) bridged.
    away_min_s: float = 0.5
    away_gap_s: float = 0.3


@dataclass(frozen=True)
class Llm:
    # The optional LLM (palmcards.llm): off unless chosen, with `main.py --llm`
    # or here. "ollama" runs a model on this Mac (install Ollama, `ollama pull
    # <model>`); "anthropic" is the cloud (ANTHROPIC_API_KEY in the environment or .env).
    provider: str | None = None
    model: str = "llama3.1:8b"  # Ollama's
    url: str = "http://localhost:11434"
    timeout_s: float = 20.0  # Ollama: a local model can take a while to load
    max_chars: int = 1200  # the most text a request carries
    coach_max_chars: int = 6000  # the AI coach: the most of the notes, and of the take's transcript, each
    max_alternatives: int = 3  # word alternatives on the options ring
    cloud_model: str = "claude-haiku-4-5"
    cloud_timeout_s: float = 10.0  # per attempt
    cloud_retries: int = 1  # after a timeout, a dropped connection, 408/409/429 or a 5xx
    cloud_retry_wait_s: float = 0.5
    cloud_retry_wait_max_s: float = 3.0  # a server asking to wait longer than this is not retried
    cloud_max_tokens: int = 2048  # answers are short JSON; a length rewrite is the longest
    cloud_price_usd_per_mtok: tuple[float, float] = (1.0, 5.0)  # input, output: only for `llm usage`'s estimate
    close_wait_s: float = 3.0  # at exit, calls in flight get this long to finish so their tokens are logged



TRACKING = Tracking()
BODY = Body()
POSE = Pose()
TIMING = Timing()
CURSOR = Cursor()
OPS = Ops()
PREVIEW = Preview()
KNOB = Knob()
REHEARSE = Rehearse()
COACH = Coach()
QA = QA()
RECORDING = Recording()
VIDEO = Video()
SPEECH = Speech()
ANALYSIS = Analysis()
ALIGN = Align()
PROSODY = Prosody()
FOLLOW = Follow()
LLM = Llm()
METRICS = Metrics()
GAZE = Gaze()
REVIEW = Review()
FADE = Fade()
SLIDES = Slides()
