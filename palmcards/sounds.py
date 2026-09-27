"""Soft sound cues in Prepare and Review (optional: preferences `sounds`).

A focus, backing out, a commit (pinch + lift), and the options ring stepping
onto the next node each have a quiet macOS sound (style.SOUND), played on the
frame the event is handled so the sound and what the screen shows arrive
together. Never from the count-in on (a take records the microphone), never
while something plays (a take's clip, "hear it"): the cues would be heard in it.

NSSound plays on its own, apart from sounddevice, so stopping a clip never
cuts a cue and a cue never cuts a clip. Without AppKit it plays nothing.
"""

from __future__ import annotations

import math
from typing import Callable

from palmcards.style import SOUND

CUE_EVENTS = {"focus": "focus", "back": "back", "commit": "commit"}  # grammar event kind -> cue


def nssound_player() -> Callable[[str], None]:
    """Plays a cue by name with macOS's NSSound (style.SOUND); does nothing without AppKit."""
    try:
        from AppKit import NSSound
    except Exception:
        return lambda name: None
    sounds: dict[str, object] = {}

    def play(name: str) -> None:
        if name not in sounds:
            system, volume = SOUND.cues[name]
            sound = NSSound.soundNamed_(system)
            if sound is not None:
                sound = sound.copy()  # its own volume, apart from other cues using the same sound
                sound.setVolume_(volume)
            sounds[name] = sound
        sound = sounds[name]
        if sound is not None:
            sound.stop()  # a cue again before the last one ended starts it over
            sound.play()

    return play


class Cues:
    def __init__(self, play: Callable[[str], None] | None = None):
        self._play = play or nssound_player()
        self._step_t = -math.inf

    def react(self, events, mode: str, playing: bool, ring_stepped: bool, t: float) -> list[str]:
        """Play the cues for this frame's events; returns their names."""
        if mode not in ("prepare", "review") or playing:
            return []
        names = [CUE_EVENTS[ev.kind] for ev in events if ev.kind in CUE_EVENTS]
        if ring_stepped and t - self._step_t >= SOUND.step_gap_s:
            names.append("step")
            self._step_t = t
        for name in names:
            self._play(name)
        return names
