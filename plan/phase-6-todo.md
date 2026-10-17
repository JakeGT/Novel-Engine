# Phase 6 implementation todo: from written scenes back into the story

- Status: Planned (2026-10-17), after Phase 5.
- Scope: `09-delivery-roadmap.md` Phase 6. Governing documents: `13-consistency-system.md` (Tier 1
  and Tier 2), `15-prose-editor-and-memory.md` ("prose as a second way in"), ADR-014 (review),
  ADR-022 (the scene studio). Level 4 in `19-assistant-first-experience.md`.

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

- [ ] `scene_readings` table: scene, line of the story, version read, job, proposals made, when.
- [ ] Reading job: paragraph diff against the last version read; read each changed passage with
      `IntakeWorkflow.read_passage`; usage and cancellation as other jobs; record the reading.
- [ ] Facts read from a scene with a story time start at that time unless the text says otherwise.
- [ ] Studio: "Update the story from this scene", progress and stop, result with a link to the review;
      "changed since it was last read" status.
- [ ] Review: "See it in the scene" for evidence from a scene's text.
- [ ] New scene with only a title from the studio and the plan; "Fill in the plan card from the
      prose" as a reviewed plan proposal.
- [ ] Tests: reading twice proposes nothing new for unchanged text; stale review conflicts; commit
      twice is idempotent; a what-if's scene reads into the what-if only; a reader-only member cannot
      start a reading; the end-to-end loop plan → draft → keep → read → review → commit in a browser.
- [ ] Playwright screenshot review against the design rules; ADR-023; HANDOFF.

## Exit criteria (roadmap)

- No model operation writes canon directly.
- Every accepted change links to scene evidence and a review decision.
- Committing twice is idempotent; a stale review gets a conflict, not an overwrite.
- The whole release-one workflow passes from a clean project.
