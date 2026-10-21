"""Merging two entries by hand (AST-EXP-017): see what it would do, then send it to review.

No model is called. A merge is one reviewed change; it retires an entry, so it is saved
only once approved, and it is undone in one step from history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_merge_service
from backend.api.v1.canon_characters import ProposalIssueModel
from backend.domain.identity import AccessScope
from backend.domain.operations import MergeEntitiesOp
from backend.services.merge_service import MergeService, MergeSummary

router = APIRouter(prefix="/projects/{project_id}/merges", tags=["canon-entities"])


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The entry that goes: everything known about it moves to the one kept.
    entity_id: str = Field(min_length=1, max_length=200)
    # The entry that stays.
    into: str = Field(min_length=1, max_length=200)
    # A new name for the kept entry, if it should change.
    name: str | None = Field(default=None, max_length=200)


class MergePreviewRequest(MergeRequest):
    # Facts and beliefs a reviewed merge ends rather than moves (from the suggestion).
    drop_facts: list[str] = Field(default_factory=list, max_length=500)
    drop_beliefs: list[str] = Field(default_factory=list, max_length=500)
    # When previewing a merge already suggested, what it was suggested with.
    suggested: bool = False


class EndedModel(BaseModel):
    sentence: str
    reason: str


class MergePreviewModel(BaseModel):
    keep_id: str
    keep_name: str
    merged_id: str
    merged_name: str
    entry_type: str
    # The kept entry once merged: its name, other names and details.
    name: str
    aliases: list[str]
    details: dict[str, Any]
    # Other names left out, as an entry keeps at most 40 of them.
    names_left_out: int = 0
    # In plain sentences: what moves to the kept entry, and what ends instead, and why.
    facts_moved: list[str]
    facts_ended: list[EndedModel]
    beliefs_moved: list[str]
    beliefs_ended: list[EndedModel]
    # Events and parts of the plan that will name the kept entry.
    events: list[str]
    plan_items: list[str]
    pictures_moved: int
    pictures_ended: int
    # What keeps naming the merged entry (what a character said keeps its words).
    still_naming: list[str]
    # How many changes the merge makes when it is saved.
    change_count: int


def _preview(summary: MergeSummary) -> MergePreviewModel:
    return MergePreviewModel(
        keep_id=summary.keep_id,
        keep_name=summary.keep_name,
        merged_id=summary.merged_id,
        merged_name=summary.merged_name,
        entry_type=summary.entry_type,
        name=summary.name,
        aliases=summary.aliases,
        details=summary.details,
        names_left_out=summary.aliases_cut,
        facts_moved=summary.facts_moved,
        facts_ended=[EndedModel(sentence=i.sentence, reason=i.reason) for i in summary.facts_ended],
        beliefs_moved=summary.beliefs_moved,
        beliefs_ended=[
            EndedModel(sentence=i.sentence, reason=i.reason) for i in summary.beliefs_ended
        ],
        events=summary.events,
        plan_items=summary.plan_items,
        pictures_moved=summary.pictures_moved,
        pictures_ended=summary.pictures_ended,
        still_naming=summary.still_naming,
        change_count=summary.change_count,
    )


@router.post("/preview", response_model=MergePreviewModel)
async def preview_merge(
    project_id: str,
    body: MergePreviewRequest,
    merges: MergeService = Depends(get_merge_service),
    scope: AccessScope = Depends(get_project_scope),
) -> MergePreviewModel:
    """What merging one entry into another would do now. Changes nothing."""
    merge = (
        MergeEntitiesOp(
            id=body.entity_id,
            into=body.into,
            name=body.name,
            drop_facts=body.drop_facts,
            drop_beliefs=body.drop_beliefs,
        )
        if body.suggested
        else None
    )
    return _preview(
        await merges.preview(
            scope,
            scope.workspace_id or "",
            project_id,
            entity_id=body.entity_id,
            into=body.into,
            name=body.name,
            merge=merge,
        )
    )


class MergeProposalRequest(MergeRequest):
    expected_revision: int = Field(ge=0)


class MergeProposalModel(BaseModel):
    mutation_id: str
    status: str
    validation_ok: bool
    validation_issues: list[ProposalIssueModel]


@router.post("", response_model=MergeProposalModel, status_code=201)
async def propose_merge(
    project_id: str,
    body: MergeProposalRequest,
    merges: MergeService = Depends(get_merge_service),
    scope: AccessScope = Depends(require_project_scope),
) -> MergeProposalModel:
    """Send a merge to review. It is saved only once the author approves it."""
    mutation, validation = await merges.propose(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id=body.entity_id,
        into=body.into,
        name=body.name,
        expected_revision=body.expected_revision,
    )
    return MergeProposalModel(
        mutation_id=mutation.id,
        status=mutation.status.value,
        validation_ok=validation.ok,
        validation_issues=[
            ProposalIssueModel(
                ordinal=issue.ordinal,
                code=issue.code,
                message=issue.message,
                severity=issue.severity,
            )
            for issue in validation.issues
        ],
    )
