"""The reference world's layers beyond canon operations (QLT-001), seeded idempotently.

``backend.seed.canon`` commits the story bible and the plan through the review path.
This adds what lives beside them, so the seed exercises every layer:

* **a scene's prose** (ADR-022): the first scene has a text written by the author, linked
  to it as its manuscript;
* **a kept draft**: a draft from the assistant that the author kept, which became the
  text's second version and remembers the draft and the context it was written from;
* **the context it was written from** (ADR-021): a kept manifest, when a manifest store
  is given;
* **a scoped name answer** (CTX-016): "the pilot" means Mara, but only from the Saltkeep
  scene on;
* **a what-if with its own changes** (ADR-020): "What if Iven confessed?", started from
  the main story, in which the secret is common knowledge and Mara knows who took the
  ledger, while the main story keeps its secret.

Every record has a fixed id, and each is written only when it is missing, so seeding
again changes nothing. Like the canon seed, this is a trusted operator tool guarded by
the seed/reset environment check; it writes through the stores, never a request session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.domain.branching import working_in
from backend.domain.canon.assertions import FactVisibility
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.context import (
    SCENE_DRAFT_POLICY,
    ContextItem,
    ContextSection,
    assemble,
    stored_manifest,
)
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import AmendFactOp, AssertBeliefOp, CanonOperation, EvidenceRef
from backend.domain.sources import AliasBinding, SourceKind, Verdict, VersionOrigin, content_hash
from backend.domain.studio import SceneDraft
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import (
    FIXED_TIMESTAMP,
    HARBOR,
    IVEN,
    LEDGER,
    MARA,
    SCENE_DUSK,
    SCENE_SALTKEEP,
)
from backend.seed.tenancy import USER_OWNER
from backend.services.mutation_service import MutationService

STORY_SEED_VERSION = "2026-10-18.1"

PROSE_TEXT = "src_seed_scene_dusk"
PROSE_V1 = "srv_seed_scene_dusk_v1"
PROSE_V2 = "srv_seed_scene_dusk_v2"
DRAFT = "drf_seed_scene_dusk"
MANIFEST = "ctx_seed_scene_dusk"
NAME_ANSWER = "alb_seed_the_pilot"
WHAT_IF = "brn_seed_iven_confessed"
WHAT_IF_NAME = "What if Iven confessed?"
WHAT_IF_BELIEF = "blf_seed_mara_knows_ledger"

# Original words, written for the reference world; no third-party text is ever seeded.
PROSE = (
    "The lamps came on along Lantern Harbor one by one. Mara Venn tied the skiff and "
    "watched Iven Marsh cross the quay with his collar up.\n\n"
    "He did not look at the water. He never had, since the Dawn Runner went down."
)
KEPT = (
    "The lamps came on along Lantern Harbor one by one, and the tide pulled at the "
    "moorings. Mara Venn tied the skiff and watched Iven Marsh cross the quay with his "
    "collar up.\n\n"
    "He did not look at the water. He had not looked at it since the Dawn Runner went "
    "down, and Mara had stopped asking why."
)


@dataclass
class StorySeedReport:
    version: str = STORY_SEED_VERSION
    written: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


_SCOPE: dict[str, Any] = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}


def _what_if_operations() -> list[CanonOperation]:
    evidence = [EvidenceRef(source_artefact_id="seed:what-if.iven-confessed")]
    return [
        # In the what-if, Iven confessed: the secret is common knowledge...
        AmendFactOp(id="fct_seed_iven_ledger", visibility=FactVisibility.COMMON, evidence=evidence),
        # ...and Mara knows who took the ledger.
        AssertBeliefOp(
            id=WHAT_IF_BELIEF,
            holder_entity_id=MARA,
            proposition=Proposition(
                subject_id=IVEN,
                predicate="OWNS",
                object=EntityRefObject(entity_id=LEDGER, project_id=SEED_PROJECT_ID),
            ),
            source_ref="seed:what-if.iven-confessed",
            evidence=evidence,
        ),
    ]


def _manifest() -> tuple[Any, dict[str, Any]]:
    """The context the kept draft was written from: the scene, its people and its place."""
    items = [
        ContextItem(
            key=f"scene:{SCENE_DUSK}",
            section=ContextSection.SCENE,
            text="The harbor at dusk. It happens in year 4.",
            reason="This is the scene to write.",
            authority="plan",
            record_type="plan_item",
            record_id=SCENE_DUSK,
            score=100,
            hard=True,
        ),
        ContextItem(
            key=f"entity:{MARA}",
            section=ContextSection.CAST,
            text="Mara Venn (Character), also called The Fox; role: harbor pilot.",
            reason="Mara is the scene's point of view.",
            authority="entry",
            record_type="entity",
            record_id=MARA,
            score=90,
            hard=True,
        ),
        ContextItem(
            key=f"entity:{HARBOR}",
            section=ContextSection.CAST,
            text="Lantern Harbor (Location).",
            reason="The scene happens here.",
            authority="entry",
            record_type="entity",
            record_id=HARBOR,
            score=80,
            hard=True,
        ),
    ]
    manifest = assemble(items, policy=SCENE_DRAFT_POLICY)
    return manifest, stored_manifest(manifest, point_of_view=MARA)


async def apply_story_seed(
    *,
    journal: Any,
    repository: Any,
    schemas: Any,
    sources: Any,
    studio: Any,
    manifests: Any | None = None,
) -> StorySeedReport:
    """Seed the layers beside canon on top of a seeded reference world (idempotent).

    Run after ``apply_canon_seed``: the scenes, entries and facts it points at must exist.
    """
    report = StorySeedReport()
    main = await journal.ensure_root_branch(**_SCOPE)
    if await repository.get_plan_item(**_SCOPE, plan_id=SCENE_DUSK) is None:
        report.problems.append("the reference world's plan is missing; seed canon first")
        return report

    # A scene's prose, written by the author.
    if await sources.get_source(**_SCOPE, source_id=PROSE_TEXT) is None:
        await sources.add_source(
            **_SCOPE,
            source_id=PROSE_TEXT,
            title="The harbor at dusk",
            kind=SourceKind.SCENE,
            actor_user_id=USER_OWNER,
        )
        report.written.append("scene text")
    if await sources.get_version(**_SCOPE, version_id=PROSE_V1) is None:
        await sources.add_version(
            **_SCOPE,
            version_id=PROSE_V1,
            source_id=PROSE_TEXT,
            content=PROSE,
            content_hash=content_hash(PROSE),
            actor_user_id=USER_OWNER,
        )
        report.written.append("the author's version")
    if await studio.manuscript(**_SCOPE, branch_id=main.id, plan_id=SCENE_DUSK) is None:
        await studio.link(**_SCOPE, branch_id=main.id, plan_id=SCENE_DUSK, source_id=PROSE_TEXT)
        report.written.append("the scene's manuscript")

    # The context the draft was written from.
    manifest_id: str | None = None
    if manifests is not None:
        manifest_id = MANIFEST
        if await manifests.get(**_SCOPE, manifest_id=MANIFEST) is None:
            built, kept = _manifest()
            await manifests.save(
                **_SCOPE,
                manifest_id=MANIFEST,
                # As the product keeps context made in the main story.
                branch_id=None,
                purpose="scene-draft",
                plan_id=SCENE_DUSK,
                policy_version=built.policy_version,
                checksum=built.checksum,
                words=built.words,
                budget=built.budget,
                manifest=kept,
                created_by=USER_OWNER,
            )
            report.written.append("the draft's context")

    # A draft from the assistant, kept by the author as the text's second version.
    draft = await studio.get_draft(**_SCOPE, draft_id=DRAFT)
    if draft is None:
        await studio.add_draft(
            SceneDraft(
                **_SCOPE,
                id=DRAFT,
                branch_id=None,
                plan_id=SCENE_DUSK,
                job_id="seed:scene-draft",
                manifest_id=manifest_id,
                number=1,
                content=KEPT,
                model="seed",
                status="proposed",
                kept_version_id=None,
                created_by=USER_OWNER,
                created_at=FIXED_TIMESTAMP,
            )
        )
        report.written.append("a draft")
    if await sources.get_version(**_SCOPE, version_id=PROSE_V2) is None:
        await sources.add_version(
            **_SCOPE,
            version_id=PROSE_V2,
            source_id=PROSE_TEXT,
            content=KEPT,
            content_hash=content_hash(KEPT),
            actor_user_id=USER_OWNER,
            origin=VersionOrigin.ASSISTANT,
            parent_version_id=PROSE_V1,
            draft_id=DRAFT,
            manifest_id=manifest_id,
        )
        report.written.append("the kept version")
    draft = await studio.get_draft(**_SCOPE, draft_id=DRAFT)
    if draft is not None and draft.status != "kept":
        await studio.mark_kept(**_SCOPE, draft_id=DRAFT, version_id=PROSE_V2)
        report.written.append("the draft kept")

    # A name answer that holds only from the Saltkeep scene on.
    bindings = await sources.list_bindings(**_SCOPE)
    if all(binding.id != NAME_ANSWER for binding in bindings):
        await sources.put_binding(
            AliasBinding(
                **_SCOPE,
                id=NAME_ANSWER,
                phrase="the pilot",
                normalized=normalize_name("the pilot"),
                verdict=Verdict.IS,
                entity_id=MARA,
                source_id=None,
                decided_by=USER_OWNER,
                decided_at=FIXED_TIMESTAMP,
                from_scene_id=SCENE_SALTKEEP,
            )
        )
        report.written.append("a scoped name answer")

    # A what-if with its own changes, started from the main story as seeded.
    branches = await journal.list_branches(**_SCOPE)
    if all(branch.id != WHAT_IF for branch in branches):
        await repository.fork_branch(
            **_SCOPE,
            parent_branch_id=main.id,
            branch_id=WHAT_IF,
            name=WHAT_IF_NAME,
            description="Iven tells the inquiry he took the ledger.",
            actor_user_id=USER_OWNER,
        )
        report.written.append("a what-if")
    branch = await journal.get_branch(**_SCOPE, branch_id=WHAT_IF)
    if branch is None:
        report.problems.append("the what-if could not be started")
        return report
    head = await repository.get_head(**_SCOPE, branch_id=WHAT_IF)
    if head == (branch.fork_revision or 0):
        published = await schemas.get_published(**_SCOPE)
        service = MutationService(journal, repository, local_mode=True, schema_store=schemas)
        scope = AccessScope(
            actor_user_id=USER_OWNER,
            auth_mode=AuthMode.LOCAL,
            correlation_id=f"seed:{STORY_SEED_VERSION}",
            **_SCOPE,
        )
        with working_in(WHAT_IF):
            mutation = await service.create_draft(
                scope,
                **_SCOPE,
                operations=_what_if_operations(),
                expected_head=head,
                branch_id=WHAT_IF,
                idempotency_key=f"seed:{STORY_SEED_VERSION}:what-if:0",
                schema_version_id=published.id if published is not None else None,
            )
            await service.commit_author_change(scope, mutation.id)
        report.written.append("the what-if's changes")
    report.problems.extend(await verify_story_seed(journal, repository, sources, studio, manifests))
    return report


async def verify_story_seed(
    journal: Any, repository: Any, sources: Any, studio: Any, manifests: Any | None = None
) -> list[str]:
    """Check the layers the story seed exists to demonstrate."""
    problems: list[str] = []
    main = await journal.ensure_root_branch(**_SCOPE)
    # Used since it was seeded, the scene may hold other words and its text more versions:
    # what was seeded only has to still be there.
    if await studio.manuscript(**_SCOPE, branch_id=main.id, plan_id=SCENE_DUSK) is None:
        problems.append("the scene's prose is not linked to it")
    versions = {v.id: v for v in await sources.list_versions(**_SCOPE, source_id=PROSE_TEXT)}
    first, kept = versions.get(PROSE_V1), versions.get(PROSE_V2)
    if (
        first is None
        or kept is None
        or first.origin != VersionOrigin.AUTHOR
        or kept.origin != VersionOrigin.ASSISTANT
    ):
        problems.append("the scene's text does not have the author's and the kept version")
    elif kept.draft_id != DRAFT or kept.parent_version_id != PROSE_V1:
        problems.append("the kept version does not remember its draft")
    draft = await studio.get_draft(**_SCOPE, draft_id=DRAFT)
    if draft is None or draft.status != "kept" or draft.kept_version_id != PROSE_V2:
        problems.append("the draft is not kept as the text's version")
    if manifests is not None:
        saved = await manifests.get(**_SCOPE, manifest_id=MANIFEST)
        if saved is None or saved.plan_id != SCENE_DUSK:
            problems.append("the context the draft was written from is missing")
    answer = next((b for b in await sources.list_bindings(**_SCOPE) if b.id == NAME_ANSWER), None)
    if answer is None or answer.from_scene_id != SCENE_SALTKEEP or answer.entity_id != MARA:
        problems.append("the scoped name answer is missing")

    branch = await journal.get_branch(**_SCOPE, branch_id=WHAT_IF)
    if branch is None or branch.is_root:
        problems.append("the what-if is missing")
        return problems
    with working_in(WHAT_IF):
        there = await repository.get_fact(**_SCOPE, fact_id="fct_seed_iven_ledger")
        knows = await repository.get_belief(**_SCOPE, belief_id=WHAT_IF_BELIEF)
    here = await repository.get_fact(**_SCOPE, fact_id="fct_seed_iven_ledger")
    if there is None or there.visibility is not FactVisibility.COMMON or knows is None:
        problems.append("the what-if lost its own changes")
    if here is None or here.visibility is not FactVisibility.SECRET:
        problems.append("the what-if's changes leaked into the main story")
    if await repository.get_belief(**_SCOPE, belief_id=WHAT_IF_BELIEF) is not None:
        problems.append("the what-if's belief leaked into the main story")
    return problems


__all__ = [
    "DRAFT",
    "MANIFEST",
    "NAME_ANSWER",
    "PROSE_TEXT",
    "PROSE_V1",
    "PROSE_V2",
    "STORY_SEED_VERSION",
    "WHAT_IF",
    "StorySeedReport",
    "apply_story_seed",
    "verify_story_seed",
]
