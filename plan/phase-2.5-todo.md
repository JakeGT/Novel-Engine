# Phase 2.5 implementation todo: assistant-led creation

- Status: In progress (started 2026-10-10).
- Scope: `09-delivery-roadmap.md` Phase 2.5; backlog `AST-EXP-001`–`AST-EXP-018`.
- Governing documents: [`19-assistant-first-experience.md`](19-assistant-first-experience.md)
  (interaction loop, levels, definition of done), ADR-017, [`20-design-direction.md`](20-design-direction.md).
- Rule for every item: an assistant path and a by-hand path, both tested; the assistant only ever
  produces a proposal.

**Standing limitation:** every assistant workflow below is tested with a scripted model, plus a
check that its output format converts to a strict JSON schema. None has been run against a real
provider by the implementer. Draft quality is unknown until the owner uses it with a key.

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Assisted creation and fill-in | AST-EXP-001, 002 | Implemented (scripted model only) |
| B. Grouped review | AST-EXP-006 | Partly implemented |
| C. The God Hand | AST-EXP-017 | Implemented, first version (scripted model only) |
| D. Real-provider verification and evaluation set | AST-EXP-003 | Not started; needs a provider key |
| E. Stored, resumable conversations | AST-EXP-004 | Not started |
| F. Start from a premise | AST-EXP-005 | Covered by A in practice; no dedicated first-run |
| G. Link suggestions (semantic) | AST-EXP-007 | Not started |
| H. First run and sample project | AST-EXP-008 | Not started; blocked on the provider-key decision |
| I. Levels and plain-language test | AST-EXP-009 | Not started (vocabulary mapping exists) |
| J. By-asking paths for existing features; belief editor; members | AST-EXP-010 | Partly covered by C |
| K. Session budgets and assistant threat cases | AST-EXP-011 | Not started |
| L. Pictures on entries | AST-EXP-018 | Not started |
| M. Design direction | AST-EXP-016 | Implemented; awaiting more owner feedback |

## A. Assisted creation and fill-in

- [x] Describe one thing or a whole idea → optional questions → one proposal of entries, details,
      and links (`canon_creation_workflow.py`, `POST /assist/questions`, `POST /assist/proposals`).
- [x] Existing entries are linked by normalized name or alias within a type, never recreated.
- [x] Mentioned-but-unknown things become bare entries; `GET /entities/needs-detail` derives the
      to-fill-in list; the same assistant fills in any existing entry.
- [x] "New entry" opens the assistant; "Fill it in by hand" is one action away and keeps what was
      typed; the no-provider case falls back cleanly (`AssistFlow.tsx`).
- [x] Output format is provider-safe (strict JSON schema test).
- [ ] Run against a real provider and tune prompts (workstream D).

## B. Grouped review

- [x] A proposal is shown in groups by kind, in the order it will be applied, with a one-line
      summary and a kept count per group.
- [x] "Keep all" and "Skip all" per group.
- [x] What the assistant understood, and what else the change affects, are shown above the
      changes when arriving from the assistant.
- [ ] Bulk decisions in one request (today: one request per change, sent in order).
- [ ] "Revise this change" by asking, instead of editing its stored form.
- [ ] Check a drafted proposal against existing canon for contradictions before review (today:
      the structural validator only).
- [ ] Undo an accepted proposal in one step from the review screen (today: History → go back).

## C. The God Hand

- [x] One instruction → every implied change of any kind in one proposal: new entries, renames,
      added and removed names and details, facts added and ended, beliefs added and ended, entries
      archived (`canon_change_workflow.py`, `POST /assist/changes`).
- [x] It sees the whole story bible by id (up to 200 entries, 300 facts, 200 beliefs) and the entry
      the author is looking at.
- [x] Ambiguous instructions return questions and propose nothing.
- [x] Deterministic consequences: a single-valued fact's old value is ended when a new one is
      added; archiving reports how many current facts will still mention the entry; the model's
      own notes are passed through as notes, never acted on.
- [x] Unresolvable ids and no-op changes are discarded with reasons; an instruction that changes
      nothing is reported plainly.
- [x] Available from the top bar, the search palette, and each entry page (`GodHand.tsx`).
- [ ] Merge as a first-class operation (today the model composes it from move-and-archive).
- [ ] Events, plan records, types, and kinds of fact (today: entries, facts, beliefs only).
- [ ] Projects larger than the context limits (retrieval instead of listing everything).
- [ ] A persistent conversation instead of a dialog; follow-up instructions on the same proposal.
- [ ] A labelled instruction set with expected operations (workstream D).

## D–L

Not started; see the roadmap and backlog for acceptance criteria. Suggested order once the owner
has tried A–C with a real provider: D (tune what exists), L (pictures), E (conversations), the
rest of B, then H and I together once the provider-key decision is made.

## Open owner decisions that block work here

1. How a new author gets a working assistant on first run (blocks H).
2. Whether the first real-provider runs show the default model is good enough, or it should change.
