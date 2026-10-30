# ADR-023: Written scenes are read back into the story as proposals; the plan card can come from the prose

- Status: Accepted (2026-10-17)
- Phase: 6 (`plan/phase-6-todo.md`)
- Builds on: ADR-014 (review), ADR-019 (prose is evidence), ADR-020 (what-ifs), ADR-022 (the scene
  studio and its jobs)

## Context

By Phase 5 an author can write and draft scenes, but what a scene establishes (a new place, who
was where, who believes what) stays in the prose until the author copies it into the story bible
by hand. Reading a pasted text into proposed changes already existed (Phase 2.7,
`IntakeWorkflow.read_passage`): it keeps only what it can quote, points every change at the exact
version and words, and sends the changes to review. Other tools show what goes wrong when this is
done carelessly: Sudowrite users report that reading a manuscript into its story bible stopped
after the first chapter, with no way to run it again; tools that re-read everything propose the
same things again.

Authors who write before they plan also need a plan that catches up with the writing.

## Decision

**"Update the story from this scene" is a job** (`scene_read`, `backend/services/scene_reading.py`)
in the queue of ADR-022, with its leases, cancellation, re-authorization, usage per call and
plain-words failures. It reads exactly the version that was current when the author asked, even if
they write on while it runs, using the same reader as any text. Its proposals go to the review
queue and change nothing until approved.

**It reads only what has not been read.** Each reading is recorded as it goes (`scene_readings`,
migration 0035): the version, the job, the spans read so far and the proposals that came of them,
saved after every passage inside the job's lease. What counts as read is every line of the version
the last complete reading read, plus every line a reading since then got through. A reading that
is stopped, fails or is interrupted is therefore resumed by the next one, never repeated, and
nothing it proposed is proposed again. The unit is a line, not a blank-line paragraph: scenes
typed with single line breaks would otherwise be read in full after any edit. Spacing inside a
line is not a change. With nothing changed, starting a
reading is refused before anything is spent. One reading runs at a time per scene; a reading that
was waiting while another finished reads nothing ("The scene was read into the story again while
this was waiting"), since it would read words the author may since have changed. The allowance is
checked before each passage with that passage's expected cost. The studio always says where things stand: never
read, read at version N, or changed since. Reading is always the author's explicit, repeatable
choice.

**Readings follow the line of the story.** A what-if starts with the main story's reading record
(the table is forked with the rest), so it does not re-propose what the main story already has;
its own readings and proposals stay in the what-if.

**From the review back to the words.** The passage behind a change now says which scene's text it
is (`scene_id`), so the review screen offers "See it in the scene", which opens the studio with
those words selected, found again even if the scene has changed since.

**A plan card from the prose** (`scene_plan`, `backend/services/scene_plan_card.py`). For a scene
with words but no summary, viewpoint or place, the assistant suggests them, and who takes part.
Each person or place must be an existing entry given by id *and* name (both must match; research on
checking model-chosen ids found that asking for both catches swapped ids), of the right kind
(a character for the viewpoint, a place for the place), with a quote the text contains; anything
else is dropped and counted. The author's plan wins: a summary is filled only when empty, a
viewpoint or place only when none is set, people are only ever added. The result is one plan change
in the review queue, with evidence pointing at the scene's words. It is marked as only filling in
(`only_fills`, a new operation field left out of the stored form when unset): if the author has
since written a summary, chosen a viewpoint or place, or removed someone, approving it is refused
rather than undoing their work.

## Consequences

- No model output reaches the story or the plan without the author's approval, and every accepted
  change from a scene points back at its words.
- Reading again after an edit is cheap and quiet; reading again with no edit is impossible.
- A stopped or failed reading leaves the scene "changed since" for the part it did not reach, and
  its proposals stay in the review queue; the next reading reads only the rest.
- Facts read from a scene do not take the scene's story time; the reader proposes timeless facts as
  it does for any text.

## Adversarial review (2026-10-17)

A read-only review found one high issue (a reading stopped partway re-proposed and re-billed its
first passages on the next run), four medium (single line breaks made a scene one paragraph;
readings could overlap or run out of order; usage limits were checked once against a low estimate;
approving a plan card could undo the author's later changes) and four low (plan card usage lost if
the lease was lost just after the call; a what-if inheriting "read" for proposals never kept; the
reader's fixed dashed markers; a long paragraph ending in spaces failing the job). All were fixed
with tests except the what-if inheritance, which is a known limit: a what-if started while the main
story's proposals from a scene are still waiting says the scene was read, and those proposals
belong to the main story.

## Not decided here

Facts that start at the scene's story time (some kinds of fact cannot take a time, and the reader is
shared with pasted texts); merging a moved paragraph rather than re-reading it; a plan derived for a
whole manuscript at once; telling the author when a suggestion from a scene is still waiting while
its studio is open (the studio shows the outcome of the last run only).

## Amendment (2026-10-18): events and thread steps (EXT-001)

Reading a text or a scene also suggests events: a short name, who takes part (known entries or
ones the same reading adds; places go with the event's places), and a story time only when the
passage states one (relative to a known event, at a point, or between two). An event a timed
scene shows happening there takes the scene's time; one remembered or reported does not. An event
already in the story bible is not suggested again. Reading a scene also suggests thread steps for
threads the plan already has: one change to the scene's plan that adds what it moves along
(`_advances`) or settles (`pays_off`), never a thread it opens. The change is marked
`only_fills`, now allowed to add those steps and nothing else, so approving it is refused if the
author has since changed the scene's plan. Both quote the passage, like facts, and wait for review
in the same set of suggested changes. Pasted text lists no threads and gets no steps.

## Sources

- Sudowrite feedback, story bible not updating after the first chapter:
  https://feedback.sudowrite.com/p/story-bible-and-editing-enhancements
- Novelcrafter Codex (detection links names to existing entries): https://www.novelcrafter.com/features/codex
- LinkML term validator on checking model-chosen identifiers:
  https://linkml.io/linkml-term-validator/anti-hallucination/

## Amendment (2026-10-21): changes of state and new threads (EXT-001)

Reading also suggests **changes of state** (`state_changes`): someone comes to hold or loses a
thing (possession), arrives at or leaves a place (location), becomes or stops being injured,
captive, dead and the like (status, with the word), or a relationship begins or ends. Each names
the subject, the kind of fact that records it (a listed predicate that fits the kind: an
object-pointing one for possession, a place-pointing one for location, a character- or
group-pointing one for relationships, a value for status), the other entry, whether it starts or
ends, a short name and the quote; anything else is set aside with a plain reason.

- A **beginning** is drafted like any other fact; in a timed scene it starts at the scene's time
  when its kind of fact can take a time (and a one-at-a-time value ends the old one then, as
  before). It carries "The passage shows this beginning." for the reviewer.
- An **ending** ends the fact the story records: at the scene's time (`amend_fact` with `ends`)
  when its kind can take a time and it began before the scene, or closed (`close_fact`, "The
  passage shows this coming to an end.") otherwise, as an untimed reading would. Nothing is
  suggested when the story records no such fact, it had already ended by then, or another
  suggestion here already ends it.
- In a **timed scene**, a change whose kind of fact cannot take a time (in the core schema
  everything but where someone is) is also suggested as an event at the scene's time, in the
  scene, naming who it touches: kind `possession`, `relationship` or `condition`, and a death as
  kind `death` with whoever died in the part `died`, which is how the continuity checks
  already recognise a death. A status whose values the author picks by hand (the core
  `HAS_STATUS` list) is suggested as the event only. Pasted text and untimed scenes get no such
  events: there is no time to place them at.

Reading a scene may also **open a thread** (`new_threads`, at most three): a question or promise
the plan's threads do not cover becomes a new thread (`create_plan_item`, its title, a one-line
summary, the quote) that the scene opens (`sets_up`), in the same one `only_fills` change to the
scene's plan as its thread steps, so approving it is still refused if the author has changed the
scene's plan since. A title the plan already has, or one not in the passage, is set aside;
pasted text opens none. `read-passage` is at version 3 and `read-scene` at version 4.

Checked with the scripted model and the browser stand-in only; how often a real model finds the
right changes is unmeasured.

## Amendment 2026-10-21 (batch 9 C): pasted text as a scene's prose, hand-picked values, carried steps

- **Pasted text and threads (EXT-001).** Reading a pasted text may name a scene of the plan
  (`scene_id` on `POST /assist/texts/{id}/read`; "Is this the writing of a scene in your plan?" on
  the text page). The plan's threads are then listed and the reading suggests their steps as one
  change to that scene's plan per passage, as a scene's reading does, only adding and refused at
  approval if the author changed the scene's plan since. The text keeps no lasting link to the
  scene; the author names it each time. The scene's story time is not used: the text is the
  author's, and whether it happens at the scene's moment is not said.
- **Values the author picks by hand (EXT-001).** A kind of fact whose values are a list
  (`enum`, such as `HAS_STATUS`) is suggested only from the author's own list (every value the
  story's facts of that kind allow, `author_choices`), in the author's spelling, and only when the
  passage itself uses that word; otherwise it is set aside saying why, and in a timed scene the
  event is still suggested. With no list yet, it is left to the author. The prompt lists the
  author's values; `read-passage` is at version 4 and `read-scene` at version 5.
- **Thread steps a stopped reading found (EXT-007).** Each passage's thread steps are recorded with
  the reading as it goes (`scene_readings.steps`). The next reading of the scene, or the same one
  resumed, suggests them together with its own in its one change at the end, and clears them, so a
  step is never suggested twice nor lost. The job's result says how many steps were suggested and
  how many came from an earlier reading.
