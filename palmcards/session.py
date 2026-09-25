"""Sessions on disk: one folder per app run, a WAV per take, session.json.

  sessions/20260925-143000-sample_notes-3fa9c1/
      session.json              schema 2, see below
      session.lock              held (flock) by the process writing the session
      source/sample_notes.md    the imported notes file, byte for byte
      notes/r1a2b3c4d5e6f.json  the parsed notes each take was rehearsed against
      take-01.wav
      take-01.transcript.json   (milestone 5, see palmcards.speech)
      take-01.prosody.npz       (milestone 6: pitch and loudness, palmcards.prosody)
      take-01.verdicts.json     (milestone 6: a verdict per delivery mark, palmcards.cues)
      take-02.wav

Take times (`t_start`, and the gesture log's `t`) share one clock: seconds
since the app started. Section marks are seconds into the take.

The folder is created with the first take, so a run without takes leaves
nothing behind. Its name ends in a random suffix and it is created
exclusively, so two runs started in the same second never share a folder.
The process that creates it holds its lock until it exits; another process
(e.g. `python -m palmcards.speech` on a session the app still has open)
can't write it meanwhile. The lock is an flock, which the system releases
when its owner exits or crashes, so a stale lock never needs clearing.

Notes. A session keeps the imported file's bytes and each notes revision
(palmcards.revisions); every take names the revision it was recorded with.
Playback and re-analysis read that revision, never the file at `notes`,
which is kept only as where the notes came from.

Schema history:
  1  (no "schema" key) notes path only. Still readable. Its takes have no
     revision; `rebind_legacy` imports the current notes file for them,
     marked "legacy-unverified" as it may differ from what was rehearsed.
     The first save as schema 2 keeps the old file as session.v1.json.
  2  revisions, source, take revisions, id.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import wave
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

import numpy as np

from palmcards import revisions
from palmcards.notes import Notes, notes_from_bytes
from palmcards.paths import data_dir

SESSIONS_DIR = data_dir()  # palmcards.paths: $PALMCARDS_DATA, the checkout's sessions/, or Application Support
SILENT_PEAK = 1e-3  # a take whose loudest sample is below this is silence
SCHEMA = 2
TAKE_WAV = re.compile(r"take-(\d+)\.wav$")
TAKE_FILE = re.compile(r"take-(\d+)\.(?:wav|wav\.part|recording\.json)$")
STATUSES = ("saved", "interrupted", "failed")


class SessionError(Exception):
    """A session folder that can't be used as asked (the message says why)."""


class SessionBusy(SessionError):
    pass


class LegacyNotes(SessionError):
    """A take recorded before notes snapshots: which notes it used is unknown."""


@dataclass
class TakeRecord:
    number: int
    wav: str  # file name inside the session folder
    started: str  # wall-clock time recording began, ISO 8601
    t_start: float  # app clock at the first sample
    duration_s: float
    sample_rate: int
    peak: float  # loudest |sample|, 0..1
    sections: list[dict] = field(default_factory=list)  # {"section": i, "t": s into the take}
    transcript: str | None = None  # take-NN.transcript.json, once transcribed
    alignment: dict | None = None  # see palmcards.align
    verdicts: str | None = None  # take-NN.verdicts.json, once judged
    marks: dict | None = None  # verdict counts: {"hit": 5, "missed": 2, "unclear": 1, "skipped": 0}
    drill: int | None = None  # a drill: the one sentence (Notes.sentences index) it rehearsed
    revision: str | None = None  # the notes revision it was recorded with; None before schema 2
    # "saved": recorded and finished normally; "interrupted": cut short (the app
    # stopped or crashed mid-take; recovered at the next start); "failed": the
    # disk refused a write, what came before it is kept. Only saved takes are
    # analysed automatically.
    status: str = "saved"
    capture: dict | None = None  # clock, gaps, overflows: see Session.finish_take
    live: dict | None = None  # voice follow during the take: engine, state, words, lag (display only)

    @property
    def silent(self) -> bool:
        return self.peak < SILENT_PEAK

    def _sibling(self, suffix: str) -> str:
        return Path(self.wav).with_suffix(suffix).name

    @property
    def transcript_name(self) -> str:
        return self._sibling(".transcript.json")

    @property
    def prosody_name(self) -> str:
        return self._sibling(".prosody.npz")

    @property
    def verdicts_name(self) -> str:
        return self._sibling(".verdicts.json")


TAKE_FIELDS = {f.name for f in fields(TakeRecord)}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


@dataclass
class Session:
    notes: Path  # where the notes were imported from (provenance only)
    dir: Path
    gesture_log: str | None = None  # path relative to the sessions root
    takes: list[TakeRecord] = field(default_factory=list)
    language: str = "en"  # Whisper language code for every take
    id: str = ""
    source: dict | None = None  # {"file", "sha256", "size"}: the imported file, kept in the folder
    revisions: list[dict] = field(default_factory=list)
    schema: int = SCHEMA
    orphans: list[str] = field(default_factory=list)  # files found on load that no take claims
    _loaded_schema: int = SCHEMA
    _parsed: Notes | None = None  # notes of the pending first revision, until the folder exists
    _source_bytes: bytes | None = None
    _lock_fd: int | None = None
    _snapshots: dict = field(default_factory=dict)  # revision id -> snapshot (cache)

    # --- creating and loading ----------------------------------------------

    @classmethod
    def create(cls, notes: str | Path, root: Path = SESSIONS_DIR, gesture_log: Path | None = None,
               now: datetime | None = None, language: str = "en", parsed: Notes | None = None,
               source: bytes | None = None) -> Session:
        """A new session for notes imported from `notes`. Pass the file's bytes
        (`source`) and what they parsed to (`parsed`) as the app loaded them;
        without them the file is read now. Nothing is written until the first take."""
        now = now or datetime.now()
        path = Path(notes).expanduser().resolve()
        if source is None:
            source = path.read_bytes()
        if parsed is None:
            parsed = notes_from_bytes(source, path)
        log = None
        if gesture_log is not None:
            log = str(gesture_log.relative_to(root)) if gesture_log.is_relative_to(root) else str(gesture_log)
        session = cls(path, root / cls._name(now, path.stem), log, language=language, id=secrets.token_hex(8))
        session._parsed, session._source_bytes = parsed, source
        return session

    @staticmethod
    def _name(now: datetime, stem: str) -> str:
        return f"{now:%Y%m%d-%H%M%S}-{stem}-{secrets.token_hex(3)}"

    @classmethod
    def load(cls, folder: str | Path) -> Session:
        folder = Path(folder).expanduser().resolve()
        path = folder / "session.json"
        if not path.exists():
            raise SessionError(f"{folder} is not a session folder (no session.json)")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise SessionError(f"{path} is not valid JSON: {exc}") from None
        schema = data.get("schema", 1)
        if not isinstance(schema, int) or schema > SCHEMA:
            raise SessionError(f"{path} has schema {schema!r}; this PalmCards reads schemas 1 to {SCHEMA}. "
                               "Update PalmCards to open it.")
        if not isinstance(data.get("takes"), list) or "notes" not in data:
            raise SessionError(f"{path} is missing 'notes' or 'takes'")
        takes = []
        for t in data["takes"]:
            unknown = set(t) - TAKE_FIELDS
            if unknown:
                raise SessionError(f"{path}: take {t.get('number')} has unknown fields {sorted(unknown)}")
            takes.append(TakeRecord(**t))
        session = cls(
            Path(data["notes"]), folder, data.get("gesture_log"), takes, data.get("language", "en"),
            id=data.get("id", ""), source=data.get("source"), revisions=data.get("revisions", []),
            schema=SCHEMA, _loaded_schema=schema,
        )
        session.orphans = session._find_orphans()
        return session

    def _find_orphans(self) -> list[str]:
        """Take WAVs no take claims, and half-written files (*.tmp): left by a
        crash between writing a file and recording it. Kept, never overwritten."""
        claimed = {t.wav for t in self.takes}
        out = [p.name for p in sorted(self.dir.glob("take-*.wav")) if p.name not in claimed]
        return out + [p.name for p in sorted(self.dir.rglob("*.tmp"))]

    # --- ownership ---------------------------------------------------------

    def acquire(self) -> None:
        """Become the session's only writer, or raise SessionBusy."""
        if self._lock_fd is not None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / "session.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                owner = (self.dir / "session.lock").read_text().strip() or "another process"
                raise SessionBusy(f"{self.dir.name} is open in {owner}; close it there first") from None
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"pid {os.getpid()}".encode())
        self._lock_fd = fd

    def release(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def _open_new_folder(self) -> None:
        """Create the folder exclusively (a fresh name if one is taken) and own it."""
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            try:
                self.dir.mkdir()
                break
            except FileExistsError:
                stamp = self.dir.name[:15]
                self.dir = self.dir.parent / self._name(datetime.strptime(stamp, "%Y%m%d-%H%M%S"), self.notes.stem)
        else:
            raise SessionError(f"could not create a new session folder in {self.dir.parent}")
        self.acquire()

    def _ensure_written(self) -> None:
        """A new session's first write: a fresh folder (never an existing one),
        the imported file and the first notes revision. A loaded session: own it."""
        if self._parsed is None:
            self.acquire()
            return
        self._open_new_folder()
        self._store_source(self._source_bytes)
        self.add_revision(self._parsed, provenance="imported")
        self._parsed = self._source_bytes = None

    # --- notes -------------------------------------------------------------

    def _store_source(self, data: bytes) -> None:
        folder = self.dir / "source"
        folder.mkdir(exist_ok=True)
        name = self.notes.name
        _write_atomic(folder / name, data)
        self.source = {"file": f"source/{name}", "sha256": _sha256(data), "size": len(data)}

    def add_revision(self, notes: Notes, provenance: str = "edited", note: str = "") -> str:
        """Save `notes` as a revision (unchanged sentences keep their ids) and
        make it current; returns its id."""
        previous = self.snapshot(self.revisions[-1]["id"]) if self.revisions else None
        snap, ancestry = revisions.to_snapshot(notes, previous)
        rid = revisions.revision_id(snap)
        if not any(r["id"] == rid for r in self.revisions):
            folder = self.dir / "notes"
            folder.mkdir(exist_ok=True)
            _write_atomic(folder / f"{rid}.json", json.dumps(snap, indent=1, ensure_ascii=False).encode() + b"\n")
            entry = {
                "id": rid, "file": f"notes/{rid}.json", "hash": revisions.content_hash(snap),
                "parser": snap["parser"], "parent": self.revisions[-1]["id"] if self.revisions else None,
                "created": datetime.now().isoformat(timespec="seconds"), "provenance": provenance,
            }
            if ancestry:
                entry["ancestry"] = ancestry
            if note:
                entry["note"] = note
            self.revisions.append(entry)
            self._snapshots[rid] = snap
            self.save()  # the revision is on record as soon as it exists
        return rid

    @property
    def current_revision(self) -> str | None:
        return self.revisions[-1]["id"] if self.revisions else None

    def revision(self, rid: str) -> dict:
        for r in self.revisions:
            if r["id"] == rid:
                return r
        raise SessionError(f"{self.dir.name}: no notes revision {rid!r}")

    def snapshot(self, rid: str) -> dict:
        """A revision's snapshot, checked against the hash recorded for it."""
        if rid not in self._snapshots:
            entry = self.revision(rid)
            path = self.dir / entry["file"]
            if not path.exists():
                raise SessionError(f"{self.dir.name}: notes revision file {entry['file']} is missing")
            snap = json.loads(path.read_text())
            if revisions.content_hash(snap) != entry["hash"]:
                raise SessionError(f"{self.dir.name}: notes revision {rid} does not match its recorded hash "
                                   "(the file was changed or damaged)")
            self._snapshots[rid] = snap
        return self._snapshots[rid]

    def notes_for(self, take: TakeRecord) -> Notes:
        """The notes this take was recorded with. A take from before notes
        snapshots raises LegacyNotes: see rebind_legacy."""
        if take.revision is None:
            raise LegacyNotes(f"take {take.number} of {self.dir.name} was recorded before PalmCards kept a copy "
                              f"of the notes, and {self.notes} may have changed since. To use that file as it "
                              "is now (marked unverified), rebind: python -m palmcards.speech "
                              f"{self.dir} --rebind")
        return revisions.from_snapshot(self.snapshot(take.revision))

    def verified(self, take: TakeRecord) -> bool:
        return take.revision is not None and self.revision(take.revision)["provenance"] != "legacy-unverified"

    def current_notes_unverified(self) -> Notes:
        """Read-only fallback for old sessions: the notes file as it is now."""
        path = self.notes if self.notes.is_absolute() else Path.cwd() / self.notes
        if not path.exists():
            raise SessionError(f"the notes file {self.notes} of {self.dir.name} is gone; nothing to fall back on")
        return notes_from_bytes(path.read_bytes(), path)

    def sentence_map(self, take: TakeRecord) -> dict[int, int] | None:
        """The take's sentence positions -> the current revision's, by sentence
        id; None if the take has no revision (legacy)."""
        if take.revision is None or self.current_revision is None:
            return None
        if take.revision == self.current_revision:
            return {i: i for i in range(len(self.snapshot(take.revision)["sentences"]))}
        return revisions.index_map(self.snapshot(take.revision), self.snapshot(self.current_revision))

    def rebind_legacy(self, path: str | Path | None = None) -> str:
        """Give takes without a revision the notes file at `path` (default: where
        the session says they came from), saved as a revision marked
        "legacy-unverified". Explicit, as nothing proves it's what was rehearsed."""
        path = Path(path or self.notes).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise SessionError(f"cannot rebind {self.dir.name}: {path} does not exist")
        self.acquire()
        data = path.read_bytes()
        if self.source is None:
            self._store_source(data)
        rid = self.add_revision(notes_from_bytes(data, path), provenance="legacy-unverified",
                                note=f"{path} as of {datetime.now():%Y-%m-%d %H:%M}, bound to takes recorded "
                                     "before notes snapshots; may differ from what was rehearsed")
        for take in self.takes:
            if take.revision is None:
                take.revision = rid
        self.save()
        return rid

    # --- takes -------------------------------------------------------------

    def take(self, number: int) -> TakeRecord:
        for t in self.takes:
            if t.number == number:
                return t
        raise SessionError(f"{self.dir.name} has no take {number}")

    def _next_number(self) -> int:
        """One past every take and every take file on disk: never overwrite a WAV."""
        on_disk = [int(m.group(1)) for p in self.dir.glob("take-*") if (m := TAKE_FILE.match(p.name))]
        return max([t.number for t in self.takes] + on_disk + [0]) + 1

    def set_result(self, number: int, transcript: str, alignment: dict, verdicts: str | None = None,
                   marks: dict | None = None) -> None:
        """Record a take's transcript file, its alignment to the notes and its verdicts file."""
        take = self.take(number)
        take.transcript, take.alignment = transcript, alignment
        if verdicts is not None:
            take.verdicts, take.marks = verdicts, marks
        self.save()

    def add_take(self, audio: np.ndarray, rate: int, t_start: float, started: datetime,
                 sections: list[tuple[float, int]], drill: int | None = None) -> TakeRecord:
        """Save audio as the next take, bound to the current notes revision.
        `sections` is [(t into the take, section)]; `drill` is the sentence a
        drill take rehearsed."""
        self._ensure_written()
        number = self._next_number()
        wav = f"take-{number:02d}.wav"
        write_wav(self.dir / wav, audio, rate)
        take = TakeRecord(
            number=number,
            wav=wav,
            started=started.isoformat(timespec="milliseconds"),
            t_start=round(t_start, 3),
            duration_s=round(len(audio) / rate, 3),
            sample_rate=rate,
            peak=round(float(np.abs(audio).max()) if len(audio) else 0.0, 5),
            sections=[{"section": s, "t": round(t, 3)} for t, s in sections],
            drill=drill,
            revision=self.current_revision,
        )
        self.takes.append(take)
        self.save()
        return take

    def begin_take(self) -> int:
        """Get ready to record the next take: the folder, notes and session.json
        exist (so an interrupted take can be recovered). Returns its number."""
        self._ensure_written()
        self.save()
        return self._next_number()

    def finish_take(self, manifest: dict, status: str | None = None, recovered: bool = False) -> TakeRecord:
        """Add a take written by palmcards.recording.TakeWriter, from its
        manifest, and delete the manifest. Times: t_start is the first
        sample's app time; section times become seconds into the take."""
        status = status or ("failed" if manifest["state"] == "failed" else "saved")
        if status not in STATUSES:
            raise ValueError(f"unknown take status {status!r}")
        rate = manifest["rate"]
        t_start = manifest["first_sample_t"]
        if t_start is None:  # no audio arrived: the moment recording was asked for
            t_start = manifest.get("requested_t", 0.0)
        sections = [{"section": e[1], "t": round(max(0.0, e[0] - t_start), 3), "source": e[2] if len(e) > 2 else "start"}
                    for e in manifest["sections"]]
        gaps = manifest["discontinuities"]
        capture = {
            "clock": manifest["clock"],
            "dropped_samples": sum(g["samples"] or 0 for g in gaps),
            "discontinuities": [{"at_s": round(g["at"] / rate, 3), "samples": g["samples"], "why": g["why"]}
                                for g in gaps],
        }
        if manifest.get("error"):
            capture["error"] = manifest["error"]
        if recovered:
            capture["recovered"] = True
        take = TakeRecord(
            number=manifest["take"], wav=manifest["wav"], started=manifest["started"], t_start=round(t_start, 3),
            duration_s=round(manifest["samples"] / rate, 3), sample_rate=rate, peak=manifest["peak"],
            sections=sections, drill=manifest.get("drill"), revision=manifest.get("revision"),
            status=status, capture=capture, live=manifest.get("live"),
        )
        self.takes = [t for t in self.takes if t.number != take.number] + [take]
        self.takes.sort(key=lambda t: t.number)
        self.save()
        (self.dir / f"take-{take.number:02d}.recording.json").unlink(missing_ok=True)
        return take

    def recover(self) -> list[TakeRecord]:
        """Takes whose recording never finished (the app stopped or crashed
        mid-take): salvage the audio written so far as "interrupted" takes."""
        from palmcards.recording import repair_wav

        out = []
        for path in sorted(self.dir.glob("take-*.recording.json")):
            manifest = json.loads(path.read_text())
            if any(t.number == manifest["take"] for t in self.takes):
                path.unlink()  # finished, only the manifest's removal was missed
                continue
            part, wav = self.dir / manifest["part"], self.dir / manifest["wav"]
            if part.exists():
                manifest["samples"] = repair_wav(part)
                part.replace(wav)
            if not wav.exists() or manifest["samples"] == 0:
                path.replace(path.with_name(path.name + ".empty"))  # nothing to salvage; keep the evidence
                continue
            audio, _ = read_wav(wav)
            manifest["peak"] = round(float(np.abs(audio).max()) if len(audio) else 0.0, 5)
            finished = manifest["state"] in ("saved", "failed")
            status = ("failed" if manifest["state"] == "failed" else "saved") if finished else "interrupted"
            out.append(self.finish_take(manifest, status, recovered=True))
        return out

    def save(self) -> None:
        self.acquire()
        path = self.dir / "session.json"
        if self._loaded_schema < SCHEMA and path.exists() and not (self.dir / "session.v1.json").exists():
            shutil.copy2(path, self.dir / "session.v1.json")  # the old file, before its first rewrite
        data = {
            "schema": SCHEMA,
            "id": self.id,
            "notes": str(self.notes),
            "source": self.source,
            "revisions": self.revisions,
            "gesture_log": self.gesture_log,
            "language": self.language,
            # Fields a take doesn't have yet are left out, not written as null.
            "takes": [{k: v for k, v in asdict(t).items() if v is not None} for t in self.takes],
        }
        _write_atomic(path, (json.dumps(data, indent=2) + "\n").encode())
        self._loaded_schema = SCHEMA


def recover_all(root: Path = SESSIONS_DIR) -> list[str]:
    """At startup: salvage takes left mid-recording in any session under
    `root`. A session open in another running PalmCards is left alone.
    Returns a line per session touched, for the terminal."""
    lines = []
    for folder in sorted({p.parent for p in root.glob("*/take-*.recording.json")}):
        try:
            session = Session.load(folder)
            session.acquire()
        except SessionBusy:
            continue
        except SessionError as exc:
            lines.append(f"{folder.name}: could not recover: {exc}")
            continue
        try:
            for take in session.recover():
                lines.append(f"{folder.name}: take {take.number} recovered ({take.duration_s:.1f} s, {take.status})")
        finally:
            session.release()
    return lines


def write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    """Mono float audio (-1..1) as 16-bit PCM, published atomically: a crash
    leaves either no file or a *.tmp, never a truncated take."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).round().astype("<i2")
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())
    tmp.replace(path)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV -> (float32 audio in -1..1, sample rate); mono or first channel."""
    with wave.open(str(path), "rb") as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        channels, rate = f.getnchannels(), f.getframerate()
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    return pcm[::channels].astype(np.float32) / 32767, rate
