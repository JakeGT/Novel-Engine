# Phase 2.6 implementation todo: story planning through conversation

- Status: Built (2026-10-11). Two exit criteria can only be met by real use; see the end.
- Scope: `09-delivery-roadmap.md` Phase 2.6; backlog `AST-EXP-012`–`AST-EXP-014`.
- Governing documents: `19-assistant-first-experience.md` ("Story planning by conversation"),
  `03-domain-and-temporal-model.md` ("Plan records are intent"), ADR-018.

**Standing limitation:** the three assistant workflows here (planning questions, drafting a
plan, "what could happen next") have only run against a scripted model. The owner tests real
models by hand and reports back (decided 2026-10-10).

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Plan records as intent | AST-EXP-012 | Implemented; real database tested |
| B. Outline view and editor (by hand) | AST-EXP-014 | Implemented; looked at in a browser |
| C. Readiness in plain words | AST-EXP-014 | Implemented, derived on read |
| D. Planning interview | AST-EXP-013 | Implemented (scripted model only) |
| E. "What could happen next?" | AST-EXP-013 | Implemented (scripted model only) |
| F. Threads, promises, payoffs | AST-EXP-012 | Implemented as plan records; open ones are to-dos |
| G. Scene contracts | — | Not built |
| H. Evaluation of plan quality | AST-EXP-013 | Not built; structure is tested, quality is not measured |

## A. Plan records

- [x] Story, arc, beat, scene and thread are one kind of record with their own table
      (`plan_items`, migration 0028) and three operations. They go through the same review,
      journal, history and undo as the rest of canon (ADR-018).
- [x] They are never entries, facts, beliefs or events, and the graph projector skips them, so
      no reading of the story can return what is only planned. Tested against the API and
      against the tables themselves.
- [x] A plan item names entries by role from a fixed list: point of view, place, participant,
      about. One point of view (a character) and one place (a location) at most.
- [x] An arc sits under the story, a beat under an arc, a scene under a beat; every level is
      optional. Cycles and impossible nestings are refused.
- [x] Archives carry the plan in its own section, marked as intent, outside the story's records.
- [ ] A removed plan item cannot be put back by undo; undoing an edit can.
- [ ] Archiving an entry that a plan names is not blocked; the plan then lists it as a gap.

## B and C. Outline, editor, readiness

- [x] A Story plan page: the outline top to bottom, who and where in ordinary words, threads in
      their own section with where each is opened and resolved (`PlanPage.tsx`).
- [x] By hand, with no provider: add, change, reorder beside neighbours, move, and remove with
      everything under it as one version (`plan_service.py`, `/plan`).
- [x] What the plan still needs is derived each time and said as a sentence ("This scene needs a
      place"). Gaps are never errors and never block anything.
- [x] The page joins the menu once there is a plan or the author has opened it. The home page
      shows a plan's gaps, or makes one offer to start planning once the story bible has five
      entries, which can be turned down for good.
- [ ] Reordering is by "Move up" and "Move down", not by dragging.
- [ ] A plan change waiting for review can be kept, skipped, or edited in its stored form, but
      has no form editor there, and "ask the assistant to change these" does not alter plan items.

## D and E. Planning with the assistant

- [x] The author says what they mean to happen and how far to go (arcs, beats or scenes); the
      assistant may ask questions first; the result is plan items to review
      (`plan_workflow.py`, `POST /assist/plan/questions`, `/proposals`).
- [x] Grounded in the story bible: it names real entries by id, and a person or place the plan
      needs that does not exist is drafted as a bare entry on the to-fill-in list.
- [x] What the author has not decided is returned as "left open", not written into the plan.
- [x] "What could happen next?" offers up to three directions with what each would change.
      Offering proposes nothing. The one the author picks is checked again as if a model had
      sent it, and becomes plan items to review (`/directions`, `/directions/choose`).
- [x] Whatever the model returns is typed and bounded: unknown kinds, impossible nestings,
      circular outlines, entries from elsewhere and made-up roles are left out with reasons.
- [x] A planning conversation is kept and can be picked up again (migration 0029).
- [ ] The God Hand ("Change anything") does not change the plan; planning has its own dialog.
- [ ] Whether a real model actually refrains from inventing is unverified. The instructions say
      so and the structure cannot be faked, but the words in a summary are the model's.

## G, H. Not built

- Typed scene contracts (preconditions, expected changes) are not built. The roadmap makes them
  optional; a scene's intent is its summary and its notes.
- No fixtures measure plan quality or how grounded a draft is. Tests cover structure and bounds.
- A plan is not derived from written scenes (that is Phase 6).

## Exit criteria (roadmap, Phase 2.6)

| Criterion | State |
| --- | --- |
| A first plan (arcs and six beats) accepted within 30 minutes | Not measured; needs a person and a real model |
| A plan can be built with no provider through the outline editor | Met; exercised in a browser against a real backend |
| Planned intent never appears as fact in any query or export | Met by construction and tested |
| Gaps are reported as open items, never filled by invention | Met for structure; a real model's wording is unverified |
| An author who does not outline can ignore the plan and is not blocked | Met: nothing requires it, and the one offer can be declined for good |
