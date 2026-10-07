import pytest

from backend.graph.migrations import (
    INITIAL_GRAPH_SCHEMA,
    Migration,
    MigrationDriftError,
    Neo4jMigrationRunner,
    checksum,
    plan_migrations,
)

pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, records: list[dict[str, str]]) -> None:
        self._records = records

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    def __init__(self, driver: "_FakeDriver") -> None:
        self._driver = driver

    def run(self, query: str, **params: str) -> _FakeResult:
        self._driver.statements.append((query, params))
        if query.startswith("MATCH") and "RETURN m.id" in query:
            return _FakeResult(
                [{"id": key, "checksum": value} for key, value in self._driver.ledger.items()]
            )
        return _FakeResult([])

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _FakeDriver:
    def __init__(self, ledger: dict[str, str] | None = None) -> None:
        self.ledger = dict(ledger or {})
        self.statements: list[tuple[str, dict[str, str]]] = []

    def session(self) -> _FakeSession:
        return _FakeSession(self)


def test_checksum_is_stable_and_content_sensitive() -> None:
    first = Migration(id="0001", statements=("RETURN 1",))
    same = Migration(id="0001", statements=("RETURN 1",))
    changed = Migration(id="0001", statements=("RETURN 2",))

    assert checksum(first) == checksum(same)
    assert checksum(first) != checksum(changed)


def test_plan_reports_pending_when_nothing_applied() -> None:
    plan = plan_migrations([INITIAL_GRAPH_SCHEMA], {})

    assert plan.pending == (INITIAL_GRAPH_SCHEMA,)
    assert not plan.is_current


def test_plan_is_current_when_checksums_match() -> None:
    applied = {INITIAL_GRAPH_SCHEMA.id: checksum(INITIAL_GRAPH_SCHEMA)}

    plan = plan_migrations([INITIAL_GRAPH_SCHEMA], applied)

    assert plan.is_current
    assert plan.applied_current == (INITIAL_GRAPH_SCHEMA.id,)


def test_plan_reports_drift_when_checksum_differs() -> None:
    plan = plan_migrations([INITIAL_GRAPH_SCHEMA], {INITIAL_GRAPH_SCHEMA.id: "deadbeef"})

    assert plan.drift == (INITIAL_GRAPH_SCHEMA.id,)


def test_plan_reports_unknown_applied_migration_as_drift() -> None:
    plan = plan_migrations([INITIAL_GRAPH_SCHEMA], {"9999_ghost": "abc"})

    assert "9999_ghost" in plan.drift


def test_initial_migration_versions_the_embedding_index() -> None:
    joined = " ".join(INITIAL_GRAPH_SCHEMA.statements)

    assert "vector.dimensions`: 384" in joined
    assert "cosine" in joined
    assert "all-MiniLM-L6-v2" in INITIAL_GRAPH_SCHEMA.description


def test_entity_scope_migration_replaces_global_id_constraint() -> None:
    from backend.graph.migrations import ENTITY_SCOPE_IDENTITY, NEO4J_MIGRATIONS

    joined = " ".join(ENTITY_SCOPE_IDENTITY.statements)
    assert "DROP CONSTRAINT entity_id_unique" in joined
    assert "REQUIRE (n.workspace_id, n.project_id, n.id) IS UNIQUE" in joined
    assert [migration.id for migration in NEO4J_MIGRATIONS] == [
        "0001_initial_graph_schema",
        "0002_entity_scope_identity",
    ]


def test_runner_applies_pending_and_records_ledger() -> None:
    driver = _FakeDriver()
    runner = Neo4jMigrationRunner(driver, [INITIAL_GRAPH_SCHEMA], clock=lambda: "t0")

    applied = runner.run()

    assert applied == [INITIAL_GRAPH_SCHEMA.id]
    executed = [query for query, _ in driver.statements]
    assert any("CREATE CONSTRAINT entity_id_unique" in query for query in executed)
    assert any("CREATE VECTOR INDEX entity_embeddings" in query for query in executed)
    assert any(query.startswith("MERGE (m:_SchemaMigration") for query in executed)


def test_runner_is_idempotent_when_checksums_match() -> None:
    driver = _FakeDriver({INITIAL_GRAPH_SCHEMA.id: checksum(INITIAL_GRAPH_SCHEMA)})
    runner = Neo4jMigrationRunner(driver, [INITIAL_GRAPH_SCHEMA], clock=lambda: "t0")

    assert runner.run() == []


def test_runner_raises_on_drift() -> None:
    driver = _FakeDriver({INITIAL_GRAPH_SCHEMA.id: "deadbeef"})
    runner = Neo4jMigrationRunner(driver, [INITIAL_GRAPH_SCHEMA], clock=lambda: "t0")

    with pytest.raises(MigrationDriftError):
        runner.run()


def test_verify_is_read_only_and_never_creates_schema() -> None:
    driver = _FakeDriver()
    runner = Neo4jMigrationRunner(driver, [INITIAL_GRAPH_SCHEMA], clock=lambda: "t0")

    plan = runner.verify()

    assert plan.pending == (INITIAL_GRAPH_SCHEMA,)
    executed = " ".join(query for query, _ in driver.statements)
    assert "CREATE" not in executed
    assert not any(query.lstrip().startswith("MERGE") for query, _ in driver.statements)
