"""Questions answered from the story's ready views (QRY-001).

Where someone is, who holds what, who is related to whom, what leans on a record, and
which scenes show a fact. Each answer says whether it came from a ready view or was
worked out on read, and at which version of the line of the story. Reads only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.read_views import MAX_DEPTH
from backend.services.read_models import Answer, ReadModelService

router = APIRouter(prefix="/projects/{project_id}", tags=["story-views"])


def get_read_models(container: Container = Depends(get_container)) -> ReadModelService:
    return container.read_models  # type: ignore[no-any-return]


class ViewAnswerModel(BaseModel):
    """Rows of one view of the story. ``answered_from`` is ``view`` when the ready view
    answered and ``worked_out`` when it was worked out on read (the view was missing,
    behind, or not yet verified)."""

    answered_from: Literal["view", "worked_out"]
    version: int
    latest_version: int
    rows: list[dict[str, Any]]
    # True when the story has more records than one read takes, so rows may be missing.
    cut: bool = False


class LeaningRecordModel(BaseModel):
    record: str
    why: str
    depth: int
    via: str


class DependentsModel(BaseModel):
    answered_from: Literal["view", "worked_out"]
    version: int
    latest_version: int
    record: str
    dependents: list[LeaningRecordModel]
    # True when more lean on it than are listed, or the story has more records than one
    # read takes.
    cut: bool


class DisclosuresModel(ViewAnswerModel):
    # The number, in reading order, of the first scene that shows the fact plainly.
    first_shown: int | None = None


class ViewStateModel(BaseModel):
    view: str
    version: int | None
    latest_version: int
    behind: int
    rows: int
    # Whether reads use this view now: verified by replay and up to date.
    answers_reads: bool
    trusted: bool
    verified_at: datetime | None = None
    verified_version: int | None = None
    outcome: Literal["matched", "differed"] | None = None
    differences: int | None = None
    # When it was last built, and (only while it is behind) how many seconds since then:
    # how long reads have been working the answer out instead (QRY-001).
    built_at: datetime | None = None
    lag_seconds: float | None = None
    # In plain words, for the views' screen: "Ready", "Catching up", "Not checked yet",
    # "Differed when checked", "Not built yet".
    state: str = ""


class ViewStatusModel(BaseModel):
    views: list[ViewStateModel] = Field(default_factory=list)
    # The line of the story these are for: its name, and whether it is the main story.
    line: str = ""
    main_story: bool = True
    # How each view is brought up to date, in plain words.
    how_built: str = ""


# Why each view is rebuilt whole rather than changed in place (QRY-001).
HOW_BUILT = (
    "Each view is built again in full from the story's records after a change, a moment "
    "later, by the background worker. Reads use a view only once a check against the "
    "story's history has matched it and it is up to date; until then they work the answer "
    "out from the records, which is slower but always right."
)


def _state_words(state: Any) -> str:
    if state.version is None:
        return "Not built yet"
    if state.outcome == "differed":
        return "Differed when checked"
    if not state.trusted:
        return "Not checked yet"
    if state.behind:
        return "Catching up"
    return "Ready"


class ViewCheckModel(BaseModel):
    # How many views were checked, matched, differed, or could not be checked yet (built
    # by another version, or the story moved on while checking).
    checked: int
    matched: int
    differed: int
    skipped: int


def _answer(answer: Answer) -> ViewAnswerModel:
    return ViewAnswerModel(
        answered_from=answer.source,
        version=answer.version,
        latest_version=answer.latest,
        rows=answer.rows,
        cut=answer.cut,
    )


@router.get("/story-views")
async def story_views(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> ViewStatusModel:
    """How far each ready view of this line of the story is, and whether reads use it."""
    states = await service.status(scope, scope.workspace_id or "", project_id)
    line, main = await service.line_name(scope.workspace_id or "", project_id)
    now = datetime.now(UTC)
    return ViewStatusModel(
        line=line,
        main_story=main,
        how_built=HOW_BUILT,
        views=[
            ViewStateModel(
                view=state.view,
                version=state.version,
                latest_version=state.latest,
                behind=state.behind,
                rows=state.rows,
                answers_reads=state.answers,
                trusted=state.trusted,
                verified_at=state.verified_at,
                verified_version=state.verified_version,
                outcome=state.outcome,  # type: ignore[arg-type]
                differences=state.differences,
                built_at=state.built_at,
                lag_seconds=(
                    max(0.0, (now - state.built_at).total_seconds())
                    if state.built_at is not None and state.behind
                    else None
                ),
                state=_state_words(state),
            )
            for state in states
        ],
    )


@router.post("/story-views/check")
async def check_story_views(
    project_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> ViewCheckModel:
    """Check this line's ready views against the story's history now (owners and
    administrators). A view that matches is used by reads from then on; one that differs
    is built again and used only once it matches. Nothing in the story changes."""
    results = await service.check_now(scope, scope.workspace_id or "", project_id)
    return ViewCheckModel(
        checked=len(results),
        matched=sum(1 for item in results if item.matched),
        differed=sum(1 for item in results if not item.matched and item.skipped is None),
        skipped=sum(1 for item in results if item.skipped is not None),
    )


@router.get("/story-views/where")
async def where(
    project_id: str,
    entity: str = Query(min_length=1, max_length=200),
    at: float | None = Query(default=None, ge=-1e12, le=1e12),
    scope: AccessScope = Depends(get_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> ViewAnswerModel:
    """Where someone or something is at a moment of story time (or anywhere, without ``at``)."""
    return _answer(await service.where(scope, scope.workspace_id or "", project_id, entity, at))


@router.get("/story-views/holders")
async def holders(
    project_id: str,
    item: str | None = Query(default=None, max_length=200),
    holder: str | None = Query(default=None, max_length=200),
    at: float | None = Query(default=None, ge=-1e12, le=1e12),
    scope: AccessScope = Depends(get_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> ViewAnswerModel:
    """Who holds a thing, or what someone holds, at a moment of story time."""
    return _answer(
        await service.holders(
            scope, scope.workspace_id or "", project_id, item_id=item, holder_id=holder, at=at
        )
    )


@router.get("/story-views/relationships")
async def relationships(
    project_id: str,
    entity: str = Query(min_length=1, max_length=200),
    at: float | None = Query(default=None, ge=-1e12, le=1e12),
    scope: AccessScope = Depends(get_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> ViewAnswerModel:
    return _answer(
        await service.relationships(scope, scope.workspace_id or "", project_id, entity, at)
    )


@router.get("/story-views/dependents")
async def dependents(
    project_id: str,
    record: str = Query(min_length=3, max_length=220),
    depth: int = Query(default=MAX_DEPTH, ge=1, le=MAX_DEPTH),
    scope: AccessScope = Depends(get_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> DependentsModel:
    """What leans on a record (``entity:…``, ``fact:…``, ``event:…``, ``belief:…`` or
    ``plan_item:…``), nearest first."""
    answer, found, cut = await service.dependents(
        scope, scope.workspace_id or "", project_id, record, depth
    )
    return DependentsModel(
        answered_from=answer.source,
        version=answer.version,
        latest_version=answer.latest,
        record=record,
        dependents=[
            LeaningRecordModel(record=item.record, why=item.why, depth=item.depth, via=item.via)
            for item in found
        ],
        cut=cut or answer.cut,
    )


@router.get("/story-views/disclosures")
async def disclosures(
    project_id: str,
    fact: str = Query(min_length=1, max_length=200),
    scope: AccessScope = Depends(get_project_scope),
    service: ReadModelService = Depends(get_read_models),
) -> DisclosuresModel:
    """The scenes that show or hint at a fact, in reading order."""
    answer = await service.disclosures(scope, scope.workspace_id or "", project_id, fact)
    return DisclosuresModel(
        answered_from=answer.source,
        version=answer.version,
        latest_version=answer.latest,
        rows=answer.rows,
        cut=answer.cut,
        first_shown=answer.extra.get("first_shown"),
    )
