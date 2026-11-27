"""Slides from a PDF (palmcards.slides): their titles as the outline, their pages drawn."""

import numpy as np

from palmcards.notes import notes_from_bytes
from palmcards.slides import Deck, page_titles
from tests.pdf import make_pdf

# make_pdf sets each page's first line largest (40 pt), the others at 26 pt.
DECK = [["Why now", "HUAWEI ICT COMPETITION", "Every patient, read against their own", "baseline."],
        [],
        ["127,000 ~90%", "Too few nurses on the ward", ("a caption", 12)],
        ["Thank you. Questions?"]]


def test_a_slide_title_is_its_largest_text_that_is_words():
    # The eyebrow, the wrapped lines and the caption are left out; a slide of figures sets its numbers
    # largest, so its title is the largest text that is words; a page of images has none.
    assert page_titles(make_pdf(DECK)) == ["Why now", None, "Too few nurses on the ward", "Thank you. Questions?"]


def test_a_pdf_read_as_notes_is_its_outline_a_paragraph_per_slide():
    notes = notes_from_bytes(make_pdf(DECK), "deck.pdf")
    assert len(notes.sections) == 1  # one outline: the take shows all of it, the clicker moves the orange row
    assert [(s.paragraph, s.text) for s in notes.sentences] == [
        (0, "Why now"), (1, "Slide 2"), (2, "Too few nurses on the ward"), (3, "Thank you."), (3, "Questions?")]


def test_pages_are_drawn_upright_at_the_width_asked():
    deck = Deck(make_pdf(DECK), 480)
    assert deck.pages == 4 and deck.page(9).shape == deck.page(3).shape  # past the end: the last page
    page = deck.page(0)
    assert page.shape == (270, 480, 3)  # 16:9
    ink = np.where((page.mean(axis=2) < 128).any(axis=1))[0]
    assert ink.min() < 270 // 3  # the title is at the top: not drawn upside down
    assert (deck.page(1).mean(axis=2) > 200).all()  # an empty page is the page's colour


def test_the_presenter_layout_puts_the_slide_left_and_the_column_right_sharp():
    from palmcards.render import Presenter, ViewState

    notes = notes_from_bytes(make_pdf(DECK), "deck.pdf")
    p = Presenter(notes.sentences, (1280, 720))
    assert p.size == (2560, 1440)  # twice the camera's frame: a Retina screen shows it at its own pixels
    x0, x1 = p.take_slide_x  # a take: the slide large from the left margin, a face cam bottom right
    mx0, my0, mx1, my1 = p.take_mirror
    y0 = p.take_slide_y
    assert x0 < x1 < mx0 < mx1 <= 2560 and y0 < my0 and my1 == y0 + round((x1 - x0) * 9 / 16) <= 1440  # level feet
    assert abs((mx1 - mx0) / (my1 - my0) - 1280 / 720) < 0.01  # the whole frame, nothing cropped
    assert x0 == p.slide_x[0] and x1 - x0 > p.slide_x[1] - p.slide_x[0]  # larger than Prepare's
    assert p.slide_width == x1 - x0  # pages are drawn at the take's width, never scaled up
    page = Deck(make_pdf(DECK), p.slide_width).page(0)
    camera = np.full((720, 1280, 3), 90, np.uint8)
    camera[:, :40] = 250  # the frame's left edge: shown too (the old tall crop cut it off)
    nxt = np.zeros_like(page)
    nxt[:] = (40, 160, 40)  # the next slide: green, to find it
    out = p.compose(camera, ViewState(app="rehearse", slide=page, next_slide=nxt, slide_label="1 / 4",
                                      status="Slide 1 of 4"))
    assert out.shape == (1440, 2560, 3)
    assert (out[y0 + 40, x0 + 40:x1 - 40] > 200).all()  # the slide's paper, where it should be
    column = out[y0:my0, mx0:mx1]
    assert ((column[..., 1] > 140) & (column[..., 0] < 60)).sum() > 1000  # the next slide, previewed in the column
    assert column.max() > 230 and out[:y0, :x1].max() < 60  # the label in the column, none above the slide
    assert abs(int(out[(my0 + my1) // 2, (mx0 + mx1) // 2, 0]) - 90) < 5  # the camera, in the face cam
    assert out[(my0 + my1) // 2, mx0 + 6, 0] > 200  # its left edge too: everything shows


def test_a_slide_plays_from_when_it_came_up_to_when_the_clicker_moved_on():
    from palmcards.playback import slide_span
    from palmcards.session import TakeRecord

    take = TakeRecord(number=2, wav="take-02.wav", started="", t_start=100.0, duration_s=60.0, sample_rate=16000,
                      peak=0.5, slides=[[100.0, 0], [112.5, 1], [140.0, 2], [150.0, 1]])
    assert slide_span(take, 0) == (100.0, 112.5)
    assert slide_span(take, 1) == (112.5, 140.0)  # its first time up
    assert slide_span(take, 2) == (140.0, 150.0)
    assert slide_span(take, 5) is None  # never shown
    last = TakeRecord(number=3, wav="take-03.wav", started="", t_start=0.0, duration_s=30.0, sample_rate=16000,
                      peak=0.5, slides=[[0.0, 0], [20.0, 1]])
    assert slide_span(last, 1) == (20.0, 30.0)  # up until the take ended


def test_the_teleprompter_sits_under_the_camera_with_the_sentence_being_said_on_top():
    from palmcards.notes import parse_text
    from palmcards.render import PanelMotion, Teleprompter, ViewState

    notes = parse_text("One two three four five six seven. " * 12)
    tp = Teleprompter(notes.sentences, (1280, 720))
    assert tp.y == 0 and abs(tp.x + tp.box_w / 2 - 640) <= tp.pad  # top centre: where the camera is
    assert tp.x > 0.36 * 1280  # clear of the label, top left
    state = ViewState(app="rehearse", section=0, current=6, panel_motion=PanelMotion())
    panel = tp.panel(state)
    assert tp.scroll_for(state) == panel.rows[6][0]  # its first row on the top row


def test_the_notch_teleprompter_starts_its_rows_under_the_notch():
    from palmcards.notes import parse_text
    from palmcards.notch import NotchPanel

    notes = parse_text("One two three four five six seven. " * 12)
    panel = NotchPanel(notes.sentences, (751.0, 959.0, 37.5, 1112.0, 2.0))  # this MacBook's notch, 2x
    p = panel.prompter
    assert p.top == 75 and p._panel_top(0) == 75 + p.pad  # the first row under the camera's 37.5 points
    gx0, gx1, gy1 = panel._crop
    assert 280 <= (gx1 - gx0) / 2 <= 500  # as wide as Textream's, in points
    assert panel._mask[0].all() and not panel._mask[-1, 0]  # straight across the top, bottom corners round


def test_spoken_words_scroll_inside_one_sentence_without_rebuilding_the_panel():
    import numpy as np
    from palmcards.notes import parse_text
    from palmcards.render import Teleprompter, ViewState

    notes = parse_text(" ".join(["Reading the next few words"] * 12) + ".")
    tp = Teleprompter(notes.sentences, (1280, 720))
    state = ViewState(app="rehearse")
    panel = tp.panel(state)
    first = tp.scroll_for(state)
    state.spoken = (0, 24)
    assert tp.panel(state) is panel
    right, row, bottom = panel.word_edges[state.spoken]
    assert tp.scroll_for(state) == row > first
    height = bottom - row
    original = panel.color[row:bottom].copy()
    drawn, _ = tp._panel_view(panel, row, height, state.spoken)
    assert np.any(original[:, :right]) and np.allclose(drawn[:, :right], original[:, :right] * .65)
    assert np.array_equal(original[:, right:], drawn[:, right:])
    assert np.array_equal(original, panel.color[row:bottom])
