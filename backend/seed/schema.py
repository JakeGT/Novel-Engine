"""Seed a published v1 project schema version.

The reference world uses only core canon types and predicates, so the seed
publishes an empty-extension schema version for its project. This removes any
dependency on a mutable process-global schema JSON file: project schema state
lives in PostgreSQL as an immutable published version.
"""

from __future__ import annotations

import json

import psycopg

from backend.domain.schema_defs import SchemaDefinition
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.services.schema_compiler import compute_content_hash

SEED_SCHEMA_LABEL = "Seed core schema"


def apply_schema(
    dsn: str,
    *,
    workspace_id: str = SEED_WORKSPACE_ID,
    project_id: str = SEED_PROJECT_ID,
) -> int:
    """Publish the seed project's core schema version if none exists. Returns rows inserted."""
    model = SchemaDefinition(label=SEED_SCHEMA_LABEL)
    canonical = model.canonical()
    content_hash = compute_content_hash(canonical)
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM schema_versions "
                    "WHERE workspace_id = %s AND project_id = %s AND status = 'published'",
                    (workspace_id, project_id),
                )
                existing = cursor.fetchone()
                if existing is not None and existing[0]:
                    return 0
                cursor.execute(
                    """
                    INSERT INTO schema_versions (
                        id, workspace_id, project_id, version, status,
                        definition, content_hash, compatibility, created_by_user_id, published_at
                    )
                    VALUES (%s, %s, %s, 1, 'published', %s::jsonb, %s, 'additive', NULL, now())
                    """,
                    (
                        f"scv_seed_{project_id}",
                        workspace_id,
                        project_id,
                        json.dumps(canonical),
                        content_hash,
                    ),
                )
            connection.commit()
        return 1
    except psycopg.errors.UndefinedTable:
        return 0
