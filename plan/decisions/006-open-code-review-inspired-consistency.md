# ADR-006: OpenCodeReview-inspired prose review harness

- Status: Proposed
- Date: 2026-10-05
- Owners: project owner, consistency-system implementer
- Related backlog: CON-007–CON-013

## Context

Long manuscripts exceed practical model context, while reviewing isolated passages misses distant continuity dependencies. Alibaba OpenCodeReview demonstrates a hybrid approach: deterministic selection/rule dispatch surrounds bounded review agents, groups receive isolated context, coverage and token guards provide fallbacks, findings are anchored, and an independent reflection stage removes unsupported comments.

Its implementation is specific to Git diffs and source files. Its semantic grouping also uses an LLM call, so it is not itself purely deterministic.

## Proposed decision

Adopt the architectural pattern but implement a Novel Engine-native harness:

- derive review units from structured prose/version and canon deltas
- build a deterministic dependency graph from entities, facts, time, POV, rules, and narrative threads
- create overlapping rule-specific bundles with token caps and guaranteed coverage
- use narrow read-only review agents only when requested/policy-enabled
- anchor findings to immutable prose versions and structured ranges
- independently falsify model findings against minimal cited evidence
- fall back to deterministic/single-unit bundles on grouping or provider failure

The detailed algorithm is in `plan/16-open-code-review-adaptation.md`.

## Alternatives considered

### One full-manuscript critic

Simple but expensive, difficult to ground, hard to parallelize, and eventually exceeds context limits.

### Independent per-scene critics

Cheap and parallel, but misses distant entity, thread, causal, secret, and timeline dependencies.

### Pure vector retrieval per finding

Useful for candidates but cannot guarantee rule or changed-unit coverage and is difficult to audit.

## Consequences

### Positive

- Predictable coverage and spend
- More relevant cross-scene evidence per prompt
- Parallelizable and incrementally cacheable
- Clear preview and fallback behavior
- Independent filtering reduces self-confirming hallucinations

### Negative

- Review-unit extraction and dependency tracking are substantial new infrastructure
- Overlapping bundles may duplicate work
- Structured prose anchoring and relocation are harder than code line anchoring
- Deterministic coverage is only as good as extraction/schema dependencies

### Follow-up work

- Add CON-013 grouping prototype and benchmark
- Define review-unit and bundle schemas
- Extend the reference world with labeled cross-scene defects
- Evaluate full-context, vector-only, and deterministic-bundle approaches

## Validation

Accept this ADR after the offline benchmark shows complete mandatory-rule coverage and a meaningful precision/cost improvement without unacceptable recall loss. Record results and thresholds in the ADR.

## Amendment 2026-10-20: the deterministic bundle prototype and its first benchmark (CON-013)

Status of this ADR: still **proposed**. The prototype exists and has been benchmarked without a
model; the acceptance test above (precision and cost against a real critic) has not been run.

What was built (`backend/domain/review_bundles.py`, policy `review-bundles/1`): a pure function of
the story, no storage, model or vector index.

- **Review units** are a scene's paragraphs, cut further at sentence ends and then between words
  when longer than 400 tokens. A unit keeps offsets, a digest and the entries named in it (the
  application's own name-finding), never the words.
- **Anchors** say which checks a unit needs: `timeline`, `state`, `resource`, `pov`, `reveal`,
  `thread`, `rule` and `scene`, from the entries it names, the scene's viewpoint, and what the plan
  says the scene reveals, moves along, or must satisfy. Records take anchors from the story graph
  (`fact_anchors`: where something is goes to its timeline, who has what to the object, a
  condition to the subject's state, a fact of a kind a rule reads to that rule; a belief to its
  holder's viewpoint; a secret to the viewpoint of whoever lacks it).
- **Groups** are one per anchor, merged when one kind of check covers the same (or 80% the same)
  units and still fits. A group over its budget is **split, never skipped**: units in reading
  order, keeping a scene whole where it fits; when records take more than half the budget they
  are split too, every records part meeting every units part. A split is a warning.
- **Coverage** lists, for each unit, every check it needs and the groups that carry it. A unit no
  check claims is read on its own with the records about what it names; without the story graph
  units are read in order with no records and the plan says its coverage is incomplete; a cap on
  groups is never silent. A **preview** says in plain words what would be read, and how many
  secrets are shown (always labelled for the author only).

Benchmark (`tests/eval/consistency/bundles.py`, pinned in `bundles_baseline.json`, `make eval`):
an original six-scene manuscript (20 passages, 19 records) with eight labelled defects, two of them
across scenes. A strategy *covers* a defect when one group shows all its passages and every record
needed to see it. The vector-only baseline ranks records by word overlap with the passage, a
deterministic stand-in for an embedding index.

| Budget a group | Strategy | Groups | Tokens (passages + records) | Defects shown |
|---|---|---|---|---|
| 1,000 | deterministic | 20 | 15,856 (3,856) | 7/8 |
| 1,000 | full context | 8 | 7,068 (2,268) | 6/8 |
| 1,000 | vector only (k=4) | 20 | 13,575 (1,575) | 5/8 |
| 20,000 | deterministic | 17 | 13,990 (3,790) | 7/8 |
| 20,000 | full context | 1 | 1,492 (892) | 8/8 |
| 20,000 | vector only (k=4) | 20 | 13,575 (1,575) | 5/8 |

What it shows, plainly:

- Deterministic grouping covers the same defects at every budget, and more than nearest-records
  bundling, which never shows two passages together and so cannot see a cross-scene
  contradiction (the sextant cracked in scene 1 and flawless in scene 5).
- Once the story does not fit one prompt, full context loses exactly the cross-scene defects its
  split separates. When it does fit, it shows everything at a quarter of the tokens: on a story
  this small, grouping costs more, because each passage is read by about five checks and each
  group carries its own instructions (600 tokens assumed). The case for grouping is long stories
  and focused prompts (long-context models do worst on what sits in the middle of a long input:
  Liu et al., *Lost in the Middle*, TACL 12, 2024), not cost on short ones.
- The one defect the deterministic plan misses is a paragraph naming no one ("He kept the lamp
  burning…") in a scene with no viewpoint: it is read alone, without the record about who "he" is.
  In a scene with a viewpoint the same paragraph is covered through the viewpoint's checks.
- Full context shows every secret to every group; grouping shows a secret only to the viewpoint
  checks that need it.

Not measured, and needed before this ADR can be accepted: whether a critic shown a defect finds
it, how many false findings each strategy produces, and the real token cost per finding. Those
need the owner's real model on the same manuscript. Thresholds are not set until then.

Sources: Liu et al., "Lost in the Middle: How Language Models Use Long Contexts",
https://arxiv.org/abs/2307.03172 (TACL 12:157–173, 2024). Ranked-retrieval measures as in
Manning, Raghavan and Schütze, *Introduction to Information Retrieval*, ch. 8,
https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html.

## Amendment 2026-10-20 (batch 7): checks worked out only where inputs changed, what becomes of a finding, and groups in the continuity pass (CON-007, CON-001, CON-013, CON-016)

Status of this ADR: still **proposed** (the critic-side measurement above has still not been run).
What follows is built and tested against the scripted model and the in-memory and PostgreSQL
stores only.

**Fingerprints and a cache (CON-007).** The studio's checks that need no model are split into
named checks, each declaring what it reads (`backend/domain/check_fingerprints.py`). Every input is
a SHA-256 digest (the words exactly, normalised to one Unicode form; the names as the scene reads
them; each slice of the brief a check reads; for continuity, the line of the story and its version
with the clock, travel table and the scene's card). A check's fingerprint is a digest of the
version of the checks, its name and its inputs' digests, and its result is kept against it
(`check_cache`). This is a *constructive trace* in the terms of Mokhov, Mitchell and Peyton Jones:
the result is stored with the hashes of what produced it, so a check with the same fingerprint is
taken from the store, not worked out again, and exactly the checks whose inputs changed run.
Two choices worth recording:

- The brief (what the story bible says about the scene) is read every time and fingerprinted, not
  cached by the story's version. Other parts of the app add to the brief (this batch's checks
  against the prose read pictures and plan scales that do not move the story's version); a cache
  keyed by version would use a result for inputs it did not see. Reading costs the story bible
  read; it never costs a wrong answer.
- Any finding no named check claims belongs to a catch-all that reads everything, so a check added
  to `drafting.py` without being listed here is worked out whenever anything changes, never lost.

**What becomes of a finding (CON-001).** plan/13 asks for open, accepted, dismissed, fixed and
superseded. *Accepted* follows code-quality tools that separate "the finding is right and I am
leaving it" from "the finding is wrong": SonarQube calls the first **Accept** (it replaced "Won't
fix") and keeps it apart from **False positive**. Here "Intended" is accepted and "Not a problem"
(or the author's own reason) is dismissed, so the two never mix in the calibration note.
*Superseded* is an open finding a later check of the same scene found again, so the history points
at the newest record rather than repeating the same note down the page. Runs record the story's
checking profile, are carried by the archive, and are copied into a what-if.

**A dismissal depends on more than its sentence (CON-016).** A check's dismissal keeps a digest of
what the finding said. Its message is built from the facts, times, places and people it read, so a
change that matters to it changes the message, and the dismissal no longer holds; plan/13's "a
changed dependency or rule version marks it stale". On the review screen an advisory finding is
dismissed for one proposal, keyed by the change's revision and content and the version of the rule
it cites. Blocking findings and integrity checks are never offered (ADR-013).

**Calibration as a note, not a policy.** How often each check's findings are dismissed is shown
per check, with a rate only after five findings. Google's Tricorder made the same signal (developers
marking a result "not useful") the measure of an analyser's effective false-positive rate, and
analysers that stayed above about 10% were fixed or switched off by their owners. Here the author
is the owner: the note says a check is often wrong for this story, and nothing changes unless the
author changes it (plan/13: threshold calibration is an explicit choice, never silent learning).

**Groups in the continuity pass (CON-013).** The continuity pass is sent in groups when its single
prompt would be over 6,000 tokens, at most 12 groups; otherwise, or when a grouping would leave a
passage without a check it needs, it is sent whole and the job says why. Two departures from the
prototype: the scene's viewpoint is not given to every passage (what the viewpoint character
knows is the viewpoint pass's question), and records about the scene as a whole (rules, the plan,
the author's own writing) go with every passage in one group, so no record is left unread. The
grouping happens before the estimate, so the author confirms the groups' cost. Grouping usually
costs more tokens than one whole call on a story that fits (the benchmark above); the case for it
remains long scenes against large stories and focused prompts.

Sources: Mokhov, Mitchell and Peyton Jones, "Build Systems à la Carte", ICFP 2018,
https://www.microsoft.com/en-us/research/publication/build-systems-a-la-carte/ (and the JFP 2020
version, https://www.microsoft.com/en-us/research/publication/build-systems-a-la-carte-theory-and-practice/).
SonarQube issue statuses (Accepted, False positive),
https://docs.sonarsource.com/sonarqube-server/2025.3/user-guide/issues/solution-overview and
https://docs.sonarsource.com/sonarqube-for-ide/visual-studio/using/fixing-issues. Sadowski et al.,
"Tricorder: Building a Program Analysis Ecosystem", ICSE 2015,
https://research.google/pubs/tricorder-building-a-program-analysis-ecosystem/.
