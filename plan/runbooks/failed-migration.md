# Failed migration on start

The stack runs migrations in their own container (`migrate`) before the server starts. The
server only starts once `migrate` has exited cleanly, and it refuses to serve on a database or
story graph whose layout is not up to date.

## How you notice

- `docker compose up -d --wait` stops with an error, or `docker compose ps -a` shows `migrate`
  exited with a code other than 0 and `server` not running.
- The site shows Caddy's "bad gateway" page.
- If the server was started by hand against an old layout, its log says
  `PostgreSQL schema is not migrated` or `Neo4j schema is not migrated` and it exits.

## What to check

1. Which store failed, and why:
   ```sh
   docker compose logs migrate
   ```
   The last lines name the store (`postgres:` or `neo4j:`). An Alembic error names the revision
   it was applying. `neo4j: migration drift detected` means a recorded graph migration no longer
   matches the code (exit code 2).
2. Where each store stands, without changing anything:
   ```sh
   docker compose run --rm migrate python -m backend.migrate --verify
   ```
   It prints `postgres: up to date (<revision>)` or `NOT current (current=…, head=…)`, and the
   same for Neo4j.
3. That the stores themselves are up: `docker compose ps postgres neo4j` (both `healthy`). If a
   store is down, the migration failed for that reason; fix the store first (see
   [disk full](disk-full.md) if it ran out of space).

## What to do

**The database (PostgreSQL) failed.** All pending database migrations run in one transaction, so
a failure leaves the database exactly as it was before the upgrade: nothing is half applied.

- If the cause is passing (the database was restarting, the disk was full and has been cleared),
  run the migration again: `docker compose up -d --wait`.
- If the new version's migration itself is wrong, go back to the version that was running:
  set `NOVEL_ENGINE_VERSION` in `.env` to the previous commit and `docker compose up -d --wait`.
  Since the failed migration changed nothing, no restore is needed. Report the failure with the
  `migrate` log.
- If a migration did complete but the new version misbehaves, migrations are forward only: going
  back means restoring the backup taken before the upgrade ([restoring from a backup](restore.md)).
  This is why the upgrade steps in `deploy/hosted/README.md` start with `./backup.sh`.

**The story graph (Neo4j) failed or drifted.** The graph is rebuilt from the database and holds
nothing that cannot be rebuilt.

- Passing cause: `docker compose up -d --wait` again.
- Drift or a graph that will not migrate: empty the graph and build it again. This loses nothing
  from any story.
  ```sh
  docker compose stop server neo4j
  docker compose rm -f neo4j
  docker volume rm novel-engine_neo4j-data
  docker compose up -d --wait
  ```
  Then rebuild each story's graph ([story graph behind or wrong](graph-projection.md)).
  (The volume is named after the compose project, `novel-engine` by default; `docker volume ls`
  shows it.)

## How to confirm

- `docker compose ps -a`: `migrate` exited 0; `server`, `postgres`, `neo4j` healthy.
- `docker compose run --rm migrate python -m backend.migrate --verify` prints `up to date` twice.
- Readiness (see the [README](README.md)) says `200 ready`.
