"""The home card's rows (palmcards.home): pure logic, no camera."""

import json
from pathlib import Path

from palmcards.home import Home, Next, entries, recent, row_at


def make_session(root: Path, name: str, notes: Path, takes: int, current="r1") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({"notes": str(notes), "current": current,
                                                     "takes": [{}] * takes}))
    return folder


def test_rows_continue_start_over_others_newest_first_then_a_file(tmp_path):
    talk, pitch, toast = (tmp_path / n for n in ("talk.md", "pitch.txt", "toast.txt"))
    talk.write_text("Hello.")
    root = tmp_path / "sessions"
    make_session(root, "20261101-100000-pitch-aaaaaa", pitch, 1)
    newer_pitch = make_session(root, "20261120-100000-pitch-bbbbbb", pitch, 4)
    toast_s = make_session(root, "20261110-100000-toast-cccccc", toast, 0)
    make_session(root, "20261115-100000-old-dddddd", tmp_path / "old.txt", 2, current=None)  # notes never kept
    here = make_session(root, "20261126-090000-talk-eeeeee", talk, 3)
    rows = entries(root, here, talk, 3)
    assert [r.title for r in rows] == ["Continue talk.md", "Start talk.md over", "pitch.txt", "toast.txt",
                                       "Add slides…", "Open another file…"]
    assert rows[0].detail == "3 takes · since 26 November" and rows[0].next is None
    assert rows[1].next == Next(path=talk, new=True)
    assert rows[2].next == Next(folder=newer_pitch) and rows[2].detail == "4 takes · last 20 November"
    assert rows[3].next == Next(folder=toast_s) and rows[3].detail.startswith("No takes yet")
    assert rows[4].slides and rows[5].pick and rows[5].next is None


def test_a_missing_file_cannot_start_over_and_others_are_capped(tmp_path):
    root = tmp_path / "sessions"
    for i in range(6):
        make_session(root, f"2026110{i}-100000-s{i}-aaaaaa", tmp_path / f"s{i}.txt", 1)
    here = make_session(root, "20261126-090000-gone-eeeeee", tmp_path / "gone.txt", 0)
    rows = entries(root, here, tmp_path / "gone.txt", 0)
    assert [r.title for r in rows][:2] == ["Continue gone.txt", "s5.txt"]
    assert len(recent(root, tmp_path / "gone.txt")) == 4


def test_the_hand_box_height_is_shared_by_the_rows():
    assert row_at(0.0, 4) == 0 and row_at(1.0, 4) == 3
    assert [row_at(v, 4) for v in (0.2, 0.4, 0.6, 0.8)] == [0, 1, 2, 3]


def test_keys_move_and_choose_within_the_rows(tmp_path):
    root = tmp_path / "sessions"
    here = make_session(root, "20261126-090000-talk-eeeeee", tmp_path / "talk.md", 0)
    home = Home(entries(root, here, tmp_path / "talk.md", 0), (1280, 720))
    home.step(-1)
    assert home.picked == 0
    home.step(5)
    assert home.picked == len(home.rows) - 1
    assert home.choose(9) is None and home.choose(0).title == "Continue talk.md"


def test_the_slides_row_says_what_is_attached_and_a_pdf_speech_has_none(tmp_path):
    root = tmp_path / "sessions"
    here = make_session(root, "20261126-090000-talk-eeeeee", tmp_path / "talk.md", 0)
    titles = lambda slides, notes="talk.md": [r.title for r in entries(root, here, tmp_path / notes, 0, slides)
                                              if r.slides]
    assert titles(None) == ["Add slides…"]
    assert titles(("deck.pdf", 12)) == ["Slides: deck.pdf"]
    assert titles(("deck.pdf", None)) == ["Slides missing: deck.pdf"]
    assert titles(None, "deck.pdf") == []  # a PDF speech is its own slides
