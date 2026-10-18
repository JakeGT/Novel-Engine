# Restoring from a backup

`deploy/hosted/backup.sh` writes the database and the stored objects (texts' pictures, archive
exports) with a checksum list (`MANIFEST`). The story graph is not backed up: it is rebuilt
from the database. The credential key is never in a backup. Scheduled backups and how many are
kept: `deploy/hosted/README.md`, "Scheduled backups".

## How you notice

- Stories or texts are missing or damaged, and the history page cannot bring them back (a
  change an author regrets is undone from history or review, not by restoring).
- The database will not start, or `docker compose exec server python -m backend.devtools.verify`
  reports problems that are not about the graph (see [story graph behind or wrong](graph-projection.md)).
- The host or its disk was lost.

## What to check

Commands run from `deploy/hosted/` in the checkout.

1. Which backup to use: `ls backups/` (newest last). A folder without a `MANIFEST`, or whose
   `MANIFEST` does not list both `novel_engine.dump` and `objects.tar`, is a backup that failed
   part way (or one still running); do not use it. `restore.sh` refuses it.
2. That the backup is whole, without changing anything: `restore.sh` checks every file against
   the `MANIFEST` before it touches the stack and stops at the first mismatch. To check only,
   answer anything but `restore` at its question.
3. That you have the credential key that was in use when the backup was taken
   (`secrets/credential_kek`, and any older keys listed for decryption; see
   [rotating the credential key](credential-key-rotation.md)). Without it, everything comes back
   except stored provider keys, which owners must connect again.
4. Everything written after the backup will be lost. If the current state still matters, take a
   fresh `./backup.sh` first.

## What to do

Follow the restore drill in `deploy/hosted/README.md` ("Restore drill"); in short:

```sh
# in deploy/hosted/
./backup.sh                                       # if the current state matters
./restore.sh backups/novel-engine-<timestamp>     # type: restore
```

It stops the server, replaces the database and the stored objects, and starts the server again
(migrations run first). Then rebuild each story's graph, because the graph may hold changes made
after the backup:

```sh
docker compose exec server python -m backend.devtools.project --workspace <id> --project <id> --rebuild
```

To list every story's ids: `docker compose exec -T postgres psql -U novel -d novel_engine -c
"SELECT workspace_id, id FROM projects ORDER BY created_at;"`.

On a new host: install Docker and the checkout, put the old `secrets/` back (above all
`credential_kek`) or run `./init-secrets.sh` and accept that stored provider keys must be
connected again, build the image, `docker compose up -d --wait`, then restore as above.

## How to confirm

- Readiness (see the [README](README.md)) is `200 ready`.
- `docker compose exec server python -m backend.devtools.verify` prints
  `canonical verification OK`.
- For each story: `... backend.devtools.project --workspace <id> --project <id> --verify` prints
  `equivalence OK`.
- An author opens a story and finds what they had at the time of the backup, including texts,
  scenes and pictures.

The restore has not yet been rehearsed on a clean host (an owner item in
`plan/phase-2-todo.md`).
