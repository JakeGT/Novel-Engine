# Phase 3 implementation todo: time, what-ifs and knowledge

- Status: Built (2026-10-17). Assistant paths have only run against a scripted model; see the end.
- Scope: `09-delivery-roadmap.md` Phase 3; backlog `TMP-001`–`TMP-005`, `KNW-001`–`KNW-006`,
  `QRY-001`, `QRY-002`, and the record-level rebasing left over from Phase 2.
- Governing documents: `03-domain-and-temporal-model.md`, `18-author-rules-canon-queries-and-derived-views.md`,
  `19-assistant-first-experience.md` (all of this is Level 5), ADR-004, ADR-005, ADR-012,
  ADR-020 (written with this phase).

**Since this phase (checked 2026-10-21).** The checklists below record the state when the phase was
built. Later work: trust between characters and recorded witnessing of an event (KNW-006, batch 4
B); how each belief was learned (KNW-002); knowledge used before it was learned, someone in a scene
after their death, travel and age arithmetic (CON-003, CON-004, CON-006); the "Promises to the
reader" view by reading distance (STY-007); the reader's guide as a page to print and per part
(QRY-002); and archives that carry every what-if (CAN-010). Still open: calendars (by design), a
what-if of a what-if, conflicts per field, reveals read from prose, and an assistant path for
telling or choosing reveals. Current status: [`10-backlog.md`](10-backlog.md).

Everything here is optional for the author. The defaults stay: one story, time not stated,
the author's own point of view. Nothing at levels 1–4 asks for a time, a what-if or a viewpoint.

## Decisions taken for this phase (ADR-020)

- **Query store.** Branch, time and point-of-view queries are answered from PostgreSQL. Neo4j
  keeps projecting the main story only; a what-if is never projected. This settles the
  evaluation gate in ADR-012 for this phase.
- **A what-if copies the story when it starts.** Forking copies the story's current records
  into the new what-if in one transaction, keeping their ids. Reads then filter by one branch
  id, so nothing from a what-if can reach the main story or a sibling by construction. The
  plan said "does not copy the entire graph"; a story is thousands of rows, and copying makes
  isolation structural instead of a filter every query must remember.
- **The working what-if travels with each request** (`X-Story-Branch` header, checked to
  belong to the project). With no header, everything is the main story, as before.
- **Story time is the author's own number line.** A story counts time in one unit it names
  (years by default). A time is unknown, at a point, between two points, or relative to an
  event ("three years before the war"), always with the author's own words kept as a label.
  It is never converted to a calendar date.
- **Narrative order is the order of scenes in the plan.** A scene can say when it happens;
  one set earlier than the scene before it is a flashback, shown, not an error.
- **Knowledge is deny-by-default.** A character knows what they believe and what everyone in
  the story knows. A secret is known only to those who hold it. Whether a belief is right is
  derived from the facts, never stored; so is whether something said was a lie.
- **What the reader has been shown** is recorded per scene and read in narrative order.

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Story time: values, ordering, facts true for a span | TMP-001, TMP-002 | Implemented; real database and browser tested |
| B. What-ifs: start, work in, compare, bring back, set aside | TMP-003–TMP-005 | Implemented; real database and browser tested |
| C. Who knows what: secrets, common knowledge, knowledge view | KNW-001–KNW-003 | Implemented |
| D. Telling: speech that gives listeners beliefs | KNW-005, KNW-006 | Implemented by hand; trust not modelled |
| E. What the reader learns, scene by scene; irony; reveals | KNW-004, STY-007 | Implemented by hand; no Chekhov-debt view |
| F. Contradictions in time, one-at-a-time facts and knowledge | — | Partly: refused at commit, see F |
| G. State at a time; spoiler-safe reader's guide | QRY-001, QRY-002 | Implemented; Markdown only |
| H. Record-level rebasing of stale proposals | — | Implemented (amends ADR-014) |
| I. Assistant paths and Level 5 triggers | — | Implemented (scripted model only) |
| J. Screens, checked in a browser against the design rules | — | Implemented; review below |

## A. Story time

- [x] A time is unknown, at a point, between two points, or relative to an event, with the
      author's words kept (`backend/domain/worldtime.py`, property-tested).
- [x] Events can be placed in time; facts can start and stop being true; scenes can say when
      they happen (migration 0032; `amend_fact`; `PUT /plan/{id}/when`).
- [x] A loop of relative times, or one anchored to a missing event, is refused at commit. A fact
      cannot stop before it starts.
- [x] One-at-a-time kinds may hold different values at different times; unstated spans cover the
      whole story, so nothing changes for authors who never state a time.
- [x] The story names its unit of time (`/timeline/unit`); the timeline and "what was true at a
      moment" (`/timeline`, `/timeline/moment`).
- [x] Kinds of fact the setup marks as timeless refuse a time, and the screen says so.
- [ ] No calendars: a story counts in one unit on a number line, by design (ADR-020).

## B. What-ifs

- [x] Starting one copies the main story in one transaction; reads, proposals, history and the
      version a change is based on all follow the `X-Story-Branch` header.
- [x] Nothing in a what-if reaches the main story or another what-if (contract, database and
      browser tests). A what-if of another project is "not found".
- [x] Compare: what each line changed since the fork, and what both changed.
- [x] Bring back: one proposal on the main story, conflicts decided per record, reviewed like any
      change, each operation citing the what-if.
- [x] Set aside and take out again; nothing is lost.
- [x] Only the main story is projected to the graph; the outbox consumer skips what-if commits.
- [ ] A what-if is always started from the main story; no what-if of a what-if.
- [ ] Conflicts are decided per record, not per field.
- [ ] The equivalence check and archives cover the main story only.

## C, D. Who knows what, and telling

- [x] Facts are ordinary, known to everyone, or secret (`amend_fact`).
- [x] A character's knowledge: what they know and how, what they get wrong, what nothing
      confirms, secrets they do not know, and how many facts are not recorded either way.
- [x] Who knows a fact, and who believes otherwise.
- [x] Telling: an event with speaker, listeners and overhearers, and a belief for each hearer;
      overhearers believe it at 0.6. Whether it was a lie is read from the facts.
- [ ] Trust between characters is not modelled; every listener believes what they are told.
- [ ] Taking part in an event is never treated as perceiving it (deliberately), so knowledge
      from events must be told or entered.

## E. The reader

- [x] A scene lists the facts it shows the reader, plainly or as a hint.
- [x] Read in outline order: dramatic irony with the scene's viewpoint character, what the
      viewpoint character knows that the reader has not been shown, repeated reveals, and
      flashbacks (by scene time).
- [ ] Reveals are entered by hand; nothing reads them from prose yet (Phase 6).
- [ ] No Chekhov-debt view ordering open setups by reading distance; threads remain the plan's
      way to track setups and payoffs.

## F. Contradictions

- [x] Refused at commit: relative-time loops and lost anchors, a span that ends before it starts,
      two values of a one-at-a-time kind at one moment.
- [x] Shown on the timeline: an event whose anchor is gone or loops.
- [ ] Not detected: someone taking part in an event after they are recorded as dead, travel or
      age arithmetic, knowledge used before it was acquired. These need prose or richer event
      semantics (Phase 5).

## G. State at a time; reader's guide

- [x] What was true at a moment of story time, and what had happened by then.
- [x] A reader's guide up to any scene, or as one character knows it, as a Markdown file: leaves
      out unshown secrets, facts that start after the scene's time, and entries not yet met
      (`/reader/guide`; contract tests with leakage cases).
- [ ] Only Markdown; no other formats.

## H. Record-level rebasing

- [x] A stale proposal touching nothing that changed since keeps its approval when moved onto
      the newer version; one that overlaps goes back to review, and the review page names what
      changed (`changed_since`). This amends ADR-014; see ADR-020.

## I. Assistant paths and Level 5 triggers

- [x] "Change anything" can place events in time ("three years before the war"), bound facts in
      time so a move at a stated time keeps both places, and mark facts secret or common
      ("Tobin doesn't know this").
- [x] "What if Mara refused?" typed into "Change anything" is offered as a what-if: started,
      and the instruction run inside it.
- [x] Triggers, each tested: a "What if…" instruction offers a what-if; a character who believes
      something false is offered "Keep the truth a secret"; a replaced one-at-a-time value
      offers keeping both with times, pointing to the timeline.
- [ ] No assistant path for telling or for choosing a scene's reveals; both are by hand.
- [ ] Scripted model only. Whether a real model fills times and secrecy correctly is unmeasured.

## J. Screens and the design review

Timeline, What-ifs, compare, a fact's "When and who knows", a character's knowledge, telling,
scene reveals and time, the reader's guide, and the what-if band were each screenshotted in a
real browser at desktop and phone widths and read against `~/Projects/Common/rules.md`. Found and
fixed:

| Rule | Found | Changed |
| --- | --- | --- |
| V2 | A worded time ("three years before the war") hid where it falls | The resolved year is shown under it |
| T6 | Number fields stretched to 230px | Sized to their content |
| F7, V3 | Example text inside fields looked like typed answers | Examples moved to hints below |
| V2 | Placing an old event ignored what the author had written for it | Their words are offered as the time's words |
| F1 | A time could not be predicted before saving | "Reads as" / "Shown as … Placed: …" line |
| D7 | "Nothing has changed yet" sat below the disabled button | Beside it |
| L4 | "Mara Venn, who believe it" | Agreement fixed |
| F2 | A "Secret" tag sat beside a mistaken belief, not the truth it hides | Said in the line about the truth |
| L1 | Belief badges said "canon" | "True in your story" and so on |
| V5 | The menu's "More" and each plan item's "More" | "Pages you have not used yet"; "Move, plan, remove" |
| F7 | The "Change anything" dialog was titled "The God Hand" | Titled as its button |
| V2 | A conflict in a what-if did not show the two versions | Both shown beside the choice |
| L1 | A what-if fact read "Mara Venn member of Harbor Guild" | Phrased like every other fact |
| I1 | The timeline icon was an unreadable squiggle at menu size | A line with three ticks |
| T7 | On a phone, timeline buttons stretched full width | Natural width |

Browser test `frontend/e2e/time-and-what-ifs.spec.ts` covers the by-hand path end to end with
accessibility checks; it found that picture links were keyed without their branch, which would
have stopped any story with a picture from starting a what-if (fixed in migration 0032).

## Exit criteria (roadmap, Phase 3)

| Criterion | State |
| --- | --- |
| Reference-world invariant suite passes | Partly: invariants are covered by unit, contract and database tests built for this phase; the Phase 0 reference world was not extended with branches, secrets and false beliefs |
| Branch-local changes never leak to siblings | Met: contract, database and browser tests |
| False beliefs can drive POV context without being treated as canon | Met for knowledge and the reader's guide; there is no generation context yet (Phase 4/5) |
| Flashback events preserve world chronology and narrative order | Met: scene time and outline order are separate; flashbacks are shown |
| Conflicting merges require explicit resolution | Met: bringing back refuses until every conflict is decided, then goes to review |
| An agent cannot finalize a conflict without human approval | Met: bringing back only ever makes a proposal |
| Lies, mistakes, dreams and unreliable narration stay distinct from facts | Partly: lies and mistakes are beliefs; dreams and unreliable narration have no record of their own |
| Reader/character knowledge support reveal and irony checks | Met (by hand) |
| Spoiler-safe exports exclude later and inaccessible truth | Met for the reader's guide, with leakage cases in contract tests |
