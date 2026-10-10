# Assistant-first experience and the learning curve

- Status: Proposed revision (2026-10-10). The principles are the owner's direction; the specific
  mechanisms, levels, and sequencing below are the implementer's proposal for approval (ADR-017).
- Supersedes where they differ: the release-one workflow in `01-product-and-scope.md`, the
  information architecture in `06-api-and-frontend.md`, and the ordering of phases 2.5–5 in
  `09-delivery-roadmap.md`. Those documents have been revised to match.

## Why this document exists

The plan through Phase 2 built a trustworthy canon and then exposed it more or less as it is
stored: entities, predicates, mutation sets, revisions, schema versions. Each later phase adds
another expert concept (branches, world time, point of view, invariants, context manifests). An
audit of the plan on 2026-10-10 found that almost every capability was specified as a data model
plus a manual editor, with AI appearing only as a prose drafter, an extractor, and a critic. The
conversational planner was listed under "later options".

That produces a tool whose correctness is high and whose first hour is hard. The owner's direction
is the opposite order of priority for the author's experience:

1. **Writing must never be blocked by the tool's learning curve.** A new author can start
   building a world and a story within minutes, with no vocabulary to learn first.
2. **Everything can be done by talking to an assistant.** Every capability has a path where an AI
   agent does the work through conversation or human-in-the-loop iteration. The manual and
   advanced controls remain, as the alternative rather than the way in.
3. **Advanced features are kept and revealed over time.** Nothing is removed. Authors grow into
   branches, rules, custom types, and audits when their story needs them.
4. **Creation starts from description and interview**, for world-building and for the story plan
   itself, not from forms and not only from prose that already exists.

One rule from the existing plan does not change, and the rest of this document depends on it:

> **The assistant never writes canon.** Everything it produces is a proposal a person reviews.
> (ADR-012, ADR-014.) Assistant-first means the assistant does the *work*; the author keeps the
> *decision*.

## The interaction model

Every capability follows one loop, so that learning it once is learning the product:

```text
Describe  ->  Interview (optional)  ->  Draft  ->  Review  ->  Canon
   ^                                                   |
   +----------------- follow-ups / to-do --------------+
```

- **Describe.** The author says what they want in their own words: a character, a place, a whole
  premise, a chapter idea, "what if the guild betrayed her". Type, name, and structure are
  optional hints, never required input.
- **Interview.** The assistant asks a small number of specific questions that the description
  leaves open, phrased in the story's own terms and naming existing entries. Every question is
  skippable, and the whole step is skippable.
- **Draft.** The assistant turns the description and answers into a typed proposal: entries,
  details, links to what already exists, plan structure, or prose, depending on the task.
- **Review.** The author sees the proposal as plain sentences with before-and-after, keeps or
  skips each part, and approves. Approval is the only thing that changes canon.
- **Follow-ups.** Whatever the proposal introduced but did not finish becomes a visible to-do
  (an entry that is only a name, a beat with no scene, a thread with no payoff). Each to-do
  reopens the same loop on that item.

### Rules for the loop

- **No provider, no dead end.** Every step has a by-hand equivalent reachable in one action, and
  what was typed carries over. A project is fully usable with no AI provider configured.
- **The assistant states what it did not do.** Anything it could not express as a valid change is
  listed with the reason, never silently dropped or coerced (already true of assisted creation).
- **It links before it creates.** A name that matches an existing entry is linked, not duplicated.
  Today this is name and alias matching within a type; semantic candidates are added in Phase 2.5
  as *suggestions the author confirms*, never as automatic merges.
- **Bounded questions.** Five to eight questions per round by default; the author can ask for
  more. An interview is never a wall.
- **Cost is visible before it is spent.** Any step that calls a model says so, shows which key or
  local model pays for it, and respects the author's limits. Deterministic steps never call one.
- **Conversations are kept.** The description, questions, answers, and resulting proposal are
  stored together as the evidence for what was accepted, and can be resumed. (Today the session
  is evidence-referenced but not resumable; persistence is Phase 2.5 work.)
- **Review must scale.** A proposal with forty changes is reviewed by group ("12 new entries, 20
  links, 8 details"), with safe bulk actions, not forty separate clicks. (Phase 2.5.)

## Levels: how the product reveals itself

The product has five levels. A level is not a setting or a gate: every feature is always
reachable by search or from the assistant. A level describes what the interface *shows by
default* and what the assistant *offers unprompted*. A project moves up when the author uses a
feature of the next level, or says they want it.

| Level | The author is… | Shown by default | Introduced when |
| --- | --- | --- | --- |
| 1. Start | getting a story idea down | Describe box, the story bible as cards, the to-do list, review | Always |
| 2. Shape | filling in and connecting a world | Entry pages, facts as sentences, what characters believe, search, history as "versions" | The project has a handful of entries |
| 3. Plan | turning the world into a story | Story plan (arcs, beats, scenes) built by interview, threads and payoffs, readiness | The author asks to plan, or opens the plan |
| 4. Write | drafting and revising scenes | Scene studio, context the assistant will use, consistency findings as margin notes | The first scene is planned |
| 5. Control | running a long, complex book | Timelines and world time, points of view and secrets, branches ("what-ifs"), custom types, story rules, audits, exports, the raw record | The author reaches for one, or the assistant suggests one for a concrete problem |

Principles for moving between levels:

- **Suggested by need, never by tour.** The assistant offers a Level 5 feature when the story
  shows the problem it solves ("these two scenes disagree about where Mara is — want to track
  where people are over time?"), in one sentence, with a way to decline for good.
- **Plain words first, the real term second.** The interface says "version" and shows "revision"
  in technical details; says "what-if" and teaches "branch"; says "story rule" and teaches
  "invariant". `frontend/src/lib/words.ts` is the single mapping and must cover every new concept.
- **Safe defaults make advanced features optional.** One timeline, one branch, the author's own
  point of view, warning-only rules, and the starter types are the defaults. Nothing in levels
  1–4 requires choosing a branch, a tick, a POV, or a schema.
- **Nothing is hidden from a search.** The command palette and the assistant reach every feature
  at every level.
- **The author can turn the guidance down.** A per-author setting reduces unprompted suggestions;
  it does not remove features.

## Capability map

Every capability in the plan, with its assistant path, its by-hand path, the level at which it
appears by default, and where it is built. "Assistant path" is the Describe → Interview → Draft →
Review loop applied to that capability. A capability without both paths is not done (see
"Definition of done").

| Capability | Assistant path | By-hand path | Level | Phase |
| --- | --- | --- | --- | --- |
| Add entries (people, places, things) | Describe one or many; assistant drafts entries, details, links | Entry form | 1 | **Built** (2026-10-10) |
| Fill in an entry | Interview about that entry; to-do list of bare entries | Edit dialog | 1 | **Built** |
| Connect entries | Drafted with entries; link suggestions for existing ones | Sentence builder | 2 | Built by name; semantic suggestions 2.5 |
| What a character believes | Asked in interviews; drafted as beliefs, compared with canon | Belief editor (not built) | 2 | Assistant built; editor 2.5 |
| Review and approve | Grouped summary, "keep all safe changes", ask the assistant to revise a change | Per-change keep/skip/edit | 1 | Per-change built; grouping and revise-by-asking 2.5 |
| Undo and history | "Take the bible back to before the guild changes" → previewed restore | Version list, go back | 2 | By hand built; by asking 2.5 |
| Start from a premise | One conversation produces a starter world and a to-do list | — | 1 | 2.5 |
| Sample project and first-run | Guided first session inside a real project | Skippable | 1 | 2.5 (pulled from Phase 7) |
| Story plan: arcs, beats, scenes | Planning interview produces a plan proposal; "what happens next?" extends it | Outline editor | 3 | 2.6 (pulled from Phase 5) |
| Threads, promises, payoffs | Assistant notices setups without payoffs and asks | Thread list | 3 | 2.6 |
| Scene plan and readiness | "Plan the scene where…" → participants, place, intent, expected changes | Scene plan form | 3 | 2.6 |
| Paste or import prose | Assistant reads it and proposes entries, facts, events with evidence | Mark entities by hand | 2 | 2.7 (was 2.5) |
| Names and nicknames | Assistant asks "is 'the Fox' Mara?" once and remembers | Alias editor | 2 | 2.7 |
| Story rules (invariants) | "Nobody dead may speak" → rule drafted, shown with examples it would catch | Rule builder | 5 | 2.7 warn-only; blocking later |
| World time and timelines | "This happened three years before the war" → dated; assistant asks only when order matters | Timeline editor | 5 | 3 |
| Points of view and secrets | "Tobin doesn't know this" → visibility proposal | Knowledge editor | 5 | 3 |
| What-ifs (branches) | "What if Mara refused?" → a what-if with its consequences proposed; compare and keep | Branch tools | 5 | 3 |
| What the assistant will use (context) | Explains its sources in a sentence each; "don't use that" | Pin and exclude controls | 4 | 4 |
| Media references | "This is Mara's portrait" → link and scope proposed | Media library | 4 | 4 |
| Draft a scene | From the scene plan, with the context shown first | Write it yourself | 4 | 5 |
| Consistency findings | Margin notes with evidence; "fix it" proposes a prose or canon change | Findings list | 4 | 5 |
| Canon changes from a draft | Extracted, grouped, reviewed like any proposal | Mark by hand | 4 | 6 |
| Custom types and kinds of fact | "I need to track ships" → type and fields proposed | Types and fields forms | 5 | By hand built; by asking 2.5 |
| Audits | "Check act two for contradictions" → scoped run with a spend cap | Run configuration | 5 | 7 |
| Exports and reader bibles | "A spoiler-free character sheet up to chapter 10" | Export options | 5 | 3 and 7 |
| Collaborators | "Invite Sam as an editor" → invitation drafted for confirmation | Members page (not built) | 5 | 2.5 |

## Story planning by conversation

The original design intended a complete story plan to come out of conversation and interview.
Nothing in the written plan specified it, and canon has no plan records yet. The revision:

- **Plan records are canon-governed, like everything else.** Story, arc, beat, and scene plan are
  typed records with history, proposals, and review (pulled forward from Phase 5 to Phase 2.6).
  They are *intent*, kept distinct from *fact*: a planned event is not an event that happened.
- **The planning interview** starts from whatever exists: a premise, a cast, or nothing. It works
  top-down and stops at any depth: premise and stakes → the main arcs and who they belong to →
  the turning points → beats → scenes. Each round ends in a proposal the author reviews.
- **It is grounded in the bible.** Questions and drafts name real entries; a beat that needs a
  place or a person that does not exist proposes a bare entry and adds it to the to-do list.
- **It leaves honest gaps.** An arc with no ending, a setup with no payoff, a scene with no point
  of view are shown as open to-dos, not filled with invention. The assistant may *offer* options;
  it does not decide the story.
- **"What happens next?"** extends the plan from where it stands, offering two or three
  directions with what each would change, for the author to pick, edit, or reject.
- **Structure is optional.** An author who does not outline can write scenes with no plan at all;
  the plan can be derived from accepted scenes later (Phase 6).

## Definition of done for any feature

From Phase 2.5 onward, a feature is not complete until:

1. **Assistant path:** it can be done through the Describe → Interview → Draft → Review loop, and
   the result is a proposal.
2. **By-hand path:** it can be done without a provider, reachable in one action from the
   assistant path, with input carried over.
3. **Level:** it has an assigned level, a plain-language name in `words.ts`, and a default that
   lets a Level 1 author ignore it entirely.
4. **Trigger:** if it is Level 5, there is a concrete story situation in which the assistant
   offers it, and a test for that trigger.
5. **Follow-ups:** anything it leaves unfinished appears on a to-do list.
6. **Review at scale:** its proposals group sensibly and support safe bulk decisions.
7. **Honesty:** cost is shown before model calls; what could not be done is reported; nothing is
   presented as checked or complete when it was not.
8. **Accessibility and keyboard:** as today.

## Autonomy

The existing plan lets the author delegate low-risk writes. Under this revision autonomy is a
dial the author turns up as trust grows, not a setting to understand on day one:

- **Default: review everything.** Every proposal waits for the author.
- **Later: "keep the safe ones for me."** Additive, reversible, low-impact changes of kinds the
  author has approved before may be applied and reported; destructive, bulk, conflicting,
  schema-changing, and secret-revealing changes always wait (ADR-014 unchanged).
- The assistant offers this only after the author has approved the same kind of change several
  times, and every auto-applied change is listed and reversible in one action.

## Measures

The learning curve is tested, not asserted. Targets for the hosted product with a provider
connected, measured on new accounts with the reference fixtures:

- First accepted entry within 3 minutes of first sign-in, with no documentation.
- A premise of one paragraph becomes at least five linked entries, reviewed and accepted, within
  10 minutes.
- A first story plan (arcs and at least six beats) accepted within 30 minutes of starting to
  plan.
- No Level 1–3 screen shows an internal term (entity, predicate, mutation, revision, schema,
  projection, invariant, tick) outside "technical details"; enforced by a test over rendered
  screens.
- Every capability-map row has an automated test of its assistant path with a scripted model and
  of its by-hand path with no provider.
- Draft quality is measured against a labelled set: precision and recall of drafted entries and
  links, duplicate rate, and the share of drafted changes authors keep. A drop is a release
  blocker for the assistant, not for the by-hand path.

## What this revision does not change

- PostgreSQL-authoritative canon, the immutable journal, proposals, review, and the high-impact
  gate (ADR-012, ADR-014).
- Built-in integrity checks are blocking and cannot be suppressed (ADR-013).
- Bring-your-own-key and local models; no platform-funded inference.
- Invitation-only hosting.
- The advanced capabilities of Phases 3–7. Their order of *construction* is unchanged except
  where noted in the roadmap; their order of *exposure to the author* is what this document sets.

## Known risks

- **Model quality is now on the critical path for first impressions.** Mitigation: the by-hand
  path always works; the labelled evaluation set; a recommended default model per provider.
- **As of 2026-10-10 the assistant has only been exercised with a scripted model.** The first
  real-provider runs are the owner's; prompt and schema tuning follow from them.
- **Interview fatigue.** Mitigation: bounded rounds, every question skippable, "draft it now".
- **Proposal overload.** Mitigation: grouped review and safe bulk actions are scheduled before
  premise-scale drafting is promoted.
- **Assistant paths can drift from by-hand paths.** Mitigation: both go through the same
  operations and validators; the definition of done requires tests of both.
- **Scope.** Requiring two paths for every feature raises the cost of each. The roadmap absorbs
  this by building each capability's assistant path on the shared loop rather than per feature.
