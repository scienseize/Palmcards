# Evaluating PalmCards

The unit tests check that the code does what it says: they use synthetic tones for the pitch and loudness measures, and made-up word timings for pace, pauses and fillers. They do not show that a measurement matches what a listener would hear (a filler Whisper didn't write down is a filler PalmCards didn't count). That needs real, consented takes labelled by people. **None have been collected yet**, so every accuracy claim is pending. Collection starts after milestone 8's LLM work, with real people.

## Study and hypotheses

The study (design note, Evaluation tab): each participant rehearses two passages of similar length and difficulty, one with PalmCards and one the usual way (reading from paper or a screen and recording on a phone), with passage and condition order counterbalanced. The final delivery of each passage is recorded for blind rating.

| | Hypothesis | Measure |
| --- | --- | --- |
| H1 | Delivery gets more fluent from the first to the last take with PalmCards, because each take is set beside the ones before it | Fillers per minute and long pauses per minute, first against last full take of each passage: `fillers_per_min` and `long_pauses_per_min` in the take table |
| H2 | Blind listeners rate final takes made with PalmCards higher on expressiveness than final takes made the usual way, because the speaker can compare takes side by side and replay any sentence from any take | Listener rating, 1 to 5 |
| H3 | While speaking, people look at the screen more and away less with PalmCards, because the notes sit next to the camera instead of on a desk | Share of speaking time looking at the screen (camera or notes) against away: `gaze_screen_share` and `gaze_away_share` in the take table |
| H4 | False triggers during takes stay rare enough that participants don't report them as a problem, which tests the command zone directly | `gestures.per_minute` |

H1 was changed on 2026-09-26, when delivery marks were removed: it had measured cue adherence, the share of planned marks (pauses, stress, pace, endings) hit per take. With no marks, H1 measures fluency from the take metrics instead. Fillers depend on Whisper writing them down, which it does not always do even with the filler prompt, so H1 needs the filler counts checked against labels (below) before its result is reported.

H3 originally measured eye contact, the share of speaking time spent looking at the camera. Milestone 7 couldn't reliably tell the camera from the notes (see [Limitations](#limitations)), so H3 now compares screen with away and makes no claim about looking into the lens. For the comparison to be fair, the usual-way delivery has to be measured the same way: filmed by the Mac's camera after a PalmCards calibration.

## Collecting takes

- **Consent.** Every speaker agrees in writing to being recorded and to the recordings being used for evaluation. The labels file records `"consent": true`; `scripts/evaluate.py` refuses files without it. Recordings stay on the machine they were made on unless the speaker agrees otherwise.
- **Spread.** Cover different voices (pitch range, age, accent), speaking styles (read, memorised, improvised), microphone distances (laptop at arm's length, closer, further), rooms (quiet, some noise), and languages. Fillers are detected in English only (`SPEECH.filler_languages`); other languages get the other measures.
- **Hold-out.** Before any tuning, mark each take `"split": "tune"` or `"split": "holdout"`, keeping about a third as hold-out and never mixing a speaker across splits. Settings in `palmcards/config.py` (`METRICS`, `ALIGN`) are tuned on `tune` only. Hold-out results are reported once per change.

## Labelling

Label each take as its own JSON file, without looking at PalmCards' measurements:

```json
{
  "session": "20260925-101345-sample_notes-3fa9c1",
  "take": 1,
  "labeller": "L1",
  "consent": true,
  "split": "holdout",
  "words": [{"sentence": 0, "word": 3, "start": 1.84}],
  "gestures": {"from": 0.0, "to": 312.0, "intended": [[41.2, 43.0, "start"], [120.5, 121.4, "flick"]]}
}
```

- `words`: where the word starts, in seconds into the take (optional; for alignment error).
- `gestures`: a stretch of the session's gesture log in app seconds, and the intervals where the person meant to act (from the screen recording or notes taken at the time). Any gesture-driven action outside those intervals is a false trigger. Key presses never count.

Two labellers per take where possible. Report their agreement with each other next to PalmCards' agreement with each of them.

Not labelled yet, and needed before H1 is reported: the fillers a person hears in a take (word and time), to measure how many of them Whisper, and so PalmCards, counts. `scripts/evaluate.py` doesn't read them yet.

## Measuring

```sh
.venv/bin/python scripts/evaluate.py labels/*.json --split holdout --json results.json
```

| Metric | Meaning |
| --- | --- |
| `words.median_error_s` / `p90_error_s` | Alignment timing error of labelled words; `unaligned` counts words PalmCards missed |
| `gestures.per_minute` | False triggers per minute of use |

## Gaze

Gaze (milestone 7) is checked with prompted takes rather than labels: the prompt is the reference.

```sh
.venv/bin/python main.py --gaze-check          # raise a fist; follow the prompts; the take stops itself
.venv/bin/python scripts/evaluate.py --gaze RUN [--take N] [--sweep] [--json OUT]
```

Each gaze-check take calibrates in its count-in, then shows `GAZE.check_each` prompts of each target (look into the camera, read the notes, look away in a named direction) for `GAZE.check_step_s` each, shuffled. The report compares each prompt with the class of every face reading inside it, leaving out the first `GAZE.check_settle_s` (reading the prompt, moving the eyes):

| Metric | Meaning |
| --- | --- |
| `screen.agreement`, `screen.kappa`, `screen.recall` | Screen (camera or notes) against away, what the take metric reports: how often the class is the prompt, Cohen's kappa (agreement beyond what the class frequencies alone give), and per side the share of its readings given its class |
| `agreement`, `kappa`, `recall` | The same with camera, notes and away kept apart (not validated, see [Limitations](#limitations)) |
| `unclear_share` | Readings with no face, a blink, or a missing value |
| `confusion` | Prompt × class counts |
| `medians` | Each prompt's median head yaw, pitch and iris position: what the settings have to separate |

`--sweep` scores the take again over a grid of `GAZE` scale floors and radii, best screen-vs-away kappa first. Tune on one check take and confirm on another, recorded after the change; a setting chosen on a take always looks better on that take. Prompted gaze is easier than a real talk (the eyes go where they are told and stay), so a check shows the classifier can tell the targets apart, not how often it is right while someone speaks. Takes from other people need their consent, as for labelled takes.

## The take table

```sh
.venv/bin/python scripts/evaluate.py --table [RUN ...] [--csv takes.csv]
```

One CSV row per take, every session under the data folder unless RUNs are given: the take (session, number, start, length, status, drill, gaze check, notes revision, calibration, metrics version) and the take metrics: speech (`pace_wpm`, `fillers_per_min`, `long_pauses`, `long_pauses_per_min`, `restarts`, `ad_libs`), voice (`pitch_range_st`, `pitch_sd_st`, `loudness_range_db`, `voiced_s`), hands (`shape_changes_per_min`, `hand_in_view_share`, `wrist_movement_palms_s`, `fingertip_movement_palms_s`, `face_touches`, `face_touch_s`), gaze (`gaze_screen_share`, `gaze_away_share`, `gaze_unclear_share`) and posture (`shoulder_tilt_deg`, `tilted_share`, `head_height_change`, `head_dropped_share`). An empty value is one the metrics left None; the `missing` column says why (too little evidence, recorded before a feature existed, no calibration, not analysed: `python -m palmcards.speech RUN --realign` computes the metrics of older takes again). Refuses to overwrite a file.

## Measured so far

**Gaze** (2026-09-26, one person, MacBook Air, window not full screen; five gaze-check takes in three sessions; `docs/implementation-progress.md` has the details). Screen vs away (what H3 measures), with the `GAZE` settings chosen on the first two takes: kappa 0.84 and 0.87 on those two, and **held-out kappa 0.65 to 0.79** (0.65, 0.79 and 0.72) on the three recorded afterwards. Over all five, screen recall was 0.95 and away recall 0.82. Camera vs notes didn't hold up; see [Limitations](#limitations).

**Alignment speed** (`scripts/profile_align.py`, 2026-09-26, MacBook Air, Apple silicon). Synthetic scripts with 5% misheard words, 3% fillers and a restart every 100 words, with the fill compiled (numba):

| Words (notes and transcript) | Seconds | Peak memory |
| --- | --- | --- |
| 600 | 0.04 | 11 MB |
| 1200 | 0.12 | 43 MB |
| 2500 | 0.43 | 187 MB |
| 5000 | 1.57 | 744 MB |

Before compiling, 600 words took 15.3 s and 1200 words 67.7 s. A script that repeats one passage costs the same. The tested limit is 5000 words (about a 40-minute talk), where memory (three note × transcript similarity matrices) is the limit; beyond that, split the notes.

**Live follow** (`scripts/bench_live.py`, recorded takes replayed in real time): median live-word lag 0.63–0.75 s with Whisper base read every 0.3 s. The camera frame rate held at 30 fps. No wrong section jumps.

**Filler counts and pace against people**: not measured yet.

## Limitations

**Gaze: camera vs notes (milestone 7).** Gaze was first built with three classes (camera, notes and away) so that H3 could measure eye contact. That split didn't hold up across sessions:

- **Calibration drift.** In every gaze-check session, head pitch during the prompts read 3-9° higher than in the calibration's camera step. This happened in silent readings too, so jaw movement from speaking isn't the cause, and it was already there in a take's first 2 s. When the notes sit below the camera, the whole camera-to-notes difference is only 2.4-4° of pitch and 1.6-1.8° of yaw, which is smaller than the drift. The cause is unknown, and it can't be investigated from the recordings because the calibration's raw readings aren't saved.
- **Held-out agreement.** Camera, notes and away as three classes scored kappa 0.36 and 0.62 on held-out takes (0.45 on a tuning take). One calibration couldn't separate camera from notes at all (separation 1.9999, needs 2.0).

So the take metric keeps the camera and notes counts separately but reports only screen vs away (`split.validated: false`). Review says ON SCREEN / AWAY, never "eye contact", and H3 measures screen vs away. Screen vs away was measured on one person, one Mac, and prompted takes. Prompted gaze is easier than a real talk, so that result shows the classifier can tell the targets apart, not how often it is right while someone speaks.
