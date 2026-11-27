"""PalmCards entry point.

  python main.py [NOTES_FILE] [--lang xx] [--trace] [--no-follow] [--llm off|ollama|anthropic]
  python main.py --open RUN      reopen a saved session (a folder, or its name under sessions/) in Review
  python main.py --gaze-check    every take is a gaze check: timed prompts (camera, notes, away), then it
                                 stops itself; scripts/evaluate.py --gaze RUN compares them with the classifier
      NOTES_FILE: .txt, .md or .docx, or slides as .pdf; defaults to the file opened last, else one picked
                  in an Open dialog.
                  The same file, unchanged, continues its last session (--new: a new one).
                  Reopened by itself (no file named), the home card shows first: switch speech in the app
      --lang: language spoken in the takes, for Whisper (default en)
      --trace: also record every hand result's landmarks, for offline replay
      --llm: the optional LLM for word meanings, alternatives and rewrites (default: off,
             or config LLM.provider). "anthropic" is the cloud: the sentence or paragraph you ask
             about leaves the Mac (ANTHROPIC_API_KEY from the environment or .env); a CLOUD AI
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
  a play the sentence or paragraph (Review), again to stop, u undo the last edit (Prepare), r retry failed analysis,
  e calibrate the eyes again at the next take,
  g the gesture tutorial (Enter skips a step), c high contrast, h keys, q/Esc quit,
  l the home card (Prepare, Review): switch speech, start it over, or open a file; f full screen,
  m flip a take's replayed video: mirrored, or as others see you (the default),
  w fading cards on or off (also --fade, preferences `fade`).
Preferences (hand reach, hold times, contrast): python -m palmcards.prefs
Dev keys: d toggle landmarks and hand box, s save a screenshot to sessions/screens/.
"""

import argparse
import gc
import json
import math
import secrets
import threading
import sys
import time
import traceback
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from palmcards.align import counts
from palmcards.capture import AudioRecorder, Camera, CameraError
from palmcards.config import ANALYSIS, BODY, FADE, FOLLOW, LLM, QA, RECORDING, REHEARSE, SLIDES, SPEECH
from palmcards import coach, fade, features, gaze, progress
from palmcards import prefs as preferences
from palmcards import render
from palmcards.gestures import FIST, OPEN, THUMB_UP, GestureEvent, GestureLog, Grammar, HandTracker, ModeMachine
from palmcards.tutorial import Tutorial
from palmcards.notes import Notes, normalize, notes_from_bytes
from palmcards.edit import replace_text, replace_word
from palmcards.preview import Previews
from palmcards.llm import PROVIDERS, Assistant, LLMUnavailable, alternatives_request, answers_coach_request, \
    coach_request, describe, \
    get_provider, meaning_request, parse_coach, parse_meaning, parse_alternatives, parse_questions, parse_rewrite, \
    questions_request, rewrite_request
from palmcards.render import (
    DROP_HINT, HINT_SEP, REVIEW_SEP, UNDO_HINT, FocusMotion, Presenter, Teleprompter, Hit, OpsView, PanelMotion, TextOverlay, ViewState,
    draw_fingertips, draw_hand_area, draw_hand_light, draw_hand_box, draw_landmarks, draw_replay_bar, draw_replay_caption, draw_replay_hint,
    draw_stats,
)
from palmcards.motion import Spring
from palmcards.review import Board
from palmcards.sounds import Cues
from palmcards.pick import Picker
from palmcards.playback import ClipPlayer, Playback, clip_marks, clip_span, replay_words, slide_span, span_clip
from palmcards.video import VideoReader, VideoWriter
from palmcards.video import unavailable as video_unavailable
from palmcards.recording import TakeWriter
from palmcards.revisions import from_snapshot
from palmcards.session import SESSIONS_DIR, Session, SessionError, recover_all
from palmcards.style import TEXT
from palmcards.analysis import Supervisor
from palmcards.data import latest_session_for, started
from palmcards.paths import logs_dir
from palmcards.home import Home, Next, entries as home_entries, launch_entries
from palmcards.picker import pick_notes, pick_slides
from palmcards.slides import Deck
from palmcards.notch import NotchPanel
from palmcards.asr import get_recognizer
from palmcards.follow import AutoStop, LiveFollow
from palmcards.speech import make_job
from palmcards.easter import Friend
from palmcards.vision import Watcher

SAMPLE = Path(__file__).parent / "samples" / "sample_notes.md"
SCREENS_DIR = SESSIONS_DIR / "screens"
WINDOW = "PalmCards"
CARD_SIZE = (1280, 720)  # the window before the camera's first frame sets its size
NOTE_S = 1.5  # how long a commit message stays in the label
CONTINUE_NOTE_S = 4.0  # a continued session says so this long
LLM_NOTE_S = 3.0  # an LLM answer or failure arrives while you're doing something else: it stays longer
HINT_EVERY_S = 6.0  # a hint about a gesture that didn't act is shown at most this often
CAPTION_LINGER_S = 0.15  # a replay's caption still marks a word as being said this long after it ends
ENTER = 13
IN_LOG = "(details in the log)"
KEY_REJECTED = "Cloud AI: the API key was rejected. Check ANTHROPIC_API_KEY."


def short(reason: BaseException | str | None, limit: int = 40) -> str:
    """A problem's reason, short enough for the label or the alert line.
    The whole of it goes to the log."""
    if isinstance(reason, BaseException):
        reason = getattr(reason, "strerror", None) or str(reason) or type(reason).__name__
    text = (str(reason or "").strip().splitlines() or [""])[0]
    text = text[:1].lower() + text[1:] if text[1:2].islower() else text  # it follows a colon
    if not text:
        return IN_LOG
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def play_focus(view: ViewState, overlay: TextOverlay, takes: "Takes | None") -> str:
    """Review: play the focused sentence or paragraph from the take it shows;
    the `a` key does the same with nothing focused on the current sentence.
    Returns a label note. (Prepare plays nothing: your own takes are the only
    voice PalmCards plays.)"""
    sentence = view.focus.sentence if view.focus is not None else view.current
    if view.app != "review" or takes is None:
        return ""
    if takes.slides_only:  # a PDF speech: the slide's part of the newest take that showed it
        return takes.play_slide(takes.notes.sentences[sentence].paragraph)
    if view.mode == "focus" and view.level == "paragraph":
        return takes.play_paragraph(overlay.unit("paragraph", sentence))
    return takes.play_sentence(sentence)


def play_target(view: ViewState, overlay: TextOverlay, board: Board) -> tuple | None:
    """What the screen would play now: (app, sentences, take). Playback stops
    when this no longer matches what plays: another take picked, the hover
    moved to another sentence, the focus gone, another mode."""
    if view.app != "review":
        return None
    focused = view.mode == "focus" and view.focus is not None
    sentence = view.focus.sentence if focused else view.current
    if focused and view.level == "paragraph":
        unit = tuple(overlay.unit("paragraph", sentence))
        return "review", unit, board.paragraph_shown(unit)
    return "review", (sentence,), board.shown(sentence)


def slide_hints(view: ViewState) -> str:
    """A PDF speech in Prepare and Review: the gestures that act now, in slides' words (the
    label's hint row, after HINT_SEP)."""
    if view.mode == "focus":
        if view.app == "review":
            hints = ["Hold an open palm to stop"] if view.playing else ["Hold an open palm to play this slide"]
            return HINT_SEP + REVIEW_SEP.join(hints + [DROP_HINT])
        return HINT_SEP + DROP_HINT
    if view.mode == "browse":
        hints = ["Move up or down to flip"] + (["Tap to select this slide"] if view.app == "review" else [])
        return HINT_SEP + REVIEW_SEP.join(hints)
    return HINT_SEP + REVIEW_SEP.join(["Point a finger to flip through the slides", "Raise a fist to record"])


FIST_HINT = "To record, lower your hand, then raise a\u00a0fist"  # never "fist" alone on a row
FOLD_HINT = "To select, tap: curl your fingers and straighten them quickly"  # a slow curl is a fist: no tap, no take
PALM_HINT = {"rehearse": "An open palm doesn't stop recording", "count_in": "An open palm doesn't cancel"}
THUMB_HINT = {"rehearse": "A thumbs-up doesn't stop recording", "count_in": "A thumbs-up doesn't cancel"}
HINT_DWELL_S = 0.6  # a gesture that does nothing held this long before its hint (a fist formed from
#                     another pose: a fold or a slow pinch passes through a fist on its way to a focus;
#                     an open palm in a take)
FIST_HINT_S = HINT_DWELL_S


def nonactivation_hint(mode: str, gs, open_s: float, t: float | None = None) -> str:
    """Why a gesture the camera sees is not doing anything, when that's likely
    to puzzle: a fist formed from another pose and held; in a take or count-in,
    an open palm or a thumbs-up held (neither stops a take now; the label's hint
    row says what does)."""
    p = gs.primary
    if p is None:
        return ""
    if mode in ("prepare", "review") and p.stable == FIST and p.first_pose != FIST and gs.mode != "focus" \
            and (t is None or p.held(t) >= FIST_HINT_S):
        return FOLD_HINT if gs.mode == "browse" and gs.level is not None else FIST_HINT
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
    ("prepare", "word"): "Nothing to use yet. Hold an open palm for other words first.",
    ("prepare", "sentence"): "Nothing to use yet. Make an L to change the tone first.",
    ("prepare", "paragraph"): "Nothing to use yet. Make an L with each hand to change the length first.",
}


def apply_event(ev: GestureEvent, view: ViewState, overlay: TextOverlay, log: GestureLog,
                takes: "Takes | None" = None, grammar: Grammar | None = None) -> float | None:
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
        return None
    if ev.kind == "commit_ignored":  # nothing to commit yet: the focus stays, and what comes first
        view.note = COMMIT_FIRST.get((view.app, ev.level), "") if view.app == "prepare" else \
            "To practise, select a sentence, then pinch and lift"
        return time.perf_counter() + NOTE_S if view.note else None
    if ev.kind == "rewind":  # a pinch took the pointer back to before the curl: the pick goes back too
        if takes is not None:
            takes.rewind_pick(ev.value)
        return None
    if ev.kind == "palm_hold":  # an open palm held on the focus (Review): play it
        view.note = play_focus(view, overlay, takes)
        return time.perf_counter() + NOTE_S if view.note else None
    if ev.kind == "palm_stop":  # a new open palm while it plays: stop it; the focus stays
        if takes is not None and takes.stop_playback("palm", ev.t):
            view.note = "Stopped"
            return time.perf_counter() + NOTE_S
        return None
    if ev.kind == "commit" and view.app == "review":
        if ev.level != "sentence":  # a sentence commit is a drill, which the mode events start
            view.note = "To practise, select a sentence, then pinch and lift"
    elif ev.kind == "commit":
        sentence = view.focus.sentence if view.focus else None
        unit = tuple(overlay.unit(ev.level, sentence)) if sentence is not None else ()
        picked = None
        if ev.op == "ring":
            labels = overlay.ring_labels(view)
            picked = labels[view.ops.picked] if 0 <= view.ops.picked < len(labels) else None
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
            view.note = "No preview yet: this needs the optional AI"
            return time.perf_counter() + NOTE_S
        else:
            view.note = "Nothing changed"
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
        # From the notes file itself (not its revisions): a "Time: 3:00" line and a Questions section.
        self.target_file, self.file_questions = notes.target_s, list(notes.questions)
        self.section_t = 0.0  # app time the current section began, in a take (for its time budget)
        # The Q&A round (key y): after a full take, questions one at a time, each answer a take of its own.
        self.qa_on = False
        self.qa_queue: list[str] = []  # the round's questions still to come
        self.qa_of = 0  # questions in the round
        self.qa_next_at: float | None = None  # app time the next question's count-in starts
        self.answer: tuple[int, int, str] | None = None  # the answer being recorded: (k, of, question)
        self.answers: dict[int, dict] = {}  # answer take -> what was measured (coach.advise_answers)
        self.devices = devices or Devices()
        # Slides (palmcards.slides): a PDF speech is its own deck, and the clicker leads (no voice
        # follow); written notes can have a PDF attached (the home card). open_slides() opens it.
        self.slides_only = session.notes.suffix.lower() == ".pdf"
        self.follow_enabled = follow and not self.slides_only
        self.deck: Deck | None = None
        self.slide = 0  # the slide shown, 0-based
        self.slide_log: list | None = None  # the take's [[t, slide], ...] (TakeRecord.slides); None: no slides
        self.slide_heard = 0.0  # stopper.heard_s when the clicker reached this slide (the end of a PDF speech)
        self.slide_width = 0
        self.follow: LiveFollow | None = None  # the voice follow, made at the first count-in
        self.player = None  # clip playback (Review), made at the first play
        self.playback = Playback()  # what plays now (a take's clip), for which target
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
        self.switching = False  # closing to open another speech: nothing waits (analysis left queued)
        self._closed = False
        self.last_saved = ""
        self.board = Board(notes)  # the analysed takes, side by side, for Review
        self.drill_end = None
        self._practice = None
        self.drill: int | None = None  # the sentence the current count-in or take drills
        self._summary_key, self._summary = None, ()  # Review's take table, and what it was made from
        self._advice = None  # the coach's advice for the newest take (palmcards.coach)
        self.ai_advice: dict[int, tuple[str, str, str]] = {}  # take -> the AI coach's (well, work, next)
        self.panel_view = "coach"  # Review, bottom right: "coach" card, take "table" or "days" (key i cycles)
        self._days, self._ready = (), ""  # the Days view and the readiness line, made with the coach
        # Every other session of these notes (progress over days), read in the background.
        self.history: list[progress.Point] = []
        threading.Thread(target=self._load_history, name="history", daemon=True).start()
        self.vision: Watcher | None = None  # face and pose (milestone 7); run() opens it
        self.vision_off = "face and pose tracking not opened"  # why there is none
        self.calibrate_next = False  # e: calibrate the eyes again at the next count-in
        self.gaze_check = False  # main.py --gaze-check: every full take is a gaze check
        self.check: dict | None = None  # the gaze check being run: {"seed", "t0", "prompts"}
        self.stopper = AutoStop()  # the take ending itself after the last sentence (a drill: after it)
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
        # Fading cards (palmcards.fade; preference `fade`, key w, main.py --fade): the words the
        # take dims and blanks, what the stall prompt brought back, and since when the voice is quiet.
        self.fade_on = False
        self.fade_dimmed: frozenset = frozenset()
        self.fade_hidden: frozenset = frozenset()
        self.fade_prompted: list[tuple[int, int]] = []
        self._fade_quiet: tuple[int, float] = (0, 0.0)  # (live words heard, app time the count last grew)

    @property
    def target_s(self) -> float | None:
        """The talk's target length: set with [ and ] (kept in the session), else the notes' Time: line."""
        return self.session.target_s or self.target_file

    def set_target(self, step: float) -> str:
        """Keys [ and ]: the target length 15 s shorter or longer (from an estimate at 150
        words a minute when none is set); under 15 s, off."""
        words = sum(len(s.words) for s in self.notes.sentences)
        now = self.target_s or round(words / 150 * 60 / 15) * 15
        new = now + step if self.target_s else now
        self.session.target_s = new if new >= 15 else None
        try:
            self.session.save()
        except (OSError, SessionError) as exc:
            return f"Couldn't keep the target: {short(exc)}"
        self._summary_key = None  # the coach looks again
        return f"Target length {coach.mmss(new)}" if new >= 15 else "No target length"

    def status(self, mode: str, view: ViewState) -> str:
        """Second label line when nothing more pressing is shown. In Review
        only what the focus shows, or how the takes are doing: the gestures
        that act are added by the renderer (render.review_hint)."""
        if mode == "rehearse" and self.check is not None:
            now = time.perf_counter() - self.t0 - self.check["t0"]
            prompts = self.check["prompts"]
            i = next((k for k, p in enumerate(prompts) if now < p["t1"]), len(prompts) - 1)
            return f"{gaze.prompt_text(prompts[i])}  {i + 1}/{len(prompts)}"
        if mode in ("count_in", "rehearse") and self.answer is not None:
            return "Answer it aloud: the answer first, then one reason"
        if mode in ("count_in", "rehearse") and self.drill is not None:
            return (f"Sentences {self.drill + 1}–{self.drill_end + 1}" if self.drill_end is not None
                    else f"Sentence {self.drill + 1}")
        if mode in ("count_in", "rehearse"):
            title = self.notes.sections[self.section].title or "Untitled"
            fading = " · Fading cards" if view.faded is not None else ""
            budget = ""
            if mode == "rehearse" and self.target_s:  # this section's share of the target, counting down
                left = coach.budgets(self.notes, self.target_s)[self.section] - (time.perf_counter() - self.t0
                                                                                 - self.section_t)
                budget = f" · {coach.mmss(left)} {'left' if left >= 0 else 'over'}"
            if self.slides_only and (shown := self.slide_shown) is not None:  # a PDF speech: the slide, not the section
                return f"Slide {shown + 1} of {self.deck.pages}{budget}{fading}"
            return f"Section {self.section + 1} of {len(self.notes.sections)}: {title}{budget}{fading}"
        if mode == "review" and view.mode == "focus" and view.level == "sentence" and view.focus is not None:
            said = self.board.said_in(view.focus.sentence)
            if len(said) > 1:
                return self.board.take_label(view.focus.sentence)
            if said and len(self.board.rows) == 1:  # one take in all: nothing to compare it with
                return self.board.take_name(said[0])
            return f"Only take {said[0]} has this sentence" if said else "No take has this sentence yet"
        if mode == "review" and view.mode == "focus" and view.level == "paragraph" and view.focus is not None:
            n = self.board.paragraph_shown(self.paragraph(view.focus.sentence))
            return self.board.take_name(n) if n is not None else "No take has this paragraph yet"
        if self.finalizing:
            return f"Saving take {self.finalizing[0].number}…"
        queue = list(self.analysis.pending)
        queue += [n for n in self.deferred if n not in queue]
        if queue:
            dots = "." * (int(time.perf_counter() * 2) % 4)
            waiting = f" · {len(queue) - 1} waiting" if len(queue) > 1 else ""
            return f"Analysing take {queue[0]}{dots:<3}{waiting}"
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
            if take.alignment is not None and take.answer:  # an answer: for the round's coaching, off the board
                try:
                    words = json.loads((self.session.dir / take.transcript).read_text())["words"]
                except (OSError, ValueError, KeyError, TypeError):
                    words = []
                self.answers[take.number] = self._answer_facts(take, words, take.alignment)
            elif take.alignment is not None:
                self.board.add(take.number, take.alignment, take.metrics, take.drill,
                               self.session.sentence_map(take), take.duration_s, take.fade)

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

    LLM_WHAT = {"meaning": "the meaning", "alternatives": "other words", "tone": "a new tone",
                "length": "a new length", "coach": "AI coaching", "questions": "practice questions"}

    @property
    def no_llm(self) -> str:
        why = short(self.llm_off) if self.llm_off else "not set up, see the README"
        return f"this needs the optional AI ({why}). Nothing was sent."

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
            return False, "The preview isn't ready yet"
        if not self.previews.can_commit(view.edit_preview, value):
            if op.error:
                self.previews.retry(now)
                return False, "Trying the preview again…" if op.loading else op.error
            return False, "The preview isn't ready yet"
        if op.displayed_target == op.original_target or op.displayed_candidate == op.original:
            self.previews.cancel()
            return True, "Kept the original: nothing changed"
        error = self._save_edit(replace_text(self.notes, list(unit), op.displayed_candidate),
                                f"{op.kind} preview committed", op=op.kind, sentences=list(unit))
        return (False, error) if error else (True, f"Edit saved  /  {UNDO_HINT}")

    def ask_rewrite(self, kind: str, unit: tuple[int, ...], amount: float) -> str:
        what = "Tone" if kind == "tone" else "Length"
        if self.assistant is None:
            return f"{what} edits: {self.no_llm}"
        text = " ".join(self.notes.sentences[i].text for i in unit)
        self.assistant.ask(kind, unit, self._llm_revision(),
                           rewrite_request(kind, text, amount), lambda reply: parse_rewrite(reply, text, kind, amount))
        self.log(time.perf_counter() - self.t0, "llm", ask=kind, sentences=list(unit), amount=amount)
        which = "warmer" if kind == "tone" and amount > 0 else "more formal" if kind == "tone" else \
            "longer" if amount > 1 else "shorter"
        return f"Asking for a {which} version…"

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
                self.llm_alert = KEY_REJECTED
            if self.previews.accept(a, self._llm_revision()):
                continue
            if a.revision != self._llm_revision():
                note = "The notes changed, so the suggestion was dropped"
                continue
            if a.error:
                print(f"LLM {a.kind}: {a.error}", file=sys.stderr)
                if a.reason == "API KEY REJECTED":
                    self.llm_alert = KEY_REJECTED
                note = f"Couldn't get {self.LLM_WHAT.get(a.kind, a.kind)}: {a.reason.lower().replace("api ", "API ")}. Nothing changed."
                self.llm_failed.add((a.kind, a.key))
                continue
            if a.kind == "meaning":
                self.meanings[a.key] = a.value
                continue
            if a.kind == "alternatives":
                self.alternatives[a.key] = tuple(a.value)
                continue
            if a.kind == "coach":
                self.ai_advice[a.key[0]] = a.value
                note = "The AI coach's notes are on the card"
                continue
            if a.kind == "questions":
                self.session.questions = list(a.value)
                try:
                    self.session.save()
                except (OSError, SessionError) as exc:
                    print(f"couldn't keep the questions: {exc}", file=sys.stderr)
                note = f"{len(a.value)} practice questions ready: they follow your next full take"
                continue
            self.proposals[a.key] = (a.kind, a.value, a.value)
            note = "A suggestion is ready: select it again to see it"
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
            return f"The edit wasn't saved: {short(exc)}"
        self._use_notes(self.session.current_notes())
        self.log(time.perf_counter() - self.t0, "edit", revision=rid, **log)
        return ""

    def use_alternative(self, sentence: int, word: int, text: str) -> str:
        old = self.notes.sentences[sentence].words[word].text
        note = self._save_edit(replace_word(self.notes, sentence, word, text), f'"{old}" -> "{text}"',
                               op="alternative", sentence=sentence, word=word, text=text)
        return note or f'“{old}” → “{text}”  /  {UNDO_HINT}'

    def use_proposal(self, unit: tuple[int, ...]) -> str:
        kind, value, _ = self.proposals.pop(unit)
        note = self._save_edit(replace_text(self.notes, list(unit), value), f"{kind} proposal used", op=kind,
                               sentences=list(unit))
        return note or f"{kind.capitalize()} suggestion used  /  {UNDO_HINT}"

    def can_undo(self) -> bool:
        """An edit to undo: the current notes revision has a parent."""
        rid = self.session.current_revision
        return rid is not None and self.session._parsed is None and self.session.revision(rid)["parent"] is not None

    def undo(self) -> str:
        if self.session.current_revision is None or self.session._parsed is not None:
            return "Nothing to undo"
        rid = self.session.undo()
        if rid is None:
            return "Nothing to undo"
        self._use_notes(self.session.current_notes())
        self.log(time.perf_counter() - self.t0, "undo", revision=rid)
        return "Undone"

    def export_notes(self, t: float) -> str:
        """Key o in Review: the current notes revision to a new file in the session's exports/ folder,
        as `python -m palmcards.export RUN` does by default (a .docx takes about 40 ms: no worker)."""
        from palmcards import export

        rid = self.session.current_revision
        if rid is None or self.session._parsed is not None:  # nothing saved yet
            return "Nothing to export yet"
        fmt = self.session.notes.suffix.lower().lstrip(".").replace("markdown", "md")
        fmt = fmt if fmt in export.FORMATS else "txt"
        dest = self.session.dir / "exports" / f"{self.session.notes.stem}-{rid}.{fmt}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            export.write(self.notes, dest, fmt, self.session.notes)
            note = f"Exported to exports/{dest.name}"
        except FileExistsError:  # this revision was exported before: it is still there
            note = f"Already exported: exports/{dest.name}"
        except OSError as exc:
            print(f"cannot export: {exc}", file=sys.stderr)
            self.log(t, "key", command="export", mode="review", acted=False, error=str(exc))
            return f"Export failed: {short(exc)}"
        print(f"exported {dest}")
        self.log(t, "key", command="export", mode="review", acted=True, path=str(dest))
        return note

    def play_sentence(self, sentence: int) -> str:
        """Play a sentence (current notes' index) from the take Review shows for it."""
        n = self.board.shown(sentence)
        if n is None:
            return "No take to play"
        if not self._play(n, [sentence]):
            return self._play_error or f"Take {n} doesn't have this sentence"
        self.log(time.perf_counter() - self.t0, "play", take=n, sentence=sentence)
        return f"Playing take {n}"

    def play_paragraph(self, sentences: list[int]) -> str:
        """Play a paragraph (current notes' indices) from the take Review shows for it:
        from its first word said to its last."""
        n = self.board.paragraph_shown(sentences)
        if n is None:
            return "No take to play"
        if not self._play(n, sentences):
            return self._play_error or f"Take {n} doesn't have this paragraph"
        self.log(time.perf_counter() - self.t0, "play", take=n, sentences=list(sentences))
        return f"Playing take {n}"

    def slide_of_row(self, view: ViewState, current: bool = False) -> int:
        """A PDF speech: the slide of the outline row the hand is on (browsing, or selected), else of
        the current one (paragraph N is slide N). `current`: the current row's, whatever the hand does."""
        row = view.current
        if not current and view.mode == "browse" and view.hover is not None:
            row = view.hover.sentence
        elif not current and view.mode == "focus" and view.focus is not None:
            row = view.focus.sentence
        return self.notes.sentences[min(row, len(self.notes.sentences) - 1)].paragraph

    def row_of_slide(self, slide: int) -> int:
        """The first outline row of a slide (clamped to the deck)."""
        slide = min(max(slide, 0), self.notes.sentences[-1].paragraph)
        return next(s.index for s in self.notes.sentences if s.paragraph == slide)

    def slide_take(self, slide: int) -> int | None:
        """The newest take that showed this slide (a PDF speech's Review plays it)."""
        return next((t.number for t in reversed(self.session.takes) if slide_span(t, slide) is not None), None)

    def slide_target(self, view: ViewState) -> tuple | None:
        """What a PDF speech's Review would play now (see play_target): the slide shown, from its take."""
        if view.app != "review" or not self.notes.sentences:
            return None
        sentence = view.focus.sentence if view.mode == "focus" and view.focus is not None else view.current
        slide = self.notes.sentences[min(sentence, len(self.notes.sentences) - 1)].paragraph
        return "review", ("slide", slide), self.slide_take(slide)

    def play_slide(self, slide: int) -> str:
        """A PDF speech: play the slide's part of the newest take that showed it, from when the
        clicker brought it up to when it moved on, with the take's video when it has one."""
        n = self.slide_take(slide)
        if n is None:
            return "No take showed this slide yet"
        if not self._play(n, [], slide_span(self.session.take(n), slide), ("slide", slide)):
            return self._play_error or "Nothing to play"
        self.log(time.perf_counter() - self.t0, "play", take=n, slide=slide)
        return f"Playing take {n}, slide {slide + 1}"

    def _play(self, n: int, sentences: list[int], span: tuple[float, float] | None = None,
              target: tuple | None = None) -> bool:
        """Play take n's audio for these sentences (current notes' indices;
        sentences edited since the take are left out), or for `span` (app times). False if
        there is nothing to play, or it failed (then _play_error says so)."""
        self._play_error = ""
        take = self.session.take(n)
        if span is None:
            own = {cur: old for old, cur in (self.session.sentence_map(take) or {}).items()}
            mine = [own[i] for i in sentences if i in own]
            clip = span_clip(self.session, take, mine) if mine else None
            span = clip_span(take, mine) if clip is not None else None
        else:
            clip = span_clip(self.session, take, [], span)
        if clip is None:
            return False
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
            self.playback.start_clip(self.clip_player(), clip, ("review", target or tuple(sentences), n),
                                     time.perf_counter(),
                                     video=reader, video_t0=span[0])
        except Exception as exc:  # no output device
            print(f"could not play: {exc}", file=sys.stderr)
            self._play_error = f"Couldn't play it: {short(exc)}"
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
        """The player for clips (Review's), made at the first play."""
        if self.player is None:
            self.player = self.devices.player()
        return self.player

    def stop_playback(self, why: str, t: float) -> bool:
        """Stop what plays (a take's clip). True if something was playing."""
        if not self.playback.stop():
            return False
        self.log(t, "play_stop", why=why)
        return True

    def sync_playback(self, grammar: Grammar, view: ViewState, overlay: TextOverlay, t: float) -> None:
        """Every frame: forget what has finished, stop what the screen no longer
        shows, and hold the focus while its audio plays."""
        now = time.perf_counter()
        target = self.slide_target(view) if self.slides_only else play_target(view, overlay, self.board)
        if self.playback.poll(now) and self.playback.target != target:
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
            return f"Voice follow is off: {short(self.follow.error)}  /  N: next section"
        if failed := self.analysis.failed():
            return f"Couldn't analyse take {failed[0]} {IN_LOG}  /  Hold an open palm or press R to retry"
        return ""

    def sync_review(self, grammar: Grammar, view: ViewState, overlay: "TextOverlay | None" = None,
                    t: float = 0.0) -> None:
        """Review: the takes to point at, the focused sentence in each take, and the take table."""
        view.practice = None
        gs = grammar.state
        focused = view.focus.sentence if view.mode == "focus" and view.focus is not None else None
        said = self.board.said_in(focused) if focused is not None and view.level == "sentence" else []
        if said:  # the takes as chips beside the sentence, one of them pointed at
            picker = self.pickers["take"]
            shown = self.board.shown(focused)
            if gs.point is None:
                picker.index = said.index(shown) if shown in said else len(said) - 1
            view.takes = tuple(f"Take {n}" + (" (practice)" if n in self.board.drills else "") for n in said)
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
        last_answer = max(self.answers, default=None)
        if focused is None and (n is not None or last_answer is not None):  # the take table and the coach, anew per take
            key = (n, n in self.board.metrics, id(self.board), last_answer, self.target_s, len(self.history))
            if key != self._summary_key:
                self._summary_key, self._summary = key, self.board.take_table()
                every = sorted(self.history + progress.points(self.session), key=lambda p: p.when)
                self._days, self._ready = progress.days_table(every, self.target_s), progress.readiness(every, self.target_s,
                                                                                            points=not self.slides_only)
                if last_answer is not None and last_answer > (n or 0):  # the newest is a Q&A round
                    since = max((t for t in self.board.takes if t < last_answer), default=0)
                    self._advice = coach.advise_answers([self.answers[m] for m in sorted(self.answers) if m > since])
                else:
                    self._advice = coach.advise(self.board, n, self.target_s, outline=self.slides_only)
                self._practice = None
                if self._advice and last_answer is not None and last_answer > (n or 0):
                    self._practice = coach.Practice("Practice your next answer", self._advice.work,
                                                    "Q&A review", "Start full take")
                elif self._advice:
                    record = next((t for t in self.session.takes if t.number == n), None)
                    pair = None
                    if record and record.drill_end is not None and record.revision == self.session.current_revision:
                        pair = (record.drill, record.drill_end)
                    self._practice = coach.practice(self.board, n, self._advice, self.slides_only, pair)
                # The sentence to practise is the current one (orange), in view, ready to select.
                if self._advice is not None and self._advice.sentence is not None and overlay is not None \
                        and gs.mode == "idle":
                    view.current = self._advice.sentence
                    view.scroll = overlay.scroll_to(view.current)
            view.summary = self._days if self.panel_view == "days" else self._summary
            a = self._advice
            ai = self.ai_advice.get(self._coach_subject())  # the AI's, on the take or the round the card is about
            view.practice = self._practice if self.panel_view == "coach" else None
            if self.panel_view not in ("coach", "detail") or a is None:
                view.coach = ()
            elif ai is not None:  # the AI coach's, asked for with an open palm
                view.coach = (("Went well · AI", ai[0]), ("Work on · AI", ai[1]), ("Next · AI", ai[2]),
                              ("Readiness", self._ready))
            else:
                view.coach = (("Went well", a.well), ("Work on", a.work), ("Next", a.next), ("Readiness", self._ready))
        else:
            view.summary, view.coach = (), ()

    def _load_history(self) -> None:
        stem = self.session.dir.name[16:].rsplit("-", 1)[0]
        try:
            self.history = progress.history(self.session.dir.parent, stem, skip=self.session.dir)
        except OSError as exc:  # progress is extra: never let it take the app down
            print(f"progress over days: {exc}", file=sys.stderr)

    def notes_text(self) -> str:
        """The whole notes as plain text, sections headed: what the AI reads (coach, questions)."""
        return "\n\n".join((f"# {sec.title}\n" if sec.title else "") + " ".join(s.text for s in sec.sentences)
                            for sec in self.notes.sections)

    def questions(self) -> list[str]:
        """The practice questions: the notes' Questions section, else the ones the AI suggested."""
        return self.file_questions or self.session.questions

    def toggle_qa(self, on: bool, t: float) -> str:
        """Key y: the Q&A round on or off. On with no questions: the AI is asked for some (the
        key is the request), or the label says how to add them."""
        self.qa_on = on
        if not on:
            self.qa_queue, self.qa_next_at = [], None
            return "Q&A off"
        if self.questions():
            return f"Q&A on: {min(QA.per_round, len(self.questions()))} questions after each full take"
        if self.assistant is None:
            return "Q&A on: add a “Questions” section to your notes, or start with --llm for the AI's"
        if not self.assistant.asking("questions", ("questions",)):
            self.assistant.ask("questions", ("questions",), self._llm_revision(), questions_request(self.notes_text()),
                               parse_questions)
            self.log(t, "llm", ask="questions")
        return "Q&A on: asking the AI for likely questions…"

    def _pick_questions(self) -> list[str]:
        """The round's questions: the least answered so far first, in the list's order."""
        asked: dict[str, int] = {}
        for take in self.session.takes:
            if take.answer:
                asked[take.answer["question"]] = asked.get(take.answer["question"], 0) + 1
        qs = self.questions()
        return sorted(qs, key=lambda q: (asked.get(q, 0), qs.index(q)))[: QA.per_round]

    def qa_tick(self, t: float, modes: ModeMachine, view: ViewState, overlay: TextOverlay) -> str:
        """Every frame: when the round's next question is due (Review, nothing recording), its count-in."""
        if self.qa_next_at is None or t < self.qa_next_at or modes.mode != "review" or self.writer is not None:
            return ""
        self.qa_next_at = None
        if not self.qa_queue:
            return ""
        question = self.qa_queue.pop(0)
        self.answer = (self.qa_of - len(self.qa_queue), self.qa_of, question)
        modes.enter("count_in", t)
        note = self.handle(GestureEvent("count_in", t, source="qa"), modes, view, overlay)
        if modes.mode == "count_in":
            modes.count_in_end = t + QA.read_s  # time to read the question
        self.log(t, "qa_question", k=self.answer[0] if self.answer else None, of=self.qa_of, question=question)
        return note

    def _answer_facts(self, take, words: list[dict], alignment: dict) -> dict:
        heard = [w for w in words if normalize(w["text"])]
        return {"question": take.answer["question"], "duration_s": take.duration_s,
                "first_word_s": round(heard[0]["start"] - take.t_start, 2) if heard else None,
                "fillers": len(alignment.get("fillers", []))}

    def latest_round(self) -> list[int]:
        """The newest Q&A round's answer takes, when the round is newer than every take of the notes."""
        last = max(self.answers, default=None)
        if last is None or last < (self.board.latest() or 0):
            return []
        since = max((t for t in self.board.takes if t < last), default=0)
        return [m for m in sorted(self.answers) if m > since]

    def _coach_subject(self) -> int | None:
        """What the AI coach would be asked about: the newest take, or the newest round's last answer."""
        return (self.latest_round() or [self.board.latest()])[-1]

    def coach_ready(self) -> bool:
        """The AI coach can be asked about the newest take (or round of answers): there is an
        LLM, it is measured (a round: nothing still analysing), and it hasn't been asked yet."""
        n = self._coach_subject()
        if self.assistant is None or n is None or n in self.ai_advice or self.assistant.asking("coach", (n,)):
            return False
        return not self.analysis.pending if self.latest_round() else n in self.board.metrics

    def _ask_round(self, t: float, numbers: list[int]) -> str:
        """The AI coach on a Q&A round: each question and what was said in answer."""
        pairs = []
        for k, m in enumerate(numbers, 1):
            take = self.session.take(m)
            try:
                words = json.loads((self.session.dir / take.transcript).read_text())["words"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return f"Couldn't read answer {k}'s transcript: {short(exc)}"
            said = " ".join(w["text"].strip() for w in words) or "(nothing heard)"
            pairs.append(f"Q{k}: {take.answer['question']}\nA{k}: {said}")
        facts = "\n".join(f"Answer {k}: {coach.mmss(self.answers[m]['duration_s'])} long, first word after "
                          + ("-" if self.answers[m]["first_word_s"] is None else f"{self.answers[m]['first_word_s']:.1f} s")
                          + f", {self.answers[m]['fillers']} fillers" for k, m in enumerate(numbers, 1))
        if self._advice is not None:
            facts += f"\nThe app's own note: {self._advice.work}"
        self.assistant.ask("coach", (numbers[-1],), self._llm_revision(),
                           answers_coach_request(self.notes_text(), "\n\n".join(pairs), facts), parse_coach)
        self.log(t, "llm", ask="coach", round=numbers)
        return "Asking the AI coach about your answers…"

    def ask_coach(self, t: float) -> str:
        """An open palm held in Review: the newest take (or round of answers) to the AI coach."""
        if not self.coach_ready():
            return ""
        if numbers := self.latest_round():
            return self._ask_round(t, numbers)
        n = self.board.latest()
        take = self.session.take(n)
        try:
            words = json.loads((self.session.dir / take.transcript).read_text())["words"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return f"Couldn't read take {n}'s transcript: {short(exc)}"
        transcript = " ".join(w["text"].strip() for w in words)
        notes = "\n\n".join((f"# {sec.title}\n" if sec.title else "") + " ".join(s.text for s in sec.sentences)
                             for sec in self.notes.sections)
        if n in self.board.drills:
            notes += f"\n\n(This take practised one sentence: {self.notes.sentences[self.board.drills[n]].text})"
        rows = self.board.take_table(1)
        facts = "\n".join(line.strip() for line in rows[1:]) if rows else ""
        if self._advice is not None:
            facts += f"\nThe app's own note: {self._advice.work}"
        self.assistant.ask("coach", (n,), self._llm_revision(), coach_request(notes, transcript, facts), parse_coach)
        self.log(t, "llm", ask="coach", take=n)
        return "Asking the AI coach…"

    def on_transcribed(self, result: dict) -> str:
        """A take's transcript and alignment arrived. Returns a label note."""
        n = result["take"]
        if not result["ok"]:
            print(f"take {n}: analysis failed: {result.get('error')}. Press r to retry, or run: "
                  f"python -m palmcards.speech {self.session.dir} --take {n}", file=sys.stderr)
            return ""
        self.session.set_result(n, result["transcript"], result["alignment"], result.get("metrics"))
        take = self.session.take(n)
        if take.answer:  # an answer to a practice question: measured for the round, not a take of the notes
            try:
                words = json.loads((self.session.dir / take.transcript).read_text())["words"]
            except (OSError, ValueError, KeyError, TypeError):
                words = []
            self.answers[n] = self._answer_facts(take, words, result["alignment"])
            self.log(time.perf_counter() - self.t0, "transcribed", take=n, seconds=result["seconds"])
            self.last_saved = f"Answer {take.answer['index']}: {coach.mmss(take.duration_s)}"
            return ""
        self.board.add(n, result["alignment"], result.get("metrics"), take.drill, duration_s=take.duration_s,
                       faded=take.fade)
        self.log(time.perf_counter() - self.t0, "transcribed", take=n, seconds=result["seconds"])
        print(f"take {n} (transcribed and measured in {result['seconds']:.1f} s)\n{result['report']}")
        if take.drill is not None:
            row = self.board.rows[n].get(take.drill, {})
            pace = f"{row['wpm']:.0f} words a minute" if row.get("wpm") is not None else "the sentence wasn't heard"
            self.last_saved = f"Take {n} (practice): {pace}"
            return ""
        # The take table beside the notes has the measures; the label only says the take is in.
        c = counts(result["alignment"])
        self.last_saved = f"Take {n}: you said {c['spoken']} of {len(result['alignment']['sentences'])} sentences"
        return ""

    def handle(self, ev: GestureEvent, modes: ModeMachine, view: ViewState, overlay: TextOverlay) -> str:
        """Mode events. Returns a label note to show, or ""."""
        if ev.kind in ("count_in", "drill"):
            self.stop_playback("take", ev.t)
            if ev.source != "qa":  # a take or drill started by hand: the Q&A round, if any, is over
                self.answer, self.qa_queue, self.qa_next_at = None, [], None
            self.drill_end = ev.drill_end if ev.kind == "drill" else None
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
                return "The microphone isn't available"
            self.section = 0 if self.drill is None else self.notes.sentences[self.drill].section
            view.current = self.drill if self.drill is not None else self.first_of(self.section)
            view.panel_scroll, view.preview_next = 0.0, False
            view.faded = None if self.answer else self._fade_start()
            if (self.drill is None or self.drill_end is not None) and self.answer is None and self._ensure_follow():
                self.follow.prepare()  # the model loads during the count-in
            check = self.gaze_check and self.drill is None and self.answer is None
            if self.vision is not None and self.answer is None \
                    and (self.calibrate_next or self.session.calibration is None or check):
                # The count-in starts with looking into the camera; the orange sentence is the 3-2-1.
                self.vision.begin_calibration(ev.t)
                modes.count_in_end = ev.t + BODY.calib_camera_s + REHEARSE.count_in_s
                self.log(ev.t, "calibration_start")
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "coach":
            return self.ask_coach(ev.t)
        if ev.kind == "to_prepare":
            view.hover = view.focus = None
            view.mode, view.level, view.ops = "idle", None, OpsView()
            return ""
        if ev.kind == "count_in_cancel":
            self.answer, self.qa_queue, self.qa_next_at = None, [], None  # the OK sign ends the round too
            if self.vision is not None:
                self.vision.cancel()
            self.recorder.close()
            self.drill = None
            return "Recording cancelled"
        if ev.kind == "take_start":
            self.alert = ""
            note = self._finish_calibration(ev.t) \
                if self.vision is not None and self.vision.state == "calibrating" else ""
            try:
                number = self.session.begin_take()
                self.writer = TakeWriter(self.session.dir, number, self.recorder.rate, {
                    "started": datetime.now().isoformat(timespec="milliseconds"), "requested_t": round(ev.t, 3),
                    "drill": self.drill, "drill_end": self.drill_end, "revision": self.session.current_revision})
            except (OSError, SessionError) as exc:
                print(f"cannot record: {exc}", file=sys.stderr)
                self.log(ev.t, "record_error", error=str(exc))
                self.alert = f"Can't record: {short(exc)}  /  Hold an OK sign to stop"
                return ""
            self.writer.mark_section(ev.t, self.section, "start")
            self.section_t = ev.t
            if self.answer is not None:
                k, of, question = self.answer
                self.writer.meta["answer"] = {"question": question, "index": k, "of": of}
            self.slide, self.slide_heard, self.slide_log = 0, 0.0, None
            if self.slide_full_take:
                self.slide_log = self.writer.meta["slides"] = [[round(ev.t, 3), 0]]
            if view.faded is not None:
                self.fade_prompted, self._fade_quiet = [], (0, ev.t)
                self.writer.meta["fade"] = {"hidden": [list(k) for k in sorted(self.fade_hidden)],
                                            "prompted": self.fade_prompted}
            self.recorder.start(self.writer)
            self.stopper = AutoStop()
            self._start_video(number)
            if (self.drill is None or self.drill_end is not None) and self.answer is None and self.follow is not None:
                self.follow.start_take(self.section)
                if self.drill is not None:
                    self.follow.jump(self.section, self.drill)
                self.recorder.tap = self.follow.tap
            if self.vision is not None:
                self.vision.begin_take(ev.t)
            self.log(ev.t, "take_start", take=number)
            if self.gaze_check and self.drill is None:
                if self.vision is None:
                    print(f"gaze check: face tracking is off ({self.vision_off}); recording a plain take",
                          file=sys.stderr)
                    return f"The gaze check needs face tracking: {short(self.vision_off)}"
                seed = secrets.randbelow(1 << 30)
                self.check = {"seed": seed, "t0": round(ev.t, 3), "prompts": gaze.prompt_schedule(seed)}
                self.writer.meta["gaze_check"] = self.check
                self.log(ev.t, "gaze_check", take=number, seed=seed)
            return note
        if ev.kind == "next_section":
            if self.section + 1 >= len(self.notes.sections):
                return "This is the last section"
            source = "key"  # sections move with the voice, or by key
            self.section += 1
            view.current, view.panel_scroll, view.preview_next = self.first_of(self.section), 0.0, False
            self.section_t = ev.t
            if self.writer is not None:
                self.writer.mark_section(ev.t, self.section, source)
            if self.follow is not None:
                self.follow.flick(ev.t)  # the voice carries on from the new section
            self.log(ev.t, "section", section=self.section, source=source)
            return ""
        if ev.kind == "previous_section":  # a key: undo a wrong move
            if self.section == 0:
                return "This is the first section"
            self.section -= 1
            view.current, view.panel_scroll, view.preview_next = self.first_of(self.section), 0.0, False
            self.section_t = ev.t
            if self.writer is not None:
                self.writer.mark_section(ev.t, self.section, "key")
            if self.follow is not None:
                self.follow.jump(self.section, view.current)
            self.log(ev.t, "section", section=self.section, source="key")
            return ""
        if ev.kind == "take_stop":
            writer = self._stop_recording()
            note = ""
            if writer is not None:
                self.log(ev.t, "take_stop", take=writer.number, duration_s=round(writer.seconds, 3), wav=writer.wav)
                kind = "Answer" if self.answer else "Practice" if self.drill is not None else "Take"
                note = f"{kind} {writer.number} saved · " + "{}:{:02d}".format(*divmod(round(writer.seconds), 60))
                if self.answer is not None:  # the round goes on, or ends
                    self.answer = None
                    if self.qa_queue:
                        self.qa_next_at, note = ev.t + QA.gap_s, note + " · Next question"
                    else:
                        note += " · That was the last question"
                elif self.drill is None and self.qa_on and self.questions():  # a full take: its Q&A round
                    self.qa_queue = self._pick_questions()
                    self.qa_of, self.qa_next_at = len(self.qa_queue), ev.t + QA.gap_s
                    note += " · Questions next"
            if self.drill is not None:
                view.current = self.drill
            else:
                view.current = next(i for i, s in enumerate(overlay.sentences) if s.section == self.section)
            self.drill = None
            view.scroll = overlay.scroll_to(view.current)
            return note
        return ""

    def _fade_start(self) -> tuple[frozenset, frozenset] | None:
        """Fading cards at a count-in: the words this take dims and blanks (None: off).
        A full take blanks words only with the voice follow on, to bring them back on a stall;
        a drill (one sentence, no follow) blanks them anyway."""
        if not self.fade_on:
            return None
        dimmed, hidden = fade.faded(self.board.fade_levels())
        if self.drill is not None:
            dimmed, hidden = (frozenset(k for k in ks if k[0] == self.drill) for ks in (dimmed, hidden))
        elif not self.follow_enabled or (self.follow is not None and self.follow.state == "failed"):
            dimmed, hidden = dimmed | hidden, frozenset()
        self.fade_dimmed, self.fade_hidden = dimmed, hidden
        return dimmed, hidden

    def _fade_prompt(self, view: ViewState) -> None:
        """In a take with words blanked: when the voice has been quiet FADE.prompt_after_s,
        the next FADE.prompt_words blanked words come back; if the follow fails, all of them."""
        if view.faded is None or not self.fade_hidden or self.writer is None or self.drill is not None:
            return
        now = time.perf_counter() - self.t0
        if self.follow.state == "failed":
            words = sorted(self.fade_hidden)
        else:
            heard, since = self._fade_quiet
            if self.follow.heard != heard:
                self._fade_quiet = (self.follow.heard, now)
                return
            if self.follow.state != "following" or now - since < FADE.prompt_after_s:
                return
            self._fade_quiet = (heard, now)  # the next prompt after another quiet stretch
            at = self.follow.follower.at if self.follow.follower is not None else None
            after = max(at or (view.current, -1), (view.current, -1))
            words = fade.prompt(self.fade_hidden, sorted(self.fade_hidden), after)
        if not words:
            return
        self.fade_hidden -= set(words)
        self.fade_prompted += [list(k) for k in words]
        view.faded = (self.fade_dimmed, self.fade_hidden)
        self.log(now, "prompt", words=[list(k) for k in words])

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

    def quick_count_in(self) -> bool:
        """The short count-in (REHEARSE.count_in_again_s): the session has a take and
        nothing needs the full one (the eye calibration, a gaze check)."""
        return bool(self.session.takes) and not self.gaze_check and not self.calibrate_next \
            and (self.vision is None or self.session.calibration is not None)

    def auto_stop(self, t: float) -> bool:
        """The take ends itself: quiet after the notes' last sentence (a drill: after
        speech), see AutoStop. Never during a gaze check, which ends on its own."""
        if self.writer is None or self.recorder is None or self.check is not None:
            return False
        if self.drill is not None and self.drill_end is not None and self.follow and self.follow.state == "following":
            at = self.follow.follower.at if self.follow.follower else None
            last_word = len(self.notes.sentences[self.drill_end].words) - 1
            at_end = at is not None and at >= (self.drill_end, max(0, last_word - FOLLOW.handoff_words))
        elif self.drill is not None or self.answer is not None:  # one sentence, or an answer: once it heard speech
            at_end = self.stopper.heard_s >= REHEARSE.end_heard_s
        elif self.slides_only:  # the clicker leads: on the last slide, once it heard speech there
            at_end = self.slide_log is not None and self.slide == self.deck.pages - 1 \
                and self.stopper.heard_s - self.slide_heard >= REHEARSE.end_heard_s
        else:
            f = self.follow.follower if self.follow is not None and self.follow.state == "following" else None
            last = len(self.notes.sentences) - 1
            # On the last sentence and near its end (reached in other words: wherever it is in it).
            at_end = f is not None and f.sentence == last and (
                f.at is None or f.at[0] != last
                or f.at[1] >= len(self.notes.sentences[last].words) - 1 - FOLLOW.handoff_words)
        if not self.stopper.update(t, self.recorder.level, at_end):
            return False
        self.writer.meta["stopped"] = "auto"
        self.log(t, "auto_stop", drill=self.drill)
        return True

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
        return "Eye calibration didn't work. It runs again at the next take."

    def recalibrate(self) -> str:
        """e: calibrate the eyes again at the next count-in."""
        if self.vision is None:
            return f"Face tracking is off: {short(self.vision_off)}"
        self.calibrate_next = True
        return "Your eyes are calibrated again at the next take"

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

    def warm(self) -> None:
        """Load the speech models while the user prepares, not in the first
        take: the analysis worker's, and the voice follow's. Making the
        recorder only asks for the microphone's rate; it isn't opened."""
        self.analysis.warm()
        if not self.follow_enabled:
            return
        try:
            if self.recorder is None:
                self.recorder = self.devices.recorder(clock=lambda: time.perf_counter() - self.t0)
        except Exception as exc:  # no input device: the count-in says so, as before
            print(f"microphone not found yet: {exc}", file=sys.stderr)
            return
        if self._ensure_follow():
            self.follow.prepare()

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
        if self.follow is None or self.writer is None or (self.drill is not None and self.drill_end is None):
            return
        for ev in self.follow.poll():
            if self.drill is not None:
                if ev.kind == "sentence":
                    view.current = max(self.drill, min(self.drill_end, ev.index))
                continue
            if ev.kind == "section" and ev.index == self.section + 1:
                self.section, self.section_t = ev.index, ev.t
                self.writer.mark_section(ev.t, ev.index, "voice")
                self.log(ev.t, "section", section=ev.index, source="voice")
            elif ev.kind == "sentence":
                view.current = ev.index
        if self.follow.state == "failed" and not self._follow_reported:
            print(f"voice follow off: {self.follow.error}", file=sys.stderr)
            self._follow_reported = True
        self._fade_prompt(view)
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
        self.slide_log = None
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

    @property
    def slide_full_take(self) -> bool:
        """A full take with a deck: slides show (not a drill, an answer or a gaze check)."""
        return self.deck is not None and self.drill is None and self.answer is None and not self.gaze_check

    @property
    def slide_shown(self) -> int | None:
        """The slide to draw now (count-in and take), else None."""
        if self.writer is not None:
            return self.slide if self.slide_log is not None else None
        return 0 if self.slide_full_take else None

    def open_slides(self, width: int) -> None:
        """The deck at `width` px: a PDF speech's own (the session's copy), else the PDF attached,
        as the file is now (re-exported after a change, it shows the new slides)."""
        self.slide_width = width
        try:
            if self.slides_only:
                s = self.session
                data = s._source_bytes or (s.dir / s.source["file"]).read_bytes()
                self.deck = Deck(data, width, s.notes.name)
            elif self.session.slides:
                self.deck = Deck.open(Path(self.session.slides["path"]), width)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.deck = None
            print(f"slides off: {exc}", file=sys.stderr)

    def slides_info(self) -> tuple[str, int | None] | None:
        """The attached slides for the home card: (name, pages; None if the file can't be read)."""
        if not self.session.slides:
            return None
        return Path(self.session.slides["path"]).name, self.deck.pages if self.deck is not None else None

    def attach_slides(self, path: Path) -> str:
        """The home card: a PDF of slides beside the written notes, kept with the session."""
        try:
            deck = Deck.open(path, self.slide_width)
        except (OSError, ValueError) as exc:
            print(f"cannot open slides {path}: {exc}", file=sys.stderr)
            return f"Couldn't open the slides: {short(exc)}"
        self.deck = deck
        self.session.slides = {"path": str(Path(path).resolve()), "pages": deck.pages}
        try:
            self.session.save()
        except (OSError, SessionError) as exc:
            return f"Couldn't keep the slides: {short(exc)}"
        return f"Slides added: {deck.pages}"

    def move_slide(self, step: int, t: float, view: ViewState, overlay: TextOverlay) -> None:
        """The clicker, in a full take. In a PDF speech the outline's orange row follows the slide
        (paragraph N is slide N, palmcards.notes), scrolled into view."""
        if self.slide_log is None:
            return
        new = min(max(self.slide + step, 0), self.deck.pages - 1)
        if new == self.slide:
            return
        self.slide, self.slide_heard = new, self.stopper.heard_s
        self.slide_log.append([round(t, 3), new])
        self.log(t, "slide", slide=new)
        if self.slides_only:
            view.current = next((s.index for s in self.notes.sentences if s.paragraph == new), view.current)
            view.panel_scroll = overlay.panel_scroll_to(view, view.current)

    def recording_problem(self) -> None:
        """Surface a disk error while a take is still being recorded."""
        if self.writer is not None and self.writer.error and not self.alert:
            print(f"take {self.writer.number}: recording failed: {self.writer.error}; the audio up to here is kept",
                  file=sys.stderr)
            self.alert = f"Recording failed: {short(self.writer.error)}  /  The audio so far is kept"

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
            self.alert = f"The take wasn't saved: {short(writer.error) if writer.error else 'no audio arrived'}"
            return ""
        take = self.session.finish_take(manifest, None if status == "saved" else status)
        m, s = divmod(round(take.duration_s), 60)
        gaps = take.capture["dropped_samples"] / take.sample_rate if take.capture else 0.0
        print(f"saved {self.session.dir / take.wav} ({take.duration_s:.1f} s, {take.status}"
              + (f", {gaps:.2f} s of audio lost" if gaps else "") + ")")
        if take.status != "saved":
            self.alert = f"Take {take.number} was {take.status}: kept, not analysed {IN_LOG}"
            print(f"take {take.number} is {take.status}; analyse it anyway with: python -m palmcards.speech "
                  f"{self.session.dir} --take {take.number} --incomplete", file=sys.stderr)
            return ""
        self._submit(take.number)
        self.last_saved = f"Take {take.number} saved ({m}:{s:02d})"
        if take.silent:
            print("warning: the take is silent. On macOS, allow Microphone access for your terminal "
                  "app in System Settings > Privacy & Security > Microphone.", file=sys.stderr)
            self.alert = f"Take {take.number} is silent. Check microphone access."  # until the next take starts
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
        return f"Analysing {n} take{'s' if n != 1 else ''} again…" if n else "Nothing to retry"

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
            deadline = time.monotonic() + (0.0 if self.interrupted or self.switching else LLM.close_wait_s)
            try:
                while self.assistant.pending and time.monotonic() < deadline:
                    self.poll_llm()
                    time.sleep(0.05)
            except KeyboardInterrupt:
                pass
            self.poll_llm()
            self._log_llm_usage(abandoned=True)
        # Bounded: wait a while for analysis already running, then leave it for later.
        if self.analysis.pending and not self.interrupted and not self.switching:
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
        left = self.analysis.close(timeout=1.0)
        self.poll_analysis()
        if left or self.deferred:
            takes = sorted(set(left) | set(self.deferred))
            print(f"analysis of take {', '.join(map(str, takes))} left for later: python -m palmcards.speech "
                  f"{self.session.dir}")
        self.session.release()


def mirror_crop(shape, aspect):
    """Even-sized centre crop, shared by the overlay, tracking and video encoder."""
    h, w = shape[:2]
    if not aspect:
        return slice(None), slice(None)
    width = min(w, round(h * aspect / 2) * 2)
    height = min(h, round(w / aspect / 2) * 2)
    x, y = (w - width) // 2, (h - height) // 2
    return slice(y, y + height), slice(x, x + width)


def mirror_display_aspect(name):
    if sys.platform == "darwin":
        from AppKit import NSApp
        if (app := NSApp()) is not None:
            for window in app.windows():
                if window.title() == name and (screen := window.screen()) is not None:
                    size = screen.frame().size
                    return size.width / (size.height - screen.safeAreaInsets().top)
    return None


def open_mirror_window(name: str, width: int, height: int) -> None:
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)
    if cv2.getWindowProperty(name, cv2.WND_PROP_FULLSCREEN) != cv2.WINDOW_FULLSCREEN:  # kept in full screen: as it is
        cv2.resizeWindow(name, width, height)
    if sys.platform == "darwin":
        from AppKit import NSApplication, NSColor
        for window in NSApplication.sharedApplication().windows():
            if window.title() == name:
                # Cocoa fits the camera image inside the window. Its uncovered
                # area takes the window background, including in full screen.
                window.setBackgroundColor_(NSColor.blackColor())
                break


@dataclass
class Devices:
    """What the app opens. Tests pass fakes, so failures can be injected
    without a camera, a microphone or a window."""
    camera: Callable = Camera
    tracker: Callable = HandTracker
    recorder: Callable = AudioRecorder
    log: Callable = GestureLog.to_session_dir
    player: Callable = ClipPlayer
    video: Callable = VideoWriter  # (folder, take number, frame size); only with video on
    video_reader: Callable = VideoReader  # (path, t_first, frame size): Review's replay of a take's video
    llm: Callable = get_provider  # None: the optional LLM is off
    vision: Callable = Watcher.open  # face and pose; None or an error: off (they are optional)
    live: Callable = lambda language, clock, hints: get_recognizer().live(language, clock, SPEECH.live_where, hints)
    named_window: Callable = open_mirror_window
    show: Callable = cv2.imshow
    # waitKeyEx: a clicker's arrow and page keys keep their own codes (SLIDES); no key is 255, as before.
    wait_key: Callable = lambda: k if (k := cv2.waitKeyEx(1)) != -1 else 255
    window_open: Callable = lambda name: cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) >= 1
    destroy_windows: Callable = cv2.destroyAllWindows
    pump: Callable = lambda: cv2.waitKey(1)  # lets the window draw a card while something loads
    pick: Callable = pick_notes  # the Open dialog (key l): a Path, or None if cancelled
    pick_slides: Callable = pick_slides  # the Open dialog for a PDF of slides (the home card)
    notch: Callable = NotchPanel.create  # the teleprompter in the notch, or None (no notch: the window's)
    fullscreen: Callable = lambda name, on: cv2.setWindowProperty(
        name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN if on else cv2.WINDOW_NORMAL)


@dataclass
class Kept:
    """What outlives a speech, owned by main(): the window, the camera, hand tracking, face and
    pose tracking, and the app's clock (one clock: every log and take time is seconds since the
    app started, and MediaPipe's timestamps must keep rising). run() opens what is missing (the
    first speech, with its loading cards) and leaves it open, so switching speech reopens only
    the session: no loading card, nothing to wait for."""
    t0: float = field(default_factory=time.perf_counter)
    camera: object = None
    tracker: object = None
    vision: object = None
    vision_off: str = ""
    vision_tried: bool = False

    def close(self) -> None:
        for thing, close, what in ((self.vision, "close", "face and pose tracking"),
                                   (self.tracker, "close", "hand tracking"), (self.camera, "release", "the camera")):
            if thing is not None:
                guarded(getattr(thing, close), what)()
        guarded(cv2.destroyAllWindows, "the window")()


def show_card(devices: Devices, title: str, rows: tuple[str, ...] = ()) -> np.ndarray:
    """A card in the window: what is loading, or what went wrong."""
    frame = np.zeros((CARD_SIZE[1], CARD_SIZE[0], 3), np.uint8)
    render.draw_card(frame, title, rows)
    devices.show(WINDOW, frame)
    devices.pump()
    return frame


def show_error(devices: Devices, title: str, rows: tuple[str, ...] = (), can_pick: bool = False,
               keep: bool = False) -> int | Next:
    """A problem the user can act on, in the window (an app opened without a
    terminal has nowhere else to say it), until q or the window closes.
    With `can_pick`, l opens another notes file: that file is returned."""
    devices.named_window(WINDOW, *CARD_SIZE)
    rows = (*rows, "", "L  Choose another file   ·   Q  Quit" if can_pick else "Q  Quit")
    try:
        while True:
            show_card(devices, title, rows)
            key = devices.wait_key()
            if key == ord("q") or not devices.window_open(WINDOW):
                return 1
            if can_pick and key == ord("l") and (picked := devices.pick()) is not None:
                return Next(path=picked)
    finally:
        if not keep:  # main's window (Kept) stays for what comes next
            devices.destroy_windows()


def camera_problem(exc: CameraError) -> tuple[str, tuple[str, ...]]:
    if "stopped delivering" in str(exc):
        return "The camera stopped", ("Check that no other app is using it, then open PalmCards again.",)
    return "No camera picture", (
        "If camera access is off: open System Settings > Privacy & Security > Camera. Allow the app you started "
        "PalmCards from (your terminal), then quit it and open it again.",
        "If the Mac has no camera, connect one.")


class Tee:
    """A stream that also writes to the day's log file: what went wrong is kept
    even when nobody is watching the terminal."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text: str) -> int:
        self.stream.write(text)
        try:
            self.log.write(text)
            self.log.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self) -> None:
        self.stream.flush()

    def __getattr__(self, name):  # isatty, fileno, encoding: the real stream's
        return getattr(self.stream, name)


def log_to_file() -> Path | None:
    """stdout and stderr also into logs/palmcards-YYYYMMDD.log in the data directory."""
    try:
        folder = logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"palmcards-{datetime.now():%Y%m%d}.log"
        log = path.open("a", buffering=1)
    except OSError:
        return None
    log.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} {' '.join(sys.argv)}\n")
    sys.stdout, sys.stderr = Tee(sys.stdout, log), Tee(sys.stderr, log)
    return path


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


def launch_choice(entry, devices: Devices) -> Next | None:
    """A row chosen on the launch card: what to open, or None (the Open dialog cancelled: the card stays)."""
    if entry.pick:
        return Next(path=picked) if (picked := devices.pick()) is not None else None
    return entry.next


def launcher(kept: Kept, devices: Devices | None = None, root: Path = SESSIONS_DIR) -> int | Next:
    """Launched with no file named: the home card alone, no speech open (the user, 2026-11-27).
    Each speech practised before (its newest session continued), the sample, Open a file.
    Nothing opens until a row is chosen, by a tap or a key; a cancelled Open dialog leaves the
    card; q quits (Esc does nothing: there is no step back). The camera and hand tracking go into
    `kept` for the speech chosen."""
    devices = devices or Devices()
    devices.named_window(WINDOW, *CARD_SIZE)
    if kept.camera is None:
        show_card(devices, "Opening the camera…")
        kept.camera = devices.camera()
    if kept.tracker is None:
        show_card(devices, "Loading hand tracking…")
        kept.tracker = devices.tracker()
    frame = kept.camera.read()
    crop = mirror_crop(frame.shape, mirror_display_aspect(WINDOW))
    h, w = frame[crop].shape[:2]
    prefs = preferences.load()
    preferences.apply(prefs)
    devices.named_window(WINDOW, w, h)
    if prefs.fullscreen:
        devices.fullscreen(WINDOW, True)
    home = Home(launch_entries(root, SAMPLE), (w, h))
    overlay = TextOverlay([], (w, h))
    while True:
        frame = np.ascontiguousarray(kept.camera.read()[crop])
        kept.tracker.submit(frame, time.perf_counter() - kept.t0)
        if (result := kept.tracker.poll()) is not None and (entry := home.update(*result)) is not None \
                and (chosen := launch_choice(entry, devices)) is not None:
            return chosen
        state = home.grammar.state
        draw_hand_light(frame, state)
        if prefs.show_hand_box and state.primary is not None:
            draw_hand_area(frame, home.grammar.cursor, None)
        status = ("Tap to open it" if home.browsing else "Raise a finger to choose") + HINT_SEP + \
            REVIEW_SEP.join((f"Or press 1 to {len(home.rows)}", "Q: quit"))
        overlay.draw(frame, ViewState(home=tuple((r.title, r.detail) for r in home.rows), home_picked=home.picked,
                                      title="Choose a speech", status=status))
        draw_fingertips(frame, state)
        devices.show(WINDOW, frame)
        key = devices.wait_key()
        if key == ord("q") or not devices.window_open(WINDOW):
            return 0
        if key in (ord("j"), ord(" "), ord("k")):
            home.step(-1 if key == ord("k") else 1)
        elif key == ord("f"):
            prefs.fullscreen = not prefs.fullscreen
            preferences.save(prefs)
            devices.fullscreen(WINDOW, prefs.fullscreen)
        elif (entry := home.rows[home.picked] if key == ENTER else home.choose(key - ord("1"))) is not None \
                and (chosen := launch_choice(entry, devices)) is not None:
            return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description="PalmCards rehearsal mirror")
    ap.add_argument("notes", nargs="?", type=Path,
                    help=".txt, .md or .docx, or slides as .pdf (default: the file opened last, else the sample)")
    ap.add_argument("--lang", default=SPEECH.language, help="language of the takes, for Whisper")
    ap.add_argument("--trace", action="store_true", help="record hand landmarks for offline replay")
    ap.add_argument("--no-follow", action="store_true", help="don't follow the voice during takes")
    ap.add_argument("--video", action="store_true",
                    help="record video of each take (also preferences `video`), replayed in Review")
    ap.add_argument("--fade", action="store_true",
                    help="fading cards: in a take, words said take after take dim, then blank (key w)")
    ap.add_argument("--open", metavar="RUN", help="reopen a saved session in Review")
    ap.add_argument("--new", action="store_true",
                    help="start a new session, even if the last one for this notes file could be continued")
    ap.add_argument("--gaze-check", action="store_true",
                    help="every take is a gaze check (timed prompts), for scripts/evaluate.py --gaze")
    ap.add_argument("--llm", choices=PROVIDERS, default=LLM.provider or "off",
                    help="the optional LLM: off, ollama (on this Mac) or anthropic (the cloud: what you ask "
                         "about leaves the Mac; ANTHROPIC_API_KEY from the environment or .env)")
    args = ap.parse_args()
    if (log := log_to_file()) is not None:
        print(f"log: {log}")
    for line in recover_all():
        print(f"recovered: {line}")
    # One window, camera, hand tracking and clock for the whole run (Kept): switching speech (the
    # home card) closes and reopens only the session, so nothing loads on screen meanwhile.
    kept = Kept()
    try:
        if args.open:
            result = reopen(args, args.open, kept)
        elif args.notes is not None:
            result = open_notes(args.notes, args, new=args.new, kept=kept)
        else:  # no file named: choose one on the home card first; nothing opens by itself
            result = launcher(kept)
        while isinstance(result, Next):  # chosen on a home card, once that speech was closed
            result = reopen(args, result.folder, kept) if result.folder else \
                open_notes(result.path, args, new=result.new, kept=kept)
    finally:
        kept.close()
    return result


def open_notes(path: Path, args, new: bool = False, kept: Kept | None = None) -> int | Next:
    """Open a notes file: its last session continued if the file is unchanged
    since (`new`: never), else a new session. Returns the exit code, or what to open next."""
    devices = Devices(llm=lambda: get_provider(args.llm))
    try:
        source = path.read_bytes()  # parsed once and kept byte for byte in the session
        notes = notes_from_bytes(source, path)
    except (OSError, ValueError) as exc:
        print(f"Could not open notes: {exc}", file=sys.stderr)
        return show_error(devices, "Couldn't open the notes", (str(path), short(exc, 120)), can_pick=True,
                          keep=kept is not None)
    for warning in notes.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    common = dict(lang=args.lang, trace=args.trace, follow=not args.no_follow, gaze_check=args.gaze_check,
                  video=args.video, fade=args.fade, devices=devices, kept=kept)
    if not new and (folder := latest_session_for(SESSIONS_DIR, path, source, notes)) is not None:
        try:
            session = Session.load(folder)
            session.acquire()
        except SessionError as exc:  # busy in another PalmCards, or unreadable: a new session
            print(f"not continuing {folder.name}: {exc}", file=sys.stderr)
        else:
            print(f"continuing {folder.name} (L in the app switches speech or starts over)")
            continued = from_snapshot(session.snapshot(session.current_revision))
            common.update(lang=session.language, session=session, note=continue_note(session))
            return run_reporting(session.notes, continued, b"", **common)
    return run_reporting(path, notes, source, **common)


def continue_note(session: Session) -> str:
    n = len(session.takes)
    when = f"{started(session.dir):%-d %B}"
    return f"Welcome back. Continuing your session of {when} · {n} take{'' if n == 1 else 's'}"


def reopen(args, run_name: str | Path, kept: Kept | None = None) -> int | Next:
    """main.py --open RUN, or a speech chosen on the home card: the session's
    current notes revision, its takes in Review."""
    from palmcards.data import find
    from palmcards.session import SessionBusy

    try:
        folder = Path(run_name) if (Path(run_name) / "session.json").exists() else find(SESSIONS_DIR, str(run_name))
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
    return run_reporting(session.notes, notes, b"", lang=session.language, trace=args.trace,
                         follow=not args.no_follow, session=session, gaze_check=args.gaze_check, video=args.video, fade=args.fade,
                         devices=Devices(llm=lambda: get_provider(args.llm)), kept=kept)


def run_reporting(*args, **kwargs) -> int | Next:
    """run(), with a camera or a missing model file said on a card in the
    window (and in one line on stderr), not a traceback."""
    devices = kwargs.get("devices") or Devices()
    try:
        return run(*args, **kwargs)
    except CameraError as exc:
        print(exc, file=sys.stderr)
        return show_error(devices, *camera_problem(exc), keep=kwargs.get("kept") is not None)
    except FileNotFoundError as exc:
        if "download_models" not in str(exc):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return show_error(devices, "The models aren't downloaded", (
            "In the PalmCards folder, run:", ".venv/bin/python scripts/download_models.py",
            "Then open PalmCards again."), keep=kwargs.get("kept") is not None)


def run(path: Path, notes: Notes, source: bytes, lang: str = SPEECH.language, trace: bool = False,
        devices: Devices | None = None, sessions_root: Path = SESSIONS_DIR, follow: bool = FOLLOW.enabled,
        session: Session | None = None, prefs_file: Path | None = None, gaze_check: bool = False,
        video: bool = False, note: str = "", fade: bool = False, home: bool = False,
        kept: Kept | None = None) -> int | Next:
    """Open everything, run the frame loop, close everything.

    Every resource is registered for cleanup as soon as it exists, so a
    failure while opening the next one, a camera or drawing error mid-loop,
    or Ctrl-C still closes what was opened, in reverse order, each step on
    its own; a take being recorded is kept. Returns 0, or what to open next
    (the home card, key l). `home`: the home card shows first. `kept`: main's window, camera,
    trackers and clock (Kept), used and left open; what it lacks is opened into it."""
    devices = devices or Devices()
    sentences = notes.sentences
    switching = kept is not None and kept.camera is not None and kept.tracker is not None

    def card(title: str) -> None:  # what is loading; a switch loads nothing slow, so says nothing
        if not switching:
            show_card(devices, title)

    with ExitStack() as stack:
        if not switching:
            devices.named_window(WINDOW, *CARD_SIZE)  # at once: a card says what is loading
        if kept is None:
            stack.callback(guarded(devices.destroy_windows, "the window"))
        card("Opening the camera…")
        if kept is not None and kept.camera is not None:
            camera = kept.camera
        else:
            camera = devices.camera()
            if kept is None:
                stack.callback(guarded(camera.release, "the camera"))
            else:
                kept.camera = camera
        card("Loading hand tracking…")
        if kept is not None and kept.tracker is not None:
            tracker = kept.tracker
        else:
            tracker = devices.tracker()
            if kept is None:
                stack.callback(guarded(tracker.close, "hand tracking"))
            else:
                kept.tracker = tracker
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
        takes.fade_on = fade  # likewise
        card("Loading face and pose tracking…")
        if kept is not None and kept.vision_tried:
            takes.vision, takes.vision_off = kept.vision, kept.vision_off
        else:
            try:
                takes.vision = devices.vision()
            except Exception as exc:  # a model missing or failing to load: the takes go on without
                takes.vision_off = f"{type(exc).__name__}: {exc}"
                print(f"face and pose tracking off: {exc}", file=sys.stderr)
            if kept is not None:
                kept.vision, kept.vision_off, kept.vision_tried = takes.vision, takes.vision_off, True
            elif takes.vision is not None:
                stack.callback(guarded(takes.vision.close, "face and pose tracking"))
        stack.callback(guarded(takes.close, "the takes"))
        card("Getting ready…")
        review = takes.restore()
        takes.warm()
        try:
            next_notes = frame_loop(camera, tracker, log, trace_file, takes, sentences, devices,
                                    "review" if review else None, prefs_file, note, home,
                                    t0=kept.t0 if kept is not None else None)
            takes.switching = isinstance(next_notes, Next)  # closing to open another speech: don't wait
        except BaseException:
            takes.interrupted = True
            raise
    return next_notes or 0


class GcWatch:
    """Python's garbage collections that take long enough to show (GC_SLOW_MS), kept
    until the frame loop logs them: a collection can run on any thread, the log is the loop's."""

    def __init__(self):
        self.slow: list[tuple[float, int]] = []  # (ms, generation)
        self._start = 0.0

    def __call__(self, phase: str, info: dict) -> None:
        if phase == "start":
            self._start = time.perf_counter()
        elif (ms := (time.perf_counter() - self._start) * 1000) >= GC_SLOW_MS:
            self.slow.append((ms, info.get("generation", -1)))

    def drain(self) -> list[tuple[float, int]]:
        out, self.slow = self.slow, []
        return out


GC_SLOW_MS = 5.0  # a collection this long is logged ("gc")


def warm_overlay(overlay: TextOverlay, frame: np.ndarray) -> None:
    """Draw the notes once, off screen, as idle, browsing, selected, in a take and in Review,
    so the glyphs and fonts are ready before the first frame (the first draw
    cost ~80 ms, a selection's first ~40)."""
    last = len(overlay.sentences) - 1
    for view in (ViewState(), ViewState(mode="browse", level="word", hover=Hit(0, 0)),
                 ViewState(mode="browse", level="sentence", hover=Hit(0, None)),
                 ViewState(mode="focus", level="word", focus=Hit(0, 0), hover=Hit(0, 0)),
                 ViewState(mode="focus", level="paragraph", focus=Hit(min(1, last), None)),
                 ViewState(app="rehearse", section=0, current=0),
                 ViewState(app="review", mode="browse", level="sentence", hover=Hit(0, None),
                           summary=("        Take 1  Take 2  Change", "Length    0:30    0:31   +0:01")),
                 ViewState(app="review", mode="focus", level="sentence", focus=Hit(0, None),
                           takes=("Take 1", "Take 2"), take_shown=1, detail=("  Take 1: 120 wpm", "▸ Take 2: 130 wpm"))):
        overlay.draw(np.zeros_like(frame), view)
    overlay._scrim_alpha, overlay._scrim_frames = None, 0  # the glass's tint: measured on the camera, not on black


# Keys that act as usual while the home card is open (no key: 255): quit, keys help, full screen, contrast.
# The card takes j, k, Space, Enter, 1-9, and l or Esc to close it; every other key does nothing there.
HOME_PASS = frozenset((255, 9, ord("q"), ord("h"), ord("f"), ord("c")))


def frame_loop(camera, tracker, log: GestureLog, trace, takes: "Takes", sentences, devices: Devices,
               start_mode: str | None = None, prefs_file: Path | None = None, note: str = "",
               show_home: bool = False, t0: float | None = None) -> Next | None:
    """`t0`: the app's clock (Kept), so times keep rising across speeches; else this loop's start."""
    t0 = time.perf_counter() if t0 is None else t0
    frame = camera.read()
    crop = mirror_crop(frame.shape, mirror_display_aspect(WINDOW))
    frame = np.ascontiguousarray(frame[crop])
    h, w = frame.shape[:2]
    prefs = preferences.load(prefs_file)
    preferences.apply(prefs)  # before the overlay and the machines are built: box, holds, contrast
    takes.frame_size = (w, h)
    takes.replay_mirrored = prefs.replay_mirrored
    takes.video_on = takes.video_on or prefs.video
    takes.fade_on = takes.fade_on or prefs.fade
    takes.qa_on = prefs.qa
    friend = Friend()  # the easter egg (palmcards.easter): its picture, if one is there
    if takes.video_on:
        if devices.video is VideoWriter and (why := video_unavailable()):
            takes.video_off = why
            print(f"video off: {why}", file=sys.stderr)
        else:
            print("video on: each take is recorded to take-NN.mp4 in the session folder")
    # Slides (palmcards.slides): a take with them is drawn in the presenter layout, at twice the camera's
    # size, so the slide's small print stays sharp (render.Presenter); made only for a speech with slides.
    presenter = Presenter(sentences, (w, h)) if takes.slides_only or takes.session.slides else None
    if presenter is not None:
        takes.open_slides(presenter.slide_width)  # pages drawn when first shown

    tutorial = Tutorial(active=not prefs.tutorial_done)
    overlay = TextOverlay(sentences, (w, h))
    warm_overlay(overlay, frame)
    # A take with written notes reads them from a teleprompter at the notch, top centre, under the
    # camera, so reading looks like eye contact (render.Teleprompter); its scroll springs on its own.
    teleprompter = None if takes.slides_only else Teleprompter(sentences, (w, h))
    prompter_motion = PanelMotion()
    # On a Mac with a notch, the teleprompter is in the notch itself, above every window (palmcards.notch).
    notch = None if takes.slides_only else devices.notch(sentences)
    notch_motion = PanelMotion()
    modes = ModeMachine((w, h), log)
    if start_mode:
        modes.enter(start_mode, time.perf_counter() - t0)
    grammar = modes.grammar
    grammar.defer_edit_commit = True
    modes.no_drill = takes.slides_only  # a PDF speech: its notes are slide titles, nothing to drill
    view = ViewState(panel_motion=PanelMotion(), focus_motion=FocusMotion(), scroll_bounce=Spring())
    cues = Cues() if prefs.sounds else None
    ring_turn_seen = 0
    snap_panel = False  # the panel's scroll moved by a key: shown at once, not sprung
    show_debug = False
    note_until = None
    fps, work_ms, last = 0.0, 0.0, time.perf_counter()
    prev_start = last
    takes.t0 = t0
    devices.named_window(WINDOW, w, h)
    if prefs.fullscreen:
        devices.fullscreen(WINDOW, True)
    if note and not show_home:  # e.g. a session continued (the home card says so itself)
        view.note, note_until = note, last + CONTINUE_NOTE_S
    next_notes: Next | None = None  # chosen on the home card: opened once this one is closed

    def open_home() -> Home:
        s = takes.session
        return Home(home_entries(s.dir.parent, s.dir, Path(s.notes), len(s.takes), takes.slides_info()),
                    (w, h), log)

    def close_home(t: float) -> None:
        nonlocal home
        home = None
        modes.enter(modes.mode, t)  # a hand up while choosing carries over: it starts no take

    def chosen(entry) -> Next | None:
        """A row chosen on the home card: what to open, else None (the speech open now:
        the card closes; slides added, or the Open dialog cancelled: it stays)."""
        nonlocal note_until, presenter
        if entry.slides:
            if (picked := devices.pick_slides()) is not None:
                if presenter is None:
                    presenter = Presenter(sentences, (w, h))
                    takes.slide_width = presenter.slide_width
                view.note, note_until = takes.attach_slides(picked), time.perf_counter() + NOTE_S
                home.rows = open_home().rows
            return None
        if entry.pick:
            return Next(path=picked) if (picked := devices.pick()) is not None else None
        if entry.next is None:
            close_home(time.perf_counter() - t0)
        return entry.next

    home = open_home() if show_home else None
    practice_click = False
    action_box = None

    def click_action(event, x, y, flags, userdata):
        nonlocal practice_click
        if event == cv2.EVENT_LBUTTONUP and action_box:
            left, top, right, bottom = action_box
            practice_click = left <= x < right and top <= y < bottom

    if devices.show is cv2.imshow:
        cv2.setMouseCallback(WINDOW, click_action)
    queued: list[GestureEvent] = []  # from keys, handled with the next frame's events
    page_t, page_pause_until = last, 0.0
    hint_after, rehearse_open_since = 0.0, None
    hint_note, hint_mode = "", ""  # the gesture hint shown, and the mode it was about
    notes_seen = takes.notes_version
    frame_index = 0
    # Everything loaded so far (models, notes, caches) lives as long as the app: out of the
    # collector's generations, so its collections stay short. Long ones are logged ("gc").
    gc.collect()
    gc.freeze()
    gc_watch = GcWatch()
    gc.callbacks.append(gc_watch)

    while True:
        frame = np.ascontiguousarray(camera.read()[crop])
        start = time.perf_counter()
        for ms, generation in gc_watch.drain():
            log(start - t0, "gc", ms=round(ms, 1), generation=generation)
        if takes.notes_version != notes_seen:  # e.g. undo from the previous frame's key handler
            notes_seen = takes.notes_version
            sentences = takes.notes.sentences
            overlay = TextOverlay(sentences, (w, h))
            teleprompter = teleprompter and Teleprompter(sentences, (w, h))
            if notch is not None:
                notch.rebuild(sentences)
            grammar.reset()
            view.focus, view.edit_preview, view.ops = None, None, OpsView()
            view.mode, view.level = "idle", None
            view.current = min(view.current, len(sentences) - 1)
            view.scroll = overlay.clamp_scroll(view.scroll)
        tracker.submit(frame, start - t0)
        takes.push_video(frame, (getattr(camera, "last_t", 0.0) or start) - t0)  # the clean frame, as captured
        if takes.vision is not None:  # before anything is drawn on the frame; idle outside calibration and takes
            takes.vision.frame(frame, start - t0, frame_index, late=(start - prev_start) * 1000 > BODY.late_ms)
        captured = time.perf_counter()  # stage times, for a frame too slow ("slow_frame")
        frame_index += 1
        # Hand results arrive asynchronously, usually one frame behind.
        if (result := tracker.poll()) is not None:
            if trace:
                hands, t_hand = result
                trace.write(json.dumps({"t": round(t_hand, 3), "hands": [
                    {"label": hd.handedness, "points": hd.points.round(1).tolist()} for hd in hands]}) + "\n")
            if home is not None:  # choosing a speech: the hands drive the card, nothing else
                modes.grammar.track(*result)
                if (entry := home.update(*result)) is not None and (next_notes := chosen(entry)) is not None:
                    break
                result = None
        if result is not None:
            modes.undo_ready = modes.mode == "prepare" and takes.can_undo()
            modes.quick_count_in = takes.quick_count_in()
            modes.retry_ready = modes.mode == "review" and bool(takes.analysis.failed())
            modes.coach_ready = modes.mode == "review" and takes.coach_ready()
            events = modes.update(*result)
            if modes.mode in ("prepare", "review") and friend.update(result[0], start - t0):
                log(start - t0, "friend")
            if takes.vision is not None:
                takes.vision.hands(*result)
        else:
            events = []
        events += modes.tick(start - t0)
        if modes.mode == "rehearse" and (takes.check_over(start - t0)  # a gaze check ends itself
                                         or takes.auto_stop(start - t0)):  # so does a take, quiet at the end
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
        if note := takes.qa_tick(start - t0, modes, view, overlay):  # the Q&A round's next question
            view.note, note_until = note, start + NOTE_S
        takes.recording_problem()
        for ev in events:  # a drill rehearses the sentence focused now, before any handler clears the focus
            if ev.kind == "drill" and ev.sentence is None:
                ev.sentence = view.focus.sentence if view.focus else view.current
        for ev in events:
            if ev.kind == "undo":  # a V sign held in Prepare after an edit
                view.note, until = takes.undo(), start + NOTE_S
            elif ev.kind == "home":  # a V sign held in Prepare with nothing to undo: choose a speech
                takes.stop_playback("home", start - t0)
                view.focus, view.mode, view.level, view.hover = None, "idle", None, None
                home, until = open_home(), None
            elif ev.kind == "retry":  # an open palm held in Review with failed analysis
                view.note, until = takes.retry(), start + NOTE_S
            elif ev.kind in ("focus", "commit", "commit_ignored", "back", "rewind", "palm_hold", "palm_stop"):
                until = apply_event(ev, view, overlay, log, takes, grammar)
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
        view.title = "Gaze check" if takes.gaze_check and modes.mode in ("count_in", "rehearse") \
            and takes.drill is None and takes.answer is None else ""
        view.question = ""
        if takes.answer is not None and modes.mode in ("count_in", "rehearse"):  # the Q&A round
            view.title, view.question = f"Question {takes.answer[0]} of {takes.answer[1]}", takes.answer[2]
        view.calibration = takes.vision.phase(start - t0) \
            if takes.vision is not None and modes.mode == "count_in" else None
        if view.calibration == "notes" and takes.slide_full_take and (takes.slides_only or notch is None):
            view.calibration = "slide"  # a take with slides reads the slide (no notes to read but the notch's)
        view.hold_progress = modes.back_progress if modes.mode == "review" else modes.done.progress
        view.retry_progress, view.undo_ready = modes.retry_progress, modes.mode == "prepare" and takes.can_undo()
        view.coach_ready = modes.mode == "review" and takes.coach_ready() and not takes.analysis.failed()
        view.coach_progress = modes.coach_progress if modes.mode == "review" else 0.0
        view.undo_progress = modes.undo_progress if modes.mode == "prepare" else 0.0
        view.drill_end = takes.drill_end if takes.drill is not None else None
        view.drill = takes.drill if modes.mode in ("count_in", "rehearse") else None
        slide_i = takes.slide_shown if modes.mode in ("count_in", "rehearse") else None
        view.slide = takes.deck.page(slide_i) if slide_i is not None else None  # drawn when first shown (~6 ms)
        view.next_slide = takes.deck.page(slide_i + 1) \
            if slide_i is not None and slide_i + 1 < takes.deck.pages else None  # the column's preview
        view.slide_label = f"{slide_i + 1} / {takes.deck.pages}" if slide_i is not None else ""
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
            view.target_s = takes.target_s if takes.drill is None else None
            view.mic = takes.recorder.level if takes.recorder else 0.0
            view.end_progress = takes.stopper.progress if modes.mode == "rehearse" else 0.0
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
        hint = "" if home is not None else nonactivation_hint(
            modes.mode, grammar.state, start - rehearse_open_since if rehearse_open_since else 0.0, start - t0)
        if hint and start >= hint_after and not view.note:
            view.note, note_until, hint_after = hint, start + 2 * NOTE_S, start + HINT_EVERY_S
            hint_note, hint_mode = hint, modes.mode
        # A gesture hint belongs to the mode it was about: a take stopping (the palm hint)
        # or a focus (a fist seen on the way into it) takes it away at once.
        focused = view.mode == "focus" or any(ev.kind == "focus" for ev in events)
        if hint_note and view.note == hint_note and (modes.mode != hint_mode or hint_note in (FIST_HINT, FOLD_HINT) and focused):
            view.note, note_until, hint_note = "", None, ""
        if takes.notes_version != notes_seen:  # an edit or undo: lay the new notes out
            notes_seen = takes.notes_version
            sentences = takes.notes.sentences
            overlay = TextOverlay(sentences, (w, h))
            teleprompter = teleprompter and Teleprompter(sentences, (w, h))
            if notch is not None:
                notch.rebuild(sentences)
            view.current = min(view.current, len(sentences) - 1)
            view.scroll = overlay.clamp_scroll(view.scroll)
        view.status = takes.status(modes.mode, view)
        view.alert = takes.alert_line(modes.mode)
        # A PDF speech in Prepare and Review: its slides, not the outline (render.Presenter), the hand
        # flipping through them as it browses rows, a tap selecting one, an open palm playing it.
        view.slides = takes.slides_only and takes.deck is not None and presenter is not None and home is None \
            and modes.mode in ("prepare", "review")
        if view.slides:
            grammar.operations = False  # nothing to edit in a slide's title
            slide_i = takes.slide_of_row(view)
            view.slide, view.slide_label = takes.deck.page(slide_i), ""
            view.title, view.status = f"Slide {slide_i + 1} of {takes.deck.pages}", slide_hints(view)
        view.home = () if home is None else tuple((row.title, row.detail) for row in home.rows)
        if home is not None:
            view.home_picked, view.title = home.picked, "Choose a speech"
            view.status = ("Tap to open it" if home.browsing else "Raise a finger to choose") + HINT_SEP + \
                REVIEW_SEP.join((f"Or press 1 to {len(home.rows)}", "L: back"))
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

        handled = time.perf_counter()
        overlay.follow(view, snap=snap_panel)
        # A take with written notes reads them from the teleprompter (the notch's, else the window's).
        prompting = teleprompter is not None and view.app in ("count_in", "rehearse") and not view.question
        if prompting:
            follower = takes.follow.follower if (view.app == "rehearse" and takes.follow is not None
                                                  and takes.follow.state == "following") else None
            at = follower.at if follower is not None else None
            prompt = replace(view, teleprompter=False, panel_motion=notch_motion if notch else prompter_motion,
                             focus_motion=None, scroll_bounce=None,
                             spoken=at if at is not None and at[0] == view.current else None)
            prompt.panel_scroll = (notch or teleprompter).scroll_for(prompt)
            if notch is not None:
                notch.show(prompt, snap=snap_panel)
        elif notch is not None:
            notch.hide()
        if view.app in ("prepare", "review"):  # where the hands drive the app: the hands in use, lit
            draw_hand_light(frame, (home.grammar if home is not None else grammar).state)
        if (replay := takes.replay_frame()) is not None:
            # A take's video while its clip plays, as others see you: nothing on
            # it but how far it has got. Hands are still tracked live, so a fresh
            # open palm stops it; the mirror and the notes come back after.
            frame = replay
            takes.draw_replay(frame, view.play_progress or 0.0)
        elif view.slide is not None and presenter is not None:  # the eye calibration too: no other layout between
            if view.slides:  # Prepare and Review: the hand browses, so the mirror shows it
                if prefs.show_hand_box and p is not None:
                    draw_hand_area(frame, grammar.cursor, None)
                draw_fingertips(frame, grammar.state)
            frame = presenter.compose(frame, view)
        else:
            view.teleprompter = prompting
            overlay.draw(frame, view)
            if prompting and notch is None:  # no notch: the take's notes at the window's top centre
                teleprompter.follow(prompt, snap=snap_panel)
                teleprompter.draw(frame, prompt)
            if prefs.show_hand_box and not show_debug and view.app in ("prepare", "review") and p is not None:
                draw_hand_area(frame, (home.grammar if home is not None else grammar).cursor, overlay.summary_box)
            if show_debug:
                if view.app in ("prepare", "review"):
                    draw_hand_box(frame, grammar.cursor)
                for track in (grammar.state.primary, grammar.state.secondary):
                    if track is not None:
                        draw_landmarks(frame, track.hand)
            draw_fingertips(frame, (home.grammar if home is not None else grammar).state)

        friend.draw(frame, start - t0)
        snap_panel = False
        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
        work_ms = 0.9 * work_ms + 0.1 * (now - start) * 1000
        last = now
        # cam: rate the camera delivers; shown: rate we display;
        # hands: tracker latency; work: our per-frame processing.
        stats = (f"cam {camera.fps:4.1f}  shown {fps:4.1f} fps  "
                 f"hands {tracker.latency_ms:4.1f} ms  work {work_ms:4.1f} ms")
        draw_stats(frame, stats)

        drawn = time.perf_counter()
        action_box = None
        if modes.mode == "review" and view.practice and view.coach and not home and replay is None:
            owner = presenter.overlay if view.slide is not None and presenter is not None else overlay
            action_box = owner.practice_box
        practice_click = False
        devices.show(WINDOW, frame)
        key = devices.wait_key()
        shown = time.perf_counter()
        if (shown - start) * 1000 > BODY.late_ms:  # a frame too slow to keep 30 fps: what took the time
            log(start - t0, "slow_frame", ms=round((shown - start) * 1000, 1),
                capture=round((captured - start) * 1000, 1), logic=round((handled - captured) * 1000, 1),
                draw=round((drawn - handled) * 1000, 1), show=round((shown - drawn) * 1000, 1), mode=modes.mode)
        if home is not None and key not in HOME_PASS:  # the home card's keys; the others do nothing meanwhile
            if key in (ord("j"), ord(" "), ord("k")):
                home.step(-1 if key == ord("k") else 1)
            elif key in (ord("l"), 27):
                close_home(time.perf_counter() - t0)
            elif (entry := home.rows[home.picked] if key == ENTER else home.choose(key - ord("1"))) is not None \
                    and (next_notes := chosen(entry)) is not None:
                break
            continue
        if key == ord("q"):  # quit; Esc goes back a step (below), never quits
            break
        now_t = time.perf_counter() - t0
        if action_box and (practice_click or key == ENTER):
            suggestion = view.practice
            if suggestion.first is None:
                queued += modes.command("start", now_t)
            else:
                queued += modes.command("practice", now_t, suggestion.first, suggestion.last)
            continue
        if key in SLIDES.next_keys or key in SLIDES.prev_keys:  # a clicker: the slide in a full take
            step = 1 if key in SLIDES.next_keys else -1
            if view.app == "rehearse":
                takes.move_slide(step, now_t, view, overlay)
            elif view.slides:  # Prepare and Review: flip through them
                view.current = takes.row_of_slide(takes.slide_of_row(view, current=True) + step)
        elif key == 27:  # Esc: back one step. A focus backs out; Review goes to Prepare; Prepare to the
            # home card. Nothing in a take or its count-in (the OK sign or x stops those).
            if view.app in ("prepare", "review") and grammar.state.mode == "focus":
                queued += grammar.back(now_t)
            elif view.app == "review":
                queued += modes.command("prepare", now_t)
            elif view.app == "prepare":
                takes.stop_playback("key", now_t)
                view.focus, view.mode, view.level, view.hover = None, "idle", None, None
                home = open_home()
        elif key == ord("x") and takes.stop_playback("key", now_t):  # x stops any playback first
            view.note, note_until = "Stopped", time.perf_counter() + NOTE_S
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
            view.keys_page = 0
        elif key == 9 and view.keys_help:
            view.keys_page += 1
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
            view.note = "Replays are mirrored" if prefs.replay_mirrored else "Replays show you as others see you"
            note_until = time.perf_counter() + NOTE_S
        elif key in (ord("["), ord("]")) and view.app in ("prepare", "review"):  # the target length
            view.note, note_until = takes.set_target(15 if key == ord("]") else -15), time.perf_counter() + NOTE_S
        elif key == ord("y") and view.app in ("prepare", "review"):  # the Q&A round on or off
            prefs.qa = not prefs.qa
            preferences.save(prefs, prefs_file)
            view.note, note_until = takes.toggle_qa(prefs.qa, time.perf_counter() - t0), time.perf_counter() + NOTE_S
        elif key == ord("i") and view.app == "review":  # the coach card, or the take table
            views = ("coach", "table", "days", "detail")
            takes.panel_view = views[(views.index(takes.panel_view) + 1) % len(views)]
            view.note = {"coach": "Suggested practice", "detail": "Detailed feedback", "table": "Take table", "days": "Progress over days"}[takes.panel_view]
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("w") and view.app in ("prepare", "review"):  # fading cards, from the next take
            prefs.fade = takes.fade_on = not prefs.fade
            preferences.save(prefs, prefs_file)
            view.note = "Fading cards on, from the next take" if prefs.fade else "Fading cards off"
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("c"):
            prefs.high_contrast = not prefs.high_contrast
            preferences.save(prefs, prefs_file)
            render.set_contrast(prefs.high_contrast)
            overlay = TextOverlay(sentences, (w, h))  # its cached text was drawn in the old colours
            teleprompter = teleprompter and Teleprompter(sentences, (w, h))
            if notch is not None:
                notch.rebuild(sentences)
        elif key == ord("u") and view.app == "prepare":
            view.note, note_until = takes.undo(), time.perf_counter() + NOTE_S
        elif key == ord("a") and view.app == "review":  # play, or stop what plays
            view.note = "Stopped" if takes.stop_playback("key", now_t) else play_focus(view, overlay, takes)
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("o") and view.app == "review":  # write the notes out, as palmcards.export does
            view.note, note_until = takes.export_notes(now_t), time.perf_counter() + NOTE_S
        elif key == ord("e"):
            view.note, note_until = takes.recalibrate(), time.perf_counter() + NOTE_S
        elif key == ord("f"):
            prefs.fullscreen = not prefs.fullscreen
            preferences.save(prefs, prefs_file)
            devices.fullscreen(WINDOW, prefs.fullscreen)
        elif key == ord("l") and view.app in ("prepare", "review"):  # the home card: switch speech
            takes.stop_playback("key", now_t)
            view.focus, view.mode, view.level, view.hover = None, "idle", None, None
            home = open_home()
        elif key == ord("d"):
            show_debug = not show_debug
        elif key == ord("r"):
            if takes.previews.operation is not None and takes.previews.operation.error:
                takes.previews.retry(time.perf_counter() - t0)
                view.note = "Trying the preview again…" if takes.previews.operation.loading \
                    else takes.previews.operation.error
            else:
                view.note = takes.retry()
            note_until = time.perf_counter() + NOTE_S
        elif key == ord("s"):
            SCREENS_DIR.mkdir(parents=True, exist_ok=True)
            shot = SCREENS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.png"
            cv2.imwrite(str(shot), frame)
            log(time.perf_counter() - t0, "screenshot", path=shot.name)
            print(f"saved {shot}")
        elif key != 255 and view.app in ("count_in", "rehearse"):  # e.g. a clicker button SLIDES doesn't know
            log(now_t, "key", code=key, acted=False, mode=view.app)
        if not devices.window_open(WINDOW):
            break

    gc.callbacks.remove(gc_watch)
    if notch is not None:
        notch.close()
    if modes.mode == "rehearse":  # quit mid-take: keep what was recorded, as a normal take
        takes.handle(GestureEvent("take_stop", time.perf_counter() - t0), modes, view, overlay)
    return next_notes


if __name__ == "__main__":
    sys.exit(main())
