# Decisions and remaining questions

The project owner answered the original architecture questionnaires on 2026-10-05. On 2026-10-06 the product direction shifted from subscription SaaS toward an open-source/free product with an invite-only multi-tenant portfolio deployment. ADR-008 records the distribution pivot; ADR-009 accepts shared identity and invite-only tenancy.

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
| First complete workflow | Plan scene → preview context → draft → review mutations → commit canon |
| Context | Include previous prose using semantic, temporal, narrative, continuity, and author-importance signals |
| Autonomy | Include delegated low-risk canon writes; protected/high-impact actions remain human-in-the-loop |
| Branches | Fork, compare, and merge; author resolves conflicts or accepts an agent proposal through review |
| Cloud providers | OpenRouter routing behind an internal capability-based port |
| Local providers | Direct OpenAI-compatible Ollama/llama.cpp route in the self-hosted deployment |
| Vector store | pgvector initially, behind a replaceable vector port |
| Credential custody | Self-hosted operator configuration or envelope-encrypted per-user keys in hosted mode; never expose keys through APIs, logs, prompts, or exports |
| Schema | Fixed core plus dynamically registered, runtime-compiled typed project extensions |
| POV secrets | Author may include secrets; prompts separate author truth from POV knowledge and a second pass checks leakage |
| Manuscript | Versioned structured editor document is canonical, with deterministic Markdown/plain-text projections |
| Operational store | PostgreSQL remains the operational store for projects, jobs, journal, reviews, outbox, and audit |
| Prototype data | Disposable; deterministic seed data replaces preservation of current databases |
| Consistency | Deterministic checks every generation; model-assisted checks are user-triggered by default |

## Consequences for implementation

- Shared OIDC, invitations, workspace isolation, and safe self-hosting precede storing real hosted manuscripts; commercial infrastructure remains out of scope.
- PostgreSQL with pgvector and Neo4j remain required development infrastructure; the pivot does not reopen Phase 0 storage choices.
- The core application must function without a project-operated cloud dependency and degrade clearly when no model is configured.
- Local inference is a direct adapter for self-hosting. Using a user's local model from the hosted portfolio instance would require a separately secured companion and remains later work.
- Provider calls record model, token usage, latency, and estimated/actual provider cost where available, but no internal balance or markup policy is required.
- The structured manuscript schema, scene-version lineage, projections, and patch protocol precede the full editor UI.
- Consistency model calls remain opt-in/default-off automation, with recommendations and provider-usage estimates rather than platform-credit prices.
- Telemetry is opt-in; manuscript text is never sent anywhere except a provider explicitly invoked by the author.

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

## Retired SaaS selections

Payment provider, subscription entitlements, platform-credit units, markup, and tax/refund scope are no longer release blockers. Identity provider selection and hosted tenant isolation remain release-one work because the portfolio deployment is multi-tenant and invitation-only.

## Decision process

For each remaining selection:

1. Research current vendor/protocol constraints where relevant.
2. Create or amend an ADR with security, data, cost, and migration consequences.
3. Update the roadmap/backlog and API contracts.
4. Add acceptance tests that protect the decision.
