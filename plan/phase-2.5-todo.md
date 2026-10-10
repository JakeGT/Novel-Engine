# Phase 2.5 implementation todo: assistant-led creation

- Status: Built (2026-10-10). What remains is listed under "Left for later phases" at the end,
  and the two exit criteria that only real use can meet.
- Scope: `09-delivery-roadmap.md` Phase 2.5; backlog `AST-EXP-001`–`AST-EXP-018`.
- Governing documents: [`19-assistant-first-experience.md`](19-assistant-first-experience.md)
  (interaction loop, levels, definition of done), ADR-017, [`20-design-direction.md`](20-design-direction.md).
- Rule for every item: an assistant path and a by-hand path, both tested; the assistant only ever
  produces a proposal.

**Standing limitation:** every assistant workflow below is tested with a scripted model, plus a
check that its output format converts to a strict JSON schema. None has been run against a real
provider by the implementer. The owner runs the real model by hand and reports back over time
(decided 2026-10-10); that feedback, not a separate evaluation gate, is how drafts are tuned.
Each item says "scripted model only" where no real run has confirmed it.

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Assisted creation and fill-in | AST-EXP-001, 002 | Implemented (scripted model only) |
| B. Grouped review | AST-EXP-006 | Implemented (revise-by-asking: scripted model only) |
| C. The God Hand | AST-EXP-017 | Implemented, first version (scripted model only) |
| D. Real-provider verification and evaluation set | AST-EXP-003 | Closed by owner decision (2026-10-10): verified by the owner's manual runs over time; no evaluation set or gate was built |
| E. Stored, resumable conversations | AST-EXP-004 | Implemented (scripted model only) |
| F. Start from a premise | AST-EXP-005 | Covered by A in practice; no dedicated first-run |
| G. Link suggestions | AST-EXP-007 | Implemented by asking the model (scripted model only); no embeddings; precision not measured. Lists also show each entry's connection to the main entries, derived from facts |
| H. First run and sample project | AST-EXP-008 | Onboarding and sample story implemented; time to first entry not measured |
| I. Levels and plain-language test | AST-EXP-009 | Levels 1–2 in the menu; plain-language check on every tested screen; need-based suggestions not built |
| J. By-asking paths for existing features; belief editor; members | AST-EXP-010 | Implemented: asking leads to a previewed restore, a pre-filled invitation, or setup suggestions to add; the author confirms each (scripted model only) |
| K. Session budgets and assistant threat cases | AST-EXP-011 | Daily per-person allowance; what a request will read and what is left are shown before it is sent; threat cases are tests |
| L. Pictures on entries | AST-EXP-018 | Implemented for entries; scenes wait for Phase 2.6 |
| M. Design direction | AST-EXP-016 | Implemented; awaiting more owner feedback |
| N. Intuitive design rules and review | — | Rules adopted (document 21); first review applied; not yet tested with people |
| O. Providers and model choice | — | Implemented: seven providers, model chosen per workspace; no provider verified with a real call |
| P. Setup suggestions from the assistant | AST-EXP-010 (part) | Implemented: types and kinds of fact; an owner adds them; not yet verified with a real model |

## A. Assisted creation and fill-in

- [x] Describe one thing or a whole idea → optional questions → one proposal of entries, details,
      and links (`canon_creation_workflow.py`, `POST /assist/questions`, `POST /assist/proposals`).
- [x] Existing entries are linked by normalized name or alias within a type, never recreated.
- [x] Mentioned-but-unknown things become bare entries; `GET /entities/needs-detail` derives the
      to-fill-in list; the same assistant fills in any existing entry.
- [x] "New entry" opens the assistant; "Fill it in by hand" is one action away and keeps what was
      typed; the no-provider case falls back cleanly (`AssistFlow.tsx`).
- [x] Output format is provider-safe (strict JSON schema test).
- [x] Run against a real provider: done by the owner by hand, repeatedly (workstream D).
- [x] A new value for a one-at-a-time fact replaces the current one (ended, then added); a value
      already recorded is not added again; two values in one draft keep the first. Found by the
      owner's real run, where a second "located at" made the whole set unsaveable.

## B. Grouped review

- [x] A proposal is shown in groups by kind, in the order it will be applied, with a one-line
      summary and a kept count per group.
- [x] "Keep all" and "Skip all" per group.
- [x] What the assistant understood, and what else the change affects, are shown above the
      changes when arriving from the assistant.
- [x] Bulk decisions in one request, recorded in one transaction: all or none
      (`POST /mutations/{id}/operations/decisions`).
- [x] Edit a change with a form: entries with the usual fields, facts and beliefs as the sentence
      they read as. Editing the stored form remains under "More" for other kinds.
- [x] Revise the changes by asking (`POST /assist/revisions`): the assistant edits or skips
      changes already in the set; each edit is a recorded revision with the author's words as its
      reason; it cannot add a change. **Scripted model only.**
- [x] A set of changes is checked against the story when it is opened and after each decision:
      a likely duplicate under another type, an archived twin, a name two changes share, a fact
      already recorded. Advisory, model-free, worded with names (`canon_consistency.py`).
- [x] Committing announces itself in a dialog with the next step, instead of a notice out of view.
- [ ] The check does not judge meaning: two facts that contradict each other in sense, but not in
      structure, are not noticed. That needs a model and is not built.
- [x] Undo an accepted proposal in one step from the review screen, while it is still the latest
      version; after later changes the author is pointed to History.

## C. The God Hand

- [x] One instruction → every implied change of any kind in one proposal: new entries, renames,
      added and removed names and details, facts added and ended, beliefs added and ended, entries
      archived (`canon_change_workflow.py`, `POST /assist/changes`).
- [x] It sees the whole story bible by id (up to 200 entries, 300 facts, 200 beliefs) and the entry
      the author is looking at.
- [x] Ambiguous instructions return questions and propose nothing.
- [x] Deterministic consequences: a single-valued fact's old value is ended when a new one is
      added; archiving reports how many current facts will still mention the entry; the model's
      own notes are passed through as notes, never acted on.
- [x] Unresolvable ids and no-op changes are discarded with reasons; an instruction that changes
      nothing is reported plainly.
- [x] Available from the top bar, the search palette, and each entry page (`GodHand.tsx`).
- [ ] Merge as a first-class operation (today the model composes it from move-and-archive).
- [ ] Events, plan records, types, and kinds of fact (today: entries, facts, beliefs only).
- [ ] Projects larger than the context limits (retrieval instead of listing everything).
- [ ] A persistent conversation instead of a dialog; follow-up instructions on the same proposal.
- [ ] A labelled instruction set with expected operations (workstream D).

## J. People (members and invitations)

- [x] A People page for hosted workspaces: members by name, role changes and removal by an owner,
      never stranding the workspace without an owner (`PeoplePage.tsx`).
- [x] Invitation links: an owner creates one (optionally tied to an email), it is shown once, and
      waiting invitations can be cancelled. Novel Engine sends no email; the owner sends the link.
- [x] `/invite/<token>` accepts the invitation for a signed-in person; a signed-out person is sent
      to sign in and returned to the link afterwards (`InvitePage.tsx`).
- [x] Members are listed with a display name; email addresses are returned only to members who
      can invite.
- [x] A by-hand belief editor on every character's page: who is how sure that what is true of
      whom; a belief can be ended, with Undo (`BeliefsPanel.tsx`).
- [x] Asked for something that is not a story change (undo, invitations, setup, the AI key), the
      God Hand says where it is done and links there. **Scripted model only.**
- [x] Asked to undo or go back, it works out the version from the recent history (counts,
      times and origins of the last 20 versions) and the author is shown exactly what going back
      would change, in the usual restore preview, before deciding. Asked to invite someone, the
      People page opens with the role and address filled in. Setup suggestions that come with no
      change can be added from the dialog. **Scripted model only.**
- [ ] The history the model sees does not say *what* each version changed, so "before the guild
      changes" can only be answered by asking which version is meant.

## L. Pictures on entries

- [x] Upload an image to an entry (`POST /entities/{id}/pictures`, the image as the request body);
      PNG, JPEG, and WebP only, decided from the file's own bytes; 8 MB limit; any editor may add.
- [x] Stored as an immutable media version and linked to the entry through the existing reviewed
      link operation, as one new revision; removal retires the link and keeps the history.
- [x] Served only to project members (`GET /assets/{id}/versions/{vid}/content`), sandboxed and
      privately cacheable.
- [x] The newest picture is the entry's portrait: shown in the entry header and in the story bible
      index; all pictures appear on the entry page (`Pictures.tsx`).
- [x] Works with no AI provider; exercised in the browser end-to-end test with a real upload.
- [x] Choosing which picture is the main one; reusing a picture already on another entry.
- [ ] A picture that is in the media library but linked to nothing cannot be chosen from an
      entry; captions.
- [ ] Pictures on scenes (needs scene plans, Phase 2.6).
- [ ] A refused link after a stale head leaves the stored image unlinked in the media list; it is
      harmless but should be cleaned up or offered for re-linking.
- [ ] No thumbnails: the full image is fetched wherever a portrait is shown.

## E. Stored, resumable conversations

- [x] Each conversation is kept (`assist_sessions`, migration 0024): the description or
      instruction, the questions asked, the answers, and the proposal it led to. The id is the
      session id the proposal's operations already cite as evidence.
- [x] Answers are saved as the author leaves each box, and before the model is asked to draft, so
      a failed draft or a closed dialog loses nothing.
- [x] "Pick up where you left off" on the project home lists the author's own unfinished
      conversations; Continue reopens one at its questions without calling the model again.
- [x] A God Hand instruction that was answered with a question stays open and resumable.
- [x] "Where this came from" on a set of suggested changes shows the conversation behind it, to
      anyone who can read the project.
- [x] An unfinished conversation is visible only to its author; putting one away erases it;
      conversations are deleted with their project or workspace. Evidence cannot be edited.
- [x] Follow-up instructions on the same proposal: revise by asking (see B). The request is kept
      as the reason on each revision, not as a turn in the stored conversation.
- [x] An unfinished conversation left untouched for 30 days is erased.
- [ ] Conversations are not included in a project export.

## P. Setup suggestions and the first real run (2026-10-10)

The owner ran assisted fill-in against a real model for the first time. It worked end to end.
What it showed, and what was done:

- [x] Reading a kept conversation failed on the live site ("CSRF token missing"): the read
      endpoints wrongly required the write token, and the test client sends it with every request
      so no test noticed. Fixed, with a test that reads without the token.
- [x] The before/after table's "Now" heading broke into one letter per line, and changed details
      arrived as one run-on line. Fixed: one row per detail.
- [x] A person mentioned only by description ("a professor") was not given an entry. The prompt
      now asks for an entry named by the description. **Unverified until the next real run.**
- [x] The model invented a kind of fact (`EXPERIENCES_WORLD_THROUGH`), which was discarded. The
      assistant is now told to describe what the setup is missing instead; suggestions are cleaned,
      kept with the conversation, shown on the review screen, added by an owner through the normal
      additive setup path, and the same words can then be drafted again.
- [x] The owner was signed out in the middle of an interview and lost the page. Cause: after
      an hour the session id is replaced on the next request; the replacement changed the write
      token, so when that request was a save it was refused, the new cookies were dropped with the
      error, and the old session had already been revoked. Fixed at the source (the write token
      carries over; the old id works for 60 more seconds; new cookies are delivered on errors
      too) and made harmless in general (rule C9 in document 21: keep-alive while the site is
      open; a refused request waits for sign-in in another tab and is then sent again).
- [x] Suggestions that come with no change are named in the "Change anything" dialog, with a
      link to Types and fields.
- [x] Suggestions can be added from inside that dialog.
- [ ] Deliberately not built: suggested *fields* on a type. A type with fixed fields accepts only
      those details, so the assistant's own free-form details for it would be refused. Fields stay
      a by-hand decision under Types and fields. Decimal and choice-list kinds of fact are not
      suggested either, because drafting cannot fill them in yet.

## Q. Finding and ranking entries; review page layout (2026-10-10)

Done at the owner's request, following `~/Projects/Common/rules.md`:

- [x] Entries carry tags, an importance (main, supporting, minor, background), and a "fill in
      later" mark, stored beside their details under reserved names
      (`backend/domain/canon/marks.py`). The assistant sets them on what it drafts; an author
      edits tags and importance in the entry form.
- [x] The assistant is told to create an entry for anyone or anything mentioned, even in passing,
      and to mark thin ones; marked entries appear under "To fill in" until filled in or the
      author says they need nothing more. **Prompt behaviour unverified until a real run.**
- [x] Lists are ordered by importance, then newest; never alphabetically.
- [x] The story bible opens on an index of its parts. "Everything" shows each type under its own
      heading, five at a time with a labelled "show more". A type's page lists all of it. Tags
      and importance are visible in every list.
- [x] Review page: one status notice, then the changes; setup suggestions after them; the
      author's description and answers in a closed, labelled disclosure; left-out ideas
      collapsed; details one per row; before/after readable at phone width.
- [x] Search results and the home page's lists rank by importance and show tags.
- [x] Story bible lists say how each entry is connected to the story's main entries ("related
      to Ines Varga", "where Ines Varga is"), read from the facts each time (`lib/relations.ts`),
      so it changes when the facts do. Tags the assistant writes are still free text.

## H. First run

- [x] A signed-in person who owns no workspace may create one of their own (one personal
      workspace; more need a platform admin).
- [x] Onboarding: name a first story and a library, and land in the project, which opens on
      "Describe your story" (`Home.tsx`).
- [x] An owner with no AI key connected is prompted to connect one on the empty project screen.
- [x] Sign-out returns to the front page (the session keeps the ID token for the provider).
- [x] A sample story (`sample_story.py`, `POST /entities/sample`): eight original entries with
      tags, importance, facts, one entry to fill in and one belief that disagrees with the facts.
      It loads only into an empty project, as one version by the person asking; on a hosted site
      it opens as a project of its own.
- [ ] A guided first conversation beyond the existing empty-project screen; measuring time to
      first accepted entry (nothing measures it).

## K. Allowance and threat cases (2026-10-10)

- [x] A workspace owner sets how many tokens one person may spend through the workspace's key in
      a day (migration 0027); the lower of that and the site's limit applies. The Assistant page
      shows what has been used; the assistant's dialogs warn from 80%.
- [x] A used-up allowance is refused before the provider is called, as a plain 429. It was an
      unhandled server error before.
- [x] Threat cases as tests (`tests/contract/test_assist_threats.py`): hostile text stays in the
      author's turn; a model that obeys it still yields a bounded, typed, project-confined
      proposal that cannot approve itself; an author's words are not logged; a failing provider
      changes nothing and its message is not passed on.
- [x] Before a request is sent, the dialog says about how many tokens it will read and how many
      are left (`GET /assist/estimate`; from the text's length, not a provider's count).
- [ ] The reply's size is not estimated, nothing is priced in money, and there is no separate
      budget per conversation: the daily allowance is the only limit.

## G. Link suggestions (2026-10-10)

- [x] From the story bible index, the assistant reads the entries and suggests facts between
      existing ones that their details imply (`POST /assist/links`). Recorded facts are not
      repeated; a one-at-a-time fact is never corrected this way; nothing is merged.
      **Scripted model only.**
- [ ] Not semantic search: there are no embeddings, and a project larger than the listing limits
      is only partly read. Precision is not measured.

## I. Levels and plain language (2026-10-10)

- [x] A project with fewer than three entries shows Home, Story bible and Review; History, Media
      and Types and fields wait under "More" until the project grows, the author opens one, or
      asks to always see every page (`lib/levels.ts`). Nothing is gated.
- [x] Every screen the component tests check for accessibility is also checked for the engine's
      own terms and ids (`expectPlainLanguage`), outside blocks marked as technical detail.
- [ ] Levels 3–5 have nothing to show yet (plans, scenes, timelines are later phases).
      Suggestions offered by need ("want to track where people are over time?") are not built.

## Exit criteria (roadmap, Phase 2.5)

| Criterion | State |
| --- | --- |
| First-entry and premise measures met on new accounts with a real provider | Not measured. Nothing in the product records it; it needs a new person and a stopwatch |
| Every Level 1–2 capability has tested assistant and by-hand paths | Met with the scripted model; see the dashboard for which have had a real run |
| A plain instruction that changes canon gives one reviewable proposal with its consequences; nothing applied without approval | Met, and covered by the threat tests |
| Fully usable with no provider | Met: the browser test runs with none |
| Drafted-change precision, recall, duplicate rate, kept-rate against a baseline | Not built. Closed by owner decision in favour of manual runs |
| No Level 1–2 screen shows an internal term outside technical details | Met for every screen the component tests cover |

## Left for later phases

- God Hand: merge as its own operation, events, plan records, and projects larger than the
  listing limits (retrieval) — with plan records in 2.6 and retrieval in Phase 4.
- Meaning-level contradiction checks — with consistency findings in Phase 5.
- Embedding-based link suggestions — when the vector index is used for retrieval in Phase 4.
- Conversations in exports — with exports in Phases 3 and 7.
- Pictures on scenes — when scenes exist in 2.6.
- Need-based suggestions of Level 3–5 features — when those features exist.

## Open owner decisions that block work here

1. None blocking. The owner tests by hand and reports back; fixes follow from what is pasted.
