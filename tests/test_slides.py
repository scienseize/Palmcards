"""Slides from a PDF (palmcards.slides): their text as notes, their pages drawn."""

import numpy as np

from palmcards.notes import notes_from_bytes
from palmcards.slides import Deck, page_texts, slide_of
from tests.pdf import make_pdf

DECK = [["Why now", "• Revenue up 20%", "- Costs flat", "3"], [], ["Thank you"]]


def test_each_page_has_its_lines_without_bullets_or_page_numbers():
    assert page_texts(make_pdf(DECK)) == [["Why now", "Revenue up 20%", "Costs flat"], [], ["Thank you"]]


def test_a_pdf_read_as_notes_is_a_section_per_slide_with_text():
    notes = notes_from_bytes(make_pdf(DECK), "deck.pdf")
    assert [s.title for s in notes.sections] == ["Slide 1", "Slide 3"]  # page 2 has no text
    said = [[" ".join(w.text for w in x.words) for x in s.sentences] for s in notes.sections]
    assert said == [["Why now", "Revenue up 20%", "Costs flat"], ["Thank you"]]  # a line each
    assert [slide_of(s.title) for s in notes.sections] == [0, 2]
    assert slide_of("Introduction") is None


def test_pages_are_drawn_upright_at_the_width_asked():
    deck = Deck(make_pdf(DECK), 480)
    deck._thread.join()
    assert deck.ready and deck.pages == 3 and deck.page(3) is None
    page = deck.page(0)
    assert page.shape == (270, 480, 3)  # 16:9
    ink = np.where((page.mean(axis=2) < 128).any(axis=1))[0]
    assert ink.min() < 270 // 3  # the title is at the top: not drawn upside down
    assert (deck.page(1).mean(axis=2) > 200).all()  # an empty page is the page's colour


def test_a_deck_of_images_only_says_how_to_use_it():
    import pytest
    with pytest.raises(ValueError, match="add the slides from the home card"):
        notes_from_bytes(make_pdf([[], []]), "photos.pdf")
