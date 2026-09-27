from dataclasses import replace

import numpy as np
import pytest

from palmcards import render

from palmcards.notes import parse_text
from palmcards.render import Hit, OpsView, TextOverlay, ViewState, layout, sentence_units
from palmcards.style import LABEL

TEXT = (
    "———— Thank you for being here. Truly.\n\n"
    "This is a much longer sentence that has to wrap across more than one row of the block.\n\n"
    "Third. Fourth. Fifth. Sixth."
)


def overlay():
    return TextOverlay(parse_text(TEXT).sentences, (1280, 720))


def span_center(ov, ri, word, scroll=0.0):
    col, sp = next((c, s) for c, s in ov.rows[ri].spans if s.word == word)
    x = ov.x + ov.margin + ov.pad + (col + len(sp.text) / 2) * ov.char_w
    y = ov.y + ov.margin + ov.pad + (ri - scroll + 0.4) * ov.line_h
    return x, y


def test_units_are_the_words_and_punctuation():
    first, second = parse_text(TEXT).sentences[:2]
    flat = [" ".join("".join(sp.text for sp in u) for u in sentence_units(s)) for s in (first, second)]
    assert flat == ["———— Thank you for being here.", "Truly."]
    assert [sp.role for u in sentence_units(first) for sp in u] == ["punct"] + ["word"] * 5


def test_layout_flows_sentences_and_leaves_a_blank_row_between_paragraphs():
    sentences = parse_text(TEXT).sentences
    rows = layout(sentences, 40)
    for r in rows:
        assert not r.spans or max(c + len(sp.text) for c, sp in r.spans) <= 40
    # Sentences 0 and 1 are one paragraph: "Truly." runs on after the first.
    assert {sp.sentence for _, sp in rows[0].spans} == {0, 1}
    assert rows[1].spans == [] and rows[2].sentence == 2  # a blank row, then the next paragraph
    assert sum(any(sp.sentence == 2 for _, sp in r.spans) for r in rows) >= 2  # long sentence wrapped
    last = [r for r in rows if any(sp.sentence == 3 for _, sp in r.spans)][0]
    assert {sp.sentence for _, sp in last.spans} >= {3, 4}  # "Third. Fourth. ..." share a row


def test_hit_test_finds_words_and_respects_scroll():
    ov = overlay()
    assert ov.hit_test(*span_center(ov, 0, 3), scroll=0) == Hit(0, 3)  # "being"
    # Scrolled by ri rows, row ri sits where row 0 was.
    ri = next(i for i, r in enumerate(ov.rows) if i >= 2 and any(s.role == "word" for _, s in r.spans))
    sp = next(s for _, s in ov.rows[ri].spans if s.role == "word")
    assert ov.hit_test(*span_center(ov, ri, sp.word, scroll=ri), scroll=ri) == Hit(sp.sentence, sp.word)


def test_hit_test_outside_box_and_on_punctuation():
    ov = overlay()
    assert ov.hit_test(5, 5, 0) is None
    # Leading "————" is punctuation, the nearest word is not within one cell.
    x = ov.x + ov.margin + ov.pad + 2 * ov.char_w
    y = ov.y + ov.margin + ov.pad + 0.4 * ov.line_h
    assert ov.hit_test(x, y, 0) == Hit(0, None)


def test_scroll_helpers():
    ov = overlay()
    assert ov.clamp_scroll(-3) == 0
    assert ov.clamp_scroll(999) == ov.max_scroll
    last = len(ov.sentences) - 1
    assert ov.is_visible(last, ov.scroll_to(last))


def test_draw_composites_in_place():
    ov = overlay()
    frame = np.full((720, 1280, 3), 128, np.uint8)
    view = ViewState(current=1, mode="browse", level="word", hover=Hit(0, 1), scroll=0.5)
    out = ov.draw(frame, view)
    assert out is frame and out.shape == (720, 1280, 3)
    assert (frame != 128).any()


def test_cursor_maps_onto_rows_and_snaps_to_nearest_word():
    ov = TextOverlay(parse_text(TEXT).sentences, (1280, 720), visible_rows=4)  # fewer rows than the text has
    # Top-left of the hand box lands on the first row; the leading "————"
    # is not a word, so snapping picks "Thank".
    assert ov.hit_test(*ov.cursor_to_text(0.0, 0.0), 0, snap=True) == Hit(0, 0)
    assert ov.hit_test(*ov.cursor_to_text(0.0, 0.0), 0) == Hit(0, None)
    # Bottom of the hand box lands on the last visible row (a blank one: the paragraph above it).
    hit = ov.hit_test(*ov.cursor_to_text(0.5, 1.0), 0, snap=True)
    last = next(r for r in reversed(ov.rows[:ov.visible_rows]) if r.spans)
    assert hit is not None and hit.sentence in {sp.sentence for _, sp in last.spans}


def test_paragraph_unit_groups_sentences():
    ov = overlay()
    # Paragraphs: [0, 1], [2], [3, 4, 5, 6]
    assert ov.unit("paragraph", 1) == [0, 1]
    assert ov.unit("paragraph", 4) == [3, 4, 5, 6]
    assert ov.unit("sentence", 4) == [4]


def test_label_lines_follow_mode_and_operation():
    ov = overlay()
    assert ov.label_lines(ViewState(mode="browse", level="word")) == ("BROWSE BY WORD", "")
    focus = ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"), llm="local")
    assert ov.label_lines(focus) == ('FOCUS BY WORD  "being"', "L-HAND, THEN TURN TO PICK")
    assert ov.ring_labels(focus) == ("being",)
    focus.alternatives = ("present",)
    focus.ops.pointing, focus.ops.picked = True, 1
    assert ov.label_lines(focus)[1] == 'PINCH + LIFT: USE "PRESENT"'
    focus.ops.picked = 0
    assert ov.label_lines(focus)[1] == "KEEP THE WORD (NO CHANGE)"
    focus.ops = OpsView()
    assert ov.label_lines(focus)[1].startswith("OPEN PALM: ALTERNATIVES")
    tone = ViewState(mode="focus", level="sentence", focus=Hit(1, None), ops=OpsView(kind="tone", tone=0.6))
    assert ov.label_lines(tone) == ("FOCUS BY SENTENCE", "SENTENCE TONE: WARM  (PREVIEW ONLY: NEEDS THE OPTIONAL LLM)")
    tone.drop_progress = 0.4
    assert ov.label_lines(tone)[1] == "DROP HAND TO BACK OUT"


def test_labels_promise_a_rewrite_only_when_an_llm_is_on():
    ov = overlay()
    for llm in ("cloud", "local"):
        tone = ViewState(mode="focus", level="sentence", focus=Hit(1, None), ops=OpsView(kind="tone", tone=-0.6),
                         llm=llm)
        assert ov.label_lines(tone)[1] == "SENTENCE TONE: COLD  /  PINCH + LIFT: ASK FOR A REWRITE"
        stretch = ViewState(mode="focus", level="paragraph", focus=Hit(4, None), ops=OpsView(kind="stretch", stretch=1.5),
                            llm=llm)
        assert ov.label_lines(stretch)[1] == "PARAGRAPH LENGTH: FULLER +50%  /  PINCH + LIFT: ASK FOR A REWRITE"
        word = ViewState(mode="focus", level="word", focus=Hit(0, 1), llm=llm)
        assert ov.label_lines(word)[1].startswith("OPEN PALM: ALTERNATIVES")
    stretch.llm = ""
    assert ov.label_lines(stretch)[1].endswith("(PREVIEW ONLY: NEEDS THE OPTIONAL LLM)")


def test_the_cloud_chip_shows_while_the_cloud_llm_is_on():
    ov = overlay()

    def bottom_left(**kw):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, ViewState(**kw))
        return frame[620:, :640]

    off, local = bottom_left(), bottom_left(llm="local")
    idle, busy = bottom_left(llm="cloud"), bottom_left(llm="cloud", llm_busy=True)
    assert np.array_equal(off, local)  # a local model sends nothing off the Mac: no chip
    assert not np.array_equal(off, idle) and not np.array_equal(idle, busy)
    keys = bottom_left(keys_help=True)
    assert not np.array_equal(keys, bottom_left(keys_help=True, llm="cloud"))  # also under the keys help


def test_draw_focus_states_and_stubs():
    ov = overlay()
    for view in (
        ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring", picked=1, pointing=True),
                  alternatives=("present",)),
        ViewState(mode="focus", level="sentence", focus=Hit(2, None), ops=OpsView(kind="tone", tone=-0.5)),
        ViewState(mode="focus", level="paragraph", focus=Hit(4, None),
                  ops=OpsView(kind="stretch", stretch=1.4, stretch_ends=((900, 300), (1100, 320)))),
    ):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, view)
        assert (frame != 128).any()


def test_long_paragraph_focus_panel_grows_to_fit():
    long = " ".join(f"Sentence number {i} has a few more words in it." for i in range(30))
    ov = TextOverlay(parse_text(long).sentences, (1280, 720))
    view = ViewState(mode="focus", level="paragraph", focus=Hit(0, None))
    assert ov._focus_panel(ov._panel_unit(view)).height > ov.box_h
    ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)


SECTIONS = "# One\n\nFirst here. Second here.\n\n# Two\n\nThird here."


def test_section_unit_and_rehearse_panel_shows_the_section():
    ov = TextOverlay(parse_text(SECTIONS, "md").sentences, (1280, 720))
    assert ov.unit("section", 1) == [0, 1]
    assert ov._panel_unit(ViewState(app="rehearse", section=1)) == (2,)
    assert ov._panel_unit(ViewState(app="count_in", section=0)) == (0, 1)
    assert ov._panel_unit(ViewState(app="review")) is None


def test_label_lines_for_takes():
    ov = overlay()
    assert ov.label_lines(ViewState(status="HOLD FIST: START A TAKE")) == ("PREPARE", "HOLD FIST: START A TAKE")
    assert ov.label_lines(ViewState(start_progress=0.5))[1] == "START A TAKE: HOLD FIST  [=====     ]"
    assert ov.label_lines(ViewState(app="count_in", count_in=2)) == ("REHEARSE", "STARTING IN 2")
    assert ov.label_lines(ViewState(app="count_in", hold_progress=0.3))[1].startswith("CANCEL: HOLD")
    rehearse = ViewState(app="rehearse", status="SECTION 1/2: ONE")
    assert ov.label_lines(rehearse) == ("REHEARSE", "SECTION 1/2: ONE")
    rehearse.note = "LAST SECTION"
    assert ov.label_lines(rehearse)[1] == "LAST SECTION"
    review = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None))
    assert ov.label_lines(review)[1] == "  /  PINCH + LIFT: DRILL"  # no Prepare operations in Review
    assert ov.label_lines(ViewState(app="review"))[0] == "REVIEW"


def test_review_labels_list_the_gestures_that_act():
    ov = overlay()
    browse = ViewState(app="review", mode="browse", level="sentence", status="TAKE 2 SAVED (0:41)")
    assert ov.label_lines(browse) == ("BROWSE BY SENTENCE", "TAKE 2 SAVED (0:41)  /  FOLD: DETAILS · FIST: NEW TAKE")
    browse.level = "paragraph"
    assert ov.label_lines(browse)[1].endswith("FOLD: SUMMARY · FIST: NEW TAKE")
    focus = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), status="TAKE 2  2 OF 3",
                      takes=("TAKE 1", "TAKE 2", "TAKE 3"), take_shown=1, playable=True)
    assert ov.label_lines(focus)[1] == \
        "TAKE 2  2 OF 3  /  OPEN PALM: PLAY · L, POINT: TAKE 2 OF 3 · PINCH + LIFT: DRILL"
    focus.takes, focus.take_shown = ("TAKE 1",), 0
    assert ov.label_lines(focus)[1].endswith("  /  OPEN PALM: PLAY · PINCH + LIFT: DRILL")  # nothing to choose
    paragraph = ViewState(app="review", mode="focus", level="paragraph", focus=Hit(1, None), status="TAKE 3",
                          playable=True)
    assert ov.label_lines(paragraph)[1] == "TAKE 3  /  OPEN PALM: PLAY PARAGRAPH · DROP HAND: BACK"
    paragraph.playable = False
    assert ov.label_lines(paragraph)[1] == "TAKE 3  /  DROP HAND: BACK"


def hint_rows(ov, view):
    return [text for line, text in ov.label_rows(view) if line == 2]


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_review_hints_take_the_short_form_when_the_long_one_does_not_fit_a_row(size):
    ov = TextOverlay(parse_text(TEXT).sentences, size)
    focus = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), status="TAKE 2  2 OF 3",
                      takes=("TAKE 1", "TAKE 2", "TAKE 3"), take_shown=1, playable=True)
    assert hint_rows(ov, focus) == ["PALM: PLAY · L: 2/3 · PINCH+LIFT: DRILL"]  # the long form needs two rows
    focus.takes, focus.take_shown = tuple(f"TAKE {n}" for n in range(1, 13)), 10
    rows = hint_rows(ov, focus)  # too wide even short: its hints packed whole, never split
    assert 1 <= len(rows) <= 2 and " · ".join(rows) == "PALM: PLAY · L: 11/12 · PINCH+LIFT: DRILL"
    browse = ViewState(app="review", mode="browse", level="sentence")
    assert hint_rows(ov, browse) == ["FOLD: DETAILS · FIST: NEW TAKE"]  # the long form fits
    paragraph = ViewState(app="review", mode="focus", level="paragraph", focus=Hit(1, None), playable=True)
    assert hint_rows(ov, paragraph) == ["PALM: PLAY PARAGRAPH · DROP: BACK"]


def test_while_it_plays_the_hint_is_stop_and_a_held_palm_fills_the_stop_bar():
    ov = overlay()
    review = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), status="TAKE 2",
                       takes=("TAKE 1", "TAKE 2"), take_shown=1, playable=True, playing=True, play_progress=0.4)
    assert ov.label_lines(review)[1] == "TAKE 2  /  OPEN PALM: STOP"
    review.palm_progress = 0.5
    assert ov.label_lines(review)[1] == "STOP: HOLD  [=====     ]"
    prepare = ViewState(app="prepare", mode="focus", level="sentence", focus=Hit(1, None), playing=True)
    assert ov.label_lines(prepare)[1] == "OPEN PALM: STOP"
    browse = ViewState(app="review", mode="browse", level="sentence", playing=True)
    assert ov.label_lines(browse)[1].endswith("A: STOP")  # no palm stop while browsing: the key


def test_a_thin_progress_bar_under_the_focused_unit_while_it_plays():
    ov = overlay()
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), hover=Hit(1, None),
                     playing=True)  # the same label either way
    quiet = ov.draw(np.full((720, 1280, 3), 128, np.uint8), view).copy()
    view.play_progress = 0.5
    playing = ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)
    changed = np.argwhere((playing != quiet).any(axis=2))
    assert len(changed)
    rows = sorted(set(changed[:, 0]))
    assert rows[-1] - rows[0] < 12  # a thin bar, not a redraw
    orange = np.array(render.bgr(render.C.orange))
    assert ((playing[rows[0]] == orange).all(axis=1)).sum() > 20  # its played half in orange


def test_draw_count_in_and_rehearse():
    ov = TextOverlay(parse_text(SECTIONS, "md").sentences, (1280, 720))
    for view in (
        ViewState(app="count_in", count_in=3, zone_active=True, hold_progress=0.4),
        ViewState(app="rehearse", section=1, rec_s=75.2, mic=0.7, hold_progress=0.5, status="SECTION 2/2: TWO"),
    ):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, view)
        zone = frame[:int(0.42 * 720), int(0.72 * 1280):]
        assert (zone != 128).any()

def test_review_draws_the_take_table_and_the_focused_sentences_takes():
    from palmcards.review import Board
    from tests.test_review import FULL, analysed, metrics

    ov = overlay()
    plain = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(plain, ViewState(app="review"))
    notes = parse_text(TEXT)
    board = Board(notes)
    board.add(1, analysed(FULL, notes), metrics([(8, 2, 0)] * 7), duration_s=61.0)
    board.add(2, analysed(FULL, notes), metrics([(8, 2, 0)] * 7, pace=131.0), duration_s=55.0)
    table = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(table, ViewState(app="review", summary=board.take_table()))
    changed = np.argwhere((plain != table).any(axis=2))
    assert len(changed) and changed[:, 1].min() > ov.col_x1  # bottom right, clear of the text column
    assert changed[:, 0].min() > 720 / 2

    detail = ("  Take 1: 140 wpm, no fillers, pitch range 4.5 st, on screen 80%",
              "▸ Take 2: 131 wpm, 1 filler, pitch range 5 st, on screen 75%")
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), detail=detail,
                     status="TAKE 2  2 OF 2  /  L-HAND, THEN POINT: TAKES  /  PINCH + LIFT: DRILL")
    inv_with = ov._focus_panel(ov._panel_unit(view), detail).inv
    inv_without = ov._focus_panel(ov._panel_unit(view)).inv
    assert (1 - inv_with).sum() > (1 - inv_without).sum() * 1.3  # the takes' lines are drawn
    ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)
    assert ov.label_lines(view)[1].startswith("TAKE 2")


def test_a_drill_shows_only_its_sentence():
    ov = TextOverlay(parse_text(SECTIONS, "md").sentences, (1280, 720))
    view = ViewState(app="rehearse", section=0, drill=1, status="SENTENCE 2")
    assert ov._panel_unit(view) == (1,)
    assert ov.label_lines(view) == ("DRILL", "SENTENCE 2")


# --- everything reachable (review finding F2) -------------------------------------

LONG_SECTION = "# Long\n\n" + " ".join(f"This is sentence number {i} of the long section." for i in range(1, 61))
LABEL_RESERVE = lambda ov: LABEL.min_top + ov.label_h + ov.pad // 2


@pytest.mark.parametrize("size", [(640, 480), (1280, 720), (1920, 1080)])
def test_every_sentence_of_a_long_section_can_be_brought_into_view(size):
    notes = parse_text(LONG_SECTION, "md")
    ov = TextOverlay(notes.sentences, size)
    view = ViewState(app="rehearse", section=0)
    panel = ov.panel(view)
    vh = ov.panel_view_h(panel)
    assert panel.height > vh  # it does not fit: a viewport, not a clipped panel
    top = ov._panel_top(vh)
    assert top >= LABEL_RESERVE(ov) and top + vh <= size[1]  # below the label, on the frame
    for i in range(len(notes.sentences)):
        view.current = i
        view.panel_scroll = ov.panel_scroll_to(view, i)
        a, b = ov.panel(view).rows[i]
        assert view.panel_scroll <= a and b <= view.panel_scroll + vh, i
    assert view.panel_scroll == ov.panel_max_scroll(view)  # the last sentence is at the very bottom
    frame = np.full((size[1], size[0], 3), 128, np.uint8)
    ov.draw(frame, view)


def test_the_current_sentence_is_orange_in_rehearse():
    notes = parse_text(SECTIONS, "md")
    ov = TextOverlay(notes.sentences, (1280, 720))
    a = ov._focus_panel((0, 1), current=0).color
    b = ov._focus_panel((0, 1), current=1).color
    assert (a != b).any()
    assert ov._panel_current(ViewState(app="rehearse", current=1), (0, 1)) == 1
    assert ov._panel_current(ViewState(app="rehearse", current=1, drill=1), (1,)) is None  # a drill: one sentence


def test_long_review_details_reach_the_last_line():
    ov = overlay()
    detail = tuple(f"  Take {i}: a line long enough to wrap onto a second line of the panel" for i in range(30))
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), detail=detail)
    panel = ov.panel(view)
    vh = ov.panel_view_h(panel)
    assert panel.height > vh and ov.panel_max_scroll(view) == panel.height - vh
    view.panel_scroll = 10 ** 6  # far past the end: clamped to show the last line
    assert ov.clamp_panel_scroll(view, view.panel_scroll) == panel.height - vh
    ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)


def test_a_word_longer_than_a_row_is_split_and_still_hit():
    text = "Say " + "supercalifragilisticexpialidocious" * 2 + " now."
    ov = TextOverlay(parse_text(text).sentences, (1280, 720), columns=20)
    assert all(sum(len(sp.text) for _, sp in r.spans) + len(r.spans) - 1 <= 20 for r in ov.rows)
    pieces = [(ri, c, sp) for ri, r in enumerate(ov.rows) for c, sp in r.spans if sp.word == 1]
    assert len({ri for ri, _, _ in pieces}) >= 3 and "".join(sp.text for _, _, sp in pieces) == \
        "supercalifragilisticexpialidocious" * 2
    ri, col, sp = pieces[-1]  # the last piece, on its own row, still answers as word 1
    x = ov.x + ov.margin + ov.pad + (col + 1) * ov.char_w
    y = ov.y + ov.margin + ov.pad + (ri + 0.5) * ov.line_h
    assert ov.hit_test(x, y, 0.0) == Hit(0, 1)

def test_the_alert_line_and_keys_help_are_drawn():
    ov = overlay()
    base = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(base, ViewState())
    alert = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(alert, ViewState(alert="RECORDING FAILED: AUDIO SO FAR KEPT"))
    keys = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(keys, ViewState(keys_help=True))
    assert (base != alert).any() and (base != keys).any()


def test_the_calibration_steps_in_the_count_in():
    ov = overlay()
    view = ViewState(app="count_in", count_in=5, calibration="camera", current=0)
    assert ov.label_lines(view) == ("REHEARSE", "LOOK INTO THE CAMERA ABOVE THE SCREEN")
    view = ViewState(app="count_in", count_in=2, calibration="notes", current=0)
    assert ov.label_lines(view)[1] == "NOW READ THE ORANGE SENTENCE  2"
    # No big count digit while calibrating: it would pull the eyes away.
    calibrating, counting = (np.full((720, 1280, 3), 128, np.uint8) for _ in range(2))
    ov.draw(calibrating, view)
    ov.draw(counting, ViewState(app="count_in", count_in=2, current=0))
    right = slice(ov.x + ov.margin + ov.box_w + 1, 1280)
    below_zone = slice(round(720 * 0.5), 720)
    assert (counting[below_zone, right] != calibrating[below_zone, right]).any()


def test_the_take_summary_card_shows_while_browsing_review():
    ov = overlay()
    card = ("TAKE 1  1:01", "5 HIT, 2 MISSED", "ON SCREEN 82%  AWAY 14%")
    column = slice(ov.col_x1, 1280)  # right of the text column ...
    under = slice(720 // 2, 720)  # ... in the bottom half
    drawn, plain, focused, focused_plain = (np.full((720, 1280, 3), 128, np.uint8) for _ in range(4))
    ov.draw(drawn, ViewState(app="review", mode="browse", level="sentence", summary=card))
    ov.draw(plain, ViewState(app="review", mode="browse", level="sentence"))
    ov.draw(focused, ViewState(app="review", mode="focus", level="sentence", focus=Hit(0, None), summary=card))
    ov.draw(focused_plain, ViewState(app="review", mode="focus", level="sentence", focus=Hit(0, None)))
    assert (drawn[under, column] != plain[under, column]).any()  # bottom right
    assert (focused == focused_plain).all()  # not with a focused panel

def test_a_closing_pinch_is_shown_on_the_dial_it_will_act_on():
    ov = overlay()

    def drawn(v):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, v)
        return frame

    ring = ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring", picked=1, pointing=True),
                     alternatives=("present",))
    tone = ViewState(mode="focus", level="sentence", focus=Hit(2, None), ops=OpsView(kind="tone", tone=0.4))
    stretch = ViewState(mode="focus", level="paragraph", focus=Hit(4, None),
                        ops=OpsView(kind="stretch", stretch=1.4, stretch_ends=((900, 300), (1100, 320))))
    for view in (ring, tone, stretch):
        held = replace(view, ops=replace(view.ops, closing=True))
        assert (drawn(view) != drawn(held)).any(), view.ops.kind


def test_where_the_ring_nodes_and_the_take_chips_are():
    ov = overlay()
    ring = ViewState(app="prepare", mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"),
                     alternatives=("present", "around"))
    nodes = ov.ring_nodes(ring)
    assert len(nodes) == len(ov.ring_labels(ring)) == 3
    box = ov.focus_word_box(ring)
    assert nodes[0][1] < box[1]  # the word itself at the top, the rest clockwise round it
    # Kat's bubble map: the zoomed word's row mid-box, the nodes round it inside the text column, none over another.
    assert abs((box[1] + box[3]) / 2 - (ov.y + ov.box_h / 2)) < ov.line_h * 2
    size = ov._node_size()
    assert all(0 < x < ov.col_x1 for x, _ in nodes)
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            assert abs(a[0] - b[0]) > 10 or abs(a[1] - b[1]) > size
    assert ov.ring_nodes(replace(ring, focus=Hit(0, None))) == []

    review = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None),
                       takes=("TAKE 1", "TAKE 2", "TAKE 3 (DRILL)"), take_shown=1)
    points = ov.take_points(review)
    assert len(points) == 3 and points[0][1] < points[1][1] < points[2][1]  # a column beside the sentence
    text_right = ov.x + ov.panel(review).color.shape[1] - ov.pad  # the panel narrows for them
    assert all(text_right < x < ov.col_x1 for x, _ in points)  # clear of the text, in the text column

    def drawn(v):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, v)
        return frame

    assert (drawn(review) != drawn(replace(review, takes=()))).any()
    assert (drawn(review) != drawn(replace(review, take_shown=2))).any()  # the shown take is highlighted


# --- the options ring turned like a knob ----------------------------------------------

def knob_view(**kw):
    return ViewState(app="prepare", mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"),
                     alternatives=("present", "around"), **kw)  # being, present, around


def test_resolve_scramble_resolves_left_to_right():
    from palmcards.render import resolve_scramble

    assert resolve_scramble("stamped", 1.0, 7) == "stamped"
    for p in (0.0, 0.3, 0.6, 0.99):
        out = resolve_scramble("stamped", p, 7)
        assert len(out) == len("stamped")
        done = int(7 * p)
        assert out[:done] == "stamped"[:done]
    assert resolve_scramble("well-known", 0.0, 3)[4] == "-"  # only letters scramble
    assert resolve_scramble("stamped", 0.0, 1) != resolve_scramble("stamped", 0.0, 2)  # a new seed each frame


def test_the_ring_turns_the_picked_node_to_the_top():
    from palmcards.style import MOTION

    ov = overlay()
    view = knob_view(now=10.0)
    ov.follow_ring(view, "being", 0)
    at_rest = ov.ring_nodes(view)
    assert min(range(3), key=lambda i: at_rest[i][1]) == 0  # the word itself at 12 o'clock
    ov.follow_ring(view, "present", 1)  # one step clockwise
    assert (view.ops.picked, view.ops.rot_to, view.ops.rot.at(10.0)) == (1, 1.0, 0.0)
    halfway = ov.ring_nodes(replace(view, now=10.0 + MOTION.ring / 2))
    settled = ov.ring_nodes(replace(view, now=11.0))
    assert min(range(3), key=lambda i: settled[i][1]) == 1  # "present" came up to 12 o'clock
    cx = settled[1][0]
    assert settled[0][0] > cx  # and the word itself went on round, clockwise, to the right
    assert at_rest[1][0] < halfway[1][0] < settled[1][0] + 1  # on its way up from the left
    assert halfway[1] != settled[1]


def test_the_ring_turns_with_the_hand_between_nodes_and_rests_on_a_node():
    from palmcards.style import MOTION

    ov = overlay()
    view = knob_view(now=1.0)
    ov.follow_ring(view, "being", 0)
    view.ops.offset = 0.05  # the index's wobble near the node: the ring stays on it
    ov.follow_ring(view, "being", 0)
    assert ov.ring_nodes(replace(view, now=2.0)) == ov.ring_nodes(replace(view, now=1.0))
    view.ops.offset, view.now = 0.6, 2.0  # turned most of the way to the next step
    ov.follow_ring(view, "being", 0)
    turned = view.ops.rot.at(3.0)
    assert turned == pytest.approx((0.6 - MOTION.ring_flat) * MOTION.ring_gain)  # part of the way, no further
    x = view.ops.rot.at(2.05)
    view.ops.offset, view.now = -0.3, 2.05  # the step lands: it goes on from there, with its speed
    ov.follow_ring(view, "present", 1)
    assert view.ops.rot.at(2.05) == pytest.approx(x) and view.ops.rot.velocity(2.05) > 0
    assert view.ops.rot.at(3.0) == pytest.approx(1 - (0.3 - MOTION.ring_flat) * MOTION.ring_gain)
    view.ops.offset, view.now = 0.0, 3.0  # the hand lets go (closing into a pinch): onto the node
    ov.follow_ring(view, "present", 1)
    assert view.ops.rot.at(4.0) == 1.0


def test_turning_back_past_the_word_wraps_the_short_way():
    ov = overlay()
    view = knob_view(now=5.0)
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "around", -1)
    assert (view.ops.picked, view.ops.rot_to) == (2, -1.0)


def test_a_new_word_scrambles_into_the_sentence_and_the_label():
    from palmcards.config import KNOB

    ov = overlay()
    view = knob_view(now=3.0)
    ov.follow_ring(view, "being", 0)
    assert ov.label_lines(view)[0] == 'FOCUS BY WORD  "being"'
    ov.follow_ring(view, "present", 1)
    during = ov.label_lines(replace(view, now=3.0 + KNOB.scramble_s / 3))[0]
    assert during != 'FOCUS BY WORD  "present"' and len(during) == len('FOCUS BY WORD  "present"')
    view.now = 3.0 + KNOB.scramble_s + 0.001
    assert ov.label_lines(view) == ('FOCUS BY WORD  "present"', 'PINCH + LIFT: USE "PRESENT"')
    # Returning to the original word restores it as a live preview.
    ov.follow_ring(view, "being", 3)
    view.ops.pointing = True
    view.now += KNOB.scramble_s + 0.001
    assert ov.label_lines(view) == ('FOCUS BY WORD  "being"', "KEEP THE WORD (NO CHANGE)")


def test_the_picked_node_is_highlighted_at_once_and_while_turning():
    # A recorded session stepped the knob every 0.1-0.2 s: a box left empty for a moment
    # after each step meant the pick was never shown while turning.
    from palmcards.style import COLORS, MOTION

    ov = overlay()
    view = knob_view(now=1.0)
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "present", 1)
    for now in (1.0 + 1 / 30, 1.0 + MOTION.ring / 2, 3.0):  # just after the step, mid-turn, settled
        frame = np.full((720, 1280, 3), 128, np.uint8)
        v = replace(view, now=now)
        ov.draw(frame, v)
        x, y = (int(c) for c in ov.ring_nodes(v)[1])
        patch = frame[y - 4:y + 4, x - 30:x + 30].reshape(-1, 3).astype(int)
        orange = np.array(COLORS.chip_fill[2::-1])  # BGR
        assert (np.abs(patch - orange).sum(axis=1) < 60).mean() > 0.3, now


def test_nodes_arriving_keep_the_pick_and_do_not_turn_the_ring():
    ov = overlay()
    view = ViewState(app="prepare", mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"), now=2.0,
                     alternatives=("around",))
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "around", 1)
    changed = view.ops.changed_t
    view.alternatives, view.now = ("present", "around"), 2.5
    ov.follow_ring(view, "around", 1)
    assert (view.ops.picked, view.ops.pick_label, view.ops.changed_t) == (2, "around", changed)
    assert view.ops.rot.at(2.5) == view.ops.rot_to == 2.0  # snapped, not turned


def test_hear_it_is_offered_for_sentences_only():
    ov = overlay()
    assert "hear it" not in ov.ring_labels(knob_view())
    for llm in ("", "local", "cloud"):
        for mode in ("browse", "focus"):
            sentence = ViewState(mode=mode, level="sentence", focus=Hit(0, None), llm=llm)
            assert "HEAR IT" in ov.label_lines(sentence)[1]


def test_meaning_fits_below_word_and_disappears_when_alternatives_open():
    for w, h in ((640, 480), (1280, 720)):
        ov = TextOverlay(parse_text("The river bank was steep.").sentences, (w, h))
        view = ViewState(mode="focus", level="word", focus=Hit(0, 2), llm="local",
                         meaning="abcdefghijklmno " * 12 + "meaning")
        plain = np.full((h, w, 3), 128, np.uint8)
        shown = plain.copy()
        ov.draw(plain, replace(view, meaning=""))
        ov.draw(shown, view)
        ys, xs = np.nonzero(np.any(shown != plain, axis=2))
        assert len(ys) and ys.min() > ov.focus_word_box(view)[3]
        assert ys.max() < ov.y + ov.box_h and xs.max() < ov.col_x1
        ring = replace(view, ops=OpsView(kind="ring"), alternatives=("shore",))
        a, b = np.full((h, w, 3), 128, np.uint8), np.full((h, w, 3), 128, np.uint8)
        ov.draw(a, ring)
        ov.draw(b, replace(ring, meaning=""))
        assert np.array_equal(a, b)


# --- the three zones: text column, face, hand ----------------------------------------

def test_nothing_is_drawn_over_the_face():
    from palmcards.style import LAYOUT

    ov = overlay()
    table = ("         TAKE 1  TAKE 2  TAKE 3  TAKE 4", "LENGTH     0:32    0:41    0:38    0:36")
    views = [
        ViewState(mode="browse", level="word", hover=Hit(1, 0), status="RAISE A FIST: START A TAKE", llm="cloud"),
        ViewState(mode="browse", level="sentence", hover=Hit(1, 0), status="RAISE A FIST: START A TAKE"),
        ViewState(mode="browse", level="paragraph", hover=Hit(0, 0)),
        ViewState(mode="focus", level="word", focus=Hit(0, 3), llm="local",
                  meaning="Existing or present in a particular place or situation."),
        ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring", picked=1),
                  alternatives=("present", "around", "attending")),
        ViewState(mode="focus", level="sentence", focus=Hit(2, None), ops=OpsView(kind="tone", tone=0.4), llm="cloud"),
        # The stretch line runs between the two hands, like the fingertip dots: left out here.
        ViewState(mode="focus", level="paragraph", focus=Hit(4, None), ops=OpsView(kind="stretch", stretch=0.8)),
        ViewState(app="count_in", count_in=2),
        ViewState(app="rehearse", current=1, rec_s=4.0, status="SECTION 1/1: THE ONLY ONE", zone_active=True),
        ViewState(app="review", mode="browse", level="sentence", hover=Hit(2, None), summary=table,
                  status="TAKE 1: 9/9 SPOKEN, 216 WPM, 0 FILLERS/MIN  /  RAISE A FIST: NEW TAKE"),
        ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), takes=("TAKE 1", "TAKE 2 (DRILL)"),
                  detail=("  Take 1: 140 wpm, no fillers, pitch range 4.5 st, on screen 80%",)),
        ViewState(alert="ANALYSIS FAILED FOR TAKE 2: SOMETHING WENT WRONG, PRESS R", keys_help=True,
                  tutorial=(2, 6, "HOLD UP ONE FINGER: BROWSE BY WORD")),
    ]
    x0, x1 = (int(f * 1280) for f in LAYOUT.face)
    for view in views:
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, view)
        assert (frame[:, x0:x1] == 128).all(), view
        assert (frame[:, :ov.col_x1] != 128).any()  # and the text column has it


# --- motion: the panel's scroll, the section handed on, the hand's feedback --------------

def test_the_panel_scrolls_sprung_toward_where_the_app_puts_it():
    from palmcards.render import PanelMotion

    notes = parse_text(LONG_SECTION, "md")
    ov = TextOverlay(notes.sentences, (1280, 720))
    view = ViewState(app="rehearse", section=0, now=1.0, panel_motion=PanelMotion())
    ov.follow_panel_scroll(view)
    assert ov.shown_panel_scroll(view) == 0.0
    last = len(notes.sentences) - 1
    view.current, view.panel_scroll = last, ov.panel_scroll_to(view, last)  # the voice moved on
    ov.follow_panel_scroll(view)
    assert ov.shown_panel_scroll(view) == 0.0  # from where it was
    view.now = 1.1
    assert 0.0 < ov.shown_panel_scroll(view) < view.panel_scroll  # on its way
    view.now = 3.0
    assert ov.shown_panel_scroll(view) == view.panel_scroll
    ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)
    view.current, view.panel_scroll = 0, 0.0  # a key: at once
    ov.follow_panel_scroll(view, snap=True)
    assert ov.shown_panel_scroll(view) == 0.0
    # Without the app's spring (tests, the player) it is drawn where it is put.
    assert ov.shown_panel_scroll(ViewState(app="rehearse", section=0, panel_scroll=50.0)) == 50.0


def test_the_section_handed_on_slides_up_from_where_its_preview_was():
    from palmcards.render import PanelMotion

    ov = TextOverlay(parse_text(SECTIONS, "md").sentences, (1280, 720))
    view = ViewState(app="rehearse", section=0, current=1, preview_next=True, now=1.0, panel_motion=PanelMotion())
    frame = np.full((720, 1280, 3), 128, np.uint8)
    ov.follow_panel_scroll(view)
    ov.draw(frame, view)
    before = ov._drawn_rows[2]  # the next section's sentence, faint under the current one
    view.section, view.current, view.preview_next, view.panel_scroll, view.now = 1, 2, False, 0.0, 1.033
    ov.follow_panel_scroll(view)
    ov.draw(frame.copy(), view)
    assert ov._drawn_rows[2] == pytest.approx(before, abs=1)  # it starts where it was
    view.now = 3.0
    ov.draw(frame.copy(), view)
    assert ov._drawn_rows[2] < before  # and slides up into place


def test_curled_fingers_dots_are_faint_and_a_shape_not_yet_counted_is_a_ring():
    from palmcards.gestures import GestureState, HandTrack, INDEX_TIP, MIDDLE_TIP

    hand = type("H", (), {"points": np.array([[600 + 20 * i, 300 + 10 * i] for i in range(21)], float)})()
    track = HandTrack()
    track.hand, track.raw, track.stable = hand, "ONE", "ONE"
    track.feat = type("F", (), {"extended": (True, False, False, False), "thumb_out": False})()

    def drawn(**kw):
        for k, v in kw.items():
            setattr(track, k, v)
        frame = np.zeros((720, 1280, 3), np.uint8)
        render.draw_fingertips(frame, GestureState(primary=track))
        return frame

    def at(frame, i):
        x, y = (int(v) for v in hand.points[i])
        return frame[y, x].astype(int)

    steady = drawn()
    assert at(steady, INDEX_TIP).sum() > 300 and 0 < at(steady, MIDDLE_TIP).sum() < 200  # curled: faint
    pending = drawn(raw="TWO")
    assert at(pending, INDEX_TIP).sum() == 0 and pending.sum() > 0  # a ring round the tip, not a dot


def test_what_a_commit_acts_on_rises_with_the_pinched_hand_and_the_focus_fades_as_the_hand_drops():
    ov = overlay()
    view = ViewState(mode="focus", level="sentence", focus=Hit(1, None))

    def drawn(v):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, v)
        return frame

    from palmcards.style import MOTION

    still = drawn(view)
    lifted = drawn(replace(view, lift=1.0))
    panel, _, _, top = ov._panel_origin(view)
    a, b = top + 20, top + ov.panel_view_h(panel) - 20
    col = slice(ov.col_x0, ov.col_x1)
    off = lambda s: np.abs(lifted[a - s:b - s, col].astype(int) - still[a:b, col]).mean()
    assert min(range(12), key=off) == MOTION.lift_px  # the panel drawn higher by the lift
    dropping = drawn(replace(view, drop_progress=1.0))
    ink = lambda f: np.abs(f[:, ov.col_x0:ov.col_x1].astype(int) - 128).sum()
    assert ink(dropping) < ink(still)  # fading
