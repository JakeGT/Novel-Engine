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

## Amendment 2026-10-18: pictures on scenes

A picture can be shown on a scene of the plan (AST-EXP-018). The link is the ordinary reviewed
`link_asset` operation with `target_record_type` `plan_item` and role `DEPICTS`; the commit's
validator accepts only an active plan item of kind `scene` (anything else stays `entity`). No new
role was added: the role says what the picture is to its target, and the target type already says
what the target is, so a `SCENE` role would repeat it and could point at an entry. Old envelopes
are unchanged (the field existed). The picture library (`GET /pictures`) lists each active
picture's newest current version, including pictures no link shows now. Pictures are scaled in
the browser; there are no server-side thumbnails, because that needs image decoding and resizing,
which this release does not do. Scene pictures are not yet named in the scene's context.

## Amendment 2026-10-20: preparing pictures (AST-003, AST-006, AST-008, OPS-007; images only)

Every stored picture version is now prepared once by a `media_process` background job
(`backend/services/media_service.py`), keyed by the version's checksum and `PROCESSOR_VERSION`
(`pictures-1`), so running it again for the same bytes changes nothing and raising the version
makes every picture eligible again. Audio and video are still not processed.

**Decoding happens in a separate, limited process.** The API never decodes a picture. Each
picture is handed on standard input to `backend/adapters/image_worker.py`, run as a fresh
`python -I` process with an empty environment (it sees no secret) and `/` as its directory.
Before Pillow is imported the worker lowers its own limits with `setrlimit`: address space
(`MEDIA_WORKER_MEMORY_BYTES`, 1 GiB), CPU seconds (`MEDIA_WORKER_CPU_SECONDS`, 20), no file
writes and no core dumps; the parent kills it after a wall-clock timeout
(`MEDIA_WORKER_TIMEOUT_SECONDS`, 30) and runs at most `MEDIA_WORKER_CONCURRENCY` at once. The
answer is the copies or a failure code from a fixed list (`PROCESSING_FAILURES` in
`backend/domain/media.py`); no decoder message is passed on, shown or logged, because one can
quote the file. Following the Pillow security handbook, bytes are capped before decoding, only
PNG, JPEG and WebP are tried (`Image.open(formats=...)`), `MAX_IMAGE_PIXELS` is set
(`MEDIA_MAX_PIXELS`, 40 million) with `DecompressionBombWarning` raised as an error, each side
is checked against `MEDIA_MAX_DIMENSION` (12,000) from the header before the pixels are read,
and a JPEG is decoded at a reduced scale. Pillow ≥ 12.3.0 is a core dependency (the font-loader
bomb-check bypasses fixed there). A process with resource limits contains runaway memory and
CPU but is not a filesystem or network sandbox; on a hosted server the container is the outer
boundary, and seccomp or a separate service remains Phase 7 work.

**Outputs.** A 256px thumbnail and a 1024px copy, WebP (quality 80), never enlarged. The photo's
orientation is applied, then the copies are written from bare pixels, so no EXIF, XMP, text or
colour profile reaches them. WebP is supported by every current browser (about 96% of use; not
Internet Explorer 11). Each version also gets a whole-picture segment (`media_segments`, kind
`whole`, in pixels of the picture as shown); authors may mark rectangular regions through the API
(no screen for it yet). Tables: `media_processing` (state, attempts, failure code, size),
`media_variants`, `media_segments` (migration `0046_media_variants`), all in `canon_tables.py`
so project deletion covers them. They are not branch-scoped: a picture is the project's, as
before, so a what-if fork copies nothing.

**Serving.** `GET .../versions/{id}/content?size=thumbnail|web|original` serves the copy to
project members only (the same checks as the original) and falls back to the original while no
copy exists, saying which it sent in `X-Picture-Size`; a fallback is not cached as if it were
the copy. Lists in the app show the thumbnail; an opened picture is the original.

**Failure and retry.** A failure is a state of the picture, not of the job: the job succeeds
with `outcome: failed` and the picture keeps working (the original is shown). Retry
(`POST .../processing/retry`) queues it again once; a picture already prepared from these bytes
by this processor is left alone. A changed original (checksum mismatch) fails as `damaged` and
is never decoded. Processing jobs never count toward a person's limit of waiting work.

**Lifecycle.** Uploads are refused in plain words (413 `storage_full`) when the workspace would
go over `MEDIA_WORKSPACE_QUOTA_BYTES` (2 GiB; originals and copies, retired ones included).
Retention removes stored files no version or copy refers to, only after
`MEDIA_ORPHAN_GRACE_HOURS` (24) so an upload still on its way is never touched, and the copies of
retired versions (originals stay for the history). `MediaService.on_versions_removed(callback)`
tells derived indexes when a version is retired, its picture retired or an upload discarded; a
failing callback never undoes the removal. Archives leave copies out and say so (`derived`,
`left_out`); a restore queues the restored pictures as the person restoring; author-marked
regions travel as `picture_regions`.

**Operations.** Readiness has an optional `media_processing` check (waiting, oldest wait,
failed; degraded after 15 minutes); `/health/capabilities` and the diagnostics bundle report
Pillow, its formats, the processor version and limits; `python -m backend.devtools.media`
(`status`, `backfill`, `reconcile`, `copies`) and `make retention`; runbook
`plan/runbooks/media-processing.md`.

Verified by tests that run the real worker on pictures made in the test (a decompression bomb, a
header claiming 100,000 × 100,000, an over-long side, GIF/BMP/SVG, truncated files, the timeout,
too little memory, idempotent output, orientation and metadata removal), application and
contract tests with a stand-in decoder, integration tests against PostgreSQL, and a browser test
against the real decoder. Not measured: throughput or memory on real author photos, and the
limits on the hosted machine.

Sources: [Pillow security handbook](https://pillow.readthedocs.io/en/latest/handbook/security.html);
[Python `resource` module](https://docs.python.org/3/library/resource.html);
[Running untrusted Python code (limits are not a sandbox)](https://healeycodes.com/running-untrusted-python-code);
[WebP browser support](https://en.wikipedia.org/wiki/WebP) (citing caniuse).

## Amendment 2026-10-20: one embedding port, generations, and the evaluation harness (CTX-011, CTX-012, CTX-014)

**The port.** `MultimodalEmbeddingPort` (`backend/ports.py`) takes typed inputs (`TextInput`,
`ImageInput`, `AudioInput`, `VideoInput`, `InterleavedInput`, in `backend/domain/embedding.py`)
and a task (`document`, `query`, `fact_check`, `similarity`), and returns one normalized vector
per input with its provenance (model, revision, dimension, task, modality, space key, content
hash). Audio, video and interleaved inputs are part of the contract and refused by every current
adapter (`UnsupportedInputError`); nothing is dropped or guessed. `health()` never downloads a
model. The old text-only `EmbeddingPort` stays for the legacy graph store. Adapters:

- `MiniLMTextEmbedding`: the configured MiniLM as a text-only space with no task prompts, so
  it offers only `document` and `query` and refuses the rest. Its space key is exactly the one
  migration 0047 gives the moved rows, so an upgraded install keeps searching without
  re-embedding.
- `EmbeddingGemmaEmbedding`: `google/embeddinggemma-2`, behind the `embeddings` extra and never
  imported unless `EMBEDDING_PROVIDER=embeddinggemma`. It is unavailable until
  `EMBEDDING_GEMMA_REVISION` pins a commit; it loads in bfloat16 where the GPU supports it and
  float32 otherwise, and refuses float16 outright (the card: float16 "returns NaN or silently
  degraded embeddings"). Text uses the card's prompts (`task: search result | query: …`,
  `title: {title|none} | text: …`, `task: fact checking | query: …`,
  `task: sentence similarity | query: …`); pictures go without a prompt as `<|image|>` with the
  image, decoded under the Pillow handbook's rules (byte cap before decoding, pixel limit,
  decompression-bomb warnings as errors). Vectors are made at 768, shortened to the configured
  Matryoshka size (768/512/256/128) and normalized again, since queries and documents must share
  the size and the shortened vector must be L2-normalized.
- `DeterministicEmbedding` (`embedding_fake.py`): text by word directions, pictures by checksum,
  a test may `describe` a picture in words to build near and far pairs; normalized, truncated
  the Matryoshka way. It measures plumbing, never a model.

**Generations.** Chosen: one table `embedding_vectors` with an untyped `vector` column, a
`generation_id`, and one partial HNSW index per generation on `(embedding::vector(N))` `WHERE
generation_id = '<id>'`; every query names one generation and casts to its size, so the planner
uses that generation's index. This is the pgvector README's own recipe for vectors of different
sizes in one table, and it keeps deletion one statement for every generation. Rejected: a table
per generation (DDL per model change, and every delete, purge and reset must enumerate tables);
a fixed `vector(N)` (a new model would need a migration). `embedding_generations` holds model,
revision, dimension, native size, modalities, the task prompts, normalization, a space key and
a state (`building`/`active`/`retired`; at most one of each of the first two). A trigger
refuses a row whose size differs from its generation's or that goes into a retired generation;
the store refuses a row of a modality the generation does not hold, or a query of the wrong
size (`MixedSpaceError`). Search sets `hnsw.iterative_scan = strict_order` (pgvector 0.8.0)
because filtering after an approximate scan can otherwise return fewer rows than asked.

Migration 0047 moves `vector_documents` across without embedding again: rows grouped by the
model and version they recorded each become a generation; the largest named group is active,
the rest retired (rows with no model form an `unknown` generation), and `vector_documents` is
dropped so deletion cannot miss a second copy. The downgrade rebuilds it from the 384-dimension
generation in use.

**Writes and queries never mix spaces.** `PgVectorStore` keeps the `VectorStorePort` contract.
A document is embedded with the `document` task and written only into the generations whose
space key is the configured embedder's (the active one, and a build in progress); its row in any
other live generation is removed, so an older space never answers with words that changed. A
query is embedded with the `query` task and searches the active generation only when it was
built by the configured model; otherwise search by meaning is off and health says why. An
install whose active generation is empty takes the configured model's space on the first write
(nothing to lose or mix).

**Building, switching, rolling back** (`EmbeddingIndexService`, job kind `embedding_reindex`,
`python -m backend.devtools.embeddings`, `make reindex*`). A build makes a generation for the
configured model beside the active one and fills it per project: documents re-embedded from the
active generation's stored text (or cut again from the texts with `--from-texts`), accepted or
corrected captions as text, and, only when the model takes images, each picture's web copy
(made by media processing) as its whole-picture segment. Rows are compared by content hash, so a
build can stop and resume and a second run does nothing. Cancellation is a flag checked between
batches; a stopped build is removed. `activate` refuses while the new generation is missing rows
(the lag) or was made for a different model, unless forced, and switches in one transaction,
retiring the old one and remembering it; `rollback` switches back. Readiness has an optional
`search_index` check (active generation, model, size, rows, lag, any build and its lag) and
`/health/capabilities` a `search_model` entry. Media's `on_versions_removed` hook forgets
retired or discarded versions in every generation; a picture becomes searchable when its
preparation finishes (wired at container build). Archives declare the index as derived
(`derived.search_index`).

**Search for pictures** (`MediaSearchService`). Linked pictures named by the caller come first, in
its order; then pictures by meaning (the picture itself, or its caption in a text-only space),
grouped so a picture and its caption are one result. Every gate is applied before ranking, in the
same SQL as the ordering: project, active and current version, not rejected, whole segment not
retired, and for a point of view, no secret version and no secret link (in the working line) to a
record the point of view does not know. Linked pictures pass the same gates.

**Evaluation** (`tests/eval/multimodal`, `make eval-media`, JSON report in `var/eval/`). A
synthetic corpus drawn with Pillow (12 coloured shapes, 3 redrawn, 5 gated near-duplicates, 12
prose lines) is built and searched through the real services: words to picture (Recall@5/@10,
MRR, nDCG@10, beats hard negatives), picture to words, picture to picture, links first, leaks,
rebuild equivalence, a stopped build leaving nothing, bytes per vector and timings, at 768 and
512 and for a text-only space using captions. It runs in `make test` with the stand-in and is
pinned (`baseline.json`); two tests show the numbers can fail (gates broken on purpose leak; an
uninformed stand-in finds nothing). `--provider embeddinggemma --revision <commit>` runs it
against the real model, loaded once and measured at each size. Results so far are the stand-in's
only: every score 1.0, zero leaks, rebuilds equal. That measures the harness and the gates, not
retrieval quality; no real model has been run, and the 768-versus-512 choice is still open.

Not built: audio and video embedding, region segments, Qwen3 or other comparison models, a
scheduled catch-up (a missed picture or caption is caught by `make reindex-refresh`), and an API
or screen for any of this.

Sources: [EmbeddingGemma 2 model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2)
(prompts, dtype, Matryoshka sizes and re-normalization, shared 8,192-token context);
[pgvector README](https://github.com/pgvector/pgvector/blob/master/README.md) (expression and
partial indexes for several dimensions, iterative index scans from 0.8.0, HNSW up to 2,000
dimensions for `vector`);
[Pillow security handbook](https://pillow.readthedocs.io/en/latest/handbook/security.html).

## Amendment 2026-10-20: pictures in the assistant's context, reviewed descriptions, and the picture page (batch 6 C)

CTX-013, AST-004, AST-005, AST-007 and the picture part of CON-014. This supersedes the
2026-10-18 note that scene pictures are not named in the scene's context.

**Typed context.** `backend/domain/context_parts.py` defines `TextPart | ImagePart | AudioPart |
VideoPart`; a media part names one exact picture version (never bytes, never a lasting address),
why it is there, its accepted description to use instead, and the author's choice for it. Sound
and film parts are declared on the same contract and nothing makes them yet.

**Route capabilities are declared, not discovered.** `ProviderCapabilities` gains image input
and limits (count, bytes, types). The operator declares the server's own route
(`AI_IMAGE_INPUT`, `AI_IMAGE_MAX_*`) and, for workspaces' own providers, which providers may be
sent pictures (`AI_IMAGE_PROVIDERS`); everything else is text only. We did not try to read a
model's modalities from a provider listing at request time: lists differ by provider, change
without notice, and a wrong "yes" would send a private picture somewhere it should not go.
Negotiation (`negotiate`) decides per part and records it: the picture itself; else its accepted
description, marked as a degradation when the picture was wanted; else only its name. Work that
needs the picture itself raises before anything is loaded, queued or spent, and the gateway
checks the route's limits again before building a client (`check_images`), so even a caller that
skipped negotiation cannot get a picture past a text-only route. Pictures go as inline `data:`
addresses after the last user message's words (OpenRouter recommends text first, then images,
and takes base64 data URLs for private pictures), read from the 1024px web copy when one was
made, so the size checked is the size sent.

**The author's choice per picture**, kept on the picture (`media_assets.context_choice`): send
the picture / use its description (the default) / leave out. A picture to send is kept like a
pin and costed against the budget as the picture would be (about width × height / 750 tokens,
capped); one left out is listed as the author's exclusion. The default never sends a picture: a
remote provider sees one only after the author has said so for that picture.

**Scene context.** One item per picture (a portrait, its description and the scene's own picture
of the same asset are one piece of evidence): pictures on the scene itself and pictures of its
cast, each with its reason. A link may now be secret (`visibility`, `known_by`) and apply from a
story time (`applies_from`), all optional on `link_asset` and omitted while unset (ADR-015). A
secret picture reaches a viewpoint only when that character is among those who know what it
shows; otherwise it sits with the author's secrets, with its words, and is never sent as a
picture. A link that applies only from a later time is left out; one the scene's time cannot
settle is given with a note. Pictures found by meaning (area B's `MediaSearchService`) come after
linked ones, behind its gates and this one, and only as their accepted description. Kept
contexts record each picture's version and choice, and with a request, its `delivery`: version,
copy, checksum, size and representation per part.

**Descriptions are reviewed observations.** `media_describe` is model work like any other:
estimated first, asked about above the story's threshold, within the person's limits, usage
recorded once per call. It proposes a caption and appearance details for one exact version, each
with an optional region (fractions of the picture, so it holds for every copy), a confidence,
the model, prompt version (`describe-picture/1`) and job. The author accepts, corrects (their
words win) or rejects; a new description supersedes only what nobody reviewed. An observation is
never a fact: an accepted appearance detail is offered as one only through an ordinary proposal
from the model (`HAS_DETAIL` with `appearance`; joining an existing detail fact by closing it in
the same proposal, because that predicate is one at a time), secret when the picture is, with
evidence pointing at the version and region. Reviewed notes and choices are archived; proposals
nobody reviewed are not, and the archive says so.

**Checking against pictures.** A sixth check pass, "What the pictures show" (`check-pictures/1`),
is given the scene's usable pictures as the route allows and names the picture and version in
each note; with nothing usable it makes no call. Accepted details made facts reach the context
and the continuity pass like any fact. No deterministic prose-versus-picture comparison was
added: the accepted words are free text, and matching them against prose without a model would
be guesswork.

**The picture page** (`/projects/:id/pictures/:assetId`, linked from the media page and the
scene's context) shows the picture, its details, where it is shown with secrecy and time,
"Describe this picture" with its cost first, the suggestions to review with the region drawn on
the picture, "Suggest as a detail", and the three-way choice.

Everything here has run against the scripted model and the local stand-in only; no real vision
model has described a picture, and description quality is unmeasured.

Sources: [OpenRouter image inputs](https://openrouter.ai/docs/features/multimodal/images) (base64
data URLs for private images; text before images; supported types; per-model image limits);
[Claude vision](https://platform.claude.com/docs/en/build-with-claude/vision) (tokens ≈ width ×
height / 750, used for the budget estimate).

## Amendment 2026-10-20: uploads read as they stream, pictures found by words, a picture's file deleted (AST-002, AST-007, AST-008)

**Uploads.** `receive_picture` (`backend/services/media_service.py`) reads an upload chunk by
chunk: a declared `Content-Length` over 8 MB is refused with `413` before anything is read, a body
is refused as soon as more than the limit has arrived, and once its first 12 bytes show it is not
a PNG, JPEG or WebP file it is refused without reading the rest. The SHA-256 is worked out as the
bytes arrive; a `Content-Digest` or `Repr-Digest` the sender gives (RFC 9530, `sha-256=:…:`;
other algorithms are ignored) must match, or the upload is refused as not arrived whole. The
answer carries `received: {bytes, sha256}` of what arrived, before hidden details are taken out
(the stored checksum is of the cleaned file). Not built: an upload/finalize flow and hosted
malware scanning.

**The media page** adds a picture itself: a file, "Show it on" (an entry or a scene), a name.
Every picture is still placed on something, so the library never holds a picture nobody placed,
and the upload is the same reviewed link as from an entry. "Find pictures" looks for every word
in a picture's name and in descriptions the author accepted or corrected (unreviewed ones are not
searched), then asks search by meaning (`MediaSearchService.pictures_for`) when it is set up,
keeping only pictures this line's library offers; each result says how it was found. When search
by meaning is not available the page says so in fixed words rather than the operator's reason.

**Deleting a picture's file.** `POST /assets/{id}/delete`, for owners and administrators, only
when the picture is shown on no entry or scene in any line of the story (a what-if's link
counts). Each version's original and copies are removed from storage, derived indexes are told
through `on_versions_removed`, the version rows are kept as the tombstone (checksum, size, type,
`deleted_at`, retired) with `original_deleted` and `asset_deleted` lifecycle events naming who,
and the asset becomes `deleted`. Deleted versions stop counting toward the workspace's room.
History keeps the picture's name: the label is the author's own and links in history refer to
it. An archive exported afterwards carries the tombstone without the file; an earlier archive
still holds the file. Not built: removing what a model said about the picture (reviewed and
unreviewed descriptions stay, though the picture can no longer be opened), and provider-side
uploads (none are kept). On the page the word "Delete" is used for this one action, with a
deliberate tick: unlike every other ending in the product it erases something (design rules D1,
D5, D6 in `~/Projects/Common/rules.md`).

Ran against the in-memory doubles, a disposable PostgreSQL and a browser test with no model;
search by meaning was exercised with a stand-in only.

Sources: [RFC 9530, Digest Fields](https://rfc-editor.org/rfc/rfc9530) (`Content-Digest` and
`Repr-Digest`, structured byte sequences); [MDN, 413 Content Too Large](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/413)
(the RFC 9110 name for 413).

## Amendment 2026-10-21: the picture worker's sandbox, and pictures in check runs (AST-006, CON-001)

**Sandbox.** The worker already ran as a fresh `python -I` process with rlimits and an empty
environment. It now also shuts itself in after the limits and after loading everything decoding
needs (it decodes a few tiny pictures it makes itself first, so no import happens later), using
only the standard library (`ctypes`), each layer only where the machine has it and never
breaking the worker where it does not:

1. `prctl(PR_SET_NO_NEW_PRIVS)`, which the next two need without privilege.
2. `unshare(CLONE_NEWUSER | CLONE_NEWNET)`: a network of its own. Docker's default seccomp
   profile refuses this without `CAP_SYS_ADMIN`, so on the hosted stack it is usually off; the
   seccomp layer covers sockets.
3. Landlock with an empty ruleset handling every filesystem right the kernel's ABI knows (up to
   ABI 5: execute, read, write, make, remove, refer, truncate, device ioctl), TCP bind and connect
   from ABI 4, and signal and abstract-socket scoping from ABI 6: no path at all can be opened.
4. A seccomp filter written as classic BPF for x86-64 and arm64 (others get none rather than a
   wrong one): any other ABI on the same kernel (32-bit calls, x32) is stopped; opening, making,
   removing, linking, renaming and changing files by path, sockets, `execve`, `fork`/`vfork`, a
   `clone` without `CLONE_THREAD`, `clone3` (`ENOSYS`, so the C library falls back to `clone`),
   `ptrace`, `process_vm_*`, `kill`, mounting, namespaces, `bpf`, `perf_event_open`, `io_uring`,
   keys, modules, `kexec`, `reboot` and the like fail with `EPERM`.

Standard input, output and error were opened before and keep working (Landlock does not reach
descriptors already open). `MEDIA_WORKER_SANDBOX` is `best` (default), `require` (no picture is
decoded without both Landlock and seccomp; the author sees "cannot be prepared on this
installation right now") or `off`. `SubprocessImageProcessor.sandbox_status()` asks one worker,
once, which layers took hold and whether it could read a file, create one, open a socket or start
a process; `/health/capabilities` reports it under `pictures.sandbox` with a plain reason when
less than full, each decode logs its level, and a less-than-full result logs a warning. Measured
overhead on a 2000×1500 JPEG: none outside noise (about 90 ms either way). Not covered:
`stat`-family calls (neither Landlock nor the filter refuses asking whether a path exists),
kernel bugs, and hosted malware scanning. The libseccomp Python bindings were considered; they
are not installed and would add a native dependency, so the filter is written here and tested.

**Pictures in check runs (CON-001).** A check with the assistant that includes the pictures pass
records, in the run's fingerprints, the pictures that pass reads for the scene (asset, version,
the name it was given, send or describe, a digest of the accepted description; never the picture
or its words). Read back, each picture says in plain words what has changed since (a newer
version; its accepted description; no longer given with the scene or now left out; given another
way), the run is out of date when any has, and each note of the pictures pass names the picture
it was compared with and is out of date when that picture (or, if it names none, any of them)
changed. `GET /checks/runs/...` returns them (`scenes[].pictures`, `findings[].picture`).

Sources: [Linux kernel, Landlock](https://docs.kernel.org/userspace-api/landlock.html) (ABI
versions and rights; descriptors opened before are not restricted; `stat` cannot be restricted);
[Linux kernel, seccomp filter](https://docs.kernel.org/userspace-api/seccomp_filter.html)
(no_new_privs, checking `arch`, `SECCOMP_RET_ERRNO`);
[moby default seccomp profile](https://github.com/moby/profiles/blob/main/seccomp/default.json)
(allows `landlock_*`, `prctl` and `seccomp`; `unshare` and `clone3` gated on `CAP_SYS_ADMIN`);
[Docker, seccomp security profiles](https://docs.docker.com/engine/security/seccomp/);
[Pillow security handbook](https://pillow.readthedocs.io/en/latest/handbook/security.html).

## Amendment 2026-10-21 (batch 9 C): picture time windows, two-step uploads, notes of deleted pictures

**Time applicability (AST-001).** A picture link applies for a window of story time: `applies_from`
(batch 6) and now `applies_until`, a `[from, until)` window read like a fact's span (`link_applies_at`
in `backend/domain/media.py`). A scene set outside the window is not given the picture; one whose
time cannot settle it is given it with a note saying so; a window that ends before it starts is
refused. A version of a picture applies for a story time through its link: a link can show one
version (`shows_version`, with `asset_version_id`) instead of following the newest, so "Mara as a
child" and "Mara grown" are two links of one picture, each with its own window and version. Links
made before this keep following the newest version (the column defaults to false, and an upload's
link names its version only as a record). Both fields are optional operation fields omitted while
unset (ADR-015), so earlier envelopes keep their checksums. Versions themselves stay immutable and
carry no time: when a version applies is a statement about the story, so it is a reviewed link.

**Two-step upload (AST-002).** `POST /uploads` states what the picture is for (an entry, a scene, or
a new version of a picture already in the story), its size and its SHA-256; it is refused before
any byte is sent when too large (413), when the workspace has no room, when what it is for is not
in the story, or when 20 are already on their way. `PUT` to the address it answers with streams the
bytes, read never past the promised size, checked against the promised size and digest (and any
`Content-Digest`, RFC 9530), and staged; `POST …/finish` re-reads the staged bytes, checks them
against the promise again, takes hidden details out as for any picture and shows it (one reviewed
link change) or adds the version. A received upload claims "finishing" with a conditional update,
so two finishes never make two pictures; asking again once finished answers with what it made.
Unfinished uploads expire after an hour and their staged file is removed by a sweep the job runner
runs every five minutes (`JobRunner.every`); a staged file left by a crash falls to the batch-6
orphan retention after its grace period. Only the person who started an upload can see or move it.
The one-step upload stays for small pictures. Not built: resuming a partly sent upload (the bytes go
in one request; a failed send is sent again whole), and hosted scanning. Resumption was left out on
purpose: pictures are at most 8 MB, and the IETF resumable-uploads draft (104 Upload Resumption
Supported, `Upload-Offset`) is still a draft; when the limit grows, that draft is the shape to
follow rather than a protocol of our own.

**What a model said about a deleted picture (AST-008).** Deleting a picture's files now first
removes its observations (captions and details, whatever the author decided); their search rows go
with the versions (`on_versions_removed`, caption rows carry the version). A `notes_removed` audit
event records how many (`item_count`), never the words. Proposals already made from an observation
stay: they are the author's own review records. Provider-upload cleanup is still moot: no provider
keeps an upload.

Sources consulted: [tus resumable upload protocol 1.0](https://tus.io/protocols/resumable-upload)
(creation states `Upload-Length` first; checksum and expiration extensions; an unfinished upload may
cease to exist); [IETF draft, Resumable Uploads for HTTP](https://datatracker.ietf.org/doc/html/draft-ietf-httpbis-resumable-upload/)
(104 status, `Upload-Complete`, `Upload-Offset`; still a draft); [Amazon S3, checking object
integrity on upload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html)
(the server computes the checksum itself and refuses a mismatch before storing; a presigned PUT does
not bind the digest, so the server must check what arrived, as finish does here).

## Amendment 2026-10-21: audio and video deferred past release one (owner decision)

The owner decided on 2026-10-21 that audio and video are deferred past release one; pictures stay
the only media. The decision above to "deliver image ingestion/retrieval first, followed by audio
and video on the same asset, segment, context-part, authorization, and provenance contracts"
still describes the order, but audio and video are no longer release-one work: no audio or video
upload, processing, transcripts, segments, embeddings, retrieval, context delivery or checks.
What exists stays as it is: `AudioPart` and `VideoPart` are declared and never sent, and every
embedding adapter refuses audio, video and interleaved inputs. Backlog items that list audio or
video (CTX-011–CTX-014, AST-003, AST-005, CON-008, CON-014, SCN-002) now say so instead of
listing them as not built. Recorded in `11-decisions-and-questions.md`.

## Amendment 2026-10-21 (batch 10 B): marked parts of a picture, writing read from a picture, notes that point at a part

**Marking parts (AST-007, AST-003; migration `0060_picture_regions`).** The picture page has a
"Marked parts" section: draw a rectangle on the picture with the pointer, or type where it is
(from the left, from the top, width, height, in pixels of the picture as shown), give it a name
and say which entry it shows; change it, show it on the picture, or remove it with Undo. A part
is the author's own markup, like its name: `media_segments.entity_id` points at an entry and
states nothing about the story, so it is not a reviewed change (the reviewed picture link still
decides where a picture is shown and who may see it). The entry must be in the story when the
part is saved. Typing is the way that always works; drawing is a shortcut hidden from assistive
technology, so the screen meets WCAG 2.2's 2.5.7 Dragging Movements (a single-pointer or typed
alternative to every drag). Parts are stored in pixels of the picture as shown and handed to
models and to the screen as fractions of it, the same idea as W3C Media Fragments' spatial
dimension (`xywh=` in pixels or percent), so they hold for every smaller copy.

**Writing read from a picture (AST-003, AST-005).** The describe job (`describe-picture/2`) lists
the marked parts and asks the vision model for a caption of each and for the writing it can read,
copied letter for letter, never completed, corrected, translated or guessed, with where it is. Each
becomes an observation (`text_in_picture`, or a caption pointing at its part) reviewed like any
other: accept, correct, reject; never a story fact (writing cannot be offered as a detail). Writing
that asks for something is only ever copied, inside the request's marked material. No local OCR
engine is used (owner decision for this batch): the model that describes the picture already sees
it, and a second engine would be one more decoder to sandbox. Vision models are known to invent
text they cannot read in degraded pictures, recited from language patterns rather than read
(He et al., 2025), which is why every reading is a suggestion with its own confidence and the
author's correction wins. A model rectangle lying mostly (60%) inside a marked part points at that
part. Accepted part captions and writing join the picture's description for a route that cannot
see pictures ("Written in the picture: “…”").

**Parts in the search index (CTX-012).** Each part with a name or an entry is its own index row
pointing at its segment: its words (name, the entry it shows, what the author accepted about it,
kind `region_caption`) in every generation, and, in a generation whose model takes images, the
part itself (kind `region`), cut from the web copy in the same limited picture worker that
prepares pictures (`crop` in `image_worker.py`). Marking, changing or removing a part, or reviewing
a note about one, brings the story's parts up to date at once; a removed part is refused by the
search gate (the segment is retired) before its rows go. Lag and builds count parts. "Find
pictures" also finds a picture by a part's name or entry and says so.

**Notes that point at a part (CON-014).** The pictures check (`check-pictures/2`) lists each
picture's marked parts, numbered across the request; a note may name one (`part`) or, for a
picture sent, give a rectangle (`region`). The note keeps the picture version and the part
(`draft_findings.picture_anchor`), and the studio shows the picture small with the part drawn on
it and a link to the picture page. A note naming a part that was not listed keeps the picture
only; a rectangle for a picture given only as words is dropped. Notes keep the rectangle they were
made with when a part is later moved or removed.

Not built: a model-proposed part (the author marks parts; the model only describes them),
fallback-quality measurement, and drawing beside the form on a phone (the picture sits above
the form there; the typed numbers work everywhere). Everything ran against the scripted model,
the local stand-in model and the stand-in embedder only.

Sources consulted: [Understanding WCAG 2.2 SC 2.5.7 Dragging
Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html) (a feature that
uses dragging must also work with a single pointer without dragging; typing a value counts);
[W3C Media Fragments URI 1.0, spatial dimension](https://www.w3.org/TR/media-frags/#naming-space)
(rectangles as `xywh` in pixels or percent of the image); [He et al., "Seeing is Believing?
Mitigating OCR Hallucinations in Multimodal Large Language Models",
arXiv:2506.20168](https://arxiv.org/abs/2506.20168) (models invent text in degraded images instead
of admitting they cannot read it).

## Amendment 2026-10-21 (batch 10 A): what each model takes, and what is sent before it is (CTX-013)

**Per model, not per route.** `backend/services/model_capabilities.py` looks each model up by name:
the operator's table (`AI_MODEL_CAPABILITIES`, JSON keyed by `provider/model` with a trailing `*`
for a family; it always wins and is checked at start-up), else the provider's public list where it
publishes one with these details (OpenRouter `GET /api/v1/models`: `architecture.input_modalities`,
`context_length`, `top_provider.max_completion_tokens`, `supported_parameters`), read at most once a
day (`AI_MODEL_LIST`, on by default; a failed read waits an hour), parsed off the event loop and
kept in `model_capabilities` (migration 0059, install-wide public metadata). The connector already
reports what it found on the author's computer (context per model, whether it keeps to a schema).
OpenAI, Anthropic, Google, xAI, Mistral and DeepSeek do not publish modalities in their model
lists, so for them the table or the route's settings decide.

The operator still decides whether pictures may leave for a route at all (`AI_IMAGE_INPUT`,
`AI_IMAGE_PROVIDERS`). Within that, a model a list says cannot see pictures is given their
descriptions (recorded as a degradation, as before); the table may lower how many pictures go; a
model said not to keep to a schema is asked in words; its context size travels with the route
(`ProviderCapabilities.max_context_tokens`, `max_output_tokens`, `source`). Unknown models behave as
before.

**What will be sent, before it is.** `backend/services/send_preview.py` works out, for a draft or a
check, the provider and model it goes to, the words of the story it reads, each picture that would go
as itself (by the same `negotiate` the request uses) and where it goes in plain words: a company's
servers, the author's own computer, or the site's own network (a loopback, private or dot-less host).
It is shown beside a draft's cost and in every size question (`409 needs_confirmation` now carries a
`sending` member, an RFC 9457 extension). A request that would send pictures to a company's servers
is asked about first whatever its size, and confirmed the same way (`X-Confirm-Tokens`). Names and
counts only: never a key or the story's words. The dialog shows each picture small.

**Not covered.** Describing a picture (it shows its cost first and is about sending that picture)
and scheduled audits (nobody is there to ask) do not show the preview; staged provider uploads.

Sources: OpenRouter models API <https://openrouter.ai/docs/api/api-reference/models/get-models>;
RFC 9457 problem details, extension members <https://www.rfc-editor.org/rfc/rfc9457#name-extension-members>.

## Amendment 2026-10-29: an embedding service instead of a model on the server

**Owner decision (2026-10-29):** offer a cloud option for search vectors, opt-in by the operator,
with authors told plainly where their writing goes. Asked because a small VPS has no GPU and the
hosted image carries no model, so it searched by words only.

**Chosen.** `EMBEDDING_PROVIDER=api` builds `ApiTextEmbedding` (`backend/adapters/embedding_api.py`):
`POST {EMBEDDING_API_BASE_URL}/embeddings` in the OpenAI shape, which OpenAI, Google's OpenAI
compatibility layer, Mistral, OpenRouter, Ollama and LM Studio all accept. Text only; pictures are
refused (`UnsupportedInputError`), as audio and video are by every adapter. The space is the model
name, `api:<host>` as its revision (the same model name at another service is another space), the
configured size and no task prompts (`plain`). The size is set by the operator, at most 2,000 (the
HNSW limit above), and sent as `dimensions` unless `EMBEDDING_API_SEND_DIMENSION=false`: Google's
`gemini-embedding-001` gives 3,072 and is Matryoshka-trained, and OpenAI's `text-embedding-3`
models accept the field. Vectors are normalized again, since a shortened one is not unit length.
Requests carry at most 64 texts and 120,000 characters (OpenAI allows 2,048 inputs and 300,000
tokens). A refused key, unknown model or bad request (400/401/403/404/422) is not retried; while
writing the index a busy or failing service is tried three times, waiting as `Retry-After` asks
(at most ten seconds), but a query, which someone is waiting on, is tried once for at most eight
seconds and search then falls back to words. No
message quotes the service's answer, which can echo what was sent; logs carry the host, counts and
status only. `health()` never calls the service. https is required except for a model on this
machine, which also needs no key.

**Where the words go.** `GET /ai/search` answers `server`, `service` (with the operator's name for
it and the host) or `off`, and whether the index is in use; the Assistant page shows it to every
author as "Search by meaning", since it is the site's choice and not a workspace's key. On a hosted
site the service is paid for under the operator's key: this is the one place the site itself pays
for a model, and only when the operator chooses it.

**Rejected.** Per-workspace embedding keys: one search index serves the whole site, and vectors
from different keys' accounts would still be one space, so a key per workspace adds custody without
separating anything. A CPU-only MiniLM image: offered to the owner, not chosen now.

Sources: OpenAI embeddings reference <https://developers.openai.com/api/reference/resources/embeddings/methods/create>;
Gemini OpenAI compatibility <https://ai.google.dev/api/compatibility>; Gemini Embedding GA
<https://developers.googleblog.com/gemini-embedding-available-gemini-api/>.
