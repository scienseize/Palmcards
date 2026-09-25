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
| Gaze | MediaPipe Face Landmarker (head pose + iris) |
| Posture | MediaPipe Pose Landmarker (lower frame rate than hands) |
| Audio capture | sounddevice; each take streamed to disk as it is recorded (`palmcards/recording.py`), recoverable after a crash |
| Transcription | mlx-whisper (or whisper.cpp), after each take, with word timestamps; language set per session. Behind one interface (`palmcards/asr.py`, engine picked by `SPEECH.backend`), which also has the live stream for 6b: Whisper base re-read every 0.3 s by default, Apple's on-device recogniser (`palmcards/asr_apple.py`, pyobjc) as the alternative |
| Prosody | librosa: `pyin` for pitch, RMS for loudness |
| Notes parsing | python-docx, markdown-it-py |
| Script alignment | rapidfuzz (fuzzy match transcript words to note words) |
| LLM | any chat API, or local via Ollama; behind one small interface |
| Storage | JSON files per session |

MediaPipe `.task` model files live in `models/` and are downloaded by a setup script, not committed.

## Modes and gestures

Mirroring: flip the frame once at capture; do all hit-testing in the flipped coordinate space.

The gesture grammar is adapted from Kat's "Gestural editing/writing" demo (@poetengineer__). A frame-by-frame reference sheet of the demo is kept locally at `docs/local/kat-gesture-reference.jpg`; it is gitignored and not published, since the frames are Kat's.

### The grammar (applies in Prepare and Review)

1. **Hand shape picks the scope.** One finger = word, two fingers together = sentence, flat hand = paragraph/section. Holding the shape puts you in Browse at that level; the highlight follows your hand.
2. **Closing the hand focuses.** Pinch (word) or fold the extended fingers down onto the thumb (sentence, paragraph). Everything else dims and the focused unit enlarges.
3. **A second shape operates on the focus.** Open palm spreads options; an L-hand (thumb + index out) turns a knob through options or turns a dial; two L-hands stretch. An L starts a control; once started it keeps tracking while the index stays up, even if the thumb drifts in.
4. **Pinch and lift commits.** Pinch, then raise the pinched hand ~15% of frame height within ~0.6 s. The change is applied and you zoom back out to Browse at the same level.
5. **Drop the hand to back out.** Hand out of frame (or below the bottom band) for 1 s while focused = discard the preview, return to Browse. The original always stays in any options ring, so undo = focus again and pick the original.

The cursor is **relative**, not touch: a comfortable "hand box" on the right half of the frame maps onto the text block on the left (Kat's hand stays at chest height on the right while the highlight moves on the left). Vertical movement picks the line, horizontal picks the word. Moving into the top or bottom 10% of the hand box scrolls the notes.

### Prepare

| Level | Browse | Focus | Operate | Commit |
| --- | --- | --- | --- | --- |
| Word | One finger up | Pinch | Open palm: ring of alternatives (LLM synonyms) plus a **stress** node and a **hear it** node (macOS `say`). turn the L-hand like a knob (~15° per node, relative to where it starts) to preview a node live in the text | Pinch + lift |
| Sentence | Two fingers together | Fold fingers to thumb | L-hand tilt = **tone dial** (tilt toward screen right = conversational/warm, left = formal/cold), dial is relative to the angle when the dial appears. Open palm: spread LLM-suggested delivery marks for this sentence, faded; L-hand points to toggle each | Pinch + lift |
| Paragraph / section | Flat hand, fingers together | Fold fingers to thumb | Two L-hands: distance between index tips = **length** (apart = fuller, together = shorter), relative to the distance when both hands appear | Pinch + lift (either hand) |

- Closed fist raised into view and held 1 s from Prepare: start a take (enters Rehearse). The fist must be the hand's first pose since it came into view: a fist formed from another pose (a slow pinch, a flat hand curling, a hand resting closed between gestures) never starts a take. To start one while browsing, drop the hand and raise a fist.

### Rehearse (locked except inside a command zone, top-right of the frame)
- Closed fist raised and held 1 s: start take after 3-2-1 count-in
- Flick inside zone: next section (a sideways swing of the hand, usually from the wrist). From milestone 6b the notes follow your voice on their own and the flick is the manual override.
- Open palm inside zone held 1.5 s: stop take (goes to Review)
- All other hand movement is logged as data (gesture amount, fidgeting, face touching), never treated as a command.

### Review (same grammar)
- Two fingers together: browse sentences; every delivery mark is drawn as a chip coloured by its verdict (green hit, red missed, grey unclear, faint text for skipped)
- Fold to focus: the sentence enlarged with its verdicts listed under it (each mark's verdict and reason, pace against the take, fillers; gaze comes with milestone 7)
- L-hand turned like a knob (~20° per take, relative to where it starts, like the options ring): dial through the takes that said the focused sentence. Each sentence shows the latest take that said it until dialled; the pick stays after backing out, until a newer take says the sentence again.
- Pinch + lift on a focused sentence: drill it. The count-in, then only that sentence on screen; no flick; open palm in the zone stops it. A drill take is aligned against that sentence only and its pace is judged against the last full take. Pinch + lift at word or paragraph level does nothing in Review.
- Closed fist raised and held 1 s: new full take
- Open palm inside the command zone held 1.5 s: back to Prepare, to edit before the next take (the zone is shown in Review with this hint)

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
| `/` | short pause | gap >= 0.3 s from the last word said before the note word (so a restart is measured on its final attempt); a filler in the gap is a miss; words Whisper was unsure of are ignored |
| `//` | long pause | gap >= 0.7 s |
| `*word*` | stress | word's peak (90th percentile, window padded 0.05 s) >= +3 dB louder or +2 semitones higher than the other words: a line fitted through their peaks over time (pitch and loudness drift down through a sentence) with 4+ other words, else their median |
| `[slow]` / `[fast]` | pace | sentence WPM < 85% / > 115% of the take average (spoken sentences without a pace mark); fewer than 4 aligned words = unclear |
| `[rise]` / `[fall]` | ending intonation | Theil-Sen slope of pitch over the last 0.5 s of voiced sound in the sentence, at least 3 semitones/s up or down |

Each mark gets a verdict: `hit`, `missed`, or `unclear` (not enough voiced audio to judge). "Unclear" is a valid answer; never invent a verdict. A sentence that was not spoken is `skipped`, not `missed`.

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
│   ├── take-01.verdicts.json        # a verdict per delivery mark
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
  "schema": 2,
  "id": "9c1f0e7a2b4d6e8f",
  "notes": "/abs/path/to/notes.md",
  "source": {"file": "source/notes.md", "sha256": "…", "size": 1234},
  "revisions": [{"id": "r1a2b3c4d5e6f", "file": "notes/r1a2b3c4d5e6f.json", "hash": "…", "parser": 1,
                 "parent": null, "created": "2026-09-25T10:13:45", "provenance": "imported"}],
  "gesture_log": "gesture-logs/20260925-101345.jsonl",
  "language": "en",
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
| `sections` | section indices (0-based, as in `Notes.sections`) with the time into the take each one came up; the first is always `t = 0` |
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

`take-01.transcript.json`: `{"take", "wav", "model", "language", "t_start", "offset_s", "text", "words": [{"text", "start", "end", "probability"}]}`. `offset_s` is the leading silence trimmed before Whisper ran; word times are already on the app clock (`t_start + offset_s + Whisper's time`).

`take-01.prosody.npz`: arrays `t` (frame centres, app clock), `f0` (Hz, NaN where pyin found no voice), `rms_db` (dBFS). Made once per take in the transcription worker, alongside Whisper; the loudness gate and semitones are applied when it is read, so tuning `CUES` never re-runs pyin.

`take-01.verdicts.json` (`palmcards/cues.py`):

```json
{"take": 1, "take_wpm": 193.0, "baseline": "take",
 "counts": {"hit": 6, "missed": 4, "unclear": 0, "skipped": 0},
 "thresholds": {"short_pause_s": 0.3, "...": "every CUES value used"},
 "sentences": [{"sentence": 2, "status": "spoken", "wpm": 171.2, "fillers": ["um"],
                "marks": [{"kind": "long_pause", "word": 7, "verdict": "hit", "value": 0.9, "threshold": 0.7,
                           "reason": "0.90 s pause"}]}]}
```

`marks` follow `Sentence.marks` order; `word` is as in `Mark.word`. `value`/`threshold` are seconds (pauses), a ratio to `take_wpm` (pace), `{"loud_db", "pitch_st"}` (stress) or semitones per second (ending). `baseline` is `given` for a drill, whose `take_wpm` is the last full take's. A filler counts toward the sentence it falls in, or the next one said after it.

Later milestones add their results to each take (metrics) rather than inventing new files.

Gesture log lines are `{"t": ..., "kind": ..., ...}`. Kinds: `pose` (hand, pose), `browse` / `focus` (level), `fold`, `pinch_lift`, `op`, `commit` / `back` (level, op, value), `commit_stub`, `drill` (sentence), `drop_start`, `idle`, `mode` (prepare / count_in / rehearse / review), `zone` (command: flick / hold), `take_start`, `section`, `take_stop` (take, duration_s, wav), `transcribed` (take, seconds), `mic_error`, `record_error` (error), `screenshot`. Trace lines are `{"t": ..., "hands": [{"label": "Left", "points": [[x, y] × 21]}]}` in mirrored-frame pixels.

## Code layout

```
palmcards/
├── main.py              # entry point, mode loop
├── palmcards/
│   ├── capture.py       # camera + audio capture
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
│   ├── align.py         # transcript <-> notes alignment
│   ├── follow.py        # live words -> where the speaker is in the notes (6b)
│   ├── player.py        # debug player: a take's audio with the notes highlighted as they're said
│   ├── replay.py        # replay recorded hand landmarks through the gesture code (regression)
│   ├── cues.py          # planned marks vs measured delivery -> verdicts
│   ├── review.py        # which take each sentence shows in Review, verdict lines
│   ├── metrics.py       # gaze, posture, filler rate, pace
│   ├── llm.py           # optional LLM helper behind one interface
│   └── session.py       # takes and results as JSON, export
├── models/              # MediaPipe .task files (gitignored)
├── samples/             # sample notes with markup for testing
│   └── gestures/        # recorded hand landmarks + expected events, for the replay tests
└── tests/
```

## How to work in this repo

- Build one milestone at a time (below). Finish, run, commit, then move on.
- Pure logic (`notes.py`, `cues.py`, `align.py`, `review.py`) gets unit tests with pytest; `cues.py` uses synthetic audio (tones gliding up and down, a louder word) for stress and intonation. Camera and gesture code is tested by running the app; the user will report what they see or share screenshots.
- Gesture regression: `samples/gestures/*.json` are stretches of `main.py --trace` recordings (MediaPipe hand landmarks only, no video) with what the app recognised live. `tests/test_gesture_replay.py` replays them through `ModeMachine` and must reproduce it; `python -m palmcards.replay` prints the same check. Samples marked `known_issue` are strict expected failures that document a bug until it's fixed. New samples: record with `--trace`, add the window to `SEGMENTS` in `scripts/cut_gesture_samples.py`, run it. Never commit video.
- Keep camera/gesture code runnable standalone (`python -m palmcards.gestures` shows a debug view with landmarks and the detected gesture name).
- Speech runs offline on recorded sessions: `python -m palmcards.speech sessions/<run>` (transcribe, judge and report; takes from before milestone 6 get their verdicts; `--realign` re-aligns and re-judges saved transcripts without Whisper or pyin, for tuning `ALIGN` and `CUES`) and `python -m palmcards.player sessions/<run> [--take N]` (hear a take with the notes highlighted as they're said).
- Live recognition (6b) is benchmarked offline: `python scripts/bench_live.py sessions/<run> --take N [--model REPO] [--step S] [--where thread|process] [--camera SECONDS] [--json OUT]` replays a take in real time and reports live-word lag, wrong section jumps, sentence tracking and (with `--camera`) the frame rate; `--rescore OUT.json` re-scores saved runs after tuning `FOLLOW`.
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
7. **Metrics:** gaze, posture, fidgeting, filler rate.
8. **Edit gestures + LLM:** Operate and Commit in Prepare: word options ring (LLM synonyms, stress node, hear-it node via macOS `say`) with L-hand preview, sentence tone dial, faded LLM-suggested delivery marks toggled by L-hand, two-L-hand length stretch; pinch + lift commits; glyph-scramble while the LLM works.
9. **Export:** revised notes back to the original format.
