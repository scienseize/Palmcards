"""Notes revisions: the exact notes a take was rehearsed against.

A revision is a normalised snapshot of parsed notes (sections, sentences,
words) saved inside the session folder, so a session's
history never depends on the imported file staying where it was, unchanged.
Loading a snapshot rebuilds the Notes as they were; it never re-parses, so a
later parser change can't alter an old take either.

Identity. Every sentence gets an id when it first appears ("s1", "s2", ...);
its words are "<sentence id>.w<k>".
List positions are not identities: a later revision (edits, milestone 8)
keeps the ids of sentences whose text is unchanged, even if they moved, and
gives edited or new sentences fresh ids, recording which old sentences they
replace (`ancestry`). An edited sentence's words get new ids with it, as
their positions may no longer mean the same words.

    snap = to_snapshot(notes)                  # first import
    snap2, ancestry = to_snapshot(edited, previous=snap)
    notes = from_snapshot(snap)
    content_hash(snap)   # what the notes say, ignoring ids
    revision_id(snap)    # the snapshot including its ids

Snapshots made by parser 1 also hold delivery marks ("marks" per sentence,
"<sentence id>.m<k>", and a "stressed" flag per word). They load as they
are, their hash checked on what they hold, and the marks are left out of
the Notes: PalmCards no longer uses them (2026-09-26).
"""

from __future__ import annotations

import difflib
import hashlib
import json

from palmcards.notes import PARSER_VERSION, Notes, Section, Sentence, Word

SNAPSHOT_VERSION = 1
ANCESTOR_SIM = 0.6  # an edited sentence at least this alike (difflib ratio) replaces the old one


def _canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _content(snap: dict) -> dict:
    """The snapshot without ids: what the notes say."""
    def bare(items: list[dict]) -> list[dict]:
        return [{k: v for k, v in item.items() if k != "id"} for item in items]

    return {
        "sections": snap["sections"],
        "sentences": [{k: v for k, v in s.items() if k != "id"} | {"words": bare(s["words"])}
                      | ({"marks": bare(s["marks"])} if "marks" in s else {})  # parser 1
                      for s in snap["sentences"]],
    }


def content_hash(snap: dict) -> str:
    return hashlib.sha256(_canonical(_content(snap))).hexdigest()


def revision_id(snap: dict) -> str:
    """Stable id of a snapshot, ids included: equal ids mean identical revisions."""
    return "r" + hashlib.sha256(_canonical(snap)).hexdigest()[:12]


def _sentence(s: Sentence, sid: str) -> dict:
    return {
        "id": sid,
        "section": s.section,
        "paragraph": s.paragraph,
        "raw": s.raw,
        "text": s.text,
        "words": [{"id": f"{sid}.w{k}", "text": w.text, "norm": w.norm, "start": w.start, "end": w.end}
                  for k, w in enumerate(s.words)],
    }


def carry_ids(previous: dict, notes: Notes, next_id: int | None = None) -> tuple[list[str], dict[str, list[str]], int]:
    """Sentence ids for `notes`, keeping those of sentences unchanged since
    `previous` (the same text as written), however they moved. New ids start at
    `next_id` (a session passes one past every id it has ever given out, so
    a branch after an undo never reuses an undone revision's ids). Returns
    (ids, ancestry: new id -> the old ids it replaces, next id number)."""
    old = previous["sentences"]
    new = notes.sentences
    next_id = max(previous.get("next_id", len(old) + 1), next_id or 0)
    ids: list[str | None] = [None] * len(new)
    ancestry: dict[str, list[str]] = {}
    matcher = difflib.SequenceMatcher(a=[s["raw"] for s in old], b=[s.raw for s in new], autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            for k in range(b1 - b0):
                ids[b0 + k] = old[a0 + k]["id"]
            continue
        # In a changed stretch, a new sentence descends from the old one it
        # most resembles (at least ANCESTOR_SIM alike), each old one used once.
        pairs = sorted(((difflib.SequenceMatcher(a=old[i]["raw"], b=new[j].raw, autojunk=False).ratio(), i, j)
                        for i in range(a0, a1) for j in range(b0, b1)), reverse=True)
        parent: dict[int, int] = {}
        for sim, i, j in pairs:
            if sim >= ANCESTOR_SIM and j not in parent and i not in parent.values():
                parent[j] = i
        for j in range(b0, b1):
            ids[j] = f"s{next_id}"
            next_id += 1
            if j in parent:
                ancestry[ids[j]] = [old[parent[j]]["id"]]
    return ids, ancestry, next_id


def to_snapshot(notes: Notes, previous: dict | None = None,
                next_id: int | None = None) -> tuple[dict, dict[str, list[str]]]:
    """Snapshot of `notes`; with `previous`, unchanged sentences keep their ids.
    Returns (snapshot, ancestry)."""
    if previous is None:
        ids = [f"s{k + 1}" for k in range(len(notes.sentences))]
        ancestry, next_id = {}, len(ids) + 1
    else:
        ids, ancestry, next_id = carry_ids(previous, notes, next_id)
    snap = {
        "snapshot": SNAPSHOT_VERSION,
        "parser": PARSER_VERSION,
        "sections": [{"title": sec.title} for sec in notes.sections],
        "sentences": [_sentence(s, sid) for s, sid in zip(notes.sentences, ids)],
        "next_id": next_id,
        "warnings": list(notes.warnings),
    }
    return snap, ancestry


def from_snapshot(snap: dict) -> Notes:
    """The Notes a snapshot was made from (no re-parsing)."""
    if snap.get("snapshot") != SNAPSHOT_VERSION:
        raise ValueError(f"unsupported notes snapshot version {snap.get('snapshot')!r} "
                         f"(this PalmCards reads version {SNAPSHOT_VERSION})")
    sections = [Section(sec["title"]) for sec in snap["sections"]]
    for i, s in enumerate(snap["sentences"]):
        sentence = Sentence(
            raw=s["raw"], text=s["text"],
            words=[Word(w["text"], w["norm"], w["start"], w["end"]) for w in s["words"]],
            section=s["section"], paragraph=s["paragraph"], index=i,
        )
        sections[s["section"]].sentences.append(sentence)
    return Notes(sections=sections, warnings=list(snap.get("warnings", [])))


def index_map(old: dict, new: dict) -> dict[int, int]:
    """Sentence positions in `old` -> positions in `new`, for sentences with
    the same id (unchanged since). Edited or removed sentences have no entry."""
    pos = {s["id"]: i for i, s in enumerate(new["sentences"])}
    return {i: pos[s["id"]] for i, s in enumerate(old["sentences"]) if s["id"] in pos}


def sentence_ids(snap: dict) -> list[str]:
    return [s["id"] for s in snap["sentences"]]
