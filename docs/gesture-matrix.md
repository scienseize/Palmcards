# Gesture state machine: matrix and audit

Audit of `palmcards/gestures.py` (`Grammar`, `DoneHold`, `ModeMachine`), the app's handling in
`main.py` and the label copy in `palmcards/render.py`, at commit `09563c6` (2026-09-28). Report
only: nothing was changed. Times are `config.py` defaults (`REHEARSE.start_hold_s` and
`done_hold_s` can be changed in the preferences as `start_hold_s` and `stop_hold_s`).

**Modes** (`ModeMachine.mode`): `prepare`, `count_in`, `rehearse`, `review`. A drill is a
`count_in` then `rehearse` for one sentence.

**Grammar states** (`GestureState.mode`, Prepare and Review only): `idle` (no browse shape),
`browse` (at a level), `focus` (at a level, with an `op`: `ring`, `tone`, `stretch` or `take`).
Count-in and Rehearse don't run the grammar: only the thumbs-up hold, and every pose is logged.

**Poses** (stable after `TIMING.stable_s` 0.15 s): `ONE`, `TWO`, `FLAT`, `OPEN`, `L`, `PINCH`,
`FIST`, `THUMB_UP`, `NONE`. **Events**: `fold` (fingertips reach the thumb within 0.4 s), `commit`
(pinch + lift: wrist up 15% of the frame height within 0.6 s, from a pinch started after the hand
opened in the focus).

## 1. The matrix

### Prepare

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| idle | `FIST` raised into view (its first pose) | held 1.0 s | count-in (a take) | `RAISE A FIST: NEW TAKE`; while held `NEW TAKE: HOLD` and a bar |
| idle, browse | `FIST` formed from another pose | held 0.6 s | nothing; a hint (at most every 6 s) | `NEW TAKE: DROP THE HAND, RAISE A FIST` |
| idle, browse | `THUMB_UP` | – | nothing | – |
| idle, browse | `ONE` / `TWO` / `FLAT` | 0.15 s | browse by word / sentence / paragraph (a change of shape changes the level) | idle: `FINGER UP: BROWSE`; browse: the other levels' shapes |
| browse | index tip moving in the hand box | shape of the level held | the highlight follows it | – (implicit) |
| browse | index tip in the hand box's top or bottom 10% | – | the notes scroll (up to 6 rows/s) | none |
| browse word | `PINCH` | – | focus the word pointed at before the curl | `PINCH: FOCUS` |
| browse sentence, paragraph | `fold` | within 0.4 s | focus the sentence / paragraph | `FOLD: FOCUS[, THEN HEAR IT]` |
| browse | hand gone | 0.3 s | idle | – |
| focus (any) | hand gone, or all of it in the frame's bottom 10% | 1.0 s | back to browse | `DROP HAND: BACK`; while it runs `BACKING OUT` + bar, `RAISE HAND TO STAY` |
| focus (any, nothing playing) | pinch + lift, no operation | – | leaves the focus, `NO CHANGE` | none |
| focus word | `OPEN` | 0.15 s | opens the options ring and asks the LLM for alternatives | `OPEN PALM: ALTERNATIVES` |
| focus word, ring | `L` then tilting the index | 15° a step | turns the ring (the pick previews in the sentence) | `L-HAND, THEN TURN: PICK` |
| focus word, ring, pick ≠ original | pinch + lift | – | the word replaced (a new revision) | `PINCH + LIFT: USE "X"` |
| focus word, ring, pick = original | pinch + lift | – | leaves the focus, `NO CHANGE` | `ORIGINAL WORD: NO CHANGE` |
| focus sentence | `OPEN` | held 0.6 s | hear it (the sentence spoken) | `HOLD OPEN PALM: HEAR IT` |
| focus sentence | `L` then tilting | relative, ±45° full | tone dial, live preview | `L-HAND: TONE` |
| focus sentence, tone | `OPEN` | held 0.6 s | hear it, **of the original wording** | none |
| focus sentence, tone, candidate shown | pinch + lift | – | commits the preview | `PINCH + LIFT: USE IT` |
| focus sentence, tone, loading | pinch + lift | – | nothing (stays focused) | not offered |
| focus sentence, tone, error | pinch + lift | – | retry | `PINCH + LIFT: RETRY` |
| focus paragraph | two `L` hands, distance between the index tips | relative | length stretch, live preview | `TWO L-HANDS: LENGTH` |
| focus paragraph, stretch, candidate shown | pinch + lift (either hand) | – | commits the preview | `PINCH + LIFT: USE IT` |
| focus paragraph | `OPEN` | – | nothing | – |
| focus sentence, hear it playing | the palm that started it, held on | – | nothing | after 1.5 s: `OPEN PALM: STOP` |
| focus sentence, hear it playing | a new `OPEN` | held 0.3 s | stops it (the focus stays) | `OPEN PALM: STOP`; `STOP: HOLD` + bar |
| focus sentence, hear it playing | hand dropped | – | nothing: the focus is held | – |
| focus (any) | `FIST` | – | nothing (a fist only starts a take outside a focus) | – |

### Count-in (and a drill's count-in)

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| – | `THUMB_UP`, either hand, anywhere | held 1.5 s (raw pose, 0.3 s of misreads forgiven) | cancel, back to Prepare or Review | `THUMB UP: CANCEL`; `CANCEL: HOLD` + bar |
| – | `OPEN` | held 0.8 s | nothing; a hint | `A PALM DOESN'T CANCEL` |
| – | anything else | – | logged only | – |
| – | (time) | 3 s (5.5 s the first time: eye calibration) | recording starts (Rehearse) | `STARTING IN n`, digit + bar; calibration prompts |

### Rehearse (and a drill)

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| – | `THUMB_UP`, either hand, anywhere | held 1.5 s | stop the take, go to Review | `THUMB UP: STOP`; `STOP: HOLD` + bar |
| – | `OPEN` | held 0.8 s | nothing; a hint | `A PALM DOESN'T STOP A TAKE` |
| – | anything else | – | logged as data (movement, face touches) | – |
| – | voice | 3 words of the next section | the notes follow; the section moves | the orange sentence moves |

### Review

| Grammar state | Gesture | Condition, hold | Action | Hint on screen |
|---|---|---|---|---|
| idle | `FIST` raised into view (its first pose) | held 1.0 s | count-in (a new full take) | `RAISE A FIST: NEW TAKE` (short `FIST: NEW TAKE`); `NEW TAKE: HOLD` |
| idle, browse | `FIST` formed from another pose | held 0.6 s | nothing; a hint | `NEW TAKE: DROP THE HAND, RAISE A FIST` |
| **any, focus too** | `THUMB_UP`, either hand, anywhere | held 1.5 s | back to Prepare | idle, browse: `THUMB UP: PREPARE`; focus: none; `BACK TO PREPARE: HOLD` + bar |
| idle, browse | `ONE` or `TWO` / `FLAT` | 0.15 s | browse sentences / paragraphs | idle: `FINGER UP: BROWSE` |
| browse sentence (from `ONE`) | `PINCH` | – | focus the sentence | **`FOLD: DETAILS`** |
| browse sentence (from `TWO`) | `fold` | 0.4 s | focus the sentence | `FOLD: DETAILS` |
| browse paragraph | `fold` | 0.4 s | focus the paragraph | `FOLD: SUMMARY` |
| browse, something playing | `a` / `x` key, or moving to another unit | – | stops it | `A: STOP` |
| focus (any) | hand gone or low | 1.0 s | back to browse (not while playing) | `DROP HAND: BACK`; `BACKING OUT` + bar |
| focus sentence, playable | `OPEN` | held 0.6 s | play the sentence from the take shown (its video, if recorded) | `OPEN PALM: PLAY` |
| focus sentence, more than one take | `L`, then moving the index | margin 0.2 of a gap | pick the take shown | `L, POINT: PICK A TAKE` |
| focus sentence | pinch + lift | – | drill: count-in for this sentence | `PINCH + LIFT: DRILL` |
| focus paragraph, playable | `OPEN` | held 0.6 s | play the paragraph | `OPEN PALM: PLAY` |
| focus paragraph | pinch + lift | – | nothing; a note | `TO DRILL: FOCUS A SENTENCE, PINCH + LIFT` (after the fact) |
| focus, playing | a new `OPEN` | held 0.3 s | stop | `OPEN PALM: STOP`; `STOP: HOLD` + bar |
| focus, playing | the palm that started it, held on | – | nothing | after 1.5 s: `OPEN PALM: STOP` |
| focus, playing | hand dropped | – | nothing: the focus is held | – |

### Keys (any mode unless said)

| Key | Action | Gesture that does the same |
|---|---|---|
| `t` | start a take (Prepare, Review) | fist held |
| `x` | stop any playback; else stop the take or cancel the count-in | new palm; thumbs-up |
| `a` | play or stop (Prepare: hear it; Review: the focused unit) | palm held; new palm |
| `p` | Review → Prepare | thumbs-up held |
| `n` / `b` | next / previous section (Rehearse) | **none** (the voice) |
| `space` / `j`, `k` | next / previous sentence, or scroll a panel | **none** in Rehearse; browsing elsewhere |
| `u` | undo the last edit (Prepare) | **none** |
| `r` | retry failed analysis or a failed preview | **none** for analysis; pinch + lift for a preview |
| `e` | calibrate the eyes again at the next count-in | **none** |
| `m` | flip a replayed video | **none** |
| `h`, `g`, `c`, `d`, `s` | keys help, tutorial, contrast, debug view, screenshot | none (settings) |
| `Enter` | skip a tutorial step | doing the step |
| `q`, `Esc` | quit | none |

### Timings

| Action | Gesture | Hold | Pose reading |
|---|---|---|---|
| start a take | fist (first pose) | 1.0 s | stable pose, no grace |
| stop a take / cancel the count-in / back to Prepare | thumbs-up | 1.5 s | raw pose, 0.3 s grace |
| hear it / play | open palm on a focus | 0.6 s | stable pose |
| stop playback | new open palm | 0.3 s | stable pose |
| open the options ring (asks the LLM) | open palm on a focused word | none (0.15 s) | stable pose |
| back out of a focus | drop the hand | 1.0 s | position |
| browse → idle | hand gone | 0.3 s | – |
| fist hint | fist formed from another pose | 0.6 s | stable pose |
| palm hint (count-in, take) | open palm | 0.8 s | stable pose |
| commit | pinch + lift | within 0.6 s | event |
| focus a sentence or paragraph | fold | within 0.4 s | event |

## 2. The reported bug: one thumbs-up stops the take and then leaves Review

**Fixed on 2026-09-28** (both flaws below): `DoneHold.reset(release=True)` waits for a thumbs-up up at
the mode change to come down, and `ModeMachine._enter` marks every hand in view `CARRIED`. Tests:
`tests/test_gestures.py` (the thumb held on after a take and after a cancelled count-in; a carried fist).

**What happens.** `ModeMachine._enter` resets the thumbs-up hold (`done.reset()`, gestures.py
1253), but nothing asks for the thumb to come down first. The next frame in Review still sees
`THUMB_UP`, so `DoneHold.update` (1108) starts a new hold at once. The label shows
`BACK TO PREPARE: HOLD` filling from the first frame of Review, and 1.5 s later the app is in
Prepare. Reproduced with synthetic hands: one thumbs-up held 3.1 s gives `take_stop` at 1.5 s
and `to_prepare` at 3.07 s.

**The same flaw, two more ways:**
- **Count-in cancelled from Review:** a thumbs-up held 3.2 s cancels at 3.17 s (back to Review),
  then goes on to Prepare at 4.73 s.
- **A carried-over fist starts a new take.** The fist's "first pose" rule is kept per hand track,
  and `Grammar.reset` keeps the tracks across a mode change (609). A hand that started a take
  with a fist and stayed in view keeps `first_pose == FIST`. So after stopping with a thumbs-up,
  folding the thumb back into a fist in Review starts a new count-in 1.0 s later (reproduced:
  `count_in` at 7.98 s). The rule meant to stop accidental takes doesn't hold across modes.

**Suggested fix (not applied):**
- After a mode change, the thumbs-up hold should arm only once no hand has shown `THUMB_UP` for
  longer than `done_grace_s`. This is the rule the palm hold already follows (`_palm_done`, "a
  palm up when playback starts must leave the palm first").
- `_enter` should mark every present track's `first_pose` as spent (for example set it to its
  current pose), so only a hand raised again starts a take.
- Add a gesture replay sample: stop a take and keep the thumb up in Review. It should never reach
  `to_prepare`.

## 3. Findings

Severity: **B** blocker, **S** should, **N** nice.

### (1) One gesture, two meanings in the same state, told apart only by where the hand is

- **S. Focus: the frame's bottom 10% means "back out", whatever the pose.** Any pose held with all
  of the hand below 90% of the frame height (`TIMING.drop_band`, gestures.py 802) runs the 1 s
  drop timer. Above that line, the same L-hand works the tone dial or the pointer, and the same
  palm plays. A dial worked low (a relaxed arm at chest height with a low camera) backs out
  without warning. The `BACKING OUT` bar shows it, but only after the fact.
- **S. Browse: the hand box's top and bottom 10% scroll instead of moving the highlight.** The same
  pointing finger either picks a row or scrolls the notes, depending on where it is in the box
  (`CURSOR.edge_band`, 438). There is no hint, and the box's corners don't mark the bands.
- **N. With two hands up, which one browses depends on position.** The primary hand is the first
  whose wrist is inside the hand box, else the rightmost (`_pick_primary`, 595). It is sticky
  afterwards, which is right, but the first choice is positional and never shown (both hands get
  dots; only the colour tells them apart).
- **N (not position, but the same kind of trap).** A fist means "new take" or nothing depending on
  history (its first pose). This is intended, and the hint explains it. See §2 for where the
  history leaks across modes.

### (2) States with no way out, or only a keyboard way out

No mode is a gesture trap: every focus backs out by dropping the hand (or by stopping playback
first), and count-in, Rehearse and Review each have a thumbs-up. These situations can only be
resolved from the keyboard:
- **S.** Failed analysis in Review: the alert line says to press `r`; there is no gesture.
- **S.** A wrong section jump by the voice follow during a take: only `n` / `b` correct it
  (by design since 2026-09-27, but the take then records the wrong section until a key is pressed).
- **S.** Undoing an edit: only `u`. After a commit the original word is no longer on the ring (the
  committed word is now the original), so "focus again and pick the original" doesn't undo it.
- **N.** A tutorial step the camera can't see done: only `Enter` skips it (or `g` hides the card).
- **N.** Recalibrating the eyes (`e`) and flipping a replay (`m`): keys only; neither traps.
- **N.** Something playing while browsing in Review: the hint offers only `A: STOP`, though
  browsing to another sentence also stops it (gesture).

### (3) Actions with no hint on screen

- **S. A thumbs-up held in a Review focus goes to Prepare, and the focus hint doesn't say so.** Only
  the idle and browse hints list `THUMB UP: PREPARE`. With §2 this is how the reported bug happens
  with nothing on screen to explain it until the bar appears.
- **S. Hear it works during a tone preview, and speaks the original wording.** An open palm held
  0.6 s in a sentence focus with the tone dial on plays `say` of the notes, not the preview on
  screen. The preview label doesn't offer it, and what it plays doesn't match what is shown.
- **S. Scrolling at the hand box's edges** (see (1)): the tutorial doesn't teach it and no label
  names it.
- **N. Pinch + lift with no operation leaves the focus** (`NO CHANGE`): an unhinted way out.
  Harmless, but it means a stray lift exits a focus.
- **N. Review paragraph: pinch + lift does nothing** except a note after the fact.
- **N. Rehearse: `n`, `b`, `j`, `k`, `e`** are only in the keys help (`H: KEYS`).

### (4) Hints that promise something the state machine doesn't do

- **S. Review, browsing sentences with one finger: `FOLD: DETAILS`.** One finger closes by pinching
  (`REVIEW_LEVEL_OF_SHAPE` maps `ONE` to sentence and `_update_browse` focuses on `PINCH`); a fold
  from one finger does nothing. The hint should follow the shape (`PINCH: DETAILS` from one
  finger). The grammar doesn't expose `_browse_shape` to the view yet.
- **S. `PINCH + LIFT: RETRY` on a preview with no provider.** Without the LLM a tone or length
  preview errors (`PROVIDER UNAVAILABLE`), and retrying can't succeed. The line should say
  `NEEDS THE OPTIONAL AI · DROP HAND: BACK`.
- **S. `OPEN PALM: STOP` while the palm that started the audio is still up.** After the 1.5 s note
  (`SPEAKING SENTENCE`, `PLAYING TAKE 1`) the hint says `OPEN PALM: STOP`. The palm already up
  never stops it (by design): it has to leave first. The hint reads as broken to someone holding
  their palm. Suggested: `LOWER HAND, THEN OPEN PALM: STOP` until the palm has left.
- **N. Unreachable hints.**
  - `TONE: … / PINCH + LIFT: ASK FOR A REWRITE` and `LENGTH: …` (render.py 753, 758) show only
    when a tone or stretch operation has no edit preview. In Prepare it always has one
    (`Takes.sync_edit`).
  - `PINCH + LIFT: USE THE PROPOSAL` (761) needs `ViewState.proposal`, which nothing sets
    (`Takes.ask_rewrite` is only called from tests).
  - Neither misleads today, but they would promise an older flow if they surfaced.
- **N. `RAISE A FIST: NEW TAKE` in idle** is true only if the fist is the hand's first pose since it
  came into view. A hand already up in another shape needs to leave first (the 0.6 s hint then
  says so).

### (5) Holds and timings that differ between similar actions without a stated reason

- **S. An open palm on a focus: 0.15 s to open the options ring, 0.6 s to hear a sentence or play a
  take.** The ring's palm is the costlier one (it sends a request to the LLM, cloud tokens with
  `--llm anthropic`), yet it needs no hold. A palm passing through on its way to another shape
  can open the ring and send a request.
- **S. Different pose reading per hold.** The thumbs-up hold reads the raw pose frame by frame and
  forgives up to 0.3 s of misreads. The fist, palm and stop holds read the stable pose: a misread
  shorter than 0.15 s never changes it, but a longer one restarts the hold with no grace, and each
  hold starts 0.15 s late. So a 0.2 s misread restarts a fist or palm hold but not a thumbs-up
  hold.
- **N. One thumbs-up hold, 1.5 s, for three unequal consequences:**
  - stopping a take (1.5 s keeps it clear of gestures made while speaking: stated in CLAUDE.md);
  - cancelling a count-in;
  - leaving Review for Prepare.

  In Review nobody is speaking, and the start-a-take fist is 1.0 s.
- **N. Two dwell times for the two "gesture did nothing" hints:** 0.6 s for the fist, 0.8 s for
  the palm.
- **N. Two settings named `stop_hold_s`:**
  - `OPS.stop_hold_s` (0.3 s, a palm stopping playback);
  - the preference `stop_hold_s`, which sets `REHEARSE.done_hold_s` (1.5 s, the thumbs-up).

  The preference's help text says "for done (stop, cancel, back to Prepare)", but its name
  collides with the palm stop.
- **OK (with a reason):**
  - a palm stops playback at 0.3 s but starts it at 0.6 s (stopping is meant to be quick);
  - the drop timer is 1.0 s in a focus but browse goes idle after 0.3 s (a focus is worth keeping);
  - the first count-in is 2.5 s longer (the eye calibration).

## 4. Summary

| # | Severity | Finding |
|---|---|---|
| §2 | B | A thumbs-up held on after stopping a take (or cancelling a count-in) goes on to Prepare (fixed) |
| §2 | S | A fist formed from the stopping hand starts a new take (first pose carried across modes) (fixed) |
| 1 | S | Focus: any pose in the frame's bottom 10% backs out |
| 1 | S | Browse: the hand box's top and bottom 10% scroll (unmarked, unhinted) |
| 2 | S | Failed analysis, a wrong section jump and undo: keyboard only |
| 3 | S | Thumbs-up in a Review focus goes to Prepare with no hint |
| 3 | S | Hear it in a tone preview speaks the original wording, unhinted |
| 4 | S | `FOLD: DETAILS` shown while browsing with one finger (the gesture is a pinch) |
| 4 | S | `PINCH + LIFT: RETRY` without a provider |
| 4 | S | `OPEN PALM: STOP` while the palm that started playback is up |
| 5 | S | Palm opens the ring (and sends an LLM request) instantly, but plays after 0.6 s |
| 5 | S | Thumbs-up hold forgives misreads; the other holds don't |
| – | N | the rest above |
