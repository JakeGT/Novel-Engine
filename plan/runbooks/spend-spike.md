# Assistant spending suddenly high

The assistant is paid for with each workspace's own provider key: a spike costs that
workspace's owner, not the platform. Every model call is checked against limits before it is
sent and recorded afterwards (`provider_usage`, one row per call, no story text). This runbook
finds who and what is spending, and stops it.

## How you notice

- An owner reports a high bill or their provider warns them.
- Authors see "You have used up your assistant allowance for now" sooner than expected.
- The metrics show many model calls (`/health/metrics`, with the metrics token).

## What to check

1. **Who is spending, in the last day** (tokens, calls and any cost the provider reported):
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT workspace_id, actor_user_id, count(*) AS calls,
             sum(prompt_tokens + completion_tokens) AS tokens,
             round(sum(coalesce(provider_cost_usd, 0))::numeric, 2) AS usd
      FROM provider_usage WHERE created_at > now() - interval '1 day'
      GROUP BY 1, 2 ORDER BY tokens DESC LIMIT 20;"
   ```
2. **On what**: `requested_route` and `model` for the top one, by hour:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT date_trunc('hour', created_at) AS hour, requested_route, model, count(*),
             sum(prompt_tokens + completion_tokens) AS tokens
      FROM provider_usage WHERE workspace_id = '<workspace id>'
        AND created_at > now() - interval '1 day'
      GROUP BY 1, 2, 3 ORDER BY 1 DESC, tokens DESC;"
   ```
3. **Background work behind it**: automatic checks after saving, audits, readings:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT kind, status, count(*), sum(prompt_tokens + completion_tokens) AS tokens
      FROM jobs WHERE workspace_id = '<workspace id>' AND created_at > now() - interval '1 day'
      GROUP BY 1, 2 ORDER BY tokens DESC NULLS LAST;"
   ```
   Many `scene_review` jobs usually mean a story's checking is set to "thorough" (checks after
   every save); many `story_audit` jobs, an audit schedule (Audit page).
4. **An access key or connected assistant** making calls: [connected-assistants.md](connected-assistants.md).
5. **The settings**: `PROVIDER_PER_JOB_TOKEN_LIMIT`, `PROVIDER_PERIOD_TOKEN_LIMIT`,
   `PROVIDER_PERIOD_SECONDS` and the cost limits in the server's environment (0 means none);
   each workspace's own daily allowance and "ask before a large request" on its Assistant page.

## What to do

In order of how much they stop:

1. **Ask the owner** to lower the workspace's daily allowance and set "ask before a large
   request" on the Assistant page, and to set the story's checking back to "only when asked" if
   automatic checks are the cause. Authors keep working by hand meanwhile.
2. **Stop work already queued**: authors cancel their own waiting work in the studio; an audit
   schedule is removed on the Audit page. See [stuck-jobs.md](stuck-jobs.md) for work that will
   not stop.
3. **Lower the server's ceilings** for everyone (environment, then `docker compose up -d
   --wait`): `PROVIDER_PERIOD_TOKEN_LIMIT` (per person, per period) and
   `PROVIDER_PER_JOB_TOKEN_LIMIT`. Set `PROVIDER_PERIOD_COST_LIMIT` too when the provider reports
   cost (OpenRouter does).
4. **Cut it off**: the owner revokes the workspace's key on the Assistant page (or at the
   provider). Every assistant feature then says plainly that no key is connected; nothing by
   hand is affected.
5. **If it looks like abuse** (calls nobody in the workspace made, an unknown access key, a
   leaked key): [suspected-exposure.md](suspected-exposure.md).

## How to confirm

- Query 1 run again an hour later shows the workspace's calls back to normal.
- The owner's provider dashboard agrees (the provider's numbers are the bill; ours are counts).

The queries were run on a development machine; none has been used on the live site yet.
