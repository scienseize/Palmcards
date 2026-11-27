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


def test_the_presenter_layout_puts_the_slide_left_and_the_mirror_right_sharp():
    from palmcards.render import Presenter, ViewState
    from palmcards.style import LAYOUT

    notes = notes_from_bytes(make_pdf(DECK), "deck.pdf")
    p = Presenter(notes.sentences, (1280, 720))
    assert p.size == (2560, 1440)  # twice the camera's frame: a Retina screen shows it at its own pixels
    x0, x1 = p.slide_x
    mx0, my0, mx1, my1 = p.mirror
    assert x0 < x1 < mx0 < mx1 <= 2560 and p.slide_top == my0 and my1 <= 1440
    crop_x, crop_w = p.crop  # the mirror shows the face zone, not the room
    assert crop_x <= LAYOUT.face[0] * 1280 and crop_x + crop_w >= LAYOUT.face[1] * 1280
    page = Deck(make_pdf(DECK), p.slide_width).page(0)
    camera = np.full((720, 1280, 3), 90, np.uint8)
    out = p.compose(camera, ViewState(app="rehearse", slide=page, slide_label="1 / 4"))
    assert out.shape == (1440, 2560, 3)
    assert (out[p.slide_top + 40, x0 + 40:x1 - 40] > 200).all()  # the slide's paper, where it should be
    assert abs(int(out[(my0 + my1) // 2, (mx0 + mx1) // 2, 0]) - 90) < 5  # the camera, in the mirror
