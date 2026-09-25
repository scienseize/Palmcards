from palmcards.asr import LiveWord
from palmcards.follow import FollowEvent, Follower
from palmcards.notes import parse_text

NOTES = parse_text("""# One

Alpha bravo charlie delta echo. Foxtrot golf hotel india juliet. Kilo lima mike november oscar.

# Two

Papa quebec romeo sierra tango. Uniform victor whiskey xray yankee.
""", "md")


class Speaker:
    """Says words one at a time, 0.4 s apart, each confirmed 1 s after it ends."""

    def __init__(self, follower: Follower):
        self.f, self.t, self.events = follower, 0.0, []

    def say(self, text: str) -> list[FollowEvent]:
        got = []
        for word in text.split():
            w = LiveWord(word, self.t, self.t + 0.3, 0.9, confirmed_at=round(self.t + 1.3, 3))
            self.t += 0.4
            got += self.f.update([w])
        self.events += got
        return got


def moves(events: list[FollowEvent]) -> list[tuple[str, int]]:
    return [(e.kind, e.index) for e in events]


def test_sections_and_sentences():
    assert [s.section for s in NOTES.sentences] == [0, 0, 0, 1, 1]
    f = Follower(NOTES)
    assert (f.section, f.sentence) == (0, 0)
    assert f.scope() == [0, 1, 2, 3, 4]  # the next section's opening: 5 words, under 8, so its next sentence too
    assert Follower(NOTES, section=1).scope() == [3, 4]


def test_straight_read_follows_sentences_and_advances_the_section():
    f = Follower(NOTES)
    sp = Speaker(f)
    sp.say("alpha bravo")
    assert moves(sp.events) == []
    # A run reading on into the next sentence moves there at its first word.
    assert moves(sp.say("charlie delta echo foxtrot")) == [("sentence", 1)]
    sp.say("golf hotel india juliet kilo lima mike november oscar papa quebec")
    assert moves(sp.events) == [("sentence", 1), ("sentence", 2)]
    got = sp.say("romeo")
    assert moves(got) == [("section", 1), ("sentence", 3)]
    assert got[0].source == "voice" and got[0].t == round(sp.t - 0.4 + 1.3, 3)  # when "romeo" was confirmed
    assert moves(sp.say("sierra tango uniform victor whiskey")) == [("sentence", 4)]


def test_fillers_do_not_break_a_run():
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie delta echo")
    assert moves(sp.say("uh foxtrot")) == [("sentence", 1)]
    assert moves(sp.say("golf um hotel india juliet uh kilo")) == [("sentence", 2)]


def test_a_dropped_or_misheard_word_does_not_break_a_run():
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie delta echo")
    assert moves(sp.say("foxtrot hotel")) == [("sentence", 1)]  # "golf" lost: echo foxtrot hotel
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie delta echo")
    assert moves(sp.say("foxtrot gulp hotel")) == [("sentence", 1)]  # "golf" misheard
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo")
    assert sp.say("wait no kilo gosh darn lima") == []  # two odd words between: not a run


def test_going_off_script_stalls():
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie")
    assert sp.say("so anyway the weather today is lovely and warm isn't it") == []
    assert (sp.f.section, sp.f.sentence) == (0, 0)


def test_a_stray_match_does_not_jump():
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie")
    # Lone note words ahead, in ad-libs: the later sentence, then the next section.
    assert sp.say("I said mike to him") == []
    assert sp.say("and then papa quebec went home") == []
    assert (sp.f.section, sp.f.sentence) == (0, 0)


def test_moving_back_needs_five_words():
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike")
    assert sp.f.sentence == 2
    assert sp.say("foxtrot golf hotel india") == []  # re-read four words of sentence 1
    assert sp.f.sentence == 2
    assert moves(sp.say("juliet")) == [("sentence", 1)]  # the fifth
    # ... and on from there as usual.
    assert moves(sp.say("kilo lima mike")) == [("sentence", 2)]


def test_sections_never_move_back():
    sp = Speaker(Follower(NOTES))
    sp.say("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar")
    sp.say("papa quebec romeo sierra")
    assert sp.f.section == 1
    sp.say("alpha bravo charlie delta echo foxtrot golf hotel")
    assert (sp.f.section, sp.f.sentence) == (1, 3)


def test_flick_is_the_manual_override():
    f = Follower(NOTES)
    sp = Speaker(f)
    sp.say("alpha bravo")
    got = f.flick(9.0)
    assert moves(got) == [("section", 1), ("sentence", 3)]
    assert all(e.source == "flick" and e.t == 9.0 for e in got)
    assert f.tail == []
    assert moves(sp.say("uniform victor whiskey")) == [("sentence", 4)]
    assert f.flick(10.0) == []  # the last section
