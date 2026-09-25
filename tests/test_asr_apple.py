from palmcards.asr_apple import PositionAgreement


def test_confirms_what_two_partials_share():
    a = PositionAgreement()
    assert a.update("good evening".split()) == []
    assert a.update("good evening every".split()) == [(0, "good"), (1, "evening")]
    assert a.update("good evening everyone thank".split()) == []  # "every" became "everyone"
    assert a.update("good evening everyone thank you".split()) == [(2, "everyone"), (3, "thank")]


def test_a_word_revised_in_earlier_does_not_shift_the_confirmed_point():
    a = PositionAgreement()
    a.update("good evening everyone".split())
    a.update("good evening everyone thank".split())
    assert a.confirmed == ["good", "evening", "everyone"]
    # A word appears before the confirmed point: found again by "everyone".
    assert a.update("good evening to everyone thank you".split()) == [(4, "thank")]
    assert a.update("good evening to everyone thank you for".split()) == [(5, "you")]


def test_a_new_utterance_after_a_pause_starts_over():
    a = PositionAgreement()
    a.update("our heads are very forgiving".split())
    a.update("our heads are very forgiving".split())
    assert len(a.confirmed) == 5 and a.utterance == 0
    assert a.update("so we".split()) == []
    assert a.utterance == 1 and a.confirmed == []
    assert a.update("so we built".split()) == [(0, "so"), (1, "we")]


def test_a_revised_opening_word_is_not_a_new_utterance():
    a = PositionAgreement()
    a.update("evening everyone thank".split())
    a.update("evening everyone thank you".split())
    # Same utterance: "you" is confirmed at its shifted position.
    assert a.update("good evening everyone thank you for".split()) == [(4, "you")]
    assert a.utterance == 0


def test_a_final_transcript_is_confirmed_whole():
    a = PositionAgreement()
    a.update("check against your".split())
    assert a.update("check against your own voice".split(), final=True) == \
        [(0, "check"), (1, "against"), (2, "your"), (3, "own"), (4, "voice")]
