---
name: session-files
description: Reference for PalmCards' session folder format — the sessions/ tree, session.json (schema 3, revisions, calibrations, takes), every take and alignment field, transcripts, face.npz and prosody.npz, and the gesture-log and trace line kinds. Use when reading, writing, migrating or analysing session data or gesture logs.
---

# Session files

Everything is written under `sessions/` (gitignored). `palmcards/session.py` reads and writes the
session folders; `GestureLog` in `gestures.py` writes the logs. The rules that always apply (crash-safe
recording, supervised analysis, one clock, notes kept not referenced) are in CLAUDE.md; this is the
format reference.

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
│   ├── take-01.prosody.npz          # pitch and loudness per 10 ms frame (cache; the voice metric is recomputed from it)
│   ├── take-01.face.npz             # face, pose and hand features per result during the take (the body's only record
│   │                                #   unless video is on)
│   ├── take-01.mp4                  # only with video on: the take's camera frames (take-NN.mp4.part while recording)
│   ├── take-01.verdicts.json        # legacy: delivery-mark verdicts of takes judged before 2026-09-26, not read
│   ├── llm-usage.jsonl              # one line per LLM call: action, model, tokens, outcome; never the text
│   │                                #   (a call before any take or edit makes the folder, like an edit)
│   └── take-02.wav
├── gesture-logs/
│   ├── 20260925-101345.jsonl        # every pose and event, one JSON object per line
│   └── 20260925-101345.trace.jsonl  # only with `main.py --trace`: raw landmarks for offline replay
└── screens/                         # `s` key screenshots
```

`session.json`:

`session.json`:

```json
{
  "schema": 3,
  "id": "9c1f0e7a2b4d6e8f",
  "notes": "/abs/path/to/notes.md",
  "source": {"file": "source/notes.md", "sha256": "…", "size": 1234},
  "revisions": [{"id": "r1a2b3c4d5e6f", "file": "notes/r1a2b3c4d5e6f.json", "hash": "…", "parser": 2,
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

| Take field | Meaning |
| --- | --- |
| `number`, `wav` | 1-based take number and its WAV file name in the same folder (numbers skip any take file already on disk, so nothing is overwritten) |
| `revision` | the notes revision the take was recorded with; absent on takes from schema 1 until rebound |
| `started` | wall-clock time recording began (after the count-in), ISO 8601 |
| `t_start` | app time of the first audio sample (from the device's timing where available) |
| `duration_s`, `sample_rate` | length of the WAV and its rate |
| `peak` | loudest absolute sample, 0..1; below 0.001 the take is treated as silent (usually missing Microphone permission) |
| `sections` | section indices (0-based, as in `Notes.sections`) with the time into the take each one came up and its `source`: `start` (the first, `t = 0`), `voice`, `key`, or `flick` in takes before 2026-09-27 |
| `metrics` | `palmcards/metrics.py`, observations with what each rests on (None + reason when there is too little): `speech` (pace_wpm, fillers_per_min, long_pauses (count and `longest_s`: silences between words over `METRICS.long_pause_s`) and long_pauses_per_min, restarts, ad_libs, and `sentences` [{sentence, status, wpm (first to last word said, `sentence_min_words` or more; None + `why` where too few or the recording lost audio in it), fillers (the ones in it, or before it since the previous sentence said)}]; metrics before version 5 have `unplanned_long_pauses` instead, which left out pauses a mark asked for), `voice` (`palmcards/prosody.py` over voiced frames while a sentence is being said: `pitch_range_st` (10th-90th percentile, semitones from the speaker's median) and `pitch_sd_st`, `loudness_range_db` (10th-90th percentile), `voiced_s`, and `sentences` [{sentence, pitch_range_st (None under `sentence_min_voiced_s`), voiced_s}]; None + reason without pitch and loudness or under `min_voiced_s`), `hands` (shape_changes_per_min from the gesture log; in_view_share, movement_palms_s (wrists) and fingertip_movement_palms_s, palm widths per second while a hand is in view, from the take's features or, before them, a `--trace` recording; face_touches: count and `seconds` of a fingertip within `METRICS.touch_margin` face widths of the face's outline with the hand at the face's depth by its size (`touch_scale_min`..`max` times the face's width), `touch_min_s` or longer, breaks up to `touch_gap_s` bridged), `gaze` (`palmcards/gaze.py`: each face reading classed camera / notes / away / unclear against the take's calibration, counted only while a sentence is being said, `speech_pad_s` either side: `screen_share` (camera or notes) and `away_share` of the judged readings, `unclear_share` of all, `counts` (camera and notes apart), `split` (`validated: false`, the calibration's `separation`: camera vs notes did not hold up in the gaze checks, so it is kept, not reported), and `sentences` [{sentence, screen, away, unclear, camera, notes}]; None + reason without face features, a usable calibration, or `GAZE.min_frames` judged readings), `posture` (against the take's calibration: `shoulder_tilt_deg` and `head_height_change` (nose above the shoulders, shoulder widths), medians of the change, and `tilted_share` / `head_dropped_share` of pose readings over `METRICS.tilt_deg` / `head_drop`; None + reason without features, a calibration or `min_pose_readings` readings with the shoulders visible), `provenance` (what produced them: notes revision, analysis config, ASR, alignment settings and fillers, the prosody cache's status, the audio the recording lost) |
| `gaze_check` | only on a gaze-check take: `seed`, `t0` (app clock at take start) and `prompts` [{target: camera / notes / away, hint, t0, t1 (s from `t0`)}] |
| `video` | only when video was asked for: `state` (`saved`; `interrupted` + `recovered` after a crash; `off` with the `reason`, e.g. PyAV missing; `failed` with the `error`; `deleted` after `python -m palmcards.data drop-video`), `file` (`take-NN.mp4`), `t_first` (app clock of its first frame: a moment `p` into the video is app time `t_first + p`), `frames`, `dropped` and `gaps` ([app time, frames] per run of dropped frames), `duration_s`, `codec`, `width`, `height`, `bitrate` |
| `vision` | face, pose and hand features during the take: `state` (`recorded`; `off` with the `reason`, e.g. a model missing; `failed` with the `error` when the file couldn't be written), `file` (`take-NN.face.npz`), `calibration` (the id of the session's latest `ok` calibration when the take stopped, or null), `counts` per tracker (`results`, `found`, `deferred`: put off because the frame was late, `skipped`: the model was busy) and `hands.results` |
| `live` | the voice follow during the take (display only, never the record): `engine`, `model`, `state` (`following` / `failed` ...), `words` confirmed, `lag_median_s` / `lag_p90_s` (confirmed after the word's end), `dropped_blocks`, `error` |
| `transcript` | the take's transcript file; absent until transcription finishes |
| `alignment` | the transcript matched to the notes (`palmcards/align.py`); word numbers index the transcript's `words` |
| `verdicts`, `marks` | legacy: only on takes judged before 2026-09-26 (the verdicts file and its counts), kept as found, never written or read now |
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

`take-01.face.npz` (`palmcards/features.py`, written when the take stops; a take cut short by a crash has none): per face result `face_t` (app clock, the frame's capture time), `face_found` (0: no face, the frame is unclear and every other value NaN), `face_yaw` / `face_pitch` / `face_roll` (degrees, from the face's transformation matrix), `face_iris_x` (iris along the eye-corner line, 0..1 from the frame-left corner, both eyes averaged), `face_iris_y` (below the line, in eye widths), `face_eye_open` (lid gap / eye width), `face_box` (x0, y0, x1, y1 px); per pose result `pose_t`, `pose_found`, `pose_tilt` (shoulder line, degrees, + when the frame-right shoulder is lower), `pose_width` (shoulder width / frame width), `pose_head` (nose above the shoulders' midpoint, in shoulder widths), `pose_vis`; per hand-tracking result `hand_t`, `hand_n`, `hand_wrist_move` / `hand_tip_move` (since the previous result, palm sizes), `hand_tip_face` (nearest fingertip to the face box, palm sizes, 0 inside), `hand_y` (highest wrist, fraction of frame height), and from features version 2 `hand_tip_oval` (nearest fingertip to the face's outline, in face widths, 0 inside) and `hand_scale` (that hand's palm size / the face's cheek-to-cheek width: about 0.6-0.7 at the face's depth, larger nearer the camera); the face is the one seen in the last `BODY.face_max_age_s`; and `provenance` (JSON: feature version, MediaPipe version, SHA-256 of both models, every `BODY` setting, frame size, mirrored, take, calibration). Unlike the prosody cache it cannot be made again (it is not made from the video, which only takes recorded with video on have).

`take-01.prosody.npz`: arrays `t` (frame centres, app clock), `f0` (Hz, NaN where pyin found no voice), `rms_db` (dBFS), and `provenance` (JSON: WAV sha256, t_start, rate, extractor and librosa versions, every extraction setting, the actual frame hop). Made once per take in the transcription worker, alongside Whisper; the loudness gate and semitones are applied when it is read, so re-measuring never re-runs pyin. A cache made differently (another WAV, other extraction settings) is measured again; `--realign` reuses it, reporting it `stale` (or `unknown` for a cache from before provenance). The hop always comes from the cache, never from today's `PROSODY`.

Later milestones add their results to each take (metrics) rather than inventing new files.

Gesture log lines are `{"t": ..., "kind": ..., ...}`. Kinds: `pose` (hand, pose), `browse` / `focus` (level), `fold`, `pinch_lift`, `op` (op; a step of the ring's knob: `ring_step`, node, word, dir), `ring_nodes` (nodes: the options ring's nodes when they change), `rewind` (op: a dial, a pointer's op, or `cursor` for a word pinch; back_s; the values undone), `llm` (ask), `commit` / `back` (level, op, value), `commit_stub`, `drill` (sentence), `drop_start`, `idle`, `mode` (prepare / count_in / rehearse / review), `done` (mode: a thumbs-up held; logs before 2026-09-27 have `zone`, command flick / hold), `key` (command, mode, acted), `commit_ignored` (level, op: a pinch + lift with nothing to commit), `undo` (fingers crossed, held, in Prepare), `retry` (an open palm held in Review with failed analysis), `hear` (sentence, word), `palm_hold` (level: an open palm held on the focused unit), `palm_stop` (level: a new palm while it plays), `focus_hold` (reason: `play` or null; `by`: `palm_stop` when the grammar released it), `play` (take, sentence or sentences), `play_stop` (why), `section` (section, source), `take_start`, `section`, `take_stop` (take, duration_s, wav), `transcribed` (take, seconds), `calibration_start`, `calibration` (id, status, reason), `gaze_check` (take, seed), `mic_error`, `record_error` (error), `screenshot`. Trace lines are `{"t": ..., "hands": [{"label": "Left", "points": [[x, y] × 21]}]}` in mirrored-frame pixels.
