# Pictures not prepared, failing, or filling the disk

Every picture an author adds is prepared once in the background (a `media_process` job): a
separate, short-lived worker process with memory, CPU and time limits decodes it and writes a
256px thumbnail and a 1024px copy (WebP, with no metadata) and records its size. Until that is
done, and if it fails, lists show the full picture; the picture itself is never changed or lost.
See ADR-010 (amendment 2026-10-20) and `backend/adapters/image_processor.py`.

Commands below are for the hosted stack (`docker compose exec server …`); on a local install use
`uv run --locked python -m backend.devtools.media …` or `make media MEDIA_ARGS='…'`. Every
command prints counts only: never a picture, its name or a stored file's key.

## The worker's sandbox

Before it decodes anything the worker also shuts itself in (AST-006, ADR-010 amendment 2026-10-21), with
whatever the machine offers: no new privileges; a network of its own where allowed (usually not
inside a container); Landlock (Linux 5.13 or later) with no path allowed, so it can open, make,
remove or run no file; and a seccomp filter that refuses opening files, sockets, running
programs, starting processes, tracing or signalling another process (every signal call and
pidfd), and mounting. Standard input and output were opened before
and keep working. How far it got is in `/health/capabilities` under
`capabilities.pictures.sandbox` (asked once, from a worker started for that alone):

```sh
docker compose exec -T server python -c "
import json, urllib.request as u
print(json.dumps(json.load(u.urlopen('http://127.0.0.1:8000/health/capabilities', timeout=10))['capabilities']['pictures']['sandbox'], indent=2))"
```

- `level`: `full` (Landlock and seccomp both on), `partial` (one of them), `limits_only` (only the
  memory, time and file-size limits), or `unknown` (the check itself could not run).
- `layers` says each one and why one is off (`not_supported`: the kernel lacks it;
  `refused`: something in front, such as a container's own seccomp profile, would not allow it;
  `unknown_architecture`: the filter is written for x86-64 and arm64 only).
- `refused` says what a sandboxed worker was refused: `read_files`, `write_files`, `network`,
  `new_process`, each `true` when refused.
- `reason`: a plain sentence when the level is less than full.

Each picture's log line (`media.process.decode`) carries `sandbox=<level>` too, and a worker
check that finds less than full logs `media.process.sandbox` at warning level.

`MEDIA_WORKER_SANDBOX` chooses: `best` (the default: use what is there and say so), `require`
(prepare no picture unless Landlock and seccomp are both on; each fails as "Pictures cannot be
prepared on this installation right now", and can be tried again once fixed) or `off`. Docker's
default seccomp profile allows Landlock and seccomp, so the hosted stack gets `full` on a kernel
with Landlock enabled (`cat /sys/kernel/security/lsm` lists `landlock`); it refuses the network
namespace, which the seccomp layer covers. Not covered: asking whether a path exists (`stat`),
which neither Landlock nor this filter refuses, and anything in the kernel itself.

## How you notice

- Readiness (see the [README](README.md)) says `200 degraded` with "Pictures have waited too long
  to be prepared, so lists show the full pictures for now." (the oldest waiting picture has waited
  more than 15 minutes), or "How far picture preparation has got could not be read."
- With details shown, `checks.media_processing` gives `waiting`, `oldest_waiting_seconds` and
  `failed`. Metrics: `media_processing_total{outcome,failure_code}`, `media_processing_seconds`,
  `media_uploads_refused_total{reason="storage_full"}`.
- An author sees "Could not prepare" on the media page, or is told "There is no room left for this
  picture".
- Logs: `media.process.decode` (outcome and milliseconds per picture), `media.process.failed`
  (the failure code only), `media.queue_failed`, `media.reconcile`.

## What to check

1. Whether this server can prepare pictures at all:
   ```sh
   docker compose exec server python -m backend.devtools.media status
   ```
   `capability.pillow_available` must be `true` with `png`, `jpeg` and `webp` all `true`, and
   `enabled` true (`MEDIA_PROCESSING_ENABLED`). `processing` shows waiting, oldest wait, failed.
2. Pictures waiting share the background queue with drafts and checks (one job at a time per
   server). If drafts are slow, pictures wait behind them: see
   [stuck jobs](stuck-jobs.md) first.
3. Why pictures failed, by code (no names, no content):
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT status, failure_code, count(*) FROM media_processing GROUP BY 1, 2 ORDER BY 3 DESC;"
   ```
   Codes: `too_large`, `unsupported_type`, `too_many_pixels`, `damaged`, `out_of_memory` (the
   file is the problem; trying again will not help), `took_too_long`, `processor_unavailable`,
   `processor_failed` (may help to try again), `missing_original` (the stored file is gone; see
   [restore](restore.md)).

## What to do

- **Pillow is missing or cannot write WebP.** The image is broken: rebuild it from the lock file
  (`uv sync --locked`); Pillow 12.3.0 or later is a core dependency. Then queue again (below).
- **Many `took_too_long` or `out_of_memory` on ordinary photos.** The worker limits are too low
  for this machine: raise `MEDIA_WORKER_TIMEOUT_SECONDS`, `MEDIA_WORKER_CPU_SECONDS` or
  `MEDIA_WORKER_MEMORY_BYTES` (defaults 30 s, 20 s, 1 GiB) and restart. Do not raise
  `MEDIA_MAX_PIXELS` (40 million) or `MEDIA_MAX_DIMENSION` (12,000) to let a single file through:
  they are the decompression-bomb guard.
- **Queue pictures that were never prepared** (added before processing existed, after the
  processor changed, or left waiting by a worker that died; failed ones too with the flag):
  ```sh
  docker compose exec server python -m backend.devtools.media backfill
  docker compose exec server python -m backend.devtools.media backfill --include-failed
  ```
  Each is queued as the person who added it, and keyed by the picture's checksum and the
  processor version, so running it twice queues nothing twice. `--now` prepares them in the
  command's own process instead of the queue (for a server whose worker is off).
- **One picture failed and the author wants it tried again.** They can press "Try again" on the
  media page; it is the same as `backfill --include-failed` for that picture.
- **A file that should not be opened at all (quarantine).** Retire the picture (the author removes
  it, or an administrator calls `POST /api/v1/projects/{id}/assets/{asset}/retire`): it is no
  longer served or prepared, and `copies --apply` below removes its small copies. The original
  stays in storage, for the history, until the story is deleted.

## Storage room and leftovers

- **"No room left" for an author.** Each workspace may store `MEDIA_WORKSPACE_QUOTA_BYTES`
  (default 2 GiB; 0 is no limit) of pictures and their copies. Retired pictures still count until
  their story is deleted. Either the author removes pictures they no longer need, or raise the
  limit and restart.
- **Files nothing refers to** (an upload whose save was refused, an interrupted import, copies
  left by an older archive). Count, then remove; only files older than
  `MEDIA_ORPHAN_GRACE_HOURS` (default 24) are touched, so an upload still on its way is safe:
  ```sh
  ./backup.sh
  docker compose exec server python -m backend.devtools.media reconcile
  docker compose exec server python -m backend.devtools.media reconcile --apply
  ```
- **Small copies of retired pictures.**
  ```sh
  docker compose exec server python -m backend.devtools.media copies          # count
  docker compose exec server python -m backend.devtools.media copies --apply  # remove
  ```
- `make retention` (and `python -m backend.devtools.retention`) runs both of the above after the
  other retention rules, counting with `--dry-run`; `--skip-media` leaves pictures alone.

## Uploads

A picture upload is read as it streams in: refused at once when its declared length is over
8 MB, as soon as more than that has arrived, or when its first bytes are not a PNG, JPEG or WebP
file; and refused (408, "took too long to arrive") when the whole upload has not arrived within
two minutes (`UPLOAD_DEADLINE_SECONDS` in `backend/services/media_service.py`), so a sender
trickling bytes cannot hold a connection open. A proxy in front should still enforce its own
read timeout. Refusals count in `media_uploads_refused_total{reason}` (`too_large`, `too_slow`,
`storage_full`). A `Content-Digest` (or `Repr-Digest`) with a `sha-256` member must match; one
with only other methods (`sha-512`, say) is not checked, and the answer says so
(`received.digest` is `not_checked`, with a note), so the sender can compare `received.sha256`.

## Archives

Small copies are never put in an archive (the archive says so under `derived` and `left_out`);
a restore queues every restored picture to be prepared again, as the person who restored it.
Parts of pictures an author marked (regions) are in the archive under `picture_regions`.

## How to confirm it worked

- `media status` shows `waiting` falling to 0 and `failed` only for files that are themselves bad.
- Readiness no longer says pictures have waited too long.
- On the media page the picture shows its small copy and its size in pixels.

Checked against the code on 2026-10-20. The service calls behind `status`, `backfill`,
`reconcile` and `copies` ran in the automated tests against a disposable PostgreSQL database
(`tests/integration/test_media_processing_store.py`); the command line itself needs the database
migrated to `0046_media_variants` or later, and has not yet been run on the hosted stack.

## Deleting one picture's file before its story (AST-008)

An owner or administrator can delete a picture's file from the media page ("Delete this
picture's file") once the picture is shown on no entry or scene in any line of the story; taking
it off one is an ordinary reviewed change, and a scene taken out of the plan no longer counts.
The picture is marked deleted first (from then on it cannot be shown, opened or prepared), where
it is shown is checked again, and only then are the original, its smaller copies and its search
rows removed. A stored file another picture still uses (an archive restore can share one between
two pictures with the same bytes) is kept for that picture, and is counted once toward the room
used. The record stays as the tombstone (checksum, size, type, `deleted_at`) with
`original_deleted` and `asset_deleted` in the picture's activity, naming who did it. The room it
took is free at once. A deletion interrupted part way is finished by asking again
(`POST /api/v1/projects/<project>/assets/<asset>/delete`). Its reviewed
descriptions are left out of later archives.

There is no undo. Only an archive saved before the deletion still holds the file, and only a
restore of it into a new, empty story brings the picture back (there). It cannot be restored
over the story it came from: that story has its own history since, so the restore is refused.
An archive saved after the deletion restores too: the changes that once showed the picture are
checked against the picture as it was when they were made, and the restored picture keeps its
tombstone (deleted, with its checksum, size, type and `deleted_at`) and no file, because the
archive holds none. A retired picture restores the same way: its history and links come back,
and it stays retired (not shown, opened or prepared). Checked on 2026-10-21 against the fakes
(`tests/contract/test_picture_deletion.py`) and PostgreSQL
(`tests/integration/test_archive_ended_pictures.py`).

To see what was deleted:

```sh
docker compose exec -T postgres psql -U novel -d novel -c \
  "SELECT asset_id, id, byte_length, checksum, deleted_at FROM media_asset_versions WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT 20"
```

## Two-step uploads and deleted pictures' notes (batch 9)

- A two-step upload (`POST /uploads`, `PUT` the bytes, `POST …/finish`) keeps its staged file until
  it is finished or its hour is up. The job runner clears expired uploads about every five minutes
  (`MediaService.expire_uploads`, log event `media.uploads_expired`, counts only); rows of finished
  and expired uploads are forgotten a week later. A staged file left by a crash between storing and
  recording it is removed by `make retention` after the orphan grace period.
- `media_uploads_refused_total{reason="not_as_promised"}` counts uploads whose bytes did not match
  the size or SHA-256 promised. A rise usually means a client computing the digest of the wrong
  bytes, not an attack: nothing is kept either way.
- Deleting a picture's files also removes what a model said about it; the picture's lifecycle shows
  a `notes_removed` event with how many (`GET /assets/{id}/lifecycle`).
