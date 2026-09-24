import numpy as np

from palmcards.notes import parse_text
from palmcards.render import Hit, OpsView, TextOverlay, ViewState, layout, ring_pick, sentence_units

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
    assert ov.label_lines(focus) == ('FOCUS BY WORD  "being"', "EXPLORE WORD ALTERNATIVES: PLACEHOLDER")
    focus.ops.pointing, focus.ops.picked = True, 4
    assert ov.label_lines(focus)[1] == "PREVIEW: STRESS"
    tone = ViewState(mode="focus", level="sentence", focus=Hit(1, None), ops=OpsView(kind="tone", tone=0.6))
    assert ov.label_lines(tone) == ("FOCUS BY SENTENCE", "CHANGE SENTENCE TONE: WARM")
    tone.drop_progress = 0.4
    assert ov.label_lines(tone)[1] == "DROP HAND TO BACK OUT"


def test_ring_pick_by_direction_with_dead_zone():
    assert ring_pick(0.5, 0.0, 6, previous=3) == 0  # up
    assert ring_pick(1.0, 0.25, 6, previous=3) == 1  # up and to the right
    assert ring_pick(0.5, 1.0, 6, previous=0) == 3  # down
    assert ring_pick(0.52, 0.5, 6, previous=4) == 4  # centre keeps the pick


def test_draw_focus_states_and_stubs():
    ov = overlay()
    for view in (
        ViewState(mode="focus", level="word", focus=Hit(0, 3), ops=OpsView(kind="ring", picked=2, pointing=True)),
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
    panel_h, _ = ov._focus_panel(view)
    assert panel_h > ov.box_h
    ov.draw(np.full((720, 1280, 3), 128, np.uint8), view)
