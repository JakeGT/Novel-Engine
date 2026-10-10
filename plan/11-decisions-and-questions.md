# Decisions and remaining questions

The project owner answered the original architecture questionnaires on 2026-10-05. On 2026-10-06 the product direction shifted from subscription SaaS toward an open-source/free product with an invite-only multi-tenant portfolio deployment. ADR-008 records the distribution pivot; ADR-009 accepts shared identity and invite-only tenancy. On 2026-10-07 ADR-010 accepted first-class multimodal context and a shared embedding-space boundary. On 2026-10-08 ADR-012 superseded ADR-011's split authority: PostgreSQL owns journal plus normalized canon, while Neo4j is replayable. ADR-012 retains the author-review and root-branch boundaries. ADR-013 accepts warning-default declarative story invariants while keeping built-in integrity rules permanently blocking. On 2026-10-09, closing Phase 2 produced three further decisions (ADR-014, ADR-015, ADR-016); they are implemented but recorded as proposed until the owner accepts them.

## Current direction

| Topic | Direction |
| --- | --- |
| Product | Open-source-first, self-hostable fiction authoring and continuity engine |
| Primary deployments | Invite-only multi-tenant portfolio instance under `apps.jaketalling.com`, plus a self-hosted Docker Compose profile |
| Access | Shared portfolio identity followed by a Novel Engine invitation; no public app signup, subscription, or payment system |
| AI usage | User-supplied OpenRouter key or OpenAI-compatible local inference; fake/no-provider workflows remain supported |
| Project funding | No token resale or platform-credit ledger in the current roadmap |
| Identity | Authentik (self-hosted) shared across `*.jaketalling.com`; Novel Engine owns application sessions and authorization. See ADR-009 |
| Collaboration | Workspaces and project sharing use owner/editor/viewer roles; real-time co-editing is not initially required |
| Author experience | Assistant-first: every capability has an assistant path (describe → interview → draft → review) and a by-hand path; advanced features are kept and revealed by level; the assistant never writes canon (ADR-017, proposed 2026-10-10 on the owner's direction) |
| First complete workflow | Describe the story → fill in by interview → plan by conversation → plan a scene → draft → check → review changes → new version (revised 2026-10-10) |
| Context | Include previous prose using semantic, temporal, narrative, continuity, and author-importance signals |
| Multimodal context | Text, images, audio, and video are typed evidence with explicit authority, provenance, branch/time/POV scope, and provider delivery trace |
| Multimodal embeddings | Evaluate EmbeddingGemma 2 as the preferred local candidate at 768/512 dimensions; adopt only after Novel Engine retrieval/resource benchmarks |
| Autonomy | Include delegated low-risk canon writes; protected/high-impact actions remain human-in-the-loop |
| Branches | Fork, compare, and merge; author resolves conflicts or accepts an agent proposal through review |
| Cloud providers | OpenRouter routing behind an internal capability-based port |
| Local providers | Direct OpenAI-compatible Ollama/llama.cpp route in the self-hosted deployment |
| Vector store | pgvector initially, behind a replaceable vector port |
| Credential custody | Self-hosted operator configuration or envelope-encrypted per-user keys in hosted mode; never expose keys through APIs, logs, prompts, or exports |
| Schema | Fixed core plus dynamically registered, runtime-compiled typed project extensions |
| POV secrets | Author may include secrets; prompts separate author truth from POV knowledge and a second pass checks leakage |
| Manuscript | Versioned structured editor document is canonical, with deterministic Markdown/plain-text projections |
| Operational and canon store | PostgreSQL owns projects, jobs, reviews, the immutable journal, normalized/versioned canon, branch heads, outbox, and audit |
| Derived graph | Neo4j is an optional replayable projection with an applied-revision watermark; it is not part of canonical commit success |
| Phase 2 authoring | Low-impact author edits use the journal with recorded author approval; model/import/bulk/protected changes require a distinct human decision |
| Review workflow | Mutation sets are a state machine with per-operation decisions and immutable operation revisions; protected-origin, destructive, bulk, and revert changes need an explicit approval evaluated inside the commit transaction (ADR-014, accepted 2026-10-10) |
| Entity handles and evidence | Names are handles, not identity; slugs are unique among active entities; external ids bind one entity per project; evidence is a bounded pointer; reviewer identity is shown only to members who can write (ADR-015, accepted 2026-10-10) |
| Projection and restore | The canon projection has its own Neo4j labels and a versioned layout; a restore is ledgered and verified before it is successful; Phase 1 graph archives become review proposals only (ADR-016, accepted 2026-10-10) |
| Phase 2 temporal scope | One root branch plus system revisions; fork/merge and world/narrative-time behavior remain Phase 3 |
| Phase 2 media scope | Identity, immutable-version metadata, authority/provenance, explicit links, and archives only; secure ingestion/processing remains later |
| Prototype data | Disposable; deterministic seed data replaces preservation of current databases |
| Consistency | Deterministic checks every generation; model-assisted checks are user-triggered by default |
| Story invariants | Safe declarative rules; author rules default to warnings and require explicit enablement to block; built-in integrity rules are permanently blocking and non-suppressible (ADR-013) |
| Canon reasoning | One point-in-time/history/provenance query boundary serves UI, rules, exports, review agents, and MCP |
| Reader knowledge | Track reviewed disclosures separately from character beliefs and objective canon |
| External assistants | Read-only scoped MCP queries; all write-like actions return reviewable proposals and never commit directly |

## Consequences for implementation

- Shared OIDC, invitations, workspace isolation, and safe self-hosting precede storing real hosted manuscripts; commercial infrastructure remains out of scope.
- PostgreSQL with pgvector remains required development infrastructure. Neo4j is retained while its
  traversal value is evaluated, but ADR-012 makes it optional/rebuildable rather than canonical.
- The core application must function without a project-operated cloud dependency and degrade clearly when no model is configured.
- Local inference is a direct adapter for self-hosting. Using a user's local model from the hosted portfolio instance would require a separately secured companion and remains later work.
- Provider calls record model, token usage, latency, and estimated/actual provider cost where available, but no internal balance or markup policy is required.
- The structured manuscript schema, scene-version lineage, projections, and patch protocol precede the full editor UI.
- Consistency model calls remain opt-in/default-off automation, with recommendations and provider-usage estimates rather than platform-credit prices.
- The text-first import/extraction/review probe follows Phase 2 and precedes deeper branch,
  generation, and multimodal product work.
- Author finding suppressions are versioned and dependency-scoped; they cannot weaken permanent
  integrity, authorization, schema, or journal checks.
- Telemetry is opt-in; manuscript text is never sent anywhere except a provider explicitly invoked by the author.
- Original media remains in tenant-scoped object storage and is sent to a remote model only through an explicit, previewed operation. Captions/transcripts and vectors are derived, not canon.
- The prototype's MiniLM `vector(384)` index must migrate through a versioned rebuild; incompatible embedding spaces are never mixed.

## Decisions needed from the owner

These choices materially affect public release and should be answered before OSS-005/IDN-006 are complete:

1. **Source license.** Recommended default: AGPL-3.0 if network-hosted modifications should remain open. Choose Apache-2.0 if maximizing permissive commercial adoption matters more. A custom “source available” restriction should not be called open source.
2. **Shared identity implementation.** Resolved: Authentik (self-hosted) at `auth.jaketalling.com`; see ADR-009. Remaining sub-decision: whether Authentik enrollment is open, controlled, or invite-only, and who administers recovery.
3. **Portfolio host topology.** Resolved: `novel-engine.jaketalling.com` with host-only application cookies; no cookie shared across `.jaketalling.com`.
4. **Invitation authority.** Decide whether only you can invite initial users, or workspace owners can invite members after you provision the workspace.
5. **Governance.** Decide whether the project uses a single-maintainer/BDFL model initially or a documented multi-maintainer decision process.
6. **Packaging target.** Confirm Docker Compose as the supported first install; desktop bundles and Kubernetes can remain later work.
7. **Structured editor framework.** Select the editor and canonical JSON document schema before editor implementation.
8. **Autonomy defaults.** Confirm the protected-action list and exact thresholds for delegated changes and provider spend.
9. **Model-check automation.** Decide whether users may schedule automatic checks within their own provider budget.
10. **Hosted media policy.** Set initial per-file/workspace size, duration, retention, and processing quotas before enabling hosted uploads; self-hosted operators may override documented safe defaults.

## Retired SaaS selections

Payment provider, subscription entitlements, platform-credit units, markup, and tax/refund scope are no longer release blockers. Identity provider selection and hosted tenant isolation remain release-one work because the portfolio deployment is multi-tenant and invitation-only.

## Decision process

For each remaining selection:

1. Research current vendor/protocol constraints where relevant.
2. Create or amend an ADR with security, data, cost, and migration consequences.
3. Update the roadmap/backlog and API contracts.
4. Add acceptance tests that protect the decision.

## Open owner decisions as of 2026-10-10

From the plan audit ([`audit-2026-10-10.md`](audit-2026-10-10.md)):

1. How a new author gets a working assistant on first run (guided bring-your-own-key, a capped
   hosted starter allowance, or a local default).
2. ADR-017 and the resequenced roadmap (Phases 2.5, 2.6, 2.7).
3. Whether to defer audio and video, Google Docs and LaTeX intake, the local-inference connector,
   and voice fingerprints out of release one.
4. Whether Neo4j remains required infrastructure after Phase 3's query prototype.
5. Licensing and governance; PostgreSQL row-level security (carried).
