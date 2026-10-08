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
    "project": "prj",
    "workspace": "wsp",
}

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
