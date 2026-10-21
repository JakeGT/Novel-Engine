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
        # 2: the story bible is listed by the change-story policy (CTX-002), the request's
        # own entries first, with a last line saying how much is not listed.
        2,
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
        # 2: the story bible is listed by the suggest-links policy (CTX-002), entries whose
        # details name others first, with a last line saying how much is not listed.
        2,
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
        # 2: the read-text listing policy (CTX-002), and events and thread steps (EXT-001).
        2,
        "What a passage of the author's writing establishes, as changes to review.",
        _S + "intake_workflow:read_passage_messages",
        _S + "intake_workflow:PassageDraft",
    ),
    PromptSpec(
        "read-scene",
        # 2: each fact says whether the scene shows it changing or starting there,
        # instead of whether it was already so; only those take the scene's time.
        # 3: the read-text listing policy (CTX-002), and events (in the scene or not) and
        # thread steps (EXT-001).
        3,
        "A passage of a written scene set at a known story time, read as changes to review; "
        "each fact also says whether the scene shows it changing or starting there.",
        _S + "intake_workflow:read_scene_messages",
        _S + "intake_workflow:SceneDraft",
    ),
    PromptSpec(
        "draft-rule",
        # 2: ranges, implications, incompatibilities, exceptions, beliefs and story
        # time, with examples that carry times and beliefs (CON-005, CAN-013).
        2,
        "A continuity rule the author wrote as a sentence, turned into a checkable rule.",
        _S + "rule_workflow:rule_messages",
        _S + "rule_workflow:RuleDraft",
    ),
    PromptSpec(
        "scene-draft",
        # 3: the scene's conditions are given with its context (STY-005).
        # 4: the names of the pictures sent go inside the material's markers (CTX-013).
        4,
        "A draft of one scene, in the author's voice, for them to keep or discard.",
        _S + "studio_service:draft_messages",
    ),
    # A check of a scene, one pass each (CON-008); these replace "scene-review" (3).
    PromptSpec(
        "check-viewpoint",
        1,
        "Notes quoting a scene where its viewpoint character knows too much or a secret slips.",
        _S + "scene_checks:viewpoint_messages",
        _S + "scene_checks:CheckNotes",
    ),
    PromptSpec(
        "check-continuity",
        1,
        "Notes quoting a scene where it goes against the story bible, rules or past events.",
        _S + "scene_checks:continuity_messages",
        _S + "scene_checks:CheckNotes",
    ),
    PromptSpec(
        "check-continuity-group",
        1,
        "Notes quoting some passages of a long scene where they go against the parts of the "
        "story they bear on, for one kind of question (CON-013).",
        _S + "scene_checks:continuity_group_messages",
        _S + "scene_checks:CheckNotes",
    ),
    PromptSpec(
        "check-plan",
        1,
        "Notes quoting a scene where it goes against, or leaves out, what its plan asks for.",
        _S + "scene_checks:plan_messages",
        _S + "scene_checks:CheckNotes",
    ),
    PromptSpec(
        "check-voice",
        1,
        "Notes quoting a scene where it does not sound like the author's own writing.",
        _S + "scene_checks:voice_messages",
        _S + "scene_checks:CheckNotes",
    ),
    PromptSpec(
        "check-clarity",
        1,
        "Notes quoting passages of a scene a reader could find hard to follow.",
        _S + "scene_checks:clarity_messages",
        _S + "scene_checks:CheckNotes",
    ),
    PromptSpec(
        "check-pictures",
        1,
        "Notes quoting a scene where it describes someone or somewhere differently from "
        "their pictures, naming the picture; the pictures go with the request.",
        _S + "scene_checks:pictures_messages",
        _S + "scene_checks:PictureNotes",
    ),
    PromptSpec(
        "describe-picture",
        1,
        "A caption and appearance details of one picture, for the author to review; the "
        "picture goes with the request.",
        _S + "media_observations:describe_messages",
        _S + "media_observations:PictureDescription",
    ),
    PromptSpec(
        "scene-plan-card",
        1,
        "A scene's plan card (summary, viewpoint, place, who is in it) from its prose.",
        _S + "scene_plan_card:plan_card_messages",
        _S + "scene_plan_card:PlanCardDraft",
    ),
    PromptSpec(
        "scene-summary",
        1,
        "A short summary of a scene's prose, kept only when the author accepts it (CTX-006).",
        _S + "scene_summaries:summary_messages",
        _S + "scene_summaries:SceneSummaryDraft",
    ),
    PromptSpec(
        "scene-intent",
        2,
        "A scene's intent (what its viewpoint character wants, what is in the way, how it "
        "ends) from its plan card and prose, each part with the words that show it.",
        _S + "planning_help:scene_intent_messages",
        _S + "planning_help:IntentDraft",
    ),
    PromptSpec(
        "bridge-scenes",
        1,
        "One or two scenes to come before a scene and bring about what it needs first.",
        _S + "planning_help:bridge_messages",
        _S + "planning_help:BridgeSheet",
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
