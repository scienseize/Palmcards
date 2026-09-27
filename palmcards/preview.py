"""Operation-local rewrites. No Notes are mutated and no clock or provider is hidden here.

The app supplies its clock and the existing asynchronous Assistant. Answers must
match the operation, source revision, unit, selected target, generation and ticket.
Cancelled/superseded calls may finish (and are still billed/logged), never displayed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from palmcards.config import PREVIEW, LLM, Preview
from palmcards.llm import Answer, Assistant, parse_rewrite, rewrite_request


def target_for(value: float, current: float, targets: tuple[float, ...], margin: float) -> float:
    """Schmitt boundaries between neighbouring targets; large moves cross multiple bins."""
    i = targets.index(current)
    while i < len(targets) - 1 and value > (targets[i] + targets[i + 1]) / 2 + margin:
        i += 1
    while i > 0 and value < (targets[i - 1] + targets[i]) / 2 - margin:
        i -= 1
    return targets[i]


@dataclass
class EditOperation:
    operation_id: int
    source_revision: str
    unit: tuple[int, ...]
    kind: str
    original: str
    requested_target: float
    displayed_candidate: str
    displayed_target: float
    stable_since: float
    generation: int = 0
    request_id: int | None = None
    loading: bool = False
    error: str = ""
    cached_candidates: dict[float, str] = field(default_factory=dict)
    marks_warning: bool = False

    @property
    def original_target(self) -> float:
        return 0.0 if self.kind == "tone" else 1.0


@dataclass(frozen=True)
class PreviewView:
    operation_id: int
    kind: str
    requested_target: float
    displayed_target: float
    text: str
    original: bool
    target_words: int
    actual_words: int
    loading: bool
    error: str
    marks_warning: bool

    @property
    def target_label(self) -> str:
        if self.kind == "tone":
            return "WARM" if self.requested_target > 0 else "COLD" if self.requested_target < 0 else "ORIGINAL"
        return f"{round(self.requested_target * 100)}% (~{self.target_words} WORDS)"

    @property
    def shown_label(self) -> str:
        if self.original:
            return "ORIGINAL"
        if self.kind == "tone":
            return "WARM" if self.displayed_target > 0 else "COLD"
        return f"{round(self.displayed_target * 100)}%"

    @property
    def status(self) -> str:
        shown = f"SHOWING {self.shown_label}: {self.actual_words} WORDS"
        if self.error:
            return f"{self.error} / {shown}"
        if self.loading:
            return f"UPDATING PREVIEW... / {shown}"
        return f"{'ORIGINAL' if self.original else 'PREVIEW - NOT SAVED'} / {self.actual_words} WORDS"


class Previews:
    def __init__(self, assistant: Assistant | None, config: Preview = PREVIEW):
        self.assistant, self.config = assistant, config
        self.operation: EditOperation | None = None
        self._next_id = 0

    def cancel(self) -> None:
        self.operation = None

    def select(self, value: float, now: float) -> None:
        op = self.operation
        targets = self.config.tone_targets if op.kind == "tone" else self.config.length_targets
        margin = self.config.tone_hysteresis if op.kind == "tone" else self.config.length_hysteresis
        target = target_for(value, op.requested_target, targets, margin)
        if target == op.requested_target:
            return
        op.requested_target, op.stable_since = target, now
        op.generation += 1
        op.request_id, op.error = None, ""
        if target in op.cached_candidates:
            op.displayed_candidate, op.displayed_target = op.cached_candidates[target], target
        op.loading = target not in op.cached_candidates

    def sync(self, kind: str, revision: str, unit: tuple[int, ...], original: str,
             value: float, now: float, marks_warning: bool = False) -> None:
        op = self.operation
        if op is None or (op.kind, op.source_revision, op.unit) != (kind, revision, unit):
            self._next_id += 1
            neutral = 0.0 if kind == "tone" else 1.0
            op = self.operation = EditOperation(self._next_id, revision, unit, kind, original, neutral,
                                                original, neutral, now, cached_candidates={neutral: original},
                                                marks_warning=marks_warning)
        self.select(value, now)
        if op.requested_target in op.cached_candidates:
            op.loading = False
            return
        if self.assistant is None:
            op.error, op.loading = "PROVIDER UNAVAILABLE: SET UP --llm (SEE README)", False
            return
        # Never silently truncate the original paragraph before rewriting it.
        if len(op.original) > LLM.max_chars:
            op.error, op.loading = "PASSAGE TOO LONG FOR PROVIDER; EDIT SMALLER UNITS", False
            return
        if op.error or op.request_id is not None or now - op.stable_since < self.config.debounce_s:
            return
        if sum(key[0] == "preview" for _, key in self.assistant.pending.values() if key) >= self.config.max_inflight:
            return
        target = op.requested_target
        key = ("preview", op.operation_id, op.unit, op.generation, target)
        op.request_id = self.assistant.ask(op.kind, key, op.source_revision,
            rewrite_request(op.kind, op.original, target),
            lambda reply: parse_rewrite(reply, op.original, op.kind, target))
        op.loading = True

    def accept(self, answer: Answer, revision: str) -> bool:
        """Return whether the answer belongs to the preview route, including stale answers."""
        if not answer.key or answer.key[0] != "preview":
            return False
        op = self.operation
        if op is None or answer.revision != revision or answer.revision != op.source_revision \
                or answer.kind != op.kind or answer.ticket != op.request_id \
                or answer.key != ("preview", op.operation_id, op.unit, op.generation, op.requested_target):
            return True
        op.request_id, op.loading = None, False
        if answer.error:
            op.error = f"{answer.reason or 'FAILED'}: PINCH + LIFT TO RETRY"
        else:
            op.displayed_candidate, op.displayed_target = answer.value, op.requested_target
            op.cached_candidates[op.requested_target] = answer.value
            op.error = ""
        return True

    def retry(self, now: float) -> None:
        op = self.operation
        if op is not None and op.error and self.assistant is not None:
            op.error, op.request_id, op.loading = "", None, True
            op.generation += 1
            op.stable_since = now  # retry is deliberate, debounced like any target

    def view(self) -> PreviewView | None:
        op = self.operation
        if op is None:
            return None
        return PreviewView(op.operation_id, op.kind, op.requested_target, op.displayed_target,
                           op.displayed_candidate, op.displayed_target == op.original_target,
                           max(1, round(len(op.original.split()) * (op.requested_target if op.kind == "length" else 1))),
                           len(op.displayed_candidate.split()), op.loading, op.error, op.marks_warning)

    def can_commit(self, shown: PreviewView | None, value: float) -> bool:
        op = self.operation
        if op is None or shown is None:
            return False
        targets = self.config.tone_targets if op.kind == "tone" else self.config.length_targets
        margin = self.config.tone_hysteresis if op.kind == "tone" else self.config.length_hysteresis
        return (target_for(value, op.requested_target, targets, margin) == op.requested_target
                and not op.loading and not op.error and op.displayed_target == op.requested_target
                and shown.operation_id == op.operation_id and shown.requested_target == op.requested_target
                and shown.displayed_target == op.requested_target and shown.text == op.displayed_candidate)
