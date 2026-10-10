# Intuitive design rules

- Status: Adopted 2026-10-10 at the owner's request. These rules bind every screen; a change that
  breaks one needs a stated reason next to the code.
- Companions: [`19-assistant-first-experience.md`](19-assistant-first-experience.md) (what the
  product does and in what order it reveals itself) and [`20-design-direction.md`](20-design-direction.md)
  (how it looks). This document is about whether a person can use it without being taught.
- Review of the product against these rules, with what was changed: the last section.

"Intuitive" here means something specific: a person can tell what they can do, predict what will
happen when they do it, see that it happened, and get back if it was a mistake — without
remembering anything from last time. Each rule below serves one of those four.

Sources are listed at the end. They are a mix of usability research summaries (Nielsen Norman
Group), a design system's written guidance, and practitioner essays; where a source could not be
read in full, that is noted and the rule rests on the others.

## 1. Words before pictures

People read a word faster and more reliably than they decode a picture. Only a handful of icons
(home, print, the search magnifier) mean the same thing everywhere; the rest are guesses.

- **W1. Every control has a visible text label.** An icon may accompany the label; it never
  replaces it. This includes navigation, toolbars, and "obvious" icons.
- **W2. Labels are always visible.** Never only on hover, focus, or long-press. Hover does not
  exist on a touch screen and costs effort everywhere else.
- **W3. The exceptions are few and fixed:** none. A close control says "Close"; a menu control
  says "Menu". (An accessible name for a screen reader is required as well, not instead.)
- **W4. A label says what will happen, with a verb and its object:** "Add fact", "Archive entry",
  "Discard changes". Never "OK", "Yes", "Submit", or a bare "Remove" where it is unclear of what.
- **W5. Similar actions get different words.** Create and edit, save and archive, add and upload
  must be told apart by reading, not by icon shape.
- **W6. An icon keeps one meaning across the product,** and is never borrowed from a common
  convention to mean something else.
- **W7. The five-second test.** If no obvious icon comes to mind in five seconds, the control gets
  a label and no icon.
- **W8. Lists and navigation are text,** in a stack that can be scanned, not a grid of pictures.

## 2. The author's words, one word per thing

- **L1. The interface speaks the author's language,** not the system's. Internal terms (entity,
  predicate, mutation, revision, schema, projection) appear only under "Technical details" or
  "Advanced". The mapping lives in one place (`frontend/src/lib/words.ts`).
- **L2. One word means one thing, everywhere.** The fixed vocabulary for ending things:

  | Word | Means | Reversible? |
  | --- | --- | --- |
  | **Cancel** | Close this dialog or form; nothing happened | — |
  | **Skip** / **Keep** | Leave a suggested change out of, or in, what will be saved | Yes, until saved |
  | **Discard** | Throw away suggested changes that were never saved | No |
  | **Remove** | Detach something from where it is (a picture from an entry, a person from a workspace, a key) | By adding it again |
  | **Withdraw** | Take back an invitation that has not been used | By inviting again |
  | **Archive** | Take an entry out of the active story; its history stays | Through History |
  | **No longer true** | End a fact from now on | By adding it again |
  | **Go back** | Return the whole story bible to an earlier version, as a new version | Yes |

  "Delete" is not used, because nothing an author can do here erases history.
- **L3. Roles, states, and types are named the same on every screen.** A reader is a "Reader"
  in the people list, the sidebar, and every message.
- **L4. Messages are sentences about the story,** not codes. An error says what happened, that
  nothing was lost (when true), and what to do next. Bracketed codes and stack-flavoured text are
  never shown.

## 3. Recognition, not recall

Showing something a person can recognise takes less from them than asking them to remember it.

- **R1. Options are shown, not remembered.** Choices come from visible menus built from the
  story's own names; nobody types an identifier.
- **R2. The product remembers where the author has been.** Recently opened entries and recently
  added entries are offered wherever the author is choosing what to open.
- **R3. Examples sit where the typing happens,** and can be used with one click. A blank box with
  "How can I help?" is a recall task.
- **R4. Help is in context and brief:** one sentence next to the thing it explains. No tours.
- **R5. Persistent hints are text beside the field,** not placeholder text, which vanishes at the
  moment it is needed. Placeholders hold an example only.
- **R6. A shortcut is always shown next to the visible control it duplicates,** never offered
  instead of one.
- **R7. What changed is shown, not left to be remembered:** before-and-after for edits, a
  sentence for each change, the author's own words quoted back.

## 4. If it exists, it can be seen

A feature that cannot be found does not exist. People act on what is in front of them.

- **D1. Controls live where the work is.** The way to change a thing is on the thing.
- **D2. Nothing essential is only on hover.** No hover-only buttons, no explanation that exists
  only in a tooltip. A tooltip may repeat what is visible; it may not be the only place.
- **D3. No hidden gestures.** Anything a swipe, drag, or long-press does has a visible control.
- **D4. Progressive disclosure hides depth, not doors.** A feature may be one click down behind
  a plainly worded disclosure ("More options", "Advanced: versions and the raw definition"); the
  disclosure itself is always visible and says what is inside.
- **D5. Every screen has a visible way out and a visible way home.** Not only the logo.
- **D6. On a large screen, navigation is open.** It collapses behind a "Menu" control only where
  there is no room.
- **D7. A disabled control says why, in visible text,** next to it.

## 5. Actions with consequences

Prevention beats recovery, and recovery beats a warning. A person on autopilot clicks without
reading, so the layout has to protect them when the words do not.

- **C1. Consequential options are kept away from routine ones.** Not adjacent in a row, a menu, or
  a toolbar, even when they are related. Distance is cheap; an undone mistake is not.
- **C2. They also look different,** by more than one signal: colour, weight, position, wording.
  A label alone is not enough.
- **C3. Undo is preferred to confirmation** wherever the action can be reversed. The action
  happens, the result is shown, and "Undo" is offered in place.
- **C4. Confirmation is for what cannot be undone or reaches widely,** and only for that.
  Confirming routine actions teaches people to click through.
- **C5. A confirmation is specific.** Its title is a question naming the action and the thing
  ("Discard these 3 changes?"). Its body says what will happen and whether it can be reversed.
  Its buttons name the outcomes ("Discard changes" / "Keep reviewing"), never "Yes" and "No".
  Never "Are you sure?".
- **C6. The safe choice is the easy one.** The safe button comes first and receives focus; the
  consequential one is styled as such and is never the default.
- **C7. The rarest and gravest actions need a deliberate step** (an explicit acknowledgement).
  This is reserved for archiving and for returning the whole story to an earlier version, so that
  it stays meaningful.
- **C8. Typed work is not lost by a stray click.** Clicking outside a dialog does not close it.

## 6. Always say what is happening

- **S1. Every action answers within a moment:** the control changes ("Saving…"), then the result
  is visible where the author is looking.
- **S2. A change the author made is acknowledged in words** when the result is not self-evident
  ("Sam is now a Reader").
- **S3. Waiting is named** ("Working it out…", "Loading entries…"), never a bare spinner.
- **S4. The state of the whole is always visible:** which project, which version, whether it is
  saved, whether anything is waiting for review.
- **S5. Nothing is presented as done, checked, or complete when it is not.**
- **S6. Failures keep the author's input** and say so.

## 7. Consistency

- **K1. The same action looks and reads the same everywhere,** and sits in the same place: the
  main action last in a row, the way out before it.
- **K2. One main action per view,** visually the strongest.
- **K3. Platform conventions are followed:** links look like links, buttons like buttons, Escape
  closes, Tab moves in reading order, the logo leads home.
- **K4. The accent is for marks, not for alarm.** Danger has its own colour and is the only use of it.

## 8. Forms

- **F1. Ask for the least that will work.** Every field beyond the first is a reason to stop.
- **F2. Labels sit above their fields and stay.** Optional fields say "(optional)".
- **F3. Problems are caught before sending,** shown beside the field, in words that say how to fix
  them, and announced to assistive technology.
- **F4. Sensible defaults are filled in,** and are plainly editable.
- **F5. Limits are stated before they are hit** (file types, sizes).

## 9. Reach

- **A1. Everything works from the keyboard,** with a visible focus indicator.
- **A2. Text and controls meet WCAG AA contrast** in both themes; this is checked in the browser
  tests.
- **A3. Motion is optional** and respects the system setting.
- **A4. Targets are comfortably large** and not crowded against a dangerous neighbour (C1).

## How to use these rules

- A new screen is reviewed against sections 1–8 before it is called done; this joins the
  definition of done in document 19.
- The plain-language and label rules are partly enforced by tests (the browser tests address
  controls by their visible names, so an unlabelled control cannot be tested and therefore cannot
  ship).
- When two rules pull apart, the order is: do not lose the author's work; say what is happening;
  show rather than hide; then tidiness.

## Review of the product against these rules, 2026-10-10

Every screen's code was read against the rules, and the changed screens were then opened in a
browser at desktop and phone widths. Findings, and what was done:

| Rule | Finding | Outcome |
| --- | --- | --- |
| W1–W3 | Icon-only controls: theme switch, sign out, close dialog, mobile menu, remove a detail row, remove a picture | All given visible labels |
| W4, L2 | "Cancel" used to withdraw an invitation; bare "Remove" on members and keys | "Withdraw invitation"; removals name their object in the confirmation |
| L1 | The assistant's main button was labelled with its internal name, "God Hand" | Button reads "Change anything"; the name stays as the dialog's title |
| L3 | Sidebar showed the server's role word ("viewer") while People said "Reader" | One role vocabulary everywhere |
| L4 | Server validation text reached the screen with bracketed codes | Codes stripped; message shown as a sentence |
| R2 | Search offered recently added entries, not recently opened ones | Recently opened entries are remembered per project and offered first |
| R5, F2 | Repeated rows (details, fields of a type, amount and unit) were labelled only by placeholder text | Small captions that stay above each field |
| D2 | Why "Send for review instead" exists was only in a tooltip | Stated in the dialog |
| D5 | The only way from a project to the library was clicking the logo | "All projects" link under the project name |
| D5 | On a narrow screen the menu could only be put away by tapping outside it | Visible "Close menu" control |
| D1 | An entry with no details told the author to "use Edit", a button elsewhere on the page | "Add details" button where the details are missing |
| D7 | Disabled main actions explained themselves only by tooltip, or not at all (archiving) | Reason shown in text beside the button |
| C1 | "Archive" sat beside "Edit" in the entry header; "Discard all" sat beside "Approve and add to canon" | Archive moved to its own section at the foot of the entry; Discard moved away from the main action |
| C3 | Ending a fact and removing a picture happened at once with no way back | Both offer "Undo" in place |
| C4, C5 | Discarding suggested changes (irreversible), removing a member, removing an AI key, and publishing a definition (irreversible) had no confirmation | Specific confirmations added, safe choice first and focused |
| C8 | Clicking outside a dialog closed it and lost what was typed | Dialogs close only by their own controls or Escape |
| S2 | Changing someone's role gave no acknowledgement | Announced in words |
| L1 | Media page spoke of files "registered with this project" | Plain wording, and it says where pictures are added |
| R7 | The go-back preview listed what it would do under the heading "What would be undone" | Heading says what going back would do; the safe button reads "Stay on this version" |
| K1 | The Assistant and People pages had no page title on a self-hosted copy | Titles added |
| W2 | On a phone the library header broke the product name across two lines | Name stays whole; controls drop below it |
| K1 | The entry page had two buttons both labelled "Edit" that did the same thing | One, labelled "Edit entry" |
| W2, D6 | On a narrow screen the top bar ran off the side and the search prompt lost its words | Top bar wraps to two rows; every control keeps its label |
| — | Found while looking: a hidden file input made the entry page scroll sideways | Fixed |
| K1 | Landing page's main button said "Open your library" to a signed-out visitor | Says "Sign in" consistently |

Known and not yet done:

- **R7:** History says how many changes a version made, not what they were, without opening it.
- **R3:** The assistant's dialogs are separate; a single persistent conversation would let earlier
  instructions be recognised and reused (Phase 2.5, E).
- **C3:** Changing someone's role has no undo beyond changing it back by hand.
- **F1:** The by-hand entry form still shows "also known as" and details before they are needed.
- **L1:** The by-hand entry form's "Short name for links" can still produce an error that says
  "slug".
- **D6:** On a narrow screen the closed menu's links can still be reached with the Tab key.
- The People and Assistant screens as an invited member or owner sees them on the hosted site
  were reviewed in code and by test, not in a browser: the local test copy has no workspaces.
- **A4:** Small buttons are 33px tall: above the WCAG 2.2 minimum, below the 44px comfortable for
  thumbs.
- None of this has been tested with people. The rules come from published guidance; whether the
  product is intuitive is settled by watching authors use it.

## Sources

- [Icon Usability (Nielsen Norman Group)](https://www.nngroup.com/articles/icon-usability/) and the
  related [Tips for Icon Usability](https://www.nngroup.com/videos/icon-usability/) video page
- [Proximity of Consequential Options (Nielsen Norman Group)](https://www.nngroup.com/articles/proximity-consequential-options/)
- [Recognition and Recall in User Interfaces (Nielsen Norman Group)](https://www.nngroup.com/articles/recognition-and-recall/)
- [Jakob Nielsen's sixth usability heuristic](https://uxdesign.cc/jakob-nielsens-sixth-usability-heuristic-for-user-interface-design-ac88e7d58ec)
  (could not be fetched; the NN/g article above covers the same heuristic)
- [Confirmation Dialogs Can Prevent User Errors — If Not Overused (Nielsen Norman Group)](https://www.nngroup.com/articles/confirmation-dialog/)
- [Destructive actions (Automattic design system)](https://system.automattic.design/foundations/destructive-actions/)
  (returned "not found"; the Primer and NN/g guidance was used for the same ground)
- [Confirmation dialog guidelines (GitHub Primer)](https://primer.style/product/components/confirmation-dialog/guidelines)
- [In Defense of Text Labels (Christopher Butler)](https://chrbutler.com/in-defense-of-text-labels)
- [Undiscoverable Features (Rian van der Merwe)](https://elezea.com/2014/08/undiscoverable-features/)
- [Nielsen's ten usability heuristics, as summarised by The Decision Lab](https://thedecisionlab.com/reference-guide/design/nielsens-heuristics)
