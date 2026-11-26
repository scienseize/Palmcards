"""Progress over days and readiness (palmcards.progress), on made-up points
and on sessions written to a temporary folder."""

from datetime import datetime, timedelta

from palmcards.progress import Point, days_table, history, readiness

DAY1, DAY2 = datetime(2026, 11, 26, 10), datetime(2026, 11, 27, 10)


def take(when, n, length=180.0, points=1.0, fillers=1.0, answer=False):
    return Point(when, "s", n, length, answer, None if answer else points, fillers, 0, None)


def test_not_ready_until_three_full_takes():
    assert readiness([take(DAY1, 1)]) == "Not ready yet: 3 good full takes in a row are the test (1 so far)"


def test_readiness_names_the_first_thing_missing():
    good = [take(DAY1 + timedelta(minutes=i), i) for i in range(3)]
    assert readiness(good, target_s=180) == "Ready: the last 3 takes on time, with the key points"
    late = good[:2] + [take(DAY1 + timedelta(minutes=9), 9, length=230)]
    assert readiness(late, target_s=180) == "Not ready yet: on time in 2 of the last 3 takes"
    missed = good[:2] + [take(DAY1 + timedelta(minutes=9), 9, points=0.7)]
    assert readiness(missed) == "Not ready yet: 90% of the key points in 2 of the last 3 takes"
    filler = good[:2] + [take(DAY1 + timedelta(minutes=9), 9, fillers=5.5)]
    assert readiness(filler) == "Not ready yet: 5.5 fillers a minute in a recent take"


def test_the_last_qa_round_counts_when_questions_were_practised():
    good = [take(DAY1 + timedelta(minutes=i), i) for i in range(3)]
    slow = good + [take(DAY1 + timedelta(minutes=5), 5, length=84, answer=True)]
    assert readiness(slow) == "Not ready yet: an answer ran 1:24, over 1:00"
    fixed = slow + [take(DAY1 + timedelta(minutes=8), 8), take(DAY1 + timedelta(minutes=9), 9, length=40, answer=True)]
    assert readiness(fixed) == "Ready: the last 3 takes, with the key points, answers under 1:00"


def test_days_are_set_side_by_side():
    pts = [take(DAY1, 1, length=200, points=0.8, fillers=4), take(DAY1 + timedelta(hours=1), 2, length=185, fillers=2),
           take(DAY2, 3, length=178, points=1.0, fillers=1), take(DAY2 + timedelta(minutes=2), 4, 50, answer=True)]
    lines = days_table(pts, target_s=180)
    assert lines[0].split() == ["26", "Nov", "27", "Nov"]
    rows = {line[:18].strip(): line[18:].split() for line in lines[1:]}
    assert rows["Takes"] == ["2", "1"]
    assert rows["Length (3:00)"] == ["3:05", "2:58"]  # the take nearest the target each day
    assert rows["Key points"] == ["100%", "100%"] and rows["Fillers a minute"] == ["2", "1"]
    assert rows["Answers"] == ["-", "0:50"]
    assert days_table([]) == ()


def test_history_reads_every_session_of_the_same_notes(tmp_path):
    import json

    def write(name, notes_stem, takes):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "session.json").write_text(json.dumps({"schema": 3, "notes": f"/x/{notes_stem}.md", "takes": takes}))
        return folder

    alignment = {"sentences": [{"status": "spoken"}, {"status": "skipped"}]}
    t = {"number": 1, "wav": "take-01.wav", "started": "2026-11-26T10:00:00", "t_start": 0.0, "duration_s": 90.0,
         "sample_rate": 16000, "peak": 0.5, "alignment": alignment}
    write("20261126-100000-pitch-aaaaaa", "pitch", [t])
    skip = write("20261127-100000-pitch-bbbbbb", "pitch", [{**t, "started": "2026-11-27T10:00:00"}])
    write("20261127-110000-other-cccccc", "other", [t])
    got = history(tmp_path, "pitch", skip=skip)
    assert [(p.session, p.number) for p in got] == [("20261126-100000-pitch-aaaaaa", 1)]
    assert got[0].key_points == 0.5  # from the alignment, before metrics v6
