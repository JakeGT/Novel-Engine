# ADR-019: An author's text is evidence, not canon; a story rule is data, not code

- Status: Accepted (2026-10-11)
- Context: Phase 2.7; ADR-013 (story invariants), ADR-015 (evidence),
  `19-assistant-first-experience.md` (text intake, names and nicknames, story rules).

## Decision

**Source texts.** Prose an author brings in is kept in its own tables (`source_texts`,
`source_versions`), outside canon. Each change to a text is a new version whose words can
never change (a database trigger refuses updates). A change to the story that came from a
text cites the version and a character range (`chars:120-180`), using the evidence pointer
canon already had. Nothing in a text is true in the story until the author keeps a change
that cites it.

**Finding names is deterministic.** Explicit marks (`@Mara`, `@"the Fox"`,
`[[the Old Mill]]`) and plain occurrences of known names and aliases are found with no
model. What the author decides about a phrase ("the Fox" is Mara; is not Tobin; is not a
name) is kept as name memory (`alias_bindings`), everywhere or for one text. A model's guess
about a phrase is stored only as a question and never resolves anything.

**A model's reading must quote.** When the assistant reads a passage, every fact and belief
it returns must carry words that are really in that passage, or it is left out. What
survives is an ordinary set of suggested changes to review. An item the passage implies
rather than states is marked so in its evidence pointer (`chars:120-180,implied`).

**Story rules are a fixed vocabulary.** A rule says which entries it is about (type, tag,
one entry, having certain facts) and what must hold (has, lacks, at most, at least over a
fact pattern). It is data validated against the project's own types and kinds of fact;
nothing supplied by an author or a model is executed. One evaluator answers for the story,
for a rule's examples, for a scene's before-and-after conditions, and for a proposed set of
changes. A rule's wording is versioned and earlier versions are kept.

**Rules only warn.** A change that would newly break a rule gets an advisory next to the
change that causes it. A rule the story already breaks is not blamed on an unrelated change.
Publishing a rule as blocking (ADR-013) is not offered in this phase.

## Why

- Keeping texts out of canon means a pasted chapter cannot become fact by being stored, and
  removing a text is the author's right: its words are erased while a title-only marker
  lets old evidence say which text it was.
- Immutable versions make an evidence pointer mean the same passage for ever.
- Deterministic name-finding gives the same answer every time, costs nothing, and can be
  measured against a labelled passage (`tests/fixtures/intake_eval`).
- Requiring a real quote turns "the model said so" into "the text says so, here".
- A fixed rule vocabulary is safe to accept from a model and cheap to explain back in words
  built from the rule itself, so the explanation cannot drift from what is checked.

## Consequences

- Name memory is scoped to the whole story or to one text. Scoping by world time, narrative
  position, branch or point of view waits for Phase 3, where those exist.
- A rule sees entries and the facts true of them now. It cannot speak of time, order of
  events, belief or the wording of prose ("nobody dead may speak later"). The assistant is
  told to say so rather than bend such a sentence into something else.
- Rule evaluation reads committed state through the repository, not through the commit's
  own transaction. That is sound while rules only warn; a blocking rule would need the
  evaluator moved onto the transaction's reader.
  *Amended 2026-10-17 (CON-015):* rules can now be set to block. The evaluator reads the
  story through the commit's reader (`CanonTxReader.active_entities`/`asserted_facts`), on
  the branch being committed, under the commit's lock. A result that could not see the
  whole story (a reader without those reads, or more than a rule may read) is incomplete
  and only warns. A blocking rule also refuses approval, so the author hears it first in
  review. Rewording never makes a rule block; a project archive carries the choice.
- Texts, name answers and rules are keyed by project, travel in a project archive
  (`writing`, `story_rules`), and are restored after canon. Removing a failed import does
  not remove texts or rules that import had already restored.
- Scene conditions are kept inside the scene's details under a reserved key, so they are
  reviewed, versioned and undone with the rest of the plan.
