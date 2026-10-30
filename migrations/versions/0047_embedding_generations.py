"""embedding generations: versioned vector spaces beside each other

Revision ID: 0047_embedding_generations
Revises: 0046_media_variants
Create Date: 2026-10-18

CTX-011 / CTX-012 (ADR-010 amendment 2026-10-18). Replaces the fixed ``vector(384)``
table with generation-versioned vectors so a new model can be built beside the one in
use, switched to in one step, and switched back.

- ``embedding_generations``: one row per vector space: model, pinned revision,
  dimension, the task prompts it was built with, normalization, and a state
  (``building`` / ``active`` / ``retired``). At most one is active and at most one is
  being built at a time (partial unique indexes).
- ``embedding_vectors``: every derived vector (prose passages, captions, picture
  segments), keyed by generation. The ``embedding`` column is the untyped ``vector``
  type, so generations of different sizes share the table; each generation gets its
  own partial HNSW index on ``(embedding::vector(N))`` ``WHERE generation_id = ...``,
  which is the pgvector README's recipe for indexing several dimensions in one table.
  A trigger refuses a row whose size differs from its generation's, and refuses
  writes into a retired generation, so spaces cannot mix even through a bug.

Existing ``vector_documents`` rows move across without being embedded again: rows are
grouped by the model and version they recorded; each group becomes a generation of its
own (rows with no recorded model form an ``unknown`` group). The largest named group
becomes active (the default MiniLM when tied); the others are kept retired so nothing
is lost and nothing from a different model is compared with it. ``vector_documents``
is then dropped so deletion cannot miss a second copy.

Downgrade rebuilds ``vector_documents`` from the active generation when it holds
384-dimensional vectors (otherwise from the newest 384-dimensional generation, else
empty) and drops the new tables. Vectors of other sizes are lost on downgrade.

Chained after 0046_media_variants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_embedding_generations"
down_revision: str | None = "0046_media_variants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_DIMENSION = 384
DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_REVISION = "1"
# MiniLM takes no task prompts: a query and a document are embedded the same way.
PLAIN_TASKS = '{"document": "{text}", "query": "{text}"}'


def _space_key(model: str, revision_: str, dimension: int, task_set: str) -> str:
    return f"{model}@{revision_}/{dimension}/{task_set}"


def _hnsw(generation_id: str, dimension: int) -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS embedding_vectors_hnsw_{generation_id} "
        f"ON embedding_vectors USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) "
        f"WHERE generation_id = '{generation_id}'"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_generations (
            id TEXT PRIMARY KEY CHECK (id ~ '^gen_[a-z0-9_]{1,36}$'),
            model TEXT NOT NULL,
            revision TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK (dimension BETWEEN 1 AND 2000),
            native_dimension INTEGER NOT NULL,
            modalities TEXT[] NOT NULL,
            tasks JSONB NOT NULL DEFAULT '{}'::jsonb,
            task_set TEXT NOT NULL,
            normalization TEXT NOT NULL CHECK (normalization IN ('l2', 'none', 'unknown')),
            space_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('building', 'active', 'retired')),
            previous_generation_id TEXT
                REFERENCES embedding_generations (id) ON DELETE SET NULL,
            cancel_requested BOOLEAN NOT NULL DEFAULT false,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            activated_at TIMESTAMPTZ,
            retired_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS embedding_generations_one_active "
        "ON embedding_generations (state) WHERE state = 'active'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS embedding_generations_one_building "
        "ON embedding_generations (state) WHERE state = 'building'"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_vectors (
            generation_id TEXT NOT NULL
                REFERENCES embedding_generations (id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            modality TEXT NOT NULL CHECK (modality IN ('text', 'image', 'audio', 'video')),
            task TEXT NOT NULL,
            document TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            asset_id TEXT,
            asset_version_id TEXT,
            segment_id TEXT,
            content_hash TEXT NOT NULL,
            embedding vector NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (generation_id, workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS embedding_vectors_scope_idx "
        "ON embedding_vectors (workspace_id, project_id, generation_id, kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS embedding_vectors_asset_idx "
        "ON embedding_vectors (workspace_id, project_id, asset_id) WHERE asset_id IS NOT NULL"
    )

    bind = op.get_bind()
    has_legacy = bind.execute(
        sa.text("SELECT to_regclass('vector_documents') IS NOT NULL")
    ).scalar()
    groups: list[tuple[str, str, int]] = []
    if has_legacy:
        groups = [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in bind.execute(
                sa.text(
                    "SELECT COALESCE(embedding_model, ''), COALESCE(embedding_version, ''), "
                    "count(*) FROM vector_documents GROUP BY 1, 2"
                )
            )
        ]
    named = [group for group in groups if group[0]]
    if named:
        active = max(named, key=lambda group: (group[2], group[0] == DEFAULT_MODEL))
    else:
        active = (DEFAULT_MODEL, DEFAULT_REVISION, 0)
        if not any(group[:2] == active[:2] for group in groups):
            groups.append(active)
    for number, (model, version, _count) in enumerate(
        sorted(groups, key=lambda group: (group != active, group[0], group[1])), start=1
    ):
        generation_id = f"gen_legacy_{number}"
        unknown = not model
        model_name = model or "unknown"
        revision_ = version or "unknown"
        bind.execute(
            sa.text(
                "INSERT INTO embedding_generations (id, model, revision, dimension, "
                "native_dimension, modalities, tasks, task_set, normalization, space_key, "
                "state, activated_at, retired_at, note) VALUES (:id, :model, :revision, :dim, "
                ":dim, ARRAY['text'], CAST(:tasks AS JSONB), 'plain', :norm, :key, :state, "
                "CASE WHEN CAST(:state AS TEXT) = 'active' THEN now() END, "
                "CASE WHEN CAST(:state AS TEXT) = 'retired' THEN now() END, :note)"
            ),
            {
                "id": generation_id,
                "model": model_name,
                "revision": revision_,
                "dim": LEGACY_DIMENSION,
                "tasks": PLAIN_TASKS,
                # Cosine distance ignores length, so whether the old rows were
                # normalized does not change their ranking; it is recorded as unknown
                # unless the row's model is MiniLM, which normalizes its own output.
                "norm": "l2" if model_name == DEFAULT_MODEL else "unknown",
                "key": _space_key(model_name, revision_, LEGACY_DIMENSION, "plain"),
                "state": "active" if (model, version) == active[:2] else "retired",
                "note": (
                    "Moved from vector_documents without embedding again"
                    + ("; the model that made these rows was not recorded." if unknown else ".")
                ),
            },
        )
        if has_legacy:
            bind.execute(
                sa.text(
                    "INSERT INTO embedding_vectors (generation_id, workspace_id, project_id, id, "
                    "kind, modality, task, document, metadata, content_hash, embedding) "
                    "SELECT :id, workspace_id, project_id, id, "
                    "COALESCE(NULLIF(metadata->>'kind', ''), 'document'), 'text', 'document', "
                    "document, metadata, encode(sha256(convert_to(document, 'UTF8')), 'hex'), "
                    "embedding::vector FROM vector_documents "
                    "WHERE COALESCE(embedding_model, '') = :model "
                    "AND COALESCE(embedding_version, '') = :version"
                ),
                {"id": generation_id, "model": model, "version": version},
            )
        op.execute(_hnsw(generation_id, LEGACY_DIMENSION))

    op.execute(
        """
        CREATE OR REPLACE FUNCTION embedding_vectors_same_space() RETURNS trigger AS $$
        DECLARE
            expected INTEGER;
            current_state TEXT;
        BEGIN
            SELECT dimension, state INTO expected, current_state
              FROM embedding_generations WHERE id = NEW.generation_id;
            IF expected IS NULL THEN
                RAISE EXCEPTION 'unknown embedding generation %', NEW.generation_id;
            END IF;
            IF current_state = 'retired' THEN
                RAISE EXCEPTION 'embedding generation % is retired', NEW.generation_id;
            END IF;
            IF vector_dims(NEW.embedding) <> expected THEN
                RAISE EXCEPTION 'vector has % dimensions; generation % holds %',
                    vector_dims(NEW.embedding), NEW.generation_id, expected;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER embedding_vectors_same_space BEFORE INSERT OR UPDATE "
        "ON embedding_vectors FOR EACH ROW EXECUTE FUNCTION embedding_vectors_same_space()"
    )
    op.execute("DROP TABLE IF EXISTS vector_documents")


def downgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS vector_documents (
            id TEXT NOT NULL,
            document TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            embedding vector({LEGACY_DIMENSION}) NOT NULL,
            embedding_model TEXT,
            embedding_version TEXT,
            embedding_dimension INTEGER,
            workspace_id TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS vector_documents_embedding_idx "
        "ON vector_documents USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS vector_documents_scope_idx "
        "ON vector_documents (workspace_id, project_id)"
    )
    op.execute(
        f"""
        INSERT INTO vector_documents (id, document, metadata, embedding, embedding_model,
            embedding_version, embedding_dimension, workspace_id, project_id)
        SELECT v.id, COALESCE(v.document, ''), v.metadata, v.embedding::vector({LEGACY_DIMENSION}),
               g.model, g.revision, g.dimension, v.workspace_id, v.project_id
        FROM embedding_vectors v JOIN embedding_generations g ON g.id = v.generation_id
        WHERE v.modality = 'text' AND v.generation_id = (
            SELECT id FROM embedding_generations WHERE dimension = {LEGACY_DIMENSION}
            ORDER BY (state = 'active') DESC, created_at DESC LIMIT 1
        )
        ON CONFLICT DO NOTHING
        """
    )
    op.execute("DROP TABLE IF EXISTS embedding_vectors")
    op.execute("DROP FUNCTION IF EXISTS embedding_vectors_same_space()")
    op.execute("DROP TABLE IF EXISTS embedding_generations")
