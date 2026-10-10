"""Entity authoring over the atomic canonical mutation path.

Every product write builds a typed operation, commits it through the
``MutationService`` (journal + graph writer), and returns the accepted canon
entity. Reads project the canon writer's scoped view. Names are not identity;
identical ids/names in different projects never collide.
"""

from __future__ import annotations

from backend.adapters.system import uuid7
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.marks import details, needs_detail
from backend.domain.canon.naming import MAX_SLUG_LENGTH, slugify
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.operations import CreateEntityOp, RetireEntityOp, UpdateEntityOp
from backend.domain.tenancy import Permission
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.mutation_service import CommitResult, MutationService


class CanonEntityService:
    def __init__(
        self,
        *,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        schema_store: SchemaVersionStorePort | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
    ) -> None:
        self._mutation = mutation
        self._reader = reader
        self._schema_store = schema_store
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    async def _published_schema_id(self, workspace_id: str, project_id: str) -> str | None:
        if self._schema_store is None:
            return None
        published = await self._schema_store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        if published is None:
            raise ValidationError("No published schema version exists for this project.")
        return published.id

    async def get(
        self, scope: AccessScope, workspace_id: str, project_id: str, entity_id: str
    ) -> CanonEntity:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None:
            raise NotFoundError("Entity not found.")
        return entity

    async def list_entities(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CanonEntity]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_entities(
            workspace_id=workspace_id,
            project_id=project_id,
            entity_type=entity_type,
            include_retired=include_retired,
            limit=limit,
            offset=offset,
        )

    async def needs_detail(
        self, scope: AccessScope, workspace_id: str, project_id: str, *, limit: int = 50
    ) -> list[CanonEntity]:
        """Active entries still to be filled in.

        Either marked so (created from a passing mention, by the assistant or by
        hand), or only a name so far: no details and nothing stated about them.
        The second kind is derived on every call, so such an entry leaves the list
        as soon as it gains a detail or a fact of its own.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        bare: list[CanonEntity] = []
        offset = 0
        while len(bare) < limit:
            page = await self._reader.list_entities(
                workspace_id=workspace_id, project_id=project_id, limit=200, offset=offset
            )
            for entity in page:
                if needs_detail(entity.attributes):
                    bare.append(entity)
                    if len(bare) >= limit:
                        break
                    continue
                if details(entity.attributes):
                    continue
                stated = await self._reader.list_facts(
                    workspace_id=workspace_id, project_id=project_id, subject_id=entity.id, limit=1
                )
                if not stated:
                    bare.append(entity)
                    if len(bare) >= limit:
                        break
            if len(page) < 200:
                break
            offset += 200
        return bare

    async def search(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        query: str,
        *,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
    ) -> list[CanonEntity]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.search_entities(
            workspace_id=workspace_id,
            project_id=project_id,
            query=query,
            entity_type=entity_type,
            include_retired=include_retired,
            limit=limit,
        )

    async def create(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_type: str,
        name: str,
        attributes: dict | None = None,
        expected_revision: int,
        idempotency_key: str = "",
        slug: str | None = None,
        aliases: list[str] | None = None,
        external_ids: dict[str, str] | None = None,
    ) -> tuple[CommitResult, CanonEntity]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        schema_version_id = await self._published_schema_id(workspace_id, project_id)
        entity_id = format_canon_id("entity", str(uuid7()))
        if slug is None:
            slug = await self._available_slug(workspace_id, project_id, name)
        operation = CreateEntityOp(
            id=entity_id,
            type=entity_type,
            name=name,
            slug=slug,
            attributes=attributes or {},
            aliases=aliases or [],
            external_ids=external_ids or {},
        )
        result = await self._commit(
            scope,
            workspace_id,
            project_id,
            [operation],
            expected_revision,
            idempotency_key,
            schema_version_id,
        )
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        assert entity is not None
        return result, entity

    async def edit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        *,
        name: str | None = None,
        slug: str | None = None,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        external_ids: dict[str, str] | None = None,
        expected_revision: int,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, CanonEntity]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await self._require_active(workspace_id, project_id, entity_id)
        schema_version_id = await self._published_schema_id(workspace_id, project_id)
        operation = UpdateEntityOp(
            id=entity_id,
            name=name,
            slug=slug,
            attributes=attributes,
            aliases=aliases,
            external_ids=external_ids,
        )
        result = await self._commit(
            scope,
            workspace_id,
            project_id,
            [operation],
            expected_revision,
            idempotency_key,
            schema_version_id,
        )
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        assert entity is not None
        return result, entity

    async def retire(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        *,
        expected_revision: int,
        idempotency_key: str = "",
        acknowledge_high_impact: bool = False,
    ) -> tuple[CommitResult, CanonEntity]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await self._require_active(workspace_id, project_id, entity_id)
        schema_version_id = await self._published_schema_id(workspace_id, project_id)
        operation = RetireEntityOp(id=entity_id)
        result = await self._commit(
            scope,
            workspace_id,
            project_id,
            [operation],
            expected_revision,
            idempotency_key,
            schema_version_id,
            acknowledge_high_impact=acknowledge_high_impact,
        )
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        assert entity is not None
        return result, entity

    async def get_by_external_id(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        namespace: str,
        value: str,
    ) -> CanonEntity:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        entity = await self._reader.get_entity_by_external_id(
            workspace_id=workspace_id, project_id=project_id, namespace=namespace, value=value
        )
        if entity is None:
            raise NotFoundError("Entity not found.")
        return entity

    async def name_collisions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_type: str,
        name: str,
    ) -> list[CanonEntity]:
        """Active same-type entities sharing a display name (advisory, never blocking)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.find_entities_by_name(
            workspace_id=workspace_id, project_id=project_id, entity_type=entity_type, name=name
        )

    async def _available_slug(self, workspace_id: str, project_id: str, name: str) -> str | None:
        """Derive a free slug from the name; the commit transaction still enforces uniqueness."""
        base = slugify(name)
        if not base:
            return None
        candidate = base
        for suffix in range(2, 1000):
            holder = await self._reader.get_entity_by_slug(
                workspace_id=workspace_id, project_id=project_id, slug=candidate
            )
            if holder is None:
                return candidate
            tail = f"-{suffix}"
            candidate = f"{base[: MAX_SLUG_LENGTH - len(tail)].rstrip('-')}{tail}"
        return None

    async def _require_active(self, workspace_id: str, project_id: str, entity_id: str) -> None:
        existing = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if existing is None:
            raise NotFoundError("Entity not found.")

    async def _commit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        operations: list,
        expected_revision: int,
        idempotency_key: str,
        schema_version_id: str | None,
        *,
        acknowledge_high_impact: bool = False,
    ) -> CommitResult:
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            idempotency_key=idempotency_key,
            schema_version_id=schema_version_id,
        )
        return await self._mutation.commit_author_change(
            scope, mutation.id, acknowledge_high_impact=acknowledge_high_impact
        )


__all__ = ["CanonEntityService"]
