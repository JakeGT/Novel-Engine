# Checks kept, worked out only where something changed (CON-007, CON-001, CON-013, CON-016)

A scene's checks that need no model are worked out only where what they read changed: each check
keeps its result against a fingerprint of its inputs (`check_cache`), and each save keeps a
record (`consistency_runs`) saying which checks were worked out again. Dismissals keep a digest of
what the finding said (`finding_dismissals.depends_on`); advisory findings on a proposal in review
can be dismissed for that proposal (`review_dismissals`). A long scene's continuity check with the
assistant may be sent in groups. None of this ever blocks a save or a commit.

`check_cache` is derived: it holds digests and what each check found (codes, messages, offsets),
never the words, keeps the newest six results per scene and check, and can always be emptied.

## How you notice

- Logs: `checks.incremental` (`ran`, `reused`, `findings`: counts only) on every scene check;
  `checks.run_recorded` with the same counts; `checks.run_not_recorded` (the kind of error only)
  when a record could not be kept (the save itself has happened); `checks.groups_skipped` when the
  continuity pass could not be grouped and was sent whole; `review.finding_dismissed` (code and
  reason kind only).
- An author says a finding they fixed is still shown, or a check "never changes": look for
  `checks.incremental` with `ran: 0` after the change they made.
- In "Check history" each record says which checks were worked out again; a check with the
  assistant says how the continuity pass was sent (whole, or in groups and why).

## What to check

1. **A kept result looks wrong** (a check that should find something does not, after a change the
   fingerprints did not see). Every input a check reads should be in its fingerprint; a change to
   the code of a check must raise `CHECKS_VERSION` in `backend/domain/check_fingerprints.py`. If a
   deployment changed a check without raising it, results kept before are used until the inputs
   change. Empty the cache (below); nothing is lost.
2. **The cache is large.** It is bounded per scene and check; count it:

   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT count(*), count(DISTINCT (workspace_id, project_id, plan_id)) FROM check_cache;"
   ```

3. **A continuity check costs more than expected.** A pass sent in groups reads some passages more
   than once; the estimate the author confirmed already counted the groups. The thresholds
   (`GROUP_ABOVE_TOKENS`, `GROUP_BUDGET`, `MAX_GROUP_CALLS` in `backend/services/scene_checks.py`)
   are code, not settings.

## What to do

- **Empty the check cache** (safe at any time; the next check of each scene works everything out
  once and keeps it again):

  ```sh
  docker compose exec -T postgres psql -U novel -d novel_engine -c "TRUNCATE check_cache;"
  ```

- **Never** delete rows from `consistency_runs`, `finding_dismissals` or `review_dismissals` to fix
  a display: they are the author's history and decisions. A dismissal is undone from the app
  (Undo / Bring back), which keeps the record withdrawn.

## How to confirm

Open a scene in the studio, save it unchanged, and look at "Check history": the newest record
says nothing it reads had changed (or, after emptying the cache, that every check was worked out).
`checks.incremental` in the logs shows `ran: 0` on a second read of an unchanged scene.

Checked against the code 2026-10-20; the SQL above was run against a development database only.
