"""Entity naming policy: display names, slugs, aliases, and external identifiers.

Identity is the opaque entity id. Everything here is a *handle*:

* A display name is free text. Two entities may share one; a same-type duplicate
  among active entities is reported as an advisory finding, never a rejection.
* A slug is a stable, URL-safe handle, unique among the *active* entities of a
  project branch. Retiring an entity frees its slug.
* Aliases are alternative names, bounded and de-duplicated case-insensitively.
  They never collide-block, because many characters can share an epithet.
* An external identifier binds the entity to a record in another system. A
  ``(namespace, value)`` pair resolves to at most one entity per project, active
  or retired, so imports can de-duplicate deterministically.
"""

from __future__ import annotations

import re
import unicodedata

MAX_NAME_LENGTH = 300
MAX_SLUG_LENGTH = 100
MAX_ALIASES = 20
MAX_EXTERNAL_IDS = 20
MAX_EXTERNAL_ID_VALUE_LENGTH = 200

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTERNAL_NAMESPACE = re.compile(r"^[a-z][a-z0-9_.\-]{0,49}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalize_name(value: str) -> str:
    """Case- and whitespace-insensitive comparison key for names and aliases."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def name_problem(name: str) -> str | None:
    if not name.strip():
        return "name is required"
    if len(name) > MAX_NAME_LENGTH:
        return f"name exceeds {MAX_NAME_LENGTH} characters"
    if _CONTROL.search(name):
        return "name contains control characters"
    return None


def slug_problem(slug: str) -> str | None:
    if len(slug) > MAX_SLUG_LENGTH:
        return f"slug exceeds {MAX_SLUG_LENGTH} characters"
    if not _SLUG.match(slug):
        return "slug must be lowercase letters, digits, and single hyphens"
    return None


def slugify(name: str) -> str:
    """Derive a candidate slug from a display name ("" when nothing usable remains)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-")


def aliases_problem(aliases: list[str], *, name: str) -> str | None:
    if len(aliases) > MAX_ALIASES:
        return f"at most {MAX_ALIASES} aliases are allowed"
    seen = {normalize_name(name)}
    for alias in aliases:
        problem = name_problem(alias)
        if problem is not None:
            return f"alias {problem}"
        key = normalize_name(alias)
        if key in seen:
            return f"alias {alias!r} duplicates the name or another alias"
        seen.add(key)
    return None


def external_ids_problem(external_ids: dict[str, str]) -> str | None:
    if len(external_ids) > MAX_EXTERNAL_IDS:
        return f"at most {MAX_EXTERNAL_IDS} external identifiers are allowed"
    for namespace, value in external_ids.items():
        if not _EXTERNAL_NAMESPACE.match(namespace):
            return f"external identifier namespace {namespace!r} is invalid"
        if not value or len(value) > MAX_EXTERNAL_ID_VALUE_LENGTH or _CONTROL.search(value):
            return f"external identifier for {namespace!r} is empty, too long, or malformed"
        if value != value.strip():
            return f"external identifier for {namespace!r} has surrounding whitespace"
    return None


__all__ = [
    "MAX_ALIASES",
    "MAX_EXTERNAL_IDS",
    "MAX_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "aliases_problem",
    "external_ids_problem",
    "name_problem",
    "normalize_name",
    "slug_problem",
    "slugify",
]
