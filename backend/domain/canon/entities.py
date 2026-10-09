"""Canon entities and lifecycle."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from backend.domain.canon.errors import CrossProjectReferenceError, RetiredReferenceError


class EntityLifecycle(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class CanonEntity(BaseModel):
    """Stable project-scoped entity identity.

    ``attributes`` holds only schema-declared stable attributes; time-varying
    assertions belong in facts, not here.
    """

    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    type: str
    name: str
    slug: str | None = None
    lifecycle: EntityLifecycle = EntityLifecycle.ACTIVE
    schema_version_id: str
    created_revision: int
    retired_revision: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Alternative names the entity is known by. Names and aliases are never identity.
    aliases: list[str] = Field(default_factory=list)
    # Identifiers in external systems, keyed by namespace; unique per project.
    external_ids: dict[str, str] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == EntityLifecycle.ACTIVE


def retire_entity(entity: CanonEntity, *, revision: int) -> CanonEntity:
    """Return a retired copy of ``entity`` at ``revision`` (history is preserved)."""
    return entity.model_copy(
        update={
            "lifecycle": EntityLifecycle.RETIRED,
            "retired_revision": revision,
        }
    )


def ensure_active_reference(
    target: CanonEntity,
    *,
    project_id: str,
    allow_retired: bool = False,
) -> None:
    """Reject new active references to retired or cross-project entities."""
    if target.project_id != project_id:
        raise CrossProjectReferenceError(f"entity {target.id!r} belongs to another project")
    if not target.is_active and not allow_retired:
        raise RetiredReferenceError(f"entity {target.id!r} is retired")
