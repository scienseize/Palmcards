"""The analysis supervisor against scripted fake workers (and the real one on
a silent take): submission never waits, every job ends in a known state,
and results must answer the job they claim to."""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from palmcards.analysis import Supervisor, unfinished_jobs
from palmcards.notes import parse_text
from palmcards.session import Session
from palmcards.speech import make_job

ROOT = Path(__file__).resolve().parent.parent

WORKER = """
import json, os, sys, time
mode, marker = {mode!r}, {marker!r}
first = not os.path.exists(marker)
open(marker, "a").close()
if mode == "slow_start":
    time.sleep(1.0)
if mode == "die_before_read" and first:
    sys.exit(3)
for line in sys.stdin:
    msg = json.loads(line)
    job = json.load(open(msg["input"]))
    reply = {{"id": msg["id"], "take": job["take"], "revision": job["revision"], "config": job["config"], "ok": True}}
    if mode == "die_mid_job" and first:
        sys.exit(4)
    if mode == "die_before_publish" and first:
        open(os.path.join(os.path.dirname(msg["input"]), "artifact-%d" % job["take"]), "w").write("x")
        sys.exit(5)
    if mode == "garbage" and first:
        print("this is not json", flush=True)
        sys.exit(6)
    if mode == "wrong_take":
        reply["take"] += 100
    if mode == "hang":
        time.sleep(3600)
    if mode == "slow_job":
        time.sleep(0.4)
    print(json.dumps(reply), flush=True)
    if mode == "duplicate":
        print(json.dumps(reply), flush=True)
"""


def spawner(tmp_path, mode):
    script = WORKER.format(mode=mode, marker=str(tmp_path / f"{mode}.marker"))
    procs = []

    def spawn():
        procs.append(subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      text=True, bufsize=1))
        return procs[-1]

    spawn.procs = procs
    return spawn


def job(take, baseline=None, size=0):
    j = {"take": take, "revision": "r1", "config": "c1", "blob": "x" * size}
    if baseline is not None:
        j["baseline"] = {"take": baseline, "verdicts": "/nowhere"}
    return j


def collect(sup, until, timeout=10.0):
    results = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        results += sup.poll()
        if until(results):
            return results
        time.sleep(0.02)
    raise AssertionError(f"timed out; got {results}, log {sup.log}")


def records(folder):
    return {r["take"]: r for r in (json.loads(p.read_text()) for p in (folder / "jobs").glob("*.json")
                                   if not p.name.endswith(".input.json"))}


def test_submitting_never_waits_for_the_worker(tmp_path):
    sup = Supervisor(spawn=spawner(tmp_path, "slow_start"))
    t = time.perf_counter()
    assert sup.submit(tmp_path, job(1, size=69_057))  # the size that blocked the old pipe write for 1 s
    assert time.perf_counter() - t < 0.05
    (r,) = collect(sup, lambda rs: rs)
    assert r["ok"] and r["take"] == 1 and sup.pending == []
    sup.close(1)


@pytest.mark.parametrize("mode", ["die_before_read", "die_mid_job", "die_before_publish", "garbage"])
def test_a_worker_that_dies_is_replaced_and_the_job_retried_once(tmp_path, mode):
    spawn = spawner(tmp_path, mode)
    sup = Supervisor(spawn=spawn)
    sup.submit(tmp_path, job(1))
    sup.submit(tmp_path, job(2))
    results = collect(sup, lambda rs: len(rs) >= 2)
    assert sorted((r["take"], r["ok"]) for r in results) == [(1, True), (2, True)]  # each exactly once
    rec = records(tmp_path)
    assert rec[1]["state"] == "succeeded" and rec[1]["attempts"] == 2 and rec[1]["generation"] == 2
    assert len(spawn.procs) == 2
    if mode == "garbage":
        assert any("unexpected output" in line for line in sup.log)
    sup.close(1)


def test_an_old_workers_exit_settles_its_job_after_a_new_worker_started(tmp_path):
    sup = Supervisor(spawn=spawner(tmp_path, "die_mid_job"), max_attempts=1)
    sup.submit(tmp_path, job(1))
    sup.submit(tmp_path, job(2))
    results = collect(sup, lambda rs: len(rs) >= 2)
    by_take = {r["take"]: r for r in results}
    assert by_take[1]["ok"] is False and "exited" in by_take[1]["error"]
    assert by_take[2]["ok"] is True
    assert sup.pending == [] and sup.failed() == [1]  # nothing stuck pending (the review's F6)
    sup.close(1)


def test_results_for_the_wrong_take_or_twice_are_dropped(tmp_path):
    sup = Supervisor(spawn=spawner(tmp_path, "wrong_take"), job_timeout_s=0.5, max_attempts=1)
    sup.submit(tmp_path, job(1))
    (r,) = collect(sup, lambda rs: rs)
    assert r["ok"] is False and any("dropped a result" in line for line in sup.log)
    sup.close(1)

    dup = tmp_path / "dup"
    dup.mkdir()
    sup = Supervisor(spawn=spawner(dup, "duplicate"))
    sup.submit(dup, job(1))
    sup.submit(dup, job(2))
    results = collect(sup, lambda rs: len(rs) >= 2)
    time.sleep(0.3)
    results += sup.poll()
    assert sorted(r["take"] for r in results) == [1, 2]
    assert any("stale or duplicate" in line for line in sup.log)
    sup.close(1)


def test_a_hung_worker_is_stopped_after_the_timeout(tmp_path):
    spawn = spawner(tmp_path, "hang")
    sup = Supervisor(spawn=spawn, job_timeout_s=0.5, max_attempts=1)
    t = time.time()
    sup.submit(tmp_path, job(1))
    (r,) = collect(sup, lambda rs: rs)
    assert r["ok"] is False and time.time() - t < 5 and spawn.procs[0].poll() is not None
    sup.close(1)


def test_a_full_queue_refuses_at_once(tmp_path):
    sup = Supervisor(spawn=spawner(tmp_path, "hang"), max_queue=2)
    assert sup.submit(tmp_path, job(1)) and sup.submit(tmp_path, job(2))
    t = time.perf_counter()
    assert sup.submit(tmp_path, job(3)) is False
    assert time.perf_counter() - t < 0.01
    sup.close(0.2)


def test_closing_is_bounded_and_unfinished_jobs_stay_on_disk(tmp_path):
    spawn = spawner(tmp_path, "hang")
    sup = Supervisor(spawn=spawn)
    sup.submit(tmp_path, job(1))
    sup.submit(tmp_path, job(2))
    collect(sup, lambda rs: sup.state(tmp_path, 1) == "running")
    t = time.time()
    left = sup.close(timeout=0.5)
    assert time.time() - t < 4 and sorted(left) == [1, 2]
    assert all(p.poll() is not None for p in spawn.procs)  # stopped and reaped
    assert sorted((j.take, j.state) for j in unfinished_jobs(tmp_path)) == [(1, "queued"), (2, "queued")]


def test_a_worker_that_never_reads_fails_the_job_without_hanging(tmp_path):
    script = "import sys; sys.exit(9)"  # every worker dies at once: a broken pipe for every handover
    spawn = lambda: subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1)
    sup = Supervisor(spawn=spawn, max_attempts=2)
    sup.submit(tmp_path, job(1))
    (r,) = collect(sup, lambda rs: rs)
    assert r["ok"] is False and records(tmp_path)[1]["attempts"] == 2
    sup.close(1)


def test_a_drill_waits_for_the_take_it_borrows_its_pace_from(tmp_path):
    sup = Supervisor(spawn=spawner(tmp_path, "slow_job"))
    sup.submit(tmp_path, job(3, baseline=2))  # the drill, submitted first
    sup.submit(tmp_path, job(2))
    results = collect(sup, lambda rs: len(rs) >= 2)
    assert [r["take"] for r in results] == [2, 3]
    sup.close(1)


def test_retry_runs_a_failed_job_again(tmp_path):
    sup = Supervisor(spawn=spawner(tmp_path, "die_mid_job"), max_attempts=1)
    sup.submit(tmp_path, job(1))
    (r,) = collect(sup, lambda rs: rs)
    assert r["ok"] is False and sup.failed() == [1]
    assert sup.retry_failed() == 1
    (r,) = collect(sup, lambda rs: rs)
    assert r["ok"] is True and sup.failed() == [] and records(tmp_path)[1]["state"] == "succeeded"
    sup.close(1)


def test_the_real_worker_answers_its_job(tmp_path):
    text = "Hello there friend."
    (tmp_path / "notes.txt").write_text(text)
    session = Session.create(tmp_path / "notes.txt", root=tmp_path / "sessions")
    take = session.add_take(np.zeros(8000, np.float32), 8000, 2.0, datetime.now(), [(0.0, 0)])  # silent: no Whisper
    sup = Supervisor()
    assert sup.submit(session.dir, make_job(session, take, parse_text(text)))
    (r,) = collect(sup, lambda rs: rs, timeout=60)
    assert r["ok"] and (r["take"], r["revision"]) == (1, take.revision) and r["id"]
    assert r["alignment"]["sentences"][0]["status"] == "skipped"
    assert (session.dir / "take-01.verdicts.json").exists()
    assert sup.close(5) == []
