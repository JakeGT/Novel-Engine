"""Opaque typed canon identifiers.

Identifiers are opaque strings of the form ``<prefix>_<local>``. The prefix names
the kind of record; callers must not parse meaning from the local part. New ids
should be generated with a sortable/uuid token by the application, not here.
"""

from __future__ import annotations

import re

ID_PREFIXES: dict[str, str] = {
    "entity": "ent",
    "predicate": "prd",
    "fact": "fct",
    "belief": "blf",
    "event": "evt",
    "branch": "brn",
    "revision": "rev",
    "mutation": "mut",
    "operation": "opn",
    "schema_version": "scv",
    "provenance": "prv",
    "asset": "ast",
    "asset_version": "asv",
    "asset_link": "alk",
    "project": "prj",
    "workspace": "wsp",
    # A story plan record (story, arc, beat, scene, thread): intent, never fact.
    "plan_item": "pln",
    # Prose an author brought in, and one unchanging version of it. Evidence, not canon.
    "source_text": "src",
    "source_version": "srv",
    # What an author decided a phrase means ("the Fox" is Mara), or does not mean.
    "alias_binding": "alb",
    # An author's own continuity rule (ADR-013).
    "story_invariant": "inv",
    # Reserved for records later phases introduce. No tables exist for these yet;
    # reserving the prefixes keeps today's ids from colliding with them.
    "scene_contract": "sct",
    "narrated_assertion": "nar",
    "speech_act": "spa",
    "disclosure": "dsc",
    "resolution_example": "rex",
    "canon_tag": "tag",
    "finding_suppression": "sup",
    "dependency_edge": "dep",
    "read_model_watermark": "rmw",
}

# Kinds whose prefix is reserved but which have no implementation in this phase.
RESERVED_ID_KINDS: frozenset[str] = frozenset(
    {
        "scene_contract",
        "narrated_assertion",
        "speech_act",
        "disclosure",
        "resolution_example",
        "finding_suppression",
        "dependency_edge",
        "read_model_watermark",
    }
)

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*_[A-Za-z0-9_-]+$")


def format_canon_id(kind: str, local: str) -> str:
    """Format an opaque id for ``kind``. Raises ``KeyError`` for unknown kinds."""
    prefix = ID_PREFIXES[kind]
    if not local or not re.fullmatch(r"[A-Za-z0-9_-]+", local):
        raise ValueError(f"invalid id local part {local!r}")
    return f"{prefix}_{local}"


def is_canon_id(kind: str, value: str) -> bool:
    """Return whether ``value`` is a well-formed id for ``kind``."""
    prefix = ID_PREFIXES.get(kind)
    if prefix is None or not isinstance(value, str):
        return False
    if not _ID_PATTERN.match(value):
        return False
    return value.split("_", 1)[0] == prefix
