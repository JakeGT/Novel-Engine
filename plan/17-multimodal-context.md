# Multimodal context and media memory

- Status: Accepted architectural direction; implementation is planned
- Decision record: [ADR-010](decisions/010-multimodal-context-and-embedding-space.md)
- Initial embedding candidate: `google/embeddinggemma-2`, subject to the evaluation gate below

## Objective

Novel Engine must treat text, images, audio, and video as evidence that can inform planning,
generation, retrieval, and consistency review. An author may attach character portraits,
location references, maps, costume designs, recorded notes, music, dialogue performances, or
video references and expect the right material to be retrieved for the right scene.

Multimodal retrieval does not make media canonical by itself. The original asset, explicit author
links, branch/time applicability, visibility, and authority remain inspectable. Embeddings,
captions, transcripts, and extracted observations are derived and rebuildable.

## Product scenarios

- A scene involving a character retrieves the applicable canonical portrait and current
  appearance state, not an obsolete childhood or alternate-branch image.
- A text query such as “the windswept basalt coast” retrieves linked location prose and a matching
  landscape reference.
- An uploaded image can retrieve visually or semantically related characters, places, objects,
  scenes, and prose passages.
- A recorded author note is transcribed, segmented, linked to its project concepts, and remains
  available as both audio and searchable text.
- A video reference is represented by time-bounded segments, keyframes, transcript, and source
  timestamps rather than one opaque vector.
- A capable generation or critic model receives the selected original media. A text-only model
  receives an explicit degraded representation using captions, transcripts, and reviewed facts.
- The consistency system can compare prose claims with authoritative visual or audio evidence
  without treating uncertain machine observations as canon.

## Design principles

1. **Originals are evidence; vectors are indexes.** Keep the immutable source asset and its hash.
2. **Explicit links outrank similarity.** `DEPICTS`, `REFERENCES`, `SET_AT`, and author pins have
   higher authority than nearest-neighbour results.
3. **Authority is explicit.** Canonical, reference, inspiration, generated draft, and rejected
   media are different states.
4. **Time and branch apply to appearance.** Portraits, maps, costumes, injuries, voices, and
   locations may change across branches and intervals.
5. **Visibility applies before retrieval output.** A visually revealed secret is still a secret.
6. **Derived observations are proposals.** Captions and extracted attributes require provenance,
   confidence, and review before they can become canonical facts.
7. **Provider capability is negotiated.** Do not silently drop an image, audio clip, or video when
   the selected model cannot consume it.
8. **Media is bounded.** Validate type and size, segment long sources, and budget model inputs.
9. **Every representation is versioned.** Model, revision, dimension, prompt/task, processor, and
   source hash identify derived data.

## Domain model

### `MediaAsset`

The project-scoped identity and immutable-original metadata:

- `id`, `workspace_id`, `project_id`, owner/uploader, and creation revision
- media kind: image, audio, video, document, or mixed container
- original object key, media type, byte length, checksum, and detected format
- width/height, duration, frame/sample metadata where applicable
- author title, description, tags, and source/licence/attribution
- authority: `canonical`, `reference`, `inspiration`, `generated_draft`, or `rejected`
- lifecycle: `uploading`, `quarantined`, `processing`, `ready`, `failed`, `retired`, or `deleted`
- visibility and spoiler/secret classification
- branch scope and optional world/narrative validity interval
- current asset-version pointer and provenance

Changing the original creates a new `MediaAssetVersion`; it never mutates a source that existing
context manifests cite.

### `MediaVariant`

A derived, replaceable rendition such as a thumbnail, web-safe image, waveform, transcoded audio,
video proxy, keyframe, or preview. It stores its processor/version, parent checksum, object key,
and technical metadata. Variants never replace the original in an export.

### `MediaSegment`

A retrievable portion of an asset:

- an image or selected region
- an audio time range and optional speaker
- a video time range with keyframe references
- a page or bounded region of a supported document

Each segment has stable offsets/timestamps, a content hash, visibility/authority inherited or
narrowed from the asset, and exact source pointers suitable for evidence citations.

### `AssetLink`

An author or reviewed-model relationship between an asset/segment and a domain record. Links name
their semantic role, such as `DEPICTS`, `VOICE_OF`, `SET_AT`, `MAP_OF`, `INSPIRED_BY`,
`EVIDENCE_FOR`, or `ATTACHED_TO_SCENE`. They include branch/time validity, provenance, confidence,
and review state.

### `DerivedMediaObservation`

A caption, transcript span, OCR result, speaker label, visual attribute, detected entity, or other
machine observation. It records:

- exact asset/segment/version source
- extraction model, prompt/schema/processor version, and job
- structured value, confidence, and evidence region/timestamps
- `proposed`, `accepted`, `corrected`, `rejected`, or `superseded` state
- the reviewed canonical fact ID when promoted through a mutation set

An accepted observation is still not a canon write by itself. Canon changes follow the ordinary
mutation review and commit protocol.

## Authority, time, and visibility

Retrieval uses the following precedence unless a purpose-specific policy says otherwise:

1. author-pinned asset/segment for the current operation
2. active canonical explicit link at the selected branch/time
3. active reference explicit link
4. reviewed structured observation or caption
5. semantically retrieved inspiration
6. unreviewed generated or extracted material

The context trace records both the source authority and why it was selected. Similarity never
overrides a hard branch, visibility, permission, retirement, or validity decision.

Appearance and media facts need temporal semantics. A character may have several portraits for
age, disguise, uniform, injury, or branch. A location may have historical and destroyed states.
The selected scene's branch and world time determine which explicit links are eligible.

## Ingestion and indexing pipeline

```text
authorize upload
  -> stream to quarantine and hash
  -> verify signature/type/limits and scan in hosted mode
  -> persist immutable original
  -> extract safe technical metadata and strip unsafe delivery metadata
  -> create bounded variants/segments
  -> caption/transcribe/OCR/extract observations when configured
  -> generate modality embeddings
  -> publish ready/indexed status and lag
```

Each stage is an idempotent background job keyed by source checksum plus processor configuration.
Failures retain the original when policy allows, expose the failed stage, and can be retried without
duplicating records. Cancellation stops unstarted stages but does not corrupt completed outputs.

Images normally produce one whole-image segment plus optional author-selected regions. Audio is
split at semantic/speaker boundaries with bounded overlap. Video is split into timestamped windows
with sampled keyframes and separately addressable audio/transcript tracks. Segment policies are
versioned and measured; merely accepting the model's maximum context size is not a segmentation
strategy.

## Storage topology

- **Object storage:** immutable originals and derived media variants under opaque,
  workspace/project-scoped keys. Local mode uses the compatible filesystem adapter.
- **PostgreSQL:** assets, versions, variants, segments, links, observations, processing jobs,
  retention/deletion state, and embedding provenance.
- **Neo4j:** reviewed semantic links between assets/segments and canonical/story records where
  traversal adds value. PostgreSQL remains the asset lifecycle authority.
- **pgvector:** rebuildable embeddings for prose, summaries, captions, transcripts, and media
  segments with identical tenant/branch/visibility filters.

Binary media does not live in Neo4j or ordinary PostgreSQL columns. Object keys are never accepted
directly from a client, and authorization is rechecked before issuing a short-lived download URL or
streaming an object.

Project export includes permitted originals, metadata, links, reviewed observations, attribution,
and manifests. Derived variants and embeddings may be omitted when the archive declares how to
rebuild them.

## Embedding strategy

Use a provider-neutral `MultimodalEmbeddingPort` that accepts typed text, image, audio, video, or
interleaved inputs and returns normalized vectors plus provenance. Query and corpus operations are
distinct; task instructions are part of the embedding version.

`google/embeddinggemma-2` is the preferred first local candidate because it maps text, images,
audio, and video into one shared space, supports an 8,192-token context, and has an Apache-2.0
licence. The initial evaluation compares native 768 dimensions with 512-dimensional Matryoshka
vectors; 512 is the expected storage/quality default if the project benchmark confirms it. Do not
use FP16 for this model. Use BF16 on compatible accelerators and FP32 otherwise.

This is a planned migration from the prototype's `all-MiniLM-L6-v2` and PostgreSQL `vector(384)`
schema, not a configuration-only swap. The implementation must:

- add separate document, retrieval-query, fact-checking, and similarity operations
- use the model's appropriate text task prefixes
- normalize after any dimensional truncation
- pin an exact model revision and compatible inference dependencies
- record model/revision/dimension/task/normalization on every vector
- build a new embedding generation beside the old generation and atomically activate it
- never compare or mix vectors from incompatible spaces
- support complete reindex, cancellation, lag reporting, and rollback to the prior generation

The release gate compares EmbeddingGemma 2 with the current MiniLM baseline and at least one strong
text-only local model such as Qwen3-Embedding-0.6B. A hosted embedding model may be measured as a
quality ceiling but is not required for the open-source core.

## Retrieval and ranking

Retrieval begins with deterministic candidates from explicit entity/scene/asset links. Semantic
search then expands across eligible modalities in the shared space. A text query can retrieve an
image; an image can retrieve related prose; an audio or video segment can retrieve connected scene
history.

Hard gates run before any result is exposed and are rechecked when the source object is fetched:

- workspace and project authorization
- active asset/version and processing state
- branch ancestry and world/narrative validity
- POV visibility, secret classification, and requested purpose
- authority/status allowed by the context policy
- selected generation model's supported modalities and limits

Ranking combines explicit-link/author-pin priority, semantic similarity, graph distance, source
authority, narrative and world-time distance, continuity impact, author importance, diversity, and
prior usefulness. Dynamic weights remain metadata and never require re-embedding.

The context inspector groups cross-modal duplicates: a portrait, its caption, and its accepted
appearance facts are one evidence cluster, not three independent votes. It shows thumbnails or
playable bounded previews, exact timestamps/regions, authority, visibility, score components, and
the representation that will actually be sent to the provider.

## Provider-neutral context package

Context packages use a discriminated union rather than embedding binary data in prose:

```text
ContextPart = TextPart | ImagePart | AudioPart | VideoPart
```

Every part carries source/version, purpose, authority, visibility, inclusion reason, budget cost,
checksum, and optional derived-text fallback. Media parts reference an authorized immutable object
or staged provider upload; they do not contain permanent public URLs.

Model routes declare capabilities including supported input/output modalities, media count and
size limits, MIME/container support, context limits, remote-upload semantics, retention, and
whether interleaved media is preserved. The gateway either:

1. sends the selected original/variant to a compatible model;
2. uses a declared caption/transcript/structured-fact fallback and records degradation; or
3. rejects the job before spending provider funds when the operation requires the original
   modality and no compatible route exists.

Provider adapters may translate the internal parts to OpenRouter or a local OpenAI-compatible
shape, but provider-specific message objects never enter application/domain code. Context manifests
record exactly which representation and provider upload/reference was used.

## Consistency integration

Multimodal evidence participates in the same deterministic bundle and review system as prose.
Useful checks include:

- prose appearance versus an active canonical portrait or reviewed appearance fact
- age, clothing, injury, disguise, object, map, or location state at the selected branch/time
- text identifying a person/object that conflicts with a reviewed depiction link
- dialogue/speaker claims versus reviewed voice attribution
- prose or plans exposing secrets visible only in an image/audio/video reference
- media used outside its authored authority, licence, attribution, or intended scene scope

Deterministic comparisons use reviewed structured facts. A model critic may inspect original media
for nuance, but it emits evidence-backed findings rather than facts. Media findings cite asset and
version plus region, keyframe, or timestamp. When a caption/observation conflicts with author canon,
the system reports uncertainty; it never silently updates canon or prose.

## API and user experience

Planned resource groups:

- `/projects/{project_id}/assets` for upload intents, metadata, list/read/retire/delete
- `/assets/{asset_id}/versions`, `/variants`, `/segments`, and `/processing-jobs`
- `/assets/{asset_id}/links` for reviewed domain links and applicability
- `/assets/{asset_id}/observations` for extraction review
- `/assets/search` for filtered text/media similarity and explicit-link search

Uploads use a two-step intent/finalize protocol or bounded API streaming; the server verifies the
object before marking it usable. APIs return authorized preview handles, never storage credentials
or stable public object URLs.

The frontend adds a project media library, entity/scene attachment panels, branch/time/authority
controls, processing state, observation review, and multimodal context cards. Authors can pin,
exclude, replace with derived text, or require original media before starting a model job.

## Security, privacy, and lifecycle

- Validate magic bytes and decoded content, not only extensions or claimed MIME types.
- Apply decompression, pixel/frame/duration, archive, and aggregate workspace quotas.
- Scan hosted uploads and isolate processing from the API process.
- Transcode to allow-listed delivery formats; never execute embedded scripts/macros.
- Remove or separately protect EXIF/GPS and other sensitive metadata.
- Treat OCR, captions, transcripts, filenames, and media-visible text as untrusted prompt content.
- Use short-lived authorized object access and prevent cross-tenant key enumeration.
- Do not log media, captions, transcripts, signed URLs, model payloads, or perceptual/vector data.
- Obtain an explicit preview before sending original media to a remote provider and record the
  provider/retention route in the context manifest.
- Make deletion cover originals, variants, segments, vectors, provider uploads where supported,
  and caches, while retaining only the minimal audit tombstone required by policy.
- Preserve author-supplied source, licence, and attribution through export and context use.

## Failure and degradation behavior

- Media processing failure leaves text/canon workflows operational and exposes a retryable status.
- Vector outage preserves explicit-link and reviewed-fact retrieval.
- Caption/transcript failure does not discard the original asset.
- A text-only model route never pretends it saw original media.
- Stale media indexes display lag and fall back to explicit links/direct metadata.
- Deleted or newly restricted media invalidates context previews and prevents job execution.
- Missing local multimedia codecs or encoders degrade capability health without breaking startup.

## Evaluation programme

Create a publishable synthetic corpus containing:

- characters with multiple ages, outfits, injuries, disguises, and alternate-branch portraits
- visually similar characters and locations as hard negatives
- landscapes, maps, rooms, and objects linked to prose at different authority levels
- audio with multiple speakers, transcripts, and deliberate attribution ambiguity
- video with timestamped scene/location/object changes
- public, secret, and POV-inaccessible media

Measure:

- Recall@5/10, MRR, and nDCG for text-to-media, media-to-text, and media-to-media retrieval
- explicit-link precedence and hard-negative rejection
- branch/time/POV/tenant leakage rate (target: zero in deterministic gates)
- 768-versus-512 quality, raw/index storage, peak memory, and p50/p95 indexing/query latency
- caption/transcript/observation accuracy and author correction rate
- context-package fidelity across OpenRouter and local-capability fallbacks
- rebuild equivalence, cancellation, stale-index behavior, and deletion completeness
- consistency precision/recall when original media is present versus derived-text fallback

No embedding/model migration becomes the default solely from vendor benchmarks. It must improve or
meet the approved Novel Engine corpus baseline within the documented resource budget.

## Delivery placement

- **Phase 1:** preserve tenant-scoped object/artefact and job seams; no media implementation scope.
- **Phase 2:** introduce asset identity, authority, provenance, explicit domain links, and export
  semantics alongside trustworthy canon.
- **Phase 3:** add branch/time/POV applicability for assets, links, and reviewed observations.
- **Phase 4:** implement secure ingestion, processing jobs, multimodal embedding generations,
  cross-modal retrieval, context parts, media library/context inspector, and evaluation baseline.
  Deliver in vertical slices: images first, then audio, then video on the same contracts.
- **Phase 5:** send media through capability-aware generation/critic adapters and add multimodal
  consistency findings and correction review.
- **Phase 6:** allow reviewed media observations to propose ordinary evidence-backed mutations.
- **Phase 7:** harden codecs/scanning, quotas, deletion, provider-retention behavior, backup/restore,
  performance, and self-hosted packaging.

## Exit criteria

The multimodal context system is release-ready when:

- text can retrieve the correct applicable character/location image and cite why
- image/audio/video sources can retrieve relevant prose without crossing hard scope gates
- the author can inspect and override every selected media item
- provider capability fallback is explicit and reproducible from the context manifest
- a complete delete/export/restore/reindex drill passes for every supported modality
- no derived observation becomes canon without the ordinary review/commit workflow
- the approved retrieval and consistency benchmark meets its quality/resource thresholds

## Model references

- [EmbeddingGemma 2 overview](https://ai.google.dev/gemma/docs/embeddinggemma)
- [EmbeddingGemma 2 model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2)
- [Google Hugging Face model repository](https://huggingface.co/google/embeddinggemma-2)
- [Qwen3-Embedding-0.6B comparison candidate](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)

## Revision 2026-10-10: defaults and the assistant path

- **Drop it in.** The author adds an image or a recording and the assistant asks what it is, then
  proposes links and, where needed, bare entries. Authority, visibility, branch scope, and
  validity have defaults (author reference, author-visible, main branch, always) and are advanced
  controls.
- **Delivery placement, corrected.** Asset identity, immutable version metadata, authority,
  provenance, explicit links, a lifecycle audit, and archive inclusion shipped in Phase 2; secure
  ingestion and processing did not and remain Phase 4.
- **Scope.** Images come first. Whether audio and video stay in release one was an owner decision
  listed in [`audit-2026-10-10.md`](audit-2026-10-10.md); decided 2026-10-21 (below).

## Revision 2026-10-10 (second): pictures on entries come first

The owner asked for a direct way to connect a picture to a character or a scene. The model for
this already exists: media assets with immutable versions, and a reviewed `link_asset` operation
that ties an asset to an entry (and later to a scene) with a role. What is missing is everything
an author touches: there is no upload, no "add picture" on an entry, and no picture shown anywhere.

A thin slice is therefore pulled forward to Phase 2.5 (backlog AST-EXP-018):

- upload an image from an entry page, or pick one already in the media library
- link it to that entry with a role: *portrait* for the picture that represents the entry,
  *reference* for anything else; one portrait at a time per entry
- show the portrait on the entry page and beside the entry's name in lists; show references in a
  small gallery
- the same link applies to scenes once scene plans exist (Phase 2.6)
- images only (PNG, JPEG, WebP), with a size limit and a check that the bytes match the declared
  type; stored privately and served only to members of the project
- removing a picture retires the link; the asset and its history remain

Deliberately left in Phase 4: scanning and sandboxed decoding of untrusted files, thumbnails and
variants, captions and other derived observations, audio and video, embeddings and retrieval, and
sending media to a model. Until Phase 4, a picture is something the author looks at, not
something the assistant reads.

## Decision 2026-10-21: audio and video deferred past release one

The owner decided on 2026-10-21 that audio and video are deferred past release one; pictures stay
the only media. Everything in this document about recordings, transcripts, audio ranges, video
windows and keyframes, sound and film context parts, audio/video embeddings and retrieval, and
audio/video checks describes later work, not release one. The contracts that already name them
(`AudioPart`, `VideoPart`, audio and video embedding inputs) stay declared and refused, so adding
them later does not change the shapes pictures use. The release-one exit criteria above apply to
pictures. Recorded in ADR-010 (amendment 2026-10-21) and `11-decisions-and-questions.md`.
