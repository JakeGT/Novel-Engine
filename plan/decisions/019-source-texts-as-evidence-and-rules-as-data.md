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
  *Amended 2026-10-18 (CTX-016):* an answer can now also hold only in one what-if
  (`branch_id`), from a scene on and/or until a scene in reading order (`from_scene_id`,
  `until_scene_id`, both included), or in scenes seen by one character (`viewpoint_id`)
  (migration 0040). The limits sit on the answer, like qualifiers on a statement; a null
  limit means everywhere, so old answers mean what they meant. Where a phrase is read is a
  `NamePlace` (text, line of the story, scene, its viewpoint characters, reading order);
  the answers that apply are folded from least to most specific (each limit counts once,
  the later answer breaks a tie), so the narrowest decides. Time is reading order, not world
  time: authors say "from this scene on". Inside a what-if a new answer stays there unless
  the author says otherwise; `alias_bindings` is not forked, a what-if's answers simply carry
  its id. A scene that leaves the plan makes an answer limited by it stop applying. Each
  answer keeps the passages the author confirmed (`examples`: the words, where, and whether
  the phrase means the entry there), and the names page says when today's answers would read
  one differently. Every reader of names uses the same resolution for its place: a text's
  reading, scene reading back, the plan card, the studio's checks (which also count a phrase
  meaning the viewpoint, place or a secret's people), and the scene's context, which reads
  each passage again in the working line when any answer is limited. Literary coreference
  tools still do poorly on novels and worse with length; projects that need precision use
  hand-made disambiguation rules, which is what these reviewed answers are.
- A rule sees entries and the facts true of them now. It cannot speak of time, order of
  events, belief or the wording of prose ("nobody dead may speak later"). The assistant is
  told to say so rather than bend such a sentence into something else.
  *Amended 2026-10-18 (CON-005, CAN-013):* the vocabulary grew, still as data and still one
  evaluator. A check may also be a range (`between`: every value of a kind of fact is a
  number from `low` to `high`; a quantity's number is compared, not its unit), an implication
  (`implies`: if it has `if_has` it must also have `pattern`), an incompatibility (`not_both`),
  or a belief (`believes`/`does_not_believe`: the entry holds a belief like `pattern` about
  `about_id` or itself; beliefs are read as data, right or wrong). A rule may name exceptions
  (`except_for`: entries, a tag, or facts that excuse an entry). And a rule says when it must
  hold (`time`): over every fact as recorded (the default and the old meaning), at every
  moment of story time (judged at one moment in each stretch where the entry's facts stay
  the same, from the edges of their spans, with times relative to events followed), or at
  the end of the story (only facts that have not stopped). The vocabulary was chosen after
  SHACL's core constraints (value ranges, `sh:not`, `sh:or`), kept as a flat list of checks
  rather than nested and/or/not so that every rule reads back as one sentence. Where story
  times are too loose to tell, the answer is "cannot tell", in strong (Kleene) three-valued
  logic: knowing the times better can only turn it into yes or no, so only a definite break
  may block; "cannot tell" is shown as "may not hold" and only warns. A body records the
  `form` of the language it needs (1 for the old vocabulary, 2 for any of the above), so a
  kept rule means what it said; a form newer than the code reads is refused. Beliefs and
  event times are read inside the commit (`current_beliefs`, `live_events` on the commit's
  reader) only when a rule needs them; a reader without them makes the result incomplete,
  so it warns. Moving an event's time re-checks every entry for rules in story time, since
  facts can be timed from events. Scene conditions share the evaluator but not belief
  checks, which they refuse (their worlds hold facts only). Still not expressible: the order
  of events, what happens after an event, numbers worked out from other facts, and the
  wording of prose; the assistant says so in `cannot_express` (`draft-rule` v2).
  *Further, 2026-10-18 (review):* beliefs have no time, so a belief check inside an
  every-moment rule gives the same answer at every moment; only the facts around it move.
  A start or end that is set but cannot be placed (timed from an event with no time, a
  missing event, or a loop) could be at any time: the fact may or may not hold at each
  moment, so it can make a rule "cannot tell" but never a definite break. A change that
  turns a break the story only may have into a sure one is blamed for it, and blocks if
  the rule blocks and the story was read whole. A drafted check that does not fit
  together is set aside and said in `cannot_express`; the rest of the draft is kept.
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
