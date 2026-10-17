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

## Consequences

- Every story read and every repository double filters by the working branch.
- The graph, the equivalence check and archives cover the main story only. An archive is
  always the main story, whatever what-if the request came from.
- Name answers, story rules, texts, media files and the unit of time are shared by every line.
- A what-if costs a copy of the story's rows. Set-aside what-ifs keep their rows.

## Not decided here

Merging a what-if into another what-if, field-level merges, alias memory scoped by time or
viewpoint, media applicability by time, and trust between characters.
