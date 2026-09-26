# PalmCards

A gesture-controlled rehearsal mirror for words you have to say out loud. The user uploads their notes, the notes float beside their mirrored webcam image, they mark how each line should sound, rehearse it aloud, and PalmCards reports which planned delivery marks they actually hit.

Full design note: https://claude.ai/artifact/2mB94zAhFAKUNK7GnCsqSc

## Hard constraints

- Single local Python desktop app on a MacBook Air (Apple silicon). No browser, no backend, no database.
- Python 3.11 or 3.12 (newer versions may not have mediapipe wheels).
- Input is uploaded files only: `.txt`, `.md`, `.docx`. No typing inside the app. Legacy `.doc` is not supported.
- After a file is opened, everything is controlled by hand gestures.
- Visual style: notes drawn as a monospace text block over the live, mirrored webcam feed (like the "gestural editing/writing" demo by @poetengineer__). Current sentence highlighted in orange, the rest dimmed, 3 to 5 lines visible at once, soft dark backing behind the text for legibility.
- The LLM is only called when the user explicitly asks by gesture. It never rewrites notes on its own.
- Feedback covers pace, pauses, stress and intonation only. No accent correction.

## Stack

| Layer | Choice |
| --- | --- |
| Camera and compositing | OpenCV |
| Text rendering | Pillow with a TTF monospace font (DejaVu Sans Mono, shipped in `palmcards/fonts/`), pasted onto the frame (OpenCV's built-in fonts are too crude) |
| Hands | MediaPipe Hand Landmarker + Gesture Recognizer (built-in classes: Closed_Fist, Open_Palm, Thumb_Up, Pointing_Up) |
| Gaze | MediaPipe Face Landmarker (head pose + iris), during calibration and takes only, every Nth frame (`BODY` in `config.py`) |
| Posture | MediaPipe Pose Landmarker (lower frame rate than hands), during calibration and takes only, every Nth frame (`BODY`) |
| Audio capture | sounddevice; each take streamed to disk as it is recorded (`palmcards/recording.py`), recoverable after a crash |
| Transcription | mlx-whisper (or whisper.cpp), after each take, with word timestamps; language set per session. Behind one interface (`palmcards/asr.py`, engine picked by `SPEECH.backend`), which also has the live stream for 6b: Whisper base re-read every 0.3 s by default, Apple's on-device recogniser (`palmcards/asr_apple.py`, pyobjc) as the alternative |
| Prosody | librosa: `pyin` for pitch, RMS for loudness |
| Notes parsing | python-docx, markdown-it-py |
| Script alignment | rapidfuzz (fuzzy match transcript words to note words) |
| LLM | Anthropic (`claude-haiku-4-5`, `main.py --llm anthropic`) or local via Ollama (`--llm ollama`); behind one small interface (`palmcards/llm.py`) |
| Storage | JSON files per session |

MediaPipe `.task` model files live in `models/` and are downloaded by a setup script, not committed.

## Modes and gestures

Mirroring: flip the frame once at capture; do all hit-testing in the flipped coordinate space.

The gesture grammar is adapted from Kat's "Gestural editing/writing" demo (@poetengineer__). A frame-by-frame reference sheet of the demo is kept locally at `docs/local/kat-gesture-reference.jpg`; it is gitignored and not published, since the frames are Kat's.

### The grammar (applies in Prepare and Review)

1. **Hand shape picks the scope.** One finger = word, two fingers together = sentence, flat hand = paragraph/section. Holding the shape puts you in Browse at that level; the highlight follows your hand.
2. **Closing the hand focuses.** Pinch (word) or fold the extended fingers down onto the thumb (sentence, paragraph). Everything else dims and the focused unit enlarges.
3. **A second shape operates on the focus.** Open palm spreads options; an L-hand (thumb + index out) turns the word's options ring like a knob, starts choosing among other options by pointing (the fingertip moves a point; the nearest option is picked), or turns a dial; two L-hands stretch. An L starts a control; once started it keeps tracking while the index stays up, even if the thumb drifts in.
4. **Pinch and lift commits.** Pinch, then raise the pinched hand ~15% of frame height within ~0.6 s. The change is applied and you zoom back out to Browse at the same level.
5. **Drop the hand to back out.** Hand out of frame (or below the bottom band) for 1 s while focused = discard the preview, return to Browse. The original always stays in any options ring, so undo = focus again and pick the original.

The cursor is **relative**, not touch: a comfortable "hand box" on the right half of the frame maps onto the text block on the left (Kat's hand stays at chest height on the right while the highlight moves on the left). Vertical movement picks the line, horizontal picks the word. Moving into the top or bottom 10% of the hand box scrolls the notes.

### Prepare

| Level | Browse | Focus | Operate | Commit |
| --- | --- | --- | --- | --- |
| Word | One finger up | Pinch (the word pointed at before the curl: see the temporal rules) | Open palm: ring of alternatives (LLM synonyms) plus a **stress** node and a **hear it** node (macOS `say`). Make an L and turn it like a knob (Kat's "spin synonyms"): the ring turns as a whole, the picked node always at 12 o'clock, one node per `KNOB.step_deg` (15°) of index tilt, both ways, wrapping (tilt toward screen right turns it clockwise). The picked word replaces the focused word in the sentence as a live preview, in orange, and in the label (`FOCUS BY WORD "stamped"`), its glyphs scrambling in; its box stays empty while it moves up. Stress and hear it have a dimmer border and don't swap the text. The original is always a node: turning back to it is the undo | Pinch + lift |
| Sentence | Two fingers together | Fold fingers to thumb | L-hand tilt = **tone dial** (tilt toward screen right = conversational/warm, left = formal/cold), dial is relative to the angle when the dial appears. Open palm (or `m`): ask the LLM for delivery marks for this sentence, spread faded (yellow) where they would go, asked once per sentence and notes revision; from then on an L starts choosing among them instead of the tone dial: pointing moves the outline to the nearest mark. A pinch without a lift accepts it (solid yellow) or rejects it again. Pinch + lift adds the accepted ones as one revision (`u` undoes; none accepted: nothing changes); dropping the hand discards every choice | Pinch + lift |
| Paragraph / section | Flat hand, fingers together | Fold fingers to thumb | Two L-hands: distance between index tips = **length** (apart = fuller, together = shorter), relative to the distance when both hands appear | Pinch + lift (either hand) |

- Closed fist raised into view and held 1 s from Prepare: start a take (enters Rehearse). The fist must be the hand's first pose since it came into view: a fist formed from another pose (a slow pinch, a flat hand curling, a hand resting closed between gestures) never starts a take. To start one while browsing, drop the hand and raise a fist.

### Rehearse (locked except inside a command zone, top-right of the frame)
- Closed fist raised and held 1 s: start take after 3-2-1 count-in
- Eye calibration (milestone 7), in the session's first count-in, so it needs no gesture of its own: "LOOK INTO THE CAMERA ABOVE THE SCREEN" for 2.5 s (the lens itself, so any window size or place works; no dot), then the 3-2-1 with "NOW READ THE ORANGE SENTENCE" (the count-in is 2.5 s longer that once; no big digit meanwhile). Face and pose run faster meanwhile. Too few face frames in a step (`BODY.calib_min_frames`, blinks and the first 0.8 s of each step left out: time to read the instruction) = `failed`, and it runs again at the next count-in; `e` asks for it again. Kept in `session.json` (`calibrations`).
- Gaze check (`main.py --gaze-check`, milestone 7): every full take calibrates in its count-in, then shows timed prompts in the label under "GAZE CHECK" ("LOOK INTO THE CAMERA", "READ THE NOTES", "LOOK AWAY: DOWN AT THE DESK" / "TO YOUR LEFT" / ..., `GAZE.check_each` of each, `GAZE.check_step_s` apart, shuffled, never the same twice in a row) and stops itself after the last. The prompts are kept on the take (`gaze_check`); `scripts/evaluate.py --gaze RUN [--sweep]` compares them with the classifier.
- Flick inside zone: next section (a sideways swing of the hand, usually from the wrist). From milestone 6b the notes follow your voice on their own and the flick is the manual override.
- Voice follow (6b, `palmcards/follow.py` `LiveFollow`): the microphone callback hands each block to the live stream through a bounded deque (never blocking); confirmed live words move the orange sentence and scroll the panel, handing it on to the next sentence as the current one is being finished (the voice's position is estimated as the last confirmed word plus the live lag times the speaking rate; `FOLLOW.handoff_words` caps how early); while the last sentence of a section is being said, the next section shows faint underneath (display only). The recorded section changes only when the voice confirms the next section's opening (3 words), or by hand: a flick or `n` (next), `b` (previous, keys only; no backward flick gesture: with the voice following live it isn't needed), `j`/`k` (sentence; the voice carries on from there). A live stream that fails to load or dies turns the follow off (alert line) and never stops the recording. No follow in drills. `main.py --no-follow` turns it off.
- Open palm inside zone held 1.5 s: stop take (goes to Review)
- All other hand movement is logged as data (gesture amount, fidgeting, face touching), never treated as a command.

### Review (same grammar)
- Two fingers together: browse sentences; every delivery mark is drawn as a chip coloured by its verdict (green hit, red missed, grey unclear, faint text for skipped). While browsing, the latest take's summary card sits bottom right (`palmcards/review.py` `take_summary`, drawn by `render.py`, placed by `style.SUMMARY`): the take and its length, verdict counts, pace and fillers, "ON SCREEN / AWAY / UNCLEAR" (shares of every face reading while speaking), hands in view and face touches, shoulders tilted and head dropped; "NOT MEASURED" where a metric had too little to go on (the reason is in `session.json`)
- Fold to focus: the sentence enlarged with its verdicts listed under it (each mark's verdict and reason, pace against the take, fillers, and where the speaker looked while saying it in that take: "On screen 80%, away 20%", "Gaze: too few readings (2)" under `REVIEW.min_gaze_readings`, or "Gaze not measured")
- A focused sentence shows the takes that said it as chips in a column beside it (the take shown highlighted). Make an L, then point at a chip to show that take's verdicts. Each sentence shows the latest take that said it until another is picked; the pick stays after backing out, until a newer take says the sentence again. With nothing to choose the label says so ("ONLY TAKE 2 SAID THIS SENTENCE", "NO TAKE SAID THIS SENTENCE").
- Pinch + lift on a focused sentence: drill it. The count-in, then only that sentence on screen; no flick; open palm in the zone stops it. A drill take is aligned against that sentence only and its pace is judged against the last full take. Pinch + lift at word or paragraph level does nothing in Review.
- Open palm held ~0.6 s on a focused sentence: play that sentence from the take it shows (key `a`)
- Closed fist raised and held 1 s: new full take
- Open palm inside the command zone held 1.5 s: back to Prepare, to edit before the next take (the zone is shown in Review with this hint)
- `main.py --open RUN` reopens a saved session in Review: its current notes revision, every judged take on the board (takes from an earlier revision are placed by sentence id; edited sentences are left out), and takes whose analysis never finished are submitted again. New takes join the same session.

### Keyboard fallback (supplements the gestures; works with no hand tracked)
`ModeMachine.command` runs the same transitions as the gestures, logged as `key` events. `t` start a take (count-in), `x` stop it or cancel the count-in, `n` next section, `p` back to Prepare from Review, `m` suggested marks on a focused sentence (as an open palm), `space`/`j` and `k` next/previous sentence (in Rehearse within the section; in a focused panel they scroll it), `r` retry failed analysis, `e` calibrate the eyes again at the next count-in, `h` show the keys, `q` quit. No text editing.

### Guidance and preferences
- First run: a tutorial card teaches one gesture at a time (hand in the box, one finger, two fingers, fold to focus, drop to come back, fist to start); each step advances when it is done; Enter skips a step, `g` shows or hides it (`palmcards/tutorial.py`).
- The hand box is drawn faintly (its corners) whenever a hand is up in Prepare or Review.
- Hints when a gesture is seen but does nothing and that would puzzle: a fist formed from another pose ("raise it closed"), an open palm held outside the command zone in Rehearse; at most one every 6 s.
- Preferences (`palmcards/prefs.py`, `prefs.json` in the data directory, apart from the sessions): hand reach (hand box size), fist and palm hold times, high contrast (`c`), hand box shown, tutorial seen. `python -m palmcards.prefs set KEY VALUE`. No left-handed layout: decided unnecessary.

### What the screen promises
- Prepare offers only what works: the options ring holds the original word, **stress** / **unstress** (a real edit: a new notes revision, `palmcards/edit.py`) and **hear it** (speaks the sentence with the word stressed, via `palmcards/tts.py`); `u` undoes the last edit (the session's current revision steps back to its parent; nothing is deleted). Word alternatives, tone and length need the optional LLM: without one, tone and length preview their controls and say they need it; a commit never claims a change it didn't make.
- The optional LLM (`palmcards/llm.py`, off unless chosen with `main.py --llm ollama|anthropic`, default `LLM.provider`; "ollama" runs a model on the Mac; "anthropic" is the cloud, `LLM.cloud_model` = `claude-haiku-4-5`, with the key from `ANTHROPIC_API_KEY` in the environment or the gitignored `.env`, never logged or saved; its answers are held to a JSON schema; each attempt times out after `LLM.cloud_timeout_s`, one retry after a timeout, a dropped connection, 408/409/429 or 5xx, never after a wait longer than `cloud_retry_wait_max_s`): a **CLOUD LLM** chip sits bottom left whenever the cloud is on ("SENDING" while a request is out). Opening the options ring asks for word alternatives (the word's glyphs scramble while it waits, then they join the ring); committing a tone or length change asks for a rewrite; an open palm (or `m`) on a focused sentence asks for delivery marks, shown faded in place (only ones that add something: `edit.new_marks`). Rewrites come back as a *proposal*, shown under the unit when it is focused again: pinch + lift uses it (a new revision, `u` undoes), dropping the hand discards it. A request that failed is not sent again until the unit is focused again. Only explicit actions send anything; only the unit's text goes, inside `<notes>` tags with a system prompt that treats it as data; answers are validated; an answer for notes that changed since is dropped. A failed request changes nothing and says why in the label (`ALTERNATIVES FAILED: TIMED OUT, NOTHING CHANGED`); a rejected key stays on the alert line. Without a provider these say so and nothing is sent. Every call's tokens (action, provider, model, input and output tokens, outcome; never the text) go to the session's `llm-usage.jsonl`, including calls whose answer was dropped and, at exit, calls still in flight (`abandoned at exit`, after `LLM.close_wait_s`); `python -m palmcards.llm usage [RUN ...]` sums them by action with a cost estimate. Tests never see a real key and can't reach the API (`tests/conftest.py`).
- Edits never touch the imported file. Takes keep the revision they were recorded with; Review places older takes on the current notes by sentence id, so an edited sentence starts without verdicts. Sentence ids are unique across the whole session, including branches after an undo.
- The focus panel is a viewport over the whole unit: never under the label, never off the frame; long sections and long verdict lists scroll (scrollbar, more-above/below markers). In Rehearse the current sentence is orange and kept in view. A focused Review panel too tall for the frame pages itself every few seconds.
- Verdicts carry a symbol as well as a colour (✓ hit, ✗ missed, ? unclear, – skipped), and counts are given separately ("4 HIT, 3 MISSED, 2 UNCLEAR"): unclear is too little evidence, not a miss.
- Recording and analysis trouble stays on a persistent alert line under the text until dealt with; gesture hints stay in the label.

### Gesture classification (rules on MediaPipe hand landmarks, no training needed)

Landmark ids: 0 wrist, 4 thumb tip, 5/9/13/17 MCPs, 6/10/14/18 PIPs, 8/12/16/20 tips.

Features per hand per frame (all distances divided by `palm = dist(0, 9)` so they work at any distance from the camera):
- `extended(f)` for index/middle/ring/pinky: `dist(0, tip) > 1.15 * dist(0, pip)`
- `thumb_out`: `dist(4, 5) > 0.9 * palm`
- `pinch`: `dist(4, 8) < 0.25` to enter, `> 0.35` to exit (hysteresis)
- `together(8, 12)`: `dist(8, 12) < 0.30`
- `spread`: mean distance between adjacent fingertips (8-12, 12-16, 16-20); flat hand < 0.30, open palm > 0.45
- `fold`: mean `dist(tip, 4)` over the previously extended fingers drops below 0.35
- `tilt`: angle of vector 5 -> 8 from vertical, in degrees
- two-hand `stretch`: `dist(8_left, 8_right) / mean(palm_left, palm_right)`

Pose classes:
| Class | Rule |
| --- | --- |
| `ONE` | index extended; middle, ring, pinky curled; not `thumb_out` |
| `TWO` | index + middle extended and `together`; ring, pinky curled |
| `FLAT` | all four extended, `spread` < 0.30 |
| `OPEN` | all four extended + `thumb_out`, `spread` > 0.45 |
| `L` | index extended + `thumb_out`; middle, ring, pinky curled; thumb/index angle 50 to 130 degrees |
| `PINCH` | `pinch` true, and index tip > 1.1 palms from the wrist (so a fist isn't a pinch) |
| `FIST` | nothing extended, not `pinch` |
| `NONE` | anything else (including a spread V sign), ignored |

Temporal rules:
- A pose must be stable for ~150 ms (about 5 frames) before the state machine acts on it.
- `FOLD` is an event: from `TWO` or `FLAT`, fingertips converge on the thumb within ~400 ms.
- `COMMIT` is an event: `PINCH` held while wrist y rises > 0.15 of frame height within ~600 ms.
- `FLICK` is an event (command zone only): after the hand has been in the zone ~150 ms, the fingertip that travels furthest moves > 0.8 palms sideways within ~300 ms, at least 1.5x its vertical travel. Measured at the fingertips because a flick swings from the wrist and the palm barely moves. The zone follows a hand by position, not by MediaPipe's handedness label, which flips during fast moves.
- Smooth the cursor fingertip with a One Euro filter before mapping it to text.
- Dials and stretch are always relative to the value captured when the control appears, then clamped.
- The options ring's knob (`palmcards/knob.py`, settings `KNOB`): the L's index tilt (5 -> 8 from vertical), One Euro smoothed, relative to its angle when the L first appears (no jump), and when picked up again it carries on from its step. It steps to the next node once the angle is `step_deg / 2 + hyst_deg` (11.5°) past the current step's centre, so it doesn't flicker at a boundary; within `dead_deg` (5°) of the start angle it is back on the node it started on. A fast turn takes every node between. The pick is held by label: alternatives arriving from the LLM join the ring without moving it (the app gives the grammar the nodes, logged as `ring_nodes`). Each step is logged as an `op` (`ring_step`, node, word, dir), so the log reconstructs the preview sequence. The ring turns to its new angle eased over `rotate_s`, the new word resolves from random glyphs over `scramble_s`, and the picked node's box stays empty for `vacate_s`. Earlier knobs (10 and 15° a step, hysteresis 0.2 of a step) followed the index's wobble (85 changes in 21 s on one recording); the wider band is the fix to check on `--trace` recordings before adding a dwell.
- Choosing by pointing (spread marks, Review's takes; `palmcards/pick.py`): an L starts it; then the index fingertip, thumb in or out, moves `GestureState.point` (hand-box units since the focus, smoothed like the browse cursor; it carries on from its value when picked up again, so nothing jumps). The app puts the point on screen from the item picked when pointing started (not drawn: only the picked item's outline shows): for the marks at `OPS.point_gain` (2) screen px per px of fingertip, the same both ways (at the scale of browsing, 0.65 up and down, crossing the ring took ~360 px of fingertip and felt too slow); for Review's take chips at the scale of browsing. It picks the nearest item once the point is nearer to it than to the current one by `OPS.pick_margin` (0.2) of the gap between them. It replaced turning a knob for these: the index angle wobbles by tens of degrees in a fraction of a second, and a recorded session turned the ring's knob 85 times in 21 s without choosing.
- Pointing at a word and pinching focuses the word pointed at before the curl: the index tip sinks as it curls to meet the thumb (in the recorded sessions 9 of 15 word pinches landed on another word, mostly the line below). When the pinch registers, the cursor goes back to where it was just before the thumb started moving in (`rewind_max_s`, `rewind_plateau`) and holds (the hovered word boxed) until the focus: 2 of 15 off. Only the pinch triggers it: pointing, the thumb often rests near the index tip.
- A pinch while pointing (spread marks, the takes) acts on what was picked before the curl: when the pinch registers, the point goes back to where it was just before the thumb started moving in (as for a word pinch) and holds; a `rewind` event tells the app the moment, and its pick goes back to what it was then.
- Closing into a pinch doesn't turn a dial. Curling the index to meet the thumb turns the angle the dials read (a median 41° in the recorded traces, up to 105°). So when the thumb of a hand working a dial (the ring's knob, tone, stretch) comes within `OPS.closing_enter` (0.9) palms of the index tip, or a pinch registers, the dial goes back to its value from just before the thumb started closing (the latest moment in the last `rewind_max_s` with the thumb within `rewind_plateau` palms of its farthest out) and holds there (`state.closing`, drawn bolder: the gauge knob and stretch line heavier; while a pinch holds a pointer, the picked ring node, the marks outline and the take chip are drawn bolder too) until the thumb opens past `closing_leave`. A pinch's toggle or commit acts on that value. Logged as `rewind`.
- Log every recognized pose and event with a timestamp; the false-trigger measure in the evaluation depends on it.

### Visual feedback (copy Kat's patterns)
- Top-left state label: `BROWSE BY WORD`, `FOCUS BY SENTENCE`, with a second line for the operation (`CHANGE TONE: WARM`, `EXPLORE ALTERNATIVES: LOADING`).
- Yellow dot on the active fingertip, small dots on the other fingertips, cyan dot for a second hand.
- Non-focused text dims; the focused unit enlarges.
- Glyph-scramble animation on text while the LLM is rewriting; it doubles as the loading indicator.
- Radial options ring with curved yellow connectors from the focused word, turning so the picked node sits at 12 o'clock; Review's takes as a column of chips beside the focused sentence; vertical gauge for the tone dial; a straight line between the two index tips for stretch.

## Delivery marks (plain ASCII in the uploaded file)

| Markup | Meaning | Check after a take (starting thresholds, tune later) |
| --- | --- | --- |
| `/` | short pause | gap >= 0.3 s from the last word said before the note word (so a restart is measured on its final attempt); a filler in the gap is a miss; words Whisper was unsure of are ignored |
| `//` | long pause | gap >= 0.7 s |
| `*word*` | stress | word's peak (90th percentile, window padded 0.05 s) >= +3 dB louder or +2 semitones higher than the other words: a line fitted through their peaks over time (pitch and loudness drift down through a sentence) with 4+ other words, else their median |
| `[slow]` / `[fast]` | pace | sentence WPM < 85% / > 115% of the take average (spoken sentences without a pace mark); fewer than 4 aligned words = unclear |
| `[rise]` / `[fall]` | ending intonation | Theil-Sen slope of pitch over the last 0.5 s of voiced sound in the sentence, at least 3 semitones/s up or down; judged only if the sentence's last word was heard (confidence >= 0.3), else unclear |

Each mark gets a verdict: `hit`, `missed`, or `unclear` (not enough evidence to judge). "Unclear" is a valid answer; never invent a verdict. A sentence that was not spoken is `skipped`, not `missed`. Each mark needs its own evidence, so a partly said sentence is still judged where it can be: a pause needs the words on both sides, pace 4+ aligned words, stress its word, an ending the last word. Audio the recording lost (take `capture` gaps) inside what a mark measures makes it unclear. Stress and intonation thresholds are calibrated for the languages in `SPEECH.scoring_languages` (English); elsewhere those marks are unclear (the measurement is kept in the reason), fillers are not detected, and pauses and pace are judged as usual.

Pitch is librosa `pyin` (65 to 400 Hz, 10 ms hop) on the take at 16 kHz, in semitones from the speaker's median for the take. Voiced frames 18 dB below the take's loud speech are ignored (breath, hum and creak that pyin tracks at the bottom of its range). All thresholds are `CUES` in `config.py`.

Whisper tends to drop "um"/"uh": prime it with an initial prompt containing fillers.

## Session files

Everything is written under `sessions/` (gitignored). `palmcards/session.py` reads and writes the session folders; `GestureLog` in `gestures.py` writes the logs.

```
sessions/
├── 20260925-101345-sample_notes-3fa9c1/  # one folder per app run: <start time>-<notes stem>-<random>, created exclusively
│   ├── session.json                 # created with the first take; no takes, no folder
│   ├── session.lock                 # flock held by the process writing the session (released on exit or crash)
│   ├── source/sample_notes.md       # the imported notes file, byte for byte
│   ├── notes/r1a2b3c4d5e6f.json     # a notes revision: the parsed notes a take was recorded with (palmcards/revisions.py)
│   ├── take-01.wav                  # 16-bit PCM mono at the mic's own rate (48 kHz on the MacBook Air)
│   ├── take-02.wav.part             # only while recording (or after a crash): the take so far, a valid WAV
│   ├── take-02.recording.json       # only while recording: rate, first sample's app time, sections, gaps
│   ├── jobs/                        # analysis job records (<id>.json: take, revision, config, state, attempts,
│   │                                #   worker generation) and their inputs (<id>.input.json)
│   ├── take-01.transcript.json      # Whisper's words, written after the take stops
│   ├── take-01.prosody.npz          # pitch and loudness per 10 ms frame (cache; verdicts are recomputed from it)
│   ├── take-01.face.npz             # face, pose and hand features per result during the take (the only record: no video)
│   ├── take-01.verdicts.json        # a verdict per delivery mark
│   ├── llm-usage.jsonl              # one line per LLM call: action, model, tokens, outcome; never the text
│   │                                #   (a call before any take or edit makes the folder, like an edit)
│   └── take-02.wav
├── gesture-logs/
│   ├── 20260925-101345.jsonl        # every pose and event, one JSON object per line
│   └── 20260925-101345.trace.jsonl  # only with `main.py --trace`: raw landmarks for offline replay
└── screens/                         # `s` key screenshots
```

**Recording.** A take is written to disk while it is recorded: the audio callback hands blocks to a writer thread through a bounded queue (`RECORDING.queue_s`) and never waits; a full queue drops the block and records the gap. The writer rewrites the WAV header and fsyncs every `RECORDING.flush_s`. If the app dies mid-take, at most the queue's contents are lost (plus up to `flush_s` if the whole machine loses power); the next start salvages the rest as an `interrupted` take (`recover_all` in `session.py`). `main.run` owns every resource through one `ExitStack`: whatever fails (opening a model, the camera, drawing, Ctrl-C), everything opened is closed once, a take being recorded is kept, and the original error is raised.

**Analysis.** `palmcards/analysis.py` runs each saved take's transcription and judging in a supervised worker process (`python -m palmcards.speech --serve`). Submitting never blocks the frame loop; the job's input is written once to `jobs/` and the worker gets a one-line reference. Job states `queued` / `running` / `succeeded` / `failed` are saved; every worker process is a generation, and when one exits its unfinished jobs are retried (`ANALYSIS.max_attempts`) or failed, even if another worker has started since. Results must match the job's take, notes revision and analysis-config hash. Failed analysis stays on the status line; `r` retries it. Closing waits at most `ANALYSIS.shutdown_s`, then stops the worker and leaves unfinished jobs queued on disk for `python -m palmcards.speech`. A drill's pace baseline is the latest saved full take before it, by take number; the drill's job waits for that take's.

**One clock.** `t` in the gesture log, `t` in the trace, `t_start` of a take, and every word, filler and sentence time in transcripts and alignments are all seconds since the app started. A moment in a take's audio at `x` seconds is app time `t_start + x`, so audio, poses and events line up.

`session.json`:

```json
{
  "schema": 3,
  "id": "9c1f0e7a2b4d6e8f",
  "notes": "/abs/path/to/notes.md",
  "source": {"file": "source/notes.md", "sha256": "…", "size": 1234},
  "revisions": [{"id": "r1a2b3c4d5e6f", "file": "notes/r1a2b3c4d5e6f.json", "hash": "…", "parser": 1,
                 "parent": null, "created": "2026-09-25T10:13:45", "provenance": "imported"}],
  "gesture_log": "gesture-logs/20260925-101345.jsonl",
  "language": "en",
  "calibrations": [{"id": "c1", "created": "2026-09-25T10:14:30", "status": "ok", "reason": "", "version": 1,
                    "t": 44.1, "frame_size": [1280, 720],
                    "camera": {"n": 21, "yaw": [2.1, 0.8], "pitch": [-3.0, 0.6], "iris_x": [0.51, 0.02], "iris_y": [0.01, 0.01]},
                    "notes": {"n": 22, "…": "…"}, "posture": {"n": 17, "tilt": [1.2, 0.4], "width": [0.31, 0.0], "head": [0.9, 0.02]},
                    "counts": {"face": {"results": 49, "found": 49, "deferred": 0, "skipped": 0}, "…": "…"}}],
  "takes": [
    {
      "number": 1,
      "revision": "r1a2b3c4d5e6f",
      "wav": "take-01.wav",
      "started": "2026-09-25T10:14:34.435",
      "t_start": 48.787,
      "duration_s": 53.323,
      "sample_rate": 48000,
      "peak": 0.40456,
      "sections": [{"section": 0, "t": 0.0}, {"section": 1, "t": 30.943}],
      "transcript": "take-01.transcript.json",
      "verdicts": "take-01.verdicts.json",
      "marks": {"hit": 6, "missed": 4, "unclear": 0, "skipped": 0},
      "alignment": {
        "sentences": [{"sentence": 0, "status": "spoken", "coverage": 1.0, "start": 50.29, "end": 52.49,
                       "words": [0, 1, 2], "misheard": []}],
        "fillers": [{"word": 7, "text": "um", "t": 53.1}],
        "restarts": [{"sentence": 2, "words": [9, 10, 11]}],
        "extras": [{"sentence": 2, "words": [30, 31]}],
        "unsure": []
      }
    }
  ]
}
```

`language` is the Whisper language code for the session's takes (`main.py --lang`, default `en`).

**Notes are kept, not referenced.** `notes` is only where the file was imported from. The session keeps the file's bytes (`source`) and each notes revision: a normalised snapshot of sections, sentences, words and marks, with stable ids (`s1`, `s1.w0`, `s1.m0`; an unchanged sentence keeps its id across revisions, an edited one gets a new id and an `ancestry` entry naming the one it replaces). Each take names its `revision`; playback and re-analysis read that, never the file. A revision's `hash` covers its content, not its ids, and is checked on load. `provenance` is `imported`, or `legacy-unverified` for notes bound after the fact to takes recorded before snapshots (schema 1, no `schema` key): those sessions stay readable, and `python -m palmcards.speech <run> --rebind` binds the notes file as it is now, keeping the old file as `session.v1.json`. Sessions with a newer schema are refused, not rewritten.

| Take field | Meaning |
| --- | --- |
| `number`, `wav` | 1-based take number and its WAV file name in the same folder (numbers skip any take file already on disk, so nothing is overwritten) |
| `revision` | the notes revision the take was recorded with; absent on takes from schema 1 until rebound |
| `started` | wall-clock time recording began (after the count-in), ISO 8601 |
| `t_start` | app time of the first audio sample (from the device's timing where available) |
| `duration_s`, `sample_rate` | length of the WAV and its rate |
| `peak` | loudest absolute sample, 0..1; below 0.001 the take is treated as silent (usually missing Microphone permission) |
| `sections` | section indices (0-based, as in `Notes.sections`) with the time into the take each one came up and its `source`: `start` (the first, `t = 0`), `voice`, `flick` or `key` |
| `metrics` | `palmcards/metrics.py`, observations with what each rests on (None + reason when there is too little): `speech` (pace_wpm, fillers_per_min, unplanned_long_pauses, restarts, ad_libs), `hands` (shape_changes_per_min from the gesture log; in_view_share, movement_palms_s (wrists) and fingertip_movement_palms_s, palm widths per second while a hand is in view, from the take's features or, before them, a `--trace` recording; face_touches: count and `seconds` of a fingertip within `METRICS.touch_margin` face widths of the face's outline with the hand at the face's depth by its size (`touch_scale_min`..`max` times the face's width), `touch_min_s` or longer, breaks up to `touch_gap_s` bridged), `gaze` (`palmcards/gaze.py`: each face reading classed camera / notes / away / unclear against the take's calibration, counted only while a sentence is being said, `speech_pad_s` either side: `screen_share` (camera or notes) and `away_share` of the judged readings, `unclear_share` of all, `counts` (camera and notes apart), `split` (`validated: false`, the calibration's `separation`: camera vs notes did not hold up in the gaze checks, so it is kept, not reported), and `sentences` [{sentence, screen, away, unclear, camera, notes}]; None + reason without face features, a usable calibration, or `GAZE.min_frames` judged readings), `posture` (against the take's calibration: `shoulder_tilt_deg` and `head_height_change` (nose above the shoulders, shoulder widths), medians of the change, and `tilted_share` / `head_dropped_share` of pose readings over `METRICS.tilt_deg` / `head_drop`; None + reason without features, a calibration or `min_pose_readings` readings with the shoulders visible) |
| `gaze_check` | only on a gaze-check take: `seed`, `t0` (app clock at take start) and `prompts` [{target: camera / notes / away, hint, t0, t1 (s from `t0`)}] |
| `vision` | face, pose and hand features during the take: `state` (`recorded`; `off` with the `reason`, e.g. a model missing; `failed` with the `error` when the file couldn't be written), `file` (`take-NN.face.npz`), `calibration` (the id of the session's latest `ok` calibration when the take stopped, or null), `counts` per tracker (`results`, `found`, `deferred`: put off because the frame was late, `skipped`: the model was busy) and `hands.results` |
| `live` | the voice follow during the take (display only, never the record): `engine`, `model`, `state` (`following` / `failed` ...), `words` confirmed, `lag_median_s` / `lag_p90_s` (confirmed after the word's end), `dropped_blocks`, `error` |
| `transcript` | the take's transcript file; absent until transcription finishes |
| `alignment` | the transcript matched to the notes (`palmcards/align.py`); word numbers index the transcript's `words` |
| `verdicts`, `marks` | the take's verdicts file and its verdict counts; absent until judged |
| `drill` | only on a drill take: the sentence (`Notes.sentences` index) it rehearsed |
| `status` | `saved` (finished normally), `interrupted` (cut short by an error, Ctrl-C or a crash; salvaged, `capture.recovered` if at the next start) or `failed` (the disk refused a write; the audio before it is kept). Only `saved` takes are analysed automatically; `python -m palmcards.speech <run> --incomplete` analyses the others |
| `capture` | `clock`: how the first sample was placed on the app clock (`adc`: the device's timing, `callback`: the callback's arrival); `dropped_samples` and `discontinuities` (`at_s` into the take, `samples`, `why`: `queue_full`, `input_overflow`, `write_error`); `error`; `recovered` |

| Alignment field | Meaning |
| --- | --- |
| `sentences[]` | one per note sentence (`Notes.sentences` order). `status`: `spoken` (coverage >= 0.8), `partial`, `skipped` (< 0.25). `coverage`: share of note words matched. `start`/`end`: first/last matched word, null when skipped. `words`: transcript word per note word, null if not said. `misheard`: note word indices matched only approximately. `joined` (optional): `[note word, last transcript word]` where Whisper split one word in two |
| `fillers` | unmatched filler words (`um`, `uh`, `like`, `so`, ... in `config.ALIGN.fillers`) with their start time |
| `restarts` | earlier attempts at a phrase that was then said again; the notes bind to the last attempt |
| `extras` | ad-libs: unmatched non-filler words, grouped, with the sentence they fall in or after |
| `unsure` | words Whisper gave a probability below 0.1 (usually hallucinations in noise), left out of the alignment |

`take-01.transcript.json`: `{"take", "wav", "model", "asr": {"backend", "model", "revision"}, "language", "t_start", "offset_s", "text", "words": [{"text", "start", "end", "probability"}]}` (`revision`: the model's Hugging Face snapshot commit, where known). `offset_s` is the leading silence trimmed before Whisper ran; word times are already on the app clock (`t_start + offset_s + Whisper's time`).

`calibrations` (schema 3; a schema-2 file is read as is and rewritten as 3): every calibration attempt, oldest first, `ok` / `failed` (with the `reason`) / `incomplete` (the take started before it was over). `camera` and `notes` are `[median, median absolute deviation]` of head yaw/pitch and iris position over the face frames of each step, with `n`; `posture` is shoulder tilt, width and head height over both steps. `frame_size` is the camera frame's. Only the latest `ok` one is used (`Session.calibration`).

`take-01.face.npz` (`palmcards/features.py`, written when the take stops; a take cut short by a crash has none): per face result `face_t` (app clock, the frame's capture time), `face_found` (0: no face, the frame is unclear and every other value NaN), `face_yaw` / `face_pitch` / `face_roll` (degrees, from the face's transformation matrix), `face_iris_x` (iris along the eye-corner line, 0..1 from the frame-left corner, both eyes averaged), `face_iris_y` (below the line, in eye widths), `face_eye_open` (lid gap / eye width), `face_box` (x0, y0, x1, y1 px); per pose result `pose_t`, `pose_found`, `pose_tilt` (shoulder line, degrees, + when the frame-right shoulder is lower), `pose_width` (shoulder width / frame width), `pose_head` (nose above the shoulders' midpoint, in shoulder widths), `pose_vis`; per hand-tracking result `hand_t`, `hand_n`, `hand_wrist_move` / `hand_tip_move` (since the previous result, palm sizes), `hand_tip_face` (nearest fingertip to the face box, palm sizes, 0 inside), `hand_y` (highest wrist, fraction of frame height), and from features version 2 `hand_tip_oval` (nearest fingertip to the face's outline, in face widths, 0 inside) and `hand_scale` (that hand's palm size / the face's cheek-to-cheek width: about 0.6-0.7 at the face's depth, larger nearer the camera); the face is the one seen in the last `BODY.face_max_age_s`; and `provenance` (JSON: feature version, MediaPipe version, SHA-256 of both models, every `BODY` setting, frame size, mirrored, take, calibration). Unlike the prosody cache it cannot be made again: there is no video.

`take-01.prosody.npz`: arrays `t` (frame centres, app clock), `f0` (Hz, NaN where pyin found no voice), `rms_db` (dBFS), and `provenance` (JSON: WAV sha256, t_start, rate, extractor and librosa versions, every extraction setting, the actual frame hop). Made once per take in the transcription worker, alongside Whisper; the loudness gate and semitones are applied when it is read, so tuning verdict thresholds never re-runs pyin. A cache made differently (another WAV, other extraction settings) is measured again; `--realign` reuses it, reporting it `stale` (or `unknown` for a cache from before provenance). The hop always comes from the cache, never from today's `CUES`.

`take-01.verdicts.json` (`palmcards/cues.py`):

```json
{"take": 1, "take_wpm": 193.0, "baseline": "take", "version": 2, "language": {"code": "en", "calibrated": true},
 "provenance": {"notes_revision": "r1a2…", "analysis_config": "3f9a…", "asr": {"backend": "mlx-whisper", "model": "…", "revision": "…"},
                "align": {"version": 1, "settings": {"…": "…"}, "fillers": ["um", "…"]}, "scoring": {"version": 2},
                "prosody": {"status": "verified", "…": "…"}, "gaps": [[50.1, 50.2]]},
 "counts": {"hit": 6, "missed": 4, "unclear": 0, "skipped": 0},
 "thresholds": {"short_pause_s": 0.3, "...": "every CUES value used"},
 "sentences": [{"sentence": 2, "status": "spoken", "wpm": 171.2, "fillers": ["um"],
                "marks": [{"kind": "long_pause", "word": 7, "verdict": "hit", "value": 0.9, "threshold": 0.7,
                           "reason": "0.90 s pause"}]}]}
```

`marks` follow `Sentence.marks` order; `word` is as in `Mark.word`. `value`/`threshold` are seconds (pauses), a ratio to `take_wpm` (pace), `{"loud_db", "pitch_st"}` (stress) or semitones per second (ending). `baseline` is `given` for a drill, whose `take_wpm` is the last full take's (`none` if that take has no verdicts: the drill's pace is unclear). `provenance` records what produced the verdicts, so they can be checked or reproduced. A filler counts toward the sentence it falls in, or the next one said after it.

Later milestones add their results to each take (metrics) rather than inventing new files.

Gesture log lines are `{"t": ..., "kind": ..., ...}`. Kinds: `pose` (hand, pose), `browse` / `focus` (level), `fold`, `pinch_lift`, `op` (op; a step of the ring's knob: `ring_step`, node, word, dir), `ring_nodes` (nodes: the options ring's nodes when they change), `toggle`, `rewind` (op: a dial, a pointer's op, or `cursor` for a word pinch; back_s; the values undone), `mark_pick` (sentence, mark, accepted), `llm` (ask), `commit` / `back` (level, op, value), `commit_stub`, `drill` (sentence), `drop_start`, `idle`, `mode` (prepare / count_in / rehearse / review), `zone` (command: flick / hold), `key` (command, mode, acted), `hear` (sentence, word), `section` (section, source), `take_start`, `section`, `take_stop` (take, duration_s, wav), `transcribed` (take, seconds), `calibration_start`, `calibration` (id, status, reason), `gaze_check` (take, seed), `mic_error`, `record_error` (error), `screenshot`. Trace lines are `{"t": ..., "hands": [{"label": "Left", "points": [[x, y] × 21]}]}` in mirrored-frame pixels.

## Code layout

```
palmcards/
├── main.py              # entry point, mode loop
├── palmcards/
│   ├── capture.py       # camera + audio capture
│   ├── recording.py     # takes streamed to disk while recorded (bounded queue, writer thread), crash-safe
│   ├── notes.py         # file loading, sections/sentences/words, cue parsing
│   ├── gestures.py      # landmarks -> gesture events, mode-aware state machine, command zone
│   ├── config.py        # every gesture threshold in one place
│   ├── render.py        # every visual: text overlay, chips, ring, gauge, zone, labels, fingertip dots, debug drawings, player caption/strip
│   ├── style.py         # how it looks: colours (RGB/RGBA), fonts, sizes, spacing, positions
│   ├── fonts/           # DejaVuSansMono.ttf and its licence
│   ├── speech.py        # whisper transcription and judging (worker process, offline CLI)
│   ├── asr.py           # speech recognition interface: transcribe(wav), live stream of confirmed words; mlx-whisper
│   ├── asr_apple.py     # the same interface on Apple's on-device recogniser (SFSpeechRecognizer)
│   ├── audio.py         # 16 kHz resampling, silence trimming
│   ├── tts.py           # text to speech interface; macOS say
│   ├── prosody.py       # pitch (pyin) and loudness per take, cached
│   ├── features.py      # face, pose and hand features per frame, the calibration, take-NN.face.npz
│   ├── gaze.py          # gaze classes against the calibration, the take's gaze metric, the gaze check
│   ├── align.py         # transcript <-> notes alignment
│   ├── follow.py        # live words -> where the speaker is in the notes (6b)
│   ├── player.py        # debug player: a take's audio with the notes highlighted as they're said
│   ├── replay.py        # replay recorded hand landmarks through the gesture code (regression)
│   ├── cues.py          # planned marks vs measured delivery -> verdicts
│   ├── review.py        # which take each sentence shows in Review, verdict and gaze lines, the take summary card
│   ├── metrics.py       # take metrics: speech, hands, gaze, posture
│   ├── vision.py        # face and pose landmarkers at a reduced rate (calibration and takes only)
│   ├── session.py       # session folders: schema, notes revisions, takes, lock, recovery
│   ├── revisions.py     # notes snapshots with stable sentence/word/mark ids
│   ├── analysis.py      # supervised analysis worker: job records, generations, retries
│   ├── paths.py         # where data lives ($PALMCARDS_DATA, sessions/, Application Support)
│   ├── data.py          # list / export / delete / prune recordings (python -m palmcards.data)
│   ├── playback.py      # Review: play a sentence of a take
│   ├── edit.py          # edits to the notes (stress, alternatives, rewrites, marks), each a new Notes
│   ├── pick.py          # choosing by pointing: the nearest item to the pointer, with a margin
│   ├── knob.py          # the options ring's knob: angle -> steps with hysteresis, the pick by label
│   ├── llm.py           # the optional LLM: providers (fake, Ollama, Anthropic), checked answers, requests, usage
│   ├── prefs.py         # preferences (reach, holds, contrast), apart from session evidence
│   ├── tutorial.py      # the first-run gesture tutorial
│   └── export.py        # a notes revision back out as .txt / .md / .docx (python -m palmcards.export)
├── models/              # MediaPipe .task files (gitignored; scripts/download_models.py, pinned + checksummed)
├── scripts/             # download_models, bench_live, bench_vision, profile_align, evaluate, cut_gesture_samples
├── docs/                # implementation-progress (ledger), evaluation, hardware-smoke-test; local/ (gitignored)
├── requirements.lock.txt  # exact tested versions (uv pip freeze)
├── samples/             # sample notes with markup for testing
│   └── gestures/        # recorded hand landmarks + expected events, for the replay tests
└── tests/
```

## How to work in this repo

- Build one milestone at a time (below). Finish, run, commit, then move on.
- Pure logic (`notes.py`, `cues.py`, `align.py`, `review.py`) gets unit tests with pytest; `cues.py` uses synthetic audio (tones gliding up and down, a louder word) for stress and intonation. Camera and gesture code is tested by running the app; the user will report what they see or share screenshots.
- Gesture regression: `samples/gestures/*.json` are stretches of `main.py --trace` recordings (MediaPipe hand landmarks only, no video) with what the app recognised live. `tests/test_gesture_replay.py` replays them through `ModeMachine` and must reproduce it; `python -m palmcards.replay` prints the same check. Samples marked `known_issue` are strict expected failures that document a bug until it's fixed. New samples: record with `--trace`, add the window to `SEGMENTS` in `scripts/cut_gesture_samples.py`, run it (it also copies the log's `ring_nodes` lines into the sample's `inputs`, which the replay gives back to the grammar). Never commit video.
- Keep camera/gesture code runnable standalone (`python -m palmcards.gestures` shows a debug view with landmarks and the detected gesture name).
- Speech runs offline on recorded sessions: `python -m palmcards.speech sessions/<run>` (transcribe, judge and report; takes from before milestone 6 get their verdicts; `--realign` re-aligns and re-judges saved transcripts without Whisper or pyin, for tuning `ALIGN` and `CUES`) and `python -m palmcards.player sessions/<run> [--take N]` (hear a take with the notes highlighted as they're said).
- Live recognition (6b) is benchmarked offline: `python scripts/bench_live.py sessions/<run> --take N [--model REPO] [--step S] [--where thread|process] [--camera SECONDS] [--json OUT]` replays a take in real time and reports live-word lag, wrong section jumps, sentence tracking and (with `--camera`) the frame rate; `--rescore OUT.json` re-scores saved runs after tuning `FOLLOW`.
- The face and pose budget (milestone 7) is benchmarked on the camera: `python scripts/bench_vision.py [sessions/<run> --take N] [--seconds S] [--configs ...] [--json OUT]` runs the take-time load (hands every frame, the live follow replaying a take, a recording to a temporary folder) with Face Landmarker and Pose Landmarker at several frame strides, and reports frame rate, frame times, tracker latencies and a gate per setting. Sit in front of the camera as for a take while it runs.
- Environment: `requirements.lock.txt` holds the tested versions (regenerate with `uv pip freeze --color never`); model revisions are pinned in `config.py` (Whisper) and `scripts/download_models.py` (MediaPipe, with SHA-256). Data lives where `palmcards/paths.py` says (`PALMCARDS_DATA` overrides; tests always use temporary folders). `pytest` is headless; hardware checks follow `docs/hardware-smoke-test.md`. Accuracy claims need labelled takes (`docs/evaluation.md`, `scripts/evaluate.py`); gaze is checked against prompted takes (`main.py --gaze-check`, then `scripts/evaluate.py --gaze RUN [--sweep]`); every take's metrics as a table: `scripts/evaluate.py --table [RUN ...] [--csv OUT]` (a row per take, a `missing` column saying why a value is empty); alignment performance: `scripts/profile_align.py`.
- macOS needs Camera and Microphone permission for the terminal app running Python (System Settings > Privacy & Security).

## Milestones

1. **Mirror:** webcam feed mirrored in a window at ~30 fps, with a sample text block rendered via Pillow in the demo style.
2. **Notes:** load `.txt`/`.md`/`.docx` into sections/sentences/words; parse delivery marks; unit tests.
3. **Hands:** landmark debug view; pose classifier (`ONE`, `TWO`, `FLAT`, `OPEN`, `L`, `PINCH`, `FIST`) with the ~150 ms stability rule and a timestamped pose/event log; relative hand-box cursor with edge scrolling; Browse by word/sentence/paragraph, Focus (pinch or fold), drop the hand to back out; state label and fingertip dots.
4. **Rehearse:** closed fist from Prepare starts a take after the 3-2-1 count-in; command zone; flick for next section; open palm in the zone held 1.5 s stops the take and goes to Review; audio recorded to WAV per take.
5. **Speech:** Whisper transcription with word timestamps; alignment to the notes.
6. **Cues and Review:** verdicts per mark; Review mode with the same grammar: browse sentences with verdict chips, fold to focus for full verdicts, L-hand tilt dials through takes, pinch + lift drills a sentence, closed fist starts a new take.
6b. **Follow:** the notes follow your voice during a take, the flick stays as the override.
    - Live recognition: first try a small Whisper model re-run on the last ~4 s of audio every ~0.5 s; if the lag is over ~1 s or the frame rate drops, switch to Apple's on-device speech recogniser (streams; needs `pyobjc` and Speech Recognition permission).
    - Match live words with the M5 aligner, but only against the current section plus the start of the next. Advance only after ~3 words in a row match ahead, so a stray match can't jump.
    - Highlight the current sentence inside the section panel (teleprompter-style). Show the next section when its predecessor's last sentence *starts*, to hide the lag.
    - Going off script stalls the follow (correct); a flick moves on by hand and corrects a wrong jump.
    - The live match is display only; the full transcription after the take stays the record. Each `sections` entry in `session.json` notes whether it came from the voice or a flick.
7. **Metrics:** done (2026-09-26): speech (pace, fillers, unplanned long pauses, restarts, ad-libs) and hand-shape changes (`palmcards/metrics.py`); face, pose and hand features recorded during every take (`take-NN.face.npz`) with the eye calibration in the first count-in (stage 1); gaze while speaking, screen vs away, and the gaze check (stage 2: screen vs away held-out kappa 0.65-0.79; camera vs notes 0.36-0.62, not reported); posture against the calibration, hand movement and face touches from the features, without `--trace` (stage 3; face touches checked on one scripted take); on screen / away per sentence in the focused Review panel, a take summary card while browsing Review, and the per-take CSV with the gaze, posture and movement columns (`scripts/evaluate.py --table`, stage 4).
    - Face and pose run only during calibration and takes, never in Prepare or Review, at the frame strides the stage-0 benchmark set (`BODY` in `config.py`, `scripts/bench_vision.py`). Observations only, None + reason when the evidence is thin.
8. **Edit gestures + LLM:** Operate and Commit in Prepare: word options ring (LLM synonyms, stress node, hear-it node via macOS `say`) with L-hand preview, sentence tone dial, faded LLM-suggested delivery marks toggled by L-hand, two-L-hand length stretch; pinch + lift commits; glyph-scramble while the LLM works.
9. **Export:** revised notes back to the original format. Done as `python -m palmcards.export RUN [--revision ID] [--format txt|md|docx] [--out PATH]` (`palmcards/export.py`): always a new file (default: the session's `exports/`), unedited sentences keep their markup, edited ones are rebuilt from their marks; what a format can't hold is reported.
