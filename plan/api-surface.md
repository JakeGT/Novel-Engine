# API surface

- Status: checked 2026-10-21 against the committed contract, `tests/contract/openapi_snapshot.json`
  (340 operations), exact-checked by `tests/contract/test_openapi.py`; the frontend client is generated
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
| The God Hand | `POST /assist/changes` | One instruction in plain words → every change it implies (new entries, renames, details, facts added and ended, beliefs, archives) as one proposal, with the assistant's restatement, deterministic and model notes on what else is affected, and discarded items. Returns questions and no proposal when the instruction is ambiguous, and `elsewhere` (a place and a sentence) when what was asked is not a story change, with `back_to_version` (for a restore the author previews) or `invite` (role and address to pre-fill) when it could work those out. Performs none of them. Never writes canon. `context` says which listing policy chose what the assistant read (`change-story/1`: the entries the instruction names, their neighbours, then the rest by importance, within a word budget) and how much of each kind was listed and left out; a note says when something was left out, and the policy is recorded with the proposal (CTX-002). When a model for search is configured, entries close in meaning to the instruction are listed first. It can also merge two entries and change plan records (AST-EXP-017). |
| Merging entries | `POST /merges/preview`, `POST /merges` | What merging one entry into another would do, in sentences (changes nothing), and the merge sent to review as its own operation: facts, beliefs, names, details, events, plan references and pictures move to the kept entry and the other is retired as merged; undone in one step (`restore_entity`). |
| Kept conversations and premise | `GET/DELETE /assist/threads/{kind}`, `POST /assist/premise` | The caller's own running conversation with the assistant of one kind, in the line they work in, and "Clear and start again". A premise round returns at most five open questions and the entries, facts and beliefs the words state, as one proposal to review (AST-EXP-005). |
| Suggestion runs | `GET /assist/runs?kind=&limit=` | Recent runs of link and planning suggestions: how many were suggested, how candidates were found, how many were grounded in the author's words, and how many the author kept (AST-EXP-007, AST-EXP-013). |
| Conversations | `GET /assist/sessions` (own unfinished ones; or `?mutation_id=` for the one behind a proposal), `GET /assist/sessions/{id}`, `PUT /assist/sessions/{id}/answers`, `POST /assist/sessions/{id}/close` | Each conversation with the assistant is kept: description or instruction, questions, answers, and the proposal it led to. `/assist/questions` and `/assist/changes` return a `session_id`; sending it back continues the same conversation. Unfinished ones are private to their author; a finished one is read-only evidence for any project reader; closing erases it. None of these call a model. |
| AI provider and model | `GET /ai/providers`, `GET /ai/settings`, `PUT /ai/settings`, `GET /ai/models?provider=` | The providers a workspace can connect a key for; which provider and model the assistant uses (any member who can write may read it; an owner sets it); and the models the workspace's key can use, read live from the provider (`listed: false` when the provider gives no list). Workspace-scoped. Never returns a key or a provider address. |
| Where writing goes for search | `GET /ai/search` | Where an author's writing is sent to be searchable by meaning, set for the whole site: `server`, `service` (with the operator's name for it and its host) or `off`, and whether that index is in use. Any signed-in person. Never returns a key or a full address. |
| Setup suggestions | `POST /assist/sessions/{id}/suggestions/apply` | `/assist/proposals` and `/assist/changes` return `suggestions`: types of entry and kinds of fact the assistant could not find in the project's setup, cleaned and bounded, also kept on the session. This endpoint adds the named ones to the published setup (additive only; project owner; idempotent). Suggesting changes nothing. |
| Revise by asking | `POST /assist/revisions` | What should be different about a set of suggested changes, in the author's words. The model may edit or skip changes already in the set; each edit is typed, checked, and stored as a new revision of that change with the request as its reason; a skip is a recorded decision. It cannot add a change or write canon; what needs a new change is returned in `notes`. |
| Link suggestions | `POST /assist/links` | Facts between existing entries that their details imply and no fact records, as a proposal for review. With a model for search configured, pairs close in meaning that no fact connects are found first; without one, the listing is used and the run says so. Recorded facts are not repeated; single-valued facts are not corrected; nothing is merged. `context` as for the God Hand (`suggest-links/2`). Each run is recorded (`/assist/runs`). |
| Bulk decisions | `POST /mutations/{id}/operations/decisions` | Several keep/skip decisions in one request and one transaction: all are recorded, or none. |
| Before-review check | `POST /mutations/{id}/validate` | As before, plus advisory findings against current canon (`possible_duplicate`, `archived_twin`, `already_recorded`) worded with names. Common blocking findings are worded for an author. |
| What a change touches | `GET /mutations/{id}/impact`, `GET /entities/{id}/impact` | CON-017/CON-007, computed on read with no model and no stored index: `affected` (scenes, plan parts and threads whose people, conditions, reveals or thread steps name a changed entry, fact or plan item; story rules about a changed entry before or after the change; kept drafts whose context manifest included it), `may_be_affected` (texts and scene prose whose indexed passages name a changed entry, or a changed scene's own prose), and `not_checked` (never empty: what the index does not cover, texts the index is behind on, limits reached, other lines of the story). A proposal is read in its own line of the story and only its kept changes count; an entry in the request's line. Each dependent carries ids and a passage range to link to. |
| Assistant allowance | `GET /ai/usage`, `PUT /ai/usage` | What the signed-in person has used of their allowance in the period, and the limit in force (the lower of the site's and the workspace's). An owner sets or clears the workspace's daily allowance. Exceeding it returns 429 `usage_limit` before any provider call. |
| Sample story | `POST /entities/sample` | Fills a project that has no entries with a short sample story as one author version; 409 otherwise. |
| Request estimate | `GET /assist/estimate` | About how many tokens a request that reads the whole story bible, or only the names in it, will take in. From the text's length; calls no model. |
| Story plan | `GET /plan`, `POST /plan`, `PATCH /plan/{id}`, `POST /plan/{id}/move`, `POST /plan/{id}/remove` | The outline (story, arcs, beats, scenes, threads) and what it still needs, in plain words. Intent only: no entry, fact, belief or event endpoint returns a plan item. Writes are ordinary author versions; removing takes everything under an item with it. Needs no provider. |
| Planning with the assistant | `POST /assist/plan/questions`, `POST /assist/plan/proposals`, `POST /assist/plan/directions`, `POST /assist/plan/directions/choose` | Questions about what the author means to happen; one round of planning as plan items to review, with what was left undecided returned as notes; up to three directions for what could happen next. Offering directions proposes nothing; choosing one re-checks the draft it carries and calls no model. Nothing here changes the plan directly. |
| The author's writing | `GET/POST /texts`, `GET/PATCH/DELETE /texts/{id}`, `GET /texts/passage`, `POST /texts/{id}/entries`, `GET/POST /names`, `DELETE /names/{id}`, `POST /names/{id}/examples`, `DELETE /names/{id}/examples/{example_id}` | Prose the author brings in, kept as given in immutable versions; the names found in it with no model; the passage a change points back to; answers about what a phrase means, remembered for the story, one text, one what-if (`what_if_only`; the default inside a what-if), from or until a scene, or scenes seen by one character, the narrowest that applies deciding; each answer keeps the passages the author confirmed, and the list says whether today's answers still read them that way (CTX-016). A text is evidence, never canon. Needs no provider. |
| Reading a text with the assistant | `POST /assist/texts/{id}/read` | One bounded passage read into suggested changes to review. Every item must quote words really in the passage. Guesses about nicknames become questions only. Events (who takes part; a story time only when the passage says it) are suggested too, counted in `events`; reading a scene can also suggest thread steps (`thread_steps`, EXT-001). `context` as for the God Hand (`read-text/1`: the entries the passage names, their neighbours, then the rest). |
| Story rules | `GET/POST /rules`, `PATCH/DELETE /rules/{id}`, `POST /rules/check`, `POST /assist/rules/draft` | The author's own continuity rules in a fixed vocabulary, with examples they must agree with, read back in words, and the entries that break them now. A change that would newly break one is warned about, or, for a rule the author set to block (`PATCH` with `enforcement: blocking`), refused at approval and at commit until fixed (CON-015). Drafting from a sentence keeps nothing. A rule body (form 2, CON-005/CAN-013) also says `between` (a value within `low`/`high`), `implies` (`if_has` then `pattern`), `not_both` (`pattern` and `not_with`), `believes`/`does_not_believe` (a belief about `about_id` or the entry itself), `except_for` (named entries, a tag, or facts that excuse an entry) and `time` (`as_recorded`, the default; `every_moment`; `at_end`); examples may carry fact `starts`/`ends` and `beliefs`. `broken_by` items carry `certain` (false when story times are too loose to tell; such a break never blocks) and `when` ("at 5 in story time", "at the end of the story"). A body in a form newer than the server reads is refused (422). |
| Scene conditions | `PUT /plan/{id}/conditions` | What must be true before a planned scene and after it, checked like a story rule and reported with the outline. |
| Guided character workflow | `POST /characters/{entity_id}/questions`, `POST /characters/{entity_id}/proposals` | Spends the workspace's provider budget. Returns a proposal in the review queue; never writes canon. |
| History and provenance | `GET /revisions`, `GET /revisions/{commit_id}`, `GET /revisions/{from}/diff/{to}`, `GET /{entities\|facts\|beliefs\|events}/{id}/history`, `GET /{...}/{id}/at-revision/{revision}`, `GET /provenance/{record_type}/{record_id}`, `GET/POST /tags`, `DELETE /tags/{name}`, `GET /tags/{name}/history`, `POST /reverts/preview`, `POST /reverts` | Provenance withholds reviewer identities and notes from read-only members and says so. A revert is a new commit and needs `acknowledge_high_impact`. |
| Media | `GET/POST /assets`, `GET /assets/{id}`, `GET /assets/{id}/lifecycle`, `GET/POST /assets/{id}/versions`, `POST /assets/{id}/retire`, `POST /assets/{id}/versions/{vid}/retire`, `POST /assets/{id}/delete`, `GET/POST /assets/{id}/links`, `GET /links`, `POST /links/{id}/retire`, `GET /assets/{id}/versions/{vid}/processing`, `POST …/processing/retry`, `GET/POST /assets/{id}/versions/{vid}/segments`, `POST …/segments/{sid}/retire` | Identity, immutable versions and explicit links. Each picture version is prepared by a background job (thumbnail, web copy, size, whole-picture segment); `processing` says how far it is or why it failed, and `retry` asks again. Segments are author-marked rectangles (API only). `delete` removes a picture's files once it is shown nowhere, keeping the version rows as a tombstone (owners and administrators; AST-008). No hosted malware scanning. |
| Pictures | `POST /entities/{id}/pictures?expected_revision=N[&name=]`, `POST /plan/{id}/pictures?expected_revision=N[&name=]`, `GET /pictures`, `GET /assets/{id}/versions/{vid}/content` | The upload body is the image itself (PNG, JPEG, or WebP by its own bytes, 8 MB limit). It is stored as a media version and linked to the entry, or to a scene of the plan (`target_record_type` `plan_item`, scenes only), as `DEPICTS` in one revision. `GET /pictures` is the picture library: each active picture's newest current version and how many entries and scenes show it (0 = shown nowhere); choosing one is `POST /assets/{id}/links`. Content is served to project members only (`?size=original|web|thumbnail`). The upload is read in chunks: a declared length over the limit is refused before reading, and a `Content-Digest`/`Repr-Digest` is checked when sent (AST-002). |
| A picture's page | `GET /pictures/{id}`, `GET /pictures/search`, `PUT /pictures/{id}/links/{link_id}`, `PUT /pictures/{id}/context`, `GET /pictures/{id}/describe/estimate`, `POST /pictures/{id}/describe`, `POST /observations/{id}/review`, `POST /observations/{id}/propose` | A picture with where it is shown; search by name and accepted descriptions, then by meaning when set up. Changing a link sets who may know what it shows and the story time it applies from (one reviewed change). The context choice is send the picture, use its accepted description, or leave it out. "Describe this picture" is a `media_describe` job, estimated first; what the model saw is accepted, corrected or rejected, and an accepted appearance detail can be proposed as a fact for review (AST-005, CTX-013). |
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
- `/api/v1/credentials` (+ `DELETE /{id}`, `/{id}/rotate`, `/{id}/revoke`): envelope-encrypted
  provider keys (OpenRouter and six other providers); plaintext is never returned. When a key is
  created or rotated the provider is asked whether it accepts it; a clear "no" is answered 400 and
  nothing is stored, and the answer's `verified` says `true` when the provider accepted it, `false`
  when it could not be reached (the key is kept and tried on first use), or `null` from the list.
- `/api/v1/ai/connector/codes/check`, `/codes/decide`, `/devices`, `/devices/{id}/use`,
  `/devices/{id}/disconnect`, `DELETE /ai/connector/use`: a workspace owner confirms a computer's
  connection code, lists connected computers, chooses one of their models for the assistant, stops
  using it, or disconnects it (SCN-002A, ADR-025).
- `/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout`: OIDC (hosted) or local session.
- `/api/v1/access-keys` (`GET`, `POST`; `/{id}/revoke`, `/{id}/uses`): personal access keys for
  other assistants (QRY-003, ADR-024). Signed-in session routes; `POST` answers with the key once
  (`key`), never again. A member sees their own keys; an owner sees and can withdraw everyone's in
  the workspace. `uses` is counts per day and tool for 30 days, never content.

## Local connector (SCN-002A, ADR-025)

`/api/v1/connector/codes`, `/token` (the OAuth device grant, RFC 8628), `/rotate`, `/poll` (long
poll for work), `/requests/{id}/answer` and `GET /connector/version` are for the connector program
on an author's computer, authenticated by its own credential (`nvldev_`), never a session. They
relay only listing models and chat completions. See `plan/runbooks/local-connector.md`.

## MCP endpoint (QRY-003, ADR-024)

`POST /mcp`: Streamable HTTP, one JSON-RPC message per request, one JSON answer, authenticated only
by `Authorization: Bearer <access key>` (401 with `WWW-Authenticate: Bearer` otherwise; 429 with
`Retry-After` when busy; 403 for a browser page from another address). MCP 2026-07-28 (stateless,
`_meta` protocol version, `server/discover`, header checks -32020, unsupported version -32022) and
2025-11-25/2025-06-18/2025-03-26 (`initialize`, no session id). `GET`/`DELETE` are 405. Not in the
OpenAPI document. Tools: `list_stories`, `search_entries`, `read_entry` (`as_known_by` keeps to one
character's knowledge), `list_plan`, `read_scene`, `timeline` (`at`), `record_history`,
`open_reviews`; with a key that may suggest, `suggest_changes` (one proposal, origin `machine`,
always reviewed). Every tool takes `story` (narrows a workspace key) and `what_if`.

## Health and operations

- `/health/live`, `/health/ready` (store connectivity and read-only migration verification;
  `unavailable` and 503 when a required store is down; `degraded` but 200 when an optional part
  — the job worker (or waiting work older than five minutes), the graph worker, the search store,
  the search index, or picture preparation — is down or behind, with plain `reasons`; counts only
  with the metrics token on a hosted site; never addresses or credentials),
  `/health/capabilities` (embeddings, provider, telemetry, pictures and their decoding sandbox; no
  secrets, no model load, no writes).
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

## Story time, what-ifs and knowledge (Phase 3, ADR-020)

Under `/projects/{project_id}`:

- Any project request may carry `X-Story-Branch: <what-if id>` to work in that what-if; it must
  belong to the project (404 otherwise). Without it, everything is the main story.
- `/what-ifs` (list, start), `/what-ifs/{id}`, `/what-ifs/{id}/set-aside`,
  `/what-ifs/{id}/compare`, `/what-ifs/{id}/bring-back` (makes a proposal on the main story).
- `/timeline`, `/timeline/moment?at=`, `PUT /timeline/unit`.
- `GET /timeline/checks` (continuity across story time and place, CON-003/CON-004: findings
  with severity warning or note, the records each names, the scenes it concerns, and what each
  check covers; never blocking), `GET|PUT /timeline/how-long` (the author's travel times between
  places and lengths of events and facts, with `expected_version`; 409 when someone saved first).
  Plan open items carry `records`; the studio's checks include `continuity_*` findings.
- `POST /facts/{id}/amend` (when a fact is true; ordinary, common or secret). Facts and events
  accept `starts`/`ends`/`visibility` and `when` on create.
- `/knowledge/characters/{entity_id}`, `/knowledge/facts/{fact_id}`.
- `POST /knowledge/tell` and `POST /knowledge/witness` propose, for review, the event and a belief
  for each hearer or each person who notices, by their trust (KNW-006); the answer is the
  `mutation_id` and each person's proposal with its reason. Nothing is written until approved.
  Both take an optional `scene_id` and `when`, so what the hearers learn is placed (KNW-002).
- `POST /knowledge/infer` (`holder_id`, a `fact_id` or `proposition`, `premises`: 1–12 fact ids
  or the holder's own belief ids, optional `scene_id`, `when`, `confidence`): records that a
  character worked something out, as an event of kind `inferred` with its premises and the belief
  it gave them; the author's own change, saved at once (`201`, `revision`, `event_id`). `GET
  /knowledge/characters/{id}` adds `learned`: for each belief, how (`told`, `overheard`, `saw`,
  `worked_out`, `heard_said`, `event`, `recorded`), in words, the scene and story time it was
  learned, the premises of an inference and the fact it agrees with. Events carry `scene_id` and
  `premises`.
- `GET/PUT /knowledge/characters/{entity_id}/trust` (how a character takes what others say: a
  stance for anyone and for particular speakers; saved on the character as an author change).
- `GET /knowledge/claims?entity_id=&scene_id=`, `POST /knowledge/claims` (something said, with
  modality, speaker or narrator, hearers and scene; `hearers_believe` also proposes beliefs for the
  hearers), `POST /knowledge/claims/{event_id}/promote` (proposes the fact for review) (KNW-005).
- `/reader` (the plan's scenes in reading order with what the reader is shown),
  `/reader/guide?scene=&viewpoint=&version=&part=` (the story as it stood at a saved version, the
  latest by default; the answer names the line of the story, version, scene position and viewpoint,
  gives sections for reading and a Markdown export with that binding in a YAML header; `part` makes
  only the characters, places, timeline or recap; `400` for a version that does not exist, `404` for
  a scene not in the plan then); `PUT /plan/{id}/reveals`,
  `PUT /plan/{id}/when`.
- `PUT /plan/{id}/path` (an arc's stages in order: name, the scene where each is reached, a fact
  that should hold from then; STY-006). `POST /plan` accepts `path` for a new arc. Plan items
  carry `path`; `GET /plan` adds `arcs` (each stage's scene number in reading order and whether
  its fact holds there) and lists arc checks in `open` (`arc_out_of_order`, `arc_fact_not_true`,
  `arc_fact_gone`, `arc_stage_scene_gone`; notes `arc_long_stretch`, `arc_stage_not_placed`).
- `/mutations/{id}` reports `changed_since` for a stale proposal.

## Scene context (Phase 4, ADR-021)

Under `/projects/{project_id}`:

- `GET /plan/{id}/context?budget=&secrets=`: what the assistant would use to write a scene, each
  item with its reason, authority, source and word cost, and what was left out and why. Secrets the
  viewpoint character does not know are apart and left out unless asked for. Calls no model.
- `PUT /plan/{id}/context`: the author's pins, exclusions, budget and whether to give secrets, kept in
  the scene's details through the reviewed plan path.
- `GET /plan/{id}/context/kept`: the versions of the scene's prose kept from a draft, with the
  context each was written from (this line of the story only).
  `GET /plan/{id}/context/kept/{manifest_id}`: that context against the context now — added, no
  longer given (and why), changed. 404 for another scene or another line of the story.
- The plan (`/plan`): items carry `advances` (threads moved along) and, for a scene, `intent`
  (`goal`, `obstacle`, `outcome`: `yes` | `no` | `yes_but` | `no_and`); both are accepted on add and
  change. The outline returns `threads`: each thread's steps (`opens`, `advances`, `resolves`) in
  outline order.
- Plan checks and promises (STY-005, STY-007; no model): each `open` item has `kind` (`gap` or
  `problem`); problems are a fact shown before the scene marked as where it comes out
  (`reveals_too_early`), a fact marked to come out twice, a thread resolved or moved along before it
  opens, and a required "before" condition not true where its scene comes (`condition_not_met`).
  A reveal takes `main_reveal: true` (with `how: shown`) for the scene where the fact comes out. The
  outline returns `promises`: each opened thread and hinted fact, where it is made (`made_at`, a scene
  number in reading order), moved along and kept, `distance` in scenes, and `out_of_order`. A scene
  condition takes `strength` (`hard` | `soft`) and is either `entity_id` + `check` or `words`; its
  state has `status` (`met`, `planned`, `not_met`, `unclear`, `unchecked`), `note`, `index` and
  `planned_in`, judged at the scene's story time when it has one and against earlier scenes' "after".

## The scene studio (Phase 5, ADR-022)

Under `/projects/{project_id}`:

- `GET /plan/{id}/studio`: the scene's prose (one of the author's texts, for the line of the story
  being worked in), its versions with where each came from (`origin`, `parent_version_id`,
  `draft_id`, `manifest_id`), checks that need no model, the assistant's notes, drafts waiting to be
  kept, and recent work.
- `PUT /plan/{id}/studio/text`: save the author's words as a new version; `base_version_id` must be
  the current one or it is `409` and nothing is lost. In a what-if, the first save copies the text.
- `POST /plan/{id}/studio/estimate`: about how many tokens drafting would read and write. Calls no
  model.
- `POST /plan/{id}/studio/drafts` (`202`, optional `Idempotency-Key`): ask for 1–3 drafts of
  150–3,000 words, with an optional direction. Returns a job. Checks the allowance first (`429
  usage_limit`). Changes no text.
- `POST /plan/{id}/studio/reviews` (`202`): a check with the assistant of the current text or one
  draft, with `passes` (any of `viewpoint`, `continuity`, `plan`, `voice`, `clarity`, `pictures`; every pass when
  left out; an empty list is `400`). One model call per pass. Returns a job whose result counts
  notes per pass. Every note quotes the text; one that does not is dropped. Notes carry
  `check_pass` (null for a note from the earlier all-in-one check) (CON-008).
- `GET /plan/{id}/studio/check-estimate` (optional `passes`, `draft_id`): what a check would use,
  with `passes` giving each pass's label, description and tokens. Calls no model.
- `POST /drafts/{id}/keep` (`base_version_id`): the draft becomes the scene's newest version and
  remembers the job and context it came from. `DELETE /drafts/{id}`: discard it.
- `GET /jobs/{id}`: status, stage, progress, tokens used, and a plain-words reason when it failed.
  `POST /jobs/{id}/cancel`: a waiting job stops at once, a running one at its next step.
- `GET/PUT /voice`: how the author writes, a note per story given first in every draft request.
- Every check and note carries `key` (a fingerprint of what it is about and the sentence it points
  at) and `dismissal` when the author dismissed it. `POST /plan/{id}/studio/dismissals` (`key`,
  `reason`: `intended`, `not_a_problem` or `other` with `note`, optional `days`: 7 or 30) dismisses
  it for this scene and line of the story until those words change; `DELETE
  /plan/{id}/studio/dismissals/{dismissal_id}` brings it back. Both return the studio. The text is
  never changed (CON-010, CON-016).

## Deep audits (CON-011, ADR-022 amendment)

- `GET /audits/estimate?plan_id=&passes=`: the scenes in a part of the plan (a story, arc, beat or
  scene; empty for the whole plan) in reading order, which are written, and the tokens each pass
  would use across them. Calls no model.
- `POST /audits` (`202`, optional `Idempotency-Key`; `plan_id`, `passes`: every pass when left out,
  none for only the checks that need no model): a job that runs the passes over every written scene
  (at most 60), reading the exact versions that were current when it started. Asks first like the
  studio (`409 needs_confirmation`); `400` when nothing is written or the part is a thread. Followed
  and stopped with `/jobs/{id}`; a stopped or failed audit keeps the scenes it finished.
- `GET /audits?plan_id=`: the latest audits of that part. `GET /audits/{job_id}`: the results by
  scene: status (`checked`, `not_checked`, `waiting`), the version read and whether it is still the
  newest, the assistant's notes from that audit with `quoted` words, the checks that need no model
  (worked out on reading, free) and the plan's own order and reveal checks for the scene.

## Checks kept as runs (CON-001)

- `GET /plan/{id}/checks/runs`: the runs that read this scene, newest first (at most 20; checks
  without the assistant kept on each saved or kept version, checks with the assistant, audits),
  each judged for this scene now: `scenes[]` with `stale`, `reasons` and counts, and `findings[]`
  with `status` (`open`, `dismissed` with its `dismissal`, `fixed` with `fixed_by`: `words` or
  `story`) and `stale` (open, but the scene changed since, for a note only the assistant could
  re-judge). Calls no model. `POST /plan/{id}/checks/runs` (`201`) keeps a record of the checks that
  need no model now; the same version of the text and the story returns the earlier record.
- `GET /checks/runs/{run_id}` and `GET /checks/runs/by-job/{job_id}` (an audit's or an assistant
  check's run, once it has started reading): every scene it read. `404` from another line of the
  story. Each run says its `kind`, `scope` (`scene`, `part`, `story`), `story_revision`, the text
  versions read and the `passes` run (`model_free` first).
- The studio's checks and the plan's open items include knowledge used before it is learned
  (CON-006): `learned_later` (the words name everyone in it), `learned_later_planned`,
  `learned_later_said`, `learned_later_context` in the studio; `shown_before_learned` and
  `said_before_learned` in the plan. Warnings only, never blocking.

- `GET /checks/calibration`: how often each check's findings were dismissed in this line of the
  story (a note for the author; it changes no check). `GET /mutations/{id}/findings`,
  `POST /mutations/{id}/findings/dismissals`, `DELETE …/dismissals/{dismissal_id}`: the advisory
  findings on a proposal and dismissing one for that proposal only (CON-016).
- `POST /plan/{id}/findings/proposal-estimate` and `POST /plan/{id}/findings/proposal`: settle a
  check or note in the story bible ("make this true" or "change the story bible instead") through
  the God Hand, as a proposal for review (CON-010).

## Corrections, schedules, drift and voice (CON-011, CON-018)

- `GET/POST /audits/{job_id}/corrections` and `GET …/corrections/estimate`: a correction plan from a
  finished audit (a job, estimated first); `PUT /corrections/{plan_id}/items/{index}` keeps or skips
  one step. Nothing in the story or its prose changes.
- `GET /audits/{job_id}/drift`: voice against the author's usual and each pair of characters'
  drift across the audited range. `GET /what-ifs/{id}/audit`: the differences between a what-if and
  the main story. Both call no model.
- `GET/PUT /audit-schedules`, `DELETE /audit-schedules/{id}`: an audit of a part of the story every
  1–90 days, with a most per run (confirmed above the threshold).
- `GET /voice/book`, `GET /plan/{id}/voice`, `PUT /plan/{id}/voice/usual`: each written scene's voice
  against the author's usual, worked out with no model; leaving a scene out of the usual.

## Planning help, summaries, scales (STY-003, STY-004, STY-006, CTX-006)

- `GET /plan/{id}/intent/estimate`, `POST /plan/{id}/intent/suggest`: suggest what a scene is for, as
  a plan change for review. `GET /plan/{id}/bridges/estimate`, `POST /plan/{id}/bridges/suggest?condition=`:
  suggest one or two scenes to bring about an unmet "before" condition, as a plan change for review.
- `GET/PUT /plan/{id}/summary`, `POST /plan/{id}/summary/suggest`, `POST /plan/{id}/summary/{summary_id}`:
  the summary later scenes are given of this one; the assistant's suggestion is used only once the
  author keeps it (as it is or corrected).
- `GET/PUT /plan/scales`: the story's shared scales, each with its levels and a version.

## Indexes and ready views (CTX-005, CTX-012, QRY-001)

- `GET /texts-index`, `POST /texts-index/rebuild`: how far the writing index is (texts behind, what
  waits and for how long, failures) and reading every text again.
- `GET /story-views` and `GET /story-views/where|holders|relationships|dependents|disclosures`: the
  state of each ready view of this line of the story, and reads that answer from a verified view or
  work it out, saying which.

## Scenes back into the story (Phase 6, ADR-023)

- `GET /plan/{id}/studio` also returns `reading`: the version last read into the story, when, the
  proposals that came of it, and `changed_since`.
- `POST /plan/{id}/studio/readings` (`202`): read the scene's current version into proposed changes
  for review; only paragraphs changed since the last reading are read. `400` when nothing is written
  or nothing changed. The job's result lists the proposals. In a scene with a story time, facts
  of a kind that can take a time start at the scene's time unless the passage says they were
  already so (`timed` in the result counts them), and a new one-at-a-time value ends the old one
  at that time instead of removing it.
- `POST /plan/{id}/studio/plan-card` (`202`): suggest the scene's summary, viewpoint, place and who is
  in it from the prose, as one plan change for review. `400` when nothing is written.
- `GET /texts/passage` also returns `scene_id` when the text is a scene's, for "See it in the scene".

## Keep the safe ones for me (AST-EXP-015, OSS-007)

Off by default. Nothing is kept unless the author switched a kind on; only the assistant's
suggestions made in the app are considered (`/assist/proposals`, `/changes`, `/links`,
`/characters/{id}/proposals`); a suggestion made through `POST /mutations` is never looked at.

- `GET/PUT /kept-for-you/settings`: `rules` (`new_entry`, `first_details`, `new_fact`, each with
  `on`, `hand_approvals` and `since`), `allow_clients` (always false: assistants connected with a key only ever suggest),
  `workspace_forbidden`, `hand_approvals_needed` (3), `size_limit`, `daily_limit`, `always_waits`
  (plain lines) and `kept_since_seen`. `PUT {kinds, allow_clients}` switches kinds on or off at once (`allow_clients: true` is refused, `400`);
  switching one on is refused (`400`) until it has been approved by hand `hand_approvals_needed`
  times, or while a workspace owner forbids it. Switching off is never refused, and a suggestion
  approved under a rule but not yet committed goes back to the review queue.
- `GET /kept-for-you`: what was kept, newest first: `note` ("Kept for you under your rule “New
  entries”."), `kept_at`, `revision`, `operations`, `undo_mutation_id`, `undo_status`.
- `POST /kept-for-you/seen` (`204`): the person has looked; `kept_since_seen` counts from now.
- `GET /kept-for-you/decisions/{mutation_id}`: `how` (`kept`, `waited`, `by_hand`, or null when the
  rules never looked at it), `kinds`, `note`, `reason_code` and `reason` (plain words) for one
  suggestion.
- `POST /kept-for-you/{mutation_id}/undo` (`201`): suggests the revert of a kept suggestion; it waits
  in the review queue (an undo is never kept). Asked again, the same waiting undo is returned. `404`
  for something that was not kept; `400` when what it added has changed since.
- `GET/PUT /ai/kept-for-you` (workspace): `forbidden`; only a workspace owner may change it.
- A kept suggestion is approved with the decision note above and committed through the ordinary
  commit; the commit transaction refuses it (`422 kept_rule_off`) if its rule was switched off
  after it was approved.

## Cost and automation controls (CON-009, OPS-002, AST-EXP-011)

- `GET/PUT /assistant-settings`: a story's checking profile (`on_request`, the default;
  `balanced`; `thorough`), the tokens checks on their own may use for one scene in a day
  (`scene_tokens`), the least time between checks after saving (`check_minutes`), the size above
  which one request is confirmed first (`confirm_above_tokens`) and the most one conversation may
  use (`conversation_tokens`); the last two are off when null. The answer also gives the
  workspace's limits and the lower of the two that applies. Reading needs read access; changing,
  write access.
- `GET/PUT /ai/limits` (workspace): an owner's `confirm_above_tokens` and `conversation_tokens` for
  every story. A story may choose lower, never higher.
- Asking first: `/plan/{id}/studio/drafts`, `/reviews`, `/readings`, `/plan-card`, `/audits`, the assistant's
  `/assist/questions`, `/proposals`, `/changes`, `/links`, `/revisions`, `/assist/plan/questions`,
  `/proposals`, `/directions` and `/assist/texts/{id}/read` refuse a request estimated above the
  threshold with `409 needs_confirmation`, `field_errors.estimated_tokens` and
  `field_errors.threshold_tokens`. Nothing is sent or queued. The same request with
  `X-Confirm-Tokens` at least the estimate goes through.
- A conversation over its budget is refused with `409 conversation_budget` before any call; what each
  conversation used is counted from the provider's reports. `GET /assist/estimate?session_id=`
  also returns `reply_tokens`, `confirm_above_tokens`, `conversation_tokens` and
  `conversation_used_tokens`.
- `GET /plan/{id}/studio` returns `checking` (profile, `scene_tokens`, `spent_tokens` today,
  `check_minutes`, `next_after_save`); the answer to a save or a kept draft returns `automatic`:
  `started`, or why no check started (`busy`, `too_soon`, `scene_allowance`, `needs_confirmation`,
  `allowance`, `no_provider`, `job_cap`, `not_started`). Jobs return `automatic` and
  `provider_cost_usd`. `GET /plan/{id}/studio/check-estimate`: what a check of the scene would use, pass by pass.
- `GET /ai/usage` and jobs return `cost_usd`/`provider_cost_usd` only when the provider reported a
  cost (OpenRouter's `usage.cost`); no price is ever worked out.

## Not yet in the API

Streaming drafts, merging drafts, sound and film, and an HTTP archive import. No route accepts
parameters that imply they are enforced.
