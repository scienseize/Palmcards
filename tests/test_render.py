from dataclasses import replace

import numpy as np
import pytest

from palmcards.notes import parse_text
from palmcards.render import Hit, OpsView, TextOverlay, ViewState, layout, sentence_units
from palmcards.style import LABEL

TEXT = (
    "[slow] Thank you for *being* here. / Truly. [rise]\n\n"
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


def test_units_show_marks_as_written():
    first, second = parse_text(TEXT).sentences[:2]
    flat = [" ".join("".join(sp.text for sp in u) for u in sentence_units(s)) for s in (first, second)]
    assert flat[0] == "[slow] Thank you for *being* here."
    assert flat[1] == "/ Truly. [rise]"


def test_layout_wraps_and_starts_each_sentence_on_new_row():
    sentences = parse_text(TEXT).sentences
    rows = layout(sentences, 34)
    for r in rows:
        end = max(c + len(sp.text) for c, sp in r.spans)
        assert end <= 34
    assert [r.sentence for r in rows][:2] == [0, 1]
    assert sum(r.sentence == 2 for r in rows) >= 2  # long sentence wrapped


def test_hit_test_finds_words_and_respects_scroll():
    ov = overlay()
    assert ov.hit_test(*span_center(ov, 0, 3), scroll=0) == Hit(0, 3)  # "being"
    # Scrolled by 2 rows, row 2 sits where row 0 was.
    ri = 2
    word = next(s.word for _, s in ov.rows[ri].spans if s.role == "word")
    assert ov.hit_test(*span_center(ov, ri, word, scroll=2), scroll=2) == Hit(ov.rows[ri].sentence, word)


def test_hit_test_outside_box_and_on_marks():
    ov = overlay()
    assert ov.hit_test(5, 5, 0) is None
    # Leading "[slow]" is a mark, the nearest word is not within one cell.
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
    ov = overlay()
    # Top-left of the hand box lands on the first row; the leading "[slow]"
    # mark is not a word, so snapping picks "Thank".
    assert ov.hit_test(*ov.cursor_to_text(0.0, 0.0), 0, snap=True) == Hit(0, 0)
    assert ov.hit_test(*ov.cursor_to_text(0.0, 0.0), 0) == Hit(0, None)
    # Bottom of the hand box lands on the last visible row.
    hit = ov.hit_test(*ov.cursor_to_text(0.5, 1.0), 0, snap=True)
    assert hit is not None and hit.sentence == ov.rows[ov.visible_rows - 1].sentence


def test_paragraph_unit_groups_sentences():
    ov = overlay()
    # Paragraphs: [0, 1], [2], [3, 4, 5, 6]
    assert ov.unit("paragraph", 1) == [0, 1]
    assert ov.unit("paragraph", 4) == [3, 4, 5, 6]
    assert ov.unit("sentence", 4) == [4]


def test_label_lines_follow_mode_and_operation():
    ov = overlay()
    assert ov.label_lines(ViewState(mode="browse", level="word")) == ("BROWSE BY WORD", "")
    focus = ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"))
    assert ov.label_lines(focus) == ('FOCUS BY WORD  "being"', "L-HAND, THEN TURN TO PICK")
    # Only what works is offered: the word itself, (un)stressing it, and hearing it.
    assert ov.ring_labels(focus) == ("being", "unstress", "hear it")  # *being* is stressed
    assert ov.ring_labels(ViewState(mode="focus", level="word", focus=Hit(0, 1)))[1] == "stress"
    focus.ops.pointing, focus.ops.picked = True, 2
    assert ov.label_lines(focus)[1] == "PINCH + LIFT: HEAR IT"
    focus.ops.picked = 1
    assert ov.label_lines(focus)[1] == 'PINCH + LIFT: UNSTRESS "BEING"'
    focus.ops.picked = 0
    assert ov.label_lines(focus)[1] == "KEEP THE WORD (NO CHANGE)"
    focus.ops = OpsView()
    assert ov.label_lines(focus)[1].startswith("OPEN PALM: STRESS, HEAR IT")
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
        assert ov.label_lines(stretch)[1] == "PARAGRAPH LENGTH: LONGER  x1.50  /  PINCH + LIFT: ASK FOR A REWRITE"
        word = ViewState(mode="focus", level="word", focus=Hit(0, 1), llm=llm)
        assert ov.label_lines(word)[1].startswith("OPEN PALM: ALTERNATIVES, STRESS, HEAR IT")
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
        ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring", picked=1, pointing=True)),
        ViewState(mode="focus", level="sentence", focus=Hit(2, None), ops=OpsView(kind="tone", tone=-0.5)),
        ViewState(mode="focus", level="paragraph", focus=Hit(4, None),
                  ops=OpsView(kind="stretch", stretch=1.4, stretch_ends=((900, 300), (1100, 320)))),
    ):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, view)
        assert (frame != 128).any()


def test_long_paragraph_focus_panel_grows_to_fit():
    long = " ".join(f"Sentence number {i} has a few more words in it." for i in range(8))
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
    assert ov.label_lines(review)[1] == "DROP HAND: BACK"  # no Prepare operations in Review
    assert ov.label_lines(ViewState(app="review"))[0] == "REVIEW"


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


def test_marks_know_their_index_for_verdict_chips():
    s = parse_text(TEXT).sentences[0]  # [slow] Thank you for *being* here.
    spans = [sp for u in sentence_units(s) for sp in u if sp.role == "mark"]
    assert [(sp.text, s.marks[sp.mark].kind) for sp in spans] == [
        ("[slow]", "slow"), ("*", "stress"), ("*", "stress")]


def test_review_draws_verdict_chips_and_the_focus_detail():
    ov = overlay()
    verdicts = (("hit", "missed"), ("unclear", "skipped"), (), (), (), (), ())
    plain = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(plain, ViewState(app="review"))
    chips = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(chips, ViewState(app="review", mark_verdicts=verdicts))
    assert (plain != chips).any()

    detail = (("hit", '/ before "Truly."  HIT  0.40 s pause'), ("", "Pace 140 wpm, take 150"), ("", "No fillers."))
    view = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None), mark_verdicts=verdicts,
                     detail=detail, status="TAKE 2  2 OF 3  /  L-HAND: TAKES  /  PINCH + LIFT: DRILL")
    inv_with = ov._focus_panel(ov._panel_unit(view), detail, verdicts).inv
    inv_without = ov._focus_panel(ov._panel_unit(view)).inv
    assert (1 - inv_with).sum() > (1 - inv_without).sum() * 1.3  # the verdict lines are drawn
    ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)
    assert ov.label_lines(view)[1].startswith("TAKE 2")


def test_a_drill_shows_only_its_sentence():
    ov = TextOverlay(parse_text(SECTIONS, "md").sentences, (1280, 720))
    view = ViewState(app="rehearse", section=0, drill=1, status="SENTENCE 2")
    assert ov._panel_unit(view) == (1,)
    assert ov.label_lines(view) == ("DRILL", "SENTENCE 2")


# --- everything reachable (review finding F2) -------------------------------------

LONG_SECTION = "# Long\n\n" + " ".join(f"This is sentence number {i} of the long section." for i in range(1, 21))
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
    detail = tuple(("missed", f"mark {i}: a reason long enough to wrap onto a second line of the panel") for i in range(30))
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


def test_verdict_lines_carry_a_symbol_not_just_a_colour():
    ov = overlay()
    plain = ov._focus_panel((1,), (("", "hit or not"),)).inv
    marked = ov._focus_panel((1,), (("hit", "hit or not"),)).inv
    assert (plain != marked).any()


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
    right = slice(ov.x + ov.margin + ov.box_w + ov.pad, 1280)
    bottom = slice(560, 720)
    drawn, plain, focused = (np.full((720, 1280, 3), 128, np.uint8) for _ in range(3))
    ov.draw(drawn, ViewState(app="review", mode="browse", level="sentence", summary=card))
    ov.draw(plain, ViewState(app="review", mode="browse", level="sentence"))
    ov.draw(focused, ViewState(app="review", mode="focus", level="sentence", focus=Hit(0, None), summary=card))
    assert (drawn[bottom, right] != plain[bottom, right]).any()
    assert (focused[bottom, right] == 128).all()  # not over a focused panel

def test_a_closing_pinch_is_shown_on_the_dial_it_will_act_on():
    ov = overlay()

    def drawn(v):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, v)
        return frame

    ring = ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring", picked=1, pointing=True))
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
    assert len(nodes) == len(ov.ring_labels(ring)) == 5
    box = ov.word_box(ring.focus, ring.scroll)
    assert nodes[0][1] < box[1]  # the word itself at the top, the rest clockwise round it
    assert ov.ring_nodes(replace(ring, focus=Hit(0, None))) == []

    review = ViewState(app="review", mode="focus", level="sentence", focus=Hit(1, None),
                       takes=("TAKE 1", "TAKE 2", "TAKE 3 (DRILL)"), take_shown=1)
    points = ov.take_points(review)
    assert len(points) == 3 and points[0][1] < points[1][1] < points[2][1]  # a column beside the sentence
    assert all(x > ov.x + ov.margin + ov.box_w for x, _ in points)  # clear of the text

    def drawn(v):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, v)
        return frame

    assert (drawn(review) != drawn(replace(review, takes=()))).any()
    assert (drawn(review) != drawn(replace(review, take_shown=2))).any()  # the shown take is highlighted


# --- the options ring turned like a knob ----------------------------------------------

def knob_view(**kw):
    return ViewState(app="prepare", mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"),
                     alternatives=("present", "around"), **kw)  # being, present, around, unstress, hear it


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
    from palmcards.config import KNOB

    ov = overlay()
    view = knob_view(now=10.0)
    ov.follow_ring(view, "being", 0)
    at_rest = ov.ring_nodes(view)
    assert min(range(5), key=lambda i: at_rest[i][1]) == 0  # the word itself at 12 o'clock
    ov.follow_ring(view, "present", 1)  # one step clockwise
    assert (view.ops.picked, view.ops.rot_from, view.ops.rot_to) == (1, 0.0, 1.0)
    halfway = ov.ring_nodes(replace(view, now=10.0 + KNOB.rotate_s / 2))
    settled = ov.ring_nodes(replace(view, now=10.0 + KNOB.rotate_s))
    assert min(range(5), key=lambda i: settled[i][1]) == 1  # "present" came up to 12 o'clock
    cx = settled[1][0]
    assert settled[0][0] > cx  # and the word itself went on round, clockwise, to the right
    assert at_rest[1][0] < halfway[1][0] < settled[1][0] + 1  # on its way up from the left
    assert halfway[1] != settled[1]


def test_turning_back_past_the_word_wraps_the_short_way():
    ov = overlay()
    view = knob_view(now=5.0)
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "hear it", -1)
    assert (view.ops.picked, view.ops.rot_to) == (4, -1.0)  # turned back one node, not on four


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
    # An action node puts the word itself back in the sentence (scrambling
    # back from "present"), never its own name.
    ov.follow_ring(view, "unstress", 3)
    view.now += KNOB.scramble_s + 0.001
    assert ov.label_lines(view) == ('FOCUS BY WORD  "being"', 'PINCH + LIFT: UNSTRESS "BEING"')
    # From the word itself, stepping onto an action node changes nothing in the sentence: no scramble.
    view = knob_view(now=3.0)
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "hear it", -1)
    assert view.ops.scrambled_t is None
    assert ov.label_lines(view) == ('FOCUS BY WORD  "being"', "PINCH + LIFT: HEAR IT")


def test_the_picked_box_stays_empty_while_its_word_moves_up():
    from palmcards.config import KNOB

    ov = overlay()

    def drawn(v):
        frame = np.full((720, 1280, 3), 128, np.uint8)
        ov.draw(frame, v)
        return frame

    view = knob_view(now=1.0)
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "present", 1)
    assert max(KNOB.rotate_s, KNOB.scramble_s) < KNOB.vacate_s - 0.01  # the turn and the scramble over by then
    empty = drawn(replace(view, now=1.0 + KNOB.vacate_s - 0.01))
    full = drawn(replace(view, now=1.0 + KNOB.vacate_s + 0.01))
    x, y = (int(v) for v in ov.ring_nodes(replace(view, now=5.0))[1])
    assert (empty[y - 6:y + 6, x - 20:x + 20] != full[y - 6:y + 6, x - 20:x + 20]).any()


def test_nodes_arriving_keep_the_pick_and_do_not_turn_the_ring():
    ov = overlay()
    view = ViewState(app="prepare", mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring"), now=2.0)
    ov.follow_ring(view, "being", 0)
    ov.follow_ring(view, "unstress", 1)
    changed = view.ops.changed_t
    view.alternatives, view.now = ("present", "around"), 2.5
    ov.follow_ring(view, "unstress", 1)
    assert (view.ops.picked, view.ops.pick_label, view.ops.changed_t) == (3, "unstress", changed)
    assert view.ops.turned_t is None and view.ops.rot_to == 3.0


def test_the_ring_action_nodes_have_a_dimmer_border(monkeypatch):
    import palmcards.render as render

    ov = overlay()
    view = knob_view(now=9.0)
    ov.follow_ring(view, "being", 0)
    frame = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(frame, view)
    monkeypatch.setattr(render, "C", replace(render.C, node_outline_dim=render.C.node_outline))
    same = np.full((720, 1280, 3), 128, np.uint8)
    ov.draw(same, view)
    assert (frame != same).any()
    assert render.COLORS.node_outline_dim[3] < render.COLORS.node_outline[3]
