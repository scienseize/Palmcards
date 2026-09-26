# PalmCards

A gesture-controlled rehearsal mirror for words you have to say out loud. Open your notes, and they float beside your mirrored webcam image. Mark how each line should sound, then rehearse it aloud. PalmCards follows your voice through the notes and afterwards reports which of your planned delivery marks (pauses, stress, pace, rising or falling endings) you actually hit.

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

Mark your notes in plain text: `/` short pause, `//` long pause, `*word*` stress, `[slow]` / `[fast]` pace, `[rise]` / `[fall]` ending. Headings start sections.

- **Prepare.** Hold a hand in the box on the right of the frame and the highlight follows it in the notes. One finger picks a word, two fingers a sentence, a flat hand a paragraph. Pinch or fold the fingers onto the thumb to focus. On a focused word, an open palm offers **hear it**, which speaks the sentence with that word stressed.
- **Rehearse.** Raise a closed fist and hold it for 1 s to start a take after a 3-2-1 count-in. The notes follow your voice. Flick sideways in the top-right zone to move on by hand. An open palm held there for 1.5 s stops the take.
- **Review.** Every mark shows its verdict: ✓ hit, ✗ missed, ? unclear (not enough evidence to judge), – skipped. Focus a sentence to see why. Pinch and lift on it to drill just that sentence.

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
- the transcripts and verdicts.

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
- transcription (mlx-whisper), alignment to the notes, and verdicts for every mark;
- Review with drills and a dial for stepping through takes.

Also working: stressing or unstressing a word from the options ring (a new version of the notes, `u` to undo), and exporting any version of your notes (`python -m palmcards.export RUN`).

Optional: word alternatives, tone and length rewrites, and suggested marks need a language model, and they are off by default. To use one on your Mac, install [Ollama](https://ollama.com), run `ollama pull llama3.1:8b`, and set `LLM.provider = "ollama"` in `palmcards/config.py`. Only what you explicitly ask about is sent (one sentence or paragraph, to localhost). Suggestions are shown as previews that you choose to use. Cloud models are not supported.

Each analysed take also records observations: pace, fillers per minute, unplanned long pauses, restarts, ad-libs, hand movement (more detail with `--trace`), and whether you looked at the screen or away while speaking, against an eye calibration made in the session's first count-in (telling the camera from the notes is not reliable yet, so it isn't reported). Gaze is checked with prompted takes: `python main.py --gaze-check`, then `python scripts/evaluate.py --gaze RUN`. Review shows the latest take's summary (bottom right) and, for a focused sentence, how much of it you said looking at the screen; `python scripts/evaluate.py --table --csv takes.csv` exports every take's metrics. It also notes posture against that calibration (shoulder tilt, head height) and, without `--trace`, how much your hands moved and how often a fingertip touched your face.

The verdict thresholds are starting values checked on synthetic audio. They have not yet been validated against human judgments; see [docs/evaluation.md](docs/evaluation.md).

## Development

```sh
.venv/bin/python -m pytest -q          # headless: no camera, microphone or window needed
```

Hardware checks are manual: [docs/hardware-smoke-test.md](docs/hardware-smoke-test.md). Implementation progress is tracked in [docs/implementation-progress.md](docs/implementation-progress.md).
