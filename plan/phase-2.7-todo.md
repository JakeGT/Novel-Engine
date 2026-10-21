# Phase 2.7 implementation todo: text intake

- Status: Built (2026-10-11). Three exit criteria need a real model or real use; see the end.
- Scope: `09-delivery-roadmap.md` Phase 2.7; backlog `EXT-008`, `EXT-009`, `CTX-016`,
  `CAN-013`, `CON-015`, `CAN-012`.
- Governing documents: `19-assistant-first-experience.md`, ADR-013, ADR-015, ADR-019.

**Since this phase (checked 2026-10-21).** The dashboard and checklists below record the state when
the phase was built. Later work: a rule can block (CON-015) and speak of story time and belief
(CAN-013, CON-005); a rule set to warn can be dismissed on one proposal (CON-016); reading a text
asks first when its estimate is above the author's threshold (OPS-002); proposal-only MCP access
exists (H; QRY-003, ADR-024); scene conditions can be preferred or written in the author's words,
and are judged at the scene's story time (STY-005). Still open: rules over the order of events,
comparing two arbitrary versions on screen (CAN-012), a form editor for plan changes on the review
page (EXT-004), review telemetry, and removing restored texts and rules with a failed import.
Current status: [`10-backlog.md`](10-backlog.md).

**Standing limitation:** the two assistant workflows here (reading a passage into suggested
changes, drafting a rule from a sentence) have only run against a scripted model. The owner
tests real models by hand and reports back (decided 2026-10-10).

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Paste and Markdown import, immutable versions | EXT-009 | Implemented; real database and browser tested |
| B. Marks and names found without a model | EXT-009 | Implemented; measured against a labelled passage |
| C. Name memory, asked one question at a time | CTX-016 | Implemented; scoped to the story or one text only |
| D. A passage read into suggested changes | EXT-009 | Implemented (scripted model only) |
| E. Scene conditions, readiness and postflight | — | Implemented by hand; not drafted by the assistant |
| F. Story rules from a sentence, warning only | CAN-013, CON-015 | Implemented; drafting is scripted model only |
| G. History views: provenance, compare, name a version, go back | CAN-012 | Implemented |
| H. Proposal-only MCP access | — | Not built |
| I. Labelled evaluation set | EXT-008 | Names: built and pinned. A model's reading: scorer only, nothing measured |

## A, B, C. Texts and names

- [x] A text is pasted or chosen as a plain text or Markdown file, kept exactly as given, and
      split at its headings. Changing the words makes a new version; earlier versions cannot
      change (`source_texts`, `source_versions`, migration 0030; `SourceService`, `/texts`).
- [x] Removing a text erases its words. Evidence that pointed at it then says the text was
      removed.
- [x] Marks (`@Mara`, `@"the Fox"`, `[[the Old Mill]]`) and known names and aliases are found
      with no model (`backend/domain/sources.py`). A one-word name is only matched with its
      capital, so "hope" is not Hope.
- [x] "Who is this?" is asked one phrase at a time: it is this entry, it is not, it is someone
      else, add it as a new entry, or it is not a name. The answer is remembered for the whole
      story or for one text (`alias_bindings`, `/names`, `NameQuestions.tsx`).
- [x] A marked name added as an entry points back at where the text first uses it.
- [x] A page for the author's writing: the words as written with names picked out, what is
      named, the questions, and versions (`TextsPage.tsx`, `TextPage.tsx`).
- [ ] Name memory is not scoped by time, narrative position, branch or point of view. Those
      arrive with Phase 3.
- [ ] A common word that is also a name is still mistaken at the start of a sentence ("Hope
      was all she had"). This is in the recorded baseline.
- [ ] Only plain text and Markdown. No DOCX, EPUB or Scrivener. Google Docs, DOCX and LaTeX import
      are deferred past release one by owner decision (2026-10-21); pasted text and Markdown stay the
      ways in.
- [ ] A text is at most 400,000 characters; longer work is brought in a chapter at a time.

## D. Reading a passage with the assistant

- [x] One bounded passage (24,000 characters, cut at a paragraph) is sent with the entries it
      names. The text is sent as material to read, never as instructions
      (`intake_workflow.py`, `POST /assist/texts/{id}/read`).
- [x] Every fact and belief must quote words that are really in the passage, or it is left
      out with a reason. An entry must be quoted or named in the passage.
- [x] What survives is an ordinary set of suggested changes to review. Each change shows the
      passage it came from, and "implied, not stated" where the model said so (`FromText.tsx`,
      on the review page and in an entry's activity).
- [x] A nickname or description the model thinks means an entry becomes a question, never a
      decision.
- [x] A long text is read a part at a time; the result says the text goes on and offers the
      next part.
- [ ] Unverified with a real model: whether quotes come back exact enough to be found, how
      much is left out as a result, and whether "implied" is used sensibly.
- [ ] A passage read twice suggests the same changes twice; the second set is flagged as
      already recorded only for facts, by the existing check.
- [ ] No estimate of what a read will cost is shown beforehand, only the remaining allowance.

## E. Scene conditions

- [x] A scene can say what must be true before it and what it should leave true after it.
      Each is about one entry and uses the same checks and evaluator as a story rule
      (`scene_conditions.py`, `PUT /plan/{id}/conditions`, `SceneConditions.tsx`).
- [x] The plan shows each with whether it holds now: readiness for "before", and whether the
      change has been recorded for "after". Nothing is blocked.
- [x] Conditions are part of the plan: reviewed, versioned and undone with the scene.
- [ ] They are set by hand. The assistant does not draft them, and reading a text does not
      check a scene's "after" conditions against what was read.
- [ ] At most six per scene, and only "must have" and "must not have".

## F. Story rules

- [x] A rule is data in a fixed vocabulary: which entries, and what must hold. Nothing an
      author or model supplies is run (`backend/domain/rules.py`, ADR-019).
- [x] A rule carries examples it should catch and allow. They run through the same evaluator
      as the story, and a rule whose example disagrees with it is refused.
- [x] A rule is shown as the author said it and as it is checked, in words built from the rule
      itself, with the entries that break it now (`RulesPage.tsx`).
- [x] A change that would newly break a rule is warned about on the review page, next to the
      change that causes it, and can still be kept. A paused rule says nothing.
- [x] Rewording a rule adds a version; earlier versions are kept (`story_rule_versions`,
      migration 0031).
- [x] The assistant drafts a rule from a sentence; drafting keeps nothing. What a rule cannot
      say is reported rather than bent into something else (`rule_workflow.py`).
- [x] A rule can be built entirely by hand, with no provider.
- [ ] Rules only warn. Publishing a rule as blocking (ADR-013) is not built.
- [ ] A rule cannot speak of time, order of events, belief or prose. "Nobody dead may speak"
      cannot be expressed until Phase 3.
- [ ] The by-hand builder edits one condition and one "only those that" clause. A drafted
      rule with more keeps the rest as drafted.
- [ ] No scoped overrides or suppressions (CON-016).

## G. History

- [x] Already present: every version, what changed in it, where a record came from, and going
      back to an earlier version as a new version.
- [x] Added: name a version, and see everything that changed since a version, in words
      (`HistoryPage.tsx`).
- [ ] Comparing two arbitrary versions (rather than one version and now) has no screen.

## I. Evaluation

- [x] A labelled passage from the reference world and a tool that scores the same
      name-finding the application uses (`backend/devtools/intake_eval.py`,
      `tests/fixtures/intake_eval`). Baseline `names/1` on `lantern-coast/1`: precision 0.933,
      recall 0.636 overall, 0.933 over what marks, names, aliases and answers can cover. First
      names alone, unanswered descriptions and pronouns: 0 of 7 found, reported as such.
- [x] A scorer for drafted facts against expected ones.
- [ ] No model's reading has been measured, so there is no extraction baseline.
- [ ] One passage of about 200 words. It pins behaviour; it is not a broad benchmark.

## Data ownership

- [x] Texts, name answers and rules are deleted with their project or workspace.
- [x] A project export carries them, and a restore brings them back with the same ids after
      canon is restored. Checked before anything is written.
- [ ] Removing a failed import does not remove texts or rules that import already restored.

## Not built

- Proposal-only MCP access (the roadmap makes it conditional on authentication for machine
  clients, which does not exist).
- Review telemetry showing how often changes are decided in bulk rather than one by one.
- A form editor for plan changes on the review page (carried over from Phase 2.6).

## Exit criteria (roadmap, Phase 2.7)

| Criterion | State |
| --- | --- |
| Import or paste prose, resolve its entities, review grouped proposals, commit without generation | Met by hand with no provider (browser tested). With the assistant reading: scripted model only |
| Invariant examples, scene conditions and commit results agree deterministically | Met: one evaluator, tested together |
| Extraction and entity-resolution precision/recall recorded against a versioned baseline | Entity resolution: recorded. Extraction: not measured; needs a real model |
| Review telemetry shows a scene does not need blind one-by-one approval | Not built. Bulk decisions exist; nothing records how they are used |
| Every accepted change can be explained back to source evidence and review decision | Met for changes from a text, while the text is kept |
