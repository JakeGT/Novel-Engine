# Disk full

Everything the stack keeps lives in Docker volumes on the host's disk (the database, the story
graph, the stored objects) plus `deploy/hosted/backups/`. Commands run from `deploy/hosted/`. Container logs are capped at three
files of 10 MB per container.

## How you notice

- Saves fail with a server error; readiness (see the [README](README.md)) says `503 unavailable`
  with "The database cannot be reached."
- `docker compose ps` shows `postgres` restarting or unhealthy; its log says
  `No space left on device` or `could not extend file`.
- `./backup.sh` stops part way (a backup folder without `MANIFEST`).

## What to check

1. How full the disk is, and where Docker keeps its data:
   ```sh
   df -h / /var/lib/docker
   docker system df
   ```
2. What is taking the space:
   ```sh
   du -sh backups
   docker system df -v | grep -E 'novel-engine|VOLUME NAME'
   docker image ls novel-engine
   ```
3. How much assistant leftovers and expired sign-in records could be cleared, without removing
   anything:
   ```sh
   docker compose exec server python -m backend.devtools.retention --days 30 --dry-run
   ```

## What to do

In this order, stopping when there is room again (aim for at least 20% free):

1. **Old backups.** Keep the newest few, after copying anything you need elsewhere:
   `./prune-backups.sh --keep 7` (`--dry-run` first to see what goes). Never remove the only
   complete backup.
2. **Old images and build leftovers.** Keep the running version and the one before it (for
   rolling back): `docker image rm novel-engine:<old commit>` for older tags, then
   `docker builder prune`.
3. **Assistant leftovers** that no author needs (finished background work, notes on replaced
   versions, expired sessions): `docker compose exec server python -m backend.devtools.retention --days 30`.
   It never removes a text, a version, a waiting draft or anything an author decided.
4. **The story graph**, as a last resort: it can be emptied and rebuilt from the database
   (see [failed migration](failed-migration.md), "The story graph failed").

Never delete files inside the database's volume by hand.

When there is room, start what stopped: `docker compose up -d --wait`. PostgreSQL recovers on
its own once it can write again. Then take a backup (`./backup.sh`); the last one may have
failed.

## How to confirm

- `df -h` shows room to spare.
- `docker compose ps`: `postgres`, `neo4j`, `server` healthy; readiness `200 ready`.
- `./backup.sh` finishes and prints its checksums.

To keep it from coming back: schedule backups with the retention in
`deploy/hosted/README.md` ("Scheduled backups"), and run the retention command monthly.
