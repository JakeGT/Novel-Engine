# Audits on a schedule, correction plans and the author's voice (CON-011, CON-018)

An author can set an audit of a part of their story to run every few days (from the audit
dialog: "Run this audit on a schedule"), with the most each run may use. The job runner's loop
looks for schedules that are due about once a minute, between jobs when it is busy, and starts
at most five each time (any more wait for the next minute). It claims each in one conditional
update (so two servers never start the same run), and starts the audit as the author who set it
up, in the line of the story it belongs to. The most they set is a hard limit while the audit
runs too: if a scene, planned again as it is read, takes the estimate above it, the audit stops
there (the job fails as `refused`, keeping the scenes it finished) and nothing more is sent. A
run whose scenes are all unchanged since the last audit that *succeeded* is skipped and spends
nothing; one that failed or stopped part of the way is run again next time. Correction plans
are one model call each, started by the author with the cost shown first. Voice measures
(`voice_baselines` holds only which scenes an author left out of their usual) need no model at
all.

Tables: `audit_schedules`, `correction_plans`, `voice_baselines` (migration 0055).
`audit_schedules` and `voice_baselines` hold none of the author's words: the part of the plan,
the passes and limits, and plan ids. **`correction_plans` does hold some of the story**: each
step keeps the scene's title and, in `items[].cited`, the words of the findings it answers as
they were when the plan was made, which quote up to 160 characters of the scene (and of the
assistant's suggested wording); the summary and each step's change and why are the assistant's
words about the scene. Treat it like the scenes themselves: it is deleted with the story,
never written to logs, and not carried by an archive.

## How you notice

- Logs: `audit.schedule_saved` (every_days, passes: counts and fixed words only),
  `audit.schedule_ran` with `outcome` (one of the fixed words below), `audit.schedule_failed`
  (the kind of error only), `job.periodic.error` (the loop's periodic work failed; the kind of
  error only), `audit.corrections_planned` (steps, dropped), `voice.scene_read` (counts).
- An author says a scheduled audit "never runs": the audit dialog shows the last outcome as a
  sentence ("It would have used more than the most you set, so it did not run.").

Outcomes: `started`, `unchanged` (nothing written there changed), `nothing_written`,
`too_many_scenes`, `over_most` (the estimate is above the author's most for one run),
`allowance`, `needs_confirmation`, `no_provider`, `job_cap` (tried again in an hour),
`not_allowed` and `gone` (the schedule is turned off), `not_started` (tried again in an hour).

## What to check

1. **Schedules due and not starting.** List them, oldest due first:

   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT workspace_id, project_id, id, next_run_at, last_run_at, last_outcome, enabled FROM audit_schedules WHERE enabled AND next_run_at < now() ORDER BY next_run_at LIMIT 20;"
   ```

   A due schedule whose `next_run_at` stays in the past while the server is up means the job
   runner's loop is not running (readiness, [stuck jobs](stuck-jobs.md)). A run claimed by a
   server that stopped comes due again after 30 minutes on its own; if that server had already
   started the audit, the run claimed again gives back the same audit job (it is keyed by the
   schedule and the run after the last one recorded), so it is never paid for twice.
2. **What runs have come to across the install** (counts only):

   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT last_outcome, count(*) FROM audit_schedules GROUP BY last_outcome ORDER BY count(*) DESC;"
   ```

   Many `over_most` means authors set a most below what their audits cost; many `allowance`
   means allowances are tight ([spending](spend-spike.md)).
3. **Spending from schedules.** Each run is the author's own audit job (`jobs.kind =
   'story_audit'`, with `payload->>'schedule_id'` set) and is counted against their allowance
   like any other:

   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT workspace_id, count(*), sum(prompt_tokens + completion_tokens) FROM jobs WHERE kind = 'story_audit' AND payload ? 'schedule_id' AND created_at > now() - interval '7 days' GROUP BY workspace_id ORDER BY 3 DESC NULLS LAST LIMIT 20;"
   ```

## What to do

- **Stop every schedule in a story** (for example while investigating spending). The author
  can turn them back on from the audit dialog; nothing they ran is lost:

  ```sh
  docker compose exec -T postgres psql -U novel -d novel_engine -c \
    "UPDATE audit_schedules SET enabled = false, updated_at = now() WHERE workspace_id = '<workspace_id>' AND project_id = '<project_id>';"
  ```

- **A correction plan failed.** It is an ordinary job (`kind = 'correction_plan'`); see
  [stuck jobs](stuck-jobs.md) and [provider outage](provider-outage.md). Nothing in the story
  changed; the author can plan again.
- **Voice notes look wrong for a story.** They are worked out on read and never stored; an
  author can leave a scene out of their usual from the studio. To clear every scene left out:

  ```sh
  docker compose exec -T postgres psql -U novel -d novel_engine -c \
    "DELETE FROM voice_baselines WHERE workspace_id = '<workspace_id>' AND project_id = '<project_id>';"
  ```

## How to confirm

- `audit.schedule_ran` appears about once per due schedule, and the dialog's "Last time" line
  changes after the next due time.
- Deleting a story removes its schedules, plans and voice choice (`backend/adapters/canon_tables.py`).
  An archive carries only the voice choice (no schedules, and no correction plans with their
  quotes and titles): a restored story never starts spending on its own. A what-if that is set
  aside starts nothing: its schedules' next run is recorded as `gone` and they are turned off
  (the author can turn them on again once it is brought back).
