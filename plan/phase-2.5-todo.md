# Phase 2.5 implementation todo: assistant-led creation

- Status: In progress (started 2026-10-10).
- Scope: `09-delivery-roadmap.md` Phase 2.5; backlog `AST-EXP-001`–`AST-EXP-018`.
- Governing documents: [`19-assistant-first-experience.md`](19-assistant-first-experience.md)
  (interaction loop, levels, definition of done), ADR-017, [`20-design-direction.md`](20-design-direction.md).
- Rule for every item: an assistant path and a by-hand path, both tested; the assistant only ever
  produces a proposal.

**Standing limitation:** every assistant workflow below is tested with a scripted model, plus a
check that its output format converts to a strict JSON schema. None has been run against a real
provider by the implementer. Draft quality is unknown until the owner uses it with a key.

## Dashboard

| Workstream | Backlog | Status |
| --- | --- | --- |
| A. Assisted creation and fill-in | AST-EXP-001, 002 | Implemented (scripted model only) |
| B. Grouped review | AST-EXP-006 | Partly implemented |
| C. The God Hand | AST-EXP-017 | Implemented, first version (scripted model only) |
| D. Real-provider verification and evaluation set | AST-EXP-003 | Not started; needs a provider key |
| E. Stored, resumable conversations | AST-EXP-004 | Not started |
| F. Start from a premise | AST-EXP-005 | Covered by A in practice; no dedicated first-run |
| G. Link suggestions (semantic) | AST-EXP-007 | Not started |
| H. First run and sample project | AST-EXP-008 | Onboarding implemented; sample project not started |
| I. Levels and plain-language test | AST-EXP-009 | Not started (vocabulary mapping exists) |
| J. By-asking paths for existing features; belief editor; members | AST-EXP-010 | People page implemented; belief editor and by-asking paths open |
| K. Session budgets and assistant threat cases | AST-EXP-011 | Not started |
| L. Pictures on entries | AST-EXP-018 | Implemented for entries; scenes wait for Phase 2.6 |
| M. Design direction | AST-EXP-016 | Implemented; awaiting more owner feedback |
| N. Intuitive design rules and review | — | Rules adopted (document 21); first review applied; not yet tested with people |

## A. Assisted creation and fill-in

- [x] Describe one thing or a whole idea → optional questions → one proposal of entries, details,
      and links (`canon_creation_workflow.py`, `POST /assist/questions`, `POST /assist/proposals`).
- [x] Existing entries are linked by normalized name or alias within a type, never recreated.
- [x] Mentioned-but-unknown things become bare entries; `GET /entities/needs-detail` derives the
      to-fill-in list; the same assistant fills in any existing entry.
- [x] "New entry" opens the assistant; "Fill it in by hand" is one action away and keeps what was
      typed; the no-provider case falls back cleanly (`AssistFlow.tsx`).
- [x] Output format is provider-safe (strict JSON schema test).
- [ ] Run against a real provider and tune prompts (workstream D).

## B. Grouped review

- [x] A proposal is shown in groups by kind, in the order it will be applied, with a one-line
      summary and a kept count per group.
- [x] "Keep all" and "Skip all" per group.
- [x] What the assistant understood, and what else the change affects, are shown above the
      changes when arriving from the assistant.
- [ ] Bulk decisions in one request (today: one request per change, sent in order).
- [ ] "Revise this change" by asking, instead of editing its stored form.
- [ ] Check a drafted proposal against existing canon for contradictions before review (today:
      the structural validator only).
- [ ] Undo an accepted proposal in one step from the review screen (today: History → go back).

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
- [ ] Inviting by asking the assistant; a by-hand belief editor; undo and custom types by asking.

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
- [ ] Choosing an existing picture from the media library; picking which picture is the portrait;
      captions.
- [ ] Pictures on scenes (needs scene plans, Phase 2.6).
- [ ] A refused link after a stale head leaves the stored image unlinked in the media list; it is
      harmless but should be cleaned up or offered for re-linking.
- [ ] No thumbnails: the full image is fetched wherever a portrait is shown.

## H. First run

- [x] A signed-in person who owns no workspace may create one of their own (one personal
      workspace; more need a platform admin).
- [x] Onboarding: name a first story and a library, and land in the project, which opens on
      "Describe your story" (`Home.tsx`).
- [x] An owner with no AI key connected is prompted to connect one on the empty project screen.
- [x] Sign-out returns to the front page (the session keeps the ID token for the provider).
- [ ] A sample project; a guided first conversation; measuring time to first accepted entry.

## D, E, G, I, K

Not started; see the roadmap and backlog for acceptance criteria. Suggested order once the owner
has tried A–C with a real provider: D (tune what exists), E (conversations), the rest of B, then H
and I together once the provider-key decision is made.

## Open owner decisions that block work here

1. Whether the first real-provider runs show the default model is good enough, or it should change.
