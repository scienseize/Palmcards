from palmcards import fade


def test_a_word_fades_with_the_takes_that_said_it_in_a_row():
    fade._demo()


def test_a_take_that_missed_a_word_starts_its_run_again():
    hist = [{0: (True,)}, {0: (True,)}, {0: (False,)}]
    assert fade.levels(hist).get((0, 0)) is None


def test_a_take_that_did_not_say_the_sentence_neither_counts_nor_breaks_the_run():
    hist = [{0: (True,)}, {1: (True,)}, {0: (True,)}]
    assert fade.levels(hist)[(0, 0)] == 2


def test_a_prompt_crosses_into_the_next_sentence():
    hidden = {(0, 2), (1, 0), (1, 1), (1, 2)}
    order = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
    assert fade.prompt(hidden, order, (0, 2), 2) == [(1, 0), (1, 1)]
