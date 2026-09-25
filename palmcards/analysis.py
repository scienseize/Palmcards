"""Background analysis of takes: a supervised worker process and job records.

The app hands a finished take's analysis to a Supervisor and never waits:
submit() only puts a small command on a queue. A supervisor thread owns all
job state. It writes the job's input once to the session's jobs/ folder,
starts the worker (python -m palmcards.speech --serve) when needed, and sends
it one short line naming that file; jobs run one at a time, in order.

Job records (jobs/<id>.json, rewritten atomically) hold the take, its notes
revision, the analysis configuration (a hash of the settings that shape the
result), the worker generation it ran on, attempts, timestamps and state:

    queued -> running -> succeeded | failed      (or back to queued to retry)

Every worker process is a new generation. When one exits, every job still
running on that generation is reconciled, however many workers have
started since: retried up to ANALYSIS.max_attempts, then failed. A result
counts only if it answers the running job, for the same take, revision and
configuration; stale, duplicate and malformed replies are logged and
dropped. A job running past ANALYSIS.job_timeout_s gets its worker killed.

close() is bounded: the worker is asked to finish (stdin closed), then
terminated, then killed. Unfinished jobs stay on disk (queued) and are
reported; they are found again with unfinished_jobs() and run by
`python -m palmcards.speech`, which does the same work (speech.run_job).

A drill's pace is judged against the latest full take before it (by take
number, not whichever verdicts file happens to exist); its job waits until
that take's job has finished, or, if no job for it is known, until its
verdicts exist, at most ANALYSIS.baseline_wait_s (then the pace is unclear).
"""

from __future__ import annotations

import hashlib
import json
import queue
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from palmcards.config import ALIGN, ANALYSIS, CUES, SPEECH

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = ("succeeded", "failed")


def analysis_config() -> str:
    """Short hash of every setting that shapes an analysis result."""
    data = json.dumps([asdict(SPEECH), asdict(ALIGN), asdict(CUES)], sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    tmp.replace(path)


@dataclass
class Job:
    id: str
    session: str  # the session folder
    take: int
    revision: str | None
    config: str
    state: str = "queued"
    attempts: int = 0
    generation: int | None = None
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    error: str | None = None
    depends_on: int | None = None  # a drill: the take whose pace it borrows
    baseline_verdicts: str | None = None  # ... and that take's verdicts file

    @property
    def folder(self) -> Path:
        return Path(self.session) / "jobs"

    @property
    def input_path(self) -> Path:
        return self.folder / f"{self.id}.input.json"

    def save(self) -> None:
        self.updated = _now()
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_json(self.folder / f"{self.id}.json", asdict(self))


def unfinished_jobs(session_dir: str | Path) -> list[Job]:
    """Jobs of a session that never finished (the app closed or crashed first)."""
    folder = Path(session_dir) / "jobs"
    out = []
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(".input.json"):
            continue
        job = Job(**json.loads(path.read_text()))
        if job.state not in TERMINAL:
            out.append(job)
    return out


def resolve_jobs(session_dir: str | Path, take: int, state: str, error: str | None = None) -> None:
    """Close a take's unfinished jobs (e.g. after the CLI analysed it)."""
    for job in unfinished_jobs(session_dir):
        if job.take == take:
            job.state, job.error = state, error
            job.save()


def spawn_worker() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "palmcards.speech", "--serve"], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


class Supervisor:
    """Runs analysis jobs in a worker process; see the module doc. The app
    calls submit(), poll(), state(), retry_failed() and close(); nothing
    else touches job state."""

    def __init__(self, spawn: Callable[[], subprocess.Popen] = spawn_worker, max_queue: int = ANALYSIS.max_queue,
                 job_timeout_s: float = ANALYSIS.job_timeout_s, max_attempts: int = ANALYSIS.max_attempts):
        self._spawn = spawn
        self.max_queue, self.job_timeout_s, self.max_attempts = max_queue, job_timeout_s, max_attempts
        self._commands: queue.Queue = queue.Queue()  # app -> supervisor, and worker readers -> supervisor
        self._results: queue.Queue = queue.Queue()  # supervisor -> app
        self._lock = threading.Lock()
        self._states: dict[tuple[str, int], str] = {}  # (session, take) -> state, for the app to read
        self._admitted = 0  # jobs submitted and not yet finished
        self.log: list[str] = []  # what went wrong, for the terminal
        self._jobs: list[Job] = []  # every job this run, in order (supervisor thread only)
        self._accepted: dict[str, float] = {}  # job id -> when it was accepted (monotonic)
        self._proc: subprocess.Popen | None = None
        self._generation = 0
        self._running: Job | None = None
        self._running_since = 0.0
        self._closing = False
        self._thread = threading.Thread(target=self._loop, name="analysis", daemon=True)
        self._thread.start()

    # --- the app's side -------------------------------------------------------

    def submit(self, session_dir: str | Path, job_input: dict) -> bool:
        """Queue a take's analysis. Never blocks; False if the queue is full
        (the take keeps no results yet; submit it again later)."""
        with self._lock:
            if self._closing or self._admitted >= self.max_queue:
                return False
            self._admitted += 1
            self._states[(str(session_dir), job_input["take"])] = "queued"
        self._commands.put(("submit", str(session_dir), job_input))
        return True

    def poll(self) -> list[dict]:
        """Results of finished jobs (validated), oldest first. Never blocks."""
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                return out

    def state(self, session_dir: str | Path, take: int) -> str | None:
        with self._lock:
            return self._states.get((str(session_dir), take))

    @property
    def pending(self) -> list[int]:
        """Takes whose analysis is queued or running, oldest first."""
        with self._lock:
            return [take for (_, take), st in self._states.items() if st in ("queued", "running")]

    def failed(self) -> list[int]:
        with self._lock:
            return [take for (_, take), st in self._states.items() if st == "failed"]

    def retry_failed(self) -> int:
        """Queue failed jobs again (the in-app retry); returns how many."""
        n = len(self.failed())
        if n:
            self._commands.put(("retry",))
        return n

    def close(self, timeout: float = ANALYSIS.shutdown_s) -> list[int]:
        """Stop within about `timeout`: finish or abandon the running job,
        then stop the worker (terminate, kill). Returns the takes left
        unfinished; their jobs stay on disk as queued."""
        with self._lock:
            self._closing = True
        done = threading.Event()
        self._commands.put(("close", timeout, done))
        done.wait(timeout + 5.0)
        self._thread.join(timeout=1.0)
        return self.pending

    # --- the supervisor thread ------------------------------------------------

    def _set(self, job: Job, state: str, error: str | None = None) -> None:
        job.state = state
        if error is not None:
            job.error = error
        try:
            job.save()
        except OSError as exc:
            self.log.append(f"take {job.take}: could not save its job record: {exc}")
        with self._lock:
            self._states[(job.session, job.take)] = state
            if state in TERMINAL:
                self._admitted = max(0, self._admitted - 1)

    def _fail(self, job: Job, why: str) -> None:
        """A job the supervisor gives up on: the app hears about it like a result."""
        self._set(job, "failed", why)
        self._results.put({"id": job.id, "take": job.take, "revision": job.revision, "config": job.config,
                           "ok": False, "error": why})

    def _loop(self) -> None:
        while True:
            try:
                command = self._commands.get(timeout=0.1)
            except queue.Empty:
                command = None
            if command is not None:
                kind = command[0]
                if kind == "submit":
                    self._accept(command[1], command[2])
                elif kind == "retry":
                    for job in self._jobs:
                        if job.state == "failed":
                            job.attempts, job.error = 0, None
                            with self._lock:
                                self._admitted += 1
                            self._set(job, "queued")
                elif kind == "line":
                    self._reply(command[1], command[2])
                elif kind == "exit":
                    self._exited(command[1], command[2])
                elif kind == "close":
                    self._shutdown(command[1])
                    command[2].set()
                    return
            self._check_timeout()
            self._dispatch()

    def _accept(self, session_dir: str, job_input: dict) -> None:
        baseline = job_input.get("baseline") or {}
        job = Job(id=secrets.token_hex(6), session=session_dir, take=job_input["take"],
                  revision=job_input.get("revision"), config=job_input.get("config", ""),
                  depends_on=baseline.get("take"), baseline_verdicts=baseline.get("verdicts"))
        self._accepted[job.id] = time.monotonic()
        try:
            job.folder.mkdir(parents=True, exist_ok=True)
            _write_json(job.input_path, {**job_input, "job": job.id})
        except OSError as exc:
            self._jobs.append(job)
            self._fail(job, f"could not write the job input: {exc}")
            return
        self._jobs.append(job)
        self._set(job, "queued")

    def _ready(self, job: Job) -> bool:
        """A drill waits for the full take whose pace it borrows."""
        if job.depends_on is None:
            return True
        known = [j for j in self._jobs if j.session == job.session and j.take == job.depends_on]
        if known:
            return all(j.state in TERMINAL for j in known)
        if job.baseline_verdicts and Path(job.baseline_verdicts).exists():
            return True
        return time.monotonic() - self._accepted.get(job.id, 0.0) > ANALYSIS.baseline_wait_s

    def _dispatch(self) -> None:
        if self._running is not None or self._closing:
            return
        job = next((j for j in self._jobs if j.state == "queued" and self._ready(j)), None)
        if job is None:
            return
        if self._proc is None or self._proc.poll() is not None:
            try:
                self._start_worker()
            except OSError as exc:
                job.attempts += 1
                why = f"could not start the analysis worker: {exc}"
                if job.attempts >= self.max_attempts:
                    self._fail(job, why)
                else:
                    self._set(job, "queued", why)
                return
        job.attempts += 1
        job.generation = self._generation
        self._running, self._running_since = job, time.monotonic()
        self._set(job, "running")
        try:
            self._proc.stdin.write(json.dumps({"id": job.id, "input": str(job.input_path)}) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:  # the worker is gone: its exit reconciles the job
            self.log.append(f"take {job.take}: could not hand the job to the worker: {exc}")
            self._kill()

    def _start_worker(self) -> None:
        self._generation += 1
        proc = self._spawn()
        self._proc = proc
        threading.Thread(target=self._read, args=(proc, self._generation), name=f"analysis-read-{self._generation}",
                         daemon=True).start()

    def _read(self, proc: subprocess.Popen, generation: int) -> None:
        try:
            for line in proc.stdout:
                self._commands.put(("line", generation, line))
        except (OSError, ValueError):
            pass
        proc.wait()
        self._commands.put(("exit", generation, proc.returncode))

    def _reply(self, generation: int, line: str) -> None:
        try:
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ValueError("not an object")
        except ValueError:
            self.log.append(f"analysis worker {generation}: unexpected output {line.strip()[:80]!r}")
            return
        job = self._running
        if job is None or result.get("id") != job.id or generation != job.generation:
            self.log.append(f"analysis worker {generation}: dropped a stale or duplicate result for take "
                            f"{result.get('take')}")
            return
        expected = (job.take, job.revision, job.config)
        got = (result.get("take"), result.get("revision"), result.get("config"))
        if got != expected:
            self.log.append(f"take {job.take}: dropped a result for {got}, expected {expected}")
            return
        self._running = None
        if result.get("ok"):
            self._set(job, "succeeded")
        else:
            self._set(job, "failed", result.get("error", "analysis failed"))
        self._results.put(result)

    def _exited(self, generation: int, code) -> None:
        if self._proc is not None and generation == self._generation:
            self._proc = None
        # Every job still running on that generation, whatever has started since.
        for job in self._jobs:
            if job.generation == generation and job.state == "running":
                if job is self._running:
                    self._running = None
                why = f"the analysis worker exited (code {code}) during the job"
                if self._closing:
                    self._set(job, "queued", why)  # left for next time
                elif job.attempts < self.max_attempts:
                    self._set(job, "queued", why)
                else:
                    self._fail(job, why)

    def _check_timeout(self) -> None:
        job = self._running
        if job is not None and time.monotonic() - self._running_since > self.job_timeout_s:
            self.log.append(f"take {job.take}: no result after {self.job_timeout_s:.0f} s; stopping the worker")
            self._kill()

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def _shutdown(self, timeout: float) -> None:
        self._closing = True
        deadline = time.monotonic() + timeout
        proc = self._proc
        if proc is not None:
            # Let the running job finish (the worker exits after it), up to the deadline.
            while self._running is not None and time.monotonic() < deadline:
                try:
                    command = self._commands.get(timeout=0.05)
                except queue.Empty:
                    continue
                if command[0] == "line":
                    self._reply(command[1], command[2])
                elif command[0] == "exit":
                    self._exited(command[1], command[2])
            try:
                proc.stdin.close()
            except OSError:
                pass
            for stop in (None, proc.terminate, proc.kill):
                if stop:
                    stop()
                try:
                    proc.wait(timeout=max(0.2, deadline - time.monotonic()) if stop is None else 1.0)
                    break
                except subprocess.TimeoutExpired:
                    continue
        # Whatever did not finish stays on disk, queued, for next time.
        for job in self._jobs:
            if job.state not in TERMINAL:
                self._set(job, "queued")
