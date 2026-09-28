"""PalmCards entry point.

  python main.py [NOTES_FILE] [--lang xx] [--trace] [--no-follow] [--llm off|ollama|anthropic]
  python main.py --open RUN      reopen a saved session (a folder, or its name under sessions/) in Review
  python main.py --gaze-check    every take is a gaze check: timed prompts (camera, notes, away), then it
                                 stops itself; scripts/evaluate.py --gaze RUN compares them with the classifier
      NOTES_FILE: .txt, .md or .docx; defaults to the sample
      --lang: language spoken in the takes, for Whisper (default en)
      --trace: also record every hand result's landmarks, for offline replay
      --llm: the optional LLM for word meanings, alternatives and rewrites (default: off,
             or config LLM.provider). "anthropic" is the cloud: the sentence or paragraph you ask
             about leaves the Mac (ANTHROPIC_API_KEY from the environment or .env); a CLOUD LLM
             chip shows while it is on. Each call's tokens: python -m palmcards.llm usage

Mirrored webcam feed with the notes overlaid in the demo style. Prepare mode
follows Kat's gesture grammar (see CLAUDE.md). Hold your hand in the box on
the right of the frame; it steers the highlight in the text on the left.

  one finger up / two fingers together / flat hand
                           browse by word / sentence / paragraph
  top or bottom of the box scroll
  pinch (word), fold fingers onto the thumb (sentence, paragraph)
                           focus; a word shows its meaning (with --llm)
  open palm (word)         options ring: the word and alternatives (with --llm);
                           make an L and turn it like a knob: one option per ~15 degrees, tilting right
                           turns the ring clockwise; the picked word previews in the sentence
  L-hand tilt (sentence)   live wording preview: cold / original / warm
  hold open palm (sentence) hear the selected sentence (~0.6 s; also key a); a new open palm
                           stops it, and the focus is held while it speaks
  two L-hands (paragraph)  live length preview: about 70% / original / 130%
  pinch + lift             commit: an alternative makes a new notes revision (u undoes it);
                           tone and length commit only the complete preview visible for the selected target;
                           a loading preview stays open; after an error pinch + lift retries
  drop the hand for 1 s    back out
  fist raised into view, held 1 s
                           start a take after a 3-2-1 count-in. The session's first
                           count-in also calibrates the eyes: look into the camera
                           for 2.5 s, then read the orange sentence during the 3-2-1

Rehearse listens for one gesture only, anywhere in the frame:
  OK sign held 1.5 s       stop the take (or cancel the count-in), on to Review: thumb and
                           index touching in a circle, middle, ring and pinky up
Everything else your hands do while you speak is only measured. The notes
follow your voice (the current sentence in orange, the next section shown
faint as you start the last sentence of one); n, b, j and k move by hand
and the voice carries on from there. Off with --no-follow.

Review browses and focuses like Prepare, without Prepare's operations and
without the word level: one finger browses sentences too. The label's last
line lists the gestures that act.
Each take is transcribed and measured in the background as soon as it
stops; the label shows TRANSCRIBING, then the take's pace and fillers, and
the terminal prints the full report. Nothing is judged: the takes are set
side by side.

  two fingers together (or one finger)
                           browse sentences; the take table (the last full takes side by side:
                           pace, fillers, long pauses, restarts, pitch range, gaze, posture)
  fold onto the thumb (pinch, from one finger)
                           focus: the sentence in every take that said it (pace, fillers,
                           pitch range, on screen)
  flat hand, then fold     focus a paragraph: the newest full take that said it (sentences
                           said, time from first word to last, pace, fillers)
  L-hand, then point (focused)
                           the takes that said this sentence, as chips beside it: point at one
  pinch + lift (focused)   drill the sentence: count-in, then just that
                           sentence; the OK sign held to stop
  open palm on a focused sentence or paragraph, held ~0.6 s
                           play it from the take it shows (key: a); a take recorded with
                           video (--video) replays it full frame, as others see you; while it
                           plays the focus is held (drop the hand freely) and a new open palm,
                           ~0.3 s, stops it
  fist raised, held 1 s    new full take
  V sign held 1 s          back to Prepare, to edit before the next take (in Prepare, with an
                           edit made, a V sign held undoes it)

Without --llm, tone and length preview their controls and say they need the
optional LLM; nothing is ever sent unless you ask. Poses and events are
logged to sessions/gesture-logs/; each take is saved as a WAV in its session
folder under sessions/, with its transcript, pitch and loudness, and
session.json (the metrics).

Keys, the fallback when gestures won't do (h shows them in the app):
  t start a take, x stop playback (else the take, or cancel the count-in), n next section, b previous section,
  p back to Prepare from Review, space/j next sentence, k previous (in
  Rehearse within the section; in a focused panel they scroll it),
  a hear the sentence (Prepare) / play the sentence or paragraph (Review), again to stop, u undo the last edit (Prepare), r retry failed analysis,
  e calibrate the eyes again at the next take,
  g the gesture tutorial (Enter skips a step), c high contrast, h keys, q/Esc quit,
  m flip a take's replayed video: mirrored, or as others see you (the default).
Preferences (hand reach, hold times, contrast): python -m palmcards.prefs
Dev keys: d toggle landmarks and hand box, s save a screenshot to sessions/screens/.
"""

import argparse
import json
import math
import secrets
import sys
import time
import traceback
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2

from palmcards.align import counts
from palmcards.capture import AudioRecorder, Camera, CameraError
from palmcards.config import ANALYSIS, BODY, FOLLOW, LLM, RECORDING, REHEARSE, SPEECH
from palmcards import features, gaze
from palmcards import prefs as preferences
from palmcards import render
from palmcards.gestures import FIST, OPEN, THUMB_UP, GestureEvent, GestureLog, Grammar, HandTracker, ModeMachine
from palmcards.tutorial import Tutorial
from palmcards.notes import Notes, notes_from_bytes
from palmcards.edit import replace_text, replace_word
from palmcards.preview import Previews
from palmcards.llm import PROVIDERS, Assistant, LLMUnavailable, alternatives_request, describe, get_provider, \
    meaning_request, parse_meaning, parse_alternatives, parse_rewrite, rewrite_request
from palmcards.render import (
    UNDO_HINT, FocusMotion, Hit, OpsView, PanelMotion, TextOverlay, ViewState,
    draw_fingertips, draw_hand_area, draw_hand_box, draw_landmarks, draw_replay_bar, draw_replay_caption, draw_replay_hint,
    draw_stats,
)
from palmcards.motion import Spring
from palmcards.review import Board
from palmcards.sounds import Cues
from palmcards.pick import Picker
from palmcards.playback import ClipPlayer, Playback, clip_marks, clip_span, replay_words, span_clip
from palmcards.video import VideoReader, VideoWriter
from palmcards.video import unavailable as video_unavailable
from palmcards.recording import TakeWriter
from palmcards.revisions import from_snapshot
from palmcards.session import SESSIONS_DIR, Session, SessionError, recover_all
from palmcards.style import TEXT
from palmcards.tts import get_speaker
from palmcards.analysis import Supervisor
from palmcards.asr import get_recognizer
from palmcards.follow import LiveFollow
from palmcards.speech import make_job
from palmcards.vision import Watcher

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
SCREENS_DIR = SESSIONS_DIR / "screens"
WINDOW = "PalmCards"
NOTE_S = 1.5  # how long a commit message stays in the label
LLM_NOTE_S = 3.0  # an LLM answer or failure arrives while you're doing something else: it stays longer
HINT_EVERY_S = 6.0  # a hint about a gesture that didn't act is shown at most this often
CAPTION_LINGER_S = 0.15  # a replay's caption still marks a word as being said this long after it ends
ENTER = 13


def hear_words(overlay: TextOverlay, sentence: int) -> list[str]:
    return [w.text for w in overlay.sentences[sentence].words]


def hear_sentence(sentence: int, overlay: TextOverlay, speaker, log: GestureLog, t: float,
                  playback: Playback | None = None, player=None, words: list[str] | None = None) -> str:
    """Speak a sentence: the speaker's rendered audio played as a clip (its
    progress exact; rendered from when the sentence was focused), or, with a
    speaker that can't render, `say` live. `words`: the wording to speak, if not
    the sentence's (a tone preview on screen)."""
    words = words or hear_words(overlay, sentence)
    target = ("prepare", (sentence,), None)
    try:
        if playback is not None and player is not None and hasattr(speaker, "render_async"):
            playback.start_rendered(player, speaker, words, target, time.perf_counter())
        elif playback is not None:
            playback.start_say(speaker, words, target, time.perf_counter())
        else:
            speaker.say_words(words)
    except OSError as exc:
        print(f"text to speech failed: {exc}", file=sys.stderr)
        return "COULD NOT SPEAK (SEE TERMINAL)"
    log(t, "hear", sentence=sentence)
    return "SPEAKING SENTENCE"


def play_focus(view: ViewState, overlay: TextOverlay, log: GestureLog, speaker, takes: "Takes | None",
               t: float) -> str:
    """Hear the focused sentence (Prepare), or play the focused sentence or
    paragraph from the take it shows (Review); the `a` key does the same with
    nothing focused on the current sentence. Returns a label note."""
    sentence = view.focus.sentence if view.focus is not None else view.current
    if view.app == "prepare":
        # A tone preview on screen is what is heard: the wording shown, not the notes'.
        preview = view.edit_preview
        words = preview.text.split() if preview is not None and not preview.original and preview.text else None
        if takes is None:
            return hear_sentence(sentence, overlay, speaker, log, t, words=words)
        return hear_sentence(sentence, overlay, speaker, log, t, takes.playback, takes.clip_player(), words)
    if takes is None:
        return ""
    if view.mode == "focus" and view.level == "paragraph":
        return takes.play_paragraph(overlay.unit("paragraph", sentence))
    return takes.play_sentence(sentence)


def play_target(view: ViewState, overlay: TextOverlay, board: Board) -> tuple | None:
    """What the screen would play now: (app, sentences, take). Playback stops
    when this no longer matches what plays: another take picked, the hover
    moved to another sentence, the focus gone, another mode."""
    if view.app not in ("prepare", "review"):
        return None
    focused = view.mode == "focus" and view.focus is not None
    sentence = view.focus.sentence if focused else view.current
    if view.app == "prepare":
        return "prepare", (sentence,), None
    if focused and view.level == "paragraph":
        unit = tuple(overlay.unit("paragraph", sentence))
        return "review", unit, board.paragraph_shown(unit)
    return "review", (sentence,), board.shown(sentence)


FIST_HINT = "NEW TAKE: DROP THE HAND, RAISE\u00a0A\u00a0FIST"  # it wraps at the comma, not before "A FIST"
PALM_HINT = {"rehearse": "A PALM DOESN'T STOP A TAKE", "count_in": "A PALM DOESN'T CANCEL"}
THUMB_HINT = {"rehearse": "A THUMBS-UP DOESN'T STOP A TAKE", "count_in": "A THUMBS-UP DOESN'T CANCEL"}
HINT_DWELL_S = 0.6  # a gesture that does nothing held this long before its hint (a fist formed from
#                     another pose: a fold or a slow pinch passes through a fist on its way to a focus;
#                     an open palm in a take)
FIST_HINT_S = HINT_DWELL_S


def nonactivation_hint(mode: str, gs, open_s: float, t: float | None = None) -> str:
    """Why a gesture the camera sees is not doing anything, when that's likely
    to puzzle: a fist formed from another pose and held; in a take or count-in,
    an open palm or a thumbs-up held (each stopped takes once; the label's hint
    row already says what does now)."""
    p = gs.primary
    if p is None:
        return ""
    if mode in ("prepare", "review") and p.stable == FIST and p.first_pose != FIST and gs.mode != "focus" \
            and (t is None or p.held(t) >= FIST_HINT_S):
        return FIST_HINT
    if mode in ("count_in", "rehearse") and p.stable == OPEN and open_s > HINT_DWELL_S:
        return PALM_HINT[mode]
    if mode in ("count_in", "rehearse") and p.stable == THUMB_UP and t is not None and p.held(t) >= HINT_DWELL_S:
        return THUMB_HINT[mode]
    return ""
KEY_COMMANDS = {ord("t"): "start", ord("x"): "stop", ord("n"): "next", ord("b"): "previous",
                ord("p"): "prepare"}  # ModeMachine.command


def sync_view(grammar: Grammar, view: ViewState, overlay: TextOverlay) -> None:
    """Copy the gesture state into the view after each hand result."""
    gs = grammar.state
    view.mode, view.level, view.drop_progress = gs.mode, gs.level, gs.drop_progress
    view.shape, view.palm_spent = gs.shape, gs.palm_spent and gs.primary is not None and gs.primary.stable == OPEN
    rate = float(gs.scroll_rate)  # a numpy float from the cursor's filter: its comparisons are numpy bools
    view.scrolling = (1 if rate > 0 else -1 if rate < 0 else 0) if gs.mode == "browse" else 0
    if gs.mode == "browse" and gs.cursor is not None:
        word_level = gs.level == "word"
        hit = overlay.hit_test(*overlay.cursor_to_text(*gs.cursor), overlay.shown_scroll(view), snap=word_level)
        if hit is not None:
            view.hover = hit if word_level else Hit(hit.sentence, None)
            view.current = hit.sentence
    elif gs.mode == "idle":
        view.hover = None
    ops = view.ops
    ops.kind, ops.pointing, ops.tone, ops.stretch = gs.op, gs.pointing or gs.turning, gs.tone, gs.stretch
    ops.stretch_ends, ops.closing = gs.stretch_ends, gs.closing  # ops.picked: the ring's knob (follow_ring)
    ops.offset = gs.ring_offset
    ops.dialing, ops.tone_over, ops.stretch_raw = gs.dialing, gs.tone_over, gs.stretch_raw
    view.lift = gs.primary.lift_progress(grammar.h) if gs.mode == "focus" and gs.primary is not None else 0.0


COMMIT_FIRST = {  # a pinch + lift with nothing to use yet, in Prepare: what comes first
    ("prepare", "word"): "NOTHING TO USE YET: OPEN PALM, HELD: ALTERNATIVES",
    ("prepare", "sentence"): "NOTHING TO USE YET: L-HAND: TONE",
    ("prepare", "paragraph"): "NOTHING TO USE YET: TWO L-HANDS: LENGTH",
}


def apply_event(ev: GestureEvent, view: ViewState, overlay: TextOverlay, log: GestureLog,
                speaker=None, takes: "Takes | None" = None, grammar: Grammar | None = None) -> float | None:
    """Grammar events. Returns the time a label note should expire, if one was set.
    A commit only ever reports what really happened."""
    if ev.kind == "focus":
        if takes is not None:
            takes.previews.cancel()
            takes.llm_failed.clear()  # a new focus may ask again what failed before
            takes.reset_pick()
        if view.hover is None:  # focused before the cursor ever touched the text
            view.hover = Hit(view.current, 0 if ev.level == "word" else None)
        view.focus = view.hover
        view.ops = OpsView()
        view.panel_scroll = 0.0
        if view.app == "prepare" and ev.level == "sentence" and hasattr(speaker, "render_async"):
            speaker.render_async(hear_words(overlay, view.focus.sentence))  # "hear it" ready by the palm hold
        return None
    if ev.kind == "commit_ignored":  # nothing to commit yet: the focus stays, and what comes first
        view.note = COMMIT_FIRST.get((view.app, ev.level), "") if view.app == "prepare" else \
            "TO DRILL: FOCUS A SENTENCE, PINCH + LIFT"
        return time.perf_counter() + NOTE_S if view.note else None
    if ev.kind == "rewind":  # a pinch took the pointer back to before the curl: the pick goes back too
        if takes is not None:
            takes.rewind_pick(ev.value)
        return None
    if ev.kind == "palm_hold":  # an open palm held on the focus: hear it (Prepare), play it (Review)
        view.note = play_focus(view, overlay, log, speaker, takes, ev.t)
        return time.perf_counter() + NOTE_S if view.note else None
    if ev.kind == "palm_stop":  # a new open palm while it plays: stop it; the focus stays
        if takes is not None and takes.stop_playback("palm", ev.t):
            view.note = "STOPPED"
            return time.perf_counter() + NOTE_S
        return None
    if ev.kind == "commit" and view.app == "review":
        if ev.level != "sentence":  # a sentence commit is a drill, which the mode events start
            view.note = "TO DRILL: FOCUS A SENTENCE, PINCH + LIFT"
    elif ev.kind == "commit":
        sentence = view.focus.sentence if view.focus else None
        picked = overlay.ring_labels(view)[view.ops.picked] if ev.op == "ring" else None
        unit = tuple(overlay.unit(ev.level, sentence)) if sentence is not None else ()
        if picked in view.alternatives and takes is not None and view.focus is not None and view.ops.picked > 0:
            view.note = takes.use_alternative(view.focus.sentence, view.focus.word, picked)
        elif ev.op in ("tone", "stretch") and takes is not None and unit:
            committed, view.note = takes.commit_preview(view, unit, ev.value, ev.t)
            if not committed:
                return time.perf_counter() + NOTE_S
            if grammar is not None:
                grammar.accept_edit_commit(ev.t)
        elif ev.op is None and takes is not None and unit in takes.proposals:
            view.note = takes.use_proposal(unit)
        elif ev.op in ("tone", "stretch"):
            view.note = "PREVIEW NOT READY: SET UP THE OPTIONAL AI"
            return time.perf_counter() + NOTE_S
        else:
            view.note = "NO CHANGE"
    if ev.kind == "back" and takes is not None and view.focus is not None:  # backing out discards a proposal
        takes.proposals.pop(tuple(overlay.unit(ev.level, view.focus.sentence)), None)
    if takes is not None:
        takes.previews.cancel()
        takes.reset_pick()  # the take picker starts again with the next focus
    view.focus = None
    view.edit_preview = None
    view.ops = OpsView()
    return time.perf_counter() + NOTE_S if ev.kind == "commit" and view.note else None


class Takes:
    """The recording side of the modes: microphone, sections, saved takes.

    A take is streamed to disk while it is recorded (palmcards.recording).
    Stopping hands the writer over to finish in the background; poll()
    adds the finished take to the session and starts its analysis. close()
    is safe to call more than once and after a failure: a take still being
    recorded is finished and kept ("interrupted" if the app is closing
    because of an error or Ctrl-C).
    """

    def __init__(self, notes: Notes, session: Session, log: GestureLog, devices: "Devices | None" = None,
                 follow: bool = FOLLOW.enabled):
        self.notes, self.session, self.log = notes, session, log
        self.devices = devices or Devices()
        self.follow_enabled = follow
        self.follow: LiveFollow | None = None  # the voice follow, made at the first count-in
        self.player = None  # clip playback (Review, "hear it"), made at the first play
        self.speaker = None  # "hear it"'s, closed with the takes
        self.playback = Playback()  # what plays now (a take's clip or "hear it"), for which target
        self._play_error = ""
        self.notes_version = 0  # bumped when an edit or undo changes the notes
        self.llm_off = ""  # why the chosen LLM can't be used, if it can't
        self.llm_alert = ""  # an LLM problem that won't go away by itself (a rejected key)
        try:
            provider = self.devices.llm()
        except LLMUnavailable as exc:
            provider, self.llm_off = None, str(exc)
            print(f"LLM off: {exc}", file=sys.stderr)
        if provider is not None:
            print(describe(provider))
        self.assistant = Assistant(provider) if provider is not None else None
        self.previews = Previews(self.assistant)
        self.alternatives: dict[tuple[int, int], tuple[str, ...]] = {}  # (sentence, word) -> words, this revision
        self.meanings: dict[tuple[int, int], str] = {}
        self.proposals: dict[tuple[int, ...], tuple[str, object, str]] = {}  # unit -> (kind, value, text shown)
        self.llm_failed: set[tuple[str, tuple]] = set()  # (kind, key) that failed: not asked again until a new focus
        self.reset_pick()
        self._follow_reported = False
        self.recorder: AudioRecorder | None = None
        self.analysis = Supervisor()
        self.deferred: list[int] = []  # takes the analysis queue had no room for yet
        self._log_seen = 0
        self.t0 = time.perf_counter()  # the app clock's zero; main() sets it
        self.section = 0
        self.writer: TakeWriter | None = None  # the take being recorded
        self.finalizing: list[TakeWriter] = []  # stopped, still writing
        self.alert = ""  # a recording problem, shown until the next take starts
        self.interrupted = False  # the app is closing because of an error or Ctrl-C
        self._closed = False
        self.last_saved = ""
        self.board = Board(notes)  # the analysed takes, side by side, for Review
        self.drill: int | None = None  # the sentence the current count-in or take drills
        self._summary_key, self._summary = None, ()  # Review's take table, and what it was made from
        self.vision: Watcher | None = None  # face and pose (milestone 7); run() opens it
        self.vision_off = "face and pose tracking not opened"  # why there is none
        self.calibrate_next = False  # e: calibrate the eyes again at the next count-in
        self.gaze_check = False  # main.py --gaze-check: every full take is a gaze check
        self.check: dict | None = None  # the gaze check being run: {"seed", "t0", "prompts"}
        self.video_on = False  # record video of each take (preferences `video`, main.py --video)
        self.video_off = ""  # why video was asked for and isn't recorded
        self.frame_size: tuple[int, int] | None = None  # the camera's, for the video
        self.video: VideoWriter | None = None  # the take's video being recorded
        # Review's replay of a take's video: the clip's span (app times), its bar's marks,
        # its words for the captions; mirrored or not (preference, key m).
        self.replay_span: tuple[float, float] | None = None
        self.replay_marks: list = []
        self.replay_words: list = []
        self.replay_mirrored = False

    def status(self, mode: str, view: ViewState) -> str:
        """Second label line when nothing more pressing is shown. In Review
        only what the focus shows, or how the takes are doing: the gestures
        that act are added by the renderer (render.review_hint)."""
        if mode == "rehearse" and self.check is not None:
            now = time.perf_counter() - self.t0 - self.check["t0"]
            prompts = self.check["prompts"]
            i = next((k for k, p in enumerate(prompts) if now < p["t1"]), len(prompts) - 1)
            return f"{gaze.prompt_text(prompts[i])}  {i + 1}/{len(prompts)}"
        if mode in ("count_in", "rehearse") and self.drill is not None:
            return f"SENTENCE {self.drill + 1}"
        if mode in ("count_in", "rehearse"):
            title = self.notes.sections[self.section].title or "untitled"
            return f"SECTION {self.section + 1}/{len(self.notes.sections)}: {title.upper()}"
        if mode == "review" and view.mode == "focus" and view.level == "sentence" and view.focus is not None:
            said = self.board.said_in(view.focus.sentence)
            if len(said) > 1:
                return self.board.take_label(view.focus.sentence)
            if said and len(self.board.rows) == 1:  # one take in all: nothing to compare it with
                return self.board.take_name(said[0])
            return f"ONLY TAKE {said[0]} SAID THIS SENTENCE" if said else "NO TAKE SAID THIS SENTENCE"
        if mode == "review" and view.mode == "focus" and view.level == "paragraph" and view.focus is not None:
            n = self.board.paragraph_shown(self.paragraph(view.focus.sentence))
            return self.board.take_name(n) if n is not None else "NO TAKE SAID THIS PARAGRAPH"
        if self.finalizing:
            return f"TAKE {self.finalizing[0].number}: SAVING…"
        if self.analysis.pending or self.deferred:
            dots = "." * (int(time.perf_counter() * 2) % 4)
            take = (self.analysis.pending or self.deferred)[0]
            return f"TAKE {take}: ANALYSING{dots:<3}"
        if mode == "review":
            return self.last_saved
        return ""  # Prepare: the renderer says how to begin (render.BROWSE_ENTRY, NEW_TAKE)

    def paragraph(self, sentence: int) -> list[int]:
        """The sentences (current notes' indices) of the sentence's paragraph."""
        p = self.notes.sentences[sentence].paragraph
        return [i for i, s in enumerate(self.notes.sentences) if s.paragraph == p]

    def _fill_board(self) -> None:
        """Every analysed take on the board, placed on the current notes by sentence id."""
        for take in self.session.takes:
            if take.alignment is not None:
                self.board.add(take.number, take.alignment, take.metrics, take.drill,
                               self.session.sentence_map(take), take.duration_s)

    def restore(self) -> bool:
        """A reopened session: its analysed takes onto the board, and takes whose
        analysis never finished submitted again. Returns whether there is
        anything to review."""
        from palmcards.analysis import resolve_jobs

        self._fill_board()
        for take in self.session.takes:
            if take.alignment is None and take.status == "saved" and take.revision is not None:
                resolve_jobs(self.session.dir, take.number, "failed", "superseded: submitted again on reopening")
                self._submit(take.number)
        return bool(self.board.takes)

    def _use_notes(self, notes: Notes) -> None:
        """New current notes (an edit or an undo): the board, the follow and the
        display (main's frame loop rebuilds the overlay on notes_version)."""
        self.previews.cancel()
        self.notes = notes
        self.board = Board(notes)
        self._fill_board()
        self.alternatives, self.proposals = {}, {}  # they were for other text
        self.meanings = {}
        self.llm_failed = set()
        if self.follow is not None:
            self.follow.notes = notes
        self.notes_version += 1

    # --- the optional LLM: requests from explicit actions, answers as previews ---

    LLM_WHAT = {"meaning": "MEANING", "alternatives": "ALTERNATIVES", "tone": "TONE REWRITE", "length": "LENGTH REWRITE"}

    @property
    def no_llm(self) -> str:
        return f"NEED THE OPTIONAL AI ({'SEE TERMINAL' if self.llm_off else 'NOT SET UP, SEE README'}): NOTHING SENT"

    @property
    def llm(self) -> str:
        """The optional LLM in use: "cloud" (the text asked about leaves the Mac), "local", or ""."""
        if self.assistant is None:
            return ""
        return "cloud" if self.assistant.cloud else "local"

    def _llm_revision(self) -> str:
        """What a request is made on, to drop answers about notes that changed
        since: the current revision's id, but the imported notes are always
        "imported", whether or not the session folder (and so that revision's
        id) exists yet: logging a call's tokens can make the folder."""
        rid = self.session.current_revision
        first = self.session.revisions[0] if self.session.revisions else None
        if rid is None or (first is not None and rid == first["id"] and first["provenance"] == "imported"):
            return "imported"
        return rid

    def ask_meaning(self, sentence: int, word: int) -> None:
        """Selecting a word asks for its meaning in context, once per revision."""
        key = (sentence, word)
        if self.assistant is None or key in self.meanings or self.assistant.asking("meaning", key) \
                or ("meaning", key) in self.llm_failed:
            return
        s = self.notes.sentences[sentence]
        self.assistant.ask("meaning", key, self._llm_revision(),
                           meaning_request(s.text, s.words[word].text), parse_meaning)
        self.log(time.perf_counter() - self.t0, "llm", ask="meaning", sentence=sentence, word=word)

    def sync_word(self, view: ViewState, op: str | None) -> None:
        """A selected word shows its meaning; only an open palm asks for alternatives."""
        key = (view.focus.sentence, view.focus.word) if view.mode == "focus" and view.level == "word" \
            and view.focus is not None and view.focus.word is not None else None
        view.meaning, view.alternatives, view.loading = "", (), False
        if key is None:
            return
        self.ask_meaning(*key)
        if self.assistant is None:
            view.meaning = "Meaning needs the optional LLM. See README to set it up."
        elif key in self.meanings:
            view.meaning = self.meanings[key]
        elif ("meaning", key) in self.llm_failed:
            view.meaning = "Meaning unavailable. Select this word again to retry."
        else:
            view.meaning = "Loading meaning..."
        if op == "ring":
            self.ask_alternatives(*key)
            view.alternatives = self.alternatives.get(key, ())
            view.loading = bool(self.assistant and self.assistant.asking("alternatives", key))

    def ask_alternatives(self, sentence: int, word: int) -> None:
        """Opening the options ring on a word: ask once for alternatives."""
        key = (sentence, word)
        if self.assistant is None or key in self.alternatives or self.assistant.asking("alternatives", key) \
                or ("alternatives", key) in self.llm_failed:
            return
        s = self.notes.sentences[sentence]
        text = s.words[word].text
        self.assistant.ask("alternatives", key, self._llm_revision(),
                           alternatives_request(s.text, text), lambda reply: parse_alternatives(reply, text))
        self.log(time.perf_counter() - self.t0, "llm", ask="alternatives", sentence=sentence, word=word)

    def sync_edit(self, view: ViewState, overlay: TextOverlay, now: float) -> None:
        from palmcards.notes import parse_sentence

        if view.app != "prepare" or view.mode != "focus" or view.focus is None \
                or (view.level, view.ops.kind) not in (("sentence", "tone"), ("paragraph", "stretch")):
            self.previews.cancel()
            view.edit_preview = None
            return
        unit = tuple(overlay.unit(view.level, view.focus.sentence))
        op = self.previews.operation
        # Source parsing and joining happen once, not on every camera frame.
        if op is None or op.unit != unit or op.source_revision != self._llm_revision():
            original = " ".join(self.notes.sentences[i].text for i in unit)
            ignored = []
            for i in unit:
                parse_sentence(self.notes.sentences[i].raw, ignored)
            marks = bool(sum(ignored))
            if self.session.current_revision is not None:
                saved = self.session.snapshot(self.session.current_revision)["sentences"]
                marks = marks or any(saved[i].get("marks") or any(w.get("stressed") for w in saved[i]["words"])
                                     for i in unit)
        else:
            original, marks = op.original, op.marks_warning
        self.previews.sync("tone" if view.ops.kind == "tone" else "length", self._llm_revision(), unit,
                           original, view.ops.tone if view.ops.kind == "tone" else view.ops.stretch, now, marks)
        previous = view.edit_preview
        view.edit_preview = self.previews.view()
        if previous is None or previous.text != view.edit_preview.text:
            # Begin a newly arrived candidate at its first line; later pages remain reachable.
            view.panel_scroll = 0.0

    def commit_preview(self, view: ViewState, unit: tuple[int, ...], value: float, now: float) -> tuple[bool, str]:
        op = self.previews.operation
        if op is None or op.source_revision != self._llm_revision() or op.unit != unit:
            return False, "PREVIEW NOT READY"
        if not self.previews.can_commit(view.edit_preview, value):
            if op.error:
                self.previews.retry(now)
                return False, "RETRYING PREVIEW" if op.loading else op.error
            return False, "PREVIEW NOT READY"
        if op.displayed_target == op.original_target or op.displayed_candidate == op.original:
            self.previews.cancel()
            return True, "ORIGINAL KEPT: NO CHANGE"
        error = self._save_edit(replace_text(self.notes, list(unit), op.displayed_candidate),
                                f"{op.kind} preview committed", op=op.kind, sentences=list(unit))
        return (False, error) if error else (True, f"EDIT SAVED  /  {UNDO_HINT}")

    def ask_rewrite(self, kind: str, unit: tuple[int, ...], amount: float) -> str:
        what = "TONE" if kind == "tone" else "LENGTH"
        if self.assistant is None:
            return f"{what} EDITS {self.no_llm}"
        text = " ".join(self.notes.sentences[i].text for i in unit)
        self.assistant.ask(kind, unit, self._llm_revision(),
                           rewrite_request(kind, text, amount), lambda reply: parse_rewrite(reply, text, kind, amount))
        self.log(time.perf_counter() - self.t0, "llm", ask=kind, sentences=list(unit), amount=amount)
        return f"ASKING FOR A {'WARMER' if kind == 'tone' and amount > 0 else 'COOLER' if kind == 'tone' else 'LONGER' if amount > 1 else 'SHORTER'} VERSION..."

    def reset_pick(self) -> None:
        """A new focus, or leaving one: Review's take picker starts again on the
        take shown. (The word's ring is the grammar's knob.)"""
        self.pickers = {"take": Picker()}

    def rewind_pick(self, t: float) -> None:
        for picker in self.pickers.values():
            picker.rewind(t)

    def poll_llm(self) -> str:
        """Answers in: alternatives onto the ring, rewrites as proposals. One made on notes that have changed since is dropped."""
        if self.assistant is None:
            return ""
        answers = self.assistant.poll()
        self._log_llm_usage()  # after: a call's usage is queued before its answer, so these answers' are in
        note = ""
        for a in answers:
            if a.reason == "API KEY REJECTED":
                self.llm_alert = "CLOUD AI: API KEY REJECTED (SEE TERMINAL)"
            if self.previews.accept(a, self._llm_revision()):
                continue
            if a.revision != self._llm_revision():
                note = "THE NOTES CHANGED: SUGGESTION DROPPED"
                continue
            if a.error:
                print(f"LLM {a.kind}: {a.error}", file=sys.stderr)
                if a.reason == "API KEY REJECTED":
                    self.llm_alert = "CLOUD AI: API KEY REJECTED (SEE TERMINAL)"
                note = f"{self.LLM_WHAT.get(a.kind, a.kind.upper())} FAILED: {a.reason}, NOTHING CHANGED"
                self.llm_failed.add((a.kind, a.key))
                continue
            if a.kind == "meaning":
                self.meanings[a.key] = a.value
                continue
            if a.kind == "alternatives":
                self.alternatives[a.key] = tuple(a.value)
                continue
            self.proposals[a.key] = (a.kind, a.value, a.value)
            note = "PROPOSAL READY: FOCUS IT AGAIN TO SEE IT"
        return note

    def _log_llm_usage(self, abandoned: bool = False) -> None:
        """Every finished call's tokens into the session (llm-usage.jsonl), and
        with `abandoned`, the calls still in flight as unknown."""
        entries = [{"t": round(u.started - self.t0, 3), "action": u.kind, "provider": u.provider, "model": u.model,
                    "input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "ok": u.ok, "error": u.error,
                    "seconds": u.seconds, "attempts": u.attempts, "request_id": u.request_id,
                    "revision": u.revision} for u in self.assistant.spent()]
        if abandoned:
            p = self.assistant.provider
            entries += [{"t": round(time.perf_counter() - self.t0, 3), "action": kind, "provider": p.name,
                         "model": p.model, "input_tokens": None, "output_tokens": None, "ok": False,
                         "error": "abandoned at exit"} for kind, _ in self.assistant.pending.values()]
        for entry in entries:
            entry["at"] = datetime.now().isoformat(timespec="seconds")
            try:
                self.session.log_llm_call(entry)
            except (OSError, SessionError) as exc:
                print(f"could not log an LLM call's tokens: {exc}", file=sys.stderr)

    def _save_edit(self, notes: Notes, what: str, **log) -> str:
        try:
            rid = self.session.edit(notes, note=what)
        except (OSError, SessionError) as exc:
            print(f"could not save the edit: {exc}", file=sys.stderr)
            return "EDIT NOT SAVED (SEE TERMINAL)"
        self._use_notes(self.session.current_notes())
        self.log(time.perf_counter() - self.t0, "edit", revision=rid, **log)
        return ""

    def use_alternative(self, sentence: int, word: int, text: str) -> str:
        old = self.notes.sentences[sentence].words[word].text
        note = self._save_edit(replace_word(self.notes, sentence, word, text), f'"{old}" -> "{text}"',
                               op="alternative", sentence=sentence, word=word, text=text)
        return note or f'"{old.upper()}" → "{text.upper()}"  /  {UNDO_HINT}'

    def use_proposal(self, unit: tuple[int, ...]) -> str:
        kind, value, _ = self.proposals.pop(unit)
        note = self._save_edit(replace_text(self.notes, list(unit), value), f"{kind} proposal used", op=kind,
                               sentences=list(unit))
        return note or f"{kind.upper()} PROPOSAL USED  /  {UNDO_HINT}"

    def can_undo(self) -> bool:
        """An edit to undo: the current notes revision has a parent."""
        rid = self.session.current_revision
        return rid is not None and self.session._parsed is None and self.session.revision(rid)["parent"] is not None

    def undo(self) -> str:
        if self.session.current_revision is None or self.session._parsed is not None:
            return "NOTHING TO UNDO"
        rid = self.session.undo()
        if rid is None:
            return "NOTHING TO UNDO"
        self._use_notes(self.session.current_notes())
        self.log(time.perf_counter() - self.t0, "undo", revision=rid)
        return "UNDONE"

    def play_sentence(self, sentence: int) -> str:
        """Play a sentence (current notes' index) from the take Review shows for it."""
        n = self.board.shown(sentence)
        if n is None:
            return "NO TAKE TO PLAY"
        if not self._play(n, [sentence]):
            return self._play_error or f"TAKE {n}: SENTENCE NOT SAID"
        self.log(time.perf_counter() - self.t0, "play", take=n, sentence=sentence)
        return f"PLAYING TAKE {n}"

    def play_paragraph(self, sentences: list[int]) -> str:
        """Play a paragraph (current notes' indices) from the take Review shows for it:
        from its first word said to its last."""
        n = self.board.paragraph_shown(sentences)
        if n is None:
            return "NO TAKE TO PLAY"
        if not self._play(n, sentences):
            return self._play_error or f"TAKE {n}: PARAGRAPH NOT SAID"
        self.log(time.perf_counter() - self.t0, "play", take=n, sentences=list(sentences))
        return f"PLAYING TAKE {n}"

    def _play(self, n: int, sentences: list[int]) -> bool:
        """Play take n's audio for these sentences (current notes' indices;
        sentences edited since the take are left out). False if there is
        nothing to play, or it failed (then _play_error says so)."""
        self._play_error = ""
        take = self.session.take(n)
        own = {cur: old for old, cur in (self.session.sentence_map(take) or {}).items()}
        mine = [own[i] for i in sentences if i in own]
        clip = span_clip(self.session, take, mine) if mine else None
        if clip is None:
            return False
        span = clip_span(take, mine)
        reader = self._video_reader(take)
        # What the replay shows besides the video: marks on its bar, and (if asked for) captions.
        self.replay_span, self.replay_marks, self.replay_words = span, [], []
        if reader is not None:
            try:
                self.replay_marks = clip_marks(self.session, take, span)
                self.replay_words = replay_words(self.session, take, span)
            except Exception as exc:  # the replay plays on without them
                print(f"take {n}: replay marks not shown: {exc}", file=sys.stderr)
        try:
            self.playback.start_clip(self.clip_player(), clip, ("review", tuple(sentences), n), time.perf_counter(),
                                     video=reader, video_t0=span[0])
        except Exception as exc:  # no output device
            print(f"could not play: {exc}", file=sys.stderr)
            self._play_error = "COULD NOT PLAY (SEE TERMINAL)"
            return False
        return True

    def _video_reader(self, take):
        """A reader of the take's video, if it was recorded with one that is still there."""
        video = take.video or {}
        if video.get("state") not in ("saved", "interrupted") or video.get("t_first") is None:
            return None
        path = self.session.dir / video["file"]
        if not path.exists():
            return None
        try:
            return self.devices.video_reader(path, video["t_first"], self.frame_size)
        except Exception as exc:  # the audio plays on without it
            print(f"take {take.number}: video not replayed: {exc}", file=sys.stderr)
            return None

    def replay_frame(self):
        """While a take's clip plays with its video: that moment's frame flipped
        back, as others see you (it is recorded mirrored), or as recorded with
        `replay_mirrored` (key m); a new array to draw on. Else None (the live mirror)."""
        frame = self.playback.video_frame(time.perf_counter())
        if frame is None:
            return None
        return frame.copy() if self.replay_mirrored else cv2.flip(frame, 1)

    def draw_replay(self, frame, progress: float) -> None:
        """On a replayed frame: the captions (what has been said so far), its bar
        with the clip's marks, and a note that m flips the video."""
        draw_replay_bar(frame, progress, self.replay_marks, self.replay_span)
        if (t := self.playback.clip_time(time.perf_counter())) is not None:
            draw_replay_caption(frame, [(text, kind, a <= t <= b + CAPTION_LINGER_S)
                                        for a, b, text, kind in self.replay_words if a <= t])
        draw_replay_hint(frame)

    def clip_player(self):
        """The player for clips (Review's, "hear it"), made at the first play."""
        if self.player is None:
            self.player = self.devices.player()
        return self.player

    def stop_playback(self, why: str, t: float) -> bool:
        """Stop what plays (a take's clip or "hear it"). True if something was playing."""
        if not self.playback.stop():
            return False
        self.log(t, "play_stop", why=why)
        return True

    def sync_playback(self, grammar: Grammar, view: ViewState, overlay: TextOverlay, t: float) -> None:
        """Every frame: forget what has finished, stop what the screen no longer
        shows, and hold the focus while its audio plays."""
        now = time.perf_counter()
        if self.playback.poll(now) and self.playback.target != play_target(view, overlay, self.board):
            self.stop_playback("target", t)
        grammar.set_focus_hold(t, "play" if self.playback.playing and view.mode == "focus" else None)
        view.playing = self.playback.playing
        view.play_progress = self.playback.progress(now)
        view.palm_progress = grammar.state.palm_progress

    def alert_line(self, mode: str = "") -> str:
        """Persistent trouble (recording, analysis, voice follow), shown until it is dealt with."""
        if self.alert:
            return self.alert
        if self.llm_alert and mode in ("prepare", "review"):
            return self.llm_alert
        if mode in ("count_in", "rehearse") and self.follow is not None and self.follow.state == "failed" \
                and self.drill is None:
            return "VOICE FOLLOW OFF (SEE TERMINAL)  /  N: NEXT SECTION"
        if failed := self.analysis.failed():
            return f"TAKE {failed[0]}: ANALYSIS FAILED (SEE TERMINAL)  /  OPEN PALM, HELD, OR R: RETRY"
        return ""

    def sync_review(self, grammar: Grammar, view: ViewState, overlay: "TextOverlay | None" = None,
                    t: float = 0.0) -> None:
        """Review: the takes to point at, the focused sentence in each take, and the take table."""
        gs = grammar.state
        focused = view.focus.sentence if view.mode == "focus" and view.focus is not None else None
        said = self.board.said_in(focused) if focused is not None and view.level == "sentence" else []
        if said:  # the takes as chips beside the sentence, one of them pointed at
            picker = self.pickers["take"]
            shown = self.board.shown(focused)
            if gs.point is None:
                picker.index = said.index(shown) if shown in said else len(said) - 1
            view.takes = tuple(f"TAKE {n}" + (" (DRILL)" if n in self.board.drills else "") for n in said)
            view.take_shown = picker.index
            if gs.op == "take":
                picker.update(t, gs.point, overlay.take_points(view), (overlay.box_w, overlay.box_h))
                self.board.picked[focused] = said[picker.index]
                view.take_shown = picker.index
        else:
            view.takes = ()
        if focused is None:
            view.detail, view.playable = (), False
        elif view.level == "paragraph":
            unit = self.paragraph(focused)
            view.detail, view.playable = self.board.paragraph_detail(unit), self.board.paragraph_shown(unit) is not None
        else:
            view.detail, view.playable = self.board.detail(focused), bool(said)
        n = self.board.latest()
        if focused is None and n is not None:  # the take table, made again when a take arrives
            key = (n, n in self.board.metrics, id(self.board))
            if key != self._summary_key:
                self._summary_key, self._summary = key, self.board.take_table()
            view.summary = self._summary
        else:
            view.summary = ()

    def on_transcribed(self, result: dict) -> str:
        """A take's transcript and alignment arrived. Returns a label note."""
        n = result["take"]
        if not result["ok"]:
            print(f"take {n}: analysis failed: {result.get('error')}. Press r to retry, or run: "
                  f"python -m palmcards.speech {self.session.dir} --take {n}", file=sys.stderr)
            return ""
        self.session.set_result(n, result["transcript"], result["alignment"], result.get("metrics"))
        take = self.session.take(n)
        self.board.add(n, result["alignment"], result.get("metrics"), take.drill, duration_s=take.duration_s)
        self.log(time.perf_counter() - self.t0, "transcribed", take=n, seconds=result["seconds"])
        print(f"take {n} (transcribed and measured in {result['seconds']:.1f} s)\n{result['report']}")
        if take.drill is not None:
            row = self.board.rows[n].get(take.drill, {})
            pace = f"{row['wpm']:.0f} WPM" if row.get("wpm") is not None else "NOT SAID"
            self.last_saved = f"TAKE {n} (DRILL): {pace}"
            return ""
        # The take table beside the notes has the measures; the label only says the take is in.
        c = counts(result["alignment"])
        self.last_saved = f"TAKE {n}: {c['spoken']} OF {len(result['alignment']['sentences'])} SENTENCES SAID"
        return ""

    def handle(self, ev: GestureEvent, modes: ModeMachine, view: ViewState, overlay: TextOverlay) -> str:
        """Mode events. Returns a label note to show, or ""."""
        if ev.kind in ("count_in", "drill"):
            self.stop_playback("take", ev.t)
            self.drill = ev.sentence if ev.kind == "drill" else None
            if self.drill is not None:
                self.log(ev.t, "drill", sentence=self.drill)
            try:
                if self.recorder is None:
                    self.recorder = self.devices.recorder(clock=lambda: time.perf_counter() - self.t0)
                self.recorder.open()
            except Exception as exc:  # no input device, PortAudio error
                print(f"Could not open the microphone: {exc}. On macOS, allow Microphone access for "
                      "your terminal app in System Settings > Privacy & Security > Microphone.",
                      file=sys.stderr)
                self.log(ev.t, "mic_error", error=str(exc))
                modes.cancel_count_in(ev.t)
                self.drill = None
                return "MICROPHONE UNAVAILABLE"
            self.section = 0 if self.drill is None else self.notes.sentences[self.drill].section
            view.current = self.drill if self.drill is not None else self.first_of(self.section)
            view.panel_scroll, view.preview_next = 0.0, False
            if self.drill is None and self._ensure_follow():
                self.follow.prepare()  # the model loads during the count-in
            check = self.gaze_check and self.drill is None
            if self.vision is not None and (self.calibrate_next or self.session.calibration is None or check):
                # The count-in starts with looking into the camera; the orange sentence is the 3-2-1.
                self.vision.begin_calibration(ev.t)
                modes.count_in_end = ev.t + BODY.calib_camera_s + REHEARSE.count_in_s
                self.log(ev.t, "calibration_start")
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "to_prepare":
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "count_in_cancel":
            if self.vision is not None:
                self.vision.cancel()
            self.recorder.close()
            self.drill = None
            return "TAKE CANCELLED"
        if ev.kind == "take_start":
            self.alert = ""
            note = self._finish_calibration(ev.t) \
                if self.vision is not None and self.vision.state == "calibrating" else ""
            try:
                number = self.session.begin_take()
                self.writer = TakeWriter(self.session.dir, number, self.recorder.rate, {
                    "started": datetime.now().isoformat(timespec="milliseconds"), "requested_t": round(ev.t, 3),
                    "drill": self.drill, "revision": self.session.current_revision})
            except (OSError, SessionError) as exc:
                print(f"cannot record: {exc}", file=sys.stderr)
                self.log(ev.t, "record_error", error=str(exc))
                self.alert = "CANNOT RECORD (SEE TERMINAL): OK SIGN TO STOP"
                return ""
            self.writer.mark_section(ev.t, self.section, "start")
            self.recorder.start(self.writer)
            self._start_video(number)
            if self.drill is None and self.follow is not None:
                self.follow.start_take(self.section)
                self.recorder.tap = self.follow.tap
            if self.vision is not None:
                self.vision.begin_take(ev.t)
            self.log(ev.t, "take_start", take=number)
            if self.gaze_check and self.drill is None:
                if self.vision is None:
                    print(f"gaze check: face tracking is off ({self.vision_off}); recording a plain take",
                          file=sys.stderr)
                    return "GAZE CHECK NEEDS FACE TRACKING (SEE TERMINAL)"
                seed = secrets.randbelow(1 << 30)
                self.check = {"seed": seed, "t0": round(ev.t, 3), "prompts": gaze.prompt_schedule(seed)}
                self.writer.meta["gaze_check"] = self.check
                self.log(ev.t, "gaze_check", take=number, seed=seed)
            return note
        if ev.kind == "next_section":
            if self.section + 1 >= len(self.notes.sections):
                return "LAST SECTION"
            source = "key" if ev.source == "key" else "flick"
            self.section += 1
            view.current, view.panel_scroll, view.preview_next = self.first_of(self.section), 0.0, False
            if self.writer is not None:
                self.writer.mark_section(ev.t, self.section, source)
            if self.follow is not None:
                self.follow.flick(ev.t)  # the voice carries on from the new section
            self.log(ev.t, "section", section=self.section, source=source)
            return ""
        if ev.kind == "previous_section":  # a key: undo a wrong move
            if self.section == 0:
                return "FIRST SECTION"
            self.section -= 1
            view.current, view.panel_scroll, view.preview_next = self.first_of(self.section), 0.0, False
            if self.writer is not None:
                self.writer.mark_section(ev.t, self.section, "key")
            if self.follow is not None:
                self.follow.jump(self.section, view.current)
            self.log(ev.t, "section", section=self.section, source="key")
            return ""
        if ev.kind == "take_stop":
            writer = self._stop_recording()
            if writer is not None:
                self.log(ev.t, "take_stop", take=writer.number, duration_s=round(writer.seconds, 3), wav=writer.wav)
            if self.drill is not None:
                view.current = self.drill
            else:
                view.current = next(i for i, s in enumerate(overlay.sentences) if s.section == self.section)
            self.drill = None
            view.scroll = overlay.scroll_to(view.current)
            return ""
        return ""

    def _start_video(self, number: int) -> None:
        """The take's video beside its audio, if asked for and possible."""
        self.writer.video = None
        if not self.video_on:
            return
        if self.video_off or self.frame_size is None:
            self.writer.meta["video"] = {"state": "off", "reason": self.video_off or "no camera frame yet"}
            return
        try:
            self.video = self.writer.video = self.devices.video(self.session.dir, number, self.frame_size)
        except Exception as exc:  # the take goes on without video
            print(f"take {number}: no video: {exc}", file=sys.stderr)
            self.writer.meta["video"] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}

    def push_video(self, frame, t: float) -> None:
        """A frame of the take being recorded (before anything is drawn on it),
        with its capture time on the app clock. Never blocks."""
        if self.video is None or self.writer is None:
            return
        first = self.video.t_first is None
        self.video.push(frame.copy(), t)
        if first and self.video.t_first is not None:  # the audio's manifest knows, should the app die mid-take
            self.writer.meta["video"] = {"state": "recording", "file": self.video.file,
                                         "t_first": round(self.video.t_first, 3), "codec": self.video.codec}

    def check_over(self, t: float) -> bool:
        """A gaze check stops its take after the last prompt."""
        return self.check is not None and self.writer is not None and \
            t >= self.check["t0"] + self.check["prompts"][-1]["t1"]

    def _finish_calibration(self, t: float) -> str:
        """Keep the calibration that just ended. Returns a label note if it didn't work."""
        summary = self.vision.finish_calibration(t)
        cid = self.session.add_calibration(summary)  # written with the take's first save
        self.log(t, "calibration", id=cid, status=summary["status"], reason=summary["reason"])
        if summary["status"] == "ok":
            self.calibrate_next = False
            print(f"eyes calibrated ({cid}: face seen in {summary['camera']['n']} + {summary['notes']['n']} frames)")
            return ""
        print(f"eye calibration {cid} {summary['status']}: {summary['reason']}; it runs again at the next take",
              file=sys.stderr)
        return "EYE CALIBRATION DIDN'T WORK: AGAIN AT THE NEXT TAKE"

    def recalibrate(self) -> str:
        """e: calibrate the eyes again at the next count-in."""
        if self.vision is None:
            return "FACE TRACKING IS OFF (SEE TERMINAL)"
        self.calibrate_next = True
        return "EYES CALIBRATED AGAIN AT THE NEXT TAKE"

    def _save_vision(self, writer: TakeWriter) -> dict:
        """The take's face, pose and hand features to take-NN.face.npz; what to note on the take."""
        if self.vision is None:
            return {"state": "off", "reason": self.vision_off}
        rows, counts = self.vision.end_take()
        calibration = self.session.calibration
        name = f"take-{writer.number:02d}.face.npz"
        info = {"state": "recorded", "file": name, "calibration": calibration["id"] if calibration else None,
                "counts": counts}
        try:
            features.save(self.session.dir / name, rows,
                          self.vision.provenance(take=writer.number, calibration=info["calibration"]))
        except OSError as exc:
            print(f"take {writer.number}: face and pose features not saved: {exc}", file=sys.stderr)
            return {"state": "failed", "error": f"{type(exc).__name__}: {exc}", "counts": counts}
        return info

    def first_of(self, section: int) -> int:
        return next(i for i, s in enumerate(self.notes.sentences) if s.section == section)

    def section_sentences(self) -> list[int]:
        return [i for i, s in enumerate(self.notes.sentences) if s.section == self.section]

    def _ensure_follow(self) -> bool:
        """The voice follow, made once the microphone's rate is known."""
        if self.follow is None and self.follow_enabled and self.recorder is not None:
            clock = lambda: time.perf_counter() - self.t0  # noqa: E731
            hints = tuple(s.text for s in self.notes.sentences)
            model = SPEECH.live_model if SPEECH.backend == "mlx-whisper" else SPEECH.backend
            self.follow = LiveFollow(self.notes, lambda: self.devices.live(self.session.language, clock, hints),
                                     self.recorder.rate, SPEECH.backend, model)
        return self.follow is not None

    def poll_follow(self, view: ViewState, overlay: TextOverlay) -> None:
        """Move the display with the voice: the sentence, the section (recorded
        with source "voice"), and the preview of the next section."""
        if self.follow is None or self.writer is None or self.drill is not None:
            return
        for ev in self.follow.poll():
            if ev.kind == "section" and ev.index == self.section + 1:
                self.section = ev.index
                self.writer.mark_section(ev.t, ev.index, "voice")
                self.log(ev.t, "section", section=ev.index, source="voice")
            elif ev.kind == "sentence":
                view.current = ev.index
        if self.follow.state == "failed" and not self._follow_reported:
            print(f"voice follow off: {self.follow.error}", file=sys.stderr)
            self._follow_reported = True
        section = self.section_sentences()
        view.section = self.section
        # The next section shows faint from its predecessor's last sentence on, and
        # stays while the highlight has already been handed on into it.
        handed_on = self.notes.sentences[view.current].section == self.section + 1
        view.preview_next = (view.current == section[-1] or handed_on) and self.section + 1 < len(self.notes.sections)
        view.panel_scroll = overlay.panel_scroll_to(view, view.current)

    def _stop_recording(self) -> "TakeWriter | None":
        """Stop the microphone; the take finishes writing in the background."""
        writer, self.writer = self.writer, None
        self.check = None
        if self.video is not None:
            self.video.stop()  # finishes in the background, like the audio
            self.video = None
        if writer is not None and (self.vision is None or self.vision.state == "take"):
            writer.meta["vision"] = self._save_vision(writer)
        if self.follow is not None and self.follow.follower is not None:
            live = self.follow.stop_take()
            if writer is not None:
                writer.meta["live"] = live
        if self.recorder is not None:
            self.recorder.tap = None
            self.recorder.stop()
            self.recorder.close()
        if writer is not None:
            self.finalizing.append(writer)
        return writer

    def recording_problem(self) -> None:
        """Surface a disk error while a take is still being recorded."""
        if self.writer is not None and self.writer.error and not self.alert:
            print(f"take {self.writer.number}: recording failed: {self.writer.error}; the audio up to here is kept",
                  file=sys.stderr)
            self.alert = "RECORDING FAILED: AUDIO SO FAR KEPT (SEE TERMINAL)"

    def poll(self) -> str:
        """Add takes that finished writing to the session. Returns a label note."""
        note = ""
        for writer in [w for w in self.finalizing if w.done.is_set()
                       and (getattr(w, "video", None) is None or w.video.done.is_set())]:
            self.finalizing.remove(writer)
            note = self._finish(writer, "saved") or note
        return note

    def _finish(self, writer: TakeWriter, status: str) -> str:
        if (video := getattr(writer, "video", None)) is not None:
            if not video.wait(RECORDING.finalize_timeout_s):
                print(f"take {writer.number}: the video is still being written; it is recovered at the next start",
                      file=sys.stderr)
            writer.meta["video"] = video.summary()
            if video.error:
                print(f"take {writer.number}: video: {video.error}", file=sys.stderr)
        manifest = writer.manifest()
        if manifest["samples"] == 0:
            print(f"take {writer.number}: no audio was written ({writer.error or 'none arrived'})", file=sys.stderr)
            self.alert = "TAKE NOT SAVED: NO AUDIO (SEE TERMINAL)"
            return ""
        take = self.session.finish_take(manifest, None if status == "saved" else status)
        m, s = divmod(round(take.duration_s), 60)
        gaps = take.capture["dropped_samples"] / take.sample_rate if take.capture else 0.0
        print(f"saved {self.session.dir / take.wav} ({take.duration_s:.1f} s, {take.status}"
              + (f", {gaps:.2f} s of audio lost" if gaps else "") + ")")
        if take.status != "saved":
            self.alert = f"TAKE {take.number} {take.status.upper()}: KEPT, NOT ANALYSED (SEE TERMINAL)"
            print(f"take {take.number} is {take.status}; analyse it anyway with: python -m palmcards.speech "
                  f"{self.session.dir} --take {take.number} --incomplete", file=sys.stderr)
            return ""
        self._submit(take.number)
        self.last_saved = f"TAKE {take.number} SAVED ({m}:{s:02d})"
        if take.silent:
            print("warning: the take is silent. On macOS, allow Microphone access for your terminal "
                  "app in System Settings > Privacy & Security > Microphone.", file=sys.stderr)
            return "TAKE IS SILENT: CHECK MICROPHONE ACCESS"
        return ""

    def _submit(self, number: int) -> None:
        take = self.session.take(number)
        if self.analysis.submit(self.session.dir, make_job(self.session, take, self.session.notes_for(take))):
            if number in self.deferred:
                self.deferred.remove(number)
        elif number not in self.deferred:
            self.deferred.append(number)  # queue full: tried again every frame

    def poll_analysis(self) -> str:
        """Results in, deferred takes resubmitted, worker trouble to the terminal."""
        note = ""
        for result in self.analysis.poll():
            note = self.on_transcribed(result) or note
        for number in list(self.deferred):
            self._submit(number)
        for line in self.analysis.log[self._log_seen:]:
            print(f"analysis: {line}", file=sys.stderr)
        self._log_seen = len(self.analysis.log)
        return note

    def retry(self) -> str:
        n = self.analysis.retry_failed()
        return f"RETRYING ANALYSIS OF {n} TAKE{'S' if n != 1 else ''}" if n else "NOTHING TO RETRY"

    def close(self) -> None:
        """Finish any take (kept as "interrupted" when closing on an error), release
        the microphone, then let pending transcriptions finish unless Ctrl-C.
        Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        active = self._stop_recording()  # the take being recorded right now, if any
        for writer in self.finalizing:
            if writer.wait(RECORDING.finalize_timeout_s):
                try:
                    self._finish(writer, "interrupted" if writer is active and self.interrupted else "saved")
                except Exception:  # keep closing; the recording is on disk and recovered at the next start
                    traceback.print_exc()
            else:
                print(f"take {writer.number} is still being written; it will be recovered at the next start",
                      file=sys.stderr)
        self.finalizing = []
        if self.assistant is not None:  # calls in flight get a moment, so their tokens are logged
            deadline = time.monotonic() + (0.0 if self.interrupted else LLM.close_wait_s)
            try:
                while self.assistant.pending and time.monotonic() < deadline:
                    self.poll_llm()
                    time.sleep(0.05)
            except KeyboardInterrupt:
                pass
            self.poll_llm()
            self._log_llm_usage(abandoned=True)
        # Bounded: wait a while for analysis already running, then leave it for later.
        if self.analysis.pending and not self.interrupted:
            print(f"finishing analysis of take {', '.join(map(str, self.analysis.pending))} "
                  f"(up to {ANALYSIS.shutdown_s:.0f} s; Ctrl-C to leave it for later)")
            deadline = time.monotonic() + ANALYSIS.shutdown_s
            try:
                while self.analysis.pending and time.monotonic() < deadline:
                    self.poll_analysis()
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
        if self.follow is not None:
            self.follow.close()
        if self.player is not None:
            self.player.stop()
        if hasattr(self.speaker, "close"):
            self.speaker.close()
        left = self.analysis.close(timeout=1.0)
        self.poll_analysis()
        if left or self.deferred:
            takes = sorted(set(left) | set(self.deferred))
            print(f"analysis of take {', '.join(map(str, takes))} left for later: python -m palmcards.speech "
                  f"{self.session.dir}")
        self.session.release()


@dataclass
class Devices:
    """What the app opens. Tests pass fakes, so failures can be injected
    without a camera, a microphone or a window."""
    camera: Callable = Camera
    tracker: Callable = HandTracker
    recorder: Callable = AudioRecorder
    log: Callable = GestureLog.to_session_dir
    speaker: Callable = get_speaker
    player: Callable = ClipPlayer
    video: Callable = VideoWriter  # (folder, take number, frame size); only with video on
    video_reader: Callable = VideoReader  # (path, t_first, frame size): Review's replay of a take's video
    llm: Callable = get_provider  # None: the optional LLM is off
    vision: Callable = Watcher.open  # face and pose; None or an error: off (they are optional)
    live: Callable = lambda language, clock, hints: get_recognizer().live(language, clock, SPEECH.live_where, hints)
    named_window: Callable = lambda name, w, h: (cv2.namedWindow(name, cv2.WINDOW_NORMAL), cv2.resizeWindow(name, w, h))
    show: Callable = cv2.imshow
    wait_key: Callable = lambda: cv2.waitKey(1) & 0xFF
    window_open: Callable = lambda name: cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) >= 1
    destroy_windows: Callable = cv2.destroyAllWindows


def guarded(fn: Callable[[], None], what: str) -> Callable[[], None]:
    """A cleanup step that reports its own failure instead of raising, so the
    other steps still run and the error that started the shutdown survives."""
    def run() -> None:
        try:
            fn()
        except BaseException:
            print(f"error while closing {what}:", file=sys.stderr)
            traceback.print_exc()
    return run


def main() -> int:
    ap = argparse.ArgumentParser(description="PalmCards rehearsal mirror")
    ap.add_argument("notes", nargs="?", type=Path, default=SAMPLE, help=".txt, .md or .docx")
    ap.add_argument("--lang", default=SPEECH.language, help="language of the takes, for Whisper")
    ap.add_argument("--trace", action="store_true", help="record hand landmarks for offline replay")
    ap.add_argument("--no-follow", action="store_true", help="don't follow the voice during takes")
    ap.add_argument("--video", action="store_true",
                    help="record video of each take (also preferences `video`), replayed in Review")
    ap.add_argument("--open", metavar="RUN", help="reopen a saved session in Review")
    ap.add_argument("--gaze-check", action="store_true",
                    help="every take is a gaze check (timed prompts), for scripts/evaluate.py --gaze")
    ap.add_argument("--llm", choices=PROVIDERS, default=LLM.provider or "off",
                    help="the optional LLM: off, ollama (on this Mac) or anthropic (the cloud: what you ask "
                         "about leaves the Mac; ANTHROPIC_API_KEY from the environment or .env)")
    args = ap.parse_args()
    for line in recover_all():
        print(f"recovered: {line}")
    if args.open:
        return reopen(args)
    path = args.notes
    try:
        source = path.read_bytes()  # parsed once and kept byte for byte in the session
        notes = notes_from_bytes(source, path)
    except (OSError, ValueError) as exc:
        print(f"Could not open notes: {exc}", file=sys.stderr)
        return 1
    for warning in notes.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    try:
        return run(path, notes, source, lang=args.lang, trace=args.trace, follow=not args.no_follow,
                   gaze_check=args.gaze_check, video=args.video, devices=Devices(llm=lambda: get_provider(args.llm)))
    except CameraError as exc:
        print(exc, file=sys.stderr)
        return 1


def reopen(args) -> int:
    """main.py --open RUN: the session's current notes revision, its takes in Review."""
    from palmcards.data import find
    from palmcards.session import SessionBusy

    try:
        folder = Path(args.open) if (Path(args.open) / "session.json").exists() else find(SESSIONS_DIR, args.open)
        session = Session.load(folder)
        session.acquire()
    except SessionBusy as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if session.current_revision is None:
        print(f"error: {folder.name} was recorded before PalmCards kept the notes; bind them first: "
              f"python -m palmcards.speech {folder} --rebind", file=sys.stderr)
        return 1
    notes = from_snapshot(session.snapshot(session.current_revision))
    try:
        return run(session.notes, notes, b"", lang=session.language, trace=args.trace, follow=not args.no_follow,
                   session=session, gaze_check=args.gaze_check, video=args.video,
                   devices=Devices(llm=lambda: get_provider(args.llm)))
    except CameraError as exc:
        print(exc, file=sys.stderr)
        return 1


def run(path: Path, notes: Notes, source: bytes, lang: str = SPEECH.language, trace: bool = False,
        devices: Devices | None = None, sessions_root: Path = SESSIONS_DIR, follow: bool = FOLLOW.enabled,
        session: Session | None = None, prefs_file: Path | None = None, gaze_check: bool = False,
        video: bool = False) -> int:
    """Open everything, run the frame loop, close everything.

    Every resource is registered for cleanup as soon as it exists, so a
    failure while opening the next one, a camera or drawing error mid-loop,
    or Ctrl-C still closes what was opened, in reverse order, each step on
    its own; a take being recorded is kept."""
    devices = devices or Devices()
    sentences = notes.sentences
    with ExitStack() as stack:
        camera = devices.camera()
        stack.callback(guarded(camera.release, "the camera"))
        tracker = devices.tracker()
        stack.callback(guarded(tracker.close, "hand tracking"))
        log = devices.log()
        stack.callback(guarded(log.close, "the gesture log"))
        trace_file = log.path.with_suffix(".trace.jsonl").open("w") if trace and log.path else None
        if trace_file:
            stack.callback(guarded(trace_file.close, "the trace"))
        if session is None:
            session = Session.create(path, root=sessions_root, gesture_log=log.path, language=lang, parsed=notes,
                                     source=source)
        takes = Takes(notes, session, log, devices, follow)
        takes.gaze_check = gaze_check
        takes.video_on = video  # or the preference, read with the others in frame_loop
        try:
            takes.vision = devices.vision()
        except Exception as exc:  # a model missing or failing to load: the takes go on without
            takes.vision_off = f"{type(exc).__name__}: {exc}"
            print(f"face and pose tracking off: {exc}", file=sys.stderr)
        if takes.vision is not None:
            stack.callback(guarded(takes.vision.close, "face and pose tracking"))
        stack.callback(guarded(takes.close, "the takes"))
        review = takes.restore()
        stack.callback(guarded(devices.destroy_windows, "the window"))
        try:
            frame_loop(camera, tracker, log, trace_file, takes, sentences, devices, "review" if review else None,
                       prefs_file)
        except BaseException:
            takes.interrupted = True
            raise
    return 0


def frame_loop(camera, tracker, log: GestureLog, trace, takes: "Takes", sentences, devices: Devices,
               start_mode: str | None = None, prefs_file: Path | None = None) -> None:
    frame = camera.read()
    h, w = frame.shape[:2]
    prefs = preferences.load(prefs_file)
    preferences.apply(prefs)  # before the overlay and the machines are built: box, holds, contrast
    takes.frame_size = (w, h)
    takes.replay_mirrored = prefs.replay_mirrored
    takes.video_on = takes.video_on or prefs.video
    if takes.video_on:
        if devices.video is VideoWriter and (why := video_unavailable()):
            takes.video_off = why
            print(f"video off: {why}", file=sys.stderr)
        else:
            print("video on: each take is recorded to take-NN.mp4 in the session folder")
    tutorial = Tutorial(active=not prefs.tutorial_done)
    overlay = TextOverlay(sentences, (w, h))
    modes = ModeMachine((w, h), log)
    if start_mode:
        modes.enter(start_mode, 0.0)
    grammar = modes.grammar
    grammar.defer_edit_commit = True
    view = ViewState(panel_motion=PanelMotion(), focus_motion=FocusMotion(), scroll_bounce=Spring())
    cues = Cues() if prefs.sounds else None
    ring_turn_seen = 0
    snap_panel = False  # the panel's scroll moved by a key: shown at once, not sprung
    show_debug = False
    note_until = None
    fps, work_ms, last = 0.0, 0.0, time.perf_counter()
    t0 = prev_start = last
    takes.t0 = t0
    devices.named_window(WINDOW, w, h)
    speaker = takes.speaker = devices.speaker()
    queued: list[GestureEvent] = []  # from keys, handled with the next frame's events
    page_t, page_pause_until = last, 0.0
    hint_after, rehearse_open_since = 0.0, None
    hint_note, hint_mode = "", ""  # the gesture hint shown, and the mode it was about
    notes_seen = takes.notes_version
    frame_index = 0

    while True:
        frame = camera.read()
        start = time.perf_counter()
        if takes.notes_version != notes_seen:  # e.g. undo from the previous frame's key handler
            notes_seen = takes.notes_version
            sentences = takes.notes.sentences
            overlay = TextOverlay(sentences, (w, h))
            grammar.reset()
            view.focus, view.edit_preview, view.ops = None, None, OpsView()
            view.mode, view.level = "idle", None
            view.current = min(view.current, len(sentences) - 1)
            view.scroll = overlay.clamp_scroll(view.scroll)
        tracker.submit(frame, start - t0)
        takes.push_video(frame, (getattr(camera, "last_t", 0.0) or start) - t0)  # the clean frame, as captured
        if takes.vision is not None:  # before anything is drawn on the frame; idle outside calibration and takes
            takes.vision.frame(frame, start - t0, frame_index, late=(start - prev_start) * 1000 > BODY.late_ms)
        frame_index += 1
        # Hand results arrive asynchronously, usually one frame behind.
        if (result := tracker.poll()) is not None:
            if trace:
                hands, t_hand = result
                trace.write(json.dumps({"t": round(t_hand, 3), "hands": [
                    {"label": hd.handedness, "points": hd.points.round(1).tolist()} for hd in hands]}) + "\n")
            modes.undo_ready = modes.mode == "prepare" and takes.can_undo()
            modes.retry_ready = modes.mode == "review" and bool(takes.analysis.failed())
            events = modes.update(*result)
            if takes.vision is not None:
                takes.vision.hands(*result)
        else:
            events = []
        events += modes.tick(start - t0)
        if modes.mode == "rehearse" and takes.check_over(start - t0):  # a gaze check ends itself
            modes.enter("review", start - t0)
            events.append(GestureEvent("take_stop", start - t0))
        snap_panel = snap_panel or any(ev.source == "key" for ev in queued)
        events, queued = queued + events, []
        if note := takes.poll_analysis():
            view.note, note_until = note, start + NOTE_S
        if note := takes.poll_llm():
            view.note, note_until = note, start + LLM_NOTE_S
        if note := takes.poll():
            view.note, note_until = note, start + NOTE_S
        takes.recording_problem()
        for ev in events:  # a drill rehearses the sentence focused now, before any handler clears the focus
            if ev.kind == "drill" and ev.sentence is None:
                ev.sentence = view.focus.sentence if view.focus else view.current
        for ev in events:
            if ev.kind == "undo":  # a V sign held in Prepare after an edit
                view.note, until = takes.undo(), start + NOTE_S
            elif ev.kind == "retry":  # an open palm held in Review with failed analysis
                view.note, until = takes.retry(), start + NOTE_S
            elif ev.kind in ("focus", "commit", "commit_ignored", "back", "rewind", "palm_hold", "palm_stop"):
                until = apply_event(ev, view, overlay, log, speaker, takes, grammar)
            elif note := takes.handle(ev, modes, view, overlay):
                view.note, until = note, start + NOTE_S
            else:
                until = None
            if until is not None:
                note_until = until
        view.app, view.now = modes.mode, start - t0
        if cues is not None:  # the sound on the frame the event is handled (Prepare, Review; nothing playing)
            cues.react(events, modes.mode, view.playing, grammar.state.ring_turn != ring_turn_seen, start - t0)
        ring_turn_seen = grammar.state.ring_turn
        view.llm = takes.llm
        view.llm_busy = bool(takes.assistant and takes.assistant.pending)
        view.title = "GAZE CHECK" if takes.gaze_check and modes.mode in ("count_in", "rehearse") \
            and takes.drill is None else ""
        view.calibration = takes.vision.phase(start - t0) \
            if takes.vision is not None and modes.mode == "count_in" else None
        view.hold_progress = modes.back_progress if modes.mode == "review" else modes.done.progress
        view.retry_progress, view.undo_ready = modes.retry_progress, modes.mode == "prepare" and takes.can_undo()
        view.undo_progress = modes.undo_progress if modes.mode == "prepare" else 0.0
        view.drill = takes.drill if modes.mode in ("count_in", "rehearse") else None
        if modes.mode in ("prepare", "review") and result is not None:
            sync_view(grammar, view, overlay)
        takes.sync_edit(view, overlay, start - t0)
        if modes.mode == "prepare":  # the optional LLM: what the focused word or unit has
            takes.sync_word(view, grammar.state.op)
            if view.focus is not None and view.focus.word is not None and grammar.state.op == "ring":
                grammar.set_ring_labels(start - t0, overlay.ring_labels(view))
                overlay.follow_ring(view, grammar.state.ring_pick, grammar.state.ring_turn)
        if modes.mode in ("prepare", "review"):
            view.start_progress = modes.start_progress
            if modes.mode == "review":
                takes.sync_review(grammar, view, overlay, start - t0)
            else:
                view.detail = ()
        else:
            if modes.mode == "rehearse":
                takes.poll_follow(view, overlay)
            view.section = takes.section
            view.count_in = max(1, math.ceil(modes.count_in_end - (start - t0)))
            view.rec_s = takes.recorder.seconds if modes.mode == "rehearse" and takes.recorder else 0.0
            view.mic = takes.recorder.level if takes.recorder else 0.0
            view.recording_video = takes.video is not None
            view.start_progress = 0.0
            view.detail = ()
        takes.sync_playback(grammar, view, overlay, start - t0)
        if tutorial.update(grammar.state, events, start - t0) and tutorial.done:
            prefs.tutorial_done = True
            preferences.save(prefs, prefs_file)
        view.tutorial = tutorial.card if modes.mode == "prepare" else None
        p = grammar.state.primary
        opened = modes.mode in ("count_in", "rehearse") and p is not None and p.stable == OPEN
        rehearse_open_since = (rehearse_open_since or start) if opened else None
        hint = nonactivation_hint(modes.mode, grammar.state, start - rehearse_open_since if rehearse_open_since else 0.0,
                                  start - t0)
        if hint and start >= hint_after and not view.note:
            view.note, note_until, hint_after = hint, start + 2 * NOTE_S, start + HINT_EVERY_S
            hint_note, hint_mode = hint, modes.mode
        # A gesture hint belongs to the mode it was about: a take stopping (the palm hint)
        # or a focus (a fist seen on the way into it) takes it away at once.
        focused = view.mode == "focus" or any(ev.kind == "focus" for ev in events)
        if hint_note and view.note == hint_note and (modes.mode != hint_mode or hint_note == FIST_HINT and focused):
            view.note, note_until, hint_note = "", None, ""
        if takes.notes_version != notes_seen:  # an edit or undo: lay the new notes out
            notes_seen = takes.notes_version
            sentences = takes.notes.sentences
            overlay = TextOverlay(sentences, (w, h))
            view.current = min(view.current, len(sentences) - 1)
            view.scroll = overlay.clamp_scroll(view.scroll)
        view.status = takes.status(modes.mode, view)
        view.alert = takes.alert_line(modes.mode)
        # A focused panel taller than the frame turns its own pages, so every
        # line is reachable without keys; a key pauses it.
        if view.app in ("prepare", "review") and view.mode == "focus" and (most := overlay.panel_max_scroll(view)):
            if start >= page_pause_until and start - page_t >= TEXT.page_s:
                page = overlay.panel_view_h(overlay.panel(view)) - overlay.line_h
                view.panel_scroll = 0.0 if view.panel_scroll >= most else min(most, view.panel_scroll + page)
                page_t = start
        elif view.app in ("prepare", "review"):
            view.panel_scroll, page_t = 0.0, start
        # Edge scrolling advances every displayed frame so it stays smooth; pushed
        # past the first or last row, the notes give a little (scroll_push).
        if view.app in ("prepare", "review") and view.mode == "browse" and grammar.state.scroll_rate:
            wanted = view.scroll + grammar.state.scroll_rate * (start - prev_start)
            view.scroll = overlay.clamp_scroll(wanted)
            view.scroll_push = view.scroll_push + (wanted - view.scroll) if wanted != view.scroll else 0.0
        else:
            view.scroll_push = 0.0
        prev_start = start
        if note_until is not None and start > note_until:
            view.note, note_until = "", None

        overlay.follow(view, snap=snap_panel)
        snap_panel = False
        if (replay := takes.replay_frame()) is not None:
            # A take's video while its clip plays, as others see you: nothing on
            # it but how far it has got. Hands are still tracked live, so a fresh
            # open palm stops it; the mirror and the notes come back after.
            frame = replay
            takes.draw_replay(frame, view.play_progress or 0.0)
        else:
            overlay.draw(frame, view)
            if prefs.show_hand_box and not show_debug and view.app in ("prepare", "review") and p is not None:
                draw_hand_area(frame, grammar.cursor, overlay.summary_box)
            if show_debug:
                if view.app in ("prepare", "review"):
                    draw_hand_box(frame, grammar.cursor)
                for track in (grammar.state.primary, grammar.state.secondary):
                    if track is not None:
                        draw_landmarks(frame, track.hand)
            draw_fingertips(frame, grammar.state)

        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
        work_ms = 0.9 * work_ms + 0.1 * (now - start) * 1000
        last = now
        # cam: rate the camera delivers; shown: rate we display;
        # hands: tracker latency; work: our per-frame processing.
        stats = (f"cam {camera.fps:4.1f}  shown {fps:4.1f} fps  "
                 f"hands {tracker.latency_ms:4.1f} ms  work {work_ms:4.1f} ms")
        draw_stats(frame, stats)

        devices.show(WINDOW, frame)
        key = devices.wait_key()
        if key in (ord("q"), 27):
            break
        now_t = time.perf_counter() - t0
        if key == ord("x") and takes.stop_playback("key", now_t):  # x stops any playback first
            view.note, note_until = "STOPPED", time.perf_counter() + NOTE_S
        elif key in KEY_COMMANDS:
            queued += modes.command(KEY_COMMANDS[key], now_t)
        elif key in (ord(" "), ord("j"), ord("k")):
            step = -1 if key == ord("k") else 1
            if view.app in ("count_in", "rehearse") and view.drill is None:  # within the section
                unit = takes.section_sentences()
                i = unit.index(view.current) if view.current in unit else 0
                view.current = unit[min(max(i + step, 0), len(unit) - 1)]
                view.panel_scroll, snap_panel = overlay.panel_scroll_to(view, view.current), True
                if takes.follow is not None:
                    takes.follow.jump(takes.section, view.current)  # the voice carries on from here
            elif view.mode == "focus" and overlay.panel_max_scroll(view):  # scroll the focused panel
                view.panel_scroll = overlay.clamp_panel_scroll(view, view.panel_scroll + step * 3 * overlay.line_h)
                snap_panel = True
                page_pause_until = time.perf_counter() + TEXT.page_pause_s
            elif view.app in ("prepare", "review"):
                view.current = min(max(view.current + step, 0), len(sentences) - 1)
                if not overlay.is_visible(view.current, view.scroll):
                    view.scroll = overlay.scroll_to(view.current)
        elif key == ord("h"):
            view.keys_help = not view.keys_help
        elif key == ENTER and tutorial.card is not None:
            tutorial.skip()
            if tutorial.done:
                prefs.tutorial_done = True
                preferences.save(prefs, prefs_file)
        elif key == ord("g"):
            tutorial.active = not tutorial.active if not tutorial.done else True
            if tutorial.done:
                tutorial.restart()
        elif key == ord("m"):  # a replay: mirrored, or as others see you (the default)
            prefs.replay_mirrored = takes.replay_mirrored = not prefs.replay_mirrored
            preferences.save(prefs, prefs_file)
            view.note = "REPLAYS MIRRORED" if prefs.replay_mirrored else "REPLAYS AS OTHERS SEE YOU"
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("c"):
            prefs.high_contrast = not prefs.high_contrast
            preferences.save(prefs, prefs_file)
            render.set_contrast(prefs.high_contrast)
            overlay = TextOverlay(sentences, (w, h))  # its cached text was drawn in the old colours
        elif key == ord("u") and view.app == "prepare":
            view.note, note_until = takes.undo(), time.perf_counter() + NOTE_S
        elif key == ord("a") and view.app in ("prepare", "review"):  # play, or stop what plays
            view.note = "STOPPED" if takes.stop_playback("key", now_t) \
                else play_focus(view, overlay, log, speaker, takes, now_t)
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("e"):
            view.note, note_until = takes.recalibrate(), time.perf_counter() + NOTE_S
        elif key == ord("d"):
            show_debug = not show_debug
        elif key == ord("r"):
            if takes.previews.operation is not None and takes.previews.operation.error:
                takes.previews.retry(time.perf_counter() - t0)
                view.note = "RETRYING PREVIEW" if takes.previews.operation.loading else takes.previews.operation.error
            else:
                view.note = takes.retry()
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("s"):
            SCREENS_DIR.mkdir(parents=True, exist_ok=True)
            shot = SCREENS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.png"
            cv2.imwrite(str(shot), frame)
            log(time.perf_counter() - t0, "screenshot", path=shot.name)
            print(f"saved {shot}")
        if not devices.window_open(WINDOW):
            break

    if modes.mode == "rehearse":  # quit mid-take: keep what was recorded, as a normal take
        takes.handle(GestureEvent("take_stop", time.perf_counter() - t0), modes, view, overlay)


if __name__ == "__main__":
    sys.exit(main())
