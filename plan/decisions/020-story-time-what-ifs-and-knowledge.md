# ADR-020: Story time on the author's own number line, what-ifs as copied branches, deny-by-default knowledge

- Status: Accepted in effect (2026-10-17); built under the owner's standing direction to
  continue the plan. Open to amendment like any ADR.
- Context: Phase 3; ADR-004 (branches), ADR-005 (POV and secrets), ADR-012 (query store gate),
  ADR-014 (review), `03-domain-and-temporal-model.md`, `18-author-rules-canon-queries-and-derived-views.md`,
  `19-assistant-first-experience.md` (Level 5).

## Decision

**Query store.** Branch, time and point-of-view questions are answered from PostgreSQL. Neo4j
projects the main story only. The outbox consumer acknowledges what-if commits without
projecting them, because a what-if shares record ids with the story it came from and would
otherwise write over the main story's nodes. This settles the ADR-012 gate for these queries.

**A what-if copies the story when it starts.** Starting a what-if inserts a branch row and copies
every story table (entries, facts, beliefs, events, plan items, picture links, external ids,
provenance, evidence, record versions, named versions) into it in one transaction, keeping ids,
while the main story's head is locked and no commit is reserved. The plan said a branch "does not
copy the entire graph". A story is thousands of rows; copying makes isolation structural (every
read filters by one branch id) instead of an overlay every query must remember, and leaves the
commit path untouched. Picture links were keyed without their branch and are re-keyed (migration
0032).

**The working what-if travels with each request** in an `X-Story-Branch` header, checked to
belong to the project before anything is read; a branch of another project is "not found",
never a fall back to the main story. With no header everything is the main story, so nothing
before Phase 3 changes. The frontend keeps the choice per project in the browser and makes it
part of every cache key.

**Comparing and bringing back.** A comparison lists records changed in each line since the fork,
from record versions. Records changed in both are conflicts; the author chooses, per record,
whose version to keep. Bringing a what-if back replays its committed operations onto the main
story as one proposal, minus those on records where the main story's version was kept, each
carrying the what-if as evidence. It is reviewed and approved like any change; nothing is
merged automatically. A what-if is always started from the main story.

**Story time is the author's own number line.** A story names one unit ("year" by default). A
time is unknown, at a point, between two points (either end may be open), or so long before or
after an event, optionally "about"; the author's own words are kept beside it. It is never
converted to a calendar date. Resolving gives the interval a time is certainly inside; chains of
relative times are followed, and a chain that loops or loses its anchor is refused at commit.
Facts gain `starts`/`ends` (half-open), events gain a time, scenes may say when they happen.
These are new optional operation fields (and one new operation, `amend_fact`), omitted from the
canonical form while unset, so every earlier envelope keeps its checksum (ADR-015).

**One-at-a-time facts across time.** Two values of a one-at-a-time kind may stand together when
their spans certainly do not overlap. Unstated spans cover the whole story, so the old rule is
unchanged for anyone who never states a time.

**Knowledge is deny-by-default.** A fact is ordinary, known to everyone, or secret. A character
knows a fact when they hold an agreeing belief or everyone knows it; nothing else is assumed,
and taking part in an event does not count as perceiving it. Whether a belief is right, and
whether something one character told another was a lie, are derived from the facts each time,
never stored. Telling is an event with speaker, listeners and overhearers, plus a belief for
each hearer (overhearers less firmly).

**The reader is a separate ledger.** A scene lists the facts it shows the reader, plainly or as
a hint; scenes are read in outline order. Dramatic irony, knowledge withheld from the reader,
repeated reveals and flashbacks are derived from it. A reader's guide up to a scene leaves out
unshown secrets, facts that start after the scene's time, and entries not yet met.

**Record-level rebasing (amends ADR-014).** ADR-014 made every retarget perish the approval. A
stale proposal now keeps its approval when none of the records it touches or refers to changed
between the version it was made on and the new one; otherwise it returns to review and says
which records changed. The commit still revalidates in its own transaction.

**What is said, and how people take it (amends this ADR; KNW-005, KNW-006, 2026-10-18).** A
*claim* is an event of kind `claim` whose `claim` holds a proposition, a modality (states,
suggests, wonders, denies) and optionally the scene it is said in; participants say who says it
(`speaker`, or `narrator` for a character narrating, perhaps unreliably; none for the narration
itself) and who hears it (`perceiver`). A claim is never a fact and never counts as knowledge;
whether the facts bear it out (`borne_out`, `contradicted`, `unconfirmed`) is derived each time,
like whether a belief is right. Recording one is an ordinary author change. It becomes a fact only
through a reviewed proposal, which also ends the facts it contradicts when the kind of fact can
hold only one value at a time; a denial is never made a fact.

Telling and seeing propose beliefs; they never write them. Trust is kept on the listener under the
reserved `_trust` key of a character's details: a stance for anyone (believes by default, doubts,
disbelieves) and stances for particular speakers. A telling, something seen, or a claim whose
author asks for it becomes one proposal of origin `derived` (always reviewed, never kept for the
author by a delegation rule): the event, and a belief for each hearer the policies allow, each
with its reason in words. Believes gives confidence 1, doubts 0.5; overhearing multiplies by 0.6,
a suggestion by 0.5. Disbelief gives *no* belief, not a belief in the opposite: a proposition is
one value, its opposite is rarely one value ("not in Saltmere" names no place), and trust
discounting in subjective logic likewise moves distrusted testimony into uncertainty rather than
into disbelief. Perception: everyone present sees what happens unless the author says they did not
notice, and what one sees is believed fully whatever one's trust; being in a scene is not taken to
mean being present, the author confirms who was there (the screen fills it from the scene's people).
Reading a scene does not propose claims: it shares the text reader with every text, and teaching it
to tell speech from narration is left for later.

**How and when something was learned (amends this ADR; KNW-002, CON-006, 2026-10-18).** A
belief's `source_ref` names the event it was acquired through, and the event says how: a telling
(told or overheard), something seen, a claim heard, or a new kind, `inferred`, whose `premises`
name the facts and the holder's own beliefs it was worked out from. Recording an inference is the
author's own change, saved at once like recording a claim; telling and seeing still only propose.
An event may name the scene it happens in (`scene_id`, a new optional field omitted from the
canonical form while unset, so earlier checksums stand). When it was learned is the event's story
time, else its scene's time, else its scene's place in the reading order. Someone learns a thing
*after* a scene only when that is certain: by story time when both have one and they do not
overlap, otherwise by reading order. Anything unplaced counts as known all along, so nothing warns
on a guess. Knowledge read for a scene (its context and its checks) leaves out what the viewpoint
character only learns later; a secret learned later is the author's alone in that scene. A scene
that uses such knowledge first (its words name everyone in it, the plan shows it through them, a
claim has them say it, or drafting would give it as true) is a warning in the studio and the plan,
and never blocks (plan/13).

**How long things take (amends this ADR; CON-003, 2026-10-18).** Beside the unit, a story keeps
the author's own table of travel times between places (either way; 0 means one place is inside the
other) and lengths of events and facts (migration `0045_how_long`, on `story_clocks`, versioned so
two saves at once cannot overwrite each other). Like the unit it is shared by every line of the
story; rows naming a place, event or fact a line does not have are ignored there. It travels in
the archive with the unit. Nothing ever fills it in by guessing: tools that compute ages from
birth dates and keep characters to one place at a time (Aeon Timeline, for one) leave journey
times to the author too. The continuity checks that read it (`backend/domain/continuity.py`) are
worked out on read, never stored, and never block.

## Consequences

- Every story read and every repository double filters by the working branch.
- The graph, the equivalence check and archives cover the main story only. An archive is
  always the main story, whatever what-if the request came from.
- Name answers, story rules, texts, media files and the unit of time are shared by every line.
- A what-if costs a copy of the story's rows. Set-aside what-ifs keep their rows.

## Not decided here

Merging a what-if into another what-if, field-level merges, alias memory scoped by time or
viewpoint, and media applicability by time.

## Amendment 2026-10-20: ready views of the story, and the guide in parts (QRY-001, QRY-002)

**Ready views.** Six views are kept per line of the story in `read_models` (migration
`0050_read_models`): location, possession, relationship, timeline, reader disclosure and
dependency (`backend/domain/read_views.py`). Rows hold record ids, predicates and story times
only, never names or the author's words, sorted deterministically and checksummed. They are
built by `ReadModelConsumer` (`backend/services/read_models.py`), which the projection worker's
loop runs: it finds lines whose latest `canon.commit` outbox message is past their watermark,
reads the line's current records between two reads of its head (a commit landing in between
means the build is dropped and redone next round), and stores every view at that head. Its
progress is its own watermark per line, never the outbox's `published_at`, which belongs to the
graph's delivery: each consumer of the outbox keeps its own checkpoint, as event-sourced
projections do, so one never hides messages from the other. A save never moves a watermark back.
A line builds from scratch each time (bounded like every on-read computation, 10,000 records):
incremental maintenance was not worth its risk at novel sizes; the gain is that the work is done
once per commit instead of once per read.

**Trust by replay.** `ReadModelService.verify` (`make read-models-verify`) rebuilds each stored
view from the record history (`story_at`, the journal's replay) at the stored watermark and
compares checksums, counting differing rows. Two independent paths (today's tables and the
history) must agree. The on-read computations remain the source of truth: a view answers reads
only when its builder has been verified for that line (`verify_outcome = matched` with the same
`BUILDER_VERSION`) and its watermark is the line's head; otherwise reads are worked out as
before. A rebuilt view stays trusted while the builder version is unchanged; raising the version
or a differing replay withdraws trust (`--repair` forgets the line). The timeline
(`StoryTimeService.timeline`) and the reader's journey (`KnowledgeService.journey`) answer from
their views when they can, and also fall back if the records read differ in ids from the view's.
New questions answer from the views with the same fallback, each saying which it used and at
which version: `GET /story-views/where`, `/holders`, `/relationships`, `/dependents`,
`/disclosures`, and `GET /story-views` for each view's state.

Times in the views are those the timeline uses, exact as stated; the continuity checks' reading
of a whole unit of a coarse clock stays theirs. Views are derived: purged with the story
(`canon_tables.py`), never archived (the archive's `derived.story_views` says so and how they are
rebuilt), never copied into a what-if (each line builds its own on its first commit; until then
reads are worked out), and rebuilt after a restore from the replayed commits' outbox messages.

**The guide in parts.** `/reader/guide?part=` makes characters, places, a timeline or a recap on
their own (`backend/services/reader_exports.py`), each under the guide's rules, with `part:` in
the Markdown header; the page has "What to include". The timeline holds only scenes read so far
and the events tied to them, in story-time order: an event dated earlier but not told in any
scene read so far stays out, since the story may not have told the reader of it. The recap gives
whose eyes, where, who is there and what each scene shows plainly, and never the plan's summary
(the author's note may name what the reader has not been shown) or hints.

**Sharing by link stays out.** A link anyone holding it can open is a capability: URLs leak
through history, referrers, logs and forwarding, so such a link needs its own expiry, revocation,
audit and a narrower read than a member's, and a leaked link to a reader's guide is a leaked
story. That design is not done; print and download share the guide instead.

Ran against the in-memory doubles and a disposable PostgreSQL only; no measurement of read speed
on a large story was made.

Sources: [W3C TAG, Good Practices for Capability URLs](https://www.w3.org/2001/tag/doc/capability-urls/)
(capability URLs cannot generally be kept secret; leaks of shared-document links);
[evoq projections](https://evoq.hexdocs.pm/evoq_projection.html) and
[Cratis Chronicle, rebuilding a read model](https://www.cratis.io/chronicle/scenarios/rebuild-a-read-model/)
(idempotent projections with their own checkpoints; rebuild by clearing and replaying, then
checking the result; rebuild time grows with history).

## Amendment 2026-10-21 (batch 9 C): a screen for the ready views, and why they stay rebuilt whole

`/projects/:id/story-views` ("Ready views of the story", linked from the Timeline) shows each view
of the line being worked in: whether it is built, at which version and how many changes behind, when
it was last built and (only while behind) how long it has been waiting, how many rows, the last
replay check and its outcome, and whether reads use it now. `GET /story-views` gained `built_at`,
`lag_seconds`, a plain `state`, the line's name and `how_built`.

Incremental updates were not built. Each view is a function of the whole line: the timeline orders
every event by resolving relative times through each other, the dependency view is a transitive
closure, and the disclosure view depends on the reading order of every scene. One commit (a revert,
a merge that expands into many changes, a time moved that others are relative to) can change rows
far from the records it names, so a delta builder would need per-view invalidation rules, each a new
way for a view to drift from the records. Practitioners report the same for recursive views:
insertions are cheap to apply, but deletions and large cascades can cost more than a refresh, so
hybrid systems fall back to a full rebuild. Replay verification compares whole checksums either way.
A whole rebuild per commit is kept until a measurement on a large story shows it is too slow; the
screen's "waiting" time is the first place that would show it.

Sources consulted: [PostgreSQL hackers, materialized view incremental
maintenance](https://www.postgresql.org/message-id/20161128231816.GA24797%40localhost) (mass deletion
in a transitive closure: refreshing is faster than applying the delta);
[pg_trickle, recursive CTEs that update themselves](https://pgxn.org/dist/pg_trickle/0.37.0/blog/recursive-ctes-that-update-themselves.html)
(semi-naive for inserts, delete-and-rederive otherwise, and an automatic fall back to a full refresh).

## Amendment 2026-10-21: what-ifs stay "set aside only" (owner decision)

Batch 8 left one question open: a what-if cannot be discarded, so what is kept for it lives on.
The owner decided on 2026-10-21 to keep it that way. A what-if is set aside and taken out again
(`WhatIfService.set_aside`, which only changes the line's status); there is no discard action.
A set-aside what-if keeps its rows, as before, and also its kept assistant conversations ("Change
anything" keeps one per author and line) and its scheduled audits. A scheduled audit of a
set-aside what-if does not run: each time it falls due it is recorded as gone and spends nothing,
and it runs again if the what-if is taken out. All of it is removed only when the story itself is
deleted. Recorded in `11-decisions-and-questions.md` and `HANDOFF.md`.
