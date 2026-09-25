"""Whisper transcription of each take, and its alignment to the notes.

After a take stops, its analysis goes to a worker process run by
palmcards.analysis.Supervisor, so the camera loop never waits on Whisper;
the worker keeps the model loaded between takes. For each take it:

  1. resamples to 16 kHz and trims leading/trailing silence (`offset_s` is
     how much was cut from the front),
  2. runs the recogniser (palmcards.asr; mlx-whisper) with word
     timestamps, the session's language, a filler-laden initial prompt (so
     "um"/"uh" survive) and condition_on_previous_text off,
  3. writes take-NN.transcript.json, every word on the app clock:
     app time = t_start + offset_s + Whisper's time,
  4. aligns the words to the notes (palmcards.align),
  5. meanwhile, in a thread, measures pitch and loudness (palmcards.prosody,
     cached as take-NN.prosody.npz),
  6. judges every delivery mark (palmcards.cues), writes
     take-NN.verdicts.json, and returns the lot, which the app stores on the
     take in session.json.

A drill take (one sentence rehearsed on its own) is aligned against that
sentence only, and its pace is judged against the latest full take
recorded before it (by take number); if that take has no verdicts, the
drill's pace is unclear.

Offline, for takes already recorded:

  python -m palmcards.speech SESSION_DIR [--take N] [--force] [--realign] [--lang xx] [--rebind]
      transcribes takes that have no transcript yet and prints a report;
      --force transcribes again, --realign re-aligns the saved transcripts
      and judges them again without running Whisper or pyin (fast, for
      tuning palmcards/config.py ALIGN and CUES). Takes transcribed before
      milestone 6 get their verdicts on a plain run. Every take is analysed
      against the notes revision it was recorded with (palmcards.session).
      Takes from before notes snapshots have none: --rebind saves the notes
      file as it is now for them, marked unverified.

`python -m palmcards.speech --serve` is the worker: one line per job on
stdin, {"id", "input"} naming the job's input file (written by the
supervisor), and one JSON result per line on stdout, carrying the job's id,
take, notes revision and analysis configuration so the supervisor can check
it answers the job it asked for.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from palmcards import cues, prosody
from palmcards.align import VERSION as ALIGN_VERSION, align, summary
from palmcards.asr import Transcription, get_recognizer, initial_prompt  # noqa: F401 (initial_prompt: re-exported)
from palmcards.audio import prepare_audio, resample, trim_silence  # noqa: F401 (re-exported)
from palmcards.config import ALIGN, CUES, SPEECH
from palmcards.notes import Notes
from palmcards.prosody import Prosody
from palmcards.session import Session, TakeRecord, read_wav

ROOT = Path(__file__).resolve().parent.parent


# --- pitch and loudness --------------------------------------------------------

def take_prosody(wav: Path, t_start: float, cache: Path, silent: bool,
                 reuse_stale: bool = False) -> tuple[Prosody, dict]:
    """The take's pitch and loudness, and where they came from: the cache if
    it was made from this WAV with today's extraction settings ("verified"),
    else measured again and cached. With reuse_stale (re-judging only), a
    mismatched or old cache is used as it is and reported "stale" or
    "unknown"."""
    expected = prosody.provenance(wav, SPEECH.rate, t_start)
    if cache.exists():
        p, meta = prosody.load(cache)
        if meta == expected:
            return p, {"status": "verified", **meta}
        if reuse_stale:
            status = "unknown" if meta is None else "stale"
            changed = sorted(k for k in expected if meta is not None and meta.get(k) != expected[k])
            return p, {"status": status, **(meta or {}), **({"changed": changed} if changed else {})}
    if silent:
        p = Prosody(prosody.EMPTY, prosody.EMPTY, prosody.EMPTY, CUES.hop_s)
    else:
        audio, rate = read_wav(wav)
        p = prosody.analyse(resample(audio, rate), SPEECH.rate, t_start)
    prosody.save(p, cache, expected)
    return p, {"status": "verified", **expected}


# --- Whisper -----------------------------------------------------------------

def words_on_clock(result: dict, t_start: float, offset_s: float) -> list[dict]:
    """Whisper's words, times moved onto the app clock."""
    base = t_start + offset_s
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            text = w["word"].strip()
            if text:
                words.append({
                    "text": text,
                    "start": round(base + float(w["start"]), 3),
                    "end": round(base + float(w["end"]), 3),
                    "probability": round(float(w.get("probability", 0.0)), 3),
                })
    return words


# --- jobs ----------------------------------------------------------------------

def baseline_wpm(baseline: dict | None) -> float | None:
    """A drill's baseline pace: exactly that full take's, from its verdicts,
    or None if it has none (never another take's instead). The supervisor
    runs the drill after that take's own analysis."""
    if not baseline:
        return None
    path = Path(baseline["verdicts"])
    return json.loads(path.read_text()).get("take_wpm") if path.exists() else None


def capture_gaps(take: TakeRecord) -> list[list[float]]:
    """Where the recording lost audio, as app-clock (start, end) intervals."""
    from palmcards.cues import GAP_UNKNOWN_S

    out = []
    for g in (take.capture or {}).get("discontinuities", []):
        start = take.t_start + g["at_s"]
        length = g["samples"] / take.sample_rate if g["samples"] else GAP_UNKNOWN_S
        out.append([round(start, 3), round(start + length, 3)])
    return out


def baseline_take(session: Session, take: TakeRecord) -> TakeRecord | None:
    """The full take a drill's pace is judged against: the latest saved full take before it."""
    earlier = [t for t in session.takes if t.number < take.number and t.drill is None and t.status == "saved"]
    return earlier[-1] if earlier else None


def make_job(session: Session, take: TakeRecord, notes: Notes, realign: bool = False) -> dict:
    """Everything the worker needs, as plain JSON (it never parses the notes)."""
    from palmcards.analysis import analysis_config

    base = baseline_take(session, take) if take.drill is not None else None
    sentences = [[w.norm for w in s.words] for s in notes.sentences]
    if take.drill is not None:  # only the drilled sentence can be matched
        sentences = [words if i == take.drill else [] for i, words in enumerate(sentences)]
    return {
        "take": take.number,
        "revision": take.revision,
        "config": analysis_config(),
        "drill": take.drill,
        "wav": str(session.dir / take.wav),
        "transcript": str(session.dir / take.transcript_name),
        "prosody": str(session.dir / take.prosody_name),
        "verdicts": str(session.dir / take.verdicts_name),
        "t_start": take.t_start,
        "language": session.language,
        "silent": take.silent,
        "sentences": sentences,
        "texts": [s.text for s in notes.sentences],
        "words": [[w.text for w in s.words] for s in notes.sentences],
        "marks": [[[m.kind, m.word] for m in s.marks] for s in notes.sentences],
        "baseline": {"take": base.number, "verdicts": str(session.dir / base.verdicts_name)} if base else None,
        "gaps": capture_gaps(take),
        "duration_s": take.duration_s,
        "gesture_log": str(session.dir.parent / session.gesture_log) if session.gesture_log else None,
        "realign": realign,
    }


def run_job(job: dict) -> dict:
    """Transcribe (unless re-aligning), align and judge one take."""
    path = Path(job["transcript"])
    t0 = time.perf_counter()
    # pyin runs on the CPU while Whisper runs on the GPU.
    measured: dict = {}

    def measure() -> None:
        try:
            measured["prosody"], measured["provenance"] = take_prosody(
                Path(job["wav"]), job["t_start"], Path(job["prosody"]), job["silent"],
                reuse_stale=bool(job.get("realign")))
        except Exception:
            print(f"take {job['take']}: pitch and loudness failed; stress and intonation will be unclear",
                  file=sys.stderr)
            traceback.print_exc()

    thread = threading.Thread(target=measure, name="prosody", daemon=True)
    thread.start()
    if job.get("realign") and path.exists():
        data = json.loads(path.read_text())
    else:
        recognizer = get_recognizer()
        if job["silent"]:
            tr = Transcription("", 0.0, [], recognizer.model, None)
        else:
            tr = recognizer.transcribe(Path(job["wav"]), job["language"])
        data = {
            "take": job["take"],
            "wav": Path(job["wav"]).name,
            "model": tr.model,
            "asr": {"backend": SPEECH.backend, "model": tr.model, "revision": tr.revision},
            "language": job["language"],
            "t_start": job["t_start"],
            "offset_s": round(tr.offset_s, 3),
            "text": tr.text,
            "words": words_on_clock({"segments": tr.segments}, job["t_start"], tr.offset_s),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(path)
    language = job.get("language", "en")
    calibrated = language in SPEECH.scoring_languages
    fillers = ALIGN.fillers if calibrated else ()  # the filler list is English
    alignment = align(job["sentences"], data["words"], fillers)
    thread.join()
    judged = cues.verdicts(job["marks"], alignment, data["words"], measured.get("prosody"),
                           baseline_wpm(job.get("baseline")), drill=job.get("drill") is not None,
                           gaps=job.get("gaps", []), language=language, calibrated=calibrated)
    judged["take"] = job["take"]
    # What produced these verdicts, so they can be checked or reproduced later.
    judged["provenance"] = {
        "notes_revision": job.get("revision"),
        "analysis_config": job.get("config"),
        "asr": data.get("asr", {"model": data.get("model"), "revision": None}),
        "align": {"version": ALIGN_VERSION, "settings": asdict(ALIGN), "fillers": list(fillers)},
        "scoring": {"version": cues.VERSION},
        "prosody": measured.get("provenance", {"status": "failed"}),
        "gaps": job.get("gaps", []),
    }
    vpath = Path(job["verdicts"])
    tmp = vpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(judged, indent=1) + "\n")
    tmp.replace(vpath)
    from palmcards import metrics

    log = Path(job["gesture_log"]) if job.get("gesture_log") else None
    measured_take = metrics.take_metrics(
        alignment, data["words"], job["t_start"], job.get("duration_s", 0.0), log,
        log.with_suffix(".trace.jsonl") if log else None, metrics.planned_pause_words(alignment, job["marks"]))
    return {
        **_identity(job),
        "ok": True,
        "metrics": measured_take,
        "transcript": path.name,
        "alignment": alignment,
        "verdicts": vpath.name,
        "verdict_data": judged,
        "marks": judged["counts"],
        "summary": f"{summary(alignment)}; {cues.summary(judged)}",
        "report": report(alignment, data["words"], job["texts"], job["t_start"]) + "\n"
                  + cues.report(judged, job["words"], job["texts"])
                  + (f"\n  metrics  {metrics.summary(measured_take)}" if metrics.summary(measured_take) else ""),
        "seconds": round(time.perf_counter() - t0, 2),
    }


def _clock(t: float | None, t_start: float) -> str:
    if t is None:
        return "   -   "
    m, s = divmod(max(0.0, t - t_start), 60)
    return f"{int(m)}:{s:04.1f}"


def report(alignment: dict, words: list[dict], texts: list[str], t_start: float) -> str:
    """Readable per-sentence account of a take; times are into the take."""
    lines = []
    for s in alignment["sentences"]:
        span = f"{_clock(s['start'], t_start)}-{_clock(s['end'], t_start)}"
        misheard = ""
        if s["misheard"]:
            heard = [words[s["words"][k]]["text"] for k in s["misheard"]]
            misheard = f"   (heard: {', '.join(heard)})"
        lines.append(f"  [{s['sentence']:2d}] {s['status']:<7} {s['coverage']:4.0%}  {span}  "
                     f"{texts[s['sentence']]}{misheard}")

    def said(ids: list[int]) -> str:
        return " ".join(words[i]["text"] for i in ids)

    for r in alignment["restarts"]:
        lines.append(f"  restart  [{r['sentence']:2d}] {_clock(words[r['words'][0]]['start'], t_start)}  "
                     f"\"{said(r['words'])}\"")
    for e in alignment["extras"]:
        where = f"[{e['sentence']:2d}]" if e["sentence"] is not None else "[  ]"
        lines.append(f"  ad-lib   {where} {_clock(words[e['words'][0]]['start'], t_start)}  \"{said(e['words'])}\"")
    if alignment.get("unsure"):
        lines.append("  unsure   " + ", ".join(f"\"{words[i]['text']}\" {_clock(words[i]['start'], t_start)}"
                                              for i in alignment["unsure"]) + "  (left out)")
    if alignment["fillers"]:
        lines.append("  fillers  " + ", ".join(f"{f['text']} {_clock(f['t'], t_start)}" for f in alignment["fillers"]))
    lines.append("  " + summary(alignment))
    return "\n".join(lines)


# --- worker process ------------------------------------------------------------

def _identity(job: dict) -> dict:
    """What a result must carry to be accepted as the answer to its job."""
    return {"id": job.get("job"), "take": job.get("take"), "revision": job.get("revision"),
            "config": job.get("config")}


def serve() -> None:
    """Worker loop. Results go out on a private copy of stdout; anything a
    library prints lands on stderr, so it can't corrupt the protocol."""
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    for line in sys.stdin:
        if not line.strip():
            continue
        job: dict = {}
        try:
            message = json.loads(line)
            job = json.loads(Path(message["input"]).read_text())
            job["job"] = message["id"]
            result = run_job(job)
        except Exception as exc:
            traceback.print_exc()
            result = {**_identity(job), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(result) + "\n")
        out.flush()


# --- offline CLI -----------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse

    from palmcards.session import SessionError

    ap = argparse.ArgumentParser(prog="python -m palmcards.speech", description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path, help="a folder under sessions/")
    ap.add_argument("--take", type=int, help="only this take (1-based)")
    ap.add_argument("--force", action="store_true", help="transcribe again even if a transcript exists")
    ap.add_argument("--realign", action="store_true", help="re-align saved transcripts, no Whisper")
    ap.add_argument("--lang", help="Whisper language code; saved to the session")
    ap.add_argument("--incomplete", action="store_true",
                    help="also analyse takes that were interrupted or failed while recording")
    ap.add_argument("--rebind", action="store_true",
                    help="give takes from before notes snapshots the notes file as it is now (marked unverified)")
    args = ap.parse_args(argv)
    try:
        return _run_cli(args)
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run_cli(args) -> int:
    from palmcards.analysis import resolve_jobs, unfinished_jobs
    from palmcards.session import LegacyNotes

    session = Session.load(args.session)
    if left := sorted({j.take for j in unfinished_jobs(session.dir)}):
        print(f"analysis left unfinished by the app for take {', '.join(map(str, left))}; running it now")
    if args.rebind:
        rid = session.rebind_legacy()
        print(f"bound takes without notes to {session.notes} as revision {rid} (unverified)")
    if args.lang:
        session.language = args.lang
    takes = [session.take(args.take)] if args.take else session.takes
    status = 0
    for take in takes:
        if take.status != "saved" and not args.incomplete:
            print(f"take {take.number} is {take.status} (cut short while recording); skipped. "
                  "Analyse it anyway with --incomplete")
            continue
        have = take.transcript is not None and (session.dir / take.transcript).exists()
        realign = args.realign and have
        report_only = have and not (args.force or realign) and take.alignment is not None \
            and take.verdicts is not None and (session.dir / take.verdicts).exists()
        try:
            notes = session.notes_for(take)
            if not session.verified(take):
                print(f"take {take.number}: notes revision is unverified (bound after the take was recorded)")
        except LegacyNotes as exc:
            if not report_only:  # results must be tied to known notes
                print(f"error: {exc}", file=sys.stderr)
                status = 1
                continue
            print(f"warning: take {take.number} has no saved notes; reporting against {session.notes} as it is "
                  "now, which may differ from what was rehearsed", file=sys.stderr)
            notes = session.current_notes_unverified()
        if have and not (args.force or realign):
            if not report_only:
                realign = True  # transcribed before milestone 6: judge it now
            else:
                t = json.loads((session.dir / take.transcript).read_text())
                texts = [s.text for s in notes.sentences]
                print(f"take {take.number} (saved; --force to transcribe again, --realign to re-align)")
                print(report(take.alignment, t["words"], texts, take.t_start))
                judged = json.loads((session.dir / take.verdicts).read_text())
                print(cues.report(judged, [[w.text for w in s.words] for s in notes.sentences], texts))
                continue
        result = run_job(make_job(session, take, notes, realign=realign))
        session.set_result(take.number, result["transcript"], result["alignment"], result["verdicts"],
                           result["marks"], result.get("metrics"))
        resolve_jobs(session.dir, take.number, "succeeded")  # any job the app left unfinished for it
        print(f"take {take.number} ({'re-aligned' if realign else 'transcribed'} in {result['seconds']:.1f} s)")
        print(result["report"])
    return status


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        serve()
    else:
        sys.exit(_cli(sys.argv[1:]))
