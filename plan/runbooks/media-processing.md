# Pictures not prepared, failing, or filling the disk

Every picture an author adds is prepared once in the background (a `media_process` job): a
separate, short-lived worker process with memory, CPU and time limits decodes it and writes a
256px thumbnail and a 1024px copy (WebP, with no metadata) and records its size. Until that is
done, and if it fails, lists show the full picture; the picture itself is never changed or lost.
See ADR-010 (amendment 2026-10-20) and `backend/adapters/image_processor.py`.

Commands below are for the hosted stack (`docker compose exec server …`); on a local install use
`uv run --locked python -m backend.devtools.media …` or `make media MEDIA_ARGS='…'`. Every
command prints counts only: never a picture, its name or a stored file's key.

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
