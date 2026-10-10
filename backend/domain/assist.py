"""Conversations with the assistant, kept as the evidence behind what they produced."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AssistKind(StrEnum):
    CREATE = "create"
    FILL_IN = "fill_in"
    CHANGE = "change"


class AssistStatus(StrEnum):
    # Still being worked on; the author can pick it up again.
    OPEN = "open"
    # Led to a set of suggested changes; kept as the evidence behind them.
    PROPOSED = "proposed"
    # Put away by the author without proposing anything. Reported once, then erased:
    # no row is kept in this state.
    CLOSED = "closed"


class AssistQuestion(BaseModel):
    id: str
    question: str
    category: str = ""


class AssistAnswer(BaseModel):
    question_id: str
    question: str
    answer: str


class AssistSession(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    actor_user_id: str
    kind: AssistKind
    status: AssistStatus = AssistStatus.OPEN
    # The author's description, or their instruction for a change.
    description: str = ""
    entity_type: str | None = None
    name: str | None = None
    # The entry being filled in, or the one the author was looking at.
    entity_id: str | None = None
    questions: list[AssistQuestion] = Field(default_factory=list)
    answers: list[AssistAnswer] = Field(default_factory=list)
    # A change conversation: the assistant's restatement and what else it noticed.
    understood: str = ""
    notes: list[str] = Field(default_factory=list)
    mutation_id: str | None = None
    created_at: datetime
    updated_at: datetime
