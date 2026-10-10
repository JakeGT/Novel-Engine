# API surface

- Status: Phase 2. The committed contract is `tests/contract/openapi_snapshot.json` (151
  operations), exact-checked by `tests/contract/test_openapi.py`; the frontend client is generated
  from it and `make frontend-api-check` fails on drift. This page is a guide to that contract, not a
  second source of truth.
- Error contract: every failure returns `application/problem+json` with `type`, `title`, `status`,
  `code`, `detail`, `field_errors`, and `correlation_id`. The `X-Correlation-ID` response header
  echoes the request value or a generated UUIDv7.
- Scope: project routes are `/api/v1/projects/{project_id}/...`. Hosted mode requires an
  `X-Workspace-Id` header and authorizes workspace membership and project ownership on every
  request. Local mode has exactly one workspace and project; a URL naming any other project is
  `404`.
- Writes require the CSRF header and an explicit `expected_revision`; a successful commit returns
  the new revision in `ETag`.

## Canonical product API (`/api/v1/projects/{project_id}`)

| Area | Routes | Notes |
| --- | --- | --- |
| Access | `GET /access` | Caller's role and permissions; drives permission-aware UI. |
| Schema | `GET /schemas`, `GET /schemas/{id}`, `GET /schemas/vocabulary`, `POST /schemas/validate`, `POST /schemas/drafts`, `POST /schemas/{id}/publish` | Immutable versions. `vocabulary` lists core plus published entity types (with attribute JSON Schema) and predicates. `validate` reports errors and compatibility against the published version. Publish needs project administration; a forbidden change is `409 schema_incompatible`. |
| Entities | `GET/POST /entities`, `GET /entities/search`, `GET /entities/by-external-id`, `GET /entities/name-collisions`, `GET /entities/needs-detail`, `GET/PATCH /entities/{id}`, `POST /entities/{id}/retire` | Names are handles, not identity (ADR-015). Search covers name, slug, and aliases. `needs-detail` is a derived to-do list: active entries with no attributes and no facts of their own. Retire needs `acknowledge_high_impact`. |
| Facts, beliefs, events | `GET/POST /facts`, `GET /facts/{id}`, `POST /facts/{id}/close`; `GET/POST /beliefs`, `GET /beliefs/{id}`, `POST /beliefs/{id}/close`, `GET /beliefs/{id}/truth-agreement`; `GET/POST /events`, `GET/PATCH /events/{id}`, `POST /events/{id}/retire` | Truth agreement is a derived query result, never a stored field. |
| Proposals and review | `GET/POST /mutations`, `GET /mutations/{id}`, `POST /mutations/{id}/validate`, `/submit`, `/approve`, `/reject`, `/retarget`, `/commit`; `PUT /mutations/{id}/operations/{ordinal}`; `POST /mutations/{id}/operations/{ordinal}/decision` | Review state machine (ADR-014). Validation is an advisory preview; the commit revalidates in its transaction. A client may propose as `author`, `model`, or `import` only. |
| Assisted creation | `POST /assist/questions`, `POST /assist/proposals` | Describe one thing or a whole idea (optionally a type and name, or `entity_id` to fill in an existing entry); answers are optional. Drafts new entries, details, and facts linking them to each other and to existing entries; an entry whose name matches an existing one of the same type is linked, not recreated. Spends the workspace's provider budget. Returns a proposal in the review queue; never writes canon. |
| The God Hand | `POST /assist/changes` | One instruction in plain words → every change it implies (new entries, renames, details, facts added and ended, beliefs, archives) as one proposal, with the assistant's restatement, deterministic and model notes on what else is affected, and discarded items. Returns questions and no proposal when the instruction is ambiguous. Never writes canon. |
| Guided character workflow | `POST /characters/{entity_id}/questions`, `POST /characters/{entity_id}/proposals` | Spends the workspace's provider budget. Returns a proposal in the review queue; never writes canon. |
| History and provenance | `GET /revisions`, `GET /revisions/{commit_id}`, `GET /revisions/{from}/diff/{to}`, `GET /{entities\|facts\|beliefs\|events}/{id}/history`, `GET /{...}/{id}/at-revision/{revision}`, `GET /provenance/{record_type}/{record_id}`, `GET/POST /tags`, `DELETE /tags/{name}`, `GET /tags/{name}/history`, `POST /reverts/preview`, `POST /reverts` | Provenance withholds reviewer identities and notes from read-only members and says so. A revert is a new commit and needs `acknowledge_high_impact`. |
| Media | `GET/POST /assets`, `GET /assets/{id}`, `GET /assets/{id}/lifecycle`, `GET/POST /assets/{id}/versions`, `POST /assets/{id}/retire`, `POST /assets/{id}/versions/{vid}/retire`, `GET/POST /assets/{id}/links`, `GET /links`, `POST /links/{id}/retire` | Identity, immutable versions, and explicit links only. No upload, scanning, or processing exists. |
| Pictures | `POST /entities/{id}/pictures?expected_revision=N[&name=]`, `GET /assets/{id}/versions/{vid}/content` | The upload body is the image itself (PNG, JPEG, or WebP by its own bytes, 8 MB limit). It is stored as a media version and linked to the entry as `DEPICTS` in one revision. Content is served to project members only. No processing, scanning, or model use. |
| Projection | `GET /projection` | Graph projection head, applied revision, lag, last error, and the latest archive import. |
| Archive | `POST /archive/export`, `GET /imports`, `DELETE /imports/{import_id}` | Export and import removal need project administration and are audited. Import itself is an operator CLI (`make archive`), not an HTTP route. |

### Review-related responses

| Status | `code` | Meaning |
| --- | --- | --- |
| 409 | `review_required` | The change needs an explicit approval. It has been parked as a proposal; `field_errors.mutation_id` names it and `field_errors.reasons` says why. |
| 409 | `revision_conflict` | The branch head moved or a commit id was reused; reload and retry (or retarget a proposal). |
| 409 | `invalid_state` | The proposal or import is in a state that does not allow the action (for example it is already committed). |
| 422 | `validation_error` | The authoritative validator rejected the operations; `detail` lists the finding codes. |

## Tenancy, credentials, and identity

- `/api/v1/workspaces` and `/workspaces/{id}/members|projects|invitations|ownership`,
  `/api/v1/invitations/accept`: invite-only tenancy (Phase 1). The member list includes a display
  name for every member and the email address for callers who can invite.
- `/api/v1/credentials` (+ `/{id}/rotate`, `/{id}/revoke`): envelope-encrypted OpenRouter BYOK;
  plaintext is never returned.
- `/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout`: OIDC (hosted) or local session.

## Health and operations

- `/health/live`, `/health/ready` (store connectivity and read-only migration verification; 503 when
  degraded), `/health/capabilities` (no secrets, no model load, no writes).
- `/health/metrics`: Prometheus text format, counts and timings only. Off unless
  `METRICS_ENABLED`; requires `METRICS_TOKEN` as a bearer token when set, and is never served
  unauthenticated in production. Deliberately not part of the OpenAPI contract.

## Local/admin graph surface (not the product API)

`/api/v1/graph/projects/{project_id}/...` and the pre-project aliases `/api/v1/entities`,
`/edges`, `/schema/*`, `/context`, `/characters/*`, and `/rag/query` are the Phase 0 generic graph
API. It reads and writes the legacy Neo4j graph, which is separate from canon: canon projects into
its own `Canon*` labels (ADR-016), so the two never see each other's nodes.

- Reads and `/context` (explicit-tag plus optional semantic anchors) remain available.
- Every write, including `/characters/init` and `/characters/process-answers`, is restricted to
  local mode and a platform administrator. Hosted deployments cannot write through this surface.
- `/rag/query` is deliberately `501 not_implemented`.

## Not yet in the API

Branch forks and overlays, world/narrative-time and point-of-view query parameters, story-invariant
authoring, scene contracts, manuscript import, generation, media upload/processing, and an HTTP
archive import are later-phase work. No route accepts parameters that imply they are enforced.
