# Failed archive import

A story archive (one story with its history, texts, pictures, what-ifs and conversations) is
restored into an empty project with the archive command. Every import is recorded in the
project's import list with how far it got. An import that stops part way leaves the project
incomplete and says so; it can be removed and tried again. Removing never touches work an
author did in that project: it is refused if the project holds anything the import did not
write.

## How you notice

- The import command ends with `import FAILED (<kind of error>): …` and
  `the project is not a successful restore; retry or use --remove-import`.
- Authors see a banner on the story: "Restoring a backup into this project stopped part-way, so
  this project is incomplete. An owner should retry the restore or remove it." (or, while it is
  still running, "A backup is being restored into this project…").

## What to check

Commands run from `deploy/hosted/` in the checkout.

1. The project's import list (the operator acts as a user id, `usr_…`, who owns the workspace;
   `docker compose exec server python -m backend.devtools.provision --list-users` lists users):
   ```sh
   docker compose exec server python -m backend.devtools.archive \
     --target-workspace <id> --target-project <id> --imports --actor <usr_…>
   ```
   Each line shows the import id, its state (`importing`, `completed`, `failed`, `removed`), the
   stage it reached, versions applied out of the total, objects, and the error kind.
2. Why it failed. A malformed or damaged archive is refused before anything is written, in plain
   words. A failure part way is usually the database or disk ([disk full](disk-full.md)), or the
   server being stopped during the import.
3. That the archive file itself is whole, if it was copied from elsewhere: compare its size or
   checksum with the source.

## What to do

1. Remove what the failed import wrote:
   ```sh
   docker compose exec server python -m backend.devtools.archive \
     --target-workspace <id> --target-project <id> --remove-import <import id> --actor <usr_…>
   ```
   The import's state becomes `removed`. This is refused for a completed import, and for a
   project that holds anything else, so authored work is never removed. Removing also clears that
   story's studio work (waiting drafts, voice notes, dismissed notes), which in a project that only
   held the failed import is the import's own.
2. Fix the cause (space, a complete copy of the archive, a server left running).
3. Import again, with the archive file (here `story-archive.json`, copied into `deploy/hosted/`)
   lent to a one-off server container (read only):
   ```sh
   docker compose run --rm --no-deps -v "$PWD/story-archive.json:/import/archive.json:ro" \
     server python -m backend.devtools.archive \
     --target-workspace <id> --target-project <id> --import /import/archive.json --actor <usr_…>
   ```
   (`docker compose cp` cannot be used: the server's own disk is read only and its `/tmp` is a
   memory space that copying does not reach.)

## How to confirm

- The import command prints `imported … ; projection rebuilt: True; verified: True; import id: …`.
- `--imports` shows the newest import `completed`.
- The banner is gone from the story, and the story checks out:
  `docker compose exec server python -m backend.devtools.project --workspace <id> --project <id> --verify`
  prints `equivalence OK`.
