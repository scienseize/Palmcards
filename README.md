# PalmCards

A gesture-controlled rehearsal mirror for words you have to say out loud. Open your notes, and they float beside your mirrored webcam image. Rehearse them aloud: PalmCards follows your voice through the notes, measures each take (pace, fillers, pauses, pitch range, where you looked, posture) and sets your takes side by side, so you can see how this one went against the last. It observes; it doesn't grade.

Tested on macOS with Apple silicon. Windows is not supported yet.

Everything runs on your Mac: no account, no server. The only network use is the one-time model download.

## Install

You need Python 3.11 or 3.12 (MediaPipe has no wheels for newer versions yet) and [uv](https://docs.astral.sh/uv/).

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt   # the exact tested versions
.venv/bin/python scripts/download_models.py                           # ~1.7 GB, checksummed, pinned revisions
```

`requirements.txt` lists the direct dependencies. If you install from it rather than the lock file, name `"numpy<2"` alongside anything you add (MediaPipe 0.10.21 needs NumPy 1).

### Permissions

macOS asks for these on first use; allow them for the terminal app you run PalmCards from (System Settings > Privacy & Security):

- **Camera**, for the mirror and hand tracking.
- **Microphone**, for takes. Without it, macOS records silence rather than failing; PalmCards warns when a take is silent.
- **Speech Recognition**, only if you switch the speech engine to Apple's recogniser (`SPEECH.backend = "apple"` in `palmcards/config.py`).

## Use

```sh
.venv/bin/python main.py my-talk.md            # .txt, .md or .docx
.venv/bin/python main.py --no-follow notes.txt # don't follow the voice during takes
```

Notes are plain text; headings start sections. (Older versions read delivery marks such as `/`, `*word*` or `[slow]`; they are left out now, and the app says how many it ignored.)

- **Prepare.** Hold a hand in the box on the right of the frame and the highlight follows it in the notes. One finger picks a word, two fingers a sentence, a flat hand a paragraph. Pinch or fold the fingers onto the thumb to focus. Selecting a word shows its **meaning in context** (requires the optional LLM). Open your palm to reveal alternatives, turn an L-hand to choose, then pinch + lift to use one. To **hear it**, browse by sentence, fold to select one, then hold an open palm for about 0.6 s (or press `a`): the whole sentence is spoken. Release and hold again to replay.
- **Rehearse.** Raise a closed fist and hold it for 1 s to start a take after a 3-2-1 count-in. The notes follow your voice. Flick sideways in the top-right zone to move on by hand. An open palm held there for 1.5 s stops the take.
- **Review.** A table sets your last few takes side by side: length, pace, fillers, long pauses, restarts, pitch range, time looking at the screen, face touches, posture. Focus a sentence to see it in every take that said it. Pinch and lift on it to drill just that sentence.

Keys work when gestures won't (press `h` to see them in the app):

| Key | Action |
| --- | --- |
| `t` | start a take |
| `x` | stop it, or cancel the count-in |
| `n` / `b` | next / previous section |
| `j` / `k` | next / previous sentence, or scroll a focused panel |
| `p` | back to Prepare from Review |
| `r` | retry failed analysis |
| `q` | quit |

The full gesture grammar and design are in [CLAUDE.md](CLAUDE.md).

## Your data

Sessions live in `sessions/` in this folder when it exists, otherwise in `~/Library/Application Support/PalmCards/sessions`. Set `PALMCARDS_DATA` to use somewhere else. Each run is one folder holding:

- the audio of each take;
- a byte-for-byte copy of the notes you imported, and the parsed notes each take was recorded with, so later edits to the file never change old results;
- the transcripts and each take's measurements.

```sh
.venv/bin/python -m palmcards.data list
.venv/bin/python -m palmcards.data export RUN out.zip
.venv/bin/python -m palmcards.data delete RUN --yes                 # without --yes it only says what would go
.venv/bin/python -m palmcards.data prune --older-than 90 --yes
```

Nothing is deleted without `--yes`, and a session another PalmCards window has open is never deleted.

## When something goes wrong

- **The app stops mid-take** (a crash, Ctrl-C, a camera failure). The audio is written as it's recorded, so at the next start the take is recovered and marked *interrupted*. At most the last ~2 s may be lost.
- **Analysis fails or is left unfinished.** Press `r`, or run `.venv/bin/python -m palmcards.speech sessions/RUN`.
- **Old sessions**, recorded before PalmCards kept a copy of the notes: re-analysing them needs `--rebind`. That uses the notes file as it is now and marks the result unverified.

## What works, what's planned

Working now:
- gesture and keyboard control, voice follow, and recording with crash recovery;
- transcription (mlx-whisper), alignment to the notes, and each take's measurements;
- Review with the take table and drills; a focused sentence lists every take that said it, and its takes sit beside it as chips: make an L, then point at one to pick the take it plays.

Also working: exporting any version of your notes (`python -m palmcards.export RUN`).

Optional: word meanings, alternatives and tone and length rewrites need a language model, and they are off by default. Selecting a word requests its meaning; opening your palm requests alternatives. Suggestions are shown as previews that you choose to use; only what you explicitly ask about is sent (a word's sentence, a sentence or a paragraph). Sentence **hear it** works without a language model.

- **Cloud (Anthropic):** put `ANTHROPIC_API_KEY=...` in a `.env` file in the repo (gitignored) or in your environment, then run `python main.py --llm anthropic`. It uses `claude-haiku-4-5`. The text you ask about leaves your Mac, and a **CLOUD LLM** chip shows at the bottom left for as long as the cloud is on. The key is never printed, logged or saved. Each call's tokens are logged in the session (`llm-usage.jsonl`, never the text); `python -m palmcards.llm usage` sums them for every session, with a cost estimate (`python -m palmcards.llm usage RUN` for one).
- **On your Mac (Ollama):** install [Ollama](https://ollama.com), run `ollama pull llama3.1:8b`, then `python main.py --llm ollama`. Nothing leaves the Mac.

Each analysed take records observations: pace (for the take and each sentence), fillers per minute, long pauses, restarts, ad-libs, pitch and loudness range, hand movement (more detail with `--trace`), and whether you looked at the screen or away while speaking, against an eye calibration made in the session's first count-in (telling the camera from the notes is not reliable yet, so it isn't reported). Gaze is checked with prompted takes: `python main.py --gaze-check`, then `python scripts/evaluate.py --gaze RUN`. Review shows the take table (bottom right) and, for a focused sentence, each take's pace, fillers, pitch range and how much of it you said looking at the screen; `python scripts/evaluate.py --table --csv takes.csv` exports every take's metrics. It also notes posture against that calibration (shoulder tilt, head height) and, without `--trace`, how much your hands moved and how often a fingertip touched your face.

The measurements are observations with what each rests on; where there is too little to go on, they say so rather than guess. How well they hold up on real takes is still being checked; see [docs/evaluation.md](docs/evaluation.md).

## Development

```sh
.venv/bin/python -m pytest -q          # headless: no camera, microphone or window needed
```

Hardware checks are manual: [docs/hardware-smoke-test.md](docs/hardware-smoke-test.md). Implementation progress is tracked in [docs/implementation-progress.md](docs/implementation-progress.md).
