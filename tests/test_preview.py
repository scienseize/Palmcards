"""Controlled responses and an explicit clock: no sleep-based preview tests."""
import json
from dataclasses import replace
from datetime import datetime
from queue import Queue
from threading import Event

import numpy as np
import pytest

import main
from palmcards.config import PREVIEW
from palmcards.gestures import GestureEvent, Grammar
from palmcards.llm import Answer, Assistant, FakeProvider, parse_rewrite, rewrite_request
from palmcards.notes import parse_text
from palmcards.preview import Previews, target_for
from palmcards.render import Hit, OpsView, TextOverlay, ViewState
from tests.test_llm import takes_with

ORIGINAL = "Thank you for being here tonight."
WARM = "Thanks so much for joining us tonight."
COLD = "Thank you for your attendance this evening."


class ControlledAssistant:
    """The asynchronous provider boundary: the test decides exactly when calls finish."""
    cloud = False

    def __init__(self):
        self.provider = FakeProvider("unused")
        self.pending, self.calls, self.answers = {}, {}, []
        self.next = 0

    def ask(self, kind, key, revision, request, parse):
        self.next += 1
        self.pending[self.next] = (kind, key)
        self.calls[self.next] = (kind, key, revision, request, parse)
        return self.next

    def complete(self, ticket, text=None, error=None):
        kind, key, revision, _, parse = self.calls[ticket]
        value = parse(json.dumps({"text": text})) if error is None else None
        self.answers.append(Answer(ticket, kind, key, revision, value, error, error))

    def poll(self):
        answers, self.answers = self.answers, []
        for a in answers:
            self.pending.pop(a.ticket, None)
        return answers

    def spent(self):
        return []


def sync(p, value, now, kind="tone", revision="r1", unit=(0,), original=ORIGINAL):
    p.sync(kind, revision, unit, original, value, now)


def receive(p, assistant, revision="r1"):
    for a in assistant.poll():
        assert p.accept(a, revision)


@pytest.mark.parametrize("targets,margin,low,high", [
    ((-1., 0., 1.), .1, -.61, .61), ((.7, 1., 1.3), .035, .81, 1.19),
])
def test_target_mapping_has_hysteresis(targets, margin, low, high):
    neutral = targets[1]
    assert target_for(low, neutral, targets, margin) == targets[0]
    assert target_for(high, neutral, targets, margin) == targets[2]
    boundary = (targets[1] + targets[2]) / 2
    assert target_for(boundary + margin / 2, neutral, targets, margin) == neutral
    assert target_for(boundary - margin / 2, targets[2], targets, margin) == targets[2]
    assert target_for(neutral, targets[2], targets, margin) == neutral
    assert target_for(low, targets[2], targets, margin) == targets[0]


def test_debounce_and_cached_targets_and_exact_original():
    a = ControlledAssistant()
    p = Previews(a)
    sync(p, 0, 0)
    assert not a.calls
    sync(p, 1, .1)
    assert p.view().loading and p.view().text == ORIGINAL and not a.calls
    sync(p, 1, .349)
    assert not a.calls
    sync(p, 1, .351)
    assert len(a.calls) == 1
    for t in (.4, .5, 3):
        sync(p, 1, t)
    assert len(a.calls) == 1
    a.complete(1, WARM)
    receive(p, a)
    sync(p, 0, 4)
    assert p.view().text == ORIGINAL and p.view().original and not p.view().loading
    sync(p, 1, 4.001)
    assert p.view().text == WARM and not p.view().loading and len(a.calls) == 1


def test_out_of_order_and_superseded_targets_cannot_replace_current_preview():
    a, p = ControlledAssistant(), None
    p = Previews(a)
    sync(p, 1, 0)
    sync(p, 1, .3)
    old = p.operation.request_id
    sync(p, -1, .4)
    sync(p, -1, .7)
    new = p.operation.request_id
    a.complete(new, COLD)
    receive(p, a)
    a.complete(old, WARM)
    receive(p, a)
    assert p.view().text == COLD
    assert 1 not in p.operation.cached_candidates
    # Returning to an earlier target while its old generation is still pending is also stale.
    sync(p, 1, 1)
    sync(p, 1, 1.3)
    old = p.operation.request_id
    sync(p, 0, 1.4)
    sync(p, 1, 1.5)
    sync(p, 1, 1.8)
    a.complete(old, WARM)
    receive(p, a)
    assert p.view().loading and p.view().text == ORIGINAL


@pytest.mark.parametrize("change", ["cancel", "focus", "revision", "kind"])
def test_cancel_focus_revision_and_operation_changes_discard_inflight_answers(change):
    a = ControlledAssistant()
    p = Previews(a)
    sync(p, 1, 0)
    sync(p, 1, .3)
    old_id = p.operation.operation_id
    if change == "cancel":
        p.cancel()
    else:
        sync(p, 0 if change != "kind" else 1, .4, unit=(1,) if change == "focus" else (0,),
             revision="r2" if change == "revision" else "r1", kind="length" if change == "kind" else "tone")
    a.complete(1, WARM)
    receive(p, a, "r2" if change == "revision" else "r1")
    assert p.view() is None if change == "cancel" else p.view().text == ORIGINAL
    if p.operation:
        assert p.operation.operation_id != old_id


def test_inflight_limit_includes_obsolete_calls():
    a = ControlledAssistant()
    p = Previews(a, replace(PREVIEW, max_inflight=1))
    sync(p, 1, 0)
    sync(p, 1, .3)
    sync(p, -1, .4)
    sync(p, -1, .8)
    assert len(a.calls) == 1
    a.complete(1, WARM)
    receive(p, a)
    sync(p, -1, .9)
    assert len(a.calls) == 2


def test_failure_keeps_readable_candidate_and_explicit_retry():
    a = ControlledAssistant()
    p = Previews(a)
    sync(p, 1, 0)
    sync(p, 1, .3)
    a.complete(1, WARM)
    receive(p, a)
    sync(p, -1, .4)
    sync(p, -1, .7)
    assert ORIGINAL in a.calls[2][3][1] and WARM not in a.calls[2][3][1]
    a.complete(2, error="NO CONNECTION")
    receive(p, a)
    assert p.view().text == WARM and "RETRY" in p.view().error
    assert not p.can_commit(p.view(), -1)
    sync(p, -1, 10)
    assert len(a.calls) == 2
    p.retry(10)
    sync(p, -1, 10.3)
    a.complete(3, COLD)
    receive(p, a)
    assert p.view().text == COLD and not p.view().error
    assert p.can_commit(p.view(), -1)


def test_original_needs_no_provider_and_unavailable_is_explicit():
    p = Previews(None)
    sync(p, 0, 0)
    assert p.can_commit(p.view(), 0)
    sync(p, 1, 1)
    assert "PROVIDER UNAVAILABLE" in p.view().error and p.view().text == ORIGINAL
    assert not p.can_commit(p.view(), 1)


def test_request_preserves_facts_and_validation_rejects_numbers_or_marks():
    original = "Mara may deliver 12 boxes by June, subject to approval."
    for kind, value in (("tone", 1), ("tone", -1), ("length", .7), ("length", 1.3)):
        system, user = rewrite_request(kind, original, value)
        assert "names, numbers, negations and qualifications" in system
        assert "Do not invent facts" in system and original in user
    for text in (original.replace("12", "13"), original + " 2 extra.", "[slow] " + original,
                 original.replace("Mara", "*Mara*"), original + " /", original.replace("Mara", "/Mara"), None):
        with pytest.raises(ValueError):
            parse_rewrite(json.dumps({"text": text}), original, "tone", 1)


@pytest.fixture
def app(tmp_path, monkeypatch):
    takes = takes_with(tmp_path, monkeypatch, None)
    a = ControlledAssistant()
    takes.assistant, takes.previews = a, Previews(a)
    ov = TextOverlay(takes.notes.sentences, (1280, 720))
    view = ViewState(app="prepare", mode="focus", level="sentence", focus=Hit(0, None), llm="local")
    yield takes, a, ov, view
    a.pending.clear()
    takes.close()


def warm_up(app):
    takes, a, ov, view = app
    view.ops = OpsView(kind="tone", tone=1)
    takes.sync_edit(view, ov, 0)
    takes.sync_edit(view, ov, .3)


def test_app_starts_only_after_deliberate_control_activation(app):
    takes, a, ov, view = app
    takes.sync_edit(view, ov, 0)
    assert not a.calls and takes.previews.operation is None
    warm_up(app)
    assert len(a.calls) == 1 and takes.notes_version == 0
    view.mode = "browse"
    takes.sync_edit(view, ov, .4)
    a.complete(1, WARM)
    takes.poll_llm()
    assert not takes.previews.operation and takes.notes.sentences[0].text == ORIGINAL


def test_commit_requires_a_complete_visible_candidate_and_stays_open_while_loading(app):
    takes, a, ov, view = app
    warm_up(app)
    event = GestureEvent("commit", .35, "sentence", "tone", value=1)
    main.apply_event(event, view, ov, takes.log, takes=takes)
    assert view.focus == Hit(0, None) and view.note == "PREVIEW NOT READY"
    assert takes.notes_version == 0
    a.complete(1, WARM)
    takes.poll_llm()
    # The answer exists, but has not yet appeared in a rendered frame.
    assert not takes.commit_preview(view, (0,), 1, .4)[0]
    takes.sync_edit(view, ov, .5)
    assert not takes.commit_preview(view, (0,), -1, .5)[0]  # gesture moved to another target
    main.apply_event(event, view, ov, takes.log, takes=takes)
    assert view.focus is None and takes.notes.sentences[0].text == WARM
    assert takes.undo() == "UNDONE" and takes.notes.sentences[0].text == ORIGINAL


def test_preview_and_commit_preserve_historical_notes(app):
    takes, a, ov, view = app
    take = takes.session.add_take(np.zeros(800, np.float32), 8000, 0, datetime.now(), [(0., 0)])
    original_revision = take.revision
    before = json.dumps(takes.session.snapshot(original_revision), sort_keys=True)
    warm_up(app)
    a.complete(1, WARM)
    takes.poll_llm()
    takes.sync_edit(view, ov, .4)
    assert takes.notes.sentences[0].text == ORIGINAL
    assert takes.session.current_revision == original_revision
    assert takes.commit_preview(view, (0,), 1, .5)[0]
    assert take.revision == original_revision
    assert takes.session.notes_for(take).sentences[0].text == ORIGINAL
    assert json.dumps(takes.session.snapshot(original_revision), sort_keys=True) == before
    assert takes.undo() == "UNDONE"
    assert takes.session.current_revision == original_revision


def test_original_commit_is_no_revision_and_cancel_invalidates_answer(app):
    takes, a, ov, view = app
    warm_up(app)
    view.ops.tone = 0
    takes.sync_edit(view, ov, .4)
    assert view.edit_preview.text == ORIGINAL
    assert takes.commit_preview(view, (0,), 0, .5) == (True, "ORIGINAL KEPT: NO CHANGE")
    assert takes.notes_version == 0
    a.complete(1, WARM)
    takes.poll_llm()
    assert takes.previews.operation is None
    warm_up(app)
    main.apply_event(GestureEvent("back", .4, "sentence", "tone"), view, ov, takes.log, takes=takes)
    a.complete(2, WARM)
    takes.poll_llm()
    assert view.focus is None and view.edit_preview is None
    assert takes.notes.sentences[0].text == ORIGINAL


def test_removed_delivery_marks_are_flagged_never_remapped(app):
    takes, a, ov, view = app
    takes.notes = parse_text("[slow] Thank you for *being* here tonight. //")
    ov = TextOverlay(takes.notes.sentences, (1280, 720))
    view.ops = OpsView(kind="tone", tone=1)
    takes.sync_edit(view, ov, 0)
    assert view.edit_preview.marks_warning
    assert "LEGACY MARKS" in " ".join(row for _, row in ov.label_rows(view))
    takes.sync_edit(view, ov, .3)
    a.complete(1, WARM)
    takes.poll_llm()
    takes.sync_edit(view, ov, .4)
    assert takes.commit_preview(view, (0,), 1, .5)[0]
    assert takes.notes.sentences[0].text == takes.notes.sentences[0].raw == WARM
    assert not hasattr(takes.notes.sentences[0], "marks")


def test_actual_async_assistant_can_finish_in_reverse_order_without_sleep():
    entered = Queue()
    gates = [Event(), Event()]

    def reply(system, user):
        i = 0 if "warmer" in system else 1
        entered.put(i)
        assert gates[i].wait(3), "test did not release provider"
        return json.dumps({"text": WARM if i == 0 else COLD})

    a = Assistant(FakeProvider(reply))
    p = Previews(a)
    try:
        sync(p, 1, 0)
        sync(p, 1, .3)
        assert entered.get(timeout=3) == 0
        sync(p, -1, .4)
        sync(p, -1, .7)
        assert entered.get(timeout=3) == 1
        for i in (1, 0):
            gates[i].set()
            answer = a._answers.get(timeout=3)
            a._answers.put(answer)
            receive(p, a)
        assert p.view().text == COLD
    finally:
        for gate in gates:
            gate.set()


@pytest.mark.parametrize("ratio,requested", [(.7, .7), (1, 1), (1.3, 1.3)])
def test_length_targets_report_requested_and_actual_counts_separately(ratio, requested):
    a = ControlledAssistant()
    p = Previews(a)
    original = "We made a mirror. It helps us practise speaking."
    sync(p, ratio, 0, kind="length", original=original)
    sync(p, ratio, .3, kind="length", original=original)
    assert p.view().requested_target == requested
    assert p.view().target_words == round(len(original.split()) * ratio)
    if ratio == 1:
        assert p.view().text == original and not a.calls
    else:
        candidate = "Our mirror helps us practise speaking." if ratio < 1 else "We made a mirror that helps us practise speaking aloud, so we can rehearse."
        a.complete(1, candidate)
        receive(p, a)
        assert p.view().actual_words == len(candidate.split())
        sync(p, 1, 1, kind="length", original=original)
        assert p.view().text == original


def test_pinch_lift_retries_error_without_committing_previous_candidate(app):
    takes, a, ov, view = app
    warm_up(app)
    a.complete(1, error="TIMED OUT")
    takes.poll_llm()
    takes.sync_edit(view, ov, .4)
    assert takes.commit_preview(view, (0,), 1, .5) == (False, "RETRYING PREVIEW")
    assert view.focus is not None and takes.notes_version == 0
    takes.sync_edit(view, ov, .8)
    assert len(a.calls) == 2
    a.complete(2, WARM)
    takes.poll_llm()
    takes.sync_edit(view, ov, .9)
    assert takes.commit_preview(view, (0,), 1, 1)[0]


def test_notes_change_and_save_failure_cannot_commit_an_invalid_preview(app, monkeypatch):
    takes, a, ov, view = app
    warm_up(app)
    a.complete(1, WARM)
    takes.poll_llm()
    takes.sync_edit(view, ov, .4)
    from palmcards.session import SessionError
    def fail(*args, **kwargs):
        raise SessionError("disk full")
    with monkeypatch.context() as m:
        m.setattr(takes.session, "edit", fail)
        assert takes.commit_preview(view, (0,), 1, .5) == (False, "EDIT NOT SAVED (SEE TERMINAL)")
    assert takes.previews.operation and view.focus is not None and takes.notes_version == 0
    takes.use_alternative(0, 0, "Thanks")
    assert takes.previews.operation is None
    assert not takes.commit_preview(view, (0,), 1, .6)[0]


def test_paragraph_commit_cancel_and_exact_original_undo(app):
    takes, a, ov, view = app
    view.level, view.ops = "paragraph", OpsView(kind="stretch", stretch=1.3)
    original = [s.text for s in takes.notes.sentences]
    takes.sync_edit(view, ov, 0)
    takes.sync_edit(view, ov, .3)
    unit = takes.previews.operation.unit
    assert unit == (0, 1, 2)
    candidate = "Thank you for joining us tonight. We are very glad that you came along to be here with us."
    a.complete(1, candidate)
    takes.poll_llm()
    takes.sync_edit(view, ov, .4)
    assert [s.text for s in takes.notes.sentences] == original
    assert takes.commit_preview(view, unit, 1.3, .5)[0]
    assert takes.undo() == "UNDONE"
    assert [s.text for s in takes.notes.sentences] == original


def test_delayed_commit_keeps_gesture_focus_and_requires_another_pinch():
    from tests.test_gestures import prepare_sentence_focus, run, hold, l_hand, pinch, lift, H
    g, t = prepare_sentence_focus()
    g.defer_edit_commit = True
    _, t = run(g, hold(l_hand, .3) + hold(l_hand, .3, rotate=40), t)
    events, t = run(g, hold(pinch, .2) + lift(.4, .2 * H), t)
    assert [e.kind for e in events] == ["commit"]
    assert g.state.mode == "focus" and g.state.op == "tone"
    events, t = run(g, hold(pinch, .3, origin=(960, 600 - .2 * H)), t)
    assert not events
    g.accept_edit_commit(t)
    assert g.state.mode == "browse" and g.state.op is None


def test_long_preview_viewport_reaches_last_line_without_covering_header():
    from palmcards.style import LABEL, LAYOUT
    original = "We can practise speaking aloud. " * 35
    notes = parse_text(original)
    a, p = ControlledAssistant(), None
    p = Previews(a)
    sync(p, 1.3, 0, kind="length", unit=tuple(range(len(notes.sentences))), original=original)
    sync(p, 1.3, .3, kind="length", unit=tuple(range(len(notes.sentences))), original=original)
    a.complete(1, "We can take time to practise speaking aloud together. " * 35)
    receive(p, a)
    for w, h in ((640, 480), (1280, 720)):
        ov = TextOverlay(notes.sentences, (w, h))
        view = ViewState(mode="focus", level="paragraph", focus=Hit(0, None),
                         ops=OpsView(kind="stretch", stretch=1.3), edit_preview=p.view())
        panel = ov.panel(view)
        scroll = ov.panel_max_scroll(view)
        assert scroll > 0
        height = ov.panel_view_h(panel)
        assert ov._panel_top(height, panel.header_h) >= LABEL.min_top + sum(ov._label_row_h(i) for i, _ in ov.label_rows(view))
        assert panel.rows[0][1] <= scroll + height
        first, last = np.full((h, w, 3), 80, np.uint8), np.full((h, w, 3), 80, np.uint8)
        ov.draw(first, view)
        view.panel_scroll = scroll
        ov.draw(last, view)
        assert not np.array_equal(first, last)
        x0, x1 = (int(f * w) for f in LAYOUT.face)
        assert (last[:, x0:x1] == 80).all()
        assert "NOT SAVED" in " ".join(text for _, text in ov.label_rows(view))


def test_long_original_is_not_silently_truncated_for_provider():
    from palmcards.config import LLM
    a = ControlledAssistant()
    p = Previews(a)
    sync(p, 1, 0, original="word " * LLM.max_chars)
    sync(p, 1, .3, original="word " * LLM.max_chars)
    assert "TOO LONG" in p.view().error and not a.calls


def test_legacy_snapshot_marks_without_raw_markup_are_flagged(app, monkeypatch):
    takes, a, ov, view = app
    takes.session._ensure_written()
    saved = json.loads(json.dumps(takes.session.snapshot(takes.session.current_revision)))
    saved['parser'] = 1
    saved['sentences'][0]['marks'] = [{'kind': 'slow', 'word': None}, {'kind': 'short_pause', 'word': 2}]
    saved['sentences'][0]['words'][2]['stressed'] = True
    before = json.dumps(saved, sort_keys=True)
    monkeypatch.setattr(takes.session, 'snapshot', lambda rid: saved)
    warm_up(app)
    assert view.edit_preview.marks_warning
    assert json.dumps(saved, sort_keys=True) == before
    assert a.calls[1][3][1].endswith('</notes>')
