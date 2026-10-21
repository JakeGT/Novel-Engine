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

## Amendment (2026-10-18): an arc's path of stages, in details

An arc may carry a path (`_path`, STY-006): up to nine named stages in order, each with the scene
where it is reached (or none yet; the first stage is where the character starts) and, optionally,
a fact that should hold from then on. It is kept in the arc's details like the keys above, so it
is part of the plan, has history and undo, forks with what-ifs and travels in archives; no table,
column or operation field was added, and no migration was needed (0039 is `0039_check_passes`,
for check notes, not arcs). The plan service checks that stages are on an arc, have their own
names, name active scenes and current facts, and fit in one detail (2,000 characters); the commit
validator checks a `_path` sent any other way (a raw change for review) the same, looking up only
the scenes and facts the change adds. Removing a scene puts its stages back to "not placed" in the
same change. Stages out of order, a fact not true at its scene's story time (or no longer there), a
long stretch with no stage (more than a quarter of the plan's scenes and at least four) and
unplaced stages are derived on read in `backend/domain/arcs.py` and never block. Drafting a scene
that moves its viewpoint character's arc is given the stage going in and the stage reached, as a
required context item. A stage's fact that is a secret the viewpoint character does not know is
not part of that item: it is kept apart for the author, like any other secret.

## Amendment (2026-10-21): shared scales, the assistant's planning help, and checks against the prose

**Shared scales (STY-006; migration `0051_planning_help`).** A story may keep up to eight scales
that many characters share ("Trust": None → Wary → Some → Complete), each a name, two to nine
named levels, whether they go in order, whether a character moves one level at a time, and a
line on what it is about. Levels are words, not scores: an author's "Wary" is not 2 out of 4, and
putting a number on it would invite arithmetic the story never meant. The scales sit beside the
story's unit and travel table on `story_clocks` (`scales`, `scales_version`), shared by every
line of the story and never forked into a what-if, and are saved against their version so two
people cannot overwrite each other unnoticed (409, said plainly). They travel in the project
archive; a restore fills them only into a story with none. An arc's stage may name a scale and a
level (in `_path`, as before); the plan service refuses a level the scale lacks and refuses
removing a scale a stage still uses. Worked out on read in `backend/domain/arcs.py`, never
blocking: where each arc's character stands scene by scene and whether each step moves toward
or away from where the arc ends (only for scales in order), a jump of more than one level on a
one-step scale, a stage on a scale or level no longer there, and a stage's fact that stops being
true (by story time) at a scene before the next stage is reached — said once, at the first such
scene, and never when either scene has no story time.

**The assistant's planning help (STY-003, STY-004).** Two background jobs, each estimated and
shown with its cost before anything is sent, subject to the confirmation threshold and the
person's limits, with usage recorded once per call. Neither changes the plan: each ends in a
plan change for review with the model as its origin.

- *What a scene is for* (`scene-intent` v1): from the plan card and, when there is one, the exact
  text version the author asked about, the assistant suggests the goal, what stands in the way
  and the outcome. Each part must come with words copied from the card or the scene, or it is
  dropped (a quote in the scene becomes evidence on the change). Only empty parts are filled; the
  commit validator treats a filled-in intent like added thread steps (`only_fills`), so if the
  author has since set a part, approving the suggestion is refused rather than overwriting it.
  This follows Swain's scene unit — goal, conflict, disaster — which our goal / in the way /
  outcome (yes, no, yes-but, no-and) already modelled ([Scene and sequel][swain]).
- *Bridge scenes* (`bridge-scenes` v1): offered only beside a required "before" condition that is
  unmet where the scene comes (the outline's own reading, checked again when the job runs). The
  assistant suggests one or two scenes placed just before it, naming people and places only from
  the story bible by id *and* name (a mismatched pair is dropped), and each bridge carries the
  unmet condition as what it should leave true, so the plan reads the original scene as ready
  once a bridge is kept, and the bridge's own "after" is then checked against its prose like any
  other. A bridge is a plan, not a fact. This is Swain's sequel — "a unit of transition that links
  two scenes" ([Scene and sequel][swain]) — used narrowly: only the gap the author's own condition
  names is bridged.

**Checks against the prose (STY-005, SCN-007, CON-004, CON-014).** Deterministic, worked out with
the studio's other checks on every save, never blocking, notes unless nothing could make both
true. The facts they need are gathered from the story without reading the prose
(`backend/services/prose_signals.py`); the reading is in `backend/domain/drafting.py` and
`backend/domain/appearance.py`. Each reads only signals it can read for certain and says nothing
otherwise; a pronoun is never resolved, and what characters say aloud is not read.

- *"After" conditions*: a condition naming an entry and the other end of its fact (or its value)
  is a note when no paragraph names both. Naming both is not proof the scene shows it, so a
  co-mention is not reported as "shown"; whether the scene actually does it is left to the plan
  pass of the assistant's check, which already receives every condition.
- *Time words*: only the opening's words for how a scene follows the one before in reading order
  ("the next morning", "later that day", "three days later") are compared with the two scenes'
  story times, in the story's unit; a whole unit ("day 5") means some time in that day. "The next
  morning" on the same day is a warning; long gaps and amounts far from story time are notes.
  Recognising such phrases is the easy part; anchoring them is the part that needs rules
  ([TimeML][timeml]); we anchor only to the previous scene and only in units with a length in days.
- *Resources*: a sentence naming someone in the scene, a word for using, then a thing someone
  else has at that time (and is not named), or that is used up, lost or destroyed by then.
- *Appearance*: colour words for eyes, hair and beard next to a character's name (or their
  possessive) against their accepted details — `HAS_DETAIL` facts and the accepted descriptions of
  their pictures (CON-014) — a note quoting both and naming the picture when it came from one.
  Colours are grouped so near words agree ("hazel" and "brown"), and a sentence that says "not",
  "dyed" or names two people is read only through the possessive. Physical details shifting
  between scenes are among the commonest continuity slips editors look for
  ([Career Authors][gaffes]).

Measured on 18 new labelled cases in `tests/eval/consistency` (11 defects, 7 near misses): 7
caught, 0 false alarms; the four misses are a pronoun ("her blue eyes" with the name in the
sentence before), "it" for the thing, a time of day with no "next" word, and a sentence that
names both ends while saying it does not happen — each recorded as a limit.

[swain]: https://en.wikipedia.org/wiki/Scene_and_sequel
[timeml]: https://www.academia.edu/111118722/The_Specification_Language_TimeML
[gaffes]: https://careerauthors.com/5-ways-to-avoid-continuity-gaffes/
