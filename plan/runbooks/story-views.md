# The story's ready views: behind, not trusted, or wrong (QRY-001)

Some questions about a story are answered from views kept ready in the database: where someone
is at a moment, who holds what, who is related to whom, the timeline, what each scene shows the
reader, and what leans on a record. Each line of the story (the main story and every what-if)
has its own views, built from the story's records after each saved change. They are never the
story itself: they can always be thrown away and built again, and a restore or an archive never
carries them.

The server builds them in the background, in the same loop that keeps the story graph up to
date, one story at a time under the same per-story lock as graph delivery (so two servers never
build one story at once), and for at most about two seconds a poll before the loop moves on to
graph delivery and the passage index (`READ_MODEL_BUDGET_SECONDS`); lines left over are built
next poll. A view answers reads only once **replay verification** has matched it: the view is built
again from the story's history and compared with the stored one. Until then, and whenever a view
is behind the story, reads work everything out on read, exactly as before. Nothing an author sees
is ever wrong because a view is behind; the cost is speed.

An answer worked out on read is read again when a change lands while it is being read; if changes
keep landing, it answers with what it read and says so (`version` below `latest_version`). The
timeline and the reader's journey use their view only when the story did not move while their
records were read. A story with more than 10,000 entries, facts, beliefs or events (or 2,000 plan
items) is read only up to that limit: such an answer has `cut: true`, and its views never match
their replay, so they are never trusted.

## How you notice

- `GET /api/v1/projects/<project>/story-views` (as a member, with `X-Workspace-Id`) lists each
  view of the line: `version` (what it reflects), `latest_version`, `behind`, `answers_reads`,
  `outcome` (`matched` / `differed`) and `differences` (a count of rows, never content).
- `make read-models` prints how many lines have views, how many are waiting to be built, and the
  verification outcomes. The logs have `read_models.build_failed` (with the kind of error only)
  and `read_models.verify_differed` (view name and a count).
- Metrics: `read_model_reads_total{view, source}` (`source="worked_out"` growing while `view`
  stays flat means reads are not using the views), `read_model_builds_total`,
  `read_model_verifications_total{outcome}`.

## What to do

1. **Views were never verified** (new install, or after a builder change): verify them.
   ```sh
   docker compose exec server python -m backend.devtools.read_models verify
   ```
   It prints counts only, and exits 1 when any view differed. A matching view answers reads
   from then on, and keeps doing so after each rebuild while the builder version is the same.
   Run it again after an upgrade that raises the builder version (`builder_version` in
   `make read-models`).
2. **Views are behind**: they catch up within seconds while the server runs. To build now:
   `python -m backend.devtools.read_models build`. A story whose build keeps failing waits longer
   each time (up to five minutes) and never stops the others; look for `read_models.build_failed`.
3. **A view differed from its replay**: reads have already stopped using it. Forget and rebuild:
   ```sh
   docker compose exec server python -m backend.devtools.read_models verify --repair
   docker compose exec server python -m backend.devtools.read_models build
   docker compose exec server python -m backend.devtools.read_models verify
   ```
   If it differs again, the story's current records and its history disagree: run the story
   check in [Story graph behind or wrong](graph-projection.md) (`project --verify`), which compares
   them record by record, and treat that first.
4. **After a restore**: nothing to do. A restored story's commits reach the outbox and its views
   are built again; verify them as in step 1. `forget --project <id>` removes one story's views
   if you want them built from scratch.

## How to confirm

`make read-models-verify` reports `differed: 0`, and `story-views` shows `answers_reads: true`
with `behind: 0` for each view.

The commands were run against the in-memory test doubles and a disposable PostgreSQL database
(2026-10-20); none has been run on the live site.
