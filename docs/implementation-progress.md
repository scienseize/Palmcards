# PalmCards implementation progress

Execution ledger for the code-review handoff
(`~/Documents/ChatGPT/Astra_New/palmcards-review/CLAUDE_CODE_IMPLEMENTATION_PLAN.md`, prepared 2026-09-25;
supporting assessment `assessment.md` in the same folder). Phases are implemented in order. Each phase is
committed on `main` once its tests pass, following the repository's workflow ("finish, run, commit, then
move on"). Hardware checks (camera, microphone, real models, real speakers) are listed as pending until the
user runs them. They are never inferred from unit tests.

## Summary

| Phase | Status | Changes applied | Validation evidence | Remaining issues / next action |
| --- | --- | --- | --- | --- |
| 0 — Current baseline | Verified | Ledger created; no source changes | 200 passed; all 8 findings reproduced on HEAD `c5a5dd2` (see log) | — |
| 1 — Sessions and notes | Implemented, validation pending | Schema 2 + migration, notes snapshots/revisions with stable ids, exclusive session folders, flock writer lock, atomic WAVs, orphan report | 216 passed; F3/F7 regression tests; CLI rebind run on a temp copy of a real session | App run on hardware (first take writes the new layout); orphan *salvage* is Phase 2 |
| 2 — Recording lifecycle | Implemented, validation pending | Takes streamed to disk (bounded queue, writer thread, fsynced valid WAV + manifest), saved/interrupted/failed states, startup salvage, device-clock first sample, ExitStack-owned resources with guarded cleanup | 235 passed; SIGKILL mid-recording salvaged; injected camera/draw/init/Ctrl-C/disk-full/queue-overflow failures | Hardware: real mic run, clock sync measurement |
| 3 — Analysis supervision | Implemented, validation pending | Supervisor thread owns jobs; non-blocking submit; persisted job records with generations; reconcile on exit; result identity checks; timeouts; bounded close; drill baseline by take identity; in-app retry (r) | 251 passed; F5/F6 regression tests; real worker answers a silent take | Real Whisper run in the app; long takes vs job_timeout_s |
| 4 — Navigation and controls | Implemented, validation pending | Panel viewport + scroll, Rehearse current sentence nav, long-word splitting, keyboard fallback (ModeMachine.command), persistent alert line, truthful Prepare ops (hear it via TTS; tone/length preview only), verdict symbols + separate counts, label backing | 266 passed; reachability at 640x480/1280x720/1920x1080; synthetic dark/light/busy renders inspected | Real camera scenes; gesture-based panel scrolling not added (keys + auto-paging instead) |
| 5 — Scoring/provenance | Implemented, validation pending | Endings need their last word (and confidence); capture gaps make marks unclear; dropped audio filled with silence to keep the timeline; prosody cache provenance + hop from cache; verdict/transcript provenance; uncalibrated languages not judged by English standards | 280 passed (x3); F4 regression; real-session re-judge diff: only the F4 case changed | Human-labelled agreement (Phase 7) |
| 6 — Live following/state | Implemented, validation pending | Live stream states (starting/ready/failed/closed), LiveFollow (non-blocking tap, feeder, failure containment), app integration (sentence highlight, viewport, next-section preview, voice/flick/key section sources, per-take live stats), typed drill target, benchmark readiness/tracking/frame-time fixes | 292 passed; real-engine smoke on a recorded take (in-order follow, section +1.3 s, median lag 0.74 s) | Live mic + camera run; backward flick gesture undecided |
| 7 — Setup/evaluation | Implemented, validation pending | Lock file, pinned+checksummed models (required vs optional), CI workflow (not run), README, configurable data dir, data CLI (list/export/delete/prune), compiled alignment fill (identical results), evaluation protocol + script, hardware smoke-test doc | 343 passed; alignment 1200 words 67.7 s -> 0.12 s; pinned-model transcript identical | Collect consented labelled takes; run CI after push (needs your go-ahead) |
| 8 — Product completion | Implemented, validation pending | Six slices: reopen + playback; export; stress edits + undo; optional LLM (off by default); tutorial, hints, preferences; take metrics (speech, hands; gaze/posture not measured) | 381 passed | Hardware checks; decisions: cloud LLM provider, left-handed layout, backward flick; gaze/posture need models + calibration |
| End-to-end release gate | Passed on hardware (2026-09-26), 4 items covered by automated tests only | scripts/hardware_check.py; the user's 10-step smoke test | 2026-09-26: camera 29.9 fps, loop 30.2 fps, hands 12.8 ms, draw 3.2 ms; mic 48 kHz with device clock (adc), no overflows; click round trip 113.6 ms; live model ready 1.6 s; say ok ; smoke test steps 1-10 all passed, frame rate stable throughout (user) | Live checks of worker retry, truncated endings, editing the imported file, long sessions |
| M7 stage 0 — Face/pose budget | Measured on hardware (2026-09-26) | CLAUDE.md milestone 7 rewritten to what's left, duplicate llm.py line removed; `BODY` config; palmcards/vision.py (face and pose trackers, not in the app yet); scripts/bench_vision.py | 388 passed; five benchmark runs by the user (sessions/bench-vision*.json) | Cool: every rate keeps 30 fps. Warm (after 2.5-3.5 min): every rate costs frames, and hands-only also slows (see log). Rates set to face every 6th, pose every 15th frame; Stage 1 decides on backing off when frames run late |
| M7 stage 1 — Capture and calibration | Checked on hardware (2026-09-26) | Face, pose and hand features during every take (take-NN.face.npz with provenance); the eye calibration in the session's first count-in (dot, then the orange line; `e` redoes it), kept in session.json (schema 3); face/pose wait for a later frame when one is late | 413 passed; the user's session 20260926-115417: calibration ok, `e` redid it, a covered camera failed it with its reason; takes at 5/s face, 2/s pose, 30/s hands, face found 100% | The two calibration targets barely differ (see log); a face-box overlap is not a face touch in a close-up frame; stage 2 (gaze) |
| M7 stage 2 — Gaze | Screen vs away validated on held-out checks (one person); camera vs notes not | palmcards/gaze.py (classes against the calibration, the take's gaze metric while speaking, per sentence), the gaze check (`main.py --gaze-check`, `scripts/evaluate.py --gaze [--sweep]`) | 424 passed; on the user's last real take the defaults gave 70% away while speaking: not believable | A gaze-check take to tune `GAZE`, a second to confirm |
| M7 stage 3 — Posture and movement | Checked on one scripted take (touches 3/3, head drop and tilt found) | Posture against the calibration; hand movement and face touches from the take's features, no --trace; features v2 (fingertip to the face outline, hand size against the face) | 433 passed; earlier real takes: posture near the calibration, wrist 0.5-3.1 palms/s | A take with counted face touches and hands in front of the face |
| M7 stage 4 — Review and export | Implemented, validation pending | Gaze line per focused sentence, take summary card in Review, per-take CSV (`scripts/evaluate.py --table`) | 438 passed; rendered offline from the user's scripted take; the table over all 30 takes | The user sees it in the app |
| M8 stage 1 — Cloud LLM | Checked on hardware by the user (2026-09-26) | Anthropic provider (`claude-haiku-4-5`, `main.py --llm anthropic`, key from the environment or .env), JSON-schema answers, 10 s timeout + one bounded retry, CLOUD LLM chip, truthful tone/length labels, per-call token log (`llm-usage.jsonl`) and `python -m palmcards.llm usage` | 454 passed; the provider tested through the real SDK against a stand-in server on 127.0.0.1; no test sees a key or can reach the API | — |
| M8 stage 2 — Open palm for marks | Checked on hardware by the user (2026-09-26) | An open palm (or `m`) on a focused sentence asks once per sentence and revision for marks, shown faded yellow in place (`edit.new_marks` keeps only what adds); pinch + lift adds all as one revision; a failed request is not re-sent until a new focus | 461 passed; gesture replay samples unchanged; panel rendered offline | — |
| M8 stage 3 — Per-mark toggling | Checked on hardware by the user (2026-09-26) | On spread marks the L-hand is a knob through them (15°/step, relative, reading order, clamped), the current one outlined; a pinch without a lift accepts/rejects it (solid yellow); pinch + lift adds only the accepted ones as one revision; dropping the hand discards the choices | 468 passed; gesture replay samples unchanged; panel rendered offline | The L-to-pinch nudge confirmed by the user, on every L-hand control: fixed below |
| M8 — L-hand dials held through a pinch | Implemented, validation pending | When the thumb of a hand working a dial (ring, marks, tone, stretch, take dial) closes toward the index tip or a pinch registers, the dial goes back to its value from just before the thumb started closing and holds, drawn bolder; marks knob and take dial clamped in the grammar | 477 passed; on the 7 recorded L-to-pinch moments (simulated): knob off 4/7 -> 1/7, dial angle 41 -> 2 deg (median) | Measured on the user's recording: see the next row |
| M8 — Knob dwell, rewind keep, rewrite length | Implemented, validation pending | Knob steps wait 0.15 s (wobble); a knob step on screen 0.25 s survives the rewind; tone/length rewrites get a word count in the prompt and room above it in the check | 480 passed; the user's recording 20260926-161541 replayed through the code: pinches off what was shown 9/19 -> 1/19, knob changes 158 -> 49 | The user: the ring stretch was pinch-only testing; then the ring knob and word pinch problems below |
| M8 — Word options by tilt; word pinch | Tilt reverted by the user's choice; word pinch kept | The word's options in a row over the word, left to right; tilt the L-hand right for the next, left for the one before (one per tilt); marks/take knobs walk one step per frame; pointing + pinch focuses the word pointed at before the curl | 484 passed; on the recordings: word pinches on another word 9/15 -> 2/15; ring 32 steps/min against 158 | The user: tilting doesn't feel like a knob |
| M8 — Ring knob back, 10 degrees an option | Implemented, validation pending | The round ring and its knob back (wrapping), OPS.ring_step_deg 10 (60 degrees of wrist for six options), dwell and one-step walk kept; the word-pinch fix kept | 485 passed; the recording: 0/19 pinches off what was shown when the thumb started closing (2/19 against the longest shown) | The user: none of the L-hand choosers feel like they work |
| M8 — Choosing by pointing | Implemented, validation pending | Options ring, spread marks and Review's takes: an L starts choosing, then the index fingertip moves a point and the nearest item is picked (a margin against flicker); a pinch acts on the item pointed at before the curl; Review's takes as chips beside the sentence, with a message when there is nothing to choose | 478 passed; rendered offline | The user: Review fine; ring and marks too slow |
| M8 — Pointer speed | Implemented, validation pending | Ring and marks: 2 screen px per px of fingertip, both ways (was 1.15 across, 0.65 up/down); a yellow dot shows the point while choosing; Review's take chips unchanged | 481 passed | The user tries it |

## Log

```text
Date: 2026-09-25
Phase / issue IDs: Phase 0 (baseline); findings F1-F8 from assessment.md
Status: verified
Current HEAD / optional commit ID: c5a5dd2 (working tree clean before this ledger)
Pre-existing changes preserved: The plan's baseline was d6baf3d with uncommitted
  config.py, follow.py, bench_live.py, test_follow.py. That work was since committed
  by the user's session as 678bfd1 (stage 1: follower + benchmark) and c5a5dd2
  (Apple recogniser engine, live defaults: Whisper base every 0.3 s in a process).
  Nothing deleted or reset.
Files and behavior changed: docs/implementation-progress.md (this ledger) only.
Migration / compatibility implications: none.
Tests run and exact outcome:
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
  -> 200 passed in 4.10s (Python 3.12.14, numpy 1.26.4, mediapipe 0.10.21, librosa 0.11.0).
  No environment/setup failures in this run (the review's Numba cache failure did not recur).
Reproductions (synthetic data in a temporary directory; no real session touched):
  F1 active recording lost on exceptions: confirmed by inspection. main.py's frame loop
     (inside `with camera:`) has no try/finally; the save-on-quit path, takes.close() and
     tracker.close() run only after a normal loop exit.
  F2 long section clipped: 20-sentence section at 1280x720 needs 1926 px, panel capped at 648 px.
  F3 notes not snapshotted: after editing the notes file, Session.load + load_notes returns the
     edited text ("Goodbye here enemy.").
  F4 ending judged on a partial sentence: "We need to act before the opportunity disappears. [rise]"
     with only "we need to act" said and rising pitch -> alignment "partial", [rise] verdict "hit".
  F5 blocking submit: Transcriber.submit() of a 69 KB job to a worker that waits 1 s before
     reading took 1.02 s.
  F6 orphaned job: worker 1 exits with take 1 unanswered; submit() starts worker 2 before poll()
     sees the exit; after take 2's result, take 1 stays in `pending` forever.
  F7 session collision: two Session.create() calls with the same stem and second share a folder;
     the second take-01.wav (0.8) overwrites the first (0.2).
  F8 false live readiness: a live reader whose model raises RuntimeError reports ready=True, errors=[].
Manual / hardware checks performed: none in this phase.
Evidence or artifact paths: reproduction commands in this session; results above.
Unverified assumptions and remaining risks: none new.
Reason for any departure from this plan: none.
Next action: Phase 1.
```

```text
Date: 2026-09-25
Phase / issue IDs: Phase 1 (F3 notes not snapshotted, F7 session collision)
Status: implemented, validation pending (hardware)
Current HEAD / optional commit ID: a6dcc5b -> Phase 1 commit (see git log)
Pre-existing changes preserved: yes; working tree was clean. Real sessions under sessions/ were
  only read (checksums of every session.json identical before/after the load check).
Files and behavior changed:
  palmcards/revisions.py (new): normalised notes snapshot (sections, sentences, words, marks,
    parser version), stable ids s<n> / s<n>.w<k> / s<n>.m<k>, content hash (ignores ids),
    revision id (includes ids), carry_ids: unchanged sentences keep ids across revisions even
    when moved; edited ones get new ids with ancestry to the most similar old sentence.
  palmcards/session.py: schema 2 (schema, id, source, revisions, take.revision); schema 1 loads
    unchanged in memory, first save keeps session.v1.json; newer schema, missing/invalid
    session.json and unknown take fields raise SessionError. Folder names get a random suffix
    and are created exclusively (a new session never adopts an existing folder). The imported
    file's bytes go to source/, the parsed notes to notes/<rev>.json, takes bind to the current
    revision; notes_for(take) rebuilds them from the snapshot (hash-checked), never the file.
    LegacyNotes for schema-1 takes; rebind_legacy() binds the current file as
    "legacy-unverified" (explicit, repeatable, no duplicate revision). Writer lock: flock on
    session.lock (system releases it when the owner exits or crashes; SessionBusy names the
    owner pid). WAVs and all JSON published via *.tmp + rename; stray take WAVs and *.tmp are
    reported as `orphans` on load and take numbering skips any take file on disk.
    take(n) looks up by number, not list position. notes paths are stored absolute.
  palmcards/notes.py: PARSER_VERSION; notes_from_bytes (the app parses the exact bytes it saves);
    .docx parsed from bytes.
  main.py: reads the notes file once as bytes; Session.create(..., parsed=, source=); releases
    the lock on close.
  palmcards/speech.py CLI: analyses each take against its own revision; --rebind; report-only
    on a legacy take falls back to the current file with a warning, but writing results for a
    legacy take requires --rebind; SessionError/SessionBusy -> message, exit 1.
  palmcards/player.py, scripts/bench_live.py (read-only): take's revision, else the current file
    with a warning.
  CLAUDE.md: session layout, schema 2 fields, legacy/rebind behaviour.
Migration / compatibility implications: new sessions use schema 2. Existing (schema 1) sessions
  read as before; nothing is rewritten until the user runs a writing command, which requires
  --rebind for takes without notes and keeps session.v1.json. Tools that read sessions must use
  session.notes_for(take), not load_notes(session.notes).
Tests run and exact outcome: pytest (full) -> 216 passed in 3.66s. New tests in
  tests/test_session.py: same-count edit / delete / move of the imported file leave
  notes_for(take) unchanged; load from another cwd; two sessions in the same second (even with
  a forced name clash) keep distinct audio (0.2 and 0.8) and ids; second writer refused until
  release; stray take-02.wav + session.json.tmp reported, next take is take-03; damaged
  revision refused by hash; schema-1 load, rebind (unverified, v1 backup, repeatable), rebind
  without the file, schema 99 refused, no session.json refused; snapshot round trip, id carry
  over with ancestry, hash ignores ids.
Manual / hardware checks performed: none (no camera/mic run). Offline end-to-end on a temporary
  copy of sessions/20260925-131002-sample_notes: report-only exit 0 with warning; --realign
  without --rebind exit 1 with instructions; with --rebind exit 0, schema 2, 3 takes bound to
  one legacy-unverified revision, session.v1.json kept, re-alignment identical to the original.
Evidence or artifact paths: tests/test_session.py; commands in this session.
Unverified assumptions and remaining risks: flock semantics verified on macOS only (the only
  supported platform). A crash between writing a take WAV and session.json leaves an orphan
  that is reported but not yet salvaged into a take (Phase 2). The app's first-take write path
  is exercised by unit tests, not yet by a live run.
Reason for any departure from this plan: none. Session folders are still created at the first
  take (existing behaviour), exclusively.
Next action: Phase 2.
```

```text
Date: 2026-09-25
Phase / issue IDs: Phase 2 (F1 exceptions discard the active recording; clock accuracy; overflows)
Status: implemented, validation pending (hardware)
Current HEAD / optional commit ID: cbeef80 -> Phase 2 commit (see git log)
Pre-existing changes preserved: yes (clean tree at start; real sessions not touched).
Files and behavior changed:
  palmcards/recording.py (new): TakeWriter. The audio callback pushes blocks into a bounded queue
    (RECORDING.queue_s = 2 s) with put_nowait: never blocks, never touches the disk; a full queue
    drops the block and records a queue_full gap at its place in the file; PortAudio input
    overflows are recorded as gaps too. A writer thread appends 16-bit PCM to take-NN.wav.part,
    rewrites the WAV header and fsyncs every RECORDING.flush_s (1 s), and rewrites the manifest
    take-NN.recording.json atomically. A write error (disk full) keeps what was written, marks the
    take failed and records the rest as a write_error gap. stop() never blocks: the writer drains,
    fixes the header, renames .part -> .wav in the background. first_sample_time(): the first
    sample's app time from PortAudio's ADC time, else callback arrival minus block length.
    repair_wav() rebuilds a stale header from the file size.
  palmcards/capture.py: AudioRecorder streams to a TakeWriter (fixed 1024-frame blocks, app clock
    injected); no audio kept in memory; overflow counted per take.
  palmcards/session.py: TakeRecord.status (saved | interrupted | failed) and capture (clock,
    dropped_samples, discontinuities with at_s/samples/why, error, recovered). begin_take()
    writes folder, notes and session.json before recording so a crash is recoverable;
    finish_take(manifest) adds the take (t_start = first sample, section times relative to it) and
    removes the manifest; recover() salvages leftover .part/.wav as interrupted takes (headerless
    parts kept as *.recording.json.empty evidence; already-added takes just lose the manifest);
    recover_all(root) at startup skips sessions another running PalmCards holds. Take numbering
    also skips .wav.part and manifests.
  main.py: Devices (camera, tracker, recorder, log, window functions; tests pass fakes); run()
    registers each resource in one ExitStack as soon as it is opened, cleanup steps are guarded
    (a failing step is reported and the rest still run; the original exception is re-raised);
    any exception or Ctrl-C marks the active take interrupted. Takes: begin_take + TakeWriter at
    take_start, sections marked on the writer in app time, stop hands the writer to background
    finalization, poll() adds finished takes and submits analysis only for saved takes;
    recording errors and interrupted/failed takes show a persistent status line; close() is
    idempotent and waits at most RECORDING.finalize_timeout_s per take (a take still writing is
    left for recovery). main() runs recover_all() at startup.
  palmcards/speech.py CLI: skips interrupted/failed takes unless --incomplete.
  palmcards/config.py: RECORDING (block_frames, queue_s, flush_s, finalize_timeout_s).
  CLAUDE.md: recording lifecycle, take status/capture fields, part/manifest files.
Migration / compatibility implications: schema unchanged (2); takes without status read as
  "saved". The session folder now appears when a take starts (it used to appear when the first
  take was saved). Session.add_take (in-memory audio) remains for tests and tools.
Tests run and exact outcome: pytest (full) -> 235 passed in 12.09s. New:
  tests/test_recording.py (11): stream-to-WAV exactness, section/t_start mapping, device-clock
    and fallback stamping, callback hand-over with overflow, queue overflow (worst push < 5 ms,
    written + dropped == pushed, gap recorded), disk full mid-take (5 blocks kept, failed,
    write_error gap), open failure, header repair, SIGKILLed subprocess salvaged by
    recover_all (>= 3 s - flush_s kept, interrupted, recovered flag, idempotent), recovery skips
    a session still open, stale manifest cleanup, no-audio take timing.
  tests/test_app_lifecycle.py (8, main.run with fake devices): camera failure, drawing error,
    Ctrl-C mid-take -> take interrupted, not analysed, everything closed once, lock released, no
    part/manifest left; q mid-take -> saved and submitted once; failing cleanup step reported,
    others still run, original CameraError raised; tracker init failure -> camera released, no
    session folder; take_stop twice -> one take, one job; cancelled count-in -> nothing written.
Manual / hardware checks performed: none. Real microphone callback timing, ADC-time availability
  on the MacBook Air mic, and audio/camera synchronisation error are not measured.
Evidence or artifact paths: tests above.
Unverified assumptions and remaining risks: PortAudio on macOS is assumed to report
  inputBufferAdcTime (fallback path covered by tests). Maximum loss if the app is killed:
  the queue (<= 2 s); on power loss up to a further 1 s. The frame loop still draws while a take
  finalizes; the fsync happens on the writer thread.
Reason for any departure from this plan: the plan suggests a separate controller object; resource
  ownership was centralised in run() + Takes with an ExitStack instead, a smaller change
  (the fuller state controller is Phase 6).
Next action: Phase 3.
```

```text
Date: 2026-09-25
Phase / issue IDs: Phase 3 (F5 blocking submit, F6 orphaned pending jobs)
Status: implemented, validation pending (real Whisper run in the app)
Current HEAD / optional commit ID: c9fd649 -> Phase 3 commit (see git log)
Pre-existing changes preserved: yes (clean tree at start).
Files and behavior changed:
  palmcards/analysis.py (new): Supervisor. submit() only checks admission (ANALYSIS.max_queue,
    refuses at once when full) and puts a command on a queue. One supervisor thread owns all job
    state: writes jobs/<id>.input.json once, keeps jobs/<id>.json (take, revision, config hash,
    state queued/running/succeeded/failed, attempts, generation, timestamps, error), starts the
    worker, and sends it {"id","input"} (a short line; one job in flight at a time). A reader
    thread per worker generation forwards lines and its exit. On exit, every job still running on
    that generation is retried (max_attempts) or failed, whatever worker is current. Results are
    accepted only for the running job with the same take, revision and config; stale, duplicate,
    malformed replies are logged and dropped. job_timeout_s kills a hung worker. Broken pipe on
    handover kills the worker so its exit reconciles the job. Spawn failure retries then fails.
    Supervisor-side failures reach the app as failed results. close(timeout): lets the running
    job finish until the deadline, closes stdin, terminate, kill, reaps; unfinished jobs saved as
    queued; unfinished_jobs() finds them later. retry_failed() requeues failed jobs. Drill jobs
    wait for their baseline take's job (or its verdicts, at most baseline_wait_s).
  palmcards/speech.py: worker protocol reads the input file named in each line and echoes
    id/take/revision/config; make_job adds revision, config (analysis_config()), drill and
    baseline = the latest *saved full* take before the drill (by number); baseline_wpm reads only
    that take's verdicts; Transcriber removed. CLI reports and resolves jobs the app left
    unfinished.
  palmcards/cues.py: a drill with no baseline pace judges pace "unclear: no earlier full take to
    compare with" (baseline "none"), never against itself.
  main.py: Takes uses Supervisor; a full queue defers the take and resubmits each frame; failed
    analysis shows on the status line until retried with the new r key; supervisor log lines go
    to stderr; close() waits at most ANALYSIS.shutdown_s (Ctrl-C leaves it), then close(1 s) and
    reports deferred takes.
  palmcards/config.py: ANALYSIS (max_queue, job_timeout_s 900, max_attempts 2, shutdown_s 20,
    baseline_wait_s 30).
Migration / compatibility implications: verdicts of drills without a baseline now say
  baseline "none" (was "take"). jobs/ appears in session folders. Offline CLI unchanged in use.
Tests run and exact outcome: pytest (full) -> 251 passed in 16.78s; analysis, recording and
  lifecycle tests run 3 times: 33 passed each time. New tests/test_analysis.py (14): 69 KB
  submission to a worker that waits 1 s before reading returns in < 50 ms; worker dying before
  reading / mid-job / after writing an artifact / after garbage output -> retried on generation 2,
  each take answered exactly once; old worker exit with max_attempts=1 -> job 1 failed "exited",
  job 2 succeeded, nothing pending (F6); wrong-take reply dropped then timed out; duplicate reply
  dropped; hung worker killed after 0.5 s; full queue refuses in < 10 ms; close(0.5) returns in
  < 4 s, workers reaped, both jobs on disk queued; worker that never reads -> failed after 2
  attempts, no hang; drill submitted before its baseline runs after it; retry_failed reruns;
  real `palmcards.speech --serve` answers a silent take with matching id/revision.
  tests/test_speech.py: baseline by identity (latest full take, not an older one with verdicts;
  interrupted takes excluded); drill without baseline -> pace unclear.
Manual / hardware checks performed: none. Whisper inference in the supervised worker is only
  exercised by the offline CLI so far, not by an app run.
Evidence or artifact paths: tests above.
Unverified assumptions and remaining risks: job_timeout_s (15 min) must exceed Whisper time for
  the longest take; a first-run model download counts against it. Deferred jobs are resumed by the
  CLI, not yet by reopening a session in the app (Phase 8 reopen).
Reason for any departure from this plan: none.
Next action: Phase 4.
```

```text
Date: 2026-09-25
Phase / issue IDs: Phase 4 (F2 clipped long sections; stub controls; verdict colour-only; fallback controls)
Status: implemented, validation pending (real camera scenes, hands-on use)
Current HEAD / optional commit ID: a506859 -> Phase 4 commit (see git log)
Pre-existing changes preserved: yes (clean tree at start).
Files and behavior changed:
  palmcards/render.py: the focus panel is laid out whole (Panel: height, image, each sentence's
    rows) and drawn through a viewport (max_panel_h: below the label's reserved height, above the
    alert line, at most TEXT.panel_max_h); ViewState.panel_scroll (px) with clamp, panel_scroll_to
    (keeps a sentence whole in view), scrollbar and "▲ MORE"/"▼ MORE" markers. Text size never goes
    below normal to fit. Rehearse: the current sentence orange, the rest of the section dimmed.
    layout() splits units wider than a row into row-wide pieces that keep word/mark identity
    (hit-testing finds the word from any piece). Verdict chips carry a badge symbol (✓ ✗ ? –,
    only on a stress mark's closing *), detail lines start with the symbol. Persistent alert chip
    under the text; keys help (h) and an "H: KEYS" hint. The label chips get a translucent backing.
    Ring nodes: original word + "hear it" only; tone/stretch labels say "PREVIEW ONLY, NOT
    AVAILABLE YET"; focus hints updated.
  palmcards/gestures.py: ModeMachine.command(name, t) -> the gesture transitions for start, stop
    (or cancel count-in), next section, back to prepare; logs `key` events with acted flag.
  main.py: keys t/x/n/p queue ModeMachine commands handled with the next frame's events;
    j/k/space move the current sentence within the section in Rehearse (panel follows), scroll a
    focused panel (pausing auto-paging for TEXT.page_pause_s), or browse as before; h toggles key
    help. Overflowing focused panels in Prepare/Review page every TEXT.page_s. view.alert comes
    from Takes.alert_line() (recording trouble, failed analysis with the r hint), separate from the
    status line. Count-in and next section set the current sentence and reset the panel scroll.
    Commits: "hear it" speaks the sentence with the word (and its stress marks) emphasised through
    Devices.speaker (tts.get_speaker) and logs `hear`; tone/length log commit_stub and say "NOT
    AVAILABLE YET: NOTHING CHANGED"; keeping the word says "NO CHANGE". Nothing says COMMITTED.
  palmcards/tts.py: Speaker.say_words(words, stressed); MacSay uses `say` [[emph +]] markup.
  palmcards/review.py, palmcards/cues.py: summaries give separate counts ("4 HIT, 3 MISSED,
    2 UNCLEAR"; terminal "1 hit, 1 unclear, 1 skipped (3 marks)").
  palmcards/style.py: alert/label fills, scrollbar colours, symbol scale, page timing.
  CLAUDE.md: keyboard fallback, what the screen promises, new log kinds.
Migration / compatibility implications: none for data. Terminal/label summary wording changed.
Tests run and exact outcome: pytest (full) -> 266 passed in 19.77s. New/changed:
  tests/test_render.py: 20-sentence section reachable sentence by sentence at 640x480, 1280x720,
    1920x1080 (viewport within frame, below label; last sentence at max scroll); rehearse current
    colouring; 30 wrapped verdict lines reach the last (clamped max scroll); 68-letter word split
    into >= 3 pieces within 20 columns and hit from its last piece; symbol drawn in detail lines;
    alert and keys help drawn; ring offers only word + hear it; tone label says preview only.
  tests/test_gestures.py: key commands (start/cancel/start/next/stop/prepare, refused when the mode
    doesn't take them, none in a drill), `key` log entries.
  tests/test_controls.py: hear it speaks with the word and existing stress stressed; keep = no
    change; tone/stretch commits never claim a change; the keyboard alone (t, n, x, q) records a
    saved take with two sections through the real ModeMachine while tracking returns nothing.
  tests/test_review.py, tests/test_cues.py, tests/test_tts.py: summaries, say markup.
Manual / hardware checks performed: synthetic renders (not camera) at 1280x720 and 640x480 on
  dark, light and busy backgrounds: sessions/renders/phase4/*.png (24 + 2). Inspected rehearse
  (busy), focused Review with 14 verdict lines (light), Review chips (busy, light): after the fixes
  the label and badges are legible on all three. No live camera check.
Evidence or artifact paths: sessions/renders/phase4/ (gitignored); tests above.
Unverified assumptions and remaining risks: gestures still cannot scroll a focused panel
  (auto-paging + keys cover reachability; a gesture would change the grammar and its fixtures).
  Contrast over real camera scenes, and readability at arm's length, need a hands-on check.
Reason for any departure from this plan: none of substance; reachability without keys comes from
  auto-paging rather than a new gesture, to keep the gesture grammar and its regression fixtures.
Next action: Phase 5.
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 5 (F4 ending judged on a partial sentence; cache provenance; timeline gaps; language)
Status: implemented, validation pending (agreement with human judgments not measured)
Current HEAD / optional commit ID: 7f8ff0c -> Phase 5 commit (see git log)
Pre-existing changes preserved: yes (clean tree at start; real sessions only read; re-judging ran on
  temporary copies).
Files and behavior changed:
  palmcards/cues.py (VERSION 2): [rise]/[fall] need the sentence's last note word aligned (a joined or
    approximate match counts) and heard at CUES.tail_min_probability (0.3) or better, else unclear.
    Marks already had their own evidence rules (pause: both neighbours; pace: 4+ words; stress: its
    word); now each also checks `gaps` (app-clock intervals of lost audio) over what it measures and
    is unclear if any audio was lost there. For languages not calibrated, stress/ending hit/missed
    become unclear with the measurement kept in the reason; pause/pace judged as usual. Verdicts
    record version and language {code, calibrated}.
  palmcards/recording.py: dropped queue blocks are written as silence at their stream position (and
    at the end on finish), so file time = take time; gaps merge only when contiguous; write-error
    gaps use stream positions.
  palmcards/prosody.py: provenance(wav, rate, t_start) (WAV sha256, time origin, extractor and
    librosa versions, fmin/fmax/frame_length/hop_s/frame_hop_s/rms_frame_s) saved in the cache;
    load() returns it; Prosody.hop comes from the cache (frame_hop_s) or the frame times, never from
    today's CUES.hop_s.
  palmcards/speech.py: take_prosody() reuses a cache only if its provenance matches ("verified"),
    else measures again; with --realign (reuse_stale) it reuses and reports "stale" (with the
    changed keys) or "unknown" (old cache). make_job adds gaps from the take's capture record
    (unknown-length overflows count as 0.05 s). run_job: fillers only for scoring languages,
    verdicts carry provenance {notes_revision, analysis_config, asr, align {version, settings,
    fillers}, scoring {version}, prosody, gaps}; transcript carries asr {backend, model, revision}.
  palmcards/asr.py: model_revision() (local HF snapshot commit, never downloads); Transcription.revision.
  palmcards/align.py: VERSION; align(..., fillers) for the take's language.
  palmcards/config.py: CUES.tail_min_probability, SPEECH.scoring_languages ("en",).
  CLAUDE.md: delivery-mark evidence rules, languages, cache and verdict provenance.
Migration / compatibility implications: old prosody caches are "unknown": a full analysis measures
  again; --realign keeps them and says so. Verdict files gain version/language/provenance. Takes
  recorded before this change keep their (shorter) WAVs if the queue ever dropped audio; new takes
  keep the timeline.
Tests run and exact outcome: pytest (full) -> 280 passed in 22.4-22.6 s, three consecutive runs.
  New tests/test_evidence.py (14): the review's reproduction ("we need to act" with rising pitch
  against "...before the opportunity disappears. [rise]") -> unclear, not hit; same for [fall];
  middle skipped but ending said -> judged (hit); misheard last word -> judged; last word split in
  two -> judged; last word at confidence 0.2 -> unclear; lost audio in a pause / stressed word /
  ending -> unclear, elsewhere -> no effect; capture gaps to app clock; uncalibrated language ->
  stress/ending unclear with measurement, pauses still judged; cache reused for same settings,
  reused after threshold-only changes (pyin call count unchanged), re-measured after an extraction
  change or a different WAV; --realign with a changed hop uses the cache's 0.01 s hop and reports
  stale; old cache -> unknown, hop read off frames; verdicts and transcript carry provenance.
  tests/test_recording.py: gap-filled timeline (each dropped stretch silent, the rest in place,
  duration = stream length).
Manual / hardware checks performed: offline re-judge (--realign --rebind) of temporary copies of
  sessions 20260925-131002 (3 takes) and 20260925-101345 (2 takes): all counts identical except
  131002 take 3 (a drill of the last sentence), whose [fall] went from missed to unclear "the end of
  the sentence wasn't heard" -- the F4 case on real data. Old caches reported "unknown".
Evidence or artifact paths: tests above; command in this session (temporary copies, deleted).
Unverified assumptions and remaining risks: tail_min_probability (0.3) is a starting value, not
  tuned against human judgments. Stress/intonation calibration beyond English is not attempted.
Reason for any departure from this plan: filling dropped audio with silence (a recording change)
  was added because gaps otherwise shifted every later timestamp, which the gap rules depend on.
Next action: Phase 6.
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 6 (F8 false live readiness; live-follow integration; drill state)
Status: implemented, validation pending (live microphone + camera run by the user)
Current HEAD / optional commit ID: 32a778b -> Phase 6 commit (see git log)
Pre-existing changes preserved: yes (clean tree at start).
Files and behavior changed:
  palmcards/asr.py: LiveStream.state starting/ready/failed/closed (+ ready property, errors,
    reset()). MlxWhisperLive: a warm-up error fails the stream (was ready=True, errors=[]); fails if
    the model hasn't loaded within SPEECH.live_start_timeout_s (60 s) or the reader thread/process
    died; reset() clears window, agreement and in-flight windows (epoch-tagged) but keeps the
    model; close() idempotent, joins/terminates the reader. AppleLive: state, reset().
  palmcards/follow.py: LiveFollow. tap(block, t_end) appends to a bounded deque (FOLLOW.tap_blocks)
    from the audio callback, never blocks; a feeder thread resamples mic-rate chunks to 16 kHz every
    FOLLOW.feed_s and feeds the stream; poll() -> Follower events. Any exception or a failed stream
    turns the follow "failed" (error kept) and it returns no more events; recording and manual
    control are untouched. prepare() at the count-in loads the model and warms the scipy import off
    the frame loop. flick()/jump() are the manual overrides; stop_take() -> per-take stats.
  palmcards/capture.py: AudioRecorder.tap gets each recorded block with its end on the app clock.
  palmcards/recording.py, session.py: sections carry a source (start/voice/flick/key); TakeRecord.live.
  palmcards/gestures.py: GestureEvent.source ("key") and .sentence (a drill's target);
    ModeMachine.command("previous").
  palmcards/render.py: ViewState.preview_next: the next section in the rehearse panel, faint.
  main.py: Takes owns LiveFollow (one stream per app run, made at the first count-in via
    Devices.live); take_start taps the recorder and starts the follow (not for drills); each
    rehearse frame poll_follow() moves view.current, advances the recorded section only on a voice
    `section` event for the next section (source voice), sets preview_next while the section's
    last sentence is current, and scrolls the panel; flick/n record source flick/key and tell the
    follow; b (previous section) and j/k jump the follow; take_stop saves the follow's stats in the
    take's manifest (-> TakeRecord.live); a failed follow shows on the alert line. The drill target
    is set on the drill event from the focus at the moment the events arrive, before any handler
    clears the focus (was read from a mutable field set by the commit handler). --no-follow.
  scripts/bench_live.py: wait_ready raises (and main exits 1) when the stream fails, the reader dies,
    or no answer arrives (backstop timeout); sentence tracking only over spoken sentence spans
    (silence between sentences no longer counts as behind); frame-time p50/p90/p99 and peak RSS.
  CLAUDE.md: voice follow behaviour, sections source, live field, log kinds.
Migration / compatibility implications: sections entries gain "source" (older takes lack it);
  takes gain "live". The first count-in now starts the live model (a separate process for
  mlx-whisper) unless --no-follow.
Tests run and exact outcome: pytest (full) -> 292 passed in 26.54 s. New tests/test_live_follow.py
  (12): failing warm-up -> failed, not ready (F8); silent reader -> failed at the deadline; dead
  reader -> failed; reset drops answers to older windows; LiveFollow turns words into
  sentence/section moves, resamples 48k->16k, keeps stats; a failing stream or factory only turns
  the follow off; in the app (fake devices): the voice advances the section (recorded "voice", take
  live stats saved), flick then b recorded "flick" then "key", a broken live stream leaves the take
  saved, a drill records the sentence current when it was decided and runs without the follow;
  benchmark raises on a stream that never starts; tracking ignores a 17 s silence.
Manual / hardware checks performed: no camera or live microphone. Real-engine smoke without the app
  (sessions/bench/live_follow_smoke.py): LiveFollow + mlx-whisper base in a separate process, fed
  sessions/20260925-101345 take 1 (48 kHz) through tap() in real time in 1024-sample blocks:
  stream ready in 1.4 s; sentences 1, 2, 3 followed in order; section 2 at 34.5 s (speaker began it
  at 33.2 s: +1.3 s); sentences 4-6 followed; 71 words confirmed, median lag 0.74 s, p90 1.44 s,
  0 dropped blocks.
Evidence or artifact paths: tests above; sessions/bench/live_follow_smoke.py (gitignored).
Unverified assumptions and remaining risks: frame rate with the follow running inside the real app
  (the camera benchmark earlier measured the same pipeline at 30.0 fps, but not wired into
  main.py); whether the voice section change feels timely in real rehearsal.
  Open product decision: a backward flick gesture (asked on 2026-09-25, not answered); keys b/j/k
  cover correction for now.
Reason for any departure from this plan: step 1 (one controller for all state) was done at the
  integration boundaries only: the drill target moved onto a typed event, and the voice/manual
  section changes are owned by Takes; a full controller refactor would rewrite main.py's loop
  without changing behaviour, so it is left for when the Phase 8 editing work needs it.
Next action: Phase 7.
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 7 (reproducibility, scaling, evaluation, data controls)
Status: implemented, validation pending (CI not run: no push; no human-labelled data exists)
Current HEAD / optional commit ID: code in cdab0ad; this ledger entry and CLAUDE.md in the
  follow-up commit (a documentation edit failed on a pattern and the code commit went ahead
  without it; fixed forward, history not rewritten).
Pre-existing changes preserved: yes (clean tree at start; real sessions only read).
Files and behavior changed:
  requirements.lock.txt (new): uv pip freeze of the tested venv (Python 3.12.14; numpy 1.26.4,
    mediapipe 0.10.21, mlx-whisper 0.4.3, librosa 0.11.0, ...), no colour codes.
  scripts/download_models.py: MediaPipe from versioned /1/ URLs (verified byte-identical to the local
    files) with SHA-256 checks on existing and downloaded files; only gesture_recognizer.task is
    required today, hand/face/pose landmarkers optional (--all); Whisper at pinned revisions.
  palmcards/config.py + asr.py: SPEECH.model_revision / live_model_revision pinned to the cached
    commits (a4aaeec0.., 1e3e249f..); model_path() loads exactly that local snapshot (clear error
    if missing, never an unpinned download); model_revision() reports the pinned commit.
  palmcards/paths.py (new): data_dir(): $PALMCARDS_DATA, else the checkout's sessions/ if present
    (existing sessions keep being found), else ~/Library/Application Support/PalmCards/sessions.
    session.SESSIONS_DIR, gestures.LOG_DIR and main.SCREENS_DIR use it. No automatic migration.
  palmcards/data.py (new): python -m palmcards.data list | export RUN DEST.zip (never overwrites,
    no lock file) | delete RUN [--yes] (dry run without --yes; takes the gesture log and trace
    with it; refuses a session another PalmCards holds) | prune --older-than DAYS [--yes].
  palmcards/align.py: the DP fill as _fill on arrays, compiled with numba (njit, cache) when
    available, falling back to the same Python code; _dp_python kept as the reference. Results
    identical (tests); traceback and restart handling unchanged.
  scripts/profile_align.py (new): time and peak memory for varied and repeating scripts.
  scripts/evaluate.py (new) + docs/evaluation.md (new): consent/hold-out/labelling protocol, label
    format, metrics (mark agreement, false hits/misses, abstention rate, called-skipped, word start
    error median/p90 and unaligned, gesture false triggers per minute; keys never count).
  docs/hardware-smoke-test.md (new): manual camera/mic/recovery/clock checklist.
  .github/workflows/tests.yml (new): macos-14, uv, lock file, pytest with PALMCARDS_DATA in a temp dir.
  README.md (new): install, permissions, use (gestures + keys), data and deletion, recovery, what
    works vs planned; includes "Tested on macOS with Apple silicon. Windows is not supported yet."
  CLAUDE.md: code layout (recording, revisions, analysis, paths, data, scripts, docs, lock), how to work.
Migration / compatibility implications: none for data. Installs from the lock file reproduce the
  tested environment; a Whisper model not at the pinned revision is refused with instructions.
Tests run and exact outcome: pytest (full) -> 343 passed in 27.01 s. New: tests/test_align_fast.py
  (46: compiled fill is numba; 40 random scripts, 4 realistic takes incl. repeating, joins/splits/
  restarts/mid-start/chatter: identical pairs to the reference), tests/test_data.py (4: list,
  export incl. no-overwrite and no lock file, delete dry-run then --yes with the log, open session
  refused, prune only old and only with --yes), tests/test_evaluate.py (1: agreement 0.5, a false
  hit, abstention 1/3, word error median 0.125 / p90 0.185 with one unaligned, one false trigger in
  a minute, the key-driven mode change not counted).
Manual / hardware checks performed: download_models.py re-run: required files verified, pinned
  Whisper snapshots present. Pinned-path transcription of a temporary copy of 20260925-171226 take
  1: words identical to the original transcript; transcript records the revision. Alignment
  profile (synthetic): 600 words 15.27 s -> 0.04 s, 1200 words 67.74 s -> 0.12 s, 5000 words
  1.57 s and 744 MB peak (the tested limit, memory-bound).
Evidence or artifact paths: docs/evaluation.md (profile table), scripts/profile_align.py.
Unverified assumptions and remaining risks: the CI workflow has never run (pushing publishes the
  repository; not done without your go-ahead). The Application Support default is untested on an
  installed build. Numba's first call compiles (~0.4 s); its cache needs a writable __pycache__.
  No consented, labelled evaluation data exists: verdict accuracy remains unmeasured.
Reason for any departure from this plan: none. Alignment was optimised by compiling the existing
  fill (identical results) rather than coarse anchors/local alignment, which would have changed
  results; memory, not time, is now the limit.
Next action: Phase 8.
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 8 slice 1 (review playback and reopening)
Status: implemented, validation pending (audio output and reopening on the target Mac)
Current HEAD / optional commit ID: 81d7126 -> slice 1 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  main.py: --open RUN (a folder or a name under the data dir) loads the session, takes its lock
    (refuses one open elsewhere, and one without saved notes, pointing to --rebind), and runs the
    app on its current notes revision, starting in Review. Takes.restore() puts every judged take
    on the board (mapped by sentence id) and resubmits saved takes without verdicts (closing their
    stale job records). Review playback: an open palm held PLAY_HOLD_S (0.6 s) on a focused
    sentence, or key a, plays that sentence from the take it shows (Devices.player); `play` log
    entries; notes "NO TAKE TO PLAY" / "SENTENCE NOT SAID" / "COULD NOT PLAY".
  palmcards/playback.py (new): sentence_clip (first to last matched word, padded 0.25 s) and
    ClipPlayer (sounddevice.play, one clip at a time).
  palmcards/revisions.py: index_map(old, new) by sentence id. palmcards/session.py:
    Session.sentence_map(take). palmcards/review.py: Board.add(..., sentence_map) places a take's
    sentences onto the current notes; edited/removed sentences are left out (skipped, no marks).
  palmcards/render.py: key help lists a and b.
  CLAUDE.md: Review playback, --open.
Migration / compatibility implications: none.
Tests run and exact outcome: pytest (full) -> 347 passed. New tests/test_reopen.py (4): mapping of
  an earlier revision's take onto edited notes (edited sentence not claimed, moved one placed);
  clip length and "not said"; reopening in Review, key a plays sentence 1 of take 1 (1.5 s clip),
  the unfinished take 2 is submitted again, log has mode review and play, nothing lost; an old
  session without notes is refused with the --rebind hint.
Manual / hardware checks performed: none (sound output and the open-palm hold untested live).
Unverified assumptions and remaining risks: the open-palm hold in Review focus is new gesture
  meaning (open palm had none there); a hold might be triggered by a relaxed hand. PLAY_HOLD_S may
  need tuning.
Reason for any departure from this plan: none.
Next action: Phase 8 slice 2 (export).
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 8 slice 2 (export)
Status: verified for the pure logic (round-trip tests); no hardware involved
Current HEAD / optional commit ID: 485eba9 -> slice 2 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  palmcards/export.py (new): write(notes, dest, fmt, imported_from) and the CLI
    `python -m palmcards.export RUN [--revision ID] [--format txt|md|docx] [--out PATH]`. Always a
    new file (default <session>/exports/<stem>-<revision>.<fmt>; refuses an existing file and the
    imported original). marked(): a sentence whose raw markup still parses to the same words and
    marks is written as it was; an edited one is rebuilt (pace, pauses, *stress*, ending).
    Sections as headings (txt, md, docx), untitled breaks as two blank lines (txt) or two empty
    paragraphs (docx); in md an untitled later section becomes "# Section N" and is reported; an
    imported .docx's formatting is reported as not kept.
  palmcards/session.py: add_revision() saves session.json when it adds a revision (a new revision
    was not on record until some later save).
  CLAUDE.md: milestone 9 note, code layout (playback.py, export.py).
Migration / compatibility implications: none.
Tests run and exact outcome: pytest (full) -> 354 passed. New tests/test_export.py (7): txt, md,
  docx each re-import to the same sections, sentences, words, marks and paragraphs; unedited markup
  kept ("**Very** forgiving heads. [fall]"), an edited stress rebuilt with its pause kept; untitled
  sections round-trip in txt and are reported in md; never overwrites; the CLI exports a chosen
  earlier revision and the current one (docx formatting reported), and refuses a second export to
  the same name.
Manual / hardware checks performed: none needed beyond tests.
Unverified assumptions and remaining risks: rebuilt sentences normalise spacing around marks.
Reason for any departure from this plan: none.
Next action: Phase 8 slice 3 (stress edits with revisions and undo).
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 8 slice 3 (Prepare edits: stress; revisions; undo)
Status: implemented, validation pending (the ring gesture on the target Mac)
Current HEAD / optional commit ID: d822682 -> slice 3 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  palmcards/edit.py (new): toggle_stress(notes, sentence, word) -> new Notes; the sentence is
    rebuilt as markup (export.marked) and re-parsed, so words, marks (parser order) and raw agree;
    other sentences untouched; the input Notes unchanged. is_stressed().
  palmcards/session.py: `current` revision (persisted; default the latest); edit(notes) makes the
    session folder if needed and adds an "edited" revision whose parent is the current one;
    undo() makes the parent current (nothing deleted; returns None at the import); takes bind to
    the current revision; current_notes(). New sentence ids start past every id the session has
    given out (fixes an id collision found by the tests: a branch after an undo reused an undone
    revision's id for a different sentence).
  palmcards/revisions.py: carry_ids/to_snapshot take next_id.
  palmcards/export.py: an empty raw means "rebuild from words and marks".
  palmcards/render.py: ring = (word, stress|unstress, hear it); labels for each; focus hint; keys
    help lists U.
  main.py: a ring commit on stress/unstress calls Takes.edit_stress (new revision, board rebuilt on
    the new notes by sentence id, follow notes updated, `edit` log entry, "STRESSED ... / U: UNDO");
    key u in Prepare undoes (`undo` log entry, "UNDONE" / "NOTHING TO UNDO"); the frame loop lays
    out the new notes when notes_version changes.
  CLAUDE.md: what Prepare offers, how edits relate to takes, edit.py.
Migration / compatibility implications: session.json gains "current". Older sessions read as before
  (current = latest revision).
Tests run and exact outcome: pytest (full) -> 357 passed. New tests/test_edit.py (3): toggling stress
  on and off rebuilds only that sentence with canonical marks; edit -> folder, imported + edited
  revisions, take bound to the edit; undo to the import, then a take bound to it; a new edit after
  the undo parents from the import; reload keeps current; ids unique across branches; sentence
  maps; the app's edit_stress/undo rebuild notes and board, log them, persist "current".
  tests/test_render.py, tests/test_controls.py: ring order (word, unstress/stress, hear it).
Manual / hardware checks performed: none.
Unverified assumptions and remaining risks: after an edit, the edited sentence's earlier verdicts
  are no longer shown (by design: they were for other words); users may expect them.
Reason for any departure from this plan: none.
Next action: Phase 8 slice 4 (LLM interface).
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 8 slice 4 (optional LLM)
Status: implemented, validation pending (no provider available here: no Ollama; a cloud provider
  was not used, see below)
Current HEAD / optional commit ID: 9f1e8d7 -> slice 4 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  palmcards/llm.py (was a placeholder): Provider protocol; FakeProvider (tests); OllamaProvider
    (http://localhost:11434/api/chat, format json, no account); get_provider() -> None unless
    config LLM.provider is set. Prompts: a fixed system prompt that treats the text in <notes>...
    </notes> as data and never as instructions; the user's text is cut to LLM.max_chars and a
    stray closing tag in it is neutralised. Validators: alternatives (<= 3 words, no markup, not
    the word itself, no duplicates, at most LLM.max_alternatives), rewrites (non-empty, no markup,
    length bounded), marks (known kinds, indices that fit the words, at most 4). Assistant: each
    request in a daemon thread with LLM.timeout_s, answers via poll(), cancel() drops an answer.
  palmcards/edit.py: replace_word (keeps the word's punctuation, marks stay in place), replace_text
    (a sentence or paragraph rewritten; its marks dropped, the user marks it again), add_marks.
  palmcards/config.py: LLM (provider None, model llama3.1:8b, url, timeout 20 s, max_chars 1200,
    max_alternatives 3).
  palmcards/render.py: ring = word, alternatives, stress/unstress, hear it; "EXPLORE ALTERNATIVES:
    LOADING" and a glyph scramble of the word while waiting; 'PINCH + LIFT: USE "..."'; a focused
    unit with a proposal says "PINCH + LIFT: USE THE PROPOSAL / DROP HAND: DISCARD IT".
  main.py: Devices.llm; Takes.assistant; opening the ring on a word asks for alternatives (once
    per word and revision); a tone/stretch commit asks for a rewrite ("ASKING FOR A WARMER
    VERSION..."); key m on a focused sentence asks for marks; answers become ring nodes or
    proposals (shown as a detail line under the unit); a commit with no operation on a unit with a
    proposal uses it (new revision, undo); backing out discards it; answers made on another
    revision are dropped ("THE NOTES CHANGED: SUGGESTION DROPPED"); failures go to stderr;
    `llm` and `edit` log entries. Without a provider: "... NEED THE OPTIONAL LLM (NOT SET UP, SEE
    README): NOTHING SENT", and the ring simply has no alternatives.
  CLAUDE.md, README.md: the optional LLM and how to set up Ollama.
Migration / compatibility implications: none.
Tests run and exact outcome: pytest (full) -> 369 passed. New tests/test_llm.py (12): notes stay in
  the user message inside one data block (an injected "</notes>" can't close it early), bounded
  length; alternative, rewrite and mark validation; background answer (< 50 ms to ask), cancel,
  provider error as an answer; Ollama request shape against a stand-in server on 127.0.0.1; off
  by default; replace_word/replace_text/add_marks; in the app: alternatives asked once, used as
  a revision, undone; a tone rewrite is a proposal until used; an answer about changed notes is
  dropped; without a provider nothing is sent and it says so.
Manual / hardware checks performed: none with a real model.
Evidence or artifact paths: tests above.
Unverified assumptions and remaining risks: suggestion quality depends on the model and is
  unmeasured. Mark suggestions are applied as a whole proposal (no per-mark toggling by L-hand
  yet), and asked for with the m key: a sentence-level open-palm gesture would change the gesture
  grammar and its regression fixtures, so it is left for a deliberate grammar change.
  Decision needed from the user: a cloud provider (e.g. Anthropic): ANTHROPIC_API_KEY is set in
  this environment, but sending notes off the Mac, and its cost, were not authorised, so no cloud
  provider was implemented or called.
Reason for any departure from this plan: the per-mark L-hand toggle and the open-palm gesture for
  mark suggestions (see above).
Next action: Phase 8 slice 5 (guidance, accessibility, preferences).
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 8 slice 5 (guidance and accessibility)
Status: implemented, validation pending (hands-on with the camera)
Current HEAD / optional commit ID: 6f64dbc -> slice 5 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  palmcards/prefs.py (new): Prefs (tutorial_done, high_contrast, show_hand_box, reach 0.6-1.4,
    start_hold_s, stop_hold_s; clamped), load (missing -> defaults, broken -> defaults + warning,
    unknown keys ignored), atomic save to <data dir>/prefs.json, hand_box(reach), apply() (replaces
    only the fields preferences own in gestures.CURSOR / REHEARSE and render.REHEARSE; contrast),
    CLI `python -m palmcards.prefs [set KEY VALUE]`.
  palmcards/tutorial.py (new): six steps, each advanced by the gesture it teaches (hand held up
    0.5 s, browse by word, browse by sentence, fold-focus, back, count-in).
  palmcards/style.py, render.py: HIGH_CONTRAST palette and set_contrast(); the tutorial card
    (progress dots, "ENTER: SKIP / G: HIDE", the step) at the bottom centre; draw_hand_area (faint
    corners of the hand box).
  main.py: preferences loaded and applied before the overlay and machines are built (run(...,
    prefs_file)); tutorial shown in Prepare until done (saved); Enter skips, g shows/hides; c
    toggles high contrast (saved, overlay rebuilt); the hand area while a hand is up; hints for a
    fist formed from another pose and an open palm outside the zone (throttled to one per 6 s).
  tests/test_app_lifecycle.py: the rig uses its own prefs file (never the real one).
  CLAUDE.md: guidance and preferences.
Migration / compatibility implications: a prefs.json may appear in the data directory.
Tests run and exact outcome: pytest (full) -> 376 passed. New tests/test_guidance.py (7): prefs
  defaults/clamp/unknown keys/broken file; reach scaling on the right half; apply() changes only
  its fields; tutorial steps; hints (formed fist -> hint, real fist -> none, open palm outside the
  zone after 0.8 s -> hint, inside -> none); contrast, card and hand area drawn; keys c and Enter
  (x6) in the app save high_contrast and tutorial_done to the rig's prefs file.
Manual / hardware checks performed: synthetic render sessions/renders/phase8/tutorial-highcontrast-
  busy.png inspected: card and high-contrast text readable over a busy background; the hand area
  corners are faint there by design.
Unverified assumptions and remaining risks: whether the hints fire at helpful moments in real use.
  Open decision: a left-handed layout (mirror text, hand box and zone); MediaPipe's handedness label
  is too unreliable to key it on.
Reason for any departure from this plan: handedness left as a decision (above); "readable status
  hierarchy" addressed by the Phase 4 alert line and label backing rather than a rewrite of the
  label text.
Next action: Phase 8 slice 6 (metrics).
```

```text
Date: 2026-09-26
Phase / issue IDs: Phase 8 slice 6 (metrics, milestone 7 in part)
Status: implemented for speech and hand observations; gaze and posture pending (models,
  calibration, validation)
Current HEAD / optional commit ID: d31bdd7 -> slice 6 commit (see git log)
Pre-existing changes preserved: yes (real sessions only read).
Files and behavior changed:
  palmcards/metrics.py (was a placeholder): take_metrics() -> speech (pace over sentences said,
    fillers per minute, unplanned long pauses excluding silences a pause mark asked for, restarts,
    ad-libs), hands (hand-shape changes per minute from the gesture log; with --trace, share of
    the take a hand was in view and wrist movement in palm widths/s), gaze and posture reported as
    not measured with the reason. Each value carries its basis; too little evidence gives None and
    a reason. summary() for the label.
  palmcards/speech.py: jobs carry duration and the gesture log; run_job computes metrics in the
    worker (never on the camera loop) and adds them to the result and the report.
  palmcards/session.py: TakeRecord.metrics; set_result(..., metrics).
  main.py: stores metrics with the result; the Review label adds "196 WPM, 0 FILLERS/MIN".
  palmcards/config.py: METRICS thresholds. CLAUDE.md, README.md: the take `metrics` field.
Migration / compatibility implications: takes gain "metrics" when analysed (older takes: after
  python -m palmcards.speech --realign).
Tests run and exact outcome: pytest (full) -> 381 passed. New tests/test_metrics.py (5): pace and
  fillers, the planned 2.0 s pause not counted and the 1.8 s one counted; too little -> None with
  reasons; gesture-log shape rate within the take window, trace in-view share and movement
  (1.0 palm/s on a synthetic trace), no trace -> reason; gaze/posture not measured; a silent take's
  metrics stored through run_job and session.json.
Manual / hardware checks performed: read-only metrics on real takes: 20260925-101345 take 1: 196
  wpm, 0 fillers/min, 1 unplanned long pause (its 18 s silence), 3.4 shapes/min, hand in view 38%,
  1.3 palms/s (traced); 20260925-131002 take 1: 210 wpm, 2 unplanned long pauses, 1 restart, 8
  ad-lib runs, 5.4 shapes/min, no trace (reported as such).
Unverified assumptions and remaining risks: the thresholds (1.5 s long pause, minimum speaking
  time) are starting values. Whisper may drop some fillers even with the filler prompt, so the
  filler rate is a lower bound.
Reason for any departure from this plan: gaze, posture and face touching are not implemented:
  they need new models at runtime (with their cost to the camera loop measured), a calibration
  step and validation against people; reporting them as "not measured" is the honest state.
Next action: the end-to-end release gate on the target Mac (hardware; user).
```

```text
Date: 2026-09-26
Phase / issue IDs: Milestone 7, stage 0 (housekeeping and the face/pose performance budget)
Status: measured on hardware; no metric code
Current HEAD / optional commit ID: 1dc7f70 -> stage 0 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  CLAUDE.md: milestone 7 lists what is done (speech metrics, hand-shape changes) and what is left
    (gaze, posture, hand movement and face touches on every take; face and pose only during
    calibration and takes); duplicate llm.py line removed; vision.py, bench_vision, BODY noted.
  palmcards/config.py: BODY (face/pose frame strides and offsets, image size, confidences).
  palmcards/vision.py (new): FaceTracker, PoseTracker on a shared LandmarkTask (LIVE_STREAM, every
    Nth frame, skipped while busy, busy timeout, latency and found counters). Not wired into the app.
  scripts/bench_vision.py (new): the take load (hands every frame, Rehearse overlay, a transcribed
    take replayed through the live follow, the microphone recorded to a temporary folder) plus face
    and pose at chosen strides; per setting: fps, frame times, hand/face/pose latency, results per
    second, share found, live read times, dropped audio, macOS thermal state; gate against the
    nearest earlier baseline.
  tests/test_vision.py (new, 7 tests, fake task, no model files).
Migration / compatibility implications: none.
Tests run and exact outcome: pytest (full) -> 388 passed.
Manual / hardware checks performed (the user in front of the camera; face found in 97-100% of
  results except one 85% setting; MacBook Air M3, 1280x720 at 30 fps, live Whisper base every
  0.3 s in a process):
  run 1 (stopped after 3 settings): base 30.0 fps, hands 15.1 ms; face every 2nd frame 30.0 fps,
    face 10.0 ms at 15/s; every 3rd 30.0 fps.
  run 2 (base, p3, p5, f2p3, f2p5, f3p3, f3p5, base): all fine for the first ~3 min (f2p3 30.0 fps,
    hands 13.1 ms); after that f2p5, f3p3 and f3p5 failed and the last baseline was slower
    (work 5.5 -> 9.4 ms per frame).
  run 3 (reversed): f3p5, f3p3 fine; f2p5 24.9 fps, f2p3 20.6 fps with hands 100 ms; the last
    baseline failed as well (hands 13 -> 53 ms, live read p90 135 -> 242 ms). So the order,
    not the setting, decided which settings failed.
  run 4 (base, f3p5, f3p3 interleaved x4): cool, both 30.0 fps. Warm: base 26.5-29.6 fps, hands
    35-48 ms; f3p5 22.9-25.3 fps, hands 48-78 ms; f3p3 25.5-26.6 fps, hands 46-54 ms; live read
    p90 240-312 ms (the read step is 300 ms).
  run 5 (base, f5p10, f6p15 interleaved x3): cool, all 30.0 fps. A step at ~3.5 min: per-frame
    work 6-7 -> 15-20 ms for every setting, hands-only included; live read p90 briefly 0.8-1.2 s.
    Afterwards base 26.1-27.3 fps; f5p10 24.4-25.1; f6p15 26.9 (level with the baseline).
  No audio was dropped in any run. The "fair" thermal state came within the first minute and did
    not by itself mark the step.
  MediaPipe keeps 16 threads and about 14 MB of each closed face + pose pair (idle, 0% CPU): trackers
    must be made once and kept.
Unverified assumptions and remaining risks: the step looks like the fanless Mac throttling under
  the take load as a whole; which part heats it most (live Whisper every 0.3 s, hands every frame)
  was not measured. The warm slowdown of hands-only (hand latency 13 -> 35-94 ms, live reads
  slower) applies to the app as it is today, not only to milestone 7.
Reason for any departure from this plan: the plan expected one matrix run to name the rates.
  Heat decided the outcome more than the settings did, so the benchmark was changed to reuse
  trackers, report the thermal state and gate against the nearest baseline, and further
  interleaved runs followed.
Next action: stage 1 (capture and calibration) with BODY face every 6th frame (5/s) and pose every
  15th (2/s); decide there whether face and pose back off when frames run late.
```

```text
Date: 2026-09-26
Phase / issue IDs: Milestone 7, stage 1 (capture and calibration)
Status: implemented, validation pending (the app on hardware)
Current HEAD / optional commit ID: 4c51cb5 -> stage 1 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  palmcards/features.py (new): per-result rows (face: head yaw/pitch/roll from the transformation
    matrix, iris position along and across the eye-corner line, eye openness, face box; pose:
    shoulder tilt, width, head height, visibility; hands: count, wrist and fingertip movement in
    palm sizes, nearest fingertip to the face box, highest wrist), calibrate() (median and MAD per
    step, blinks and the first 0.4 s of each step left out, ok/failed/incomplete with a reason),
    save/load of take-NN.face.npz (atomic, provenance).
  palmcards/vision.py: Watcher (calibration and take states, rows from results polled on the
    frame loop, counts, provenance with model SHA-256); trackers submit when N frames have passed
    since the last run and never on a late frame (BODY.late_ms), counted as deferred; mediapipe is
    imported when a tracker is made (its first import took ~0.8 s, which would have eaten the
    calibration's first step).
  palmcards/session.py: schema 3: `calibrations` (all attempts; Session.calibration is the latest
    ok one), take `vision`; schema 2 read as is; session.v1.json only for schema 1.
  main.py: the face/pose watcher opened at start (a missing model or failure: takes go on, their
    `vision` says off and why); the first count-in (or after `e`) starts with the dot for
    BODY.calib_camera_s, then the 3-2-1 on the orange line; the calibration is kept at take start;
    the features are written when the take stops; hand results fed to the watcher.
  palmcards/render.py, style.py: the dot (top centre, a ring closing in), the calibration label
    lines, the count digit hidden during calibration, `E` in the keys help.
  palmcards/config.py: BODY calibration and capture settings.
  palmcards/metrics.py: gaze/posture reasons say the features are recorded, the metrics come next.
  scripts/download_models.py: face and pose landmarkers now fetched by default (required).
  CLAUDE.md: Rehearse (calibration), keys (`e`), session files (take-NN.face.npz, calibrations,
    schema 3, take `vision`), gesture log kinds, code layout (features.py), milestone 7.
  Tests: tests/test_features.py (10), tests/test_vision.py (+6), tests/test_calibration.py (5, the
    app with fake devices: calibrate once, `e` again, no face -> failed twice, off, model failing),
    tests/test_session.py (+2), tests/test_render.py (+1).
Migration / compatibility implications: sessions are written as schema 3; schema 1 and 2 load.
  An older PalmCards refuses a schema-3 session (as designed).
Tests run and exact outcome: pytest (full) -> 412 passed.
Manual / hardware checks performed: the real Watcher with the real camera and models (no window,
  no images kept): a 4.2 s calibration -> ok, face in 63/63 results (24 per step after settling),
  pose 25/25; a 5 s take -> 25 face and 11 pose results (5/s and 2/s as set), all found, 151 hand
  results; iris_x 0.36-0.64, eye_open 0.26-0.42. The person was not looking at a target, so the
  values only show the conversion works.
Unverified assumptions and remaining risks: the dot is drawn at the frame's top centre, under the
  camera only when the window fills the screen; iris position at 640 px wide is coarse (an eye is
  ~30 px); a take cut short by a crash (kill -9) has no feature file.
Next action: the user runs the calibration and a take in the app; then stage 2 (gaze classes and
  the prompted validation take).
Hardware check (2026-09-26, the user; session 20260926-115417-sample_notes-0dad82):
  first count-in: dot, then the orange line; c1 ok (face in 21 and 24 frames after settling, pose
    22). Take 2: plain count-in. `e` then take 3: c2 with the camera covered -> failed, "face seen in
    5 frames looking at the camera and face seen in 0 frames looking at the notes (needs 8)".
  takes 1-3: face 5.0/s, pose 2.0/s, hands 30/s, face found 100% (takes 1-2), nothing deferred or
    skipped during takes; files, provenance and log events as specified.
  Bug found and fixed: a face result for a frame from the calibration, answered after the take
    began, was kept as the take's (take 3: 38 rows for 37 results). Results and hand results from
    before a calibration or take began are now dropped (test added).
  Findings for the next stages:
    - c1's two targets barely differ: camera vs notes yaw -3.2 vs -4.1 deg, pitch -2.3 vs -0.9,
      iris_x 0.496 vs 0.483, iris_y -0.081 vs -0.070, against spreads (MAD) up to 1.5 deg and
      0.054 in the notes step. Stage 2 has to show whether camera and notes can be told apart;
      the face now runs on a 640 px frame (a face ~170 px wide there), full resolution is an option.
    - The face box reached the frame's bottom edge (a close-up, the face 340 x 410 px of 1280 x 720)
      and every raised hand overlapped it: tip_face was 0 for 269 of 277 hand results in take 1.
      A fingertip inside the face's bounding box is not a face touch in such a frame.
  The user's answers explain the first finding: the window was not full screen, so the dot was not
    under the camera, and "the orange line" was not recognised as the orange sentence. Changed: the
    camera step has no dot ("LOOK INTO THE CAMERA ABOVE THE SCREEN", the lens itself, 2.5 s), the
    notes step says "NOW READ THE ORANGE SENTENCE", and the first 0.8 s of each step (was 0.4 s) is
    left out, to read the instruction. c1 of that session stays on record but was made with the old
    wording; the next session calibrates with the new one.
  Re-run with the new wording (session 20260926-120945-sample_notes-3f8353, window not full screen):
    c1 ok, face in 24 + 18 frames. Camera vs notes: yaw -2.1 vs -8.5 deg (12x the larger MAD),
    iris_x 0.536 vs 0.499 (4.5x), pitch -1.7 vs -0.3 (2.5x), iris_y -0.077 vs -0.084 (2.1x). The
    targets now separate, mostly sideways (the notes are left of the camera at about its height);
    the vertical cues are weak. A 35 s take: face found 100%, yaw -6.6..-1.2 (10th-90th
    percentile), between the two baselines.
```

```text
Date: 2026-09-26
Phase / issue IDs: Milestone 7, stage 2 (gaze and its validation)
Status: implemented, validation pending (gaze-check takes)
Current HEAD / optional commit ID: 5afdb89 -> stage 2 commit (see git log)
Pre-existing changes preserved: yes.
Files and behavior changed:
  palmcards/gaze.py (new): classify() (camera / notes / away / unclear: the distance to each
    calibration baseline over yaw, pitch, iris x/y, each divided by max(floor, 2 x the larger MAD);
    nearest within its radius, else away; no face, a blink or a missing value: unclear; baselines
    closer than GAZE.min_separation: everything unclear, said why), take_gaze() (readings while a
    sentence is said, padded 0.2 s: shares of the judged, unclear share, counts, per sentence; None +
    reason when there is too little), prompt_schedule(), check_agreement() (confusion, agreement,
    Cohen's kappa, recall, per-target medians).
  palmcards/metrics.py: gaze from the take's face file and calibration (VERSION 2); reasons for
    takes before milestone 7, tracking off, a missing file.
  palmcards/speech.py: the job carries the take's vision record, face file and calibration.
  palmcards/analysis.py: the analysis-config hash covers METRICS and GAZE too.
  main.py: --gaze-check (every full take calibrates, shows the prompts under "GAZE CHECK" and stops
    itself after the last; the schedule is kept on the take). palmcards/render.py: a title
    replaces only the first label line during takes. palmcards/session.py: take `gaze_check`.
  scripts/evaluate.py: --gaze RUN [--take N] [--sweep] [--json OUT].
  palmcards/config.py: GAZE. CLAUDE.md, README.md, docs/evaluation.md (a Gaze section).
  Tests: tests/test_gaze.py (10), tests/test_calibration.py (+1: a gaze check through the app,
    stopping itself, reported and swept), tests/test_metrics.py (reason text).
Migration / compatibility implications: new analyses have metrics version 2 and a new config hash.
Tests run and exact outcome: pytest (full) -> 424 passed.
Manual / hardware checks performed: read-only, the user's take 1 of 20260926-120945 with its
  calibration c1 (separation 4.96): while speaking, 153 readings judged: camera 7, notes 39, away
  107 (70% away). Not believable for someone reading notes and looking up: the calibration steps
  are steadier (MAD 0.15-0.5 deg) than a head while talking, so the scales sit on their floors, and
  the notes baseline is one sentence where a take reads the whole panel. GAZE is left at its
  starting values; the gaze check is there to set them.
Unverified assumptions and remaining risks: the GAZE starting values; prompted gaze is easier than
  a real talk; one person, one Mac, so far.
Next action: the user records two gaze-check takes; tune GAZE on the first (--sweep), confirm on the
  second; then stage 3.
Gaze-check takes (the user, session 20260926-122645-sample_notes-455f98, window not full screen;
  each take calibrated afresh: c1 separation 3.35, c2 5.83; face found 95-100%):
  starting values: take 1 kappa 0.482 (agreement 0.654), take 2 kappa 0.522 (0.675). Mostly "camera"
    prompts classed away: recall 0.36 and 0.31. Head pitch during the prompts differed from the
    calibration's by 3-8 degrees (take 2: camera -4.7 in the calibration, +3.8 under the prompts).
  sweep on take 1 alone: 25 settings tie at kappa 0.741, and on take 2 those range 0.47-0.88, so one
    take could not choose. Set, looking at both takes: floor_pitch 3.0 and floor_iris_y 0.02 (twice
    the sideways cues' weight removed from the vertical ones), camera_radius 4, notes_radius 6.
    take 1 kappa 0.741 (agreement 0.827; recall camera 0.71, notes 0.93, away 0.84);
    take 2 kappa 0.876 (0.919; camera 0.93, notes 0.98, away 0.82).
  Both takes were used to choose, so neither is a hold-out: a third check take recorded after the
    change confirms or not. Away prompts to the left are the usual miss (classed notes: the notes
    are left of the camera). Take 2's 12 unclear readings: 9 looking to the right (the head turned
    far enough to lose the face), 3 looking down at the desk (lids below the blink line).
  The user's earlier rehearsal take with the new settings: camera 23%, notes 69%, away 7% while
    speaking (was 5 / 26 / 70%).
Hold-out gaze check (session 20260926-123218-sample_notes-c36749, recorded after the change,
  settings not changed since): FAILED. Its calibration c1 separated camera from notes by 1.9999
  (needs 2.0): yaw 0.9 vs -1.2 deg (2.1 apart, where the earlier calibrations had 4.5-7.5), iris_x
  0.503 vs 0.472, pitch -10.3 vs -7.3. So every reading was unclear. Diagnostic only (not used to
  tune): without the separation gate kappa 0.358, agreement 0.59; "camera" prompts classed notes 35
  of 45. Face found in 87% of results (13% of away-prompt readings lost the face).
  Reading: when the notes sit close to the camera on screen, or the head hardly turns, head angles
  can't tell camera from notes; the iris is then the cue, and at 640 px an eye is ~30 px wide. Head
  pitch drifted again (-10.3 in the calibration, about -4 under the prompts). Camera vs notes is
  not reliable across sessions yet.
Next (the user's choice): track the face at full resolution (BODY.face_max_side, apart from pose).
  A first benchmark at 1280 px (sessions/bench-vision-face1280.json) only counts for its first
  f6p15 row (30.0 fps, face 6.7 ms, 5/s, cool Mac): the benchmark counted frames from 0 in each
  setting, and a reused tracker's schedule (since stage 1: N frames after the last run) then
  never came due, so later rows ran no face or pose. Fixed: the benchmark counts frames over the
  whole run, and a tracker whose frame count starts again starts its schedule again (test added).
  The app counts frames over the whole run and was not affected; the stage 0 runs used the older
  modulo schedule and are not affected either.
Benchmark again at 1280 px (sessions/bench-vision-face1280b.json; base and f6p15 interleaved x4,
  35 s each, thermal state fair from ~3 min): every row 30.0 fps, p90 36.8-37.9 ms, hands 13.1 ms,
  face 6.6-6.9 ms at 5/s (found 96-100%), pose ~10 ms at 2/s, live read p90 122-126 ms, no audio
  dropped; face and pose level with hands alone. (The slowdown step of the stage 0 runs did not
  come in these 5 minutes.) BODY.face_max_side set to 1280.
Gaze check at full resolution (session 20260926-125312-sample_notes-b6ff90, window kept in one
  place; rule stated before looking: tune on take 1 only, best kappa, ties within 0.01 -> closest to
  the current settings; take 2 reported once, unchanged):
  calibrations: c1 separation 1.88 (unusable at the current settings), c2 2.22. The orange sentence
    sat almost straight below the camera: yaw 1.6-1.8 deg apart, iris_x 0.009-0.020, pitch 2.4-4 deg.
  take 1 (tune): best kappa 0.449 (15 settings tied); picked floors x0.5 with the vertical floors x2
    on top, camera_radius 4, notes_radius 5. Camera recall 0.02: "camera" readings sat on the notes
    baseline.
  take 2 (held out): kappa 0.615, agreement 0.742; recall camera 0.67, notes 0.73, away 0.83.
  Why: in every check session head pitch under the prompts read 3-9 deg higher than in the
    calibration's camera step, silent readings included (so not speech moving the jaw), and already
    in the take's first 2 s. Where the notes are below the camera, that is larger than the whole
    camera-notes difference. The cause is not known; the calibration's raw readings are not saved.
  Screen (camera or notes) vs away, the same classes merged, current settings, no separation gate:
    kappa 0.84 and 0.87 (the two takes the settings were chosen on), 0.65, 0.79, 0.72 (the three
    recorded after); over all five, screen recall 0.95, away recall 0.82.
  Conclusion: camera vs notes is not reliable across sessions and window placements; screen vs away
    is. GAZE left unchanged (the tuning pick is not adopted: its held-out result is worse than
    screen vs away and camera recall on its own tuning take was 0.02).
The user chose to report screen vs away. palmcards/gaze.py VERSION 2: the take metric gives
  screen_share (camera or notes) and away_share of the judged readings, unclear_share, counts with
  camera and notes apart, `split` {validated: false, separation, note}, and per sentence screen /
  away / unclear (camera and notes kept). A calibration that can't split camera from notes no
  longer makes every reading unclear (usable() and separates() are apart). The gaze check reports
  screen vs away first (agreement, kappa, recall), then the three classes (not validated); --sweep
  ranks by the screen-vs-away kappa. The user's rehearsal take (20260926-120945 take 1): screen
  92.8%, away 7.2% of 153 readings while speaking.
```

```text
Date: 2026-09-26
Phase / issue IDs: Milestone 7, stage 3 (posture and movement)
Status: implemented, validation pending (face touches against a person)
Current HEAD / optional commit ID: b58527d -> stage 3 commit (see git log)
Pre-existing changes preserved: yes.
Checked first (read-only, 10 takes with calibrations): median shoulder tilt during takes within
  1.3 deg of the calibration's; head height 0.01-0.07 shoulder widths lower during takes (more in
  rehearsals than in gaze checks). Unlike gaze pitch, the posture baseline holds, so posture is
  measured against the calibration as asked.
Files and behavior changed:
  palmcards/features.py (VERSION 2): face rows keep the face outline (FACE_OVAL) and the
    cheek-to-cheek width; hand rows add tip_oval (nearest fingertip to the outline, face widths, 0
    inside) and scale (palm size / face width, ~0.6-0.7 at the face's depth). The stage 1 face-box
    test counted every raised hand in a close-up; the outline plus the hand's size is meant to tell a
    touch from a hand in front of the face.
  palmcards/vision.py: the watcher keeps the last face row (BODY.face_max_age_s 1.5 s, was a 0.5 s
    face box) for the hand rows.
  palmcards/metrics.py (VERSION 3): hands from the features when the take has them (in view share,
    wrist and fingertip movement per second with a hand in view, face touches: count and seconds;
    trace otherwise); posture (median shoulder tilt and head height change against the calibration,
    shares over METRICS.tilt_deg / head_drop); None + reason for takes before milestone 7, features
    version 1 (touches), no calibration, too few readings.
  palmcards/config.py: METRICS touch, movement and posture settings (starting values).
  CLAUDE.md, README.md.
  Tests: tests/test_metrics.py (+5: movement, touches with a bridged break, a hand in front of the
    face and a brush, the reasons; posture shares and medians, the reasons), tests/test_features.py
    (+2: outline distance, scale, two hands).
Migration / compatibility implications: new takes have features version 2; version 1 files still
  load (touches say why they're missing). Metrics version 3.
Tests run and exact outcome: pytest (full) -> 433 passed.
Manual / hardware checks performed (read-only, the user's earlier takes): 0dad82 take 1: hand in
  view 19%, wrist 2.9 palms/s, fingertips 6.1; posture tilt +0.6 deg, head -0.029. Take 2: wrist
  0.5, head -0.067 with 32% of readings dropped. 3f8353 take 1: wrist 3.1; posture within the
  baseline. Touches: "recorded before face-touch features".
Unverified assumptions and remaining risks: the touch settings (margin, 0.3-1.0 scale, 0.3 s) are
  starting values never checked against a person; landmark jitter adds to movement (a still hand is
  not 0 palms/s; not measured); a hand covering the face for over 1.5 s can lose the face and end the
  touch early.
Next action: the user records a take with counted face touches and hands held in front of the face
  without touching; compare the counts; then stage 4.
Scripted take (the user, session 20260926-132125-sample_notes-306f4b, 54 s, features v2): asked for
  3 face touches, 2 hands held in front of the face, a head drop and a shoulder tilt of ~3 s each.
  touches: 3 counted (3.3-5.1, 6.4-8.6, 10.0-12.5 s; 6.5 s in all), hand scale 0.42-0.55 (p10-p90),
    fingertips inside the outline. No false touch.
  hands in front of the face: not counted, but mostly because nothing was measured: at 24-26 s the
    face was lost (0-2 of 5 readings) and the hand tracker saw no hand but one result (scale 0.78);
    at 52-54 s a hand near the face measured 0.86-0.91, inside the old 0.3-1.0 range, and missed a
    touch only by staying just off the outline. touch_scale_max lowered to 0.7 (between the two
    groups); the take still gives 3 touches. One take, one person.
  head drop: readings over 0.08 below the baseline at 34.1-37.1 s (plus one at 24.6); face lost
    meanwhile (gaze unclear there). Shoulder tilt: over 5 deg at 42.6-46.1 s (plus single readings at
    15.1, 18.1, 24.1 and during the head drop). Take metrics: tilted 13.3%, head dropped 7.6% of 105
    pose readings; hand in view 15%, wrist 0.98 and fingertips 1.8 palms/s.
  Limits seen: a hand close to the camera in front of the face can hide the face and not be tracked
    as a hand, so hand metrics miss it; looking down at the lap loses the face.
```

```text
Date: 2026-09-26
Phase / issue IDs: Milestone 7, stage 4 (Review and export)
Status: implemented, validation pending (the app on hardware)
Current HEAD / optional commit ID: b29b58d -> stage 4 commit (see git log)
Pre-existing changes preserved: yes.
Decisions (the user): the gaze is labelled ON SCREEN / AWAY (not "eye contact": camera vs notes
  did not validate); "the evaluation export" is a new per-take CSV (nothing with columns existed).
Files and behavior changed:
  palmcards/review.py: Board keeps each take's metrics and its per-sentence gaze counts (mapped
    like the verdicts for takes of an older notes revision); detail() adds "On screen 80%, away
    20%[, unclear n%]." (shares of every reading while the sentence was said, adding to 100), "Gaze:
    too few readings (n)." or "Gaze not measured."; take_summary() and latest() for the card.
  palmcards/render.py, style.py (SUMMARY): the card, bottom right, clear of the text box, first
    line orange, only while browsing Review (not over a focused panel). ViewState.summary.
  main.py: metrics handed to the board (reopened sessions too); the card made again only when the
    latest take or its metrics change.
  palmcards/config.py: REVIEW.min_gaze_readings.
  scripts/evaluate.py: --table [RUN ...] [--csv OUT]: a row per take, the metric columns listed in
    docs/evaluation.md, empty where None, `missing` with the reasons.
  CLAUDE.md, README.md, docs/evaluation.md (The take table).
  Tests: tests/test_review.py (+3), tests/test_render.py (+1), tests/test_evaluate.py (+1).
Tests run and exact outcome: pytest (full) -> 438 passed.
Manual / hardware checks performed: Review drawn offline (no window) from the user's scripted take
  (306f4b): the card reads TAKE 1 0:54 / 5 HIT, 5 MISSED / 149 WPM 0 FILLERS/MIN / ON SCREEN 83%
  AWAY 3% UNCLEAR 15% / HANDS IN VIEW 15% 3 FACE TOUCHES / SHOULDERS TILTED 13% HEAD DROPPED 8%;
  a focused sentence ends "On screen 100%, away 0%." The table over every session: 30 takes in 20
  sessions; 14 without metrics (older analyses; --realign recomputes them).
Unverified assumptions and remaining risks: seen in the app only as an offline render; rounded
  shares can add to 99-101%.
Next action: the user opens Review after a take and focuses a sentence; milestone 7 then done.
```

## Decisions (2026-09-26, by the user)

- **Cloud LLM provider:** part of milestone 8. Until then only the local Ollama provider exists, off by default.
- **Backward flick:** not needed, now that the notes follow the voice live; `b` / `j` / `k` remain as keys.
- **Left-handed layout:** not needed.
- **Push and CI:** approved; pushed to origin/main to prepare for the next milestone.
- **Evaluation data:** scheduled after the LLM work in milestone 8, collecting consented takes from real people (protocol and scoring ready: docs/evaluation.md, scripts/evaluate.py). No accuracy claims until then.

## End-to-end release gate (to run on the target Mac)

`import -> prepare -> rehearse -> review -> play a sentence -> drill -> close -> reopen the saved session`
(`python main.py notes.md`, then `python main.py --open RUN`). Tick each item after checking it live:

- [x] Every sentence and verdict is reachable without clipping. (smoke test 3, 4)
- [x] Gesture controls work, with a reliable fallback stop and navigation route (keys t x n b j k). (2, 3, 5)
- [x] An interrupted take is preserved and recoverable, with an accurate status. (6: Ctrl-C and kill -9)
- [ ] A worker failure can be retried (r) without duplicate or mismatched results. (automated tests only: tests/test_analysis.py)
- [ ] Missing terminal speech does not receive an ending-intonation hit or miss. (automated tests + the real-session re-judge in Phase 5)
- [ ] Editing or moving the imported document does not alter past takes. (automated tests only: tests/test_session.py)
- [x] New edits create revisions and support undo (u); old takes keep their meaning. (2, 10)
- [x] Recording, live-follow and analysis clocks and discontinuities line up (clap test). (7: within the 50 ms bound; exact offset not recorded)
- [ ] Long sessions have bounded queues and memory, and measured performance. (bounded by design and tests; no long live session run)
- [x] Saved sessions reopen, and Review and playback use their own snapshots. (9)
- [x] The available controls match what is implemented. (2, 4)
- [x] Automated tests (pytest: 381 passed on 2026-09-26) and manual checks are reported separately.

```text
Date: 2026-09-26
Phase / issue IDs: end-to-end release gate, automatic part
Status: automatic hardware checks passed; interactive checks pending (need the user at the Mac)
Current HEAD / optional commit ID: cdfa4ee -> hardware check commit (see git log)
Files and behavior changed: scripts/hardware_check.py (new): camera (rate, loop rate with hand tracking
  and drawing), microphone (3 s through AudioRecorder + TakeWriter to a temporary folder, deleted;
  clock source, peak, overflows, gaps), a loopback click (speaker -> microphone round trip), the live
  model's load in its own process, `say`. --quiet skips the sounds; --json saves results.
Tests run and exact outcome: pytest unchanged (381 passed).
Manual / hardware checks performed (MacBook Air, macOS 26.6.2, 2026-09-26), all ok:
  camera 1280x720, 29.9 fps from the camera, 30.2 fps loop with hand tracking (12.8 ms) and the
    overlay (3.2 ms median);
  microphone 48 kHz, first sample placed with the device clock (adc, not the fallback), 3.05 s
    written, peak 0.21 (permission granted), 0 overflows, 0 gaps, state saved;
  click round trip 113.6 ms (output + input latency: the most the audio/app-clock mapping can be off
    by; the input side alone is less);
  live model (whisper-base, pinned) ready in 1.6 s in a separate process; `say` spoke in 2.9 s.
Evidence or artifact paths: sessions/hardware/check-*.json (gitignored).
Unverified assumptions and remaining risks: the interactive checklist (gestures, voice follow in a
  real take, clap test, recovery after a kill, reopen and playback, keys) still needs the user.
Next action: the user runs the interactive release-gate checklist.
```

```text
Date: 2026-09-26
Phase / issue IDs: end-to-end release gate, interactive part
Status: passed on the target Mac (user-reported)
Current HEAD / optional commit ID: db500c9 -> ledger commit (see git log)
Manual / hardware checks performed: the user ran all 10 steps of docs/hardware-smoke-test.md on the
  MacBook Air: start, Prepare gestures (tutorial, ring, hear it, stress + undo, tone message), a
  take with voice follow and flick, Review (chips, paging, playback, drill), keys-only take,
  recovery (Ctrl-C and kill -9), clap test (within 50 ms; offset not written down), close,
  reopen, export. All passed; frame rate stable throughout.
Not exercised live (automated tests only): analysis worker failure + retry, an ending cut short,
  editing/moving the imported file, a long session.
Next action: open decisions (cloud LLM provider, backward flick, left-handed layout, push/CI,
  evaluation data).
```

```text
Date: 2026-09-26
Phase / issue IDs: follow-up to the release gate (user feedback: the highlight should move to the
  next sentence as the previous one is being finished)
Status: implemented; checked on a real recorded take (not yet in a live run)
Files and behavior changed: palmcards/follow.py: the Follower hands the highlight on once the voice
  is probably on the sentence's last word: the last confirmed word + (time since it ended x recent
  speaking rate, 1.5-4 words/s) reaches the end, and at most FOLLOW.handoff_words (2) words are
  still unconfirmed. Words finishing the old sentence after the handoff don't pull it back; the
  guard ends once the new sentence is heard (a genuine re-read of 5 words still moves back).
  Across a section's end the next section's first sentence is highlighted inside the faint
  preview; the recorded section still changes only when its opening is confirmed. main.py keeps the
  preview while the highlight is already in it; render.py draws the current sentence orange even
  inside the preview.
Tests: pytest -> 381 passed (follow tests updated to the new timing, harness lag 0.7 s = the measured
  median; stalls, stray matches and pauses mid-sentence still don't jump).
Check on real audio (LiveFollow + whisper-base, 20260925-101345 take 1 replayed through tap()):
  the highlight moved 0.1-0.7 s after each previous sentence ended (before: about 1 s into the new
  one); during the take's 18 s pause the next section's first sentence waited highlighted in the
  preview, and the section itself changed at 34.6 s (speech resumed at 33.2 s).
```

```text
Date: 2026-09-26
Phase / issue IDs: sentence handoff (8a15f26), live check
Status: verified live by the user: in a live take the highlight moving on as a sentence is finished
  "feels right". FOLLOW.handoff_words stays at 2.
```

```text
Date: 2026-09-26
Phase / issue IDs: first CI run (push approved by the user)
Status: fixed; see the next CI run
What happened: the first run on GitHub (macos-14) gave 376 passed, 5 failed. The live-stream tests
  use a scripted reader, but MlxWhisper.live() resolved the pinned model's local snapshot at
  construction, so they needed the downloaded model: a hidden dependency the Mac hid.
Fix: palmcards/asr.py resolves the pinned snapshot inside the reader (_read_window, once per model),
  so a missing model fails the stream's warm-up ("failed" with the download instruction) instead of
  raising while the app starts the follow, and tests never touch model files.
Checks: pytest with an empty, offline Hugging Face cache (CI's conditions) -> 381 passed; normally ->
  381 passed; real live stream: ready in 1.1 s; with no model: failed in 0.9 s with the message.
```

```text
Date: 2026-09-26
Phase / issue IDs: milestone 8 stage 1, cloud LLM provider (decisions by the user: switched on per run
  with --llm; token counts in the session folder)
Status: implemented, validation pending (no real API call made here: the first ones are the user's)
Files and behavior changed:
  palmcards/llm.py: providers return a Reply (text, input/output tokens, model, request id, attempts,
    problem) instead of a bare string. AnthropicProvider (anthropic SDK 1.8.0, imported only when
    chosen): claude-haiku-4-5, system prompt + the <notes> user turn as before, answers held to a
    per-task JSON schema (output_config.format), LLM.cloud_timeout_s per attempt, one retry after a
    timeout, a dropped connection, 408/409/429 or 5xx, done here with max_retries=0 on the SDK: the SDK
    sleeps for as long as a 429's retry-after says, so a wait longer than cloud_retry_wait_max_s (3 s)
    is not retried. A refusal or a cut-off answer is a failure whose tokens still count. The key:
    ANTHROPIC_API_KEY from the environment, else the repo's .env; never printed, logged or saved, or
    put into os.environ; repr() leaves it out. get_provider(name) raises LLMUnavailable (no key, no
    package). Assistant records a Usage per call (also for answers later cancelled or dropped as
    stale) and gives a short reason for the screen (TIMED OUT, NO CONNECTION, RATE LIMITED, SERVICE
    BUSY, API KEY REJECTED, DECLINED, CUT OFF, BAD ANSWER). `python -m palmcards.llm usage [RUN ...]`
    sums calls, failures and tokens by provider and action, with a cost estimate at
    LLM.cloud_price_usd_per_mtok. OllamaProvider returns prompt_eval_count/eval_count as tokens.
  palmcards/config.py: LLM.cloud_* settings, close_wait_s. palmcards/session.py: log_llm_call appends
    to llm-usage.jsonl (making the folder, as an edit does).
  main.py: --llm off|ollama|anthropic (also with --open); a start-up line saying what is used and
    where the key came from; failures say "<WHAT> FAILED: <REASON>, NOTHING CHANGED" for 3 s; a
    rejected key stays on the alert line in Prepare and Review; at exit, calls in flight get up to
    3 s, then are logged as "abandoned at exit".
    Bug found by the new tests and fixed: logging the first call's tokens makes the session folder and
    so the imported revision's id, which made answers asked for before it look stale ("THE NOTES
    CHANGED"). Requests on the imported notes are now always marked "imported" (Takes._llm_revision).
  palmcards/render.py: CLOUD LLM chip bottom left while the cloud is on (orange "SENDING" while a
    request is out; above the keys help when that is shown); tone/length labels and focus hints say
    "PINCH + LIFT: ASK FOR A REWRITE" with an LLM, "(PREVIEW ONLY: NEEDS THE OPTIONAL LLM)" without.
  tests/conftest.py (autouse): no ANTHROPIC_API_KEY/AUTH_TOKEN/PROFILE, ANTHROPIC_BASE_URL to a closed
    local port, llm.ENV_FILE unreadable.
  requirements: anthropic (+ pydantic, pydantic-core, jiter, docstring-parser, annotated-types,
    typing-inspection, sniffio); the lock diff is additions only.
Tests run and exact outcome: pytest (full) -> 454 passed. New: key from environment/.env and never in
  repr; cloud off without a key; the request against a stand-in Messages API through the real SDK
  (model, system prompt, <notes>, schema, x-api-key only there); retry once on 500/529/0 s 429, not on
  400/401 or a 30 s retry-after; a timeout after two attempts; declined/cut off keep their tokens; the
  usage log (tokens, never the text) and the command; a failure changes nothing; a rejected key on the
  alert line; calls in flight at exit; the folder-making regression; the chip and the labels.
Not checked: a real call to the Anthropic API (the user's stage-1 test).
```

```text
Date: 2026-09-26
Phase / issue IDs: milestone 8 stage 1, check on hardware
Status: the user ran the stage-1 checks (no flag, --llm anthropic with the chip, alternatives, tone
  proposal, Wi-Fi off, a wrong key, the usage command, no key in sessions/): "Everything works as expected."
```

```text
Date: 2026-09-26
Phase / issue IDs: milestone 8 stage 2, open palm for suggested marks
Status: implemented, validation pending
Files and behavior changed:
  palmcards/gestures.py: on a focused sentence (Prepare), a stable open palm sets op "marks" (logged
    "op"), like the word ring; from then on the L-hand no longer turns the tone dial (an open palm
    during the dial switches; the tone preview is dropped, nothing had been sent). Grammar.open_marks
    does the same for the m key; it refuses outside Prepare or away from a focused sentence.
  palmcards/edit.py: new_marks(sentence, marks): only suggestions that add (not ones it has, not a
    second pause in a gap, not stress on a stressed word, not a pace or ending replacing its own),
    in reading order.
  main.py: ask_marks asks once per sentence per revision (cached in Takes.mark_suggestions, cleared
    when the notes change); the frame loop asks while op is "marks"; suggestion_view gives the panel
    the sentence with the suggestions added and which marks are suggestions; pinch + lift with op
    marks adds all of them as one revision (use_suggested_marks; u undoes); dropping the hand adds
    nothing. The marks proposal ("PROPOSED: ...") is gone; tone and length proposals are unchanged.
    Fixed on the way (present since stage 1 for the ring): a request that failed was sent again on
    the next frame while the ring stayed open; now a failed (kind, key) waits for a new focus
    (Takes.llm_failed; m asks again).
  palmcards/render.py, style.py: the focus panel draws the focused sentence with the suggestions, each
    suggested mark in COLORS.suggest_mark (faded yellow; brighter in high contrast); labels for asking,
    ready ("N MARKS SUGGESTED / PINCH + LIFT: ADD ALL / DROP HAND: DISCARD"), empty, failed, no LLM;
    the sentence hint with an LLM starts "OPEN PALM: SUGGEST MARKS"; keys help lists M.
Tests run and exact outcome: pytest (full) -> 461 passed. New: open palm -> op marks, the L-hand no
  longer tilts the tone, commit carries op marks; open_marks only in Prepare on a focused sentence;
  new_marks; asked once across frames and after backing out, shown, back adds nothing, commit adds both
  as one revision, undo; nothing new says so; a failed request not re-sent until a new focus; faded
  drawing and labels. The recorded samples sentence-tone-back and sentence-fold-back replay unchanged
  (no open palm is seen in those real folds and tilts, so nothing would have been sent).
Not checked: the gesture on the camera (the user's stage-2 test).
```

```text
Date: 2026-09-26
Phase / issue IDs: milestone 8 stage 2, check on hardware
Status: the user: "Stage 2 works as expected."
```

```text
Date: 2026-09-26
Phase / issue IDs: milestone 8 stage 3, per-mark toggling
Status: implemented, validation pending
Files and behavior changed:
  palmcards/gestures.py: the ring's knob code is one helper (_turn_knob), used by the ring and by
    spread marks; with op "marks" the L-hand turns it (OPS.knob_step_deg, hysteresis, picked up again
    where it was) instead of the tone dial. A new "toggle" event: a stable pinch that started after the
    hand opened again (the commit's arming) and is let go without a lift; a pinch + lift is only the
    commit; a pinch taken out of view doesn't toggle. The knob doesn't turn while pinched. Logged
    "toggle" (knob).
  palmcards/notes.py: reading_place(mark, n_words), the reading order shared by edit.new_marks and the
    label.
  main.py: a picker per focus (Takes.pick_current / pick_accepted / pick_knob, reset on focus and on
    leaving it): knob steps move it along the suggestions, clamped like Review's take dial; "toggle"
    accepts or rejects the current one (logged "mark_pick") and keeps the focus; pinch + lift adds only
    the accepted ones as one revision ("NO MARKS ACCEPTED: NOTHING CHANGED" with none); dropping the hand
    discards the choices (the suggestions stay cached for the revision, as in stage 2).
  palmcards/render.py, style.py: accepted marks solid yellow (accepted_mark), the knob's mark outlined
    (pick_outline) around its spans (around *word* for a stress); the label: position in reading order,
    the mark in words ([SLOW], *YOU*, // BEFORE "BEING"), accepted or not, what a pinch does, and
    "PINCH + LIFT: ADD n" once something is accepted (else "TURN L-HAND: NEXT").
Tests run and exact outcome: pytest (full) -> 468 passed. New: the knob on spread marks (relative,
  hysteresis, not the tone); a pinch tap toggles once and keeps the focus, the knob continuing; pinch +
  lift commits without a toggle; the focusing fold's pinch and a pinch taken out of view don't toggle;
  the app's clamped knob; accept 2 of 3 (with a reject in between) -> one revision with those two,
  undo; dropping discards; none accepted changes nothing; outline and accepted drawing; the labels.
  Stage-2 tests updated to the new commit (accepted only). Replay samples unchanged.
Not checked: on camera. The risk to look for: moving from an L to a pinch may turn the knob a step
  before the pinch registers (the synthetic hands keep the index still). A --trace recording of the
  motion would become a replay sample.
```

```text
Date: 2026-09-26
Phase / issue IDs: milestone 8 stage 3 check, and the L-hand + pinch problem
Status: the user: stage 3 works, but "Pinching after navigating with L hand switches the selection",
  on every L-hand + pinch control, noticed since early on. Measured, fixed (the user chose the fix:
  a rewind for all L-hand dials plus a visual cue, built now and tuned on a new recording after).
Measurements (the 4 --trace recordings of 2026-09-25, 7 moments where an L became a pinch; scripts
  kept in the session scratchpad, not in the repo):
  - The angle the dials read (index MCP -> tip) turns a median 41 deg (p90 66, max 93) in the
    approach, while the palm (wrist -> index MCP) turns 3.4 deg: curling the index to meet the thumb.
    word-ring-commit shows 105 deg in 0.3 s; its knob only stayed put because the curling hand read as
    FIST for a frame, which re-anchored the knob.
  - The knob as the app ran it: 4/7 pinches landed 1-2 steps off the value 0.4 s before.
  - Reading the palm instead: it follows a deliberate turn (r 0.85) at only ~45% of the angle with
    twice the jitter; not used. Freezing the knob once the thumb nears the tip: 3/7 still off.
    Rewinding at the pinch to where the thumb was clearly out: 1/7 off; to the latest moment within
    0.1 palms of the thumb's farthest in the last 0.5 s: 1/7 off, angle error median 2.0 deg (max
    11.7). A steady L holds the thumb 1.3-1.9 palms from the index tip; a thumb drifting in while
    turning comes within 0.9 palms 2.3% of the time.
Files and behavior changed:
  palmcards/gestures.py: Grammar keeps the dials' values per frame in focus; when the thumb of the
    hand working a dial (both hands for the stretch) comes within OPS.closing_enter (0.9) palms of the
    index tip or a pinch registers, _rewind puts every dial back to its value from just before the
    thumb started closing, and the dials hold (state.closing) until the thumb is past closing_leave
    (1.0); they then pick up from that value. Logged "rewind" with what was undone. The marks knob and
    Review's take dial are clamped to GestureState.dial_limits (set by the app), re-anchoring at the
    ends, so a rewind can't land on a step the selection never took.
  main.py: the marks picker reads the (clamped) knob directly; the app sets the limits for the marks
    (one step per suggestion) and for the take dial (the takes that said the sentence).
  palmcards/render.py, style.py: while held, the picked ring node is boxed, the marks outline is
    thicker, the gauge knob and the stretch line are heavier.
  palmcards/config.py: OPS.closing_enter/closing_leave/rewind_max_s/rewind_plateau, with the evidence.
Trade-off: a thumb tucked hard against the index (within 0.9 palms of its tip) now holds a dial;
  test_thumb_drifting_in_does_not_freeze_a_started_control uses a realistic drifted thumb (0.95).
Tests: pytest (full) -> 477 passed. New: the synthetic approach turns the knob with the fix off (so
  the tests exercise the bug); with it, the ring knob, the marks toggle, the tone commit, the stretch
  and the take dial keep their values through the approach and the pinch; a thumb that closes and
  opens without pinching lets the dial go on from its value; the marks knob stops at its ends and
  turns back at once; the cue is drawn on all four. Replay samples unchanged; word-ring-commit replayed
  through the new code holds the knob from the frame the thumb came within 0.7 palms.
Next: the user records one --trace session made for this (turn, settle, pinch, ~10 times each on the
  ring, the marks and the tone dial); measure before/after, tune closing_enter and the rewind, and cut
  replay samples.
```

```text
Date: 2026-09-26
Phase / issue IDs: the L-hand + pinch fix, measured on the user's recording; LLM tone rewrites refused
Recording: sessions/gesture-logs/20260926-161541.trace.jsonl (5 min: ring 2x, marks 2x, tone 3x,
  stretch 1x; 19 knob pinches). Replayed through ModeMachine it reproduces the live log event for
  event (one extra marks rewind where the app's clamp to the suggestions isn't known offline).
Findings:
  - The rewind worked as built: pinches acting on something other than what was shown when the thumb
    started closing, 10/19 without it, 0/19 with it; against the value shown longest in the 0.4 s
    before the thumb came within 0.9 palms: 9/19 -> 1/19.
  - What was left is the knob itself: it followed every wobble of the index (tens of degrees in a
    fraction of a second, with the thumb still out): 158 knob changes in 2.2 min of turning, 24.5 a
    minute reversed within 0.4 s. The palm is not steadier (at 10 deg a step: 23.6 reversals a minute;
    its jitter cancels its smaller swing); bigger steps help less than waiting: 30 deg a step 10.2,
    15 deg with a 0.15 s dwell 0.4.
  - With the dwell, the rewind could undo a step shown for 0.3 s (t=64.7): a knob step on screen for
    0.25 s now stays.
  - Ring, t=268-313: ten pinches, no lift detected (pinch_lift never fired), so nothing committed.
  - Both tone rewrites were refused as too long ("25 words for 13"; the check allowed 1.6x + 3 = 23;
    the prompt said "about as long" with no number).
Changes:
  palmcards/gestures.py: _step_dial (ring and marks knob, take dial): hysteresis, limits, and the dwell
    (OPS.knob_dwell_s 0.15); _rewind leaves a knob/take step on screen for OPS.rewind_keep_s (0.25 s).
  palmcards/llm.py: rewrite_words(kind, words, amount): a tone rewrite asks for at most 1.3x + 2 words
    (13 -> 19) and accepts up to 2x + 4 (30); a length rewrite asks for about the target (words x
    amount) and accepts up to 2x + 4 of it.
Result on the recording (rewind + dwell + keep): 1/19 pinches off the longest-shown value, 0/19 off
  what was shown when the thumb started closing; knob changes 158 -> 49.
Tests: pytest (full) -> 480 passed (knob tests hold new angles 0.3 s; a quick wobble takes no step; the
  rewind alone still brings a drifted knob back; the 13-word case and the word counts).
```

```text
Date: 2026-09-26
Phase / issue IDs: the word options knob ("too fast and wonky"; "does not turn as expected"), and pointing
  then pinching a word
Recording: sessions/gesture-logs/20260926-163552.trace.jsonl (replayed: the live log reproduced, 22/22
  events). The ring was open 3 s at the end, the hand low (wrist below the frame's bottom edge, the
  Left/Right label flipping, one frame read as a fist).
Findings:
  - Ring: the angle went from +68 to -63 deg in 0.4 s; the dwell kept the knob still through the
    sweep, then took 4 steps at once, and the ring wraps (4 back on 6 options = 2 forward): the
    highlight went the other way. On the earlier recording the dwell knob made 74 multi-step jumps.
  - Word pinch: the index tip sinks as it curls to meet the thumb; 9 of 15 word pinches on six
    recordings focused another word than the one pointed at longest in the 0.5 s before the pinch
    registered (mostly the line below). The thumb can't be the early sign here: pointing, it rests
    within 0.9 palms of the index tip a third of the time (median 1.0).
Changes (the user asked: tilting right goes to the next on the right, left to the previous):
  palmcards/gestures.py: _tilt_step for the ring: past OPS.tilt_on_deg (20) from upright, held
    tilt_hold_s (0.1): one option that way; back within tilt_off_deg (10) arms the next; upright
    follows the resting hand (tilt_recenter_s 1.0); kept on the options (dial_limits, set by the app).
    Simulated on both recordings: ~32 steps a minute (158 for the knob), each one option.
    _step_dial (marks, take dial): after the dwell, walks one step per frame toward the hand, never
    jumps. _hold_word: browsing by word, when the pinch registers the cursor goes back to where it
    was before the thumb started moving in (rewind_max_s, rewind_plateau), held (state.closing)
    until the focus. Replayed: 2/15 word pinches off.
  palmcards/render.py, style.py: the options in a row over the word (under it when the label is in
    the way), left to right, with the curved connectors; the hovered word boxed while a pinch holds it.
  main.py: the ring's limits (its options), no wrap.
Tests: pytest (full) -> 484 passed; ring tests rewritten for tilting (one per tilt, held tilt stays one
  step, threshold and a moment, ends, re-captured upright, drifted thumb, quick and slow curl into a
  pinch); marks knob walks; word pinch rewind (and without it the cursor sinks).
```

```text
Date: 2026-09-26
Phase / issue IDs: the word options: back to the knob
User: tilting "does not feel like a knob"; back to the round ring, with a more sensitive knob (turning
  too far hurts the wrist).
Measured before choosing (both 2026-09-26 recordings, 3.0 min of L-hand): at 10 degrees an option
  with no steadying, 40 quick reversals a minute; One Euro smoothing barely helps (29-35: the
  wobble is real movement, not noise); a sweep-following step rule 28-35; the dwell (0.15 s in the
  same step) 3.7, the same as 15 degrees with it, with the highlight ~0.3 s behind the hand.
Changes: the tilt-to-step code and its settings removed; the ring drawn round again; the ring's knob
  uses OPS.ring_step_deg (10) with the dwell and the one-step-per-frame walk (a turn shows as the
  highlight moving that way round the ring, never a jump), wrapping; the marks knob keeps 15; the
  word-pinch cursor rewind stays.
Result on the recording: 0/19 pinches off what was shown when the thumb started closing, 2/19
  against the value shown longest in the 0.4 s before; knob changes 105 (49 at 15 degrees).
Tests: pytest (full) -> 485 passed (the ring's knob tests back, at its own step; a slow curl too).
```

```text
Date: 2026-09-26
Phase / issue IDs: choosing by pointing (the user: the L-hand word and sentence selection "does not feel
  like it is working"; pivot to pointing, the L kept as the trigger; Review's take dial too, with chips)
Evidence (sessions/gesture-logs/20260926-185900.trace.jsonl, replayed 1:1): the ring open 21 s without
  a choice, its knob turned 85 times across -6..+5 steps, the index angle over 120 degrees; 72 changes
  in 29 s to accept one mark; the hand read as ONE (thumb in, pointing) about 70% of the time.
  Review (20260926-190933): the take dial was tried three times with two takes that said no sentence
  (both ~6 s): nothing to dial, and the screen didn't say so.
Changes:
  palmcards/gestures.py: for the ring, the marks and Review's takes, an L starts a pointer; then the
    index fingertip (thumb in or out) moves GestureState.point (hand-box units since the focus,
    One Euro smoothed, carried on when picked up again: no jump). A pinch rewinds the point to
    before the thumb moved in and emits a "rewind" event (the time it went back to). The knob
    (knob, take_step, dial_limits, the dwell and walk) is gone; the pinch hold on dials stays for
    the tone dial and the stretch.
  palmcards/pick.py (new): Picker: the point starts on the picked item and moves at the scale of
    browsing (hand box onto text box, OPS.point_gain); the nearest item wins once nearer by
    OPS.pick_margin (0.2) of the gap; rewind(t) puts the pick back.
  palmcards/render.py: ring_nodes (where each option sits), mark_points (where each suggested mark
    is, recorded while the panel is drawn), the take chips (a column beside the focused sentence)
    and take_points; labels "L-HAND, THEN POINT TO PICK", "L-HAND, THEN POINT: ANOTHER".
  main.py: three pickers (ring, marks, take), reset on each focus; the frame loop moves them from the
    pointer; "rewind" events put them back; Review shows the take chips and, with one take or none,
    "ONLY TAKE n SAID THIS SENTENCE" / "NO TAKE SAID THIS SENTENCE".
Found on the way: when pointing restarted, the pointer's smoothing filter still held the old hand
  position, so the point jumped by the blend; the filter now resets before the first reading.
Tests: pytest (full) -> 478 passed (the knob tests replaced by pointer tests: an L starts it, thumb in
  keeps it, it carries on without a jump, a pinch rewinds it; the picker's margin and rewind; the
  outline following the point over marks; the take chips in Review; where the pointer's targets are).
```

```text
Date: 2026-09-26
Phase / issue IDs: pointing too slow on the ring and the marks (Review's take chips fine)
Recording: sessions/gesture-logs/20260926-193557.trace.jsonl. Ring open 37 s and 5 s without a choice;
  marks 14 s and 15 s without one, then 28 s for one mark. Pointing 55-83% of those times (restarting
  wasn't it: 1-4 stops a window). The pointer moved at the scale of browsing: 1.15 px across and 0.65
  px up/down per px of fingertip; crossing the ring (414 x 244 px) took ~360-380 px of fingertip, and
  the fingertip roamed 910 x 747 px in the first ring window. Nothing showed the point itself, only the
  pick, so a move showed nothing until an item changed.
Changes: OPS.point_gain = 2 screen px per px of fingertip, the same both ways, for the ring and the
  marks (main.point_scale); Review's take chips keep the scale of browsing. The picker keeps where the
  point is (Picker.at) and the app draws it as a yellow dot while choosing.
Why the tone dial worked and the knob didn't (for the record): the tone commit only uses the sign
  (warmer / more formal), past a small neutral band, and its gauge moves continuously, so a 20-degree
  wobble changes nothing that matters; the ring and the marks need one of 4-6 targets 10-15 degrees
  apart, which the measured wobble (10-25 degrees in fractions of a second) crosses.
Tests: pytest (full) -> 481 passed (the gain the same both ways; the picker's point; the dot drawn).
```

```text
Date: 2026-09-26
Phase / issue IDs: the word options ring as Kat's "spin synonyms" knob (user's request, spec in the chat)
Changes:
  palmcards/knob.py (new): Knob (angle -> step k, unbounded: to k +/- 1 once the angle is
    step_deg / 2 + hyst_deg = 11.5 deg past step k's centre; within dead_deg = 5 deg of the start angle,
    step 0; a fast turn takes every step between; regrip carries on with no jump) and RingSelection
    (the pick by label, wrapping; step 0 is the node it started on; nodes arriving keep the pick).
  palmcards/config.py: KNOB (step_deg 15, hyst_deg 4, dead_deg 5, One Euro on degrees, rotate_s 0.12,
    scramble_s 0.15, vacate_s 0.2).
  palmcards/gestures.py: the ring is turned, not pointed at (_turn_ring): the L's index tilt, One Euro
    smoothed, relative to the L's first angle. GestureState.turning / ring_pick / ring_k / ring_turn.
    The app gives the nodes (Grammar.set_ring_labels, logged as "ring_nodes"). Each step is logged as
    op ring_step (node, word, dir). "ring" joins DIALS, so the pinch hold and rewind cover it.
  palmcards/render.py: the ring turns so the picked node is at 12 o'clock (nodes laid out
    counter-clockwise, so a clockwise turn brings the next one up), eased over rotate_s; the picked
    word previews in the sentence and the label, resolving from random glyphs (resolve_scramble); the
    picked node's box stays empty for vacate_s; stress and hear it have a dimmer border
    (COLORS.node_outline_dim) and never swap the text. ViewState.now (app clock) drives it all.
  main.py: gives the grammar the ring's nodes and follows its knob (TextOverlay.follow_ring); the ring
    picker and its pointer dot are gone (marks and Review's takes still point).
  palmcards/replay.py, scripts/cut_gesture_samples.py: replays keep a step's dir; a sample's optional
    "inputs" (the log's ring_nodes lines) are given back to the grammar at their times.
Decided with the user: the midpoint rule (11.5 deg to step), not STEP + HYST from the centre (19 deg).
  So the band against flicker is 2 x hyst_deg = 8 deg: after a step at c + 11.5, turning back to
  c + 3.5 steps back. The recordings' index wobble was 10-25 deg; check the next --trace recording
  for steps reversed within ~0.4 s before adding a dwell.
Pending: the settled look of the picked node (refills highlighted after vacate_s, or stays empty while
  picked) waits for docs/local/kat-knob-reference.jpg.
The word-ring-commit replay sample (recorded 2026-09-25, with the old knob) now shows 11 ring steps on
  its L turns (5 back during a swing from +45 to -36 deg, 6 on to +66 deg, no reversals). With the
  user's agreement they are in its expected: the cut script takes ring steps from the replay for
  recordings from before the knob (no ring_nodes lines in the log). The other samples are unchanged.
Tests: pytest (full) -> 507 passed (test_knob: hysteresis at a boundary, the dead zone, wrap both ways,
  a fast turn, nodes arriving mid-turn, regrip; grammar: turn and commit, wrap, back to the original,
  thumb drifting in, the pinch keeping the node, dropping the hand; render: the turn to 12 o'clock and
  its easing, the short way round, the scramble in the sentence and label, the empty box, the dim border).
```

```text
Date: 2026-09-26
Phase / issue IDs: no pointer dot on the suggested marks (user's request)
Changes: the yellow dot drawn where the point is while choosing is gone (ViewState.point_at, its
  drawing, HANDS.point_r). It was left only on the suggested marks since the ring became a knob; the
  outlined mark shows the pick. Choosing the marks by pointing is unchanged (Picker.at is still
  computed, just not drawn).
Tests: pytest (full) -> 506 passed (the test that drew the dot removed).
```

```text
Date: 2026-09-26
Phase / issue IDs: no delivery marks, stage 1 of 4: the LLM-suggested marks removed (user's request)
Plan: stage 1 suggestions; 2 Review compares takes on metrics (verdicts and cues.py removed, a voice
  metric from pitch and loudness); 3 the markup itself removed; 4 docs and the study's H1.
Changes: an open palm (or m) on a focused sentence no longer asks for marks; on a focused sentence an
  L-hand is only the tone dial. Gone: Grammar.open_marks, the toggle event (a pinch without a lift),
  Takes.ask_marks / suggestion_view / point_marks / toggle_mark / use_suggested_marks, the marks
  picker and OPS.point_gain (pointing is only Review's take chips now, at the scale of browsing),
  llm.marks_request / parse_marks and the marks schema, edit.add_marks / new_marks, the suggestion
  colours and drawing, the m key. Old llm-usage.jsonl lines with action "marks" still sum; revisions
  made from accepted suggestions are ordinary revisions.
Tests: pytest (full) -> 493 passed (the suggestion tests removed; the pointer tests moved to Review's
  take chips; an open palm on a focused sentence changes nothing). Replays: 12 ok.
```

```text
Date: 2026-09-26
Phase / issue IDs: no delivery marks, stage 2 of 4: Review compares takes on metrics; verdicts removed
Changes:
  palmcards/cues.py and tests/test_cues.py deleted: nothing is judged. CUES in config became PROSODY
    (the pyin settings only; the mark thresholds went).
  palmcards/metrics.py (version 4): speech per sentence (status, pace from its first to its last word,
    METRICS.sentence_min_words aligned words, None + why where the recording lost audio; the fillers
    that count toward it), and a voice group from the take's pitch and loudness: pitch range (10th-90th
    percentile, semitones from the speaker's median) and spread, loudness range, voiced seconds, pitch
    range per sentence; None + reason under METRICS.min_voiced_s. metrics.report for the terminal.
  palmcards/speech.py: no verdicts file, no drill baseline; metrics carry the provenance the verdicts
    did (notes revision, analysis config, ASR, alignment, prosody cache, lost audio).
  palmcards/analysis.py: a drill no longer waits for a baseline take; job records from before (with
    depends_on / baseline_verdicts) still load.
  palmcards/review.py: the board holds each analysed take's per-sentence rows (from its metrics, or
    from its alignment for takes measured before version 4): take_table() sets the last 4 full takes
    side by side (length, WPM, fillers/min, long pauses, restarts, pitch range, on screen, face
    touches, shoulders tilted); detail() lists the focused sentence in every take that said it, the one
    it shows (and plays) marked. Pointing at the take chips is unchanged.
  palmcards/render.py: no verdict chips or symbols; the take table bottom right while browsing Review.
  palmcards/session.py: set_result(number, transcript, alignment, metrics); the take's verdicts and
    marks fields are legacy, kept as found. scripts/evaluate.py: mark agreement removed; the take table
    has the voice columns instead of verdict counts.
Checked on a copy of 20260926-193557 (two takes): --realign printed per-sentence pace and pitch range
  and the voice metric (pitch range 8.6 and 9.7 st); the take table and the focused panel drawn from it.
Docs (CLAUDE.md, README) are rewritten in stage 4.
Tests: pytest (full) -> 466 passed; replays 12 ok.
```

```text
Date: 2026-09-27
Phase / issue IDs: no delivery marks, stage 3 of 4: the markup itself removed
Changes:
  palmcards/notes.py (PARSER_VERSION 2): notes are plain text. Mark, MarkKind, Sentence.marks / pace /
    ending / pauses and Word.stressed are gone. Old markup (/ // *word* [slow] [fast] [rise] [fall]) is
    still recognised by the same rules, only to leave it out of the text; a file with any gets one
    warning ("Ignored N delivery marks ..."). Sentence.raw stays the text as written.
  palmcards/revisions.py: snapshots hold no marks. Parser-1 snapshots (marks, a stressed flag per word)
    load as they are, their hash checked on what they hold, the marks left out of the Notes; sentence
    ids carry on across the change (matched on the text as written).
  palmcards/edit.py: toggle_stress / is_stressed gone; replace_word rebuilds the sentence from its text
    (punctuation such as em dashes kept). palmcards/export.py writes each sentence as it reads.
  The options ring: the word, its alternatives, hear it (stress/unstress gone; hear it still speaks the
    sentence with the focused word emphasised). gestures.DEFAULT_RING = ("original", "hear it").
  palmcards/metrics.py (version 5): long pauses are every silence between words over
    METRICS.long_pause_s (no planned ones any more), counted and per minute of the take
    (long_pauses, long_pauses_per_min); the take table reads unplanned_long_pauses from older metrics.
  SPEECH.scoring_languages became filler_languages (what it still decides).
  samples/sample_notes.md: plain text, its two sentences about marks reworded.
Checked on a copy of 20260926-193557: its parser-1 revision loads and verifies; an edit made a parser-2
  revision keeping the other sentences' ids; export wrote plain text; --realign gave long_pauses.
Tests: pytest (full) -> 461 passed; replays 12 ok.
```

```text
Date: 2026-09-27
Phase / issue IDs: no delivery marks, stage 4 of 4: docs and the study
Changes: CLAUDE.md: what PalmCards is (it measures takes and sets them side by side; it no longer
  judges marks), the feedback constraint (observations only), the ring, Review (take table, a line
  per take in a focused sentence), a short "Delivery marks (removed)" section in place of the marks
  table and the verdicts file, metrics (speech per sentence, voice, long pauses), session files and
  the legacy take fields, code layout, milestones annotated. README.md and docs/hardware-smoke-test.md
  likewise. docs/evaluation.md: H1 now "delivery gets more fluent from the first to the last take"
  (fillers and long pauses per minute), with the note that filler counts need checking against labels
  first; H2's reasoning is the side-by-side comparison; mark labels and mark agreement removed.
The design-note artifact still describes delivery marks; CLAUDE.md says so where it links it.
```

```text
Date: 2026-09-27
Phase / issue IDs: word meanings and sentence hear-it
Changes: Selecting a word in Prepare requests a short contextual meaning from the optional LLM,
  displayed below the word. Only opening the palm requests alternatives; the ring contains the
  original word and replacements. Meanings are validated, cached per revision, cleared on edits,
  and stale answers are discarded. Unavailable/failed meanings say so without repeated requests.
  Hear-it moved to selected sentences: hold an open palm for ~0.6 s, or press a. It speaks the whole
  sentence, once per hold; release and hold again to replay. Review still plays the selected take.
Validation: full headless suite 469 passed; after the definition-card sizing adjustment and an added
  layout test, targeted controls/render/LLM checks 69 passed (8 provider tests deselected, already
  passed in the full run). Rendered at 1280x720 and 640x480, including maximum-length definitions.
Pending: live camera/gesture and audible playback check by the user.
```

```text
Date: 2026-09-27
Phase / issue IDs: immediate in-context tone and length previews
Status: implemented; automated and offline visual checks passed; live validation pending
Changes:
  palmcards/preview.py: explicit operation ID, source revision/unit/original, selected and displayed
    targets, request ticket/generation, loading/error state and per-operation candidate cache.
    Hysteretic cold/original/warm and 70%/100%/130% targets; configurable 250 ms debounce and two-call
    concurrency limit (including obsolete requests). Uses the existing asynchronous Assistant;
    no provider call on hover/focus, no canonical Notes mutation, no slow-request glyph scrambling.
  main.py / gestures.py: deliberate L-hand/two-L activation starts the preview. Pinch + lift only
    accepts the complete candidate already shown for the selected target; loading commits remain
    focused, with no later auto-commit. Drop cancels. An error keeps the prior readable candidate;
    pinch + lift or r retries. Commit uses Session.edit, u restores the original, old takes retain
    their revisions. Undo invalidates active edits and refreshes focus/layout before further input.
  render.py: candidate replaces the focused unit inside a bounded scrollable viewport, dim context,
    anchored starting row, explicit unsaved/loading/error labels and target versus actual counts.
    Header space is reserved; long passages page/scroll; tone gauge retains its compact size and
    does not share the scrollbar's edge. Exact original uses the canonical layout, not a rewrite.
  llm.py: prompts preserve facts, names, numbers, negation and qualifications; forbid invented claims.
    Numeric changes and delivery-mark syntax are rejected. Oversize source passages are explicitly
    unavailable rather than silently truncated. Semantic fidelity still needs real-provider checks.
  Delivery marks: active Notes have no marks (removed previously). Legacy raw/snapshot marks are
    flagged for review, never mapped to new words. Historical mark-bearing snapshots are unchanged.
Automated:
  Full headless suite: 497 passed (two existing protobuf deprecation warnings).
  Final targeted preview/control/lifecycle checks, including an additional legacy-snapshot test:
    44 passed. Controlled response ordering and threading Events avoid timing-fragile sleeps.
  Covers targets/hysteresis/debounce/cache, stale/out-of-order results, cancel/focus/revision changes,
    loading and not-yet-visible commit rejection, failure/retry, save failure, original/undo,
    historical snapshots, removed mark handling, relative gestures and long viewport reachability.
Visual:
  Rendered/inspected original, loading, warm, cold, shorter, longer, error and cancelled at 1280x720
    and 640x480 with offline fixture text; no real provider output was represented as a rewrite.
  QA images: /tmp/palmcards-preview-checks/ (temporary, not committed).
Real-provider checks: not performed; no real service called and no credentials accessed.
Live-gesture checks: not performed; updated docs/hardware-smoke-test.md for the user's camera check.
```
