# Phase 3 implementation todo: time, what-ifs and knowledge

- Status: In progress (started 2026-10-17).
- Scope: `09-delivery-roadmap.md` Phase 3; backlog `TMP-001`–`TMP-005`, `KNW-001`–`KNW-006`,
  `QRY-001`, `QRY-002`, and the record-level rebasing left over from Phase 2.
- Governing documents: `03-domain-and-temporal-model.md`, `18-author-rules-canon-queries-and-derived-views.md`,
  `19-assistant-first-experience.md` (all of this is Level 5), ADR-004, ADR-005, ADR-012,
  ADR-020 (written with this phase).

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
| A. Story time: values, ordering, facts true for a span | TMP-001, TMP-002 | Not started |
| B. What-ifs: start, work in, compare, bring back, set aside | TMP-003–TMP-005 | Not started |
| C. Who knows what: secrets, common knowledge, knowledge view | KNW-001–KNW-003 | Not started |
| D. Telling: speech that gives listeners beliefs | KNW-005, KNW-006 | Not started |
| E. What the reader learns, scene by scene; irony; reveals | KNW-004, STY-007 | Not started |
| F. Contradictions in time, one-at-a-time facts and knowledge | — | Not started |
| G. State at a time; spoiler-safe reader's guide | QRY-001, QRY-002 | Not started |
| H. Record-level rebasing of stale proposals | — | Not started |
| I. Assistant paths and Level 5 triggers | — | Not started |
| J. Screens, checked in a browser against the design rules | — | Not started |
