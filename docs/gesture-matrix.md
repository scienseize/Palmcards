# Gesture state machine: matrix and audit

Audit of `palmcards/gestures.py` (`Grammar`, `DoneHold`, `ModeMachine`), the app's handling in
`main.py` and the label copy in `palmcards/render.py`. First written at commit `09563c6`
(2026-09-28) as a report; the findings were then fixed (§2, §3), and the matrix below describes
the machine as it is after the fixes. Times are `config.py` defaults (`REHEARSE.start_hold_s`
and `done_hold_s` can be changed in the preferences, as `start_hold_s` and `done_hold_s`).

**Modes** (`ModeMachine.mode`): `prepare`, `count_in`, `rehearse`, `review`. A drill is a
`count_in` then `rehearse` for one sentence.

**Grammar states** (`GestureState.mode`, Prepare and Review only): `idle` (no browse shape),
`browse` (at a level), `focus` (at a level, with an `op`: `ring`, `tone`, `stretch` or `take`).
Count-in and Rehearse don't run the grammar: only the thumbs-up hold, and every pose is logged.

**Poses** (stable after `TIMING.stable_s` 0.15 s): `ONE`, `TWO`, `FLAT`, `OPEN`, `L`, `PINCH`,
`FIST`, `THUMB_UP`, `NONE`. **Events**: `fold` (fingertips reach the thumb within 0.4 s), `commit`
(pinch + lift: wrist up 15% of the frame height within 0.6 s, from a pinch started after the hand
opened in the focus).

**Every hold** (a fist to start, a thumbs-up for done, crossed fingers for undo, an open palm to hear, play, stop,
open the ring or retry) forgives 0.3 s of other readings in the middle (`gestures.HOLD_GRACE_S`
on the stable pose, `REHEARSE.done_grace_s` on the thumbs-up's raw pose). It completes only on a
frame that reads its pose, or one where tracking has lost the hand, never on another pose. A pose
that was up when the mode changed doesn't count until it has come down: a thumbs-up, or a hand
that closes into a fist (its first pose is `CARRIED`).

## 1. The matrix

### Prepare

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| idle | `FIST` raised into view (its first pose) | held 1.0 s | count-in (a take) | `RAISE A FIST: NEW TAKE`; while held `NEW TAKE: HOLD` and a bar |
| idle, browse | `FIST` formed from another pose, or by a hand up across a mode change | held 0.6 s | nothing; a hint (at most every 6 s) | `NEW TAKE: DROP THE HAND, RAISE A FIST` |
| idle, browse, an edit made | fingers crossed, index over middle (one hand; it reads as `TWO` and browses sentences meanwhile) | held 1.0 s, once per crossing | undo the last edit | idle: `CROSS YOUR FINGERS: UNDO`; after an edit `… / CROSS YOUR FINGERS: UNDO`; `UNDO: HOLD` + bar |
| idle, browse | `THUMB_UP` | – | nothing | – |
| idle, browse | `ONE` / `TWO` / `FLAT` | 0.15 s | browse by word / sentence / paragraph (a change of shape changes the level) | idle: `FINGER UP: BROWSE`; browse: the other levels' shapes |
| browse | index tip moving in the hand box | shape of the level held | the highlight follows it | – (implicit) |
| browse | index tip in the hand box's top or bottom 10% | – | the notes scroll (up to 6 rows/s) | a chevron in each band; while scrolling `SCROLLING UP` / `DOWN` / `MOVE TO THE MIDDLE: STOP` |
| browse word | `PINCH` | – | focus the word pointed at before the curl | `PINCH: FOCUS` |
| browse sentence, paragraph | `fold` | within 0.4 s | focus the sentence / paragraph | `FOLD: FOCUS[, THEN HEAR IT]` |
| browse | hand gone | 0.3 s | idle | – |
| focus (any) | hand gone, or all of it in the frame's bottom 10% and not working a control or holding a palm | 1.0 s | back to browse | `DROP HAND: BACK`; while it runs `BACKING OUT` + bar, `RAISE HAND TO STAY` |
| focus (any), no operation | pinch + lift | – | nothing: the focus stays; a note | `NOTHING TO USE YET: …` (what comes first) |
| focus word | `OPEN` | held 0.6 s | opens the options ring and asks the LLM for alternatives | `HOLD OPEN PALM: ALTERNATIVES` |
| focus word, ring | `L` then tilting the index | 15° a step | turns the ring (the pick previews in the sentence) | `L-HAND, THEN TURN: PICK` |
| focus word, ring, pick ≠ original | pinch + lift | – | the word replaced (a new revision) | `PINCH + LIFT: USE "X"` |
| focus word, ring, pick = original | pinch + lift | – | leaves the focus, `NO CHANGE` | `ORIGINAL WORD: NO CHANGE` |
| focus sentence | `OPEN` | held 0.6 s | hear it (the sentence spoken) | `HOLD OPEN PALM: HEAR IT` |
| focus sentence | `L` then tilting | relative, ±45° full | tone dial, live preview | `L-HAND: TONE` |
| focus sentence, tone | `OPEN` | held 0.6 s | hear it, **the wording shown** (the preview, when there is one) | `HOLD OPEN PALM: HEAR IT` |
| focus sentence, tone, candidate shown | pinch + lift | – | commits the preview | `PINCH + LIFT: USE IT` |
| focus sentence, tone, loading | pinch + lift | – | nothing (stays focused) | not offered |
| focus sentence, tone, error | pinch + lift | – | retry | `PINCH + LIFT: RETRY` |
| focus sentence or paragraph, no provider | the dial | – | nothing to preview | `… · PREVIEW ONLY / NEEDS THE OPTIONAL AI · DROP HAND: BACK` |
| focus paragraph | two `L` hands, distance between the index tips | relative | length stretch, live preview | `TWO L-HANDS: LENGTH` |
| focus paragraph, stretch, candidate shown | pinch + lift (either hand) | – | commits the preview | `PINCH + LIFT: USE IT` |
| focus paragraph | `OPEN` | – | nothing | – |
| focus sentence, hear it playing | the palm that started it, held on | – | nothing | `LOWER HAND, THEN OPEN PALM: STOP` |
| focus sentence, hear it playing | a new `OPEN` | held 0.3 s | stops it (the focus stays) | `OPEN PALM: STOP`; `STOP: HOLD` + bar |
| focus sentence, hear it playing | hand dropped | – | nothing: the focus is held | – |
| focus (any) | `FIST`, `THUMB_UP` | – | nothing (the hand is at work) | – |

### Count-in (and a drill's count-in)

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| – | `THUMB_UP`, either hand, anywhere | held 1.5 s | cancel, back to Prepare or Review | `THUMB UP: CANCEL`; `CANCEL: HOLD` + bar |
| – | `OPEN` | held 0.6 s | nothing; a hint | `A PALM DOESN'T CANCEL` |
| – | anything else | – | logged only | – |
| – | (time) | 3 s (5.5 s the first time: eye calibration) | recording starts (Rehearse) | `STARTING IN n`, digit + bar; calibration prompts |

### Rehearse (and a drill)

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| – | `THUMB_UP`, either hand, anywhere | held 1.5 s | stop the take, go to Review | `THUMB UP: STOP`; `STOP: HOLD` + bar |
| – | `OPEN` | held 0.6 s | nothing; a hint | `A PALM DOESN'T STOP A TAKE` |
| – | anything else | – | logged as data (movement, face touches) | – |
| – | voice | 3 words of the next section | the notes follow; the section moves | the orange sentence moves |

### Review

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| idle | `FIST` raised into view (its first pose) | held 1.0 s | count-in (a new full take) | `RAISE A FIST: NEW TAKE` (short `FIST: NEW TAKE`); `NEW TAKE: HOLD` |
| idle, browse | `FIST` formed from another pose, or by a hand up across a mode change | held 0.6 s | nothing; a hint | `NEW TAKE: DROP THE HAND, RAISE A FIST` |
| idle, browse | `THUMB_UP`, either hand, anywhere | held 1.0 s | back to Prepare | `THUMB UP: PREPARE`; `BACK TO PREPARE: HOLD` + bar |
| focus | `THUMB_UP` | – | nothing (drop the hand first) | – |
| idle, browse, analysis failed | `OPEN` | held 1.0 s | retry the analysis | alert `… ANALYSIS FAILED … / OPEN PALM, HELD, OR R: RETRY`; `RETRY ANALYSIS: HOLD` + bar |
| idle, browse | `ONE` or `TWO` / `FLAT` | 0.15 s | browse sentences / paragraphs | idle: `FINGER UP: BROWSE` |
| browse | index tip in the hand box's top or bottom 10% | – | the notes scroll | chevrons; `SCROLLING UP` / `DOWN` |
| browse sentence (from `ONE`) | `PINCH` | – | focus the sentence | `PINCH: DETAILS` |
| browse sentence (from `TWO`) | `fold` | 0.4 s | focus the sentence | `FOLD: DETAILS` |
| browse paragraph | `fold` | 0.4 s | focus the paragraph | `FOLD: SUMMARY` |
| browse, something playing | moving to another sentence, or the `a` / `x` key | – | stops it | `MOVE TO ANOTHER SENTENCE · A: STOP` |
| focus (any) | hand gone, or low and not working a control or holding a palm | 1.0 s | back to browse (not while playing) | `DROP HAND: BACK`; `BACKING OUT` + bar |
| focus sentence, playable | `OPEN` | held 0.6 s | play the sentence from the take shown (its video, if recorded) | `OPEN PALM: PLAY` |
| focus sentence, more than one take | `L`, then moving the index | margin 0.2 of a gap | pick the take shown | `L, POINT: PICK A TAKE` |
| focus sentence | pinch + lift | – | drill: count-in for this sentence | `PINCH + LIFT: DRILL` |
| focus paragraph, playable | `OPEN` | held 0.6 s | play the paragraph | `OPEN PALM: PLAY` |
| focus paragraph | pinch + lift | – | nothing: the focus stays; a note | `TO DRILL: FOCUS A SENTENCE, PINCH + LIFT` |
| focus, playing | a new `OPEN` | held 0.3 s | stop | `OPEN PALM: STOP`; `STOP: HOLD` + bar |
| focus, playing | the palm that started it, held on | – | nothing | `LOWER HAND, THEN OPEN PALM: STOP` |
| focus, playing | hand dropped | – | nothing: the focus is held | – |

### Keys (any mode unless said)

| Key | Action | Gesture that does the same |
|---|---|---|
| `t` | start a take (Prepare, Review) | fist held |
| `x` | stop any playback; else stop the take or cancel the count-in | new palm; thumbs-up |
| `a` | play or stop (Prepare: hear it; Review: the focused unit) | palm held; new palm |
| `p` | Review → Prepare | thumbs-up held |
| `n` / `b` | next / previous section (Rehearse) | none: in a take only the thumbs-up is a command (the voice moves on) |
| `space` / `j`, `k` | next / previous sentence, or scroll a panel | none in Rehearse (as above); browsing elsewhere |
| `u` | undo the last edit (Prepare) | fingers crossed, held |
| `r` | retry failed analysis or a failed preview | open palm held (Review); pinch + lift (a preview) |
| `e` | calibrate the eyes again at the next count-in | none (a setting) |
| `m` | flip a replayed video | none (a setting) |
| `h`, `g`, `c`, `d`, `s` | keys help, tutorial, contrast, debug view, screenshot | none (settings) |
| `Enter` | skip a tutorial step | doing the step |
| `q`, `Esc` | quit | none |

### Timings

| Action | Gesture | Hold |
|---|---|---|
| start a take | fist (first pose) | 1.0 s |
| back to Prepare (Review), undo (Prepare), retry analysis (Review) | thumbs-up; crossed fingers; open palm | 1.0 s (as long as a fist: nobody is speaking) |
| stop a take / cancel the count-in | thumbs-up | 1.5 s (clear of gestures made while speaking) |
| hear it / play / open the options ring | open palm on a focus | 0.6 s |
| stop playback | new open palm | 0.3 s (stopping is quick) |
| back out of a focus | drop the hand | 1.0 s |
| browse → idle | hand gone | 0.3 s |
| a hint for a gesture that did nothing (fist, palm) | – | 0.6 s (`main.HINT_DWELL_S`) |
| commit | pinch + lift | within 0.6 s |
| focus a sentence or paragraph | fold | within 0.4 s |

Every hold forgives 0.3 s of misreads (see the note at the top).

## 2. The reported bug: one thumbs-up stops the take and then leaves Review

**Fixed on 2026-09-28** (commit `0477eda`, both flaws below): `DoneHold.reset(release=True)` waits
for a thumbs-up up at the mode change to come down, and `ModeMachine._enter` marks every hand in
view `CARRIED`. Tests in `tests/test_gestures.py`: the thumb held on after a take and after a
cancelled count-in, and a carried fist.

**What happened.** `ModeMachine._enter` reset the thumbs-up hold, but nothing asked for the thumb to
come down first. The next frame in Review still saw `THUMB_UP`, so a new hold started at once: the
label showed `BACK TO PREPARE: HOLD` filling from Review's first frame, and 1.5 s later the app was
in Prepare. With synthetic hands, one thumbs-up held 3.1 s gave `take_stop` at 1.5 s and
`to_prepare` at 3.07 s. The same happened after cancelling a count-in from Review. A hand that
started a take with a fist and stayed in view kept `first_pose == FIST`, so folding the thumb back
into a fist in Review started a new count-in.

## 3. Findings and what was done

Severity when found: **S** should, **N** nice. All fixed on 2026-09-28 unless marked *kept*, with
the reason.

### (1) One gesture, two meanings in the same state, told apart only by where the hand is

- **S, fixed. The frame's bottom 10% backed a focus out whatever the pose.** A hand low in the frame
  now backs out only if it isn't working a control (the tone dial, the stretch, the ring's knob,
  the take pointer) or holding an open palm toward hear it or play (`Grammar._working`). A pinch or
  a fist, or a palm that has done its part, still backs out low.
- **S, fixed. The hand box's top and bottom 10% scroll, unmarked.** The hand box now shows a
  chevron in each band (`render.draw_hand_area`). While the notes scroll, the label says
  `SCROLLING UP` / `SCROLLING DOWN` and `MOVE TO THE MIDDLE: STOP`.
- **N, kept. With two hands up, which one browses depends on position.** The first hand whose wrist
  is inside the hand box becomes the primary one, and it stays so. The dots show which: yellow on
  the browsing hand, cyan on the other.
- **N, kept. A fist means "new take" or nothing depending on history.** This is intended, and the
  hint explains it. Since §2, history no longer carries across a mode change.

### (2) States with no way out, or only a keyboard way out

- **S, fixed. Failed analysis: `r` only.** In Review, with nothing focused, an open palm held 1.0 s
  retries it (`ModeMachine._retry_held`, a `retry` event). The alert line says
  `OPEN PALM, HELD, OR R: RETRY`.
- **S, kept. A wrong section jump during a take: `n` / `b` only.** Since 2026-09-27, gestures during
  a take are never commands except the thumbs-up (the user's rule). The voice follow moves on by
  itself when the next section's opening is said.
- **S, fixed. Undo: `u` only.** In Prepare, with an edit made and nothing focused, fingers crossed
  (index over middle, one hand) and held 1.0 s undo it (`gestures.fingers_crossed`, an `undo` event;
  once per crossing). It was a thumbs-up at first; the user asked for another gesture
  because a thumbs-up is sometimes read as a fist (which, raised as one, starts a take). Two index
  fingers crossed into an X came next and never tracked: MediaPipe merges overlapping hands into one.
  Crossed fingers on one hand did: on the user's trace the index and middle lines crossed in every
  frame, and two fingers held together never did (`samples/poses/crossed-fingers-20260928.json`). The
  note after an edit and the idle hint say `CROSS YOUR FINGERS: UNDO`.
- **N, kept. A tutorial step the camera can't see done: `Enter` only.** If the camera can't see the
  hand, no gesture can skip it either.
- **N, kept. `e` (recalibrate) and `m` (flip a replay).** These are settings, not ways out.
- **N, fixed. Something playing while browsing in Review: only `A: STOP` was offered.** The hint is
  now `MOVE TO ANOTHER SENTENCE · A: STOP`.
- **Found while fixing, fixed.** The voice follow's alert still offered the flick removed on
  2026-09-27 (`FLICK OR N: NEXT SECTION`). It now says `N: NEXT SECTION`.

### (3) Actions with no hint on screen

- **S, fixed. A thumbs-up held in a Review focus went to Prepare, unhinted.** In a focus the hand is
  at work: a thumbs-up there now does nothing. One up as the focus ends must come down first. The
  focus hint was right all along.
- **S, fixed. Hear it during a tone preview spoke the original wording, unhinted.** It now speaks
  the wording shown (`main.play_focus`), and the preview's hint row offers
  `HOLD OPEN PALM: HEAR IT`.
- **S, fixed. Scrolling at the hand box's edges.** See (1).
- **N, fixed. A pinch + lift with no operation left the focus (`NO CHANGE`).** The focus now stays,
  and a note says what comes first (`NOTHING TO USE YET: L-HAND: TONE`, a `commit_ignored` event).
  An options ring left at the original word still commits `NO CHANGE`, as its hint says.
- **N, fixed. A pinch + lift on a Review paragraph** also stays focused now, with its note.
- **N, kept. Rehearse's `n`, `b`, `j`, `k`, `e`** stay in the keys help (see (2)).

### (4) Hints that promise something the state machine doesn't do

- **S, fixed. `FOLD: DETAILS` while browsing Review with one finger (the gesture is a pinch).** The
  grammar exposes the browse shape (`GestureState.shape`), and the hint follows it
  (`PINCH: DETAILS` from one finger).
- **S, fixed. `PINCH + LIFT: RETRY` on a preview with no provider.** It now reads
  `TONE: WARM · PREVIEW ONLY / NEEDS THE OPTIONAL AI · DROP HAND: BACK`.
- **S, fixed. `OPEN PALM: STOP` while the palm that started the audio is still up.** The grammar
  exposes it (`GestureState.palm_spent`), and until that palm has left the hint says
  `LOWER HAND, THEN OPEN PALM: STOP`.
- **N, fixed. Unreachable hints.** `PINCH + LIFT: ASK FOR A REWRITE` is gone. A tone or length
  operation with no preview yet says only what it is set to; the preview's own line takes its place
  from the next frame. The proposal hint and `ViewState.proposal`, which nothing set, are gone.
- **N, kept. `RAISE A FIST: NEW TAKE` in idle** holds only for a fist raised into view. When it
  isn't, the fist hint says what to do.

### (5) Holds and timings that differ between similar actions without a stated reason

- **S, fixed. An open palm opened the options ring at once but played after 0.6 s.** The ring's palm
  is now held 0.6 s too (`HOLD OPEN PALM: ALTERNATIVES`), so a palm passing through doesn't open
  the ring and send a request to the LLM. The `word-ring-commit` sample was updated: the ring opens
  at 6.38 s (live 6.18 s).
- **S, fixed. Different pose reading per hold.** One rule now (see the note at the top): 0.3 s of
  misreads forgiven, and completion only on a frame with the pose or with the hand lost. The
  recorded `thumbs-up-stops-take` completes as the thumb is lost by tracking, as it did live.
- **N, fixed. One 1.5 s thumbs-up for three unequal consequences.** Stopping a take and cancelling
  its count-in stay at 1.5 s (a speaker's gestures). Leaving Review, where nobody is speaking, is
  held as long as a fist to start a take (1.0 s), as are undo and retry. The
  `thumbs-up-back-to-prepare` sample was updated (1.01 s).
- **N, fixed. Two hint dwell times (0.6 s fist, 0.8 s palm).** Both are now `main.HINT_DWELL_S`
  (0.6 s).
- **N, fixed. Two settings named `stop_hold_s`.** The preference is now `done_hold_s`. An old
  `prefs.json` with `stop_hold_s` still loads. `OPS.stop_hold_s` (a palm stopping playback) keeps
  its name.
- **OK (with a reason):**
  - a palm stops playback at 0.3 s but starts it at 0.6 s (stopping is meant to be quick);
  - the drop timer is 1.0 s in a focus but browse goes idle after 0.3 s (a focus is worth keeping);
  - the first count-in is 2.5 s longer (the eye calibration).
