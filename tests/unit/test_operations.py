import pytest
from pydantic import ValidationError

from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.operations import (
    AssertFactOp,
    CreateEntityOp,
    CreateEventOp,
    UnresolvedReferenceError,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)

pytestmark = pytest.mark.unit


class _Ids:
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self, kind: str, temporary_id: str) -> str:
        self.counter += 1
        return f"{kind}_{self.counter:03d}"


def _entity(tmp: str, name: str) -> CreateEntityOp:
    return CreateEntityOp(id=tmp, type="Character", name=name)


def test_temporary_ids_resolve_deterministically_and_rewrite_refs() -> None:
    operations = [
        _entity("tmp:mara", "Mara"),
        AssertFactOp(
            id="tmp:fact1",
            proposition=Proposition(
                subject_id="tmp:mara",
                predicate="HAS_NAME",
                object=StringObject(value="Mara"),
            ),
        ),
    ]
    ids = _Ids()
    resolved, mapping = resolve_temporary_ids(operations, new_id=ids)

    assert mapping["tmp:mara"].startswith("entity_")
    assert mapping["tmp:fact1"].startswith("fact_")
    assert resolved[1].proposition.subject_id == mapping["tmp:mara"]
    assert resolved[1].id == mapping["tmp:fact1"]

    # A second resolution with a fresh counter would differ, but deriving from a
    # stable seed yields the same mapping.
    def stable(kind: str, temporary_id: str) -> str:
        return f"{kind}:{temporary_id}"

    first, _ = resolve_temporary_ids(operations, new_id=stable)
    second, _ = resolve_temporary_ids(operations, new_id=stable)
    assert first == second


def test_unresolved_temporary_reference_is_rejected() -> None:
    operations = [
        AssertFactOp(
            id="tmp:fact",
            proposition=Proposition(
                subject_id="tmp:missing",
                predicate="HAS_NAME",
                object=StringObject(value="x"),
            ),
        )
    ]
    with pytest.raises(UnresolvedReferenceError):
        resolve_temporary_ids(operations, new_id=lambda kind, tmp: f"{kind}:{tmp}")


def test_entity_reference_object_is_rewritten() -> None:
    operations = [
        _entity("tmp:mara", "Mara"),
        _entity("tmp:ivo", "Ivo"),
        AssertFactOp(
            id="tmp:fact",
            proposition=Proposition(
                subject_id="tmp:mara",
                predicate="KNOWS",
                object=EntityRefObject(entity_id="tmp:ivo", project_id="pr1"),
            ),
        ),
    ]
    resolved, mapping = resolve_temporary_ids(operations, new_id=lambda kind, tmp: f"{kind}:{tmp}")
    obj = resolved[2].proposition.object
    assert isinstance(obj, EntityRefObject)
    assert obj.entity_id == mapping["tmp:ivo"]


def test_canonical_serialization_is_order_stable() -> None:
    op = CreateEventOp(id="evt_1", kind="battle", name="The Fire")
    canonical = canonicalize_operations([op])
    assert operations_checksum([op]) == operations_checksum([op])
    assert canonical == canonicalize_operations([op])


def test_parse_operation_round_trips() -> None:
    op = _entity("tmp:x", "X")
    assert parse_operation(op.model_dump(mode="json")) == op


def test_operation_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CreateEntityOp(id="e1", type="Character", name="Mara", nope=True)  # type: ignore[call-arg]
