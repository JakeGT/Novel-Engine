"""Every PostgreSQL table that holds project-owned canon, workflow, or media state.

One list drives project/workspace deletion and the development reset, so a new
table cannot be forgotten by one of them. Each table carries ``workspace_id`` and
``project_id``; child tables are listed before their parents.
"""

from __future__ import annotations

CANON_PROJECT_TABLES: tuple[str, ...] = (
    "archive_imports",
    "story_clocks",
    "media_lifecycle_events",
    "canon_evidence",
    "canon_entity_external_ids",
    "asset_links",
    "media_asset_versions",
    "media_assets",
    "canon_tag_history",
    "canon_tags",
    "projection_watermarks",
    "canon_record_versions",
    "canon_provenance",
    "plan_items",
    "canon_events",
    "canon_beliefs",
    "canon_facts",
    "canon_entities",
    "outbox_messages",
    "canon_revisions",
    "commit_attempts",
    "commit_envelopes",
    "mutation_transitions",
    "operation_decisions",
    "review_decisions",
    "mutation_operation_revisions",
    "mutation_operations",
    "branch_reservations",
    "mutation_sets",
    "branches",
    "schema_versions",
)

__all__ = ["CANON_PROJECT_TABLES"]
