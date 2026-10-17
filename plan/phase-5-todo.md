# Phase 5 implementation todo: the scene studio

- Status: Built (2026-10-17). Every assistant path has run against a scripted model and a local
  stand-in only, never a real provider.
- Scope: `09-delivery-roadmap.md` Phase 5; backlog `SCN-001`–`SCN-007`, `CON-001`, `CON-002`,
  `CON-008`, `CON-010` as far as one scene; `CON-018` (stock phrasing) in its simplest form.
- Governing documents: `04-ai-and-retrieval.md` (scene generation pipeline), `13-consistency-system.md`,
  ADR-005, ADR-021, ADR-022 (written with this phase). Level 4 in `19-assistant-first-experience.md`.

## Decisions taken for this phase (ADR-022)

- **A scene's prose is one of the author's texts**, linked to the scene, so it has immutable
  versions, names found without a model, the passage index, and (Phase 6) reading into proposed
  changes, for free. Each version records where it came from: the author, or a kept draft with
  the job and the exact context it was drafted from. In a what-if, the first change copies the
  text, so the main story's prose is never touched.
- **Drafting is a job**, stored in PostgreSQL and claimed with `FOR UPDATE SKIP LOCKED` under a
  lease, so a crashed worker's job is picked up again; it re-checks the author's access before it
  runs, can be cancelled between steps, and records usage once per model call.
- **A draft is a proposal.** The assistant's drafts wait beside the text; keeping one makes it a
  new version, discarding it deletes it. Nothing the model writes replaces the author's words on
  its own.
- **The context is shown first** (ADR-021) and kept with every draft.
- **Checks run on every draft and every version without a model**: new names, a secret the
  viewpoint character should not know appearing on the page, a planned reveal missing, the place
  or viewpoint never mentioned, stock phrasing, and length. A check with the assistant is the
  author's choice, says what it reads first, and every finding must quote the text or it is
  dropped.
- **How you write** is a note per story, given first in every draft request (research: drafting
  tools load the style guide before anything else).

## What is built

- [x] A scene's prose: a text of kind `scene`, linked per line of the story; saving names the version
      it was based on and a stale save is refused with the author's words kept (migration 0034).
- [x] Versions with lineage: who wrote it (the author, or the assistant through a kept draft), the
      version before it, the draft and the exact context. Copy on write in a what-if, with the full
      history; the main story keeps its own text when a what-if shares it.
- [x] A job queue in PostgreSQL: `SKIP LOCKED` claims, leases renewed during model calls, writes tied
      to the lease, attempts capped, put back on shutdown, access checked again before running, run in
      the what-if it was asked in, cancellation between steps, idempotency keys, three active jobs per
      person per project, plain-words failure codes (`backend/services/job_runner.py`).
- [x] Drafting (`scene_draft` job): context of ADR-021 kept with each draft; 1–3 variants; the
      assistant's drafts wait to be kept or discarded; at most nine waiting per scene.
- [x] Checks with no model on every version and draft: new names, a secret on the page, a planned
      reveal missing, place or viewpoint never named, stock phrasing, length against what was asked.
- [x] A check with the assistant (`scene_review` job): notes kept only if they quote the text, in five
      categories, stored with the version or draft they are about.
- [x] "How you write": a note per story, given to every draft as material.
- [x] An estimate before sending; usage recorded once per call, on the allowance and the job; the
      allowance checked again before each draft.
- [x] Prompts that keep rules and material apart (fences the text cannot fake), and secrets only in
      the author-only section, also when an earlier scene showed them to the reader or no viewpoint is
      chosen (found by the adversarial review, fixed, tested).
- [x] The archive carries scene links, the voice note and version origin.
- [x] The studio screen (`StudioPage`): the plan card, the text, checks and the assistant's notes in
      the margin with "Show in the text", drafting with cost and progress and Stop, drafts to read,
      keep or discard, versions, "How you write"; "Write this scene" on every scene in the plan; the
      texts page sends a scene's words to its studio.
- [x] A browser test of the whole path against a local stand-in model
      (`e2e/scene-studio.model.spec.ts`, `scripts/e2e/fake_model.py`, `playwright.model.config.ts`).

## What is not

- [ ] No real provider has drafted or checked a scene. Draft quality, the prompt and the review's
      usefulness are unmeasured.
- [ ] No streaming: a draft appears when it is finished.
- [ ] A draft is kept or discarded whole; no editing inside a draft, merging, or "rewrite this passage".
- [ ] Media in drafting context: pictures are named, not sent (Phase 4 limit).
- [ ] Typed scene contracts, character-arc paths, threads and readiness checks (roadmap items) are not
      built beyond the plan's reveals and set-ups from Phase 2.6/3.
- [ ] Consistency profiles, suppressions, incremental invalidation and impact preview are not built;
      checks are worked out each time.
- [ ] Known limits listed in ADR-022 (per-job token total, counted caps, a taken-over check).

## Adversarial review (2026-10-17)

A read-only reviewer found 2 high, 7 medium and 10 low issues; a test-writing pass found 10 more bugs.
All were fixed with tests except the three known limits above. The highest: a draft could be kept into
another what-if; a secret shown to the reader earlier could reach the draft prompt outside the
author-only section; a worker that lost its lease could end the run that took over; the main story
lost its version history on its first save after a what-if; saving at the same moment from two tabs
gave a server error and could have logged the prose (bound values are now hidden from logs).

## Design review (browser, `~/Projects/Common/rules.md`)

| Rule | Found | Changed |
| --- | --- | --- |
| V1 | The entry point was planned inside the scene's menu | "Write this scene" is a visible button on every scene |
| WCAG 2.5.3 | The link read "Write this scene" but was named "Write the scene “…”" | The accessible name starts with the visible words |
| F1, S6 | "Draft kept" and "notes ready" messages piled up; only one could be dismissed | One message at a time, each dismissable; new work clears the last |
| T6 | The versions table wrapped ("2 (current)", "just now") | Short labels and no wrapping in those columns |
| T4 | A quoted passage ran into "Show in the text" | Space after the quotation |
| F7 | The estimate said "tokens" with no explanation | Explained as the measure providers charge by |
| V5 | The texts page hid "change" and "remove" behind "More" | Labelled "Change or remove"; a scene's text shows a link to its studio instead |

## Exit criteria (roadmap, Phase 5)

| Criterion | State |
| --- | --- |
| A planned scene can be generated through an observable cancellable job | Met (scripted and stand-in models) |
| Draft lineage and exact context are inspectable | Met in data and API (version origin, draft, manifest); the screen shows who wrote each version, not yet the context behind it |
| Manual editing works without an AI provider | Met |
| Plan requirements and findings are visible beside the draft | Met (plan card, checks and notes in the margin) |
| Failures do not lose plans or manual prose | Met (stale and simultaneous saves keep the words; unsaved words kept in the browser) |
| Deterministic consistency runs on each generation without paid inference | Met |
| Provider attempts and actual usage are recorded exactly once | Met per call (own key per call) |
| Context manifests reproduce whether original media or a derived fallback reached the provider | Not met: no media reaches a provider |

