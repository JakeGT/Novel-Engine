"""Property and state-machine tests for the highest-risk canon invariants (2L).

Hypothesis explores operation sequences no hand-written example would; each test
states an invariant that must hold for *every* sequence, then lets the engine
search for a counterexample and shrink it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from backend.domain.canon.naming import normalize_name, slug_problem, slugify
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import HeadConflict, MutationStatus, RevisionConflict
from backend.domain.operations import (
    CreateEntityOp,
    RetireEntityOp,
    UpdateEntityOp,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)
from backend.domain.review import LEGAL_TRANSITIONS, TERMINAL_STATUSES, IllegalTransition
from backend.domain.schema_defs import SchemaDefinition
from backend.domain.validation import CanonValidationError
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.mutation_service import MutationService
from backend.services.projection_service import GraphProjectionService
from backend.services.schema_compiler import compile_definition, compute_content_hash
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository, InMemoryCanonWriter

pytestmark = pytest.mark.unit

WS, PR = "ws", "pr"
SCOPE = AccessScope(actor_user_id="u", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR)
PROFILE = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
MACHINE = settings(
    max_examples=150,
    stateful_step_count=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


def run(coroutine: Any) -> Any:
    # A fresh, properly closed loop per call: Hypothesis drives thousands of them.
    return asyncio.run(coroutine)


# --------------------------------------------------------------- serialization
_names = st.text(alphabet="abcdefghijklmnop XYZ", min_size=1, max_size=12).filter(str.strip)
_types = st.sampled_from(["Character", "Location", "Object", "Faction"])
_attribute_values = st.one_of(st.integers(-5, 5), st.booleans(), st.text(max_size=6))
_attributes = st.dictionaries(st.sampled_from(["a", "b", "c"]), _attribute_values, max_size=3)


@st.composite
def entity_operations(draw: st.DrawFn) -> list[Any]:
    count = draw(st.integers(1, 5))
    operations: list[Any] = []
    for index in range(count):
        operations.append(
            CreateEntityOp(
                id=f"tmp:e{index}",
                type=draw(_types),
                name=draw(_names),
                attributes=draw(_attributes),
                aliases=draw(
                    st.lists(st.sampled_from(["Fox", "Owl", "Wren"]), unique=True, max_size=2)
                ),
            )
        )
    return operations


@PROFILE
@given(entity_operations())
def test_canonical_form_is_stable_under_serialization_round_trips(operations) -> None:
    canonical = canonicalize_operations(operations)
    reparsed = [parse_operation(payload) for payload in json.loads(canonical)]
    assert canonicalize_operations(reparsed) == canonical
    assert operations_checksum(reparsed) == operations_checksum(operations)
    # A full (non-canonical) dump parses back to the same canonical form too.
    full = [parse_operation(op.model_dump(mode="json")) for op in operations]
    assert canonicalize_operations(full) == canonical


@PROFILE
@given(entity_operations(), st.text(alphabet="abc123", min_size=1, max_size=6))
def test_temporary_id_resolution_is_deterministic_and_total(operations, salt) -> None:
    def new_id(kind: str, temporary: str) -> str:
        return f"ent_{salt}_{temporary.split(':')[1]}"

    first, mapping = resolve_temporary_ids(operations, new_id=new_id)
    second, _ = resolve_temporary_ids(operations, new_id=new_id)
    assert canonicalize_operations(first) == canonicalize_operations(second)
    assert all(not op.id.startswith("tmp:") for op in first)
    assert len(set(mapping.values())) == len(mapping)


@PROFILE
@given(st.text(max_size=60))
def test_slugify_always_yields_a_valid_slug_or_nothing(name) -> None:
    slug = slugify(name)
    assert slug == "" or slug_problem(slug) is None
    assert normalize_name(normalize_name(name)) == normalize_name(name)


# ------------------------------------------------------------- schema compiler
_identifier = st.from_regex(r"[A-Z][A-Za-z]{2,8}", fullmatch=True)
_field_name = st.from_regex(r"[a-z][a-z_]{1,8}", fullmatch=True)


@st.composite
def schema_definitions(draw: st.DrawFn) -> dict[str, Any]:
    core = {"Character", "Location", "Object", "Faction", "Event", "Concept", "System"}
    type_names = draw(
        st.lists(_identifier.filter(lambda n: n not in core), unique=True, min_size=1, max_size=3)
    )
    extensions = []
    for type_name in type_names:
        fields = []
        for field_name in draw(st.lists(_field_name, unique=True, max_size=4)):
            kind = draw(st.sampled_from(["string", "integer", "boolean", "enum"]))
            field: dict[str, Any] = {
                "name": field_name,
                "field_type": kind,
                "required": draw(st.booleans()),
            }
            if kind == "enum":
                field["values"] = draw(
                    st.lists(st.sampled_from(["a", "b", "c", "d"]), unique=True, min_size=1)
                )
            fields.append(field)
        extensions.append({"entity_type": type_name, "fields": fields})
    return {"label": "generated", "entity_extensions": extensions}


@PROFILE
@given(schema_definitions(), st.randoms(use_true_random=False))
def test_schema_compilation_is_deterministic_and_content_addressed(definition, rng) -> None:
    canonical = SchemaDefinition.model_validate(definition).canonical()
    first = compile_definition(canonical, workspace_id=WS, project_id=PR, use_cache=False)
    second = compile_definition(canonical, workspace_id=WS, project_id=PR, use_cache=False)
    assert first.content_hash == second.content_hash == compute_content_hash(canonical)
    assert first.json_schema == second.json_schema

    # Key order in the submitted document never changes the compiled result.
    items = list(canonical.items())
    rng.shuffle(items)
    reordered = compile_definition(dict(items), workspace_id=WS, project_id=PR, use_cache=False)
    assert reordered.content_hash == first.content_hash

    # The same definition in another project never shares cached validators' identity.
    other = compile_definition(canonical, workspace_id=WS, project_id="other")
    assert other.content_hash == first.content_hash
    assert {ext["entity_type"] for ext in canonical["entity_extensions"]} <= set(first.entity_types)


@PROFILE
@given(schema_definitions())
def test_compiled_attribute_models_are_strict(definition) -> None:
    from pydantic import ValidationError

    canonical = SchemaDefinition.model_validate(definition).canonical()
    compiled = compile_definition(canonical, workspace_id=WS, project_id=PR, use_cache=False)
    for extension in canonical["entity_extensions"]:
        model = compiled.entity_models[extension["entity_type"]]
        valid: dict[str, Any] = {}
        for field in extension["fields"]:
            valid[field["name"]] = {
                "string": "x",
                "integer": 1,
                "boolean": True,
                "enum": (field.get("values") or ["a"])[0],
            }[field["field_type"]]
        model.model_validate(valid)
        # Undeclared attributes are never accepted through a compiled extension.
        with pytest.raises(ValidationError):
            model.model_validate({**valid, "undeclared_attribute_xyz": 1})
        for field in extension["fields"]:
            if field["required"]:
                missing = {k: v for k, v in valid.items() if k != field["name"]}
                with pytest.raises(ValidationError):
                    model.model_validate(missing)


# -------------------------------------------------------------- revision heads
@PROFILE
@given(st.lists(st.tuples(st.integers(0, 6), _names), min_size=1, max_size=12))
def test_head_advances_by_exactly_one_only_from_the_expected_revision(attempts) -> None:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    service = MutationService(journal, repo, local_mode=True)
    head = 0

    async def play() -> None:
        nonlocal head
        for index, (expected, name) in enumerate(attempts):
            draft = await service.create_draft(
                SCOPE,
                workspace_id=WS,
                project_id=PR,
                operations=[CreateEntityOp(id=f"ent_{index}", type="Character", name=name)],
                expected_head=expected,
            )
            before = dict(repo.entities)
            if expected == head:
                result = await service.commit(SCOPE, draft.id)
                assert result.revision == head + 1
                head += 1
            else:
                with pytest.raises(HeadConflict):
                    await service.commit(SCOPE, draft.id)
                assert repo.entities == before

    run(play())
    assert [revision.revision for revision in repo.revisions] == list(range(1, head + 1))
    assert len(repo.envelopes) == head
    assert len({env.mutation_id for env in repo.envelopes.values()}) == head


# ------------------------------------------------------ mutation state machine
class MutationReviewMachine(RuleBasedStateMachine):
    """Random review actions against one protected-origin proposal.

    Whatever the reviewers do, in whatever order: every recorded transition is
    legal and contiguous, terminal states never change, canon changes only by a
    commit, a rejected operation never reaches canon, and nothing commits
    without an approval made after the last edit or decision.
    """

    def __init__(self) -> None:
        super().__init__()
        self.journal = InMemoryCanonJournal()
        self.repo = InMemoryCanonRepository(journal=self.journal)
        self.service = MutationService(self.journal, self.repo, local_mode=True)
        self.operation_count = 3
        mutation = run(
            self.service.create_draft(
                SCOPE,
                workspace_id=WS,
                project_id=PR,
                operations=[
                    CreateEntityOp(id=f"tmp:e{i}", type="Character", name=f"Entity {i}")
                    for i in range(self.operation_count)
                ],
                expected_head=0,
                origin="model",
            )
        )
        self.mutation_id = mutation.id
        self.rejected: set[int] = set()
        self.names = {i: f"Entity {i}" for i in range(self.operation_count)}
        self.approved_since_change = False
        self.committed = False

    @property
    def status(self) -> MutationStatus:
        return self.journal.mutations[self.mutation_id].status

    def _terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def _expect_refused(self, coroutine: Any) -> None:
        with pytest.raises((RevisionConflict, IllegalTransition, CanonValidationError)):
            run(coroutine)

    @rule()
    def submit(self) -> None:
        if self._terminal():
            self._expect_refused(self.service.submit(SCOPE, self.mutation_id))
            return
        run(self.service.submit(SCOPE, self.mutation_id))
        # Sending an approved set back to review withdraws the approval.
        assert self.status is MutationStatus.NEEDS_REVIEW
        self.approved_since_change = False

    @rule(ordinal=st.integers(0, 2), decision=st.sampled_from(["accepted", "rejected"]))
    def decide(self, ordinal: int, decision: str) -> None:
        call = self.service.decide_operation(SCOPE, self.mutation_id, ordinal, decision)
        if self._terminal():
            self._expect_refused(call)
            return
        run(call)
        (self.rejected.add if decision == "rejected" else self.rejected.discard)(ordinal)
        self.approved_since_change = False

    @rule(ordinal=st.integers(0, 2), suffix=st.integers(0, 99))
    def edit(self, ordinal: int, suffix: int) -> None:
        name = f"Edited {ordinal}-{suffix}"
        call = self.service.revise_operation(
            SCOPE,
            self.mutation_id,
            ordinal,
            CreateEntityOp(id=f"tmp:e{ordinal}", type="Character", name=name),
        )
        if self._terminal():
            self._expect_refused(call)
            return
        run(call)
        self.names[ordinal] = name
        # An edit is a new revision: the decision on the old revision no longer applies.
        self.rejected.discard(ordinal)
        self.approved_since_change = False

    @rule()
    def approve(self) -> None:
        call = self.service.approve(SCOPE, self.mutation_id)
        if self._terminal() or len(self.rejected) == self.operation_count:
            self._expect_refused(call)
            return
        run(call)
        self.approved_since_change = True

    @precondition(lambda self: not self.committed)
    @rule()
    def reject(self) -> None:
        call = self.service.reject(SCOPE, self.mutation_id)
        if self._terminal():
            self._expect_refused(call)
        else:
            run(call)
            self.approved_since_change = False

    @rule()
    def approve_then_commit(self) -> None:
        """The path that matters most; exercised directly so every run reaches it."""
        self.approve()
        self.commit()

    @rule()
    def commit(self) -> None:
        call = self.service.commit(SCOPE, self.mutation_id)
        if self.committed:
            assert run(call).revision == 1  # idempotent retry
            return
        if self.status is MutationStatus.APPROVED and self.approved_since_change:
            assert run(call).revision == 1
            self.committed = True
        else:
            self._expect_refused(call)

    @invariant()
    def transitions_are_legal_and_contiguous(self) -> None:
        previous = MutationStatus.DRAFT
        for transition in self.journal.transitions:
            assert transition.from_status is previous
            assert transition.to_status in LEGAL_TRANSITIONS[transition.from_status]
            assert transition.reason and transition.actor_user_id == "u"
            previous = transition.to_status
        assert previous is self.status

    @invariant()
    def canon_changes_only_through_a_commit(self) -> None:
        if not self.committed:
            assert self.repo.entities == {} and self.repo.envelopes == {}
            assert self.status is not MutationStatus.COMMITTED
            return
        assert self.status is MutationStatus.COMMITTED
        assert len(self.repo.envelopes) == 1
        names = sorted(entity.name for entity in self.repo.entities.values())
        expected = sorted(
            name for ordinal, name in self.names.items() if ordinal not in self.rejected
        )
        assert names == expected

    @invariant()
    def a_commit_is_always_preceded_by_an_approval(self) -> None:
        if self.committed:
            assert any(d.decision == "approved" for d in self.journal.decisions)


MutationReviewMachine.TestCase.settings = MACHINE
TestMutationReviewMachine = MutationReviewMachine.TestCase


# ------------------------------------------------------------------- recovery
@PROFILE
@given(
    st.lists(st.integers(0, 3), min_size=1, max_size=8),
    st.lists(st.one_of(st.none(), st.integers(0, 2)), min_size=1, max_size=10),
)
def test_projection_converges_despite_crashes_and_never_duplicates(batch_sizes, faults) -> None:
    """Crash the projector at arbitrary points; retries must converge exactly once."""
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    writer = InMemoryCanonWriter()
    service = MutationService(journal, repo, local_mode=True)
    projector = GraphProjectionService(canon=repo, writer=writer)
    equivalence = CanonEquivalenceService(canon=repo, journal=journal, writer=writer)

    async def play() -> None:
        head = 0
        created: list[str] = []
        for batch, size in enumerate(batch_sizes):
            operations: list[Any] = [
                CreateEntityOp(id=f"ent_{batch}_{i}", type="Character", name=f"E{batch}{i}")
                for i in range(size + 1)
            ]
            if created and size % 2:
                operations.append(UpdateEntityOp(id=created[0], name=f"Renamed {batch}"))
            if len(created) > 2 and size == 3:
                operations.append(RetireEntityOp(id=created.pop()))
            draft = await service.create_draft(
                SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
            )
            head = (
                await service.commit_author_change(SCOPE, draft.id, acknowledge_high_impact=True)
            ).revision
            created.extend(op.id for op in operations if isinstance(op, CreateEntityOp))

        branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PR)
        target = {"workspace_id": WS, "project_id": PR, "branch_id": branch.id}
        for fault in faults:
            writer.fail_after_ops = fault
            report = await projector.project_pending(**target)
            # A failure is reported as lag; it never raises into the caller.
            assert report.error is None or report.error == "RuntimeError"
        writer.fail_after_ops = None
        final = await projector.project_pending(**target)
        assert final.error is None and final.revision == head
        assert len(writer.markers) == head
        result = await equivalence.verify(WS, PR)
        assert result.ok, result.problems

    run(play())
