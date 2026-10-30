"""The tools another assistant can call through the MCP endpoint (QRY-003, ADR-024).

Every tool is a thin call into the same application services the web app uses, and
its answer is built by the same response models as the HTTP API, so an assistant and
the app always see the same thing. No tool reads storage directly.

Who is calling comes only from the access key, checked on this request: the key's
workspace, the story it is for (or every story its holder can read there), and the
holder's role right now. A ``story`` argument can only narrow that, never widen it.
A ``what_if`` argument reads in one of that story's what-ifs.

One tool writes, and only when the key may suggest changes and its holder may still
change the story: ``suggest_changes`` puts one proposal in the review queue. It
never commits, approves, or touches rules, review settings or who may do what;
there is no tool for any of those.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from backend.api.v1 import canon_history as history_api
from backend.api.v1 import canon_mutations as mutations_api
from backend.api.v1 import story_time as time_api
from backend.api.v1.canon_assertions import _belief_model, _fact_model
from backend.api.v1.canon_entities import CanonEntityModel
from backend.api.v1.plan import OpenItemModel
from backend.api.v1.plan import _model as plan_item_model
from backend.domain.branching import working_in
from backend.domain.canon.assertions import Belief, Fact
from backend.domain.drafting import word_count
from backend.domain.errors import ForbiddenError, NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationStatus
from backend.domain.operations import CanonOperation
from backend.domain.tenancy import Permission
from backend.services.access_key_service import MachineCaller

# A suggestion is a reviewable proposal; keep it to something a person can review.
MAX_SUGGESTED_CHANGES = 50
MAX_FACTS = 200


@dataclass
class ToolCall:
    container: Any
    caller: MachineCaller
    arguments: BaseModel
    correlation_id: str = ""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _StoryArgs(_Args):
    story: str | None = Field(
        default=None,
        max_length=200,
        description=("The story's id, from list_stories. Leave out when the key is for one story."),
    )
    what_if: str | None = Field(
        default=None,
        max_length=200,
        description="Read in one of the story's what-ifs instead of the main story.",
    )


class NoArgs(_Args):
    pass


class SearchArgs(_StoryArgs):
    query: str = Field(min_length=1, max_length=200, description="A name or part of one.")
    type: str | None = Field(
        default=None, max_length=60, description="Only this kind, such as Character or Place."
    )
    limit: int = Field(default=20, ge=1, le=50)


class EntryArgs(_StoryArgs):
    entry_id: str = Field(min_length=1, max_length=200)
    as_known_by: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "A character's entry id: answer only with what that character knows or believes. "
            "Secrets they do not know and facts nothing says they know are left out. This is "
            "what they know now, as the story stands: it cannot be read as of a moment."
        ),
    )


class StoryArgs(_StoryArgs):
    pass


class SceneArgs(_StoryArgs):
    scene_id: str = Field(min_length=1, max_length=200, description="A scene's id in the plan.")


class TimelineArgs(_StoryArgs):
    at: float | None = Field(
        default=None,
        ge=-1e12,
        le=1e12,
        description="A moment in story time: what is true then, and what has happened by then.",
    )


class HistoryArgs(_StoryArgs):
    record_type: Literal["entity", "fact", "belief", "event", "asset_link"]
    record_id: str = Field(min_length=1, max_length=200)


class SuggestArgs(_StoryArgs):
    changes: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=MAX_SUGGESTED_CHANGES,
        description=(
            "The changes, each one operation in Novel Engine's change format (the same as "
            "the review screen shows), for example "
            '{"op": "create_entity", "type": "Character", "name": "Ada"}.'
        ),
    )
    note: str = Field(
        default="",
        max_length=500,
        description="Why you suggest this, in a sentence. Shown to the author in review.",
    )


_OPERATIONS: TypeAdapter[list[CanonOperation]] = TypeAdapter(list[CanonOperation])


# ---------------------------------------------------------------------- scope


def _local(call: ToolCall) -> bool:
    return not call.container.settings.is_hosted


async def _story(call: ToolCall, permission: Permission = Permission.PROJECT_READ) -> AccessScope:
    """The story this call reads, proven against the key and the holder's access now."""
    key = call.caller.key
    asked = getattr(call.arguments, "story", None) or None
    settings = call.container.settings
    if key.project_id is not None:
        if asked is not None and asked != key.project_id:
            raise NotFoundError("That story was not found.")
        project_id = key.project_id
    elif asked is not None:
        project_id = asked
    elif _local(call):
        project_id = settings.AUTH_LOCAL_PROJECT_ID
    else:
        raise ValidationError("Say which story: list_stories gives their ids.")
    scope = call.caller.scope(project_id, call.correlation_id)
    if _local(call):
        if project_id != settings.AUTH_LOCAL_PROJECT_ID:
            raise NotFoundError("That story was not found.")
    else:
        await call.container.tenancy_service.require_project(
            scope, key.workspace_id, project_id, permission
        )
    return scope


@asynccontextmanager
async def _line(call: ToolCall, scope: AccessScope) -> AsyncIterator[None]:
    """Read in the what-if asked for, once it is proven to be this story's."""
    branch_id = getattr(call.arguments, "what_if", None) or None
    if branch_id is None:
        with working_in(None):
            yield
        return
    branch = await call.container.journal.get_branch(
        workspace_id=scope.workspace_id or "",
        project_id=scope.project_id or "",
        branch_id=branch_id,
    )
    if branch is None:
        raise NotFoundError("That what-if was not found.")
    with working_in(None if branch.is_root else branch.id):
        yield


def _dump(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(model, BaseModel):
        return model.model_dump(mode="json")
    return TypeAdapter(dict[str, Any]).dump_python(model, mode="json")


# ---------------------------------------------------------------------- tools


async def list_stories(call: ToolCall) -> dict[str, Any]:
    key = call.caller.key
    settings = call.container.settings
    if _local(call):
        return {"stories": [{"id": settings.AUTH_LOCAL_PROJECT_ID, "name": "Your story"}]}
    scope = call.caller.scope(None, call.correlation_id)
    projects = await call.container.tenancy_service.list_projects(scope, key.workspace_id)
    if key.project_id is not None:
        projects = [project for project in projects if project.id == key.project_id]
    return {"stories": [{"id": project.id, "name": project.name} for project in projects]}


async def search_entries(call: ToolCall) -> dict[str, Any]:
    args = call.arguments
    assert isinstance(args, SearchArgs)
    scope = await _story(call)
    async with _line(call, scope):
        found = await call.container.canon_entity_service.search(
            scope,
            scope.workspace_id or "",
            scope.project_id or "",
            args.query,
            entity_type=args.type,
            include_retired=False,
            limit=args.limit,
        )
    return {
        "story": scope.project_id,
        "entries": [_dump(CanonEntityModel.model_validate(entity)) for entity in found],
    }


def _mentions(record: Fact | Belief, entity_id: str) -> bool:
    proposition = record.proposition
    target = getattr(proposition.object, "entity_id", None)
    return proposition.subject_id == entity_id or target == entity_id


async def read_entry(call: ToolCall) -> dict[str, Any]:
    args = call.arguments
    assert isinstance(args, EntryArgs)
    scope = await _story(call)
    workspace_id, project_id = scope.workspace_id or "", scope.project_id or ""
    container = call.container
    async with _line(call, scope):
        entity = await container.canon_entity_service.get(
            scope, workspace_id, project_id, args.entry_id
        )
        entry = _dump(CanonEntityModel.model_validate(entity))
        if args.as_known_by is None:
            facts = await container.canon_assertion_service.list_facts(
                scope, workspace_id, project_id, subject_id=entity.id, limit=MAX_FACTS
            )
            about = await container.canon_assertion_service.list_facts(
                scope, workspace_id, project_id, object_entity_id=entity.id, limit=MAX_FACTS
            )
            seen = {fact.id for fact in facts}
            facts = [*facts, *(fact for fact in about if fact.id not in seen)]
            beliefs = (
                await container.canon_assertion_service.list_beliefs(
                    scope, workspace_id, project_id, holder_entity_id=entity.id, limit=MAX_FACTS
                )
                if entity.type == "Character"
                else []
            )
            return {
                "story": project_id,
                "seen_as": "the author: everything recorded",
                "entry": entry,
                "facts": [_dump(_fact_model(fact)) for fact in facts],
                "beliefs": [_dump(_belief_model(belief)) for belief in beliefs],
            }
        # A character's point of view: the same reading as "What they know" in the app.
        known = await container.knowledge_service.character(
            scope, workspace_id, project_id, args.as_known_by
        )
        knows = [(fact, how) for fact, how in known.knows if _mentions(fact, entity.id)]
        believed = [belief for belief, _ in known.wrong] + list(known.unconfirmed)
        believed = [belief for belief in believed if _mentions(belief, entity.id)]
    # Only what names the entry. Details, other names (an alias can give away a secret
    # identity), outside ids and the slug are the author's: nothing says the character
    # knows them.
    named = {"id": entry["id"], "type": entry["type"], "name": entry["name"]}
    return {
        "story": project_id,
        "seen_as": f"what {args.as_known_by} knows or believes",
        "entry": named,
        "facts": [{**_dump(_fact_model(fact)), "known_because": how} for fact, how in knows],
        # What they believe, true or not: telling which would say what they do not know.
        "beliefs": [_dump(_belief_model(belief)) for belief in believed],
        "left_out": (
            "The entry's details and other names, secrets this character does not know, and "
            "facts nothing says they know. A point of view reads the story as it stands now: "
            "there is no reading it as of an earlier moment."
        ),
    }


async def list_plan(call: ToolCall) -> dict[str, Any]:
    scope = await _story(call)
    async with _line(call, scope):
        reading = await call.container.plan_service.outline(
            scope, scope.workspace_id or "", scope.project_id or ""
        )
    return {
        "story": scope.project_id,
        "items": [_dump(plan_item_model(item)) for item in reading.items],
        "open": [
            _dump(
                OpenItemModel(
                    plan_id=item.plan_id, code=item.code, message=item.message, kind=item.kind
                )
            )
            for item in reading.open
        ],
    }


async def read_scene(call: ToolCall) -> dict[str, Any]:
    args = call.arguments
    assert isinstance(args, SceneArgs)
    scope = await _story(call)
    async with _line(call, scope):
        state = await call.container.studio_service.state(
            scope, scope.workspace_id or "", scope.project_id or "", args.scene_id
        )
    current = state.current
    return {
        "story": scope.project_id,
        "scene": _dump(plan_item_model(state.scene)),
        "text": None
        if current is None
        else {
            "version_id": current.id,
            "version": current.number,
            "words": word_count(current.content),
            "content": current.content,
        },
    }


async def timeline(call: ToolCall) -> dict[str, Any]:
    args = call.arguments
    assert isinstance(args, TimelineArgs)
    scope = await _story(call)
    service = call.container.story_time_service
    async with _line(call, scope):
        if args.at is None:
            answer: BaseModel = await time_api.get_timeline(
                scope.project_id or "", scope=scope, service=service
            )
        else:
            answer = await time_api.get_moment(
                scope.project_id or "", at=args.at, scope=scope, service=service
            )
    return {"story": scope.project_id, **_dump(answer)}


async def record_history(call: ToolCall) -> dict[str, Any]:
    args = call.arguments
    assert isinstance(args, HistoryArgs)
    scope = await _story(call)
    history = call.container.canon_history_service
    async with _line(call, scope):
        provenance = await history_api.record_provenance(
            scope.project_id or "", args.record_type, args.record_id, scope=scope, history=history
        )
    return {
        "story": scope.project_id,
        "record_type": args.record_type,
        "record_id": args.record_id,
        "changes": provenance["items"],
    }


async def open_reviews(call: ToolCall) -> dict[str, Any]:
    scope = await _story(call)
    async with _line(call, scope):
        waiting = await mutations_api.list_mutations(
            scope.project_id or "",
            status=MutationStatus.NEEDS_REVIEW,
            limit=50,
            offset=0,
            scope=scope,
            service=call.container.mutation_service,
        )
    return {"story": scope.project_id, "waiting": _dump(waiting)["items"]}


async def suggest_changes(call: ToolCall) -> dict[str, Any]:
    args = call.arguments
    assert isinstance(args, SuggestArgs)
    if not call.caller.can_suggest:
        raise ForbiddenError("This key may only read.")
    try:
        operations = _OPERATIONS.validate_python(args.changes)
    except PydanticValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        raise ValidationError(f"A change is not in the right form ({where}).") from None
    scope = await _story(call, Permission.PROJECT_WRITE)
    container = call.container
    mutations = container.mutation_service
    workspace_id, project_id = scope.workspace_id or "", scope.project_id or ""
    async with _line(call, scope):
        branch = await container.journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        head = await container.canon_repository.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        proposal = await mutations.suggest(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=head,
        )
        # The key's id (never the key) so each suggestion can be traced to the key that
        # made it: the runbook says how to list one key's suggestions.
        reason = f"suggested by a connected assistant (key {call.caller.key.id})"
        if args.note.strip():
            reason = f"{reason}: {args.note.strip()}"
        proposal = await mutations.submit(scope, proposal.id, reason)
    return {
        "story": project_id,
        "proposal_id": proposal.id,
        "status": proposal.status.value,
        "changes": len(operations),
        "message": (
            "Suggested. Nothing in the story changed: the author will see this in Review "
            "and decide."
        ),
    }


# ---------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    arguments: type[BaseModel]
    run: Callable[[ToolCall], Awaitable[dict[str, Any]]]
    writes: bool = False

    def definition(self) -> dict[str, Any]:
        schema = self.arguments.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": not self.writes,
                "destructiveHint": False,
                "idempotentHint": not self.writes,
                "openWorldHint": False,
            },
        }


READ_TOOLS: tuple[Tool, ...] = (
    Tool(
        "list_stories",
        "List stories",
        "The stories this key can read, with their ids.",
        NoArgs,
        list_stories,
    ),
    Tool(
        "search_entries",
        "Search the story bible",
        "Find people, places and things in a story's bible by name.",
        SearchArgs,
        search_entries,
    ),
    Tool(
        "read_entry",
        "Read an entry",
        "One entry with its facts and, for a character, what they believe. Give as_known_by "
        "to see it only as one character knows it now: a point of view has no 'as of' moment, "
        "so it cannot show what they knew earlier in story time.",
        EntryArgs,
        read_entry,
    ),
    Tool(
        "list_plan",
        "Read the plan",
        "The story's plan: its parts, scenes and threads in order, and what is still open.",
        StoryArgs,
        list_plan,
    ),
    Tool(
        "read_scene",
        "Read a scene",
        "A scene's plan card and its current text.",
        SceneArgs,
        read_scene,
    ),
    Tool(
        "timeline",
        "Read the timeline",
        "Events in story time and when facts hold. Give at for what is true at one moment.",
        TimelineArgs,
        timeline,
    ),
    Tool(
        "record_history",
        "Read a record's history",
        "Every change to one entry, fact, belief or event: when, where it came from, and "
        "who reviewed it.",
        HistoryArgs,
        record_history,
    ),
    Tool(
        "open_reviews",
        "List suggestions waiting",
        "Suggested changes waiting for the author's review.",
        StoryArgs,
        open_reviews,
    ),
)

SUGGEST_TOOL = Tool(
    "suggest_changes",
    "Suggest changes",
    "Suggest changes to the story. They go to the author's review queue and change "
    "nothing until the author keeps them.",
    SuggestArgs,
    suggest_changes,
    writes=True,
)

ALL_TOOLS: dict[str, Tool] = {tool.name: tool for tool in (*READ_TOOLS, SUGGEST_TOOL)}


def tools_for(caller: MachineCaller) -> list[Tool]:
    """The tools this caller may use, in a fixed order: suggesting only when allowed."""
    tools = list(READ_TOOLS)
    if caller.can_suggest:
        tools.append(SUGGEST_TOOL)
    return tools


__all__ = [
    "ALL_TOOLS",
    "MAX_SUGGESTED_CHANGES",
    "READ_TOOLS",
    "SUGGEST_TOOL",
    "Tool",
    "ToolCall",
    "tools_for",
]
