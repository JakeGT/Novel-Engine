"""Every prompt the app sends to a model, in one list (SCN-003).

Each prompt has an id, a version, a plain description of what it is for, the function
that builds the messages, and (for prompts that ask for a shaped answer) the model of the
answer. Services take their version from here, so the version recorded with a model call
is always the one listed.

The wording lives next to the work that uses it; this list only points at it, by import
path, so the domain layer imports no service. ``tests/prompts`` builds every prompt from a
fixed input and compares it with a stored snapshot: changing a prompt's wording, or the
shape of the answer it asks for, fails that test until the version here is raised and the
snapshot is updated (``uv run python -m tests.prompts.snapshots --update``).

What a builder is given is the material the service has already gathered and formatted
(the story bible, the plan, the scene's context); how that material is laid out is tested
with the code that gathers it, not here. The provider check in
``backend.devtools.diagnostics`` carries no story and asks for one word, so it is not
listed.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Messages = list[dict[str, str]]


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: int
    purpose: str
    # "module:function" building the messages exactly as they are sent.
    builder: str
    # "module:Class" of the answer asked for; None when the answer is prose.
    output: str | None = None

    @property
    def version_id(self) -> str:
        """How the version is recorded with a model call, e.g. ``scene-draft/2``."""
        return f"{self.id}/{self.version}"

    def build(self) -> Callable[..., Messages]:
        built: Callable[..., Messages] = _resolve(self.builder)
        return built

    def output_model(self) -> Any | None:
        return _resolve(self.output) if self.output else None


def _resolve(path: str) -> Any:
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


_S = "backend.services."

_SPECS = (
    PromptSpec(
        "character-questions",
        2,
        "Questions for the author about one character in the story bible.",
        _S + "canon_character_workflow:character_questions_messages",
        _S + "canon_character_workflow:CharacterQuestionSheet",
    ),
    PromptSpec(
        "character-proposal",
        1,
        "The author's answers about a character, turned into changes to review.",
        _S + "canon_character_workflow:character_proposal_messages",
        _S + "canon_character_workflow:CharacterProposal",
    ),
    PromptSpec(
        "creation-questions",
        1,
        "Questions about something the author wants to add to the story bible.",
        _S + "canon_creation_workflow:creation_questions_messages",
        _S + "canon_character_workflow:CharacterQuestionSheet",
    ),
    PromptSpec(
        "creation-draft",
        1,
        "The author's description and answers, turned into new entries to review.",
        _S + "canon_creation_workflow:creation_draft_messages",
        _S + "canon_creation_workflow:CreationDraft",
    ),
    PromptSpec(
        "change-by-instruction",
        1,
        "One instruction from the author, worked out into every change it implies.",
        _S + "canon_change_workflow:change_messages",
        _S + "canon_change_workflow:ChangePlan",
    ),
    PromptSpec(
        "revise-proposal",
        1,
        "Edits to suggested changes that are waiting for review, as the author asks.",
        _S + "canon_revise_workflow:revise_messages",
        _S + "canon_revise_workflow:RevisionPlan",
    ),
    PromptSpec(
        "suggest-links",
        1,
        "Connections the story bible's own words imply but no fact records yet.",
        _S + "canon_revise_workflow:links_messages",
        _S + "canon_revise_workflow:LinkPlan",
    ),
    PromptSpec(
        "plan-questions",
        1,
        "Questions that would let a part of the story's plan be written down.",
        _S + "plan_workflow:plan_questions_messages",
        _S + "canon_character_workflow:CharacterQuestionSheet",
    ),
    PromptSpec(
        "plan-draft",
        1,
        "What the author said about the story, turned into plan items to review.",
        _S + "plan_workflow:plan_draft_messages",
        _S + "plan_workflow:PlanDraft",
    ),
    PromptSpec(
        "plan-next",
        1,
        "Two or three directions the story could take next, for the author to choose from.",
        _S + "plan_workflow:plan_next_messages",
        _S + "plan_workflow:DirectionSheet",
    ),
    PromptSpec(
        "read-passage",
        1,
        "What a passage of the author's writing establishes, as changes to review.",
        _S + "intake_workflow:read_passage_messages",
        _S + "intake_workflow:PassageDraft",
    ),
    PromptSpec(
        "draft-rule",
        1,
        "A continuity rule the author wrote as a sentence, turned into a checkable rule.",
        _S + "rule_workflow:rule_messages",
        _S + "rule_workflow:RuleDraft",
    ),
    PromptSpec(
        "scene-draft",
        # 3: the scene's conditions are given with its context (STY-005).
        3,
        "A draft of one scene, in the author's voice, for them to keep or discard.",
        _S + "studio_service:draft_messages",
    ),
    PromptSpec(
        "scene-review",
        # 3: the scene's conditions are given with its context (STY-005).
        3,
        "Notes on a draft scene that quote it: contradictions, viewpoint slips, gaps.",
        _S + "studio_service:review_messages",
        _S + "studio_service:ReviewNotes",
    ),
    PromptSpec(
        "scene-plan-card",
        1,
        "A scene's plan card (summary, viewpoint, place, who is in it) from its prose.",
        _S + "scene_plan_card:plan_card_messages",
        _S + "scene_plan_card:PlanCardDraft",
    ),
    PromptSpec(
        "legacy-character-questions",
        1,
        "Questions about a character, for the older graph tools (local mode only).",
        _S + "character_service:interview_questions_messages",
        _S + "character_service:CharacterQuestionSheet",
    ),
    PromptSpec(
        "legacy-graph-update",
        1,
        "Answers about a character as graph changes, for the older graph tools (local only).",
        _S + "character_service:graph_update_messages",
        _S + "character_service:GraphUpdate",
    ),
    PromptSpec(
        "reply-as-json",
        1,
        "Added to any shaped request for a model that cannot be held to a shape itself.",
        "backend.adapters.ai:prompted_messages",
    ),
)

PROMPTS: dict[str, PromptSpec] = {spec.id: spec for spec in _SPECS}


def prompt_version(prompt_id: str) -> str:
    """The recorded version of a listed prompt; an unlisted id is a programming error."""
    return PROMPTS[prompt_id].version_id


__all__ = ["PROMPTS", "Messages", "PromptSpec", "prompt_version"]
