"""The canonical reference world ("The Lantern Coast"), seeded through the commit path.

Unlike the Phase 0 graph manifest, this seed is *canon*: every record is a typed
operation committed by the mutation service into the PostgreSQL journal, so the
seed exercises exactly the path product writes use. It contains, on purpose:

* objective truths with scalar and entity-reference objects,
* an author-only secret truth,
* a false belief, an unknown belief, and a true belief,
* an event with a fuzzy world position and one with no known date,
* a close-and-reassert fact history and a retired entity,
* a media asset with an immutable version and an explicit ``MAP_OF`` link, and
* a second tenant whose project reuses the *same* entity/fact ids and names with
  different truths, so isolation tests are meaningful.

``VALID_PROPOSALS`` and ``INVALID_PROPOSALS`` are labelled fixtures: each invalid
proposal names the integrity finding the authoritative validator must raise.
Everything is deterministic and re-applying the seed is a no-op.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.domain.canon.assertions import TruthAgreement, truth_agreement
from backend.domain.canon.events import EventParticipant
from backend.domain.canon.propositions import (
    BooleanObject,
    EntityRefObject,
    IntegerObject,
    Proposition,
    PropositionObject,
    StringObject,
)
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import HeadConflict
from backend.domain.media import MediaAsset, MediaAssetVersion
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    EvidenceRef,
    LinkAssetOp,
    RetireEntityOp,
    UpdateEntityOp,
    canonicalize_operations,
)
from backend.ports import (
    CanonJournalPort,
    CanonRepositoryPort,
    MediaStorePort,
    ObjectStorePort,
    SchemaVersionStorePort,
)
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.tenancy import USER_EDITOR, USER_OWNER, WS_ALT
from backend.services.mutation_service import MutationService

CANON_SEED_VERSION = "2026-10-09.1"
ALT_PROJECT_ID = "prj_seed_alt"
FIXED_TIMESTAMP = datetime(2024, 1, 1, tzinfo=UTC)

MARA = "ent_seed_mara"
IVEN = "ent_seed_iven"
HARBOR = "ent_seed_harbor"
SALTKEEP = "ent_seed_saltkeep"
LIGHTHOUSE = "ent_seed_lighthouse"
COMPASS = "ent_seed_compass"
LEDGER = "ent_seed_ledger"
GUILD = "ent_seed_guild"
SECRET = "ent_seed_secret"

CHART_ASSET = "ast_seed_harbor_chart"
CHART_VERSION = "asv_seed_harbor_chart_v1"
CHART_LINK = "alk_seed_harbor_chart"
# A tiny original SVG: no third-party or copyrighted material is ever seeded.
CHART_BYTES = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20">'
    b'<path d="M2 14 Q12 4 22 12 T38 8" fill="none" stroke="black"/>'
    b"<title>Lantern Harbor chart</title></svg>"
)


@dataclass(frozen=True)
class SeedStep:
    label: str
    operations: tuple[CanonOperation, ...]
    # Destructive steps carry the explicit acknowledgement the review gate requires.
    acknowledge_high_impact: bool = False


@dataclass(frozen=True)
class SeedProject:
    workspace_id: str
    project_id: str
    actor_user_id: str
    steps: tuple[SeedStep, ...]


@dataclass(frozen=True)
class LabeledProposal:
    label: str
    operations: tuple[CanonOperation, ...]
    # ``None`` for a valid proposal, otherwise the finding code that must block it.
    expected_code: str | None = None


def _ref(entity_id: str, project_id: str) -> EntityRefObject:
    return EntityRefObject(entity_id=entity_id, project_id=project_id)


def _fact(
    fact_id: str,
    subject: str,
    predicate: str,
    obj: PropositionObject,
    *,
    evidence: str | None = None,
    **extra: Any,
) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(subject_id=subject, predicate=predicate, object=obj),
        evidence=[EvidenceRef(source_artefact_id=evidence)] if evidence else [],
        **extra,
    )


def _belief(
    belief_id: str, holder: str, subject: str, predicate: str, obj: PropositionObject, source: str
) -> AssertBeliefOp:
    return AssertBeliefOp(
        id=belief_id,
        holder_entity_id=holder,
        proposition=Proposition(subject_id=subject, predicate=predicate, object=obj),
        source_ref=source,
        evidence=[EvidenceRef(source_artefact_id=source)],
    )


def _primary_steps(project_id: str) -> tuple[SeedStep, ...]:
    return (
        SeedStep(
            "world",
            (
                CreateEntityOp(
                    id=MARA,
                    type="Character",
                    name="Mara Venn",
                    slug="mara-venn",
                    aliases=["The Fox"],
                    external_ids={"seed": "mara"},
                ),
                CreateEntityOp(id=IVEN, type="Character", name="Iven Marsh", slug="iven-marsh"),
                CreateEntityOp(id=HARBOR, type="Location", name="Lantern Harbor"),
                CreateEntityOp(id=SALTKEEP, type="Location", name="Saltkeep"),
                CreateEntityOp(id=LIGHTHOUSE, type="Location", name="Old Lighthouse"),
                CreateEntityOp(id=COMPASS, type="Object", name="Tide Compass"),
                CreateEntityOp(id=LEDGER, type="Object", name="Dawn Runner's Ledger"),
                CreateEntityOp(id=GUILD, type="Faction", name="Salt Guild"),
                CreateEntityOp(
                    id=SECRET,
                    type="Secret",
                    name="Who took the ledger",
                    attributes={"visibility": "author_only"},
                ),
            ),
        ),
        SeedStep(
            "truths",
            (
                _fact("fct_seed_mara_at", MARA, "LOCATED_AT", _ref(HARBOR, project_id)),
                _fact("fct_seed_mara_guild", MARA, "MEMBER_OF", _ref(GUILD, project_id)),
                _fact("fct_seed_mara_compass", MARA, "OWNS", _ref(COMPASS, project_id)),
                _fact("fct_seed_mara_knows_iven", MARA, "KNOWS", _ref(IVEN, project_id)),
                _fact("fct_seed_harbor_pop", HARBOR, "HAS_POPULATION", IntegerObject(value=4200)),
                _fact("fct_seed_compass_color", COMPASS, "HAS_COLOR", StringObject(value="brass")),
                _fact(
                    "fct_seed_saltkeep_haunted", SALTKEEP, "IS_HAUNTED", BooleanObject(value=False)
                ),
                # The secret: an objective truth no point-of-view character knows.
                _fact(
                    "fct_seed_iven_ledger",
                    IVEN,
                    "OWNS",
                    _ref(LEDGER, project_id),
                    evidence="seed:outline.secret-1",
                ),
            ),
        ),
        SeedStep(
            "beliefs",
            (
                # False: canon says Saltkeep is not haunted.
                _belief(
                    "blf_seed_mara_haunted",
                    MARA,
                    SALTKEEP,
                    "IS_HAUNTED",
                    BooleanObject(value=True),
                    "seed:ch01.s02",
                ),
                # Unknown: no accepted fact covers Saltkeep's population.
                _belief(
                    "blf_seed_iven_pop",
                    IVEN,
                    SALTKEEP,
                    "HAS_POPULATION",
                    IntegerObject(value=12),
                    "seed:ch01.s03",
                ),
                # True: agrees with the accepted fact.
                _belief(
                    "blf_seed_iven_compass",
                    IVEN,
                    MARA,
                    "OWNS",
                    _ref(COMPASS, project_id),
                    "seed:ch01.s03",
                ),
            ),
        ),
        SeedStep(
            "events",
            (
                CreateEventOp(
                    id="evt_seed_wreck",
                    kind="shipwreck",
                    name="Wreck of the Dawn Runner",
                    participants=[
                        EventParticipant(entity_id=IVEN, role="culprit"),
                        EventParticipant(entity_id=MARA, role="witness"),
                    ],
                    location_ids=[HARBOR],
                    # Fuzzy on purpose: stored verbatim, never turned into a timestamp.
                    world_position="three winters before the story opens",
                ),
                CreateEventOp(
                    id="evt_seed_inquiry",
                    kind="inquiry",
                    name="Guild Inquiry",
                    participants=[EventParticipant(entity_id=MARA, role="witness")],
                    cause_ids=["evt_seed_wreck"],
                    # Unknown date: left empty rather than invented.
                ),
            ),
        ),
        SeedStep(
            "revision",
            (
                CloseFactOp(id="fct_seed_compass_color"),
                _fact(
                    "fct_seed_compass_color_2",
                    COMPASS,
                    "HAS_COLOR",
                    StringObject(value="verdigris"),
                ),
                UpdateEntityOp(id=MARA, attributes={"role": "harbor pilot"}),
            ),
        ),
        SeedStep("retirement", (RetireEntityOp(id=LIGHTHOUSE),), acknowledge_high_impact=True),
        SeedStep(
            "media",
            (
                LinkAssetOp(
                    id=CHART_LINK,
                    asset_id=CHART_ASSET,
                    asset_version_id=CHART_VERSION,
                    target_id=HARBOR,
                    role="MAP_OF",
                ),
            ),
        ),
    )


def _alt_steps(project_id: str) -> tuple[SeedStep, ...]:
    """Another tenant's project: identical ids and names, different truth."""
    return (
        SeedStep(
            "world",
            (
                CreateEntityOp(id=MARA, type="Character", name="Mara Venn", slug="mara-venn"),
                CreateEntityOp(id=HARBOR, type="Location", name="Lantern Harbor"),
                CreateEntityOp(id=SALTKEEP, type="Location", name="Saltkeep"),
            ),
        ),
        SeedStep(
            "truths",
            (
                _fact("fct_seed_mara_at", MARA, "LOCATED_AT", _ref(SALTKEEP, project_id)),
                _fact("fct_seed_harbor_pop", HARBOR, "HAS_POPULATION", IntegerObject(value=90)),
            ),
        ),
    )


SEED_PROJECTS: tuple[SeedProject, ...] = (
    SeedProject(SEED_WORKSPACE_ID, SEED_PROJECT_ID, USER_OWNER, _primary_steps(SEED_PROJECT_ID)),
    SeedProject(WS_ALT, ALT_PROJECT_ID, USER_EDITOR, _alt_steps(ALT_PROJECT_ID)),
)

PRIMARY_HEAD = len(SEED_PROJECTS[0].steps)

# Expected belief truth agreement in the primary project (a derived query result).
EXPECTED_TRUTH_AGREEMENT: dict[str, TruthAgreement] = {
    "blf_seed_mara_haunted": TruthAgreement.DISAGREES,
    "blf_seed_iven_pop": TruthAgreement.UNKNOWN,
    "blf_seed_iven_compass": TruthAgreement.AGREES,
}

_P = SEED_PROJECT_ID

VALID_PROPOSALS: tuple[LabeledProposal, ...] = (
    LabeledProposal(
        "scalar_fact",
        (_fact("fct_new_ledger_color", LEDGER, "HAS_COLOR", StringObject(value="oxblood")),),
    ),
    LabeledProposal(
        "new_entity_with_dependent_fact",
        (
            CreateEntityOp(id="tmp:cove", type="Location", name="Gull Cove"),
            _fact("tmp:iven_at", IVEN, "LOCATED_AT", _ref("tmp:cove", _P)),
        ),
    ),
    LabeledProposal(
        "second_false_belief",
        (
            _belief(
                "blf_new_iven_haunted",
                IVEN,
                SALTKEEP,
                "IS_HAUNTED",
                BooleanObject(value=True),
                "seed:ch02.s01",
            ),
        ),
    ),
    LabeledProposal(
        "close_and_reassert_in_one_set",
        (
            CloseFactOp(id="fct_seed_mara_at"),
            _fact("fct_new_mara_at", MARA, "LOCATED_AT", _ref(SALTKEEP, _P)),
        ),
    ),
)

INVALID_PROPOSALS: tuple[LabeledProposal, ...] = (
    LabeledProposal(
        "unknown_entity_type",
        (CreateEntityOp(id="ent_x", type="Spaceship", name="Nope"),),
        "unknown_type",
    ),
    LabeledProposal(
        "unknown_predicate",
        (_fact("fct_x", MARA, "ADORES", _ref(IVEN, _P)),),
        "unknown_predicate",
    ),
    LabeledProposal(
        "wrong_subject_type",
        (_fact("fct_x", HARBOR, "OWNS", _ref(COMPASS, _P)),),
        "invalid_proposition",
    ),
    LabeledProposal(
        "wrong_object_shape",
        (_fact("fct_x", SALTKEEP, "HAS_POPULATION", StringObject(value="many")),),
        "invalid_proposition",
    ),
    LabeledProposal(
        "wrong_target_type",
        (_fact("fct_x", IVEN, "LOCATED_AT", _ref(COMPASS, _P)),),
        "invalid_proposition",
    ),
    LabeledProposal(
        "cardinality_conflict",
        (_fact("fct_x", MARA, "LOCATED_AT", _ref(SALTKEEP, _P)),),
        "cardinality_conflict",
    ),
    LabeledProposal(
        "reference_to_retired_entity",
        (_fact("fct_x", LIGHTHOUSE, "HAS_NAME", StringObject(value="The Old Light")),),
        "retired_reference",
    ),
    LabeledProposal(
        "cross_project_reference",
        (_fact("fct_x", IVEN, "LOCATED_AT", _ref(SALTKEEP, ALT_PROJECT_ID)),),
        "cross_project_reference",
    ),
    LabeledProposal(
        "belief_held_by_a_location",
        (
            _belief(
                "blf_x", HARBOR, SALTKEEP, "IS_HAUNTED", BooleanObject(value=True), "seed:ch02.s01"
            ),
        ),
        "invalid_holder",
    ),
    LabeledProposal(
        "belief_without_source",
        (
            AssertBeliefOp(
                id="blf_x",
                holder_entity_id=MARA,
                proposition=Proposition(
                    subject_id=SALTKEEP, predicate="IS_HAUNTED", object=BooleanObject(value=True)
                ),
            ),
        ),
        "missing_source",
    ),
    LabeledProposal(
        "duplicate_record_id",
        (CreateEntityOp(id=MARA, type="Character", name="Another Mara"),),
        "duplicate_id",
    ),
    LabeledProposal(
        "slug_already_used",
        (CreateEntityOp(id="ent_x", type="Character", name="Impostor", slug="mara-venn"),),
        "slug_conflict",
    ),
    LabeledProposal(
        "external_id_already_bound",
        (CreateEntityOp(id="ent_x", type="Character", name="X", external_ids={"seed": "mara"}),),
        "external_id_conflict",
    ),
    LabeledProposal(
        "unknown_event_participant",
        (
            CreateEventOp(
                id="evt_x",
                kind="duel",
                name="Duel",
                participants=[EventParticipant(entity_id="ent_missing", role="duellist")],
            ),
        ),
        "unknown_reference",
    ),
    LabeledProposal(
        "event_location_is_a_character",
        (CreateEventOp(id="evt_x", kind="duel", name="Duel", location_ids=[MARA]),),
        "invalid_location",
    ),
    LabeledProposal(
        "close_an_already_closed_fact",
        (CloseFactOp(id="fct_seed_compass_color"),),
        "closed_fact",
    ),
    LabeledProposal(
        "inverted_validity_interval",
        (
            _fact(
                "fct_x",
                IVEN,
                "LOCATED_AT",
                _ref(HARBOR, _P),
                valid_from=datetime(2030, 1, 2, tzinfo=UTC),
                valid_until=datetime(2030, 1, 1, tzinfo=UTC),
            ),
        ),
        "invalid_interval",
    ),
    LabeledProposal(
        "interval_on_a_non_temporal_predicate",
        (
            _fact(
                "fct_x",
                LEDGER,
                "HAS_COLOR",
                StringObject(value="red"),
                valid_from=datetime(2030, 1, 1, tzinfo=UTC),
            ),
        ),
        "invalid_temporal_fact",
    ),
    LabeledProposal(
        "confidence_out_of_range",
        (_fact("fct_x", LEDGER, "HAS_COLOR", StringObject(value="red"), confidence=1.5),),
        "invalid_confidence",
    ),
    LabeledProposal(
        "link_to_unknown_asset",
        (LinkAssetOp(id="alk_x", asset_id="ast_missing", target_id=HARBOR),),
        "unknown_asset",
    ),
    LabeledProposal(
        "evidence_cites_unknown_asset",
        (
            CreateEntityOp(
                id="ent_x",
                type="Location",
                name="Nowhere",
                evidence=[EvidenceRef(source_artefact_id="ast_missing")],
            ),
        ),
        "unknown_evidence",
    ),
)


def canon_seed_checksum() -> str:
    """Checksum of the seed definition (not of database state)."""
    parts = [CANON_SEED_VERSION]
    for project in SEED_PROJECTS:
        parts.append(f"{project.workspace_id}/{project.project_id}")
        for step in project.steps:
            parts.append(f"{step.label}:{canonicalize_operations(list(step.operations))}")
    parts.append(hashlib.sha256(CHART_BYTES).hexdigest())
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass
class CanonSeedReport:
    version: str
    checksum: str
    committed: int = 0
    heads: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


async def _seed_media(media: MediaStorePort, objects: ObjectStorePort) -> None:
    """Register the chart with stable ids; reuses the stored object on re-seed."""
    checksum = hashlib.sha256(CHART_BYTES).hexdigest()
    scope: dict[str, Any] = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}
    existing = next(
        (stat for stat in objects.list_objects(**scope) if stat.checksum == checksum), None
    )
    stat = existing or objects.put(CHART_BYTES, content_type="image/svg+xml", **scope)
    await media.import_asset(
        MediaAsset(
            id=CHART_ASSET,
            name="Lantern Harbor chart",
            created_revision=0,
            created_at=FIXED_TIMESTAMP,
            **scope,
        )
    )
    await media.import_version(
        MediaAssetVersion(
            id=CHART_VERSION,
            asset_id=CHART_ASSET,
            version=1,
            object_key=stat.key,
            content_type="image/svg+xml",
            byte_length=len(CHART_BYTES),
            checksum=checksum,
            source="Novel Engine reference world",
            licence="CC0-1.0",
            attribution="Novel Engine contributors",
            authority="author",
            visibility="project",
            created_revision=0,
            created_at=FIXED_TIMESTAMP,
            **scope,
        )
    )


async def apply_canon_seed(
    *,
    journal: CanonJournalPort,
    repository: CanonRepositoryPort,
    schemas: SchemaVersionStorePort,
    media: MediaStorePort,
    objects: ObjectStorePort,
) -> CanonSeedReport:
    """Commit the reference world through the mutation service (idempotent).

    This is a trusted operator tool guarded by the seed/reset environment check;
    it authors as the seed users rather than through a request session.
    """
    report = CanonSeedReport(version=CANON_SEED_VERSION, checksum=canon_seed_checksum())
    service = MutationService(
        journal, repository, local_mode=True, schema_store=schemas, media=media
    )
    await _seed_media(media, objects)
    for project in SEED_PROJECTS:
        label = f"{project.workspace_id}/{project.project_id}"
        scope = AccessScope(
            actor_user_id=project.actor_user_id,
            auth_mode=AuthMode.LOCAL,
            correlation_id=f"seed:{CANON_SEED_VERSION}",
            workspace_id=project.workspace_id,
            project_id=project.project_id,
        )
        published = await schemas.get_published(
            workspace_id=project.workspace_id, project_id=project.project_id
        )
        if published is None:
            report.problems.append(f"{label}: no published schema version")
            continue
        for index, step in enumerate(project.steps):
            try:
                mutation = await service.create_draft(
                    scope,
                    workspace_id=project.workspace_id,
                    project_id=project.project_id,
                    operations=list(step.operations),
                    expected_head=index,
                    idempotency_key=f"seed:{CANON_SEED_VERSION}:{project.project_id}:{index}",
                    schema_version_id=published.id,
                )
                await service.commit_author_change(
                    scope, mutation.id, acknowledge_high_impact=step.acknowledge_high_impact
                )
            except HeadConflict:
                report.problems.append(
                    f"{label}: canon has diverged from the seed at step {step.label!r}"
                )
                break
            report.committed += 1
        branch = await journal.ensure_root_branch(
            workspace_id=project.workspace_id, project_id=project.project_id
        )
        report.heads[label] = await repository.get_head(
            workspace_id=project.workspace_id, project_id=project.project_id, branch_id=branch.id
        )
    report.problems.extend(await verify_canon_seed(repository))
    return report


async def verify_canon_seed(repository: CanonRepositoryPort) -> list[str]:
    """Check the properties the reference world exists to demonstrate."""
    problems: list[str] = []
    primary: dict[str, Any] = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}
    alt: dict[str, Any] = {"workspace_id": WS_ALT, "project_id": ALT_PROJECT_ID}

    facts = await repository.list_facts(**primary, include_closed=False, limit=500)
    for belief_id, expected in EXPECTED_TRUTH_AGREEMENT.items():
        belief = await repository.get_belief(**primary, belief_id=belief_id)
        if belief is None:
            problems.append(f"missing belief {belief_id}")
        elif truth_agreement(belief, facts) is not expected:
            problems.append(f"belief {belief_id} is not {expected.value}")

    active = {entity.id for entity in await repository.list_entities(**primary, limit=500)}
    if LIGHTHOUSE in active:
        problems.append("retired entity is still listed as active")
    retired = await repository.get_entity(**primary, entity_id=LIGHTHOUSE)
    if retired is None or retired.is_active:
        problems.append("retired entity lost its history")

    history = await repository.list_record_versions(
        **primary, record_type="fact", record_id="fct_seed_compass_color"
    )
    if sorted(version.operation_type for version in history) != ["assert_fact", "close_fact"]:
        problems.append("closed fact lost its assert/close history")

    inquiry = await repository.get_event(**primary, event_id="evt_seed_inquiry")
    if inquiry is None or inquiry.world_position is not None:
        problems.append("an unknown event date was fabricated")

    link = await repository.get_link(**primary, link_id=CHART_LINK)
    if link is None or link.target_record_id != HARBOR:
        problems.append("media evidence link is missing")

    # Same ids and names in another tenant must resolve to that tenant's truth only.
    here = await repository.get_fact(**primary, fact_id="fct_seed_mara_at")
    there = await repository.get_fact(**alt, fact_id="fct_seed_mara_at")
    if here is None or there is None:
        problems.append("colliding fact id is missing in one tenant")
    elif (
        getattr(here.proposition.object, "entity_id", None) != HARBOR
        or getattr(there.proposition.object, "entity_id", None) != SALTKEEP
    ):
        problems.append("colliding fact id leaked across tenants")
    if await repository.get_entity(**alt, entity_id=IVEN) is not None:
        problems.append("an entity leaked into the other tenant")
    return problems


__all__ = [
    "ALT_PROJECT_ID",
    "CANON_SEED_VERSION",
    "EXPECTED_TRUTH_AGREEMENT",
    "INVALID_PROPOSALS",
    "PRIMARY_HEAD",
    "SEED_PROJECTS",
    "VALID_PROPOSALS",
    "CanonSeedReport",
    "LabeledProposal",
    "SeedProject",
    "SeedStep",
    "apply_canon_seed",
    "canon_seed_checksum",
    "verify_canon_seed",
]
