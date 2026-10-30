# Background work stuck or piling up

Drafts, checks, scene readings and plan cards from prose run as background work ("jobs") in a
worker inside the server. Each job is held under a 10-minute lease that the worker renews while
it works. A job whose worker stopped renewing is picked up again; one interrupted twice is given
up and marked failed with "The work was interrupted and could not be finished."

## How you notice

- Readiness says `200 degraded` with one of:
  - "The background worker is not running, so drafts, checks and readings wait until it starts
    again."
  - "Background work has waited too long to start; the worker may be stuck or too busy." (the
    oldest waiting job has waited more than five minutes)
  - "The list of waiting background work could not be read."
- Authors see a draft or check that says it is waiting and never moves.

## What to check

1. Readiness (see the [README](README.md)), for which of the three it is.
2. What is waiting and running, by kind (no story text):
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT kind, status, count(*), min(created_at) AS oldest, max(attempts) AS attempts
        FROM jobs WHERE status IN ('queued', 'running') GROUP BY kind, status;"
   ```
3. The server's log for the worker: `docker compose logs --since 1h server | grep -E 'job\.'`.
   `job.lease_lost` means another worker took a job over; `job.lease.renew_failed` means the
   database could not be reached while renewing.
4. Whether the model provider is the slow part: many `running` jobs of kind `scene_draft` with
   `ai.generate` errors in the log point to [the provider](provider-outage.md), not the worker.

## What to do

- **The worker is not running.** It starts with the server. Restart the server:
  `docker compose restart server`. Work that was waiting is kept and starts again.
- **Jobs wait too long.** One worker runs one job at a time, so a long queue behind slow model
  calls is expected to drain on its own. If the oldest `running` job is older than its lease
  (10 minutes) and nothing moves, restart the server; the job is picked up again (or given up
  after a second interruption) and the queue moves.
- **The list could not be read.** The database is slow or unreachable: check
  `docker compose ps postgres` and its log, and [disk full](disk-full.md).
- An author can cancel their own waiting work from the studio; the operator never needs to edit
  the `jobs` table by hand.

## How to confirm

- Readiness is back to `200 ready` (or `degraded` only for the search store).
- Running the query in step 2 again shows the oldest waiting time going down.
- An author's waiting draft or check finishes, or shows a plain failure they can retry.
