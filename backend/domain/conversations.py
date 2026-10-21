"""The assistant's kept threads, and the record of each run of suggestions.

A *thread* is one running conversation an author has with the assistant, kept so it is
remembered across visits (AST-EXP-017: Change anything; AST-EXP-005: starting from a
premise). Each author has at most one thread of each kind in each line of the story. A
thread is a list of turns: what the author said and what the assistant answered. The
words are the author's: private to them, never logged, erased when they clear it.

When the assistant is asked again, the last few turns are put back into what it reads,
inside markers, as material and never as instructions (``backend/domain/prompting.py``).

A *suggestion run* is one batch of suggestions the author reviews, recorded with counts
only, so how good the suggestions are is measured from what authors do with them:

- precision (AST-EXP-007): of the connections suggested, how many the author kept
  (those a keep-for-me rule kept are counted apart, as kept for the author);
- groundedness (AST-EXP-013): of the plan suggestions, how many quote the author's
  material, and how many are the assistant's own (invented).

A run with a set of suggested changes is *settled* once the author has decided on it
(saved or turned down); until then how many were kept is unknown, never guessed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# Turns kept per thread; the oldest go first, except what a premise is made of: in a
# premise thread the author's turns that tell the story are never let go (see ``pinned``).
MAX_TURNS = 200
# Turns put back into what the assistant reads, and how much of them.
RECALLED_TURNS = 8
RECALLED_CHARS = 6000
MAX_TURN_LENGTH = 8000


class ThreadKind(StrEnum):
    CHANGE = "change"
    PREMISE = "premise"


class TurnRole(StrEnum):
    AUTHOR = "author"
    ASSISTANT = "assistant"


class TurnAnswer(BaseModel):
    question: str
    answer: str


class Turn(BaseModel):
    """One turn. ``body`` is what the author said, or the assistant's restatement."""

    position: int = 0
    role: TurnRole
    body: str = ""
    questions: list[str] = Field(default_factory=list)
    answers: list[TurnAnswer] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # The set of suggested changes this turn led to, and how many changes it held.
    mutation_id: str | None = None
    operation_count: int = 0
    # The conversation (for spending and evidence) the turn belongs to.
    session_id: str | None = None
    created_at: datetime | None = None


class Thread(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    actor_user_id: str
    kind: ThreadKind
    branch_id: str | None = None
    created_at: datetime
    updated_at: datetime
    turns: list[Turn] = Field(default_factory=list)
    # What the provider reported the thread's replies used, for its budget.
    tokens_used: int = 0

    def session_ids(self) -> list[str]:
        return list(dict.fromkeys(turn.session_id for turn in self.turns if turn.session_id))

    def mutation_ids(self) -> list[str]:
        return list(dict.fromkeys(turn.mutation_id for turn in self.turns if turn.mutation_id))


def _size(turn: Turn) -> int:
    return (
        len(turn.body)
        + sum(len(q) for q in turn.questions)
        + sum(len(a.question) + len(a.answer) for a in turn.answers)
    )


def _fit(turn: Turn, room: int) -> Turn:
    """The turn cut down to ``room`` characters: its words first, then its questions
    and answers, in order, as far as they go."""
    body = turn.body[:room]
    room -= len(body)
    questions: list[str] = []
    for question in turn.questions:
        if room <= 0:
            break
        questions.append(question[:room])
        room -= len(questions[-1])
    answers: list[TurnAnswer] = []
    for item in turn.answers:
        if room <= len(item.question):
            break
        answer = item.answer[: room - len(item.question)]
        answers.append(TurnAnswer(question=item.question, answer=answer))
        room -= len(item.question) + len(answer)
    return turn.model_copy(update={"body": body, "questions": questions, "answers": answers})


def recalled(turns: Sequence[Turn], *, limit: int = RECALLED_TURNS) -> list[Turn]:
    """The last turns, newest kept, bounded in number and in length. The newest turn is
    always there, cut down to the bound when it is longer on its own."""
    picked: list[Turn] = []
    used = 0
    for turn in reversed(list(turns)[-limit:]):
        size = _size(turn)
        if not picked and size > RECALLED_CHARS:
            turn, size = _fit(turn, RECALLED_CHARS), RECALLED_CHARS
        if used + size > RECALLED_CHARS:
            break
        used += size
        picked.append(turn)
    return list(reversed(picked))


def pinned(kind: ThreadKind, turn: Turn) -> bool:
    """Whether a turn is kept however long the thread grows: in a premise thread, each
    round is read from everything the author told about the story, so those turns stay
    (their length is bounded by the premise's own limit)."""
    return kind is ThreadKind.PREMISE and turn.role is TurnRole.AUTHOR and bool(turn.body)


def recall_text(turns: Sequence[Turn]) -> str:
    """Earlier turns as plain lines for the assistant to read (inside markers)."""
    lines: list[str] = []
    for turn in turns:
        if turn.role is TurnRole.AUTHOR:
            if turn.body:
                lines.append(f"The author said: {turn.body}")
            lines.extend(
                f"The author answered “{item.question}”: {item.answer}" for item in turn.answers
            )
        else:
            if turn.body:
                lines.append(f"You understood: {turn.body}")
            lines.extend(f"You asked: {question}" for question in turn.questions)
            if turn.mutation_id:
                lines.append(
                    f"That led to {turn.operation_count} suggested change"
                    f"{'s' if turn.operation_count != 1 else ''} for the author to review."
                )
    return "\n".join(lines)


class RunKind(StrEnum):
    LINKS = "links"
    PLAN_DRAFT = "plan_draft"
    PLAN_NEXT = "plan_next"
    PREMISE = "premise"


class RunSource(StrEnum):
    # Candidates found by search by meaning, and the listing after them.
    MEANING = "meaning"
    # Only what the assistant was listed.
    LISTING = "listing"


# The count in a run's ``details`` of suggestions a keep-for-me rule kept.
KEPT_FOR_YOU = "kept_for_you"


class SuggestionRun(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    kind: RunKind
    actor_user_id: str | None = None
    branch_id: str | None = None
    mutation_id: str | None = None
    source: RunSource = RunSource.LISTING
    prompt_version: str | None = None
    suggested: int = 0
    grounded: int | None = None
    invented: int | None = None
    kept: int | None = None
    settled_at: datetime | None = None
    created_at: datetime
    # Counts only (how many candidates were found by meaning, how many were left out).
    details: dict[str, int] = Field(default_factory=dict)

    @property
    def kept_for_you(self) -> int | None:
        """How many a keep-for-me rule kept for the author (AST-EXP-015) without the
        author deciding: counted apart, never as kept by the author. Kept with the
        counts in ``details``."""
        value = self.details.get(KEPT_FOR_YOU)
        return None if value is None else int(value)

    @property
    def precision(self) -> float | None:
        """Of what was suggested, the share the author kept; None until decided, and
        None when a rule kept them for the author (the author decided nothing)."""
        if self.kept is None or self.suggested <= 0 or self.kept_for_you:
            return None
        return self.kept / self.suggested

    @property
    def groundedness(self) -> float | None:
        """Of the suggestions, the share that quote the author's material."""
        if self.grounded is None or self.invented is None:
            return None
        total = self.grounded + self.invented
        return self.grounded / total if total else None


_SPACES = re.compile(r"\s+")
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-"})
# A quote must be at least this long to count: "the" is in every text.
MIN_QUOTE_LENGTH = 12


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    return _SPACES.sub(" ", text).strip(" .,;:!?\"'")


def quotes(quote: str | None, material: str) -> bool:
    """Whether ``quote`` is the author's own words: found in ``material`` as written.

    Spacing, case and curly quotes are ignored; nothing else is. A quote too short to
    tell anything apart does not count.
    """
    wanted = _plain(quote or "")
    if len(wanted) < MIN_QUOTE_LENGTH:
        return False
    return wanted in _plain(material)


__all__ = [
    "KEPT_FOR_YOU",
    "MAX_TURNS",
    "MAX_TURN_LENGTH",
    "MIN_QUOTE_LENGTH",
    "RunKind",
    "RunSource",
    "SuggestionRun",
    "Thread",
    "ThreadKind",
    "Turn",
    "TurnAnswer",
    "TurnRole",
    "pinned",
    "quotes",
    "recall_text",
    "recalled",
]
