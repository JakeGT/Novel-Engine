# Testing and quality

## Test layers

### Domain unit tests

Fast, service-free tests for IDs, schema compilation/validation, facts, intervals, branches, belief visibility, invitation and autonomy policies, mutation validation, consistency rules, context budgeting, and state transitions. These should form the majority of the suite.

### Application workflow tests

Run use cases with fake repositories, fake clock/ID generator, and scripted AI adapter. Assert commands, events, proposals, and failure behavior without Neo4j or a network.

### Repository contract tests

Run repository behavior contracts against fakes and real PostgreSQL/Neo4j adapters. Cover tenant/project isolation, serialization, transactions/sagas, temporal queries, branch overlays, idempotency, outbox recovery, and concurrency.

### API contract tests

Use FastAPI's in-process test client with injected fakes for most cases. Verify OpenAPI, status codes, error format, validation, pagination, concurrency, and auth/project boundaries.

### Integration tests

Use disposable containers for Neo4j and any journal store. Start the application as needed. Never depend on a developer's persistent `data/` directory or an already-running server.

Media integration tests use small synthetic fixtures and disposable object storage/filesystem
roots. Codec, scan, and embedding adapters have deterministic fakes; optional real-processor tests
are separately marked and never download models during ordinary collection.

### Frontend tests

- component tests for editors and mutation review
- mocked-API workflow tests
- end-to-end happy paths against disposable services
- accessibility checks on core workflows

### AI contract and evaluation tests

Offline contract tests use recorded or synthetic structured responses. Live-provider evaluations are separately marked, opt-in, budgeted, and record provider/model/date. Do not make ordinary CI depend on nondeterministic model quality.

## Critical invariant matrix

| Area | Required tests |
| --- | --- |
| Project isolation | no read/write/reference across projects |
| Tenant isolation | no API/job/graph/vector/object/export access across tenants |
| Identity | OIDC validation, host-only sessions, local/hosted mode guard, invitation expiry/revocation/replay |
| Provider usage | limits, cancellation, retry idempotency, and actual-usage recording |
| Branches | inheritance, fork isolation, merge conflicts, head concurrency |
| Time | interval boundaries, replacement, unknown dates, flashback ordering |
| Beliefs | false belief retained, secret visibility, acquisition/expiry |
| Mutations | validation, per-operation review, atomicity, retry idempotency |
| Context | anchors, filters, ranking, budgeting, explanations, no leakage |
| Indexing | outbox retry, active versions, rebuild equivalence, stale warning |
| Media assets | upload validation, immutable versions, scoped object access, variants/segments, authority, retention, and complete deletion |
| Multimodal retrieval | explicit-link precedence, cross-modal relevance, representation clustering, embedding-generation isolation, and rebuild equivalence |
| Provider media | capability negotiation, limits, original/fallback fidelity, remote transfer preview, and unsupported-modality rejection before spend |
| AI | malformed output, refusal, timeout, rate limit, cancellation, cost cap |
| Credentials | BYOK encryption, worker-only access, rotation, deletion, and redaction |
| Autonomy | mandatory approvals cannot be bypassed by user/model/job state |
| Consistency | tier rules, incremental invalidation, evidence, staleness, override audit |
| Story invariants | warning default, explicit blocking enablement, example cases, deterministic completeness, permanent built-in integrity enforcement |
| Canon queries | branch/time/revision/POV state, history differences, evidence provenance, tag immutability, compensating revert conflicts |
| Reader knowledge | disclosure order, irony joins, premature reveal, setup/payoff, edition/audience isolation |
| Assertions/beliefs | lie/mistake/unreliable narration handling, explicit perception, trust-scoped propagation proposals, no direct belief write |
| Resolution memory | time/POV-scoped aliases, positive/negative correction examples, no historical/global silent remap |
| Derived views | watermark/lag, duplicate delivery, replay equivalence, dependency coverage, impact preview parity |
| Suppressions | scope/expiry/dependency staleness, audit, and inability to suppress integrity failures |
| MCP | authenticated tenant scope, POV filters, read parity, proposal-only writes, no commit/policy bypass (built: `tests/contract/test_mcp.py`, `tests/application/test_access_key_service.py`, `tests/unit/test_access_key_format.py`, `tests/integration/test_access_key_store.py`, `frontend/e2e/access-keys.spec.ts`) |
| Spoiler exports | narrative-position/viewpoint filtering, secret/media leakage, artifact-specific sharing |
| Export | round trip, schema version, checksum, missing derived data rebuild |

## Test data

Create a compact reference world containing:

- two branches with one conflict
- a public fact and a secret
- a character with a false belief
- a location/resource constraint
- events shown out of narrative order
- a scene that produces several reviewable mutations
- an unresolved thread and planned payoff
- a selected author-only secret that must not leak into POV knowledge
- deterministic continuity defects and labeled model-critic cases
- warning and explicitly blocking story invariants with positive and `should_reject` examples
- a narrated lie, mistaken report, explicit overhearer, and trust-dependent proposed belief
- reader disclosures, a premature reveal, an unresolved setup, and a payoff
- time/POV-scoped epithets plus reviewed positive and negative entity-resolution examples
- sufficient dialogue for a stable voice baseline and one intentional voice shift
- branch/time-scoped character portraits, landscapes, objects/maps, multi-speaker audio, timestamped video, and visually similar hard negatives
- a secret-bearing asset and misleading machine observation that must not become accessible truth or canon

Seed at least two tenants with deliberately similar IDs/names so isolation tests cannot pass accidentally because records are absent.

Use deterministic IDs and a fixed clock. Keep fixtures synthetic and safe to publish.

## Quality gates

For every pull request or agent handoff:

- formatting and static lint pass
- relevant unit/workflow tests pass
- new behavior has acceptance tests
- schema/API changes update contracts and migration notes
- no secrets or generated databases are staged
- docs reflect current versus planned status

For a release candidate:

- full integration and end-to-end suite passes in a clean environment
- export/restore drill passes
- migration from the previous supported version passes
- AI evaluation metrics meet the approved baseline
- journal replay reproduces purpose-built read models and spoiler-safe exports at tagged revisions
- multimodal retrieval, provider-fallback fidelity, and media-consistency metrics meet the approved baseline
- security dependency scan has no unaccepted critical issue
- tenant-isolation, identity/invitation, provider-usage, and key-redaction suites pass
- manual release-one workflow is completed with a fresh project
- media export/restore/reindex/deletion and cross-tenant object-access drills pass

## Revision 2026-10-10: gates for the assistant and the learning curve

(The "Immediate test cleanup" list that stood here was Phase 0 work and is complete.)

Added to the invariant matrix and release gates (ADR-017):

| Invariant | Evidence required |
| --- | --- |
| The assistant never writes canon | Every assistant workflow ends in a proposal; a commit without approval is refused (tested for creation and fill-in) |
| Every capability has two paths | A scripted-model test of the assistant path and a no-provider test of the by-hand path, per capability-map row |
| Existing entries are linked, not duplicated | Fixtures with name, alias, and case variants (name matching tested; semantic suggestions Phase 2.5) |
| Unusable model output is reported | Discarded items are returned with reasons and shown to the author |
| Output formats are provider-safe | Each structured format converts to a strict JSON schema |
| Plain language | No internal term on a Level 1–3 screen outside technical details, checked over rendered screens |
| No provider, no dead end | The full Level 1–3 workflow passes with no provider configured |
| Intent is never fact | Plan records never appear in fact queries or exports (Phase 2.6) |

Release gate additions: the assistant-led release-one workflow is completed on a fresh project
with a real provider, and again with no provider; the learning-curve measures in document 19 are
met; drafted-change quality is at or above its recorded baseline.

Known gap: as of 2026-10-10 no test exercises a real model. The scripted model proves the
plumbing and the validation, not the quality of what is drafted.

## Evaluation sets (2026-10-20: CON-012, CTX-010, EXT-008, CON-013, QLT-003)

Labelled evaluation sets live in `tests/eval/` under the `eval` marker. The deterministic ones run
in `make test` (and so `make check`) and their numbers are pinned: a change to a check, a case or
the context service must move them on purpose. `make eval` runs them again and writes one report,
`var/eval/report.md` and `report.json` (ignored by git; counts, rates, timings and case ids only,
never prose, secrets or keys). Every set is original synthetic text from one invented story, the
Saltglass Ferry.

| Set | What it measures | Needs a model? |
|---|---|---|
| `consistency/` (CON-012) | Detection, false positives, precision and leakage recall of the deterministic checks (continuity, story rules, knowledge timing, drafting checks, reveals, thread order, scene conditions) on 73 hand-labelled defects and near misses; latency per call and on a 300-scene story; cost (zero tokens). Every case a check gets wrong carries its reason, a stated limit or a suspected bug (the bug cases are also `xfail`). | No |
| `consistency/bundles.py` (CON-013) | The deterministic review groups against full-context and vector-only bundling: tokens, groups, secrets shown, and whether each of eight labelled defects has its passages and records shown together. | No (the vector baseline is a word-overlap stand-in) |
| `retrieval/` (CTX-010) | A scene's context against labels, built through the API: Recall@5/@10, recall, MRR, precision, leaks (secrets, later knowledge, ended facts, another line of the story) by record and in the rendered words, omissions said or silent, what-if correctness, the least budget. | No (search by meaning off) |
| `extraction/` (EXT-008) | Reading prose into suggested changes, through the API, scored with `intake_eval.score_facts` overall and by kind of reading (name, alias, pronoun, epithet, new entry, narration, speech, disclosure), plus things only said that became facts and items the reading dropped. In CI the scripted model replays hand-written answers (with typical mistakes): this pins the harness and the pipeline's filtering, and **measures no model**. | Only for `make eval-model` |
| `multimodal/` (area B, `make eval-media`) | Picture and words search on a stand-in embedder. | Only with a real embedder |

What only the owner's real model can measure, and is not measured anywhere yet: how well a model
reads prose into facts (`make eval-model` runs the four labelled passages against the configured
provider after saying about how many tokens it takes and waiting for "yes"; it refuses in CI, and
`EVAL_ARGS=--stand-in` runs the same path against the local stand-in with no spend), how many of
the defects a model critic finds when shown them and how many false findings it adds, whether
drafts are better with the chosen context, and the real token cost per finding. Until those exist
the release gates "AI evaluation metrics meet the approved baseline" and "drafted-change quality is
at or above its recorded baseline" cannot be met, and no threshold has been approved.

The sets are small and probe each check's edges, so their rates describe the set, not how often a
real manuscript goes wrong. Add a case for every repaired defect in a check, and a near miss for
every case a check must leave alone.
