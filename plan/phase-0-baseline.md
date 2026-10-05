# Phase 0 baseline

- Captured: 2026-10-05
- Baseline commit: `557e97456d8d17345fd9b3e1117174b2ba911021`
- Implementation branch: `phase-0-stabilization`
- Purpose: static regression inventory before Phase 0 refactoring

This inventory records observable intent and known hazards. It is not an endorsement of current behavior. Importing the application performs external work, so the API and storage inventory was taken from source instead of importing `backend.main` or querying a developer database.

## Worktree ownership and data policy

- `backend/services/world_service.py` contained an unfinished user edit before Phase 0 began. It is parked for FND-008.25 and must not be overwritten or staged accidentally before that work is reconciled.
- `.opencode/` contains workstation-local commands/state, including local paths. It is ignored and is not project tooling.
- `data/` is ignored and currently contains local Chroma/Neo4j prototype state and downloaded Neo4j plugins. It was approximately 587 MB when this baseline was captured.
- The project owner confirmed that every current database record is prototype/development data. It may be reset without migration or preservation. This permission does not extend to any future deployed environment.
- Future development and tests must use migrations plus deterministic seeds. Production-like reset commands must refuse to run.

## HTTP surface

All routes except `/` are currently mounted under `/api`. There is no API version prefix, authentication, tenant/project scope, pagination, idempotency, or stable error envelope.

| Method | Path | Current intent or result |
| --- | --- | --- |
| GET | `/` | Welcome response |
| GET, POST | `/api/schema/nodes/` | List or add process-global JSON custom node types |
| DELETE | `/api/schema/nodes/{node_type}` | Remove a custom node type |
| GET, POST | `/api/schema/edges/` | List or add process-global JSON custom edge types |
| DELETE | `/api/schema/edges/{edge_type}` | Remove a custom edge type |
| POST | `/api/graph/nodes/` | Create/upsert a Neo4j entity and generate an embedding |
| GET, PUT | `/api/graph/nodes/{node_id}` | Read or update a node |
| GET | `/api/graph/nodes/{node_id}/edges` | Read incoming and outgoing edges |
| GET | `/api/graph/nodes/search/` | Name autocomplete; currently shadowed by the earlier dynamic route |
| GET | `/api/graph/nodes/entities/` | List entities by type; currently shadowed by the earlier dynamic route |
| GET, POST | `/api/graph/edges/` | Find or create a directed edge |
| DELETE | `/api/graph/edges/{edge_id}` | Delete by Neo4j internal relationship ID |
| POST | `/api/rag/query` | Chroma query plus placeholder graph/answer strings |
| POST | `/api/rag/context` | Resolve entity tags, optionally search vectors, traverse, and render context |
| POST | `/api/characters/init` | Create a minimal character anchor |
| POST | `/api/characters/generate-questions` | Generate a structured questionnaire through a live model or debug cache |
| POST | `/api/characters/process-answers` | Generate and immediately write proposed nodes and edges |

The static `/search/` and `/entities/` declarations occur after `/{node_id}` and are therefore a known route-specificity regression target. The manual scripts also call node-delete routes that do not exist.

## Graph and schema surface

Neo4j currently uses a generic `Entity` label with a unique `id` constraint and adds one domain label per node. Code declares these domain labels:

`Character`, `Location`, `Object`, `Faction`, `Concept`, `Event`, `Scene`, `Action`, `Flashback`, `Interaction`, `Story`, `Act`, `PlotArc`, `Beat`, `Theme`, `Goal`, `Conflict`, `Secret`, `WorldState`, `System`, `Pressure`, `Resource`, `Constraint`, `Law`, `Appearance`, `PersonalityProfile`, `State`, and `Belief`.

The world service also tries to use `World`, which is not declared. The JSON extension registry is `backend/config/custom_schema.json`; it contains empty node and edge lists at baseline and writes unordered sets back to the tracked file.

Code declares these relationship types:

`LOCATED_AT`, `PART_OF`, `OWNS`, `MEMBER_OF`, `OCCURRED_AT`, `CONTAINS`, `HAS_APPEARANCE`, `HAS_PERSONALITY`, `CURRENT_STATE`, `HAS_GOAL`, `KNOWS`, `LIKES`, `HATES`, `RELATED_TO`, `PARTICIPATED_IN`, `BELIEVES`, `KNOWS_SECRET`, `CAUSED`, `FOLLOWS`, `MOTIVATED_BY`, `HAS_ACT`, `HAS_ARC`, `HAS_THEME`, `STARTS_AT`, `CLIMAX_AT`, `INVOLVES`, `OCCURS_IN`, `AFFECTS`, `REQUIRES_STATE`, `TRIGGERS_EVENT`, `CHANGES_STATE`, `RESOLVES`, `HAS_SYSTEM`, `HAS_PRESSURE`, `REQUIRES_RESOURCE`, `FORBIDDEN_BY`, `CAUSES`, `DEPENDS_ON`, and `TRAUMATIZED_BY`.

The manager creates the uniqueness constraint and an `entity_embeddings` cosine vector index of dimension 384 during construction. It attempts Neo4j 5.15 syntax while Compose pins Neo4j 5.14, then falls back to a procedure. DDL failures are logged rather than made fatal. Edge `valid_from` and `valid_until` model fields are not persisted.

## Vector surface

- Chroma uses a persistent client at `./data/chroma`.
- Its collection is named `novel_engine`.
- The adapter provides add and text query only; there is no project filter, version contract, delete/deactivate operation, health check, or rebuild operation.
- Neo4j entity embeddings use `sentence-transformers/all-MiniLM-L6-v2`, dimension 384, according to the current vector-search implementation.
- Both vector paths initialize globally and are unsafe to inspect through normal application import.

## Manual workflow baseline

### Graph CRUD

`test_endpoints.py` expects a separately running API and persistent Neo4j. It creates a custom node type, creates/reads/updates nodes, creates a custom edge type, creates/reads edges, lists/searches nodes, then partly cleans up through direct Cypher. It expects flattened node properties on reads but raw Neo4j record nesting from entity listing. It cannot clean up nodes through the API.

### Context extraction

`test_context.py` creates a character, location, system, and two edges; submits Markdown-style entity links to `/api/rag/context`; and prints returned nodes and scores. Its attempted API cleanup uses missing routes. The current flow is explicit-tag extraction → optional semantic anchors → policy-bounded Neo4j traversal → unbudgeted text summary.

### Character creation

`test_character_creation.py` asks for a name and bio, initializes a character, requests model-generated questions, collects answers, and immediately applies a generated graph update. Its request includes name/bio fields that the API request model does not accept/use. It requires a running API, human input, persistent stores, and potentially paid model calls.

## Known-defect coverage map

The 32 concrete repair targets are enumerated as FND-008.1 through FND-008.32 in [`phase-0-todo.md`](phase-0-todo.md). They cover manager/DTO shape errors, schema/update/delete behavior, route ordering and placeholder responses, character proposal validation and ID rewriting, the parked world workflow, import-time model/vector work, and semantic threshold/version behavior. Each item requires a regression test before or with its fix.

Additional characterization targets for resolver parsing, schema order, traversal policies, summarization, identifier validation, serialization, route resolution, and fake-AI workflows are listed in workstream 0C of the same checklist.

## Baseline limitations

No live OpenAPI document, database schema dump, or model request was executed because doing so would trigger exactly the import-time side effects Phase 0 must remove. The source-derived route inventory becomes executable contract evidence once the application factory and fakes exist.

## P0-01 verification snapshot

After introducing the foundation contract:

- `uv lock --check` passes with 136 resolved Python packages.
- A clean `pnpm install --frozen-lockfile` passes after explicitly allowing only esbuild's required install script.
- Frontend ESLint and the Vite production build pass.
- Python bytecode compilation passes for `backend/`.
- Backend Ruff lint correctly fails with 144 pre-existing findings, including real undefined-name defects already tracked under FND-008; the previous Makefile fallback no longer hides them.
- Ruff format check identifies 13 legacy files. Broad autoformatting is deferred so it does not obscure characterization work or overwrite the parked world-service edit.
- Frontend checks were executed on the workstation's Node 26 and emitted the intended engine warning. Node 24 LTS remains the supported target and must be proven in CI/clean-room verification.
- `.github/workflows/foundation.yml` now enforces Python lock integrity/source compilation and the Node 24 frozen-install/lint/build path. It intentionally does not claim backend lint or test health before P0-02 isolates the legacy suite.
