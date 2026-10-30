"""The Saltglass Ferry: a small original story world the consistency cases are built from.

Wren Halloway keeps the ferry across the Low Fen. Osric Pell is the lamp-warden at
Marrow Light; Ada Quill runs the Ferry House; Bram Tolley is a Tidewarden. The brass
sextant and the tide ledger change hands. Everything here is invented for the tests.

Only domain records are built here (entries, facts, beliefs, events, scenes), exactly
as the checks read them; nothing touches storage.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.domain.canon.assertions import Belief, Fact, FactVisibility
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event, EventClaim, EventParticipant
from backend.domain.canon.plans import PlanItem, PlanKind, PlanRef
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.knowledge import Reveal, with_reveals, with_scene_time

SCOPE: dict[str, str] = {"workspace_id": "eval", "project_id": "eval", "branch_id": "main"}

ENTRIES: dict[str, tuple[str, str]] = {
    "wren": ("Character", "Wren Halloway"),
    "osric": ("Character", "Osric Pell"),
    "ada": ("Character", "Ada Quill"),
    "bram": ("Character", "Bram Tolley"),
    "quay": ("Location", "Kestle Quay"),
    "light": ("Location", "Marrow Light"),
    "fen": ("Location", "the Low Fen"),
    "house": ("Location", "the Ferry House"),
    "sextant": ("Object", "the brass sextant"),
    "ledger": ("Object", "the tide ledger"),
    "wardens": ("Faction", "the Tidewardens"),
    "crossing": ("Goal", "a safe crossing"),
}


def entity(key: str) -> CanonEntity:
    kind, name = ENTRIES[key]
    return CanonEntity(
        id=key,
        type=kind,
        name=name,
        schema_version_id="eval",
        created_revision=1,
        **SCOPE,
    )


def entities(extra: Iterable[CanonEntity] = ()) -> dict[str, CanonEntity]:
    found = {key: entity(key) for key in ENTRIES}
    found.update({item.id: item for item in extra})
    return found


def names() -> dict[str, str]:
    return {key: name for key, (_kind, name) in ENTRIES.items()}


def types() -> dict[str, str]:
    return {key: kind for key, (kind, _name) in ENTRIES.items()}


def proposition(subject: str, predicate: str, obj: Any) -> Proposition:
    if isinstance(obj, str):
        return Proposition(
            subject_id=subject,
            predicate=predicate,
            object=EntityRefObject(entity_id=obj, project_id="eval"),
        )
    return Proposition.model_validate(
        {"subject_id": subject, "predicate": predicate, "object": obj}
    )


def fact(
    id_: str,
    subject: str,
    predicate: str,
    obj: Any,
    *,
    secret: bool = False,
    common: bool = False,
    **fields: Any,
) -> Fact:
    visibility = (
        FactVisibility.SECRET
        if secret
        else FactVisibility.COMMON
        if common
        else FactVisibility.ORDINARY
    )
    return Fact(
        id=id_,
        proposition=proposition(subject, predicate, obj),
        provenance_id="eval",
        created_revision=fields.pop("rev", 1),
        visibility=visibility,
        **SCOPE,
        **fields,
    )


def belief(id_: str, holder: str, about: Proposition, source: str | None, rev: int = 1) -> Belief:
    return Belief(
        id=id_,
        holder_entity_id=holder,
        proposition=about,
        acquisition_provenance_id="eval",
        source_ref=source,
        created_revision=rev,
        **SCOPE,
    )


def event(
    id_: str,
    when: Any = None,
    *,
    kind: str = "happening",
    who: Iterable[str | tuple[str, str]] = (),
    where: Iterable[str] = (),
    scene: str | None = None,
    claim: EventClaim | None = None,
) -> Event:
    return Event(
        id=id_,
        kind=kind,
        name=id_.replace("_", " ").capitalize(),
        participants=[
            EventParticipant(entity_id=item, role="participant")
            if isinstance(item, str)
            else EventParticipant(entity_id=item[0], role=item[1])
            for item in who
        ],
        location_ids=list(where),
        when=when,
        scene_id=scene,
        claim=claim,
        provenance_id="eval",
        created_revision=1,
        **SCOPE,
    )


def scene(
    id_: str,
    when: Any = None,
    *,
    place: str | None = None,
    who: Iterable[str] = (),
    pov: str | None = None,
    reveals: Iterable[Reveal] = (),
    position: int = 0,
    sets_up: Iterable[str] = (),
    pays_off: Iterable[str] = (),
    details: dict[str, str] | None = None,
) -> PlanItem:
    refs = [PlanRef(entity_id=item, role="participant") for item in who]
    if place:
        refs.append(PlanRef(entity_id=place, role="place"))
    if pov:
        refs.append(PlanRef(entity_id=pov, role="point_of_view"))
    kept = dict(details or {})
    if when is not None:
        kept = with_scene_time(kept, when)
    if reveals:
        kept = with_reveals(kept, list(reveals))
    return PlanItem(
        id=id_,
        kind=PlanKind.SCENE,
        title=id_.replace("_", " ").title(),
        refs=refs,
        details=kept,
        position=position,
        sets_up=list(sets_up),
        pays_off=list(pays_off),
        provenance_id="eval",
        created_revision=1,
        **SCOPE,
    )


def thread(id_: str, title: str, position: int = 0) -> PlanItem:
    return PlanItem(
        id=id_,
        kind=PlanKind.THREAD,
        title=title,
        position=position,
        provenance_id="eval",
        created_revision=1,
        **SCOPE,
    )
