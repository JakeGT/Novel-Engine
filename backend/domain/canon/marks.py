"""Marks on an entry that help an author find and rank it, kept beside its details.

Three things are stored under reserved keys in an entry's attributes:

- tags: short phrases that tell entries apart at a glance ("protagonist",
  "John's professor");
- importance: how central the entry is to the story, which orders lists;
- needs detail: set when an entry was created from a passing mention, so it is
  offered for filling in later.

They are the author's (or the assistant's, subject to review) and are changed like
any other detail, through reviewed canon operations. The keys start with an
underscore, which a project-defined field name cannot, so they never collide with
a project's own fields; they are set aside before an entry's details are checked
against its type.
"""

from __future__ import annotations

from typing import Any

TAGS_KEY = "_tags"
IMPORTANCE_KEY = "_importance"
NEEDS_DETAIL_KEY = "_needs_detail"
MARK_KEYS = frozenset({TAGS_KEY, IMPORTANCE_KEY, NEEDS_DETAIL_KEY})

# Most to least central. Lists show entries in this order.
IMPORTANCE_LEVELS = ("main", "supporting", "minor", "background")

MAX_TAGS = 6
MAX_TAG_LENGTH = 40


def details(attributes: dict[str, Any]) -> dict[str, Any]:
    """An entry's own details, without the marks."""
    return {key: value for key, value in attributes.items() if key not in MARK_KEYS}


def clean_tags(tags: list[Any]) -> list[str]:
    """Short, distinct, tidy tags; anything else is dropped."""
    found: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        text = " ".join(tag.split()).strip(" .,;:")
        if not text or len(text) > MAX_TAG_LENGTH or text.lower() in seen:
            continue
        seen.add(text.lower())
        found.append(text)
        if len(found) >= MAX_TAGS:
            break
    return found


def clean_importance(value: Any) -> str | None:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in IMPORTANCE_LEVELS else None


def marks(
    *, tags: list[Any] | None = None, importance: Any = None, needs_detail: bool = False
) -> dict[str, Any]:
    """The reserved attributes for these marks; absent ones are left out entirely."""
    found: dict[str, Any] = {}
    cleaned = clean_tags(tags or [])
    if cleaned:
        found[TAGS_KEY] = cleaned
    level = clean_importance(importance)
    if level:
        found[IMPORTANCE_KEY] = level
    if needs_detail:
        found[NEEDS_DETAIL_KEY] = True
    return found


def needs_detail(attributes: dict[str, Any]) -> bool:
    return attributes.get(NEEDS_DETAIL_KEY) is True
