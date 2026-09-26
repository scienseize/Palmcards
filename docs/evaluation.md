# Evaluating PalmCards

The unit tests check that the code does what it says: they use synthetic tones for stress and intonation, and made-up word timings for pauses and pace. They do not show that a verdict matches what a listener would say. That needs real, consented takes labelled by people. **None have been collected yet**, so every accuracy claim is pending. Collection starts after milestone 8's LLM work, with real people.

## Collecting takes

- **Consent.** Every speaker agrees in writing to being recorded and to the recordings being used for evaluation. The labels file records `"consent": true`; `scripts/evaluate.py` refuses files without it. Recordings stay on the machine they were made on unless the speaker agrees otherwise.
- **Spread.** Cover different voices (pitch range, age, accent), speaking styles (read, memorised, improvised), microphone distances (laptop at arm's length, closer, further), rooms (quiet, some noise), and languages. Scoring is calibrated for English only; other languages are evaluated for pauses and pace.
- **Hold-out.** Before any tuning, mark each take `"split": "tune"` or `"split": "holdout"`, keeping about a third as hold-out and never mixing a speaker across splits. Thresholds in `palmcards/config.py` (`CUES`, `ALIGN`) are tuned on `tune` only. Hold-out results are reported once per change.

## Labelling

Label each take as its own JSON file, without looking at PalmCards' verdicts:

```json
{
  "session": "20260925-101345-sample_notes-3fa9c1",
  "take": 1,
  "labeller": "L1",
  "consent": true,
  "split": "holdout",
  "marks": [{"sentence": 2, "mark": 0, "label": "hit"}],
  "words": [{"sentence": 0, "word": 3, "start": 1.84}],
  "gestures": {"from": 0.0, "to": 312.0, "intended": [[41.2, 43.0, "start"], [120.5, 121.4, "flick"]]}
}
```

- `marks`: `sentence` and `mark` index `Notes.sentences` and `Sentence.marks` in the take's notes revision. `label` is `hit`, `missed` or `unclear`; a person's `unclear` is not used as a reference.
- `words`: where the word starts, in seconds into the take (optional; for alignment error).
- `gestures`: a stretch of the session's gesture log in app seconds, and the intervals where the person meant to act (from the screen recording or notes taken at the time). Any gesture-driven action outside those intervals is a false trigger. Key presses never count.

Two labellers per take where possible. Report their agreement with each other next to PalmCards' agreement with each of them.

## Measuring

```sh
.venv/bin/python scripts/evaluate.py labels/*.json --split holdout --json results.json
```

| Metric | Meaning |
| --- | --- |
| `marks.agreement` | Where both the person and PalmCards judged hit or missed, how often they agree |
| `marks.false_hits` / `false_misses` | PalmCards said hit where the person heard a miss, and the reverse |
| `marks.abstention_rate` | Share of labelled marks PalmCards called unclear: honest, but a cost in coverage |
| `words.median_error_s` / `p90_error_s` | Alignment timing error of labelled words; `unaligned` counts words PalmCards missed |
| `gestures.per_minute` | False triggers per minute of use |

## Measured so far

**Alignment speed** (`scripts/profile_align.py`, 2026-09-26, MacBook Air, Apple silicon). Synthetic scripts with 5% misheard words, 3% fillers and a restart every 100 words, with the fill compiled (numba):

| Words (notes and transcript) | Seconds | Peak memory |
| --- | --- | --- |
| 600 | 0.04 | 11 MB |
| 1200 | 0.12 | 43 MB |
| 2500 | 0.43 | 187 MB |
| 5000 | 1.57 | 744 MB |

Before compiling, 600 words took 15.3 s and 1200 words 67.7 s. A script that repeats one passage costs the same. The tested limit is 5000 words (about a 40-minute talk), where memory (three note × transcript similarity matrices) is the limit; beyond that, split the notes.

**Live follow** (`scripts/bench_live.py`, recorded takes replayed in real time): median live-word lag 0.63–0.75 s with Whisper base read every 0.3 s. The camera frame rate held at 30 fps. No wrong section jumps.

**Verdict accuracy against people**: not measured yet.
