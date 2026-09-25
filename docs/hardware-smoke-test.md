# Hardware smoke test (manual)

CI and `pytest` never touch a camera, microphone, window or model inference in the app. Run this on the target Mac after changes to capture, gestures, rendering, recording or live follow, and note the result in `docs/implementation-progress.md`.

Setup: the lock-file environment, the models downloaded, and Camera and Microphone allowed for the terminal.

First run the automatic part, `.venv/bin/python scripts/hardware_check.py`. It covers the camera rate, the microphone and its device clock, a loopback click, the live model's load time and `say`, and needs no one at the Mac. The steps below are the ones that need you.

1. **Start.** Run `.venv/bin/python main.py samples/sample_notes.md`. The window shows the mirrored camera at ~30 fps (stats line, bottom right) with the notes on the left.
2. **Prepare gestures.** Browse with one finger, two fingers and a flat hand, and focus. Open palm on a focused word, pick **hear it**, then pinch and lift: `say` speaks the sentence. A tone commit says "not available yet".
3. **Take.** Raise a fist and hold 1 s; the count-in runs and REC starts. Read the first section aloud: the orange sentence follows you, and the next section appears faint during the last sentence and takes over once you say its opening. Flick in the zone; the section moves on. Hold an open palm to stop.
4. **Review.** "TRANSCRIBING" appears, then the counts. Chips show ✓ ✗ ? –. Focus a long sentence: its panel pages through every verdict line.
5. **Keys.** Press `t`, speak, `n`, `b`, `x`. A take is recorded with no hand in view; in `session.json`, its `sections` show `start`, `key`, `key`.
6. **Recovery.** Start a take, speak ~5 s, and press Ctrl-C in the terminal. The take is kept as `interrupted`. Then start a take and kill the process (`kill -9 <pid>`). On the next start, a `recovered:` line appears for it.
7. **Clock.** Clap once at the start of a take. In `take-NN.transcript.json` and the gesture log, the clap should line up with `t_start` plus its time in the WAV to within ~50 ms. Note the offset you measure.
8. **Close.** Quit with `q`; the terminal says what analysis, if any, was left for later.

Record: date, macOS version, which steps passed, the frame rate during a take, and anything odd.
