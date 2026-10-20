# Phase 4 implementation todo: explainable context and derived memory

- Status: Built (2026-10-17) for text; media processing not built. Phase 5 drafting uses it.
- Scope: `09-delivery-roadmap.md` Phase 4; backlog `CTX-001`, `CTX-002`, `CTX-004`–`CTX-010`,
  `CTX-015`; the multimodal items (`CTX-011`–`CTX-014`, `AST-003`, `AST-005`) as far as pictures.
- Governing documents: `04-ai-and-retrieval.md`, `17-multimodal-context.md`, ADR-005, ADR-020,
  ADR-021 (written with this phase). Level 4 in `19-assistant-first-experience.md`.

## Decisions taken for this phase (ADR-021)

- **Context is assembled, not searched.** For a planned scene, the context is built
  deterministically from the story: the scene and where it sits in the plan, who and what is in
  it, what is true of them at the scene's time, what the viewpoint character knows and gets wrong,
  what has happened, what the reader has been shown, the story rules that apply, pictures, and
  passages of the author's own writing that mention the scene's people and places. Similarity
  search only adds candidates; it never decides what is true.
- **Every item says why.** Each item has a plain-language reason ("Mara is the scene's point of
  view"), a source record, an authority (fact, belief, plan, the author's writing), a word cost
  and a score.
- **Secrets are separated, not filtered silently.** Facts the viewpoint character does not know
  go to an author-only section that is left out by default; the author can include it, and it is
  always labelled as not known to the character (ADR-005).
- **A budget, with hard items first.** The scene itself, its viewpoint and place are never cut;
  everything else is cut lowest-score first, and each cut is listed with its reason.
- **The author's controls are part of the plan.** Pins, exclusions, the budget and whether to
  include secrets are kept with the scene, reviewed and versioned like the rest of the plan.
- **Derived memory is rebuildable.** The author's texts are split into passages and indexed by the
  entries they mention (and by meaning when an embedding provider is configured). The index holds
  pointers, not words; removing a text removes its passages; a rebuild command recreates it; what
  is not yet indexed is shown.
- **Rendering is stable to volatile** so a provider can reuse a cached prefix: policy and rules,
  then who and what, then the scene, then recent writing.

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Context manifest and versioned policy | CTX-001, CTX-002 | Implemented (`backend/domain/context.py`) |
| B. Deterministic retrieval scoped by branch, time and viewpoint | CTX-002 | Implemented for scenes (`context_service.py`) |
| C. Derived memory of the author's writing; rebuild; staleness | CTX-004–CTX-006A | Partly: a passage index, rebuildable, updated inline (not through the outbox); search vectors in versioned generations with filters before ranking (batch 6); no summary or event-block layers |
| D. Ranking, budget, deduplication | CTX-007, CTX-008 | Implemented: fixed scores, word budget, hard items first |
| E. Context preview, pin and exclude | CTX-009 | Implemented; browser tested |
| F. Stable-to-volatile rendering | CTX-015 | Implemented; recording the provider's cached-token count is backlog work (see CTX-015) |
| G. Retrieval and leakage evaluation | CTX-010 | Built (batch 6): a labelled synthetic story with relevance, omission, leakage and what-if cases (`tests/eval/retrieval`); search by meaning off in that set |
| H. Media: pictures in context | CTX-013 | Built for pictures (batch 6): send, describe or leave out; secret and story-time gates; delivery recorded |
| I. Media processing, cross-modal embeddings, EmbeddingGemma | CTX-011, CTX-012, CTX-014, AST-003, AST-005 | Built for pictures (batch 6) against stand-ins: isolated decoding and copies, generations, descriptions as reviewed suggestions; EmbeddingGemma never loaded here; no audio or video |

## What is built

- [x] For any planned scene, the context the assistant would be given, each item with a reason,
      authority, source, location, words and score (`GET /plan/{id}/context`).
- [x] Secrets the viewpoint character does not know are in an author-only section, left out by
      default; neighbours are counted only from facts the viewpoint may know (a leak found in the
      browser review and fixed).
- [x] Facts no longer true at the scene's time are not given; a wrong belief is given with the truth.
- [x] Budget in words with named choices; hard items never cut; every cut listed with its reason.
- [x] Pins, exclusions, budget and secrets kept with the scene through the reviewed plan path
      (`PUT /plan/{id}/context`), so they have history.
- [x] A passage index of the author's writing (`text_passages`, migration 0033): pointers and the
      entries named, never the words; follows every change; forgets removed texts; rebuild command
      and button; texts not yet indexed are shown.
- [x] Search by meaning when an embedding provider is configured; otherwise the panel says passages
      are found by the names they use.
- [x] A voice sample from the most recent writing (research: re-fed passages reduce voice drift).
- [x] Rendering: rules and cast first, the scene last (research: "Lost in the Middle"); checksum.
- [x] A store for manifests actually used (`context_manifests`), for Phase 5 to trace drafts.
- [x] Works in a what-if: the context is the what-if's story.
- [x] Facts are phrased the same in context and on screen (test keeps the two tables in step).

## What is not

- [x] Drafting (Phase 5, ADR-022) now sends this context to the model and keeps the manifest with
      each draft.
- [ ] No relevance evaluation set; retrieval quality is unmeasured beyond the leakage cases.
- [ ] No image, audio or video processing, captions or cross-modal search; a picture is named, not
      shown. EmbeddingGemma was not evaluated and the vector dimension is unchanged (384).
- [ ] No assistant path for "don't use that"; the author's controls are by hand.
- [ ] Hosted deployments do not install the embeddings extra, so search by meaning is off there.

## Design review (browser, `~/Projects/Common/rules.md`)

| Rule | Found | Changed |
| --- | --- | --- |
| — | A secret's other end was named as "connected to" someone in the scene | Neighbours counted only from facts the viewpoint may know; contract test added |
| L4 | Facts read "Mara Venn located at Lantern Harbor" | Same phrasing as the screens ("is located at") |
| S6, T6 | Two buttons on every row | One action per row: "Don't use", or "Use it" for what is left out |
| W2 | Reasons as hover tooltips (a common pattern elsewhere) | Rejected: every reason is shown beside its item |

## Exit criteria (roadmap, Phase 4)

| Criterion | State |
| --- | --- |
| Every context item shows source and inclusion reason | Met |
| Vector deletion/rebuild preserves canonical state and meets retrieval baseline | Partly: the passage index is derived and rebuildable; no retrieval baseline exists |
| Stale index state is visible | Met (texts not yet indexed are listed) |
| Token budgets preserve hard constraints over soft style context | Met |
| Curated POV leakage tests pass | Met for the cases written (secrets, neighbours, what-ifs) |
| Text-to-media and media-to-text retrieval meets the approved baseline | Not met: no media processing |
| Incompatible vector generations cannot mix | Unchanged from Phase 0 (one model, one dimension) |
| Original media is never silently dropped | Met in the narrow sense: pictures are named and the panel says the image is not sent |
