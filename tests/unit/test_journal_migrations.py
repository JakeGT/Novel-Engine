from pathlib import Path

import pytest

from backend.graph.migrations import NEO4J_MIGRATIONS, checksum
from backend.migrate import postgres_head_revision

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_canon_scope_migration_is_registered() -> None:
    ids = [migration.id for migration in NEO4J_MIGRATIONS]
    assert ids == [
        "0001_initial_graph_schema",
        "0002_entity_scope_identity",
        "0003_canon_scope_identity",
        "0004_canon_projection_labels",
    ]
    canon = next(m for m in NEO4J_MIGRATIONS if m.id == "0003_canon_scope_identity")
    joined = " ".join(canon.statements)
    for label in ("Fact", "Belief", "Event", "Provenance", "CommitMarker"):
        assert label in joined
    assert checksum(canon)


def test_postgres_head_is_canon_journal() -> None:
    assert postgres_head_revision() == "0026_assist_suggestions"


def test_graph_layer_never_uses_elementid() -> None:
    for relative in ("graph/manager.py", "adapters/graph.py", "graph/traversal.py"):
        text = (BACKEND / relative).read_text(encoding="utf-8")
        assert "elementId(" not in text, relative
