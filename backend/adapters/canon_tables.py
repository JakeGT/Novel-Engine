"""Every PostgreSQL table that holds project-owned canon, workflow, or media state.

One list drives project/workspace deletion and the development reset, so a new
table cannot be forgotten by one of them. Each table carries ``workspace_id`` and
``project_id``; child tables are listed before their parents.
"""

from __future__ import annotations

CANON_PROJECT_TABLES: tuple[str, ...] = (
    # The assistant's kept threads and their turns (AST-EXP-005, AST-EXP-017), and the
    # record of each run of suggestions (AST-EXP-007, AST-EXP-013).
    "assistant_turns",
    "assistant_threads",
    "suggestion_runs",
    # Derived search vectors of every generation (CTX-012); the generations themselves
    # are install-wide and hold no project data.
    "embedding_vectors",
    # The story's ready views (QRY-001): derived, purged with the story, never archived.
    "read_models",
    "archive_imports",
    "delegation_seen",
    "delegation_records",
    "delegation_settings",
    "story_clocks",
    "assistant_settings",
    "consistency_runs",
    # The author's usual voice, audits on a schedule and correction plans (CON-018, CON-011).
    "voice_baselines",
    "audit_schedules",
    "correction_plans",
    # What each check that needs no model found for one fingerprint (CON-007): derived.
    "check_cache",
    "finding_dismissals",
    # Advisory findings on a proposal in review the author dismissed (CON-016).
    "review_dismissals",
    "scene_readings",
    "draft_findings",
    "scene_drafts",
    "jobs",
    "story_voices",
    "text_passages",
    # The passage index's waiting list (CTX-005) and summaries of scenes (CTX-006).
    "passage_index_waiting",
    "scene_summaries",
    "context_manifests",
    "media_lifecycle_events",
    "media_observations",
    "media_segments",
    "media_variants",
    "media_processing",
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
