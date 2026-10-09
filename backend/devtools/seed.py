"""Idempotently seed the deterministic reference world.

The legacy graph manifest and the canonical reference world are always seeded;
canon is committed through the mutation service, so the seed exercises the same
path product writes use. Derived vector documents are only indexed with
``--vectors`` so the default command never downloads an embedding model.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.devtools.guard import ensure_seed_allowed
from backend.graph.manager import GraphManager
from backend.seed.apply import apply_manifest
from backend.seed.canon import SEED_PROJECTS, CanonSeedReport, apply_canon_seed
from backend.seed.ledger import record_seed
from backend.seed.schema import apply_schema
from backend.seed.tenancy import apply_tenancy
from backend.seed.world import REFERENCE_WORLD


async def _seed_canon(settings: Settings) -> CanonSeedReport:
    """Commit the canonical reference world through the real mutation path."""
    from backend.adapters.canon_repository import PostgresCanonRepository
    from backend.adapters.journal import PostgresCanonJournal
    from backend.adapters.media_store import PostgresMediaStore
    from backend.adapters.objects import FilesystemObjectStore
    from backend.adapters.schema_store import PostgresSchemaVersionStore
    from backend.core.database import create_engine, create_session_factory

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        return await apply_canon_seed(
            journal=PostgresCanonJournal(factory),
            repository=PostgresCanonRepository(factory),
            schemas=PostgresSchemaVersionStore(factory),
            media=PostgresMediaStore(factory),
            objects=FilesystemObjectStore(settings.OBJECT_STORE_DIR),
        )
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.seed", description="Seed reference world."
    )
    parser.add_argument(
        "--vectors",
        action="store_true",
        help="Also index prose snippets into pgvector (may load the embedding model).",
    )
    args = parser.parse_args(argv)

    settings = default_settings
    try:
        ensure_seed_allowed(settings)
    except Exception as error:
        print(f"seed refused: {error}")
        return 2

    manager = GraphManager(settings)
    vectors = None
    embeddings = None
    if args.vectors:
        from backend.adapters.embedding import SentenceTransformerEmbedding
        from backend.adapters.vector import PgVectorStore

        embeddings = SentenceTransformerEmbedding(
            model_name=settings.EMBEDDING_MODEL_NAME,
            dimension=settings.EMBEDDING_DIMENSION,
        )
        vectors = PgVectorStore(settings.postgres_dsn_psycopg, embeddings)

    try:
        from backend.adapters.graph import Neo4jGraphRepository

        report = apply_manifest(
            Neo4jGraphRepository(manager),
            REFERENCE_WORLD,
            vectors=vectors,
            embeddings=embeddings,
        )
    finally:
        manager.close()

    record_seed(
        settings.postgres_dsn_psycopg,
        version=report.version,
        checksum=report.checksum,
        counts=report.counts,
    )

    tenancy = apply_tenancy(settings.postgres_dsn_psycopg)
    schema_rows = sum(
        apply_schema(
            settings.postgres_dsn_psycopg,
            workspace_id=project.workspace_id,
            project_id=project.project_id,
        )
        for project in SEED_PROJECTS
    )
    canon = asyncio.run(_seed_canon(settings))

    print(f"seed version: {report.version}")
    print(f"seed checksum: {report.checksum}")
    print(f"applied nodes: {report.applied_nodes}")
    print(f"applied edges: {report.applied_edges}")
    print(f"identity/tenancy: inserted {tenancy.inserted} rows ({tenancy.counts})")
    print(f"project schema: published {schema_rows} version(s)")
    print(f"canon seed version: {canon.version}")
    print(f"canon seed checksum: {canon.checksum}")
    print(f"canon heads: {canon.heads}")
    if args.vectors:
        print(f"indexed documents: {report.indexed_documents} (skipped {report.skipped_documents})")
    else:
        print("indexed documents: not requested (pass --vectors to index prose snippets)")
    problems = [*report.problems, *canon.problems]
    if problems:
        print(f"seed verification FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("seed verification: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
