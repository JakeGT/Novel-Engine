# ADR-018: Story plan records are a separate kind of record, never entries

- Status: Accepted (2026-10-11)
- Context: Phase 2.6; `03-domain-and-temporal-model.md` ("Plan records are intent");
  `19-assistant-first-experience.md` ("Story planning by conversation").

## Decision

A story plan (story, arc, beat, scene, thread) is stored as its own kind of record:
its own table (`plan_items`), its own three operations (`create_plan_item`,
`update_plan_item`, `retire_plan_item`), and its own read path (`/plan`).

It is governed exactly like the rest of canon. A plan item changes only through a
reviewed operation, is versioned in the same journal, appears in history, and is undone
by the same mechanism.

It is never an entry, a fact, a belief or an event, and it is never written to the graph
projection. The projector skips the three plan operations explicitly.

## Why not entries of reserved types

Reusing entries (types `Arc`, `Beat`, …, linked by facts) would have been a fraction of the
work: history, review, undo and export would have come for free. It was rejected because
the plan's one hard rule is that *planned intent never appears as established fact in any
query or export*. With shared tables that rule holds only as long as every present and
future query remembers a filter; the graph projection, which later phases read for
retrieval, would have held planned scenes beside real ones. With a separate table the rule
holds by construction: no query over entries, facts, beliefs or events can return a plan
item, and there is nothing to forget.

## Consequences

- One definition of what an update means (`apply_plan_update`) is shared by the store, the
  history replay and the validator.
- A plan item names entries by role from a fixed vocabulary (`point_of_view`, `place`,
  `participant`, `about`). Archiving an entry a plan names is not blocked: the plan then
  reports it as something to look at.
- What a plan still needs (a scene with no place, a thread nothing resolves) is derived on
  read, never stored, and is never an error.
- A removed plan item cannot be put back by undo (as with entries); undoing an edit can.
- Archives carry the plan in a top-level `plan` section marked `intent`, outside `canon`.
  Restoring replays the journal, so the plan is rebuilt from its own operations.
