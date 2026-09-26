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

## Open decisions (need the user)

- **Cloud LLM provider.** Only a local Ollama provider is wired in (off by default). `ANTHROPIC_API_KEY`
  is set in this environment, but sending notes off the Mac, and the cost, were never authorised, so
  no cloud provider was implemented or called.
- **Backward flick.** Asked on 2026-09-25, unanswered. The voice only advances; `b`, `j` and `k` correct
  by hand for now.
- **Left-handed layout.** Mirror the text, hand box and command zone? MediaPipe's handedness label is
  too unreliable to switch on automatically.
- **Push and CI.** `.github/workflows/tests.yml` has never run. Pushing publishes the repository, so it
  waits for your go-ahead.
- **Evaluation data.** Consented, labelled takes are needed before any accuracy claim (see
  docs/evaluation.md).

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
