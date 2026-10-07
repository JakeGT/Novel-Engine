from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from neo4j import Driver

LEDGER_LABEL = "_SchemaMigration"
ENSURE_LEDGER = (
    f"CREATE CONSTRAINT schema_migration_id IF NOT EXISTS "
    f"FOR (m:{LEDGER_LABEL}) REQUIRE m.id IS UNIQUE"
)
READ_LEDGER = f"MATCH (m:{LEDGER_LABEL}) RETURN m.id AS id, m.checksum AS checksum"
RECORD_LEDGER = (
    f"MERGE (m:{LEDGER_LABEL} {{id: $id}}) SET m.checksum = $checksum, m.applied_at = $applied_at"
)

EMBEDDING_DIMENSION = 384


@dataclass(frozen=True)
class Migration:
    id: str
    statements: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class MigrationPlan:
    applied_current: tuple[str, ...]
    pending: tuple[Migration, ...]
    drift: tuple[str, ...]

    @property
    def is_current(self) -> bool:
        return not self.pending and not self.drift


class MigrationDriftError(RuntimeError):
    """Raised when a recorded migration checksum no longer matches its definition."""


def checksum(migration: Migration) -> str:
    digest = hashlib.sha256()
    digest.update(migration.id.encode("utf-8"))
    for statement in migration.statements:
        digest.update(b"\x00")
        digest.update(statement.encode("utf-8"))
    return digest.hexdigest()


def plan_migrations(defined: Sequence[Migration], applied: dict[str, str]) -> MigrationPlan:
    defined_ids = {migration.id for migration in defined}
    applied_current: list[str] = []
    pending: list[Migration] = []
    drift: list[str] = []

    for migration in defined:
        recorded = applied.get(migration.id)
        if recorded is None:
            pending.append(migration)
        elif recorded != checksum(migration):
            drift.append(migration.id)
        else:
            applied_current.append(migration.id)

    drift.extend(sorted(mid for mid in applied if mid not in defined_ids))

    return MigrationPlan(tuple(applied_current), tuple(pending), tuple(drift))


INITIAL_GRAPH_SCHEMA = Migration(
    id="0001_initial_graph_schema",
    description=(
        "Entity uniqueness constraint and versioned embedding vector index "
        f"(all-MiniLM-L6-v2, dimension {EMBEDDING_DIMENSION}, cosine)."
    ),
    statements=(
        "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE VECTOR INDEX entity_embeddings IF NOT EXISTS FOR (n:Entity) "
        f"ON (n.embedding) OPTIONS {{indexConfig: {{`vector.dimensions`: {EMBEDDING_DIMENSION}, "
        "`vector.similarity_function`: 'cosine'}}",
    ),
)

ENTITY_SCOPE_IDENTITY = Migration(
    id="0002_entity_scope_identity",
    description=(
        "Replace the global entity id uniqueness constraint with a composite "
        "(workspace_id, project_id, id) constraint so identical ids in different "
        "projects are distinct nodes."
    ),
    statements=(
        "DROP CONSTRAINT entity_id_unique IF EXISTS",
        "CREATE CONSTRAINT entity_scope_id_unique IF NOT EXISTS FOR (n:Entity) "
        "REQUIRE (n.workspace_id, n.project_id, n.id) IS UNIQUE",
    ),
)

NEO4J_MIGRATIONS: tuple[Migration, ...] = (INITIAL_GRAPH_SCHEMA, ENTITY_SCOPE_IDENTITY)


class Neo4jMigrationRunner:
    def __init__(
        self,
        driver: Driver,
        migrations: Sequence[Migration] = NEO4J_MIGRATIONS,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.driver = driver
        self.migrations = tuple(migrations)
        self._now = clock or (lambda: datetime.now(UTC).isoformat())

    def _ensure_ledger(self) -> None:
        with self.driver.session() as session:
            session.run(ENSURE_LEDGER)

    def applied(self) -> dict[str, str]:
        with self.driver.session() as session:
            result = session.run(READ_LEDGER)
            return {record["id"]: record["checksum"] for record in result}

    def verify(self) -> MigrationPlan:
        """Read-only check. Never creates the ledger or any schema object."""
        return plan_migrations(self.migrations, self.applied())

    def run(self) -> list[str]:
        self._ensure_ledger()
        plan = plan_migrations(self.migrations, self.applied())
        if plan.drift:
            raise MigrationDriftError(
                "Recorded Neo4j migrations do not match the current definitions: "
                + ", ".join(plan.drift)
            )

        applied_now: list[str] = []
        for migration in plan.pending:
            with self.driver.session() as session:
                for statement in migration.statements:
                    session.run(statement)
                session.run(
                    RECORD_LEDGER,
                    id=migration.id,
                    checksum=checksum(migration),
                    applied_at=self._now(),
                )
            applied_now.append(migration.id)
        return applied_now


def verify_neo4j_schema(driver: Driver) -> MigrationPlan:
    return Neo4jMigrationRunner(driver).verify()
