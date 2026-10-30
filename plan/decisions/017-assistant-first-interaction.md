# ADR-017: Assistant-first interaction and progressive disclosure

- Status: Accepted in effect. The principles are the owner's direction of 2026-10-10, and on the
  same day the owner directed work to "continue with the implementation plan" as revised. No
  explicit sign-off of this record's wording has been given; amend it if any mechanism below is
  not what was meant.
- Date: 2026-10-10
- Owners: project owner, implementer
- Related: ADR-012, ADR-013, ADR-014; `19-assistant-first-experience.md`; `audit-2026-10-10.md`;
  backlog `AST-EXP-*`

## Context

Through Phase 2 the product exposed canon in the shape it is stored, and each planned phase added
expert concepts with a manual editor. AI was specified as a prose drafter, an extractor, and a
critic. The owner found the result laborious and the learning curve steep, and directed that the
system be easy to start, that everything be automatable through conversation or human-in-the-loop
iteration, and that advanced features be kept and learned over time rather than removed.

## Decision

1. **One loop for every capability:** Describe → Interview (optional) → Draft → Review → Canon,
   with unfinished work surfaced as to-dos that reopen the loop.
2. **Two paths, always:** every capability has an assistant path and a by-hand path. The
   assistant path is the default entrance; the by-hand path is one action away and works with no
   AI provider.
3. **The assistant never writes canon.** It produces proposals through the existing review
   workflow (ADR-014). This ADR does not relax any gate.
4. **Five levels of disclosure** (Start, Shape, Plan, Write, Control) decide what is shown and
   offered by default. No feature is gated; every feature is reachable by search and by asking.
5. **Defaults make advanced concepts optional:** one main branch, unspecified world time, the
   author's own point of view, warning-only story rules, and the starter types. No Level 1–4 task
   requires choosing among them.
6. **Plain language is part of the contract.** Author-facing names are defined once
   (`frontend/src/lib/words.ts`); internal terms appear only under technical details.
7. **A definition of done** (document 19) applies to every feature from Phase 2.5 onward.
8. **Story planning is created by conversation**, and plan records are canon-governed intent,
   distinct from fact.

## Alternatives considered

### Keep manual-first and add an assistant later

Preserves the roadmap. Rejected: it keeps the steep first hour, and retrofitting an assistant onto
screens designed for manual entry produces the bolt-on the owner is objecting to.

### A chat-only product

Simplest to explain. Rejected: a story bible has to be browsed, compared, and corrected; chat is a
poor surface for review at scale, and it removes the by-hand path that makes the product usable
without a provider.

### Hide advanced features behind a mode switch

A "simple/advanced" toggle is easy to build. Rejected: it makes the advanced product a cliff
rather than a slope, and authors would have to know they need it before seeing why.

### Let the assistant write low-risk canon directly from the start

Fewer clicks. Rejected as the default: trust has to be earned, and silent writes are how a story
bible becomes untrustworthy. Delegation stays an option the author turns on later.

## Consequences

### Positive

- A new author can produce reviewed canon from a paragraph, without learning the data model.
- One interaction to learn; later features reuse it.
- Advanced features gain a reason to appear instead of a menu to be found in.

### Negative

- Every feature costs more: two paths, a level, a trigger, and tests of each.
- First impressions now depend on model quality and on the author having a provider key.
- More interface text to keep honest and consistent.

### Follow-up work

- Roadmap phases 2.5–2.7 and the `AST-EXP-*` backlog items.
- A labelled evaluation set for drafted entries, links, and plans.
- The owner decision on how a new author gets a working assistant (audit, decision 1).

## Validation

The measures in document 19: time to first accepted entry, premise-to-linked-entries, first plan,
the plain-language test over rendered screens, both paths tested for every capability, and the
kept-rate of drafted changes. Revisit if authors routinely abandon interviews, discard most of
what the assistant drafts, or stay at Level 1–2 on long projects because later levels never
become visible at the right moment.

## Amendment 2026-10-21: merging, kept conversations, premise rounds, and measuring suggestions (batch 8 B)

Accepted for implementation by the agent working AST-EXP-005, -007, -013 and -017; to be
reviewed by the owner. Everything below has run against the scripted model, the local stand-in
model and the stand-in embedder only.

### 1. A merge is its own reviewed operation, worked out when it is kept

`merge_entities` (`backend/domain/operations.py`) names the entry that goes and the one that
stays. The author reviews it as one change, with a preview in sentences of everything it would
move or end (`POST /merges/preview`, the review card, the merge dialog on the entry page). When
it is kept, `backend/domain/merge.py` expands it, inside the commit and again for the envelope
outside it from the same locked version, into ordinary changes: the kept entry's names and
details, every current fact and belief naming the merged entry ended and stated again about the
kept one (with its story time, secrecy and confidence; duplicates, self-references and values a
one-at-a-time kind already holds are ended instead), events, plan items and picture links
re-pointed, and the merged entry retired with `merged_into`. Changes made earlier in the same set
are taken as they will be; a change in the same set that names the merged entry is refused.

Why expand at commit rather than when suggested: a fact added after the suggestion still moves;
and every reader of the change log (history, undo, what-ifs and bringing them back, exports and
restore, the graph projection, ready views) keeps reading only operations it already
understands. The envelope holds the expanded operations; the journal keeps the merge the author
reviewed. Two operations were added for this: `restore_entity` (undoing a merge brings the entry
back; ordinary archiving still is not undone automatically) and `participants`/`location_ids`
on `amend_event`. Envelope stability (ADR-015): `merged_into` joins the omit-when-unset list;
the two event fields are omitted only on `amend_event` and only when `None`, because
`create_event` has always recorded an empty `participants`. Undo is one step: the history
revert restores the entry first, then states its facts and beliefs again (with their times and
secrecy) and puts names, details, events and plan references back.

Research: Wikidata merges leave a redirect but undoing one means restoring the item and
reassigning every link by hand, and bot rewrites of statements pointing at the merged item are a
separate process ([T237262](https://phabricator.wikimedia.org/T237262),
[T237899](https://phabricator.wikimedia.org/T237899),
[T40664](https://phabricator.wikimedia.org/T40664)). Here the moves are part of the same reviewed
commit and undone by the same revert, so no reference is left pointing at a retired entry and
none has to be put back by hand.

### 2. Change anything keeps one conversation per author, per story line

`assistant_threads` and `assistant_turns` (migration `0054_conversations`) keep the author's
instructions and the assistant's restatements, questions and the suggested changes they led to.
The last eight turns (at most 6000 characters; the newest turn is always there, cut down to that
bound when it is longer on its own) are put back into the prompt inside markers (`fence()`), with
a rule that only the new instruction is acted on. A thread keeps its newest 200 turns, except
that a premise thread never lets go of the author's turns that tell the story (each round is read
from them). "Clear and start again" erases the thread and the words its conversations kept (an
unfinished one is erased; one behind suggested changes stays as evidence with its words blanked);
suggested and saved changes stay. Threads are private to their author, kept apart per what-if,
purged with the story and erased when their author is removed from the workspace. The project
archive carries only the exporting author's own threads, and restoring one gives every thread in
it to the author restoring it (who an archive names is not trusted: its checksum is not a
signature), so an archive can never write into someone else's thread. Each instruction is
still its own conversation for spending (AST-EXP-011); a premise thread counts as one
conversation for its budget (`assistant_threads.tokens_used`).

When the story is larger than its listing and a model for search is configured, entries the
instruction is about by meaning are listed first (`backend/services/meaning.py`; embedded on the
server from names and details only, cached in memory by content; no provider spend). Change
anything can now also change, add and remove plan items, kept apart from facts.

### 3. Starting from a premise is rounds of questions and suggested entries

`POST /assist/premise` (`premise-round` prompt): the author's story and answers go inside
markers; the assistant returns at most five open questions (about the story bible, never plot
the author has not chosen) and the entries, facts and beliefs the words state, as one reviewed
proposal per round. Entries suggested in an earlier round that still wait for review are never
suggested again. Each round with suggestions keeps an assist session as its evidence, begun
before the round so every suggestion cites it by its own id. Suggestions from a premise are never
kept by a keep-for-me rule (`via=premise`): the start of the story is always reviewed.

### 4. Suggestions are measured by what authors do with them, and by whether they quote

`suggestion_runs` records each run with counts only. Link suggestions record how many were
suggested, whether candidate pairs came from search by meaning or the listing, and, once the
proposal is saved or turned down, how many were kept (precision). What a keep-for-me rule kept
is counted apart ("kept for you", `details.kept_for_you`) and gives no precision: the author
decided nothing. Plan suggestions carry a `quote` per item; a deterministic check counts an item
as grounded only when the quote is found, as written (case, spacing and curly quotes aside, at
least 12 characters), in the author's own material: what they said and answered, the plan's
cards and the entries' names and notes, never the listing the app wrote for the assistant;
otherwise it is the assistant's own (invented). Both are shown per run, for the line of the
story the author works in.

Research: kept-rate is a noisy proxy for quality and should not become an optimisation target
([Mozannar et al., "When to Show a Suggestion?"](https://arxiv.org/html/2306.04930v3)); it is
recorded and shown here, never used to change what is suggested. Exact-match quote checks catch
fabricated quotes cheaply but say nothing about whether the claim follows; entailment or a judge
is needed for that ([survey, arXiv 2508.15396](https://arxiv.org/pdf/2508.15396);
[ACL 2024 long.806](https://aclanthology.org/2024.acl-long.806)). Groundedness here therefore
means "quotes the author's material", not "is right".

### Limits

- Claims (what a character said) that name a merged entry keep their words and are listed as
  still naming it.
- Search by meaning for entries embeds at most 1500 entries per request and compares at most 400
  pairwise for link candidates; vectors are not stored. Without a model for search, the listing
  alone is used and the result says so.
- Precision counts the changes kept in a saved proposal; an author who keeps a suggestion and
  then edits it counts as kept.

## Amendment 2026-10-21 (batch 9 C): changing types and kinds, and claims in a merge

- **Change anything can change an entry's type and a fact's kind (AST-EXP-017).** `type_changes`
  (an entry recorded as the wrong type) becomes an `update_entity` with the new `type`, placed after
  the change ends what that type cannot have (each such fact and belief is ended and the author is
  told how many), so undoing it in one step gives the type back before those are stated again.
  `kind_changes` (a fact recorded as the wrong kind) ends the fact and states it again as the right
  kind with the same entries, value, story time and secrecy, saying why; a kind that records
  something else, or does not fit the entries, is set aside in plain words. Both wait for review.
  Adding or changing the types and kinds themselves stays in setup. Prompt `change-by-instruction/4`.
- **Claims follow a merge.** What a character said about the merged entry (an event of kind
  `claim`) is rewritten to name the kept entry (`amend_event` with `claim`), and put back when the
  merge is undone; one that would name the kept entry twice keeps its words and is listed as still
  naming the merged entry. Only the entry a rewritten claim now names is checked (it must be in the
  story, and fit unless it is of the same type as the one it replaces), so a claim about something
  archived or given a new type since never blocks a merge.
- **Never cut part way (review, batch 9).** A new type with everything it ends, and a fact ended
  and stated again as another kind, are kept whole or set aside whole with a plain reason when the
  proposal has more changes than one review holds (the largest group cut is set aside first), and
  whenever changes are cut the author is told how many. A type the setup describes keeps only its
  own details: the others go with the old type, and the note names them; by hand, a new type that
  still carries them is refused in plain words. A new type is checked against every current fact
  and belief up to 100,000 of each; a larger story is refused rather than checked in part.
- **Plan records follow a kind change, and what undo restores (review, batch 9).** The fact stated
  again gets its own id in the proposal, and scenes that reveal the old fact reveal the new one in
  the same change. Arc stages tied to it are named in a note but not moved: undoing a change states
  an ended fact again as a new record, so an undone stage would point at the ended record and the
  undo itself would be refused. Undoing a kind change therefore brings the old kind back as a new
  record, and the scenes that reveal it point at the old, ended record again; the note says so.
