# Phase 6 implementation todo: from written scenes back into the story

- Status: Built (2026-10-17). Reading and the plan card have run against a scripted model and the
  local stand-in only.
- Scope: `09-delivery-roadmap.md` Phase 6. Governing documents: `13-consistency-system.md` (Tier 1
  and Tier 2), `15-prose-editor-and-memory.md` ("prose as a second way in"), ADR-014 (review),
  ADR-022 (the scene studio), ADR-023 (written with this phase). Level 4 in
  `19-assistant-first-experience.md`.

**Since this phase (checked 2026-10-21).** The checklist below records the state when the phase was
built. Later work: facts read from a scene with a story time start at that time (EXT-007,
2026-10-18), and reading a scene also suggests thread steps, changes of state and new threads
(EXT-001, batch 5 D and batch 8 D). Current status: [`10-backlog.md`](10-backlog.md).

Most of the machinery exists already. Reading a text into proposed changes (Phase 2.7,
`IntakeWorkflow.read_passage`) keeps only what it can quote, points every change at the exact
version and words, and sends the changes to the grouped review; commits are checked against the
head and are idempotent (ADR-014). A scene's prose is one of the author's texts (ADR-022). Phase 6
joins them up and fills the gaps.

## What we learned from other tools (research, 2026-10-17)

- Sudowrite reads a manuscript into its story bible, but users report it stopped after the first
  chapter, with no way to run it again by hand. So: reading is always an explicit, repeatable
  action, and the studio says when a scene has changed since it was last read.
- Novelcrafter links names it finds to existing entries and offers "new" or "merge". We already
  ask about names (Phase 2.7); the same questions appear for a scene.
- Novelcrafter's "progressions" record how an entry changes over the story. Our facts have story
  time (ADR-020); a fact read from a scene placed in time can start at that scene's time.

## Decisions

- **"Update the story from this scene"** is a job (ADR-022), reading the current version passage
  by passage with the existing reader. It records which version was read and which proposals came
  of it, so the studio can say "read at version 3; changed since".
- **Read only what changed.** The second time, only paragraphs that differ from the version last
  read are read, so kept changes are not proposed again.
- **Proposals go to the review queue**, never straight into the story. Each points back at the
  scene's version and words; from the review, "see it in the scene" opens the studio at those words.
- **A plan from the writing.** An author who writes without a plan can start a scene with only a
  title, and later ask the assistant to fill in its plan card (summary, viewpoint, place, who is
  in it) from the prose: a plan change, reviewed like any other.
- **After a change is kept**, the studio's checks are worked out again (they are never stored), and
  the assistant's notes on an older version are shown as about that version.

## Work

- [x] `scene_readings` table (migration 0035): scene, line of the story, version read, job, proposals
      made, when; forked with a what-if; purged with the project.
- [x] Reading job (`scene_read`): paragraph diff against the last version read; each changed passage
      read with `IntakeWorkflow.read_passage`; leases, cancellation and usage as other jobs.
- [ ] Facts read from a scene with a story time start at that time. Not done: the reader is shared
      with pasted texts and some kinds of fact cannot take a time; recorded in ADR-023.
- [x] Studio: "Your story bible" in the margin with the status (never read, read at version N,
      changed since), "Update the story from this scene" with its reasons when unavailable, progress
      and Stop, and the outcome with "Review it/them".
- [x] Review: "See it in the scene", opening the studio with the words selected.
- [x] A scene with only a title is made from the plan as before; "Fill in the plan card from the
      prose" (`scene_plan`) proposes summary, viewpoint, place and participants for review.
- [x] Tests: contract (`test_scene_reading.py`, `test_scene_plan_card.py`), integration (readings
      store and fork), component tests, and the browser loop draft → keep → check → read → review →
      see it in the scene, and the plan card, against the local stand-in model.
- [x] Playwright screenshot review against the design rules; ADR-023; HANDOFF.

## Adversarial review

One high, four medium and four low findings, all fixed with tests but one known limit; see ADR-023.
The fixes: readings are recorded passage by passage and resumed; lines, not paragraphs, are the
unit; one reading at a time, and an out-of-date one reads nothing; the allowance is checked before
each passage; plan card suggestions are marked `only_fills` and refused if the card changed; the
reader's passage is fenced with markers the text cannot contain.

## Design review (browser, `~/Projects/Common/rules.md`)

| Rule | Found | Changed |
| --- | --- | --- |
| F7 | One suggested change, but "review them" and "Review them" | "it" for one, "them" for several |
| S6, T4 | The plan card offer sat in the heading and pushed its actions below it | The offer is a plain notice under the heading, with the other notices |
| F7, plain language | "Approve and add to canon", "In canon", "added to canon" on screen | "your story" everywhere it is shown |

## State of the exit criteria

| Criterion | State |
| --- | --- |
| No model operation writes canon directly | Met: readings and plan cards end in proposals |
| Every accepted mutation links to scene evidence and a review decision | Met for changes read from scenes (evidence → version and words; review decision as for all proposals) |
| Committing twice is idempotent | Met by the existing commit path (ADR-014); unchanged |
| Stale reviews receive a conflict rather than overwriting newer state | Met by the existing commit path; unchanged |
| Full release-one workflow passes from a clean project | Met with the stand-in model in the browser; not run with a real provider |
