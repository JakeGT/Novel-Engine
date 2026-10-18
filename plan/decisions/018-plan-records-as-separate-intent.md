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

## Amendment (2026-10-17): more structure in details, not columns

A beat or scene's threads moved along (`_advances`, STY-002) and a scene's intent
(`_intent`: goal, what stands in the way, outcome; STY-003) are kept in the item's details
under reserved keys, like conditions and reveals, so no column or operation field was
added and every earlier envelope checksum is unchanged. The plan service checks them; the
commit validator sees them only as details within the usual size limits. A thread's steps
are derived on read in outline order (`backend/domain/threads.py`), never stored. Promoting
either to typed operation fields later would need a migration and a replay that reads the
reserved keys.

## Amendment (2026-10-18): conditions and reveals carry more, still in details

A scene condition (`_conditions`) may be required (`hard`, the default and not stored) or preferred
(`soft`), and may be a sentence in the author's words instead of a check on an entry (STY-005). A
reveal (`_reveals`) may mark the scene where a fact comes out (`main_reveal`, stored only when true).
Both keep earlier stored values readable and unchanged, so saving the same list again is still "no
change". Conditions are judged where the scene comes: at its story time against facts with time
spans when it has one, otherwise against the story as it stands, and against the nearest earlier
scene whose "after" names the same entry and kind of fact (exact match only; no inference). Too-early
reveals, threads out of order, unmet required conditions and the promises to the reader (opened
threads and hinted facts, with the reading distance in scenes) are derived on read in
`backend/domain/plan_checks.py` and `scene_conditions.py`, never stored, and never block.
