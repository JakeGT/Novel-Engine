# SaaS platform

## Scope

The SaaS platform owns identity, tenancy, access, subscriptions, entitlements, AI funding, credentials, job execution, usage history, abuse controls, and account/project lifecycle. These are correctness and security domains, not incidental middleware.

## Tenant model

Use a `Workspace` as the tenant boundary so an individual account can later invite collaborators without migrating project ownership.

```text
User --< Membership >-- Workspace --< Project
                              |
                              +-- Subscription
                              +-- CreditAccount --< LedgerEntry
                              +-- ProviderCredential
```

Every project-owned PostgreSQL record has `workspace_id` and `project_id` where applicable. Neo4j nodes/relationships, vector metadata, object keys, cache keys, job events, exports, and diagnostics carry the same scope. Client-supplied workspace/user identifiers are never trusted over authenticated context.

Initial roles:

- owner: billing, deletion, membership, credentials, and all project capabilities
- editor: authoring, generation subject to policy/budget, and review/commit
- viewer: read/comment/export only as configured

Initial collaboration uses optimistic document revisions and explicit conflict handling. Real-time co-editing/presence is deferred.

## Authentication and sessions

Keep the application behind an identity-provider port so hosted identity may be managed without coupling domain code to one vendor. Required behavior:

- verified unique account identity
- secure HTTP-only session cookies or equivalently protected tokens
- CSRF protection where cookie authentication is used
- session rotation, revocation, device/session view, and logout-all
- rate-limited signup/login/recovery
- optional MFA path for account and billing protection
- audit records for authentication, credential, billing, membership, export, and deletion events

Development uses seeded identities behind an explicit environment guard. Production cannot enable the development authenticator.

## Subscription and entitlements

Separate subscription state from feature entitlements. A versioned entitlement policy maps plan and account state to limits such as:

- project count, collaborators, and storage
- generation/concurrent job limits
- consistency profiles and scheduling
- model classes/routes
- included platform credits or periodic grants
- retention, export, and support features

Webhook state is evidence, not unquestioned authority: validate signatures, store raw event identifiers safely, process idempotently, tolerate reordering, and reconcile periodically with the payment provider.

Grace periods and failed-payment behavior must preserve export/access to user work even if paid generation is disabled.

## Platform credit ledger

Do not represent credits as a mutable numeric balance alone. Use an append-only ledger with typed entries:

- purchase/grant
- promotional or subscription allowance
- reservation
- reservation release
- usage settlement
- adjustment/refund
- expiry where legally/product appropriate

Balance is a transactionally maintained projection that can be recomputed from entries. Each entry identifies workspace, currency/credit unit, amount, related payment/job/attempt, pricing-policy version, actor, and idempotency key.

### Paid job state flow

```text
estimate
  -> reserve maximum authorized credits
    -> dispatch one provider attempt
      -> record normalized usage/provider cost
        -> settle actual charge and release remainder
```

Cancellation before a provider call releases the reservation. Timeouts and ambiguous provider outcomes enter reconciliation rather than automatically charging again. Retries use separate attempt IDs under one job and a bounded total authorization.

Display estimates as estimates. Preserve exact decimal monetary/provider-cost values internally; never use binary floating point for money.

## BYOK flow

- Accept the OpenRouter key only over authenticated TLS.
- Validate it through the provider adapter without persisting validation responses containing secrets.
- Encrypt it with a per-record data key protected by a managed key-encryption key.
- Store ciphertext, key version, masked label, validation timestamp, status, and owner scope.
- Permit decryption only in the worker execution identity and only for an authorized job.
- Never return the key after creation.
- Support rotation, revocation, deletion, and revalidation.
- Redact secrets from exceptions, tracing, request capture, support tools, and model artefacts.

BYOK avoids platform inference credits but does not necessarily bypass subscription/product limits. This is an entitlement/pricing decision.

## AI routing

The job captures a requested capability/profile, not an arbitrary unchecked provider string. Routing policy evaluates:

- funding source and credential availability
- allowed models for plan/project
- task capability: structured output, context length, reasoning, vision, embedding, prose
- author cost/latency/quality preference
- current model availability and rate limits
- data/privacy restrictions

Record the requested route, selected OpenRouter model, actual upstream details where exposed, parameters, usage, and pricing-policy version.

## Local inference

Local inference is a separate trust topology. The recommended design is an authenticated companion that:

- runs on the user's device/network
- discovers configured OpenAI-compatible endpoints
- makes outbound authenticated connections to the SaaS
- advertises capabilities without exposing the endpoint publicly
- receives only jobs/projects authorized by the user
- returns normalized result/usage telemetry
- supports revocation, version enforcement, and secure automatic updates

The SaaS must communicate clearly that local models may have weaker structured-output or context capabilities. A task may reject an incapable route rather than degrade silently.

## Autonomy policy

Policy resolves in this order: platform mandatory rules → workspace limits → project defaults → workflow/user choice. The most restrictive applicable rule wins for protected operations.

Always protected initially:

- spend above a configured threshold
- schema changes and migrations
- project/workspace deletion
- credential and membership changes
- branch conflict finalization
- broad canon rewrites or destructive retirement
- publication/export to external systems

Every delegated action records the policy version and reason approval was or was not required.

## Data lifecycle

- Workspace owners can export project data independently of subscription state.
- Account deletion distinguishes personal identity deletion from shared-workspace continuity.
- Workspace deletion uses a delayed, reconfirmed process with backup/object/vector/graph cleanup tracking.
- Billing/audit records retain only legally/operationally necessary data.
- Provider prompts/responses and manuscript diagnostics follow configurable retention.
- Credential deletion cryptographically destroys access while retaining non-secret audit facts.

## Launch gates

- cross-tenant penetration/integration suite
- webhook and credit-ledger reconciliation suite
- provider-key redaction and rotation drill
- backup/restore and tenant deletion drill
- spend cap, rate-limit, and abuse response
- terms/privacy/data-processing review
- support tooling with least privilege and audited access
