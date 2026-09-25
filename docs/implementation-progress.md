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
| 6 — Live following/state | Pending | Live-follow WIP since committed (`678bfd1`, `c5a5dd2`) | Headless + camera benchmark (user-run) | Integrate after foundations |
| 7 — Setup/evaluation | Pending | None | No real-speaker validation | Lock environment and establish evaluation |
| 8 — Product completion | Pending | None | Planned milestones only | Implement features in small complete slices |
| End-to-end release gate | Pending | None | Not run | Run on target hardware |

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
