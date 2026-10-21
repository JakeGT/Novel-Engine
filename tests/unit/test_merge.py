"""Merging two entries (AST-EXP-017): one reviewed change, worked out when kept, undone in one."""

import pytest

from backend.domain.canon.events import EventParticipant
from backend.domain.canon.plans import PlanKind, PlanRef
from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.canon.registry import CORE_PREDICATES
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media import AssetLink, LinkRole
from backend.domain.merge import (
    MergeRecords,
    MergeRefused,
    expand,
    kept_entry,
    plan_merge,
    read_records,
)
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    CreatePlanItemOp,
    MergeEntitiesOp,
    RetireEntityOp,
    canonical_operation,
)
from backend.domain.validation import CanonValidationError
from backend.services.canon_history_service import CanonHistoryService
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository, _MemoryCanonReader

pytestmark = pytest.mark.unit

WS, PR = "ws", "pr"
SCOPE = AccessScope(actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR)


class _World:
    def __init__(self) -> None:
        self.journal = InMemoryCanonJournal()
        self.repo = InMemoryCanonRepository(journal=self.journal)
        self.mutation = MutationService(self.journal, self.repo, local_mode=True)
        self.history = CanonHistoryService(
            canon=self.repo, journal=self.journal, local_mode=True, mutation=self.mutation
        )
        self.head = 0

    async def commit(self, *operations) -> int:
        draft = await self.mutation.create_draft(
            SCOPE,
            workspace_id=WS,
            project_id=PR,
            operations=list(operations),
            expected_head=self.head,
        )
        result = await self.mutation.commit_author_change(
            SCOPE, draft.id, acknowledge_high_impact=True
        )
        self.head = result.revision
        return result.revision

    async def revert(self, from_revision: int) -> int:
        result = await self.history.commit_revert(
            SCOPE,
            WS,
            PR,
            from_revision=from_revision,
            expected_head=self.head,
            acknowledge_high_impact=True,
        )
        self.head = result.revision
        return result.revision

    def reader(self) -> _MemoryCanonReader:
        return _MemoryCanonReader(self.repo, WS, PR, "main")

    async def entity(self, entity_id: str):
        return await self.repo.get_entity(workspace_id=WS, project_id=PR, entity_id=entity_id)

    async def open_facts(self, subject_id: str | None = None):
        return await self.repo.list_facts(
            workspace_id=WS, project_id=PR, subject_id=subject_id, limit=100
        )


def _link(fact_id: str, subject: str, predicate: str, target: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=subject,
            predicate=predicate,
            object=EntityRefObject(entity_id=target, project_id=PR),
        ),
    )


async def _story(world: _World) -> None:
    await world.commit(
        CreateEntityOp(id="mara", type="Character", name="Mara", attributes={"eyes": "green"}),
        CreateEntityOp(
            id="mara_v",
            type="Character",
            name="Mara V.",
            aliases=["The Fox"],
            attributes={"eyes": "grey", "role": "pilot", "_tags": ["pilot"]},
        ),
        CreateEntityOp(id="tobin", type="Character", name="Tobin"),
        CreateEntityOp(id="harbor", type="Location", name="Lantern Harbor"),
        CreateEntityOp(id="saltmere", type="Location", name="Saltmere"),
        CreateEntityOp(id="guild", type="Faction", name="Harbor Guild"),
    )
    await world.commit(
        _link("f_mara_at", "mara", "LOCATED_AT", "harbor"),
        _link("f_v_at", "mara_v", "LOCATED_AT", "saltmere"),
        _link("f_v_guild", "mara_v", "MEMBER_OF", "guild"),
        _link("f_tobin_knows_v", "tobin", "KNOWS", "mara_v"),
        _link("f_tobin_knows_m", "tobin", "KNOWS", "mara"),
        _link("f_v_knows_m", "mara_v", "KNOWS", "mara"),
        AssertBeliefOp(
            id="b_tobin",
            holder_entity_id="tobin",
            source_ref="told",
            proposition=Proposition(
                subject_id="mara_v",
                predicate="MEMBER_OF",
                object=EntityRefObject(entity_id="guild", project_id=PR),
            ),
        ),
        CreateEventOp(
            id="ev_meet",
            kind="meeting",
            name="The meeting",
            participants=[
                EventParticipant(entity_id="mara_v", role="participant"),
                EventParticipant(entity_id="tobin", role="participant"),
            ],
            location_ids=["harbor"],
        ),
        CreatePlanItemOp(
            id="scene_1",
            kind=PlanKind.SCENE,
            title="At the harbor",
            refs=[
                PlanRef(entity_id="mara_v", role="point_of_view"),
                PlanRef(entity_id="mara", role="participant"),
            ],
        ),
    )


@pytest.mark.asyncio
async def test_a_merge_moves_everything_onto_the_kept_entry_and_retires_the_other() -> None:
    world = _World()
    await _story(world)
    records = await read_records(world.reader(), "mara", "mara_v")
    assert records is not None
    merge = plan_merge(records, CORE_PREDICATES)
    # Mara is at the harbor; holding one place at a time, Mara V.'s Saltmere is ended.
    assert merge.drop_facts == ["f_v_at"]

    revision = await world.commit(merge)

    mara = await world.entity("mara")
    assert mara.name == "Mara"
    assert mara.aliases == ["Mara V.", "The Fox"]
    # The kept entry's details win; the merged one's fill in what it lacked.
    assert mara.attributes["eyes"] == "green" and mara.attributes["role"] == "pilot"
    assert mara.attributes["_tags"] == ["pilot"]
    merged = await world.entity("mara_v")
    assert merged.lifecycle.value == "retired" and merged.retired_revision == revision

    facts = await world.open_facts()
    readings = sorted(
        (f.proposition.subject_id, f.proposition.predicate, f.proposition.object.entity_id)
        for f in facts
    )
    # Tobin knew both: one "knows Mara" remains; "Mara knows Mara" is never stated.
    assert readings == [
        ("mara", "LOCATED_AT", "harbor"),
        ("mara", "MEMBER_OF", "guild"),
        ("tobin", "KNOWS", "mara"),
    ]
    beliefs = await world.repo.list_beliefs(workspace_id=WS, project_id=PR, limit=10)
    assert [(b.holder_entity_id, b.proposition.subject_id) for b in beliefs] == [("tobin", "mara")]
    event = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_meet")
    assert [p.entity_id for p in event.participants] == ["mara", "tobin"]
    scene = await world.repo.get_plan_item(workspace_id=WS, project_id=PR, plan_id="scene_1")
    assert [(r.entity_id, r.role) for r in scene.refs] == [
        ("mara", "point_of_view"),
        ("mara", "participant"),
    ]
    # Nothing current names the merged entry any more.
    assert (
        await world.repo.count_active_references(workspace_id=WS, project_id=PR, entity_id="mara_v")
        == 0
    )


@pytest.mark.asyncio
async def test_undoing_a_merge_brings_everything_back_in_one_step() -> None:
    world = _World()
    await _story(world)
    records = await read_records(world.reader(), "mara", "mara_v")
    before_facts = {
        (f.proposition.subject_id, f.proposition.predicate, f.proposition.object.entity_id)
        for f in await world.open_facts()
    }
    merged_at = await world.commit(plan_merge(records, CORE_PREDICATES))

    preview = await world.history.propose_revert(SCOPE, WS, PR, from_revision=merged_at - 1)
    assert not preview.conflicts
    assert preview.operations[0] == {"op": "restore_entity", "id": "mara_v"}
    assert all(change.inverse_operation for change in preview.changes), preview.changes
    await world.revert(merged_at - 1)

    assert (await world.entity("mara_v")).lifecycle.value == "active"
    mara = await world.entity("mara")
    assert mara.aliases == [] and mara.attributes == {"eyes": "green"}
    after_facts = {
        (f.proposition.subject_id, f.proposition.predicate, f.proposition.object.entity_id)
        for f in await world.open_facts()
    }
    assert after_facts == before_facts
    event = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_meet")
    assert [p.entity_id for p in event.participants] == ["mara_v", "tobin"]
    scene = await world.repo.get_plan_item(workspace_id=WS, project_id=PR, plan_id="scene_1")
    assert {r.entity_id for r in scene.refs} == {"mara", "mara_v"}


@pytest.mark.asyncio
async def test_a_fact_added_after_the_merge_was_suggested_still_moves() -> None:
    world = _World()
    await _story(world)
    records = await read_records(world.reader(), "mara", "mara_v")
    merge = plan_merge(records, CORE_PREDICATES)
    await world.commit(
        AssertFactOp(
            id="f_late",
            proposition=Proposition(
                subject_id="mara_v", predicate="HAS_NAME", object=StringObject(value="Vee")
            ),
        )
    )
    await world.commit(merge)
    names = [f for f in await world.open_facts("mara") if f.proposition.predicate == "HAS_NAME"]
    assert [f.proposition.object.value for f in names] == ["Vee"]


@pytest.mark.asyncio
async def test_entries_of_different_types_or_the_same_entry_are_never_merged() -> None:
    world = _World()
    await _story(world)
    with pytest.raises(MergeRefused, match="same type"):
        plan_merge(await read_records(world.reader(), "mara", "harbor"), CORE_PREDICATES)
    records = await read_records(world.reader(), "mara", "mara_v")
    with pytest.raises(MergeRefused, match="itself"):
        expand(
            MergeEntitiesOp(id="mara", into="mara"),
            MergeRecords(keep=records.keep, merged=records.keep),
            ordinal=0,
        )


@pytest.mark.asyncio
async def test_an_entry_merged_twice_in_one_set_is_refused_in_plain_words() -> None:
    world = _World()
    await _story(world)
    with pytest.raises(CanonValidationError, match="merged only once"):
        await world.commit(
            MergeEntitiesOp(id="mara_v", into="mara"), MergeEntitiesOp(id="mara", into="tobin")
        )
    assert (await world.entity("mara_v")).is_active


@pytest.mark.asyncio
async def test_pictures_move_unless_the_kept_entry_already_shows_them() -> None:
    world = _World()
    await _story(world)
    records = await read_records(world.reader(), "mara", "mara_v")

    def link(link_id: str, target: str, asset: str, known_by: list[str] | None = None):
        return AssetLink(
            id=link_id,
            workspace_id=WS,
            project_id=PR,
            branch_id="main",
            asset_id=asset,
            role=LinkRole.DEPICTS,
            target_record_id=target,
            created_revision=1,
            known_by=known_by or [],
            visibility="secret" if known_by else "open",
        )

    with_links = MergeRecords(
        keep=records.keep,
        merged=records.merged,
        links=(
            link("l1", "mara", "ast_same"),
            link("l2", "mara_v", "ast_same"),
            link("l3", "mara_v", "ast_other"),
            link("l4", "harbor", "ast_map", known_by=["mara_v"]),
        ),
    )
    operations, preview = expand(MergeEntitiesOp(id="mara_v", into="mara"), with_links, ordinal=2)
    assert preview.links_ended == [("l2", "the kept entry already shows this picture")]
    assert preview.links_moved == ["l3", "l4"]
    added = [op for op in operations if op.op == "link_asset"]
    assert [(op.id, op.target_id, op.known_by) for op in added] == [
        ("tmp:merge-2-link-1", "mara", []),
        ("tmp:merge-2-link-2", "harbor", ["mara"]),
    ]
    assert isinstance(operations[-1], RetireEntityOp) and operations[-1].merged_into == "mara"


def test_new_fields_leave_every_earlier_change_record_as_it_was() -> None:
    assert canonical_operation(AmendEventOp(id="ev", name="Later")) == {
        "op": "amend_event",
        "id": "ev",
        "name": "Later",
        "world_position": None,
    }
    assert canonical_operation(RetireEntityOp(id="e")) == {"op": "retire_entity", "id": "e"}
    # An event made with no one in it still says so, as it always has.
    assert canonical_operation(CreateEventOp(id="ev", kind="k", name="n"))["participants"] == []
    # A merge that empties who took part is kept as an empty list, not as "unchanged".
    assert canonical_operation(AmendEventOp(id="ev", participants=[]))["participants"] == []


def test_the_kept_entrys_names_never_repeat() -> None:
    from backend.domain.canon.entities import CanonEntity

    def entity(entity_id: str, name: str, aliases: list[str]) -> CanonEntity:
        return CanonEntity(
            id=entity_id,
            workspace_id=WS,
            project_id=PR,
            branch_id="main",
            type="Character",
            name=name,
            schema_version_id="",
            created_revision=1,
            aliases=aliases,
        )

    name, aliases, _ = kept_entry(
        entity("a", "Mara", ["the fox"]),
        entity("b", "Mara Venn", ["The Fox", "Mara"]),
        name="Mara Venn",
    )
    assert name == "Mara Venn"
    assert aliases == ["Mara", "the fox"]


@pytest.mark.asyncio
async def test_a_change_in_the_same_set_naming_the_merged_entry_is_refused() -> None:
    world = _World()
    await _story(world)
    with pytest.raises(CanonValidationError, match="names the entry being merged away"):
        await world.commit(
            _link("f_new", "mara_v", "KNOWS", "tobin"), MergeEntitiesOp(id="mara_v", into="mara")
        )


@pytest.mark.asyncio
async def test_two_merges_into_the_same_entry_in_one_set_are_refused() -> None:
    world = _World()
    await world.commit(
        CreateEntityOp(id="bea", type="Character", name="Bea"),
        CreateEntityOp(id="abby", type="Character", name="Abby", attributes={"eyes": "blue"}),
        CreateEntityOp(id="cee", type="Character", name="Cee", attributes={"hair": "red"}),
    )
    # Each would set Bea's other names and details over the other's.
    with pytest.raises(CanonValidationError, match="only one entry can be merged into the same"):
        await world.commit(
            MergeEntitiesOp(id="abby", into="bea"), MergeEntitiesOp(id="cee", into="bea")
        )
    assert (await world.entity("abby")).is_active and (await world.entity("cee")).is_active
    assert (await world.entity("bea")).aliases == []


@pytest.mark.asyncio
async def test_a_fact_set_to_drop_moves_when_the_kept_entry_no_longer_has_that_kind() -> None:
    world = _World()
    await _story(world)
    records = await read_records(world.reader(), "mara", "mara_v")
    merge = plan_merge(records, CORE_PREDICATES)
    assert merge.drop_facts == ["f_v_at"]
    # Mara leaves the harbour after the merge was suggested: Saltmere is no longer
    # in the way, so Mara V.'s place moves rather than leaving Mara nowhere.
    await world.commit(CloseFactOp(id="f_mara_at"))
    await world.commit(merge)
    places = [
        f.proposition.object.entity_id
        for f in await world.open_facts("mara")
        if f.proposition.predicate == "LOCATED_AT"
    ]
    assert places == ["saltmere"]


@pytest.mark.asyncio
async def test_events_caused_by_or_resulting_in_the_merged_entry_name_the_kept_one() -> None:
    world = _World()
    await world.commit(
        CreateEntityOp(id="bea", type="Character", name="Bea"),
        CreateEntityOp(id="abby", type="Character", name="Abby"),
        CreateEntityOp(id="tobin", type="Character", name="Tobin"),
    )
    await world.commit(
        CreateEventOp(id="ev_fire", kind="k", name="The fire", cause_ids=["abby"]),
        CreateEventOp(id="ev_flight", kind="k", name="The flight", outcome_ids=["abby", "bea"]),
    )
    merged_at = await world.commit(MergeEntitiesOp(id="abby", into="bea"))

    fire = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_fire")
    flight = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_flight")
    assert fire.cause_ids == ["bea"]
    assert flight.outcome_ids == ["bea"]
    assert (
        await world.repo.count_active_references(workspace_id=WS, project_id=PR, entity_id="abby")
        == 0
    )

    await world.revert(merged_at - 1)
    fire = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_fire")
    flight = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_flight")
    assert fire.cause_ids == ["abby"]
    assert flight.outcome_ids == ["abby", "bea"]


@pytest.mark.asyncio
async def test_a_merge_brought_back_after_the_entry_was_named_again_is_refused() -> None:
    world = _World()
    await _story(world)
    records = await read_records(world.reader(), "mara", "mara_v")
    # The changes the merge makes, as a what-if records them once it is kept there.
    changes, _preview = expand(plan_merge(records, CORE_PREDICATES), records, ordinal=0)
    # Meanwhile the main story names Mara V. again.
    await world.commit(_link("f_later", "tobin", "RELATED_TO", "mara_v"))
    with pytest.raises(CanonValidationError, match="1 record\\(s\\) still name the entry"):
        await world.commit(*changes)
    assert (await world.entity("mara_v")).is_active
    # On its own, a retirement as a merge that moves nothing is refused the same way.
    with pytest.raises(CanonValidationError, match="still name the entry being merged away"):
        await world.commit(RetireEntityOp(id="mara_v", merged_into="mara"))


def test_the_kept_entry_keeps_a_trust_mark_and_says_when_names_are_left_out() -> None:
    from backend.domain.canon.entities import CanonEntity
    from backend.domain.canon.marks import TRUST_KEY
    from backend.domain.merge import MAX_ALIASES

    def entity(entity_id: str, name: str, aliases: list[str], **attributes) -> CanonEntity:
        return CanonEntity(
            id=entity_id,
            workspace_id=WS,
            project_id=PR,
            branch_id="main",
            type="Character",
            name=name,
            schema_version_id="",
            created_revision=1,
            aliases=aliases,
            attributes=attributes,
        )

    keep = entity("a", "Mara", [f"Name {n}" for n in range(30)])
    merged = entity("b", "Mara V.", [f"Other {n}" for n in range(14)], **{TRUST_KEY: "doubtful"})
    # The kept entry has no trust mark: the merged one's stands.
    _name, _aliases, attributes = kept_entry(keep, merged)
    assert attributes[TRUST_KEY] == "doubtful"
    trusted = keep.model_copy(update={"attributes": {TRUST_KEY: "sure"}})
    assert kept_entry(trusted, merged)[2][TRUST_KEY] == "sure"

    _ops, preview = expand(
        MergeEntitiesOp(id="b", into="a"), MergeRecords(keep=keep, merged=merged), ordinal=0
    )
    assert len(preview.aliases) == MAX_ALIASES
    assert preview.aliases_cut == 30 + 1 + 14 - MAX_ALIASES
