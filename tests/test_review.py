from palmcards.align import align
from palmcards.cues import verdicts
from palmcards.notes import parse_text
from palmcards.review import Board
from tests.test_cues import speak

TEXT = "Good evening everyone. / Thank you for being here. [slow] We are so glad you came tonight."


def judged(script: str, notes=None):
    notes = notes or parse_text(TEXT)
    words = speak(script)
    alignment = align([[w.norm for w in s.words] for s in notes.sentences], words)
    return verdicts([[[m.kind, m.word] for m in s.marks] for s in notes.sentences], alignment, words)


FULL = "Good evening everyone. <0.1> Thank you for being here. <0.4> We are so glad you came tonight."


def test_each_sentence_shows_its_latest_take_and_the_dial_moves_through_them():
    b = Board(parse_text(TEXT))
    assert b.shown(0) is None and b.detail(0) == (("", "No take judged yet."),)
    b.add(1, judged(FULL))
    b.add(2, judged("Good evening everyone. <0.5> Thank you for being here."))
    b.add(3, judged("Thank you for being here.", ), drill=1)
    assert [b.shown(i) for i in range(3)] == [2, 3, 1]  # latest take that said each sentence
    assert b.said_in(1) == [1, 2, 3]
    assert b.take_label(1) == "TAKE 3 (DRILL)  3 OF 3"

    b.step(1, -1)
    assert b.shown(1) == 2 and b.take_label(1) == "TAKE 2  2 OF 3"
    b.step(1, -5)  # clamped
    assert b.shown(1) == 1
    b.step(2, 1)  # only one take said sentence 2
    assert b.shown(2) == 1

    # A newer take that says the sentence again shows instead of the pick.
    b.add(4, judged(FULL))
    assert b.shown(1) == 4


def test_mark_verdicts_and_detail_lines():
    b = Board(parse_text(TEXT))
    b.add(1, judged("Good evening everyone. <0.1> Thank you for being here. um <0.4> We are so glad you came tonight."))
    assert b.mark_verdicts() == ((), ("missed",), ("missed",))  # [slow] said at the usual pace
    lines = b.detail(1)
    assert lines[0] == ("missed", '/ before "Thank"  MISSED  no pause, 0.10 s (needs 0.30)')
    assert lines[1][1].startswith("Pace ") and lines[-1] == ("", "No fillers.")
    assert b.detail(2)[-1] == ("", "Fillers: um")  # before the sentence: its hesitation
    assert b.summary(1) == "0 HIT, 2 MISSED"


def test_a_sentence_not_said_in_any_take():
    b = Board(parse_text(TEXT))
    b.add(1, judged("Good evening everyone."))
    assert b.shown(2) == 1 and b.take_label(2) == "TAKE 1: NOT SAID"
    assert b.detail(2)[-1] == ("", "Not said in this take.")
    assert b.mark_verdicts()[2] == ("skipped",)


def metrics(sentences, screen_share=0.8, posture=True, touches=2):
    """Take metrics with gaze counts per sentence: [(screen, away, unclear)]."""
    per = [{"sentence": i, "screen": s, "away": a, "unclear": u, "camera": s, "notes": 0}
           for i, (s, a, u) in enumerate(sentences)]
    counts = {"camera": sum(s for s, _, _ in sentences), "notes": 0, "away": sum(a for _, a, _ in sentences),
              "unclear": sum(u for _, _, u in sentences)}
    return {
        "version": 3,
        "speech": {"pace_wpm": {"value": 142.4}, "fillers_per_min": {"value": 1.5}},
        "hands": {"in_view_share": {"value": 0.15}, "face_touches": {"value": touches, "seconds": 1.2}},
        "gaze": {"screen_share": {"value": screen_share}, "away_share": {"value": 1 - screen_share},
                 "unclear_share": {"value": 0.1}, "counts": counts, "sentences": per},
        "posture": {"tilted_share": {"value": 0.13}, "head_dropped_share": {"value": 0.08}} if posture
        else {"value": None, "reason": "no posture baseline"},
    }


def test_the_focused_sentence_says_where_the_speaker_looked():
    b = Board(parse_text(TEXT))
    b.add(1, judged(FULL), metrics=metrics([(8, 2, 0), (9, 0, 1), (1, 1, 0)]))
    assert b.detail(0)[-1] == ("", "On screen 80%, away 20%.")
    assert b.detail(1)[-1] == ("", "On screen 90%, away 0%, unclear 10%.")  # shares of every reading, adding to 100
    assert b.detail(2)[-1] == ("", "Gaze: too few readings (2).")
    b.add(2, judged(FULL), metrics={"version": 3, "gaze": {"value": None, "reason": "no calibration"}})
    assert b.detail(0)[-1] == ("", "Gaze not measured.")
    b.add(3, judged(FULL))  # analysed without metrics: no gaze line at all
    assert b.detail(0)[-1][1].endswith("fillers.") or b.detail(0)[-1][1].startswith("Fillers")


def test_gaze_follows_the_sentences_of_an_older_notes_revision():
    b = Board(parse_text(TEXT))
    b.add(1, judged(FULL), sentence_map={0: 0, 2: 2}, metrics=metrics([(8, 2, 0), (5, 5, 0), (3, 0, 0)]))
    assert b.detail(0)[-1] == ("", "On screen 80%, away 20%.")
    assert b.detail(2)[-1] == ("", "On screen 100%, away 0%.")


def test_the_take_summary_card():
    b = Board(parse_text(TEXT))
    assert b.take_summary(1) == ()
    b.add(1, judged(FULL), metrics=metrics([(8, 2, 0), (9, 0, 1), (1, 1, 0)], posture=False, touches=1))
    card = b.take_summary(1, 61.0)
    assert card[0] == "TAKE 1  1:01" and card[1] == b.summary(1)
    assert card[2:] == ("142 WPM  1.5 FILLERS/MIN", "ON SCREEN 82%  AWAY 14%  UNCLEAR 5%",
                        "HANDS IN VIEW 15%  1 FACE TOUCH", "POSTURE NOT MEASURED")
    b.add(2, judged(FULL), drill=1)
    assert b.latest() == 2 and b.take_summary(2)[-1] == "METRICS NOT MEASURED YET"
    assert b.take_summary(2)[0] == "TAKE 2 (DRILL)"


def test_review_points_at_take_chips_and_says_when_there_is_nothing_to_choose(tmp_path, monkeypatch):
    from palmcards.gestures import Grammar
    from palmcards.render import Hit, TextOverlay, ViewState
    from tests.test_llm import takes_with

    notes = parse_text(TEXT)
    takes = takes_with(tmp_path, monkeypatch, None)
    takes.board = b = Board(notes)
    b.add(1, judged(FULL))
    b.add(2, judged("Good evening everyone. <0.5> Thank you for being here."))
    b.add(3, judged("Thank you for being here.", ), drill=1)
    ov = TextOverlay(notes.sentences, (1280, 720))
    g = Grammar((1280, 720))
    g.state.mode, g.state.level = "focus", "sentence"
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None))

    takes.sync_review(g, view, ov, 0.0)
    assert view.takes == ("TAKE 1", "TAKE 2", "TAKE 3 (DRILL)") and view.take_shown == 2  # the latest said it
    assert "L-HAND, THEN POINT: TAKES" in takes.status("review", view)
    g.state.op, g.state.point = "take", (0.0, 0.0)  # an L: pointing starts on the take shown
    takes.sync_review(g, view, ov, 0.1)
    assert b.shown(1) == 3
    top = ov.take_points(view)[0][1]
    g.state.point = (0.0, (top - ov.take_points(view)[2][1]) / ov.box_h)  # up to the first chip
    takes.sync_review(g, view, ov, 0.2)
    assert view.take_shown == 0 and b.shown(1) == 1 and view.detail  # take 1's verdicts shown
    g.state.op, g.state.point = None, None

    view.focus = Hit(2, None)  # said only in take 1
    takes.sync_review(g, view, ov, 0.3)
    assert view.takes == ("TAKE 1",) and takes.status("review", view).startswith("ONLY TAKE 1 SAID THIS SENTENCE")
    b2 = Board(notes)
    b2.add(1, judged("Good evening everyone."))
    takes.board = b2
    takes.sync_review(g, view, ov, 0.4)
    assert view.takes == () and takes.status("review", view).startswith("NO TAKE SAID THIS SENTENCE")
