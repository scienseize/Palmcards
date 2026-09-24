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
| Text rendering | Pillow with a TTF monospace font, pasted onto the frame (OpenCV's built-in fonts are too crude) |
| Hands | MediaPipe Hand Landmarker + Gesture Recognizer (built-in classes: Closed_Fist, Open_Palm, Thumb_Up, Pointing_Up) |
| Gaze | MediaPipe Face Landmarker (head pose + iris) |
| Posture | MediaPipe Pose Landmarker (lower frame rate than hands) |
| Audio capture | sounddevice, buffered per take, saved as WAV |
| Transcription | mlx-whisper (or whisper.cpp), after each take, with word timestamps; language set per session |
| Prosody | librosa: `pyin` for pitch, RMS for loudness |
| Notes parsing | python-docx, markdown-it-py |
| Script alignment | rapidfuzz (fuzzy match transcript words to note words) |
| LLM | any chat API, or local via Ollama; behind one small interface |
| Storage | JSON files per session |

MediaPipe `.task` model files live in `models/` and are downloaded by a setup script, not committed.

## Modes and gestures

Mirroring: flip the frame once at capture; do all hit-testing in the flipped coordinate space.

The gesture grammar is adapted from Kat's "Gestural editing/writing" demo. Frame-by-frame reference: `docs/kat-gesture-reference.jpg`.

### The grammar (applies in Prepare and Review)

1. **Hand shape picks the scope.** One finger = word, two fingers together = sentence, flat hand = paragraph/section. Holding the shape puts you in Browse at that level; the highlight follows your hand.
2. **Closing the hand focuses.** Pinch (word) or fold the extended fingers down onto the thumb (sentence, paragraph). Everything else dims and the focused unit enlarges.
3. **A second shape operates on the focus.** Open palm spreads options; an L-hand (thumb + index out) points at options or turns a dial; two L-hands stretch.
4. **Pinch and lift commits.** Pinch, then raise the pinched hand ~15% of frame height within ~0.6 s. The change is applied and you zoom back out to Browse at the same level.
5. **Drop the hand to back out.** Hand out of frame (or below the bottom band) for 1 s while focused = discard the preview, return to Browse. The original always stays in any options ring, so undo = focus again and pick the original.

The cursor is **relative**, not touch: a comfortable "hand box" on the right half of the frame maps onto the text block on the left (Kat's hand stays at chest height on the right while the highlight moves on the left). Vertical movement picks the line, horizontal picks the word. Moving into the top or bottom 10% of the hand box scrolls the notes.

### Prepare

| Level | Browse | Focus | Operate | Commit |
| --- | --- | --- | --- | --- |
| Word | One finger up | Pinch | Open palm: ring of alternatives (LLM synonyms) plus a **stress** node and a **hear it** node (macOS `say`). L-hand points to preview a node live in the text | Pinch + lift |
| Sentence | Two fingers together | Fold fingers to thumb | L-hand tilt = **tone dial** (tilt toward screen right = conversational/warm, left = formal/cold), dial is relative to the angle when the dial appears. Open palm: spread LLM-suggested delivery marks for this sentence, faded; L-hand points to toggle each | Pinch + lift |
| Paragraph / section | Flat hand, fingers together | Fold fingers to thumb | Two L-hands: distance between index tips = **length** (apart = fuller, together = shorter), relative to the distance when both hands appear | Pinch + lift (either hand) |

- Closed fist held 1 s from Prepare: start a take (enters Rehearse).

### Rehearse (locked except inside a command zone, top-right of the frame)
- Closed fist held 1 s: start take after 3-2-1 count-in
- Flick inside zone: next section
- Open palm inside zone held 1.5 s: stop take (goes to Review)
- All other hand movement is logged as data (gesture amount, fidgeting, face touching), never treated as a command.

### Review (same grammar)
- Two fingers together: browse sentences; each shows verdict chips
- Fold to focus: full verdicts for that sentence (marks hit/missed/unclear, pace, fillers, gaze)
- L-hand tilt: dial through takes (take 1, 2, 3...) for the focused sentence
- Pinch + lift on a focused sentence: drill it (loop just that sentence as a mini take)
- Closed fist held 1 s: new full take

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
- Smooth the cursor fingertip with a One Euro filter before mapping it to text.
- Dials and stretch are always relative to the value captured when the control appears, then clamped.
- Log every recognized pose and event with a timestamp; the false-trigger measure in the evaluation depends on it.

### Visual feedback (copy Kat's patterns)
- Top-left state label: `BROWSE BY WORD`, `FOCUS BY SENTENCE`, with a second line for the operation (`CHANGE TONE: WARM`, `EXPLORE ALTERNATIVES: LOADING`).
- Yellow dot on the active fingertip, small dots on the other fingertips, cyan dot for a second hand.
- Non-focused text dims; the focused unit enlarges.
- Glyph-scramble animation on text while the LLM is rewriting; it doubles as the loading indicator.
- Radial options ring with curved connectors from the focused word; vertical gauge for the tone dial; a straight line between the two index tips for stretch.

## Delivery marks (plain ASCII in the uploaded file)

| Markup | Meaning | Check after a take (starting thresholds, tune later) |
| --- | --- | --- |
| `/` | short pause | gap >= ~0.3 s between word timestamps |
| `//` | long pause | gap >= ~0.7 s |
| `*word*` | stress | word's peak loudness or pitch clearly above sentence median |
| `[slow]` / `[fast]` | pace | sentence WPM < 85% / > 115% of take average |
| `[rise]` / `[fall]` | ending intonation | pitch slope over last ~0.5 s of voiced audio |

Each mark gets a verdict: `hit`, `missed`, or `unclear` (not enough voiced audio to judge). "Unclear" is a valid answer; never invent a verdict. A sentence that was not spoken is `skipped`, not `missed`.

Whisper tends to drop "um"/"uh": prime it with an initial prompt containing fillers.

## Code layout

```
palmcards/
├── main.py              # entry point, mode loop
├── palmcards/
│   ├── capture.py       # camera + audio capture
│   ├── notes.py         # file loading, sections/sentences/words, cue parsing
│   ├── gestures.py      # landmarks -> gesture events, mode-aware state machine, command zone
│   ├── config.py        # every gesture threshold in one place
│   ├── render.py        # Pillow text overlay onto mirrored frame
│   ├── speech.py        # whisper transcription, prosody features
│   ├── align.py         # transcript <-> notes alignment
│   ├── cues.py          # planned marks vs measured delivery -> verdicts
│   ├── metrics.py       # gaze, posture, filler rate, pace
│   ├── llm.py           # optional LLM helper behind one interface
│   └── session.py       # takes and results as JSON, export
├── models/              # MediaPipe .task files (gitignored)
├── samples/             # sample notes with markup for testing
└── tests/
```

## How to work in this repo

- Build one milestone at a time (below). Finish, run, commit, then move on.
- Pure logic (`notes.py`, `cues.py`, `align.py`) gets unit tests with pytest. Camera and gesture code is tested by running the app; the user will report what they see or share screenshots.
- Keep camera/gesture code runnable standalone (`python -m palmcards.gestures` shows a debug view with landmarks and the detected gesture name).
- macOS needs Camera and Microphone permission for the terminal app running Python (System Settings > Privacy & Security).

## Milestones

1. **Mirror:** webcam feed mirrored in a window at ~30 fps, with a sample text block rendered via Pillow in the demo style.
2. **Notes:** load `.txt`/`.md`/`.docx` into sections/sentences/words; parse delivery marks; unit tests.
3. **Hands:** landmark debug view; pose classifier (`ONE`, `TWO`, `FLAT`, `OPEN`, `L`, `PINCH`, `FIST`) with the ~150 ms stability rule and a timestamped pose/event log; relative hand-box cursor with edge scrolling; Browse by word/sentence/paragraph, Focus (pinch or fold), drop the hand to back out; state label and fingertip dots.
4. **Rehearse:** closed fist from Prepare starts a take after the 3-2-1 count-in; command zone; flick for next section; open palm in the zone held 1.5 s stops the take and goes to Review; audio recorded to WAV per take.
5. **Speech:** Whisper transcription with word timestamps; alignment to the notes.
6. **Cues and Review:** verdicts per mark; Review mode with the same grammar: browse sentences with verdict chips, fold to focus for full verdicts, L-hand tilt dials through takes, pinch + lift drills a sentence, closed fist starts a new take.
7. **Metrics:** gaze, posture, fidgeting, filler rate.
8. **Edit gestures + LLM:** Operate and Commit in Prepare: word options ring (LLM synonyms, stress node, hear-it node via macOS `say`) with L-hand preview, sentence tone dial, faded LLM-suggested delivery marks toggled by L-hand, two-L-hand length stretch; pinch + lift commits; glyph-scramble while the LLM works.
9. **Export:** revised notes back to the original format.
