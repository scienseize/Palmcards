import pytest

from palmcards.asr import LiveWord
from palmcards.follow import FollowEvent, Follower
from palmcards.notes import parse_text

NOTES = parse_text("""# One

Alpha bravo charlie delta echo. Foxtrot golf hotel india juliet. Kilo lima mike november oscar.

# Two

Papa quebec romeo sierra tango. Uniform victor whiskey xray yankee.
""", "md")


class Speaker:
    """Says words one at a time, 0.4 s apart, each confirmed 0.7 s after it ends
    (the live pipeline's measured median lag)."""

    def __init__(self, follower: Follower):
        self.f, self.t, self.events = follower, 0.0, []

    def say(self, text: str) -> list[FollowEvent]:
        got = []
        for word in text.split():
            w = LiveWord(word, self.t, self.t + 0.3, 0.9, confirmed_at=round(self.t + 1.0, 3))
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
    assert moves(sp.say("alpha bravo charlie")) == []
    # The live words lag the voice: when "delta" is confirmed the speaker is saying "echo",
    # the sentence's last word, so the highlight goes on to the next sentence now.
    assert moves(sp.say("delta")) == [("sentence", 1)]
    assert moves(sp.say("echo foxtrot")) == []  # finishing sentence 0 doesn't pull it back
    sp.say("golf hotel india juliet kilo lima mike november oscar papa quebec")
    # Across the section's end too: "papa" lights up in the preview while "oscar" is said;
    # the recorded section waits for its opening words to be confirmed.
    assert moves(sp.events) == [("sentence", 1), ("sentence", 2), ("sentence", 3)]
    got = sp.say("romeo")
    assert moves(got) == [("section", 1)]
    assert got[0].source == "voice" and got[0].t == round(sp.t - 0.4 + 1.0, 3)  # when "romeo" was confirmed
    assert moves(sp.say("sierra")) == [("sentence", 4)]
    assert moves(sp.say("tango uniform victor whiskey xray yankee")) == []  # the last sentence: nowhere to go


def test_fillers_do_not_break_a_run():
    sp = Speaker(Follower(NOTES))
    assert moves(sp.say("alpha um bravo uh charlie delta")) == [("sentence", 1)]
    assert moves(sp.say("echo foxtrot golf um hotel uh india")) == [("sentence", 2)]


def test_a_dropped_or_misheard_word_does_not_break_a_run():
    sp = Speaker(Follower(NOTES))
    assert moves(sp.say("alpha bravo delta")) == [("sentence", 1)]  # "charlie" lost
    sp = Speaker(Follower(NOTES))
    assert moves(sp.say("alpha bravo chorley delta")) == [("sentence", 1)]  # "charlie" misheard
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
    sp.say("papa quebec romeo")
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


PITCH = parse_text("""# Problem

Most founders rehearse their pitch silently in their heads. They walk into the room having never said it aloud.

# Product

PalmCards puts your notes beside your mirrored camera image. Each take measures pace, fillers and pauses.
""", "md")


def test_a_sentence_in_other_words_takes_the_highlight():
    f = Follower(PITCH)
    sp = Speaker(f)
    sp.say("most founders rehearse their pitch")
    assert (f.section, f.sentence) == (0, 0)
    # Sentence 1 reworded: "never", "said", "aloud", "walk", "room" in another order.
    got = moves(sp.say("so they actually walk in never having said a word of it aloud in the room"))
    assert ("sentence", 1) in got and f.sentence == 1 and f.section == 0


def test_a_reworded_opening_moves_the_section():
    f = Follower(PITCH)
    sp = Speaker(f)
    sp.say("most founders rehearse their pitch silently in their heads")
    got = moves(sp.say("with this the camera image is mirrored and your notes float beside it"))
    assert got[0] == ("section", 1) and f.sentence == 2


def test_a_stray_shared_word_does_not_move_it():
    f = Follower(PITCH)
    sp = Speaker(f)
    sp.say("most founders rehearse their pitch")
    assert moves(sp.say("and honestly the camera is scary for many people")) == []
    assert (f.section, f.sentence) == (0, 0)


def test_going_on_in_other_words_after_a_handoff_does_not_pull_it_back():
    f = Follower(PITCH)
    sp = Speaker(f)
    sp.say("most founders rehearse their pitch silently in their heads")
    assert f.sentence == 1  # handed on as the sentence ended
    # "the" reads close to "They" and ended the handoff; the old run then pulled it back to 0.
    sp.say("with this the camera")
    assert f.sentence >= 1


def test_auto_stop_waits_for_the_end_then_for_quiet():
    from palmcards.config import REHEARSE
    from palmcards.follow import AutoStop

    stop, t = AutoStop(), 0.0
    for _ in range(90):  # 3 s of speech at 30 fps, not yet at the end
        assert not stop.update(t, 0.75, at_end=False)
        t += 1 / 30
    for _ in range(150):  # quiet, but not at the end: never stops
        assert not stop.update(t, 0.15, at_end=False)
        t += 1 / 30
    assert stop.heard_s == pytest.approx(3.0, abs=0.1)
    stopped_at = None
    for _ in range(150):  # at the end, quiet
        if stop.update(t, 0.15, at_end=True):
            stopped_at = t
            break
        t += 1 / 30
    assert stopped_at is not None and 0 < stop.progress == 1.0


def test_auto_stop_speech_starts_the_quiet_over():
    from palmcards.config import REHEARSE
    from palmcards.follow import AutoStop

    stop, t = AutoStop(), 0.0
    for level in [0.75] * 30 + [0.15] * 60:  # 2 s of quiet at the end: not yet
        assert not stop.update(t, level, at_end=True)
        t += 1 / 30
    assert stop.progress > 0.5
    assert not stop.update(t, 0.8, at_end=True) and stop.progress == 0.0  # a word: over again
    t += 1 / 30
    for _ in range(int(REHEARSE.end_silence_s * 30) - 5):
        assert not stop.update(t, 0.15, at_end=True)
        t += 1 / 30


def test_auto_stop_works_over_a_noisy_room():
    from palmcards.follow import AutoStop

    stop, t = AutoStop(), 0.0
    for level in [0.45, 0.8] * 60:  # room noise at 0.45 (-33 dBFS), speech at 0.8
        stop.update(t, level, at_end=False)
        t += 1 / 30
    got = False
    for _ in range(120):
        got = got or stop.update(t, 0.47, at_end=True)  # the room's noise only: quiet
        t += 1 / 30
    assert got
