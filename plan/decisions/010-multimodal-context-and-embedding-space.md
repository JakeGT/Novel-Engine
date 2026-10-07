# ADR-010: Multimodal context and a shared embedding space

- Status: Accepted
- Date: 2026-10-07
- Owners: project owner
- Related backlog: AST-001–AST-008, CTX-004, CTX-005, CTX-007, CTX-009, CTX-011–CTX-014, CON-014

## Context

Novel Engine needs more than prose retrieval. Authors may use character portraits, landscapes,
maps, objects, recordings, dialogue performances, music, or video references as story evidence.
The correct media may vary by branch, world time, point of view, authority, and scene. Generation
models also differ in which modalities they accept.

The prototype uses a text-only MiniLM embedding with a fixed PostgreSQL `vector(384)` schema and a
single undifferentiated text embedding operation. That cannot provide cross-modal retrieval or
represent provider capability/fallback honestly. Media also creates privacy, ownership, upload,
processing, deletion, and consistency requirements that cannot be deferred to an image-caption
field.

## Decision

Treat text, images, audio, and video as first-class typed context parts. Store immutable originals
in tenant-scoped object storage, lifecycle/provenance metadata in PostgreSQL, reviewed semantic
links in the graph where useful, and rebuildable representations in pgvector.

Use one provider-neutral multimodal embedding boundary capable of mapping supported modalities to
a shared vector space. Explicit author/domain links and hard workspace/project/branch/time/POV
filters precede semantic ranking. Embeddings and machine-derived captions, transcripts, OCR, and
observations are never canonical truth.

Use `google/embeddinggemma-2` as the preferred first local evaluation candidate. Compare 768- and
512-dimensional normalized vectors on a Novel Engine-specific corpus; adopt 512 as the expected
default only if it meets the quality threshold. Pin the exact model revision. The model selection
remains replaceable and cannot be finalized from vendor benchmarks alone.

Media processing runs as idempotent background work. Provider routes declare modality and media
limits. A route must send the selected original/variant, declare a derived-text fallback, or reject
the operation before spending funds; it must never silently omit media.

Deliver image ingestion/retrieval first, followed by audio and video on the same asset, segment,
context-part, authorization, and provenance contracts.

## Alternatives considered

### Caption everything and retain a text-only index

This is simple and works with every text model, but loses visual similarity, nonverbal audio/video
signals, precise media evidence, and image-to-prose retrieval. Captions remain useful fallbacks but
are not sufficient as the primary representation.

### Separate unrelated vector spaces per modality

Specialist models may win individual benchmarks, but cross-modal queries require additional
translation/ranking and make explanations harder. The vector port remains replaceable so a later
specialist secondary index can be added, but the first system uses a unified space.

### Hosted embeddings only

A hosted model could offer high quality with less local operational work, but makes private asset
processing and self-hosting depend on an external provider. Hosted models may be optional quality
routes or evaluation ceilings, not the open-source default.

### Store media directly in PostgreSQL or Neo4j

This simplifies the number of services but is inappropriate for large immutable binaries,
streaming, variants, and retention. Databases store ownership and provenance; object storage owns
the bytes.

## Consequences

### Positive

- Text and media can retrieve one another with one explainable candidate system.
- Original evidence is preserved independently of changing extraction models.
- Branch/time/POV rules apply consistently to prose and media.
- Self-hosted users can keep private assets local.
- Capability-aware fallbacks allow text-only models without pretending they inspected media.

### Negative

- Secure media ingestion, codecs, scanning, segmentation, quotas, and deletion add operational
  complexity.
- The fixed 384-dimensional prototype index needs a migration and complete rebuild.
- Multimodal model/runtime support is newer and requires pinned revisions and stronger evaluations.
- Sending original media to remote providers has greater privacy, cost, and retention impact.

### Follow-up work

- Implement the `MediaAsset`, version, variant, segment, link, and observation records.
- Add a typed multimodal embedding port and discriminated context-part union.
- Create generation-based vector migrations and rebuild/rollback tooling.
- Add capability negotiation to OpenRouter and local provider adapters.
- Add asset APIs, media library/context cards, observation review, and processing status.
- Extend consistency bundles, export/restore, tenant isolation, telemetry, and deletion tests.
- Maintain the detailed implementation contract in `17-multimodal-context.md`.

## Validation

Use a synthetic, publishable corpus of prose and branch/time/POV-scoped character, location,
object, audio, and video references. Measure cross-modal retrieval quality, explicit-link
precedence, zero-leakage hard gates, latency, memory, storage, reindex equivalence, deletion, and
consistency precision/recall. Revisit the preferred model or unified-space choice if a specialist
or text-only-plus-reranker design materially improves the approved product benchmark within the
same privacy and resource budget.

Model facts used by this decision are recorded in the official
[EmbeddingGemma 2 model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2); the
project benchmark, not the published aggregate benchmark, is the acceptance authority.
