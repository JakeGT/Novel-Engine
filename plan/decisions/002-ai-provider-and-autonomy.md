# ADR-002: AI provider routing and user-controlled autonomy

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner
- Related backlog: SCN-002, SCN-002A, SCN-003, SCN-006, OSS-003, OSS-007, OSS-008, SaaS-006

## Context

Users need access to multiple cloud models through one integration and should eventually be able to use compatible local inference. Different authors have different tolerance for automatic AI action.

## Decision

Use OpenRouter as the cloud-model gateway. Keep an internal capability-based AI port rather than exposing OpenRouter response types to the domain. Self-hosted deployments call configured OpenAI-compatible Ollama or llama.cpp endpoints directly. An authenticated outbound local companion is deferred unless the hosted portfolio application later needs to invoke models on a user's device.

Represent autonomy as a user/project/workflow policy. Release one includes delegated low-risk canon writes. Protected/high-impact actions—such as conflicted merges, schema changes, broad destructive changes, secret-policy changes, and spend above limits—retain a human-review boundary.

## Consequences

- Model selection, availability, pricing, context limits, structured output, and retry behavior are normalized at the adapter boundary.
- Jobs record actual upstream provider/model identifiers even when requested through a router.
- OpenRouter and local requests share workflow code but use different credential/capability paths; the application records usage without maintaining a platform balance.
- Local inference is native to self-hosting. It needs a trusted connector before a remote hosted worker may reach a user's device.
- Evaluation baselines are capability/task-specific rather than tied to one model name.

## Validation

Contract tests run against fake, OpenRouter, and local-compatible adapters. Autonomy tests prove that policy cannot bypass mandatory approval categories.

## Amendment (2026-10-21)

The deferred authenticated outbound local companion is built: ADR-025 (SCN-002A). A workspace on a
hosted or self-hosted site can choose a model on a computer it connected; the route is the provider
`my_computer`, reached through the connector's queue rather than a key.
