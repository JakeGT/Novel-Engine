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


## Amendment (2026-10-20): richer layers, a waiting list for the passage index, and ranking that explains itself (CTX-004, CTX-005, CTX-006, CTX-006A, CTX-007)

**The passage index goes through a waiting list (CTX-005; migration `0052_context_layers`).**
Saving a text no longer reads it into the index inline. It upserts one row per text into
`passage_index_waiting`; the projection worker's loop reads waiting texts in
(`PassageIndex.deliver_waiting`). This is the transactional-outbox shape, kept in its own
table because the index is keyed by text, not by journal revision, and a text changed five
times needs reading once: asking again for a waiting text raises its `generation` and keeps
`first_requested_at`, so lag is measured from the oldest change not yet read. A delivery
removes the row only if the generation it read is still the latest, so a change made while a
text is being read is never lost. Claims use `FOR UPDATE SKIP LOCKED` with a 120-second lease
(two servers never read one text; a server that dies lets go), a failure waits 5 s doubling up
to 10 minutes, and one story's failure never stops the others. A text found behind with
nothing waiting (a server stopped between saving and asking, or a text from before the list) is
asked for again when the index status is read. The scene's context and `GET /texts-index` report
how many texts wait, since when, and how many failed; the context dialog says it in words and
looks again every two seconds while texts wait. The hermetic test container sets `deliver_now`,
which reads through the same list at once; a command-line tool that saves texts (the seed) leaves
them waiting for the server's worker, and `python -m backend.devtools.reindex` rebuilds directly.

**Metadata filters on search by meaning (CTX-004).** A document search takes `where` with keys
from a fixed list (`source_id`, `source_kind`; anything else is refused) applied in the same
scan as the ranking, as parameters, never interpolated. The scene's context searches only the
texts its own line of the story may use, so another line's prose never takes a place; a point
of view is filtered through the ids of the texts of scenes seen through it, worked out at query
time from the plan, rather than written onto the rows (a scene's viewpoint can change without
its prose being re-read). Record kind is the `kind` filter batch 6 already had.

**Earlier scenes as layers (CTX-006, CTX-006A).** Policy `scene-draft/2` adds two sections:
"What happened in earlier scenes" (up to five scenes back, nearest first) and "How the scene
before ends" (the last 250 words of its prose as written, placed after the stable material and
just before the plan and the scene, because models use the start and end of a long context best:
Liu et al., *Lost in the Middle*, TACL 2024). Each earlier scene gives an event block (the events
the author placed in it, at most eight) and a summary: the one kept for its prose, or, two or
more scenes back, what its plan card says (summary and the viewpoint's intent; the scene just
before already gives its plan). A scene's own events are given with the scene. A scene seen
through other eyes says the viewpoint character may not know all of it. A passage inside the
scene-before's tail is not given a second time.

A summary of prose (`scene_summaries`) belongs to one exact version of one text, with origin
(author / suggested by the assistant / suggested and corrected), model, prompt version
(`scene-summary/1`, fenced, structured `SceneSummaryDraft`) and job. The assistant only
proposes: it is used once the author keeps it, as it is or corrected, and the author can turn it
down; asking costs tokens and goes through the same estimate, confirmation, budget and usage
recording as every model call. When the prose changes the kept summary is still given, marked
out of date and ranked lower, until the author summarises again. Summaries are keyed by text,
so a what-if shares them until it first rewrites a scene, and then its copy has none of its own;
kept summaries travel in the project archive, and the purge list covers both tables. The waiting
list is not author data and is not archived.

**Ranking that says why (CTX-007).** Each candidate keeps its fixed base score, and every signal
that moves it is recorded on the item (`signals`: name, effect, a plain note) and appended to its
reason, so an item's signals add up to its score and the dialog shows "Raised: important to you",
"Lowered: like another passage" and so on. Weights are metadata only; nothing is written to the
index or the story. The signals:

- *Author importance*: the entry's importance mark (×1.5 main, ×1.2 supporting, ×0.85 minor,
  ×0.7 background), on items about an entry and passages naming one; never on secrets. The mark is
  set on the entry page ("How much it matters"), saved as an ordinary reviewed change.
- *Continuity impact*: +2 for records named by an open continuity warning within two scenes of
  this one (reading order), +1.5 for a story rule that does not hold for someone in the scene.
- *Narrative distance*: prose from a scene loses half its weight every four scenes back, never
  below 0.3; prose from a later scene is weighed 0.5 (it may tell what has not happened yet).
- *Thread*: +2 for up to two passages from each other scene that opens, moves or resolves one of
  this scene's active threads, with the thread named in the reason.
- *Diversity*: greedy maximal marginal relevance over the author's prose (Carbonell and Goldstein,
  SIGIR 1998): a passage sharing at least a quarter of its three-word runs with one ranked above
  it is lowered by 0.6 × that share, naming which.

The voice sample now prefers prose from a scene seen through the same eyes (by meaning, when
search is configured; else the most recent such scene).

**Measured** (stand-ins only, `tests/eval/retrieval`, search by meaning off): Recall@10 0.971 → 1.0
(two scenes 0.857 → 1.0), Recall@5 0.793, recall 1.0, MRR 1.0, precision 1.0, no leaks, no silent
omissions, what-if correct — unchanged otherwise. Not measured: whether drafts improve, the weights
on a large real story, or diversity with a real embedder; the weights are starting values to tune
against the owner's model.

Sources: Liu et al., "Lost in the Middle: How Language Models Use Long Contexts"
(https://arxiv.org/abs/2307.03172); Carbonell and Goldstein, "The Use of MMR, Diversity-Based
Reranking for Reordering Documents and Producing Summaries", SIGIR 1998
(https://people.eng.unimelb.edu.au/ammoffat/sigir98/abstracts/carbonell.html); PostgreSQL
`FOR UPDATE SKIP LOCKED` (https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE)
and outbox relays with leases and at-least-once delivery
(https://milanjovanovic.tech/blog/pessimistic-locking-ef-core-postgresql,
https://pkg.go.dev/github.com/gopherex/pg-outbox).

## Amendment (2026-10-21): story time, whole parts and nearby scenes (CTX-004, CTX-005, CTX-006, CTX-006A, CTX-007, CTX-012, OPS-007)

**Story time as a filter (CTX-004).** Policy `scene-draft/3` gains `later_in_story_time`:
`leave_out` (the default) or `lower`. Material drawn from another scene that is certainly later
in the story's time than the scene being written (its interval starts after this scene's ends)
is marked `later`. Under `leave_out`, `assemble` lists it as left out with the reason `later`
("From later in the story's time than this scene") unless the author pins it, so a jump forward
the reader has already met never feeds knowledge of the future into a draft silently, and the
author can still choose it. Search by meaning never scores those texts at all: the filter is a
set of text ids worked out from the plan at query time and passed as the existing `source_id`
filter in the same scan as the ranking, for the same reason as the point-of-view filter (a
scene's time can change without its prose being read again, so it is not written onto the
rows). At most five such passages are listed, so they never take a place from what may be given.
Material that only *may* be later (its time overlaps this one's, or is open-ended) is given,
lowered ×0.75 with a note. The scene just before keeps its "how it ends" item whatever its time:
it is what the reader has just read. Under `lower`, certainly later material is weighed ×0.5.

**Two clocks (CTX-007).** Reading order already decays prose (half every four scenes back). The
second clock is the story's time, after Genette's distinction between the order of the story and
the order of its telling (*Narrative Discourse*, 1980; a flashback or a jump forward is where they
part). Prose, summaries and event blocks from a scene placed before this one lose half their
weight every quarter of the time the line's placed scenes span (so the decay fits a story of days
or of centuries without a unit-specific constant), never below 0.5: a deliberate jump back is
still worth something. The two weights multiply, like two decay functions in a search engine's
scoring, each halving at its own scale (Elasticsearch `function_score` decay functions with
`decay: 0.5` at `scale`); recency decay over time for memory retrieval is also how Park et al.'s
generative agents rank memories (exponential, per game hour). Each is its own signal with its
own sentence ("It is four scenes back." / "In the story's time it happens 4 years before this
scene."). With fewer than two placed scenes, or no time on this one, the second clock is silent.

**Summaries of whole arcs and beats (CTX-006; migration `0056_context_remainders`).** A part
(story, arc or beat) is summarised from the kept summaries of its scenes, never its prose, the
bottom-up shape of recursive summarisation (Wu et al., "Recursively Summarizing Books with Human
Feedback", 2021; RAPTOR, Sarthi et al., 2024). Without a model the summary is those scene
summaries in reading order, cut at 150 words, always current. The author can keep their own, or
ask the assistant (`part-summary/1`, the scene summaries fenced as material, cost first and every
spending control) and keep, correct or turn down the suggestion. `part_summaries` records which
scene summaries a kept one stands on (`basis`, and its fingerprint); when any of them is kept anew,
replaced or dropped the part summary is out of date: still given, lowered by 1, and said so. A
scene's context gives up to three earlier parts that do not hold the scene, whose scenes all come
before it, and at least one of them further back than the five scenes given one by one; a part
overlapping one already chosen is left to it (nearest and smallest first). Lines of the story and
the archive behave as for scene summaries; kept ones travel in the same archive list, marked
`"part": true`, and their fingerprint is worked out again on restore.

**The ends of other recent scenes (CTX-006A).** Besides the scene just before, the last 120 words
of up to two scenes from two to eight scenes back are given ("How other recent scenes end"), when
they are seen through the same eyes or on one of this scene's threads; one through other eyes
carries the warning in its words. They are ordinary items under the budget, decayed by both
clocks and subject to the story-time policy; passages inside them are not given twice.

**Operations (CTX-005, CTX-012, OPS-007).** `/health/ready` gains an optional `passage_index`
part (texts waiting, the oldest wait, how many failed; degraded after 15 minutes; counts only,
never which story), and the `search_index` part reports `caption_lag` and `picture_lag` beside
`lag`. `SEARCH_INDEX_CATCH_UP_MINUTES` (0, off, by default) runs `make reindex-refresh`'s work in
the background with `JobRunner.every`: only into the index in use when it is the configured
model's, looking at up to 50 stories a round (taking turns) and bringing up to three of them up to
date.

**Measured.** The retrieval evaluation's numbers did not move (its scenes have story times but no
scene prose, and no parts): recall 1.0, Recall@5 0.793, Recall@10 1.0, MRR 1.0, precision 1.0, no
leaks, no silent omissions. The weights are starting values on the stand-ins only.

Sources: Genette, *Narrative Discourse: An Essay in Method* (Cornell, 1980), summarised at
https://literariness.org/2016/12/03/gerard-genette-and-structural-narratology/; Elasticsearch decay
functions (https://www.elastic.co/guide/en/elasticsearch/guide/current/decay-functions.html); Park
et al., "Generative Agents: Interactive Simulacra of Human Behavior" (https://arxiv.org/abs/2304.03442);
Wu et al., "Recursively Summarizing Books with Human Feedback" (https://arxiv.org/abs/2109.10862);
Sarthi et al., "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval"
(https://arxiv.org/abs/2401.18059).
