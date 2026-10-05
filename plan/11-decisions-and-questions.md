# Decisions and remaining questions

The project owner answered architecture questionnaires on 2026-10-05. Accepted directions are summarized here and recorded in `plan/decisions/`.

## Accepted product decisions

| Topic | Decision |
| --- | --- |
| Product | Hosted, multi-tenant SaaS with user signup and subscriptions |
| Collaboration | Project sharing with owner/editor/viewer roles; real-time co-editing is not required initially |
| AI usage | Users may purchase platform-managed AI credits or provide an OpenRouter API key |
| Credit presentation | Ledger tracks true model/provider costs and pricing policy; user-facing credits/pricing may intentionally abstract raw provider cost to create margin |
| First complete workflow | Plan scene → preview context → draft → review mutations → commit canon |
| Context | Include previous prose using semantic, temporal, narrative, continuity, and author-importance signals |
| Autonomy | Include delegated low-risk canon writes; protected/high-impact actions remain human-in-the-loop |
| Branches | Fork, compare, and merge; author resolves conflicts or accepts an agent proposal through review |
| Cloud providers | OpenRouter routing behind an internal capability-based port |
| Local providers | Authenticated local companion connects outbound to SaaS and calls OpenAI-compatible Ollama/llama.cpp endpoints |
| Vector store | pgvector initially, behind a replaceable vector port |
| BYOK custody | Envelope-encrypted server-side; decryptable only by authorized workers for execution |
| Schema | Fixed core plus dynamically registered, runtime-compiled typed project extensions |
| POV secrets | Author may include secrets; prompts separate author truth from POV knowledge and a second pass checks leakage |
| Manuscript | Versioned structured editor document is canonical, with deterministic Markdown/plain-text projections |
| Operational store | PostgreSQL from the start for tenants, subscriptions, credits, credentials, jobs, journal, and operational data |
| Prototype data | Disposable; build deterministic seed data instead of preserving current databases |
| Consistency | Deterministic checks every generation; model-assisted checks are user-triggered by default, while the UI recommends scene and chapter/act checks |

## Consequences for implementation

- Authentication, tenant isolation, memberships, billing, usage metering, and key management are foundation work.
- PostgreSQL plus pgvector and Neo4j are required development infrastructure.
- Platform pricing uses versioned internal cost and markup policies. Users see clear credit estimates/charges without requiring raw provider-price disclosure.
- Delegated autonomy requires risk classification, spending limits, protected operations, audit, reversibility, and per-workflow policy evaluation.
- The local companion is a separately versioned security boundary with device authorization and revocation.
- The structured manuscript schema, scene-version lineage, projections, and patch protocol precede the full editor UI.
- Consistency model calls are opt-in/default-off automation, but contextual recommendations explain when a compact or deep check is valuable and estimate its credit cost.

## Proposed OpenCodeReview-inspired direction

Adopt the deterministic-envelope pattern from Alibaba OpenCodeReview for prose consistency, adapted to structured prose and graph dependencies rather than Git files. The proposed architecture is documented in `16-open-code-review-adaptation.md` and should become an accepted ADR after a prototype proves grouping coverage, precision, and cost.

## Remaining implementation selections

These no longer change the high-level architecture but need ADRs before their subsystem is implemented:

1. Hosted identity vendor versus first-party authentication.
2. Payment/subscription provider and regional tax/refund scope.
3. Structured editor framework and the canonical JSON document schema.
4. Local-companion transport, packaging, signing, and update mechanism.
5. Initial credit unit, markup disclosure, expiry/refund policy, and included subscription allowance.
6. PostgreSQL tenant-isolation technique: application-scoped queries alone or defense-in-depth row-level security.
7. Default protected-action list and exact delegated-autonomy risk thresholds.
8. Model-check recommendation triggers and whether users can enable automatic checks within a monthly consistency budget.

## Decision process

For each remaining selection:

1. Research current vendor/protocol constraints where relevant.
2. Create or amend an ADR with security, data, financial, and migration consequences.
3. Update the roadmap/backlog and API contracts.
4. Add acceptance tests that protect the decision.
