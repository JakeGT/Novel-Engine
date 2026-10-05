# ADR-004: Branching, extensible schema, and canonical prose

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner
- Related backlog: TMP-003–TMP-005, CAN-002–CAN-003, SCN-004–SCN-005

## Context

Authors need alternative timelines, domain-specific concepts beyond a universal schema, and a long-running manuscript workspace.

## Decision

- Support branch fork, comparison, and merge. Authors resolve conflicts directly or request an agent-generated resolution proposal; an agent cannot finalize a conflicted merge without human approval.
- Provide a fixed core schema plus project-scoped runtime extensions. Extensions compile to strict validation models used for model structured output and canonical validation.
- Treat Novel Engine as the owner of immutable canonical scene versions, while allowing external editor integration. Store canonical prose as a versioned structured editor document with deterministic Markdown/plain-text projections.

## Consequences

- Branch and schema version scope must exist in foundation data models.
- Runtime schema compilation must be cached, versioned, safe, and reproducible; generated types are never arbitrary executable code.
- Model prompts receive a JSON-schema representation for the active project schema.
- Merge proposals are mutation sets with evidence, conflicts, and human decisions.
- External document synchronization must preserve stable scene/version identities and detect conflicting edits.

## Validation

Schema fuzz tests reject invalid or unsafe definitions. Branch property tests cover inheritance and conflicts. Round-trip editor/export tests preserve prose and entity annotations.
