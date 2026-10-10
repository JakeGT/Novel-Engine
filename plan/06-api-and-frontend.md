# API and frontend

## API style

Use versioned JSON HTTP APIs for commands and queries, plus server-sent events for job progress and consistency runs. Generate and commit OpenAPI as a reviewed contract. Prefer explicit workflow resources over generic unrestricted graph mutation routes in the product UI.

Generic graph CRUD may remain an admin/development capability, guarded separately.

## Common contracts

### Entity representation

```json
{
  "id": "01...",
  "project_id": "01...",
  "type": "Character",
  "name": "Arin",
  "properties": {},
  "status": "active",
  "revision": 12,
  "created_at": "...",
  "updated_at": "..."
}
```

Use the same nested representation on create, read, update, search, and graph expansion responses. Never expose raw Neo4j driver objects.

### Errors

Use a stable problem format with `type`, `title`, `status`, `code`, `detail`, `field_errors`, `correlation_id`, and optional conflict metadata. Do not turn domain validation failures into generic 500 responses.

### Pagination and concurrency

- Cursor pagination for collections and search.
- ETags or explicit revision fields for mutable resources.
- `Idempotency-Key` for job creation and commits.
- Project and branch are explicit in scoped URLs or resolved from a verified workspace context.
- Workspace/user identity comes only from the validated OIDC/local server session, never a trusted client-supplied ID.

## Proposed resource groups

- `/api/v1/session`, `/api/v1/workspaces`, `/api/v1/invitations`, `/api/v1/usage`
- `/api/v1/provider-credentials` (write-only secret input; responses never return keys)
- `/api/v1/projects`
- `/api/v1/projects/{project_id}/members`
- `/api/v1/projects/{project_id}/schemas`
- `/api/v1/projects/{project_id}/entities`
- `/api/v1/projects/{project_id}/assets`
- `/api/v1/projects/{project_id}/assets/{asset_id}/versions`
- `/api/v1/projects/{project_id}/assets/{asset_id}/variants`
- `/api/v1/projects/{project_id}/assets/{asset_id}/segments`
- `/api/v1/projects/{project_id}/assets/{asset_id}/links`
- `/api/v1/projects/{project_id}/assets/{asset_id}/observations`
- `/api/v1/projects/{project_id}/assets/search`
- `/api/v1/projects/{project_id}/branches`
- `/api/v1/projects/{project_id}/timeline`
- `/api/v1/projects/{project_id}/canon-query`
- `/api/v1/projects/{project_id}/revisions`, `/tags`, `/revert-proposals`
- `/api/v1/projects/{project_id}/invariants`
- `/api/v1/projects/{project_id}/aliases`
- `/api/v1/projects/{project_id}/disclosures`
- `/api/v1/projects/{project_id}/suppressions`
- `/api/v1/projects/{project_id}/stories`
- `/api/v1/projects/{project_id}/scene-plans`
- `/api/v1/projects/{project_id}/scenes`
- `/api/v1/projects/{project_id}/context-previews`
- `/api/v1/projects/{project_id}/jobs`
- `/api/v1/projects/{project_id}/mutation-sets`
- `/api/v1/projects/{project_id}/consistency-findings`
- `/api/v1/projects/{project_id}/consistency-runs`
- `/api/v1/projects/{project_id}/exports`

## Key workflows

### Context preview

`POST .../context-previews` accepts purpose, branch, timeline position, POV, text/entity/media
anchors, pinned sources, required-original flags, and text/media budgets. It returns typed context
parts, evidence clusters, individual sources, authority and score components, exclusions, warnings,
rendered token/media estimates, and the original-versus-derived representation selected for the
target model. The UI can revise the request before generation.

### Media ingestion and review

Asset creation uses an authorized upload intent/finalize flow or bounded streaming upload. The API
exposes immutable source versions, processing status, safe previews, variants/segments, explicit
domain links, branch/time/visibility/authority controls, and derived-observation review. It never
returns storage credentials, stable public object URLs, or another tenant's processing status.

### Generation job

`POST .../jobs` creates an asynchronous job from a frozen context-preview revision. It resolves the authorized user's OpenRouter/local provider route, enforces configured usage limits, and applies autonomy/consistency policy. `GET .../jobs/{id}` returns stage, progress, token/cost estimate and actual usage, artefacts, and classified failure. An event endpoint streams progress.

### Mutation review

`POST .../mutation-sets/{id}/decisions` records accept/edit/reject decisions per operation. `POST .../mutation-sets/{id}/commit` requires expected branch head and returns the new revision or a conflict response.

Before commit, an impact-preview endpoint evaluates the mutation against a scratch overlay and
returns known affected scenes/plans/manifests, invariant and scene-condition results, stale
findings/suppressions, and dependency-coverage gaps. Preview never authorizes the later commit.

### Canon query, history, and revert

Point-in-time query endpoints require explicit branch/revision and optional world/narrative/POV
coordinates. Results report their effective scope and projection watermark. Provenance responses
link state to commit, mutation operation, review decision, source scene/version, and evidence.
Revert requests create compensating mutation sets and surface intervening conflicts; they never
delete history. Tags are immutable named branch/revision pointers.

### Consistency run

`POST .../consistency-runs` accepts a prose/scene/project scope, profile, check categories, spend cap, and optional schedule context. It returns a job/run ID. Findings expose exact source revisions and evidence. Correction requests create prose diffs or mutation proposals; they do not mutate accepted data directly.

Invariant creation defaults to warning behavior. Enabling blocking requires a separate explicit
authorized action and rule-version publication. Eligible finding dismissal may create a scoped,
versioned suppression with rationale and expiry; built-in integrity failures cannot expose that
action.

### MCP transport

After canon query contracts stabilize, a read-only MCP server exposes the same scoped query,
history, provenance, and visibility operations. Authenticated server context establishes tenancy.
Any write-like MCP operation can create only a mutation/patch proposal and cannot commit or modify
review and invariant policy.

## Frontend information architecture

Revised 2026-10-10 (ADR-017). The interface is organised by what the author is doing, not by how
canon is stored, and reveals itself by level ([`19-assistant-first-experience.md`](19-assistant-first-experience.md)).
Visual direction: [`20-design-direction.md`](20-design-direction.md). Screens marked *built* exist
today; the rest are scheduled in the roadmap.

### Always present

- the assistant (the God Hand): one place on every screen to describe, ask, be asked, and change
  anything in the story by saying so; it always ends in a proposal (*built* for creation and
  fill-in; changing existing canon is Phase 2.5)
- search that reaches every entry and every feature (*built*)
- review: what is waiting for the author's decision (*built*; grouping Phase 2.5)
- the to-do list: what is unfinished (*built* for bare entries)
- which key or local model pays for the next model call, and its cost before it is spent

### Level 1 — Start

- describe-your-story first run and sample project (Phase 2.5)
- the story bible as browsable entries (*built*)
- library of projects (*built*)

### Level 2 — Shape

- entry pages: details, facts as sentences, what a character believes compared with canon, events
  (*built*); belief editor and pictures on entries (Phase 2.5)
- history as versions, with "go back to this" (*built*)
- members and invitations (Phase 2.5)

### Level 3 — Plan

- the story plan built by interview: arcs, turning points, beats, scenes; an outline as the
  by-hand view (Phase 2.6)
- threads, promises, and payoffs; open ones as to-dos (Phase 2.6)
- scene plans and readiness in plain words (Phase 2.6)

### Level 4 — Write

- scene studio: manuscript editor with entry mentions, immutable scene versions, draft variants
  (Phase 5)
- what the assistant will use, explained per source, with pin and exclude as the by-hand path
  (Phase 4)
- findings as margin notes with evidence, "explain" and "fix" (Phase 5)
- changes a scene makes to the world, in grouped review (Phase 6)
- media references with safe previews (Phase 4)

### Level 5 — Control

- timelines and world time; points of view, secrets, and reader disclosure; what-ifs (branches)
  with comparison and merge (Phase 3)
- types and fields (*built* by hand; by asking Phase 2.5); story rules (Phase 2.7)
- audits, exports, and reader-safe story bibles (Phases 3 and 7)
- the raw record: revisions, mutation sets, provenance, schema versions, projection status
  (*built*, under technical details and Advanced)

Supporting visualisation: a compact neighbourhood graph and timeline views support these screens;
they are not the primary editor.

## Frontend technical direction

- React, Vite, and TypeScript; API types generated from the OpenAPI snapshot; TanStack Query for
  server state; React Router (all in place).
- Hand-written CSS on design tokens; no component framework. Fonts are bundled, because the hosted
  site's content security policy allows only its own origin.
- WebGL2 for expressive, non-essential visuals (the landing page), always with a static fallback
  and reduced-motion support; WebGPU only as an enhancement where it is available. No visual may
  be required to use the product.
- Choose an editor that supports decorations/entity mentions without locking canonical text into a
  proprietary document format.
- Store a versioned structured editor document with deterministic Markdown/plain-text projections unless the canonical format decision selects otherwise.
- Introduce a graph visualization library only for focused exploration.
- Accessible components and keyboard workflows, checked automatically in the browser tests.

State ownership:

- server state belongs in query/cache primitives
- editor drafts remain local until explicitly saved
- branch/timeline context lives in route/workspace state
- job progress comes from event streams with polling fallback
- no duplicated client-side canonical state

## UX safeguards

- Every capability has an assistant path and a by-hand path; the by-hand path works with no AI
  provider and is one action away.
- Nothing at Levels 1–4 requires choosing a branch, a time, a point of view, or a schema.
- Internal terms appear only under technical details; author-facing names come from one mapping.
- The assistant reports what it could not do, and never fills a gap in the story by invention.

- Clearly label draft, proposed, and canonical content.
- Clearly label local versus user-funded OpenRouter execution and show estimated/actual usage.
- Show branch and timeline context beside every mutation review.
- Preview destructive or broad changes.
- Make model cost and context visible before expensive actions.
- Show why context was included and allow author overrides.
- Never silently accept newly inferred facts.
- Never silently expose an author-only secret as POV knowledge; show leakage findings and their evidence.
- Never imply that a text-only model inspected original media; preview and persist the actual fallback representation.
- Treat machine captions, transcripts, OCR, and visual/audio observations as reviewable derived material, not canon.
- Show source/licence/attribution and warn before remote-provider transfer of original media.
- Preserve manual prose edits when rerunning extraction or generation.
- Show derived-view watermarks and dependency-coverage gaps rather than presenting stale or partial
  impact analysis as complete.
- Built-in integrity failures never offer disable, suppression, or override controls. Author story
  invariants visibly default to warning and require an explicit action to become blocking.
