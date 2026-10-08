import pytest

from backend.domain.identity import AccessScope, AuthMode
from backend.domain.schema_defs import EntityExtension, FieldDef, FieldType, SchemaDefinition
from backend.domain.schemas import SchemaCompatibilityError, SchemaVersionStatus
from backend.services.schema_service import SchemaService
from tests.doubles import InMemorySchemaVersionStore

pytestmark = pytest.mark.application


def _definition(*, required: bool = True) -> SchemaDefinition:
    return SchemaDefinition(
        label="Ship extension",
        entity_extensions=[
            EntityExtension(
                entity_type="Ship",
                fields=[
                    FieldDef(name="hull", field_type=FieldType.STRING, required=required),
                    FieldDef(name="crew", field_type=FieldType.INTEGER),
                ],
            )
        ],
    )


def _scope(project: str) -> AccessScope:
    return AccessScope(
        actor_user_id="u1",
        auth_mode=AuthMode.LOCAL,
        workspace_id="ws",
        project_id=project,
    )


@pytest.mark.asyncio
async def test_draft_publish_and_isolation() -> None:
    store = InMemorySchemaVersionStore()
    service = SchemaService(store, local_mode=True)
    scope_a = _scope("prj_a")
    scope_b = _scope("prj_b")

    version, report = await service.create_draft(scope_a, _definition())
    assert report.category.value == "additive"
    assert version.status is SchemaVersionStatus.DRAFT

    published = await service.publish(scope_a, version.id)
    assert published.status is SchemaVersionStatus.PUBLISHED
    assert published.published_at is not None

    assert len(await service.list_versions(scope_a)) == 1
    assert await service.list_versions(scope_b) == []


@pytest.mark.asyncio
async def test_forbidden_change_cannot_be_published() -> None:
    store = InMemorySchemaVersionStore()
    service = SchemaService(store, local_mode=True)
    scope = _scope("prj_a")

    first, _ = await service.create_draft(scope, _definition())
    await service.publish(scope, first.id)

    # Removing the `crew` field relative to the published version is forbidden.
    removed = SchemaDefinition(
        label="Ship extension",
        entity_extensions=[
            EntityExtension(
                entity_type="Ship",
                fields=[FieldDef(name="hull", field_type=FieldType.STRING, required=True)],
            )
        ],
    )
    third, report = await service.create_draft(scope, removed)
    assert report.category.value == "forbidden"
    with pytest.raises(SchemaCompatibilityError):
        await service.publish(scope, third.id)


@pytest.mark.asyncio
async def test_validate_returns_report() -> None:
    service = SchemaService(InMemorySchemaVersionStore(), local_mode=True)
    report = service.validate(_definition())
    assert report.valid is True
    assert report.content_hash
    assert report.entity_types == ["Ship"]
