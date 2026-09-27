from palmcards.gestures import GestureEvent
from palmcards.sounds import Cues
from palmcards.style import SOUND


def cues():
    played = []
    return Cues(played.append), played


def ev(kind):
    return GestureEvent(kind, 0.0)


def test_a_cue_for_a_focus_backing_out_and_a_commit():
    c, played = cues()
    assert c.react([ev("focus")], "prepare", False, False, 1.0) == ["focus"]
    c.react([ev("pose"), ev("back")], "review", False, False, 2.0)
    c.react([ev("commit")], "prepare", False, False, 3.0)
    assert played == ["focus", "back", "commit"]


def test_never_during_a_take_or_while_something_plays():
    c, played = cues()
    for mode in ("count_in", "rehearse"):
        c.react([ev("focus"), ev("commit")], mode, False, True, 1.0)
    c.react([ev("back")], "review", True, True, 2.0)  # a take's clip or "hear it" playing
    assert played == []


def test_the_rings_steps_tick_at_most_so_often():
    c, played = cues()
    t = 10.0
    for i in range(10):  # a fast turn: a step every frame
        c.react([], "prepare", False, True, t + i / 30)
    gap = SOUND.step_gap_s
    assert 0 < played.count("step") <= int(9 / 30 / gap) + 1
    assert played.count("step") < 10
