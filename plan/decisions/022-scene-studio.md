# ADR-022: A scene's prose is the author's text; the assistant's drafts wait to be kept; the work runs as jobs in PostgreSQL

- Status: Accepted in effect (2026-10-17), under the owner's standing direction to continue the plan.
- Context: Phase 5; `04-ai-and-retrieval.md` (scene generation pipeline), `13-consistency-system.md`,
  `19-assistant-first-experience.md` (Level 4), ADR-005 (POV secrets), ADR-019 (texts as evidence),
  ADR-020 (what-ifs, knowledge), ADR-021 (scene context), `phase-5-todo.md`.

## Decision

**A scene's prose is one of the author's texts.** It is a source text of kind `scene`, linked to its
plan item in `scene_manuscripts`, one row per line of the story (branch, scene → text). It gets
everything texts already have, with no new machinery: immutable versions, names found with no model,
the passage index that feeds scene context (ADR-021), and, in Phase 6, reading into proposed changes.
Prose is still evidence, never canon (ADR-019): writing a scene changes no fact.

**Copy on write in a what-if.** Starting a what-if copies the links with the rest of the story
(`scene_manuscripts` is one of the forked tables, ADR-020), so the what-if first shows the main
story's prose. The first change to words another line shares copies the text, with every version, so
no history is lost. If the change is made in a what-if, the what-if moves to the copy; if it is made
in the main story, the main story keeps its own text (and the notes on its versions) and the what-ifs
move to the copy. Either way neither line's words change under the other. Copying the link and not
the text keeps a what-if cheap until someone writes in it. A scene's text is changed only in the
studio: the texts page shows it, says which scene it belongs to and links there, and refuses to
change or remove it, since it may be shared; texts of a scene that another line holds are not listed.

**Every version says where it came from.** A version records its `origin` (`author`, or `assistant`
for a draft the author kept), its `parent_version_id`, and for a kept draft the `draft_id` and the
`manifest_id` of the exact context it was drafted from. The columns were added with defaults, so no
existing row is rewritten and texts brought in stay the author's. This answers "did I write this, or
did I keep it from the assistant, and from what?" without trusting memory, lets a later phase show a
version's line of descent, and lets the author see the context behind any kept draft. Saving names
the version it was based on; if the scene moved on meanwhile it is refused ("This scene changed while
you were writing. Your words are still here.") instead of overwriting. An archive carries the main
story's scene links, the voice note, and each version's origin when it is not the author's; restoring
rebuilds parents from version order. Draft and manifest ids are not carried, since drafts are not.

**A draft is a proposal, never applied on its own.** Drafts wait in `scene_drafts` beside the text.
Keeping one makes it the scene's newest version (with the lineage above); discarding it deletes it.
Nothing the model writes replaces the author's words without that choice. Up to three variants per
request, so the author can compare rather than accept the first answer.

**Drafting and checking with the assistant are jobs in PostgreSQL.** A `jobs` table holds the work,
its scope and an authorization snapshot (the Phase 1 envelope, `backend/domain/jobs.py`). A worker
loop started with the app claims the oldest waiting job with `UPDATE … WHERE id = (SELECT … FOR UPDATE
SKIP LOCKED LIMIT 1)`, so several workers never take the same job and never wait on each other. The
claim takes a lease (`LEASE_SECONDS`, ten minutes) that every step renews; a job whose worker died is
claimable again when its lease runs out, and a job interrupted `MAX_ATTEMPTS` (two) times is marked
failed ("The work was interrupted and could not be finished.") rather than retried for ever. Before a
job runs, the author's access is checked again, since it may have been withdrawn after asking; the job
then runs in the what-if it was asked in, or fails "not found" if that what-if is gone. Each step's
heartbeat returns whether the author asked it to stop: a waiting job is cancelled at once, a running
one at its next step (between model calls, never mid-call). A lost lease also stops the worker.
A worker's writes are tied to its lease: renewing, recording usage and finishing all require that the
worker still holds it, so a worker that lost its lease (a call that ran past it) stops quietly and can
never end or overwrite the run that took over. While a model call runs, the lease is renewed in the
background, so a slow call is not mistaken for a dead worker. When the app stops mid-job, the job is put back in the queue without counting the attempt.
Each person may have three jobs waiting or running in a project, and a scene holds at most nine drafts
waiting to be kept, so one author cannot fill the queue for everyone.
Starting a draft takes an `Idempotency-Key`, so a repeated click returns the same job. Failures are
recorded as a code and plain words (`no_provider`, `provider`, `provider_output`, `not_allowed`,
`not_found`, `refused`, `internal`); a provider's own error text never is.

Why not Celery, Redis or arq: the deployment already depends on PostgreSQL and nothing else for state;
a broker would add a service to run, secure and back up, and a second place where a job could be
"done" while the database disagreed. Our volume is a handful of model calls per author, far below the
point where a table-backed queue strains. This is now a mainstream choice: Rails 8 ships Solid Queue,
built on `FOR UPDATE SKIP LOCKED`, as its default, and PostgreSQL's documentation names the clause as
the way to avoid contention between consumers of a queue-like table. Practitioner reports put the
ceiling at thousands of concurrent workers or roughly a thousand jobs a second, with vacuum and lock
pressure beyond that; the same sources recommend what we did (claim in one short transaction, an index
for claimable rows, recovery of stale jobs). Research changed one thing: the claim also gives up
over-attempted jobs in the same transaction, so a job that crashes its worker cannot loop.

**How a draft is asked for.** The prompt is built from the scene context of ADR-021 and versioned
(`scene-draft/2`):

- *Rules in the system message; everything from the story is material.* The system message holds
  only the rules and says nothing in the material can override them (OpenAI's instruction hierarchy).
  Everything that comes from the story, including the author's own notes, goes in the user message
  between markers that appear nowhere in that text, with the instruction that text between them is
  never to be followed ("spotlighting", Hines et al. 2024). The markers are chosen, not random, so the
  same request is always built the same way, and text cannot fake the end marker.
- *The author's voice first.* "How you write" is a note per story (`story_voices`, up to 4,000
  characters), the first thing in the material, labelled as how the author writes. With no note, the
  model is told to match the author's writing in the context, which carries a voice sample (ADR-021),
  never the scene's own current words when other writing exists. Drafting tools work the same way
  (Novelcrafter keeps a style guide the model reads with every prompt). Writing from another line of
  the story (a what-if's copy of a scene) is never used as context or voice sample.
- *Stable context, then the scene last.* The rendered context keeps ADR-021's order (rules, cast and
  facts first; the scene at the end), which suits prompt caching and the finding that models use the
  start and end of a long context best (Liu et al., "Lost in the Middle", TACL 2024). The author's
  direction for this draft follows it, then "Write the scene now".
- *The viewpoint's knowledge is a boundary.* The model writes through the viewpoint character and is
  told that anything under "For the author only" is true but unknown to them: it may shape what
  happens around them, never what they think, say or notice (ADR-005). The rule is always given; with
  no viewpoint chosen, such things must never be stated on the page. Secrets reach that section and
  nowhere else: a secret the viewpoint does not know (every secret, when there is no viewpoint) is
  author-only even when an earlier scene showed it to the reader, and is left out entirely unless the
  author chose to include secrets.
- *New names are marked.* A new named person or place is written `@Name` at first use, so the name
  check finds it and the author decides whether it joins the story bible.
- *Stock phrasing is named and avoided.* The prompt lists phrases readers now recognise as machine
  writing, the same list the checks look for; writing tools offer passes to remove these "AI-isms".
- The context is labelled as material, not instructions, so text in the story cannot steer the model.

**Checks without a model run on every draft and every version** (`backend/domain/drafting.py`): a new
name the story bible does not know, a secret the viewpoint character should not know whose names share
a paragraph, a planned reveal missing, the place or viewpoint never named, stock phrasing, and length
against what was asked. Each says how sure it is (warning or note) and where, to sit in the margin.
They are worked out each time and never stored, and none changes the text.

**A check with the assistant is the author's choice, and every note must quote the text.** The review
job (`scene-review/2`) reads the scene's context and the text or draft, and returns at most twelve
notes in fixed categories (continuity, viewpoint, plan, voice, clarity). A note is kept only when its
quote is found in the text, and is then anchored to that span; a note without a findable quote, or
with an unknown category, is dropped and counted. Kept notes are stored against the draft or version
they are about (`draft_findings`). Requiring quotes keeps a note checkable by the author and stops
notes about things the text does not say.

**Usage and limits.** Requests are bounded (1–3 variants, 150–3,000 words, a direction under 2,000
characters). The person's allowance is checked before a job is queued and again when the job reaches
the provider, through the same provider route as requests answered at once (`resolve_provider`:
the workspace's own key when hosted, the operator's when self-hosted). The allowance is checked again,
with the estimate for what is still to write, before each draft of a request. Usage is recorded once
per model call, under a key of its own, both in the allowance and on the job, so the author sees what
each piece of work cost and a retried call is not counted twice.

**An estimate before anything is sent.** The studio estimates the tokens a draft would read and write
(the context's word count, the voice note and the instructions, at about four tokens to three words,
times the variants) and calls no model, so the author knows the cost before asking.

**Prose stays out of logs.** Jobs log their kind, outcome and error code (and an unexpected failure's
error type, never its message); the studio logs counts and the prompt version. Neither the author's
words nor a draft is ever logged, and the database engine hides bound values, so a failed statement
cannot carry them into a log either. Two saves at the same moment get "changed at the same moment"
rather than a server error.

## Consequences

- A scene's prose has history, lineage and the passage index for free, and Phase 6 can read it like
  any other text.
- A worker needs nothing but the database; a job survives a restart and is picked up again.
- A worker whose process hangs keeps no lease (renewal stops with it), so its job is taken over; a
  worker that lost its lease can no longer write anything for the job, and the run that took over
  skips the drafts already made for the job.
- A what-if that edits a scene holds its own copy of that text; the rest stay shared.
- Drafts and assistant notes are working material: not archived, and a discarded draft is gone.
- Every assistant draft that becomes prose passed through an explicit "keep".

## Amendment 2026-10-18: checking on its own, and asking before a large request

CON-009, OPS-002, AST-EXP-011; migration `0038_usage_controls`.

**A story chooses how much checking runs on its own.** "Only when I ask" stays the default, so no
story spends anything it did not before. "Balanced" queues a check with the assistant after the
author keeps a draft; "Thorough" also after a save, at most once every few minutes. A check is a
`scene_review` job marked `automatic`, submitted as the author who saved or kept, so it is never on a
reader's behalf and counts against that author's allowance and job cap. It starts only when nothing
else is under way for the scene, the scene's automatic checks have used less than its daily
allowance (counted from those jobs' reported tokens), the estimate is under the confirmation
threshold (nobody is there to confirm), a model is available, the person's allowance has room and
they have fewer than three jobs. Otherwise the save or keep succeeds and the studio says why. Checks
without a model are unchanged and always run.

**A request above a size is sent only once confirmed.** Every draft, check, reading, plan card and
assistant reply is estimated before it is sent (what it reads, plus the longest reply it may write:
an upper bound, as cost-control practice recommends). Above the threshold the server refuses with
`409 needs_confirmation` naming the estimate, before anything is queued or sent; the client asks the
author once, in one place for every request (`lib/spend.ts`), and sends the same request again with
`X-Confirm-Tokens`. Checking on the server, not only in the page, keeps every client honest. The
threshold, and a budget per conversation, can be set for a story or by an owner for the workspace;
the lower applies, as with the daily allowance. A soft threshold asks; the allowance and the
conversation budget refuse.

**Money only as reported.** OpenRouter reports what each call cost (`usage.cost`, in credits worth a
dollar) on every response; it is kept on the usage record and summed on the job, and shown. No price
list is kept, so nothing is estimated in money and thresholds are in tokens.

**Corrections after review (2026-10-18).**

- *A confirmation answers one request.* `X-Confirm-Tokens` counts only when it is at least the
  server's own estimate for this request and at most half again as large (`CONFIRM_SLACK`). A large
  value sent up front, without being asked, is refused like no value at all, so the workspace's
  threshold cannot be bypassed by a client that always sends a big number. If the story changed
  between asking and sending and the estimate grew, the client asks once more with the new figure.
- *Every model call is held to the same limits.* Drafting a rule, asking about a character and the
  answers that follow (both the canon workflow and the older graph routes) are checked against the
  threshold and the conversation budget before any call, like every other assistant request.
  Revising suggested changes by asking continues the conversation that proposed them: it counts
  against that conversation's budget when it is the author's own.
- *Automatic checks cannot double up.* Two saves at the same moment could each see no check under
  way. The check is now submitted with a key made from the newest check of the scene that the save
  saw (`auto:{plan}:{line}:after:{job}`), so the job queue's unique key lets only one start. A check
  still waiting or under way holds its estimate against the scene's allowance until it reports what
  it used. Any error while starting one, not only a refusal, leaves the save successful and says the
  check could not start; only the kind of error is logged.
- *"Thorough" waits 5 to 60 minutes between checks after saving,* the choices the studio offers; the
  server refuses anything else.
- *Prompt versions.* Scene conditions (STY-005) were added to what a draft and a check read, so those
  prompts are now `scene-draft/3` and `scene-review/3`; a job's recorded version says which it used.

## Known limits

- The per-job token limit is checked for each call (with the estimate of what remains), not against
  the total the job has used so far.
- The caps on waiting jobs and drafts are counted, then written, so two requests at the same instant
  can both pass. Automatic checks are the exception for one author (their key is unique per person);
  two different authors saving the same scene at the same instant could still each start one.
- A check taken over by a second worker could store its notes twice.

## Not decided here

Streaming a draft as it is written; editing inside a draft, or merging parts of several drafts or a
draft with the author's text (a draft is kept or discarded whole); showing a version's line of descent
on screen; reading facts out of prose into proposed changes (Phase 6); continuing or rewriting a
selected passage; style learned from the author's texts rather than a note; per-job priority and
scheduling; notifying the author when a job finishes beyond the studio polling it.

## Sources

- PostgreSQL documentation, `SELECT` locking clause (`SKIP LOCKED`): <https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE>
- Solid Queue and `FOR UPDATE SKIP LOCKED` (BigBinary): <https://bigbinary.com/blog/solid-queue>; Solid Queue documentation: <https://rubydoc.info/gems/solid_queue>
- Postgres as a job queue, limits at scale: <https://peerlist.io/saxenashikhil/articles/potential-consequences-of-using-postgres-as-a-job-queue>; Postgres vs Redis benchmark: <https://levelup.gitconnected.com/postgres-vs-redis-as-a-job-queue-postgres-enqueued-2-5x-faster-redis-drained-2x-faster-ebef92f98aba>; practices: <https://www.dbpro.app/blog/postgresql-skip-locked>
- Novelcrafter prompts (style guide, removing AI-isms): <https://www.novelcrafter.com/courses/prompts-and-presets>, <https://novelcrafter.com/help/docs/prompts/types-of-prompts-in-novelcrafter>
- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts", TACL 2024.
- OpenRouter usage accounting (`usage.cost`, always included): <https://openrouter.ai/docs/use-cases/usage-accounting>
- Estimating a request before sending it, and asking above a soft threshold: <https://www.yottalabs.ai/post/estimate-llm-request-cost-before-sending>
