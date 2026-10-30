# Story graph behind or wrong

The database (PostgreSQL) holds each story. The story graph (Neo4j) is a copy built from it,
one story at a time, and can always be thrown away and built again. A story never depends on
the graph being up to date; only the main story is copied, never a what-if.

The server keeps the graph up to date in the background: a worker started with the app delivers
each story's new changes to the graph within a few seconds. A story whose delivery fails is
tried again later, waiting longer each time (up to five minutes). Readiness reports the worker as
`graph_worker`. The commands below are for when it is behind anyway, or wrong.

## How you notice

- The graph view, or anything read from the graph, is missing recent changes.
- `docker compose exec server python -m backend.devtools.project --workspace <id> --project <id> --lag`
  prints `projection: head=<n> applied=<m> lag=<n-m> status=<…>`. `status=error` means the last
  attempt failed (the kind of error is kept, never story text).
- A check of the whole story disagrees (below).

See the [README](README.md) for finding a story's workspace and project ids.

## What to check

1. How far behind it is: the `--lag` command above.
2. Whether the graph agrees with the story, record by record:
   ```sh
   docker compose exec server python -m backend.devtools.project --workspace <id> --project <id> --verify
   ```
   It compares the story's history, its current state and the graph, and prints
   `equivalence OK` or `equivalence FAILED` with the record ids and field names that differ.
3. That the database's own history is sound (this checks every story; it changes nothing):
   ```sh
   docker compose exec server python -m backend.devtools.verify
   ```
   `canonical verification OK` means the stories are sound. Lines that begin with `watermark`
   are about the graph's own bookkeeping: a graph that is behind shows as
   `marked current at <m> but head is <n>`. Bring the graph up to date (below) and run it again.
   Any other line (a branch head, a revision, a checksum that does not recompute) means the
   database itself is damaged: stop and restore instead ([restoring from a backup](restore.md));
   the graph is not the problem.
4. That the graph store is up: readiness (see the [README](README.md)) says nothing about
   `neo4j`, and `docker compose ps neo4j` is `healthy`.

## What to do

- **Behind but not wrong.** Bring it up to date:
  ```sh
  docker compose exec server python -m backend.devtools.project --workspace <id> --project <id>
  ```
  It applies the waiting changes in order and prints how many.
- **Wrong, or stuck in `error`.** Throw that story's graph away and build it again from the
  database:
  ```sh
  docker compose exec server python -m backend.devtools.project --workspace <id> --project <id> --rebuild
  ```
  Only that story's part of the graph is replaced. Readers see an emptier graph for the few
  seconds it takes.
- **To keep one story's graph current for a while** (for example during a demonstration), the
  watcher applies changes every few seconds until stopped with Ctrl-C:
  `docker compose exec server python -m backend.devtools.watch --workspace <id> --project <id> --interval 5`.
- **The whole graph store is damaged** (it will not start, or every story fails): empty it and
  rebuild every story, as in [failed migration](failed-migration.md), "The story graph failed".

## How to confirm

- `--lag` prints `lag=0 status=current`.
- `--verify` prints `equivalence OK`.
