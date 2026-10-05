# ADR-002: AI provider routing and user-controlled autonomy

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner
- Related backlog: SCN-002, SCN-003, SCN-006, SaaS-006–SaaS-009

## Context

Users need access to multiple cloud models through one integration and should eventually be able to use compatible local inference. Different authors have different tolerance for automatic AI action.

## Decision

Use OpenRouter as the cloud-model gateway. Keep an internal capability-based AI port rather than exposing OpenRouter response types to the domain. Add an authenticated local companion that maintains an outbound connection to the SaaS and invokes OpenAI-compatible Ollama or llama.cpp endpoints on the user's network.

Represent autonomy as a user/project/workflow policy. Release one includes delegated low-risk canon writes. Protected/high-impact actions—such as conflicted merges, schema changes, broad destructive changes, secret-policy changes, and spend above limits—retain a human-review boundary.

## Consequences

- Model selection, availability, pricing, context limits, structured output, and retry behavior are normalized at the adapter boundary.
- Jobs record actual upstream provider/model identifiers even when requested through a router.
- Platform-credit and BYOK requests share workflow code but use different billing and credential paths.
- Local inference needs a trusted connector or self-hosted topology; it cannot be treated as a normal hosted worker endpoint.
- Evaluation baselines are capability/task-specific rather than tied to one model name.

## Validation

Contract tests run against fake, OpenRouter, and local-compatible adapters. Autonomy tests prove that policy cannot bypass mandatory approval categories.
