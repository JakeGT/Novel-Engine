# API surface

- Status: Phase 0 (0F). Canonical routes are under `/api/v1`.
- Error contract: every failure returns `application/problem+json` with `type`, `title`,
  `status`, `code`, `detail`, `field_errors`, and `correlation_id`. The
  `X-Correlation-ID` response header echoes the request value or a generated UUIDv7.

## Canonical `/api/v1`

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/entities` | List by `type` with `limit`/`offset` pagination. |
| GET | `/entities/search` | Name/ID autocomplete (`q`, `type`). Declared before `/{entity_id}`. |
| POST | `/entities` | Create/upsert; rejects unknown types and invalid identifiers with 400. |
| GET | `/entities/{entity_id}` | Read one entity. |
| PUT | `/entities/{entity_id}` | Full replacement; rejects path/body id mismatch. |
| GET | `/entities/{entity_id}/edges` | Incoming and outgoing edges. |
| POST | `/edges` | Create edge; validates type and endpoint existence. |
| GET | `/edges` | Find edges by `source_id`/`target_id`/`type`. |
| GET/POST | `/schema/node-types` | List or register a custom node type. |
| DELETE | `/schema/node-types/{node_type}` | Remove a custom node type (core types are 404). |
| GET/POST | `/schema/edge-types` | List or register a custom edge type. |
| DELETE | `/schema/edge-types/{edge_type}` | Remove a custom edge type. |
| POST | `/context` | Explicit-tag plus optional semantic anchors, policy traversal, summary. |
| POST | `/characters/init` | Create a character anchor. |
| POST | `/characters/generate-questions` | Structured questionnaire (AI). |
| POST | `/characters/process-answers` | Structured graph update (AI), applied to the graph. |
| POST | `/rag/query` | **Placeholder-disabled:** returns 501 `not_implemented`. |

### Entity representation

`{ "id", "type", "properties": {...}, "created_at" }`. Create, read, and update
return the same shape. Embeddings and other storage-only fields are never exposed,
and `created_at` is normalized to timezone-aware UTC.

### Health

`/health/live` (process alive), `/health/ready` (Neo4j migration + connectivity,
PostgreSQL readiness + migration; 503 when degraded), `/health/capabilities`
(optional embeddings/AI/vector availability; no secrets, no model load, no writes).

## Legacy aliases

The original prototype `/api` routes were removed in Phase 1 (1A) once `/api/v1`
covered the maintained surface. Only `/api/v1`, `/health/*`, and `/auth/*` are served;
there is no `/api` alias router.

## Pending

- Edge validity/provenance (`valid_from`/`valid_until`) exists on the domain model but is
  not persisted; it is explicitly excluded from the v1 `EdgeModel` until temporal
  semantics land (FND-008.4).
- Edge deletion/retirement (`FND-008.5`) is exposed for nodes (`DELETE /entities/{id}`)
  but not yet for edges.
- The graph and RAG answer generation pipeline is future-phase work; `/api/v1/rag/query`
  is deliberately 501 so placeholder text cannot be mistaken for generated output.
- Character graph proposals are validated and applied immediately; atomic, reviewable
  mutation application is Phase 1.
- Shared OIDC authentication, invitation-derived workspace/project scoping, and pagination cursors are Phase 1.
