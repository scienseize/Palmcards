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
| 3 — Analysis supervision | Pending | None | Review reproductions only | Implement bounded transport/generations |
| 4 — Navigation and controls | Pending | None | Review renders only | Implement viewports and fallback controls |
| 5 — Scoring/provenance | Pending | None | Review false-positive reproduction | Fix tail evidence and cache metadata |
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
