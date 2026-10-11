# Open-source distribution and invite-only hosting

> The filename is retained to avoid breaking existing links. ADR-008 removes subscriptions and platform-funded inference; ADR-009 retains multi-tenancy for the invite-only portfolio deployment.

## Scope

Novel Engine has an open-source core and two supported profiles: a free, invite-only multi-tenant instance in the owner's portfolio, and a self-hosted installation. This layer owns packaging, identity integration, invitations, tenancy, configuration, provider credentials, upgrades, backups, and privacy defaults. It does not own subscription billing or platform inference funds.

## Deployment modes

### Self-hosted: trusted single user

One operator controls the host, database, provider configuration, and projects. The application has an explicit local identity and does not require signup. It should bind to localhost by default unless the operator deliberately configures a reverse proxy and authentication.

```text
Author/browser
      |
      v
React + FastAPI + worker
      |
      +-- PostgreSQL/pgvector
      +-- Neo4j
      +-- filesystem/object adapter
      |     `-- immutable prose and media originals/variants
      +-- local OpenAI-compatible model (optional)
      +-- OpenRouter with author's key (optional)
```

Local mode supplies an explicit local workspace/user identity through the same scoped application contracts used by hosted mode.

### Hosted: invite-only portfolio application

The production instance is one application within `apps.jaketalling.com`, served from the dedicated host `novel-engine.jaketalling.com` (`apps.jaketalling.com` acts as the launcher). Authentication is delegated to the shared **Authentik** identity service at `https://auth.jaketalling.com` (issuer `https://auth.jaketalling.com/application/o/novel-engine/`); Novel Engine owns authorization and does not grant access merely because an identity exists. Authentik is provisioned at `https://auth.jaketalling.com` with a Novel Engine application and confidential OIDC client; see ADR-009 for the topology and provisioning status.

```text
Browser
  |  OIDC authorization code + PKCE
  v
Shared identity service: Authentik (auth.jaketalling.com)
  |
  v
Novel Engine host-only session
  |
  +-- Workspace --< Invitation
  |          `--< Membership >-- Local identity mapping
  `--< Project
```

Every hosted record and derived artefact carries workspace/project scope where applicable. Owner/editor/viewer roles govern application actions. Real-time co-editing remains deferred; ordinary version conflicts use optimistic concurrency and review.

Hosted media uploads additionally require tenant storage/processing quotas, isolated
scan/transcode workers, opaque object keys, short-lived authorized previews, and complete
original/variant/vector cleanup. Self-hosted mode can disable processors or use local-only media
context without sending originals to an external provider.

### Deferred: public managed service

A service with open signup requires a separate accepted ADR covering abuse controls, funding, privacy, availability, support, data deletion, and legal obligations. Subscription billing, token resale, and platform-funded inference are not part of the current implementation sequence.

A limited public demo or sponsor-funded service must still impose quotas and must not imply unlimited free model inference.

## Installation and upgrades

Docker Compose is the proposed reference package because the existing architecture depends on PostgreSQL/pgvector and Neo4j. The supported path must provide:

- versioned images or reproducible source builds
- a generated local configuration with no committed secrets
- safe localhost/network defaults and explicit volume locations
- health/readiness checks and useful startup diagnostics
- explicit database migrations before application startup
- pre-upgrade backup instructions and compatibility notes
- rollback guidance where schema compatibility permits it
- deterministic seed/sample data that is never confused with user work

A one-command installer, desktop wrapper, and Kubernetes manifests are later packaging options, not parallel release blockers.

## Identity, invitations, and authorization

Local mode uses a deliberate single-user principal so application policies do not depend on anonymous `None` values. Its UI and diagnostics clearly say that it is suitable only for a trusted host.

Hosted mode uses the shared Authentik OIDC provider with the authorization-code flow and PKCE (see ADR-009 for the issuer, client, redirect, and logout URLs). Validate issuer, audience, signature, nonce, state, redirect URI, and token lifetime. Map `(issuer, subject)` to a local identity; do not use email as the durable key. Each portfolio application uses its own OIDC client and host-only session cookie rather than a cookie scoped to `.jaketalling.com`.

An authenticated identity has no Novel Engine access until an invitation is accepted. Invitations are random, hashed at rest, time-limited, single-use, revocable, optionally email-bound, and audited. Acceptance rechecks the authenticated identity and the inviter's current authority in one transaction.

Novel Engine owns `Workspace`, `Membership`, `Invitation`, and project grants. Domain and repository APIs require workspace/project scope. Core canon models must not depend on identity-provider types.

## Provider configuration and secrets

The author may configure:

- an OpenRouter API key for cloud inference
- one or more OpenAI-compatible local endpoints such as Ollama or llama.cpp
- the deterministic fake provider for development and evaluation
- no provider, retaining manual authoring and deterministic checks

Self-hosted secrets come from an ignored environment/secrets file or an operating-system secret mechanism. In hosted mode, per-user OpenRouter keys require envelope-encrypted storage, authorized worker-only decryption, rotation, deletion, and access audit. The application must:

- never return a secret after configuration
- redact it from errors, logs, traces, support bundles, prompts, and exports
- distinguish local endpoints from remote endpoints in the UI
- show which provider will receive manuscript context before a job runs
- allow rotation/removal without corrupting job history

The portfolio instance never supplies a platform key for ordinary user jobs. Each cloud inference job resolves the authenticated user's authorized credential and project policy.

## Model usage and limits

There is no platform-credit balance in the core application. Jobs still record normalized input/output/cache tokens, requested and actual model route, latency, and provider cost where the upstream exposes it. Authors can set per-job and periodic token/cost limits. Retries and cancellation must not create duplicate committed output or silently evade those limits.

The UI labels provider costs as estimates and makes clear that OpenRouter bills the author's account directly. Local model usage may have no monetary amount but still reports resource/time metadata where available.

## Autonomy policy

Policy resolves from application safety rules to project defaults to the workflow/user choice. The most restrictive applicable rule wins for protected operations.

Always protected initially:

- provider use above an author-configured limit
- schema changes and migrations
- project deletion
- credential changes
- branch conflict finalization
- broad canon rewrites or destructive retirement
- publication/export to external systems

Every delegated action records the policy version and why approval was or was not required.

## Privacy and outbound connections

- Telemetry is off by default and opt-in when implemented.
- Error monitoring (Sentry) is opt-in via a configured DSN; when enabled it sends scrubbed, content-free events (no request bodies, cookies, sensitive headers, user PII, or breadcrumbs), and the web client uses a separate `VITE_` variable.
  Revised 2026-10-11: the hosted site takes both DSNs from secret files; the server hands the
  browser its DSN and release in the page it serves, so nothing is built into the image; events
  carry the deployed commit as their release and the internal ids of the person, workspace and
  project; the browser side switches off every data-collection category of its SDK and drops
  breadcrumbs, and is covered by a browser test against a stand-in. Source maps are built but
  kept out of the site, with an upload script that has not been run against a real project.
- Logs are metadata-first and omit manuscript/prompt content by default.
- Imports and exports remain local unless the author selects an external integration.
- The context preview identifies what will be sent to a configured remote model.
- Update checks, crash reporting, model downloads, and external integrations are documented individually and independently configurable.
- A support bundle must be previewable and redacted before sharing.

## Data ownership and lifecycle

- Authors can export a readable manuscript plus a full-fidelity, versioned project archive.
- Backup and restore cover PostgreSQL, Neo4j, prose/object files, and version metadata consistently.
- Project deletion is explicit and includes derived vector/index data.
- Media deletion includes originals, variants, segments, derived observations/vectors, caches, and remote-provider uploads where the provider supports deletion.
- Provider prompt/response retention is configurable locally.
- Published/exported manuscripts never require Novel Engine to remain installed or available.
- Hosted account unlinking/deletion is distinct from workspace ownership transfer and shared-project retention.

## Open-source project operations

Before a public release, add:

- an OSI-approved license selected by the owner
- contribution and code-of-conduct expectations
- a security policy and private vulnerability-reporting route
- third-party notices and dependency/license scanning
- versioning, changelog, release, and support policies
- issue templates with diagnostics that exclude story content and secrets
- a documented governance and maintainer model

## Release gates

- clean-machine install and upgrade rehearsal
- backup/restore and full project export/import drill
- provider-key redaction and rotation tests
- OIDC/session configuration and invitation replay/expiry/revocation tests
- cross-tenant API, repository, graph, vector, object, job, cache, event, and export tests
- hosted mode refuses to start with the local identity adapter
- localhost-safe default deployment and explicit public-exposure warnings
- no-provider/manual workflow and deterministic consistency suite
- local OpenAI-compatible and OpenRouter adapter contract tests
- privacy/outbound-connection documentation review
- license, notices, contribution, and security documents present

## Revision 2026-10-10: first run and the provider key

The product is now assistant-first (ADR-017), which raises a conflict this document did not have
to resolve: "the portfolio instance never supplies a platform key" means a newly invited author
cannot use the main way in until they have obtained an OpenRouter key.

Until the owner decides otherwise, the position is unchanged, and the mitigation is the first-run
work in Phase 2.5: provider setup is a guided first step, with the cost of a typical first session
stated; the by-hand path is fully usable in the meantime; and the assistant explains, wherever it
appears, why it is unavailable and how to connect a key. A capped starter allowance funded by the
hosted instance is the alternative; it would revise ADR-008 and add cost exposure, and is listed
as an owner decision in [`audit-2026-10-10.md`](audit-2026-10-10.md).

Status: the hosted deployment went live on 2026-10-10 for the owner (`deploy/hosted`).

## Revision 2026-10-10 (second): one gate, then onboarding

The owner decided, following the platform guide (`platform-guide.md`, section 4): **who may use
Novel Engine is decided once, by Authentik.** A person who completes sign-in gets an account and is
onboarded: they name a first story and a library (a workspace of their own) and land in it. There
is no second, in-product gate.

- A signed-in person who owns no workspace may create one (`ALLOW_PERSONAL_WORKSPACE`, on by
  default). Creating more than one is still limited to platform admins unless
  `ALLOW_WORKSPACE_SELF_CREATION` is set.
- Workspace invitations remain, for sharing a workspace with someone, not for admission.
- This supersedes the statements in this document, ADR-009, and the Phase 1 evidence that hosted
  workspaces can only be created by a platform admin.
- Hosted sessions last 12 hours on the platform deployment, because Authentik sends no notice when
  access is removed.
- Sign-out returns to the product's front page: the ID token from sign-in is kept with the session
  (never exposed by the API) and sent to the provider at sign-out, which is the only form in which
  Authentik accepts a return address.

The provider-key question is settled the same way for now: bring-your-own-key stays, and onboarding
ends on the project's first screen with a prompt to connect a key; everything works by hand until
then.

Not adopted from the platform guide, by the owner's decision that gaps which are not security
issues may stay: a `/healthz` alias, port 8080, `platform-admin` group membership as the source of
admin rights (a configured list of user ids is used instead), and the platform's visual style
(Novel Engine keeps its own). Known and left: the two database password files are mode `0444`
inside an owner-only directory, and nothing prevents local single-user mode being configured with
`APP_ENV=production` on a self-hosted install.

## Revision 2026-10-10 (third): more providers, and a model chosen per workspace

Bring-your-own-key is no longer OpenRouter only. A workspace may connect one key each for
OpenRouter, Anthropic, OpenAI, Google, xAI, Mistral, and DeepSeek, and an owner chooses which
provider and which model the assistant uses (`workspace_ai_settings`, `/api/v1/ai/*`, the Assistant
page). Previously the model was one site-wide setting.

- Every provider is reached through its OpenAI-compatible endpoint, so there is one adapter
  (`backend/domain/providers.py` is the catalogue). Where a provider enforces a JSON schema it is
  asked to; where it does not (Anthropic's and DeepSeek's compatible endpoints), or refuses for
  the chosen model, the required shape is described in the prompt and the answer is validated by
  the server before use.
- Model names are not hard-coded as a list. The Assistant page reads the models the workspace's
  key can use from the provider, and a name can also be typed. Three providers carry a suggested
  model; for the others (OpenAI, Google, xAI) the owner must pick one before the assistant works.
- With no explicit choice, the newest connected key is used with its suggested model. Removing
  the chosen key falls back the same way.
- A self-hosted copy is unchanged: the operator sets `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`.
- **Not verified:** no provider, including OpenRouter, has been called for real from this
  codebase. Addresses and suggested model names are from the providers' public documentation as
  known when written. Anthropic describes its compatible endpoint as intended for evaluation; a
  native adapter would be the sturdier choice if Anthropic direct becomes the main route.

