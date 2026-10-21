# A change that failed to commit, or story records that look damaged

Every change to a story is one PostgreSQL transaction: the change log, the story's records and
the line's head move together or not at all (ADR-011, ADR-012). A commit that fails part way
leaves nothing behind to repair. The story graph (Neo4j), search and the ready views are copies
made afterwards and can always be rebuilt. So "recovery" here means: find out whether the change
went in, and if the stored history itself is damaged, put back a backup.

## How you notice

- An author says approving or committing a change failed ("Something went wrong", a
  `503`, the server restarted mid-way), or a change shows as committing for a long time.
- `make verify` (local) or the `verify` command below exits non-zero.
- The graph or a ready view disagrees with the story: that is not this runbook, see
  [graph-projection.md](graph-projection.md) and [story-views.md](story-views.md).

## What to check

1. **Did the change go in?** Its review page shows Committed, or:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT id, status, updated_at FROM mutation_sets WHERE id = '<change id>';"
   ```
   `committed` means it is in the story. `approved` (or `needs_review`) means it did not go in:
   the author commits it again, or, when the story moved on since, asks for it to be checked
   again from the review page ("Update to version N"). Nothing needs undoing.
2. **Was a commit interrupted?** Changes stuck in `committing` or `recovery_required`:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT id, status, updated_at FROM mutation_sets
      WHERE status IN ('committing', 'recovery_required') ORDER BY updated_at;"
   ```
   For each commit id the review page or the log names, ask what it came to (read only; it
   says `committed` with its revision, or fails when there is no such commit):
   ```sh
   docker compose exec -T server python -m backend.devtools.reconcile <commit id>
   ```
3. **Is the stored history sound?**
   ```sh
   docker compose exec -T server python -m backend.devtools.verify
   ```
   It reads only, and checks that each line's head is its highest revision, every change's
   checksum recomputes from what was stored, and no copy claims to be ahead of the story. It
   names the line, revision or change for each problem; it never prints story text.

## What to do

- **A change that did not go in**: nothing to repair. Tell the author to commit it again (or ask
  for it to be checked again). If it stays in `committing` after the server was restarted and
  `reconcile` finds no commit, the change did not go in: it can be approved and committed again.
- **A head that does not match, or a checksum mismatch**: the history in PostgreSQL has been
  changed outside the app (a hand-written SQL statement, disk damage, a partial restore). Do not
  edit rows by hand to make `verify` pass.
  1. Stop writes: `docker compose stop server`.
  2. `./backup.sh` (keep the damaged state, to look at later).
  3. Restore the newest backup taken before the problem: [restore.md](restore.md). A backup
     taken after it carries the same damage; `verify` on a scratch copy tells you.
  4. Rebuild each story's graph after the restore, as `restore.sh` says.
  Changes made after that backup are lost and must be made again; say so to the authors.
- **A copy ahead of the story** (`watermark ... applied N > head M`): rebuild that copy
  ([graph-projection.md](graph-projection.md)); the story itself is fine.

## How to confirm

- `verify` exits 0 and prints no problem.
- The change's review page shows Committed, or it is waiting to be committed again.
- `docker compose exec server python -m backend.devtools.project --workspace <id> --project <id>
  --verify` agrees for the story concerned.

Read-only commands (`verify`, `reconcile`, the SQL above) were run on a development machine; a
restore for a damaged history has not been rehearsed on the live site.
