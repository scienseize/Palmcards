from palmcards import metrics as metrics_mod
from palmcards.align import align
from palmcards.notes import parse_text
from palmcards.review import Board
from tests.synth import speak

TEXT = "Good evening everyone. / Thank you for being here. [slow] We are so glad you came tonight."


def analysed(script: str, notes=None):
    """A take's alignment from a script."""
    notes = notes or parse_text(TEXT)
    return align([[w.norm for w in s.words] for s in notes.sentences], speak(script))


FULL = "Good evening everyone. <0.1> Thank you for being here. <0.4> We are so glad you came tonight."


def test_each_sentence_shows_its_latest_take_until_another_is_picked():
    b = Board(parse_text(TEXT))
    assert b.shown(0) is None and b.detail(0) == ("No take analysed yet.",)
    b.add(1, analysed(FULL))
    b.add(2, analysed("Good evening everyone. <0.5> Thank you for being here."))
    b.add(3, analysed("Thank you for being here."), drill=1)
    assert [b.shown(i) for i in range(3)] == [2, 3, 1]  # latest take that said each sentence
    assert b.said_in(1) == [1, 2, 3]
    assert b.take_label(1) == "TAKE 3 (DRILL)  3 OF 3"
    b.picked[1] = 2  # pointed at take 2's chip
    assert b.shown(1) == 2 and b.take_label(1) == "TAKE 2  2 OF 3"
    # A newer take that says the sentence again shows instead of the pick.
    b.add(4, analysed(FULL))
    assert b.shown(1) == 4


def test_a_focused_sentence_lists_every_take_that_said_it():
    b = Board(parse_text(TEXT))
    b.add(1, analysed("Good evening everyone. <0.1> Thank you for being here. um <0.4> We are so glad you came "
                      "tonight."))
    b.add(2, analysed(FULL))
    b.add(3, analysed("Thank you for being here."), drill=1)
    lines = b.detail(1)
    assert len(lines) == 3 and lines[2].startswith("▸ Take 3 (drill): ")  # the take it shows, marked
    assert lines[0].startswith("  Take 1: ") and "wpm" in lines[0] and "no fillers" in lines[0]
    assert "1 filler" in b.detail(2)[0]  # the "um" before it: its hesitation
    assert b.detail(2)[1] == "▸ " + b._sentence_line(2, 2)


def test_a_sentence_not_said_in_any_take():
    b = Board(parse_text(TEXT))
    b.add(1, analysed("Good evening everyone."))
    assert b.shown(2) == 1 and b.take_label(2) == "TAKE 1: NOT SAID"
    assert b.detail(2) == ("Not said in any take yet.",) and b.said_in(2) == []


def metrics(sentences, pace=142.4, fillers=1.5, pitch=6.2, posture=True, touches=2):
    """Take metrics with gaze counts per sentence: [(screen, away, unclear)]."""
    per = [{"sentence": i, "screen": s, "away": a, "unclear": u, "camera": s, "notes": 0}
           for i, (s, a, u) in enumerate(sentences)]
    counts = {"camera": sum(s for s, _, _ in sentences), "notes": 0, "away": sum(a for _, a, _ in sentences),
              "unclear": sum(u for _, _, u in sentences)}
    return {
        "version": 4,
        "speech": {"pace_wpm": {"value": pace}, "fillers_per_min": {"value": fillers},
                   "unplanned_long_pauses": {"value": 1}, "restarts": {"value": 0}},
        "voice": {"pitch_range_st": {"value": pitch},
                  "sentences": [{"sentence": i, "pitch_range_st": 4.5, "voiced_s": 1.0} for i in range(len(sentences))]},
        "hands": {"in_view_share": {"value": 0.15}, "face_touches": {"value": touches, "seconds": 1.2}},
        "gaze": {"screen_share": {"value": 0.8}, "away_share": {"value": 0.2},
                 "unclear_share": {"value": 0.1}, "counts": counts, "sentences": per},
        "posture": {"tilted_share": {"value": 0.13}, "head_dropped_share": {"value": 0.08}} if posture
        else {"value": None, "reason": "no posture baseline"},
    }


def test_the_sentence_lines_carry_pitch_and_gaze_when_measured():
    b = Board(parse_text(TEXT))
    b.add(1, analysed(FULL), metrics([(8, 2, 0), (9, 0, 1), (1, 1, 0)]))
    assert b.detail(0)[0].endswith("pitch range 4.5 st, on screen 80%")
    assert b.detail(1)[0].endswith("on screen 90%")  # a share of every reading, unclear included
    assert "on screen" not in b.detail(2)[0]  # too few readings (2)
    b.add(2, analysed(FULL), {"version": 4, "gaze": {"value": None, "reason": "no calibration"}})
    assert "on screen" not in b.detail(0)[1] and "pitch" not in b.detail(0)[1]


def test_old_takes_follow_the_sentences_of_an_older_notes_revision():
    b = Board(parse_text(TEXT))
    b.add(1, analysed(FULL), metrics([(8, 2, 0), (5, 5, 0), (3, 0, 0)]), sentence_map={0: 0, 2: 2})
    assert b.detail(0)[0].endswith("on screen 80%") and b.detail(2)[0].endswith("on screen 100%")
    assert b.said_in(1) == []  # edited since: left out


def test_takes_measured_before_the_per_sentence_metrics_get_them_from_their_alignment():
    b = Board(parse_text(TEXT))
    al = analysed(FULL)
    old = {"version": 3, "speech": {"pace_wpm": {"value": 150.0}}}
    b.add(1, al, old)
    assert b.said_in(0) == [1] and b.rows[1][2]["wpm"] == metrics_mod.sentence_speech(al)[2]["wpm"]


def test_the_take_table_sets_the_last_full_takes_side_by_side():
    b = Board(parse_text(TEXT))
    assert b.take_table() == ()
    b.add(1, analysed(FULL), metrics([(8, 2, 0), (9, 0, 1), (1, 1, 0)], posture=False, touches=1), duration_s=61.0)
    b.add(2, analysed(FULL), metrics([(5, 5, 0)] * 3, pace=131.0, fillers=0.5, pitch=8.0), duration_s=58.4)
    b.add(3, analysed("Thank you for being here."), drill=1)  # a drill: compared in its sentence's panel
    b.add(4, analysed(FULL), duration_s=40.0)  # no metrics yet
    table = b.take_table()
    assert len({len(line) for line in table}) == 1  # every line the same width: the columns line up
    rows = {line.split("  ")[0].strip(): line.split() for line in table[1:]}
    assert table[0].split() == ["TAKE", "1", "TAKE", "2", "TAKE", "4"]
    assert rows["LENGTH"][-3:] == ["1:01", "0:58", "0:40"]
    assert rows["WPM"][-3:] == ["142", "131", "-"]
    assert rows["FILLERS/MIN"][-3:] == ["1.5", "0.5", "-"]
    assert rows["PITCH RANGE ST"][-3:] == ["6.2", "8", "-"]
    assert rows["ON SCREEN"][-3:] == ["82%", "50%", "-"]
    assert rows["FACE TOUCHES"][-3:] == ["1", "2", "-"]
    assert rows["SHOULDERS TILTED"][-3:] == ["-", "13%", "-"]
    for n in (5, 6):
        b.add(n, analysed(FULL))
    assert b.take_table()[0].split()[1::2] == ["2", "4", "5", "6"]  # the last four


def test_review_points_at_take_chips_and_says_when_there_is_nothing_to_choose(tmp_path, monkeypatch):
    from palmcards.gestures import Grammar
    from palmcards.render import Hit, TextOverlay, ViewState
    from tests.test_llm import takes_with

    notes = parse_text(TEXT)
    takes = takes_with(tmp_path, monkeypatch, None)
    takes.board = b = Board(notes)
    b.add(1, analysed(FULL))
    b.add(2, analysed("Good evening everyone. <0.5> Thank you for being here."))
    b.add(3, analysed("Thank you for being here."), drill=1)
    ov = TextOverlay(notes.sentences, (1280, 720))
    g = Grammar((1280, 720))
    g.state.mode, g.state.level = "focus", "sentence"
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None))

    takes.sync_review(g, view, ov, 0.0)
    assert view.takes == ("TAKE 1", "TAKE 2", "TAKE 3 (DRILL)") and view.take_shown == 2  # the latest said it
    assert takes.status("review", view) == "TAKE 3 (DRILL)  3 OF 3" and view.playable  # the hints are render's
    assert len(view.detail) == 3 and view.detail[2].startswith("▸ ")
    g.state.op, g.state.point = "take", (0.0, 0.0)  # an L: pointing starts on the take shown
    takes.sync_review(g, view, ov, 0.1)
    assert b.shown(1) == 3
    top = ov.take_points(view)[0][1]
    g.state.point = (0.0, (top - ov.take_points(view)[2][1]) / ov.box_h)  # up to the first chip
    takes.sync_review(g, view, ov, 0.2)
    assert view.take_shown == 0 and b.shown(1) == 1 and view.detail[0].startswith("▸ Take 1")
    g.state.op, g.state.point = None, None

    view.focus = Hit(2, None)  # said only in take 1
    takes.sync_review(g, view, ov, 0.3)
    assert view.takes == ("TAKE 1",) and takes.status("review", view).startswith("ONLY TAKE 1 SAID THIS SENTENCE")
    b2 = Board(notes)
    b2.add(1, analysed("Good evening everyone."))
    takes.board = b2
    takes.sync_review(g, view, ov, 0.4)
    assert view.takes == () and takes.status("review", view).startswith("NO TAKE SAID THIS SENTENCE")
    assert not view.playable

    view.focus, view.mode = None, "browse"  # browsing: the take table
    takes.sync_review(g, view, ov, 0.5)
    assert view.summary == b2.take_table() and view.summary[0].split() == ["TAKE", "1"]


def test_a_focused_paragraph_sums_up_the_newest_full_take_that_said_it():
    b = Board(parse_text(TEXT))
    whole = [0, 1, 2]  # TEXT is one paragraph
    assert b.paragraph_shown(whole) is None and b.paragraph_detail(whole) == ("No take analysed yet.",)
    b.add(1, analysed("Good evening everyone. <0.1> Thank you for being here. um <0.4> We are so glad you came "
                      "tonight."))
    b.add(2, analysed("Good evening everyone. <0.5> Thank you for being here."))
    b.add(3, analysed("Thank you for being here."), drill=1)
    assert b.paragraph_shown(whole) == 2  # the drill has one sentence: not the paragraph's take
    said, took, pace = b.paragraph_detail(whole)
    assert said == "Take 2: 2 of 3 sentences said"
    assert took.startswith("0:0") and took.endswith(" from first word to last")
    assert pace.split(", ")[0].endswith(" wpm") and pace.endswith(", no fillers")
    b.picked[0] = 1  # a sentence's pick doesn't move the paragraph's take
    assert b.paragraph_shown(whole) == 2
    assert b.paragraph_detail([0])[0] == "Take 2: 1 of 1 sentence said"  # a one-sentence paragraph
    assert b.paragraph_detail([0])[2] == "pace -, no fillers"  # 3 words: too few for a pace
    b.add(4, analysed("Good evening everyone. um <0.1> Thank you for being here. <0.4> We are so glad you came "
                      "tonight."))
    assert b.paragraph_detail(whole)[0] == "Take 4: 3 of 3 sentences said"
    assert b.paragraph_detail(whole)[2].endswith(", 1 filler")


def test_a_paragraph_nobody_said():
    b = Board(parse_text(TEXT))
    b.add(1, analysed("Good evening everyone."))
    assert b.paragraph_shown([1, 2]) is None
    assert b.paragraph_detail([1, 2]) == ("Not said in any take yet.",)
