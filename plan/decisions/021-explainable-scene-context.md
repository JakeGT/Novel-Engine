# ADR-021: Scene context is assembled from the story, explained item by item, and the author's to shape

- Status: Accepted in effect (2026-10-17), under the owner's standing direction to continue the plan.
- Context: Phase 4; `04-ai-and-retrieval.md`, `17-multimodal-context.md`, ADR-005 (POV secrets),
  ADR-020 (story time, knowledge).

## Decision

**Assembled, not searched.** The context for writing a scene is built deterministically from
the story: the scene (with its conditions and what it shows the reader), where it sits in the
plan, who and what is in it, what is true of them at the scene's time, what the viewpoint
character knows and gets wrong, events involving them, what the reader has already been shown,
the story rules, pictures linked to them, and passages of the author's writing that name them.
Search by meaning, when an embedding provider is configured, only raises a passage's score; it
never adds a fact.

**Explained.** Every item carries a plain-language reason, an authority (fact, belief, plan, rule,
entry, event, writing, picture, reader), a source record and location, a word cost and a score.

**Secrets apart.** A secret the viewpoint character does not know is placed in an author-only
section, left out unless the author includes it, and labelled as not known to the character
(ADR-005). The neighbours named for context are counted only from facts that are not hidden
from the viewpoint, so a secret cannot leak through who someone is said to be connected to.

**A budget, hard items first.** The scene, its conditions and reveals, its viewpoint and place are
always given; the author's pins are kept; everything else fills the budget by score, and each cut
is listed with its reason (excluded, no room, secret).

**The author's controls are part of the plan.** Pins, exclusions, the budget and whether to give
secrets are kept in the scene's details (`_context`) and changed through the reviewed plan path,
so they have history and can be undone.

**Rendering order.** Stable sections first (rules, cast, facts) for prompt-cache reuse, and the scene
last, because models use the start and end of a long context best (Liu et al., "Lost in the
Middle", TACL 2024). A checksum of the rendering identifies exactly what was given; a manifest
store keeps it for requests that use it.

**Derived memory of the author's writing.** Texts are cut into passages (`text_passages`): pointers
and the entries each passage names, never the words. The index follows every text change,
forgets a removed text, can be rebuilt (`python -m backend.devtools.reindex`, or "Read your writing
again"), and reports texts not yet indexed. When embeddings are available, passages are also
stored in the vector index for search by meaning and removed with the text.

**A voice sample.** The last passage of the most recent text is offered even when it names no one
in the scene, because practitioners report that re-feeding representative prose reduces voice
drift.

## Consequences

- Context needs no model and no graph; it is answered from PostgreSQL.
- Facts read the same in context as on screen (`backend/domain/canon/phrasing.py`, kept in step
  with the frontend by a test).
- Pictures are described by their existence and role; no image is processed or sent.

## Not decided here

Image, audio and video processing; captions; the EmbeddingGemma evaluation and any change of
vector dimension; per-request (rather than per-scene) controls; a model explaining sources in
conversation.

## Amendment (2026-10-17): comparing with a kept context (CTX-009)

A kept manifest now records each item's words and a 16-character SHA-256 fingerprint, so the
context now can be compared with the one a draft was written from (added, no longer given
and why, changed). Passages of the author's writing keep the fingerprint only: like the
passage index, a manifest never holds the author's words. Manifests kept before this compare
by length and cannot see a rewording of the same length. A kept context is read only for its
own scene, and only in the line of the story it was made in, or one a version of the scene's
prose there still points to (a what-if started after the draft was kept).


## Amendment (2026-10-18): listing policies for the other requests (CTX-002)

The God Hand, missing connections and reading a text listed the story bible up to fixed
counts (200 entries, 300 facts) in name order, so in a larger story the entries a request
named could be cut. Each now has a versioned listing policy (`backend/domain/bible_listing.py`):
`change-story/1`, `suggest-links/1`, `read-text/1`, each with a purpose, the sections it lists
and a word budget. What is listed is chosen without a model, in tiers: the entries the request
names (by name or alias, accents, case and punctuation ignored; by id; the entry the author is
looking at; the names found in the passage), then their neighbours (one fact, belief or event
away, or named in the same passages of the author's writing, from the passage index), then the
rest by the author's importance mark and how connected an entry is. A record takes the best
tier of the entries it touches; within a tier, one touching more named entries comes first
(as graph retrieval ranks edges between two seed entities first: KET-RAG, arXiv 2502.09304;
Microsoft GraphRAG local search). Named entries are always listed; the rest fill the budget.
For missing connections, the entries whose own details name another entry count as named.

What was left out is counted per section and said: to the model in the listing's last line,
to the author in a note and the response's `context`, and the policy, with the counts, is
recorded in the proposal's submit reason (its review history). Every request still checks
what the model returns against the whole story (up to 10,000 records of each kind), not only
what it was shown. Rendering keeps the old line shapes; the prompts were raised a version.
