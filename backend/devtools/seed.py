"""Idempotently seed the deterministic reference world.

Graph entities/edges are always seeded. Derived vector documents are only
indexed with ``--vectors`` so the default command never downloads an embedding
model.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from backend.core.config import settings as default_settings
from backend.devtools.guard import ensure_seed_allowed
from backend.graph.manager import GraphManager
from backend.seed.apply import apply_manifest
from backend.seed.ledger import record_seed
from backend.seed.world import REFERENCE_WORLD


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

    print(f"seed version: {report.version}")
    print(f"seed checksum: {report.checksum}")
    print(f"applied nodes: {report.applied_nodes}")
    print(f"applied edges: {report.applied_edges}")
    if args.vectors:
        print(f"indexed documents: {report.indexed_documents} (skipped {report.skipped_documents})")
    else:
        print("indexed documents: not requested (pass --vectors to index prose snippets)")
    if report.problems:
        print(f"seed verification FAILED with {len(report.problems)} problem(s):")
        for problem in report.problems:
            print(f"  - {problem}")
        return 1

    print("seed verification: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
