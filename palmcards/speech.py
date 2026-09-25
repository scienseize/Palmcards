"""Whisper transcription of each take, and its alignment to the notes.

After a take stops, its WAV goes to a worker process (`Transcriber`), so
the camera loop never waits on Whisper; the worker keeps the model loaded
between takes. For each take it:

  1. resamples to 16 kHz and trims leading/trailing silence (`offset_s` is
     how much was cut from the front),
  2. runs mlx-whisper with word timestamps, the session's language, a
     filler-laden initial prompt (so "um"/"uh" survive) and
     condition_on_previous_text off,
  3. writes take-NN.transcript.json, every word on the app clock:
     app time = t_start + offset_s + Whisper's time,
  4. aligns the words to the notes (palmcards.align),
  5. meanwhile, in a thread, measures pitch and loudness (palmcards.prosody,
     cached as take-NN.prosody.npz),
  6. judges every delivery mark (palmcards.cues), writes
     take-NN.verdicts.json, and returns the lot, which the app stores on the
     take in session.json.

A drill take (one sentence rehearsed on its own) is aligned against that
sentence only, and its pace is judged against the last full take's.

Offline, for takes already recorded:

  python -m palmcards.speech SESSION_DIR [--take N] [--force] [--realign] [--lang xx]
      transcribes takes that have no transcript yet and prints a report;
      --force transcribes again, --realign re-aligns the saved transcripts
      and judges them again without running Whisper or pyin (fast, for
      tuning palmcards/config.py ALIGN and CUES). Takes transcribed before
      milestone 6 get their verdicts on a plain run.

`python -m palmcards.speech --serve` is the worker: one JSON job per line
on stdin, one JSON result per line on stdout.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from math import gcd
from pathlib import Path

import numpy as np

from palmcards import cues, prosody
from palmcards.align import align, summary
from palmcards.config import SPEECH
from palmcards.notes import Notes
from palmcards.prosody import Prosody
from palmcards.session import Session, TakeRecord, read_wav

ROOT = Path(__file__).resolve().parent.parent


# --- audio -------------------------------------------------------------------

def resample(audio: np.ndarray, rate: int, target: int = SPEECH.rate) -> np.ndarray:
    if rate == target:
        return audio.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(rate, target)
    return resample_poly(audio, target // g, rate // g).astype(np.float32)


def trim_silence(audio: np.ndarray, rate: int) -> tuple[int, int]:
    """(start, end) sample range holding the sound, padded; (0, 0) if none."""
    frame = max(1, int(rate * SPEECH.trim_frame_s))
    n = len(audio) // frame
    if n == 0:
        return 0, 0
    frames = audio[: n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    loud = np.flatnonzero(db > max(SPEECH.trim_floor_db, db.max() - SPEECH.trim_below_peak_db))
    if len(loud) == 0:
        return 0, 0
    pad = int(rate * SPEECH.trim_pad_s)
    return max(0, loud[0] * frame - pad), min(len(audio), (loud[-1] + 1) * frame + pad)


def prepare_audio(wav: Path) -> tuple[np.ndarray, float]:
    """16 kHz mono float32 with the silence at both ends trimmed, and the
    seconds cut from the front."""
    audio, rate = read_wav(wav)
    audio = resample(audio, rate)
    start, end = trim_silence(audio, SPEECH.rate)
    return audio[start:end], start / SPEECH.rate


def take_prosody(wav: Path, t_start: float, cache: Path, silent: bool) -> Prosody:
    """The take's pitch and loudness, from the cache or measured (and cached)."""
    if cache.exists():
        return prosody.load(cache)
    if silent:
        p = Prosody(prosody.EMPTY, prosody.EMPTY, prosody.EMPTY)
    else:
        audio, rate = read_wav(wav)
        p = prosody.analyse(resample(audio, rate), SPEECH.rate, t_start)
    prosody.save(p, cache)
    return p


# --- Whisper -----------------------------------------------------------------

def initial_prompt(language: str) -> str | None:
    return dict(SPEECH.filler_prompts).get(language)


def transcribe(audio: np.ndarray, language: str) -> dict:
    import mlx_whisper  # heavy; only the worker needs it

    return mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=SPEECH.model,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=initial_prompt(language),
        hallucination_silence_threshold=SPEECH.hallucination_silence_s,
        verbose=None,
    )


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

def baseline_wpm(paths: list[str]) -> float | None:
    """Pace of the first of these verdicts files that has one. The worker
    reads them when it gets to the drill: takes are judged in order, so the
    full take just before it is done by then even if it wasn't at submit."""
    for path in map(Path, paths):
        if path.exists() and (wpm := json.loads(path.read_text()).get("take_wpm")) is not None:
            return wpm
    return None


def make_job(session: Session, take: TakeRecord, notes: Notes, realign: bool = False) -> dict:
    """Everything the worker needs, as plain JSON (it never parses the notes)."""
    sentences = [[w.norm for w in s.words] for s in notes.sentences]
    if take.drill is not None:  # only the drilled sentence can be matched
        sentences = [words if i == take.drill else [] for i, words in enumerate(sentences)]
    return {
        "take": take.number,
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
        # A drill's pace is judged against the latest full take's (newest first).
        "baseline_from": [str(session.dir / t.verdicts_name) for t in reversed(session.takes[: take.number - 1])
                          if t.drill is None] if take.drill is not None else [],
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
            measured["prosody"] = take_prosody(Path(job["wav"]), job["t_start"], Path(job["prosody"]), job["silent"])
        except Exception:
            print(f"take {job['take']}: pitch and loudness failed; stress and intonation will be unclear",
                  file=sys.stderr)
            traceback.print_exc()

    thread = threading.Thread(target=measure, name="prosody", daemon=True)
    thread.start()
    if job.get("realign") and path.exists():
        data = json.loads(path.read_text())
    else:
        audio, offset_s = (np.zeros(0, np.float32), 0.0) if job["silent"] else prepare_audio(Path(job["wav"]))
        if len(audio):
            print(f"transcribing take {job['take']} ({len(audio) / SPEECH.rate:.1f} s of sound)...",
                  file=sys.stderr, flush=True)
            result = transcribe(audio, job["language"])
        else:
            result = {"text": "", "segments": []}
        data = {
            "take": job["take"],
            "wav": Path(job["wav"]).name,
            "model": SPEECH.model,
            "language": job["language"],
            "t_start": job["t_start"],
            "offset_s": round(offset_s, 3),
            "text": result.get("text", "").strip(),
            "words": words_on_clock(result, job["t_start"], offset_s),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(path)
    alignment = align(job["sentences"], data["words"])
    thread.join()
    judged = cues.verdicts(job["marks"], alignment, data["words"], measured.get("prosody"),
                           baseline_wpm(job["baseline_from"]) if job["baseline_from"] else None)
    judged["take"] = job["take"]
    vpath = Path(job["verdicts"])
    tmp = vpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(judged, indent=1) + "\n")
    tmp.replace(vpath)
    return {
        "take": job["take"],
        "ok": True,
        "transcript": path.name,
        "alignment": alignment,
        "verdicts": vpath.name,
        "verdict_data": judged,
        "marks": judged["counts"],
        "summary": f"{summary(alignment)}; {cues.summary(judged)}",
        "report": report(alignment, data["words"], job["texts"], job["t_start"]) + "\n"
                  + cues.report(judged, job["words"], job["texts"]),
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

def serve() -> None:
    """Worker loop. Results go out on a private copy of stdout; anything a
    library prints lands on stderr, so it can't corrupt the protocol."""
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    for line in sys.stdin:
        if not line.strip():
            continue
        job = json.loads(line)
        try:
            result = run_job(job)
        except Exception as exc:
            traceback.print_exc()
            result = {"take": job.get("take"), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(result) + "\n")
        out.flush()


class Transcriber:
    """Runs the worker process and hands it jobs; used by the app.

    submit() never blocks; poll() returns finished results. The worker is
    started at the first job and keeps the Whisper model loaded after that.
    """

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._results: queue.Queue = queue.Queue()
        self.pending: list[int] = []  # take numbers, oldest first

    def _start(self) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-m", "palmcards.speech", "--serve"], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read, args=(proc,), name="transcriber", daemon=True).start()
        return proc

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                self._results.put(json.loads(line))
            except json.JSONDecodeError:
                print(f"transcriber: unexpected output {line!r}", file=sys.stderr)
        self._results.put({"exited": proc})

    def submit(self, job: dict) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = self._start()
        self._proc.stdin.write(json.dumps(job) + "\n")
        self._proc.stdin.flush()
        self.pending.append(job["take"])

    def poll(self, timeout: float | None = None) -> list[dict]:
        """Finished results. With a timeout, wait that long for the first."""
        out = []
        try:
            item = self._results.get(timeout=timeout) if timeout else self._results.get_nowait()
            while True:
                out.append(item)
                item = self._results.get_nowait()
        except queue.Empty:
            pass
        results = []
        for item in out:
            if "exited" in item:
                if item["exited"] is self._proc:  # died: fail whatever it still had
                    self._proc = None
                    results += [{"take": n, "ok": False, "error": "transcriber exited"} for n in self.pending]
                    self.pending = []
                continue
            if item.get("take") in self.pending:
                self.pending.remove(item["take"])
            results.append(item)
        return results

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()


# --- offline CLI -----------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse

    from palmcards.notes import load_notes

    ap = argparse.ArgumentParser(prog="python -m palmcards.speech", description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path, help="a folder under sessions/")
    ap.add_argument("--take", type=int, help="only this take (1-based)")
    ap.add_argument("--force", action="store_true", help="transcribe again even if a transcript exists")
    ap.add_argument("--realign", action="store_true", help="re-align saved transcripts, no Whisper")
    ap.add_argument("--lang", help="Whisper language code; saved to the session")
    args = ap.parse_args(argv)

    session = Session.load(args.session)
    if args.lang:
        session.language = args.lang
    notes = load_notes(session.notes)
    takes = [session.take(args.take)] if args.take else session.takes
    for take in takes:
        have = take.transcript is not None and (session.dir / take.transcript).exists()
        realign = args.realign and have
        if have and not (args.force or realign):
            if take.alignment is None or take.verdicts is None or not (session.dir / take.verdicts).exists():
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
                           result["marks"])
        print(f"take {take.number} ({'re-aligned' if realign else 'transcribed'} in {result['seconds']:.1f} s)")
        print(result["report"])
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        serve()
    else:
        sys.exit(_cli(sys.argv[1:]))
