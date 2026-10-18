# Recovery runbooks (OPS-004)

What to do when something goes wrong on a running Novel Engine. Each runbook says how you
notice, what to check, what to do, and how to confirm it worked. They are written for the
hosted stack in `deploy/hosted/`: run every command from that folder (`cd deploy/hosted` in the
checkout), so `./backup.sh`, `backups/` and `secrets/` are the ones there. A local install uses
the same commands with `uv run --locked` in place of `docker compose exec server`.

| Runbook | When |
|---|---|
| [Failed migration on start](failed-migration.md) | The server will not start after an upgrade |
| [Background work stuck or piling up](stuck-jobs.md) | Readiness says `degraded`; drafts, checks or readings never finish |
| [Story graph behind or wrong](graph-projection.md) | The graph is out of date or disagrees with the story |
| [AI provider outage or key revoked](provider-outage.md) | Drafts and checks fail with "The AI provider did not answer" |
| [Restoring from a backup](restore.md) | Data was lost or damaged and must be put back |
| [Failed archive import](failed-import.md) | Restoring a story archive stopped part way |
| [Disk full](disk-full.md) | Saves fail, the database stops, backups fail |
| [Rotating the credential key](credential-key-rotation.md) | The key that protects stored provider keys must change |
| [Connected assistants and access keys](connected-assistants.md) | Connecting an assistant through MCP; a key leaked; an assistant is refused |

Every command was checked against the code (2026-10-18). The read-only ones (the migration
check, the story check, the SQL queries, the retention dry run, diagnostics, the readiness
request and each command's `--help`) were run on a development machine, outside Docker Compose.
None has yet been rehearsed on the live site or a clean host; record the first real use of each
in `plan/phase-2-evidence.md`.

## Things every runbook uses

**Readiness.** `/health/ready` is not reachable from outside (Caddy hides it), so ask from inside
the server container:

```sh
docker compose exec -T server python -c "
import urllib.request as u, urllib.error as e
try:
    r = u.urlopen('http://127.0.0.1:8000/health/ready', timeout=5); print(r.status, r.read().decode())
except e.HTTPError as x:
    print(x.code, x.read().decode())"
```

`200 ready` is healthy. `200 degraded` means an optional part (the background worker or the
search store) is down and `reasons` says which, in plain words. `503 unavailable` means a
required store (the database, the story graph, or either one's layout) is down. On a hosted site
the counts are shown only with the metrics token: add
`headers={'Authorization': 'Bearer <METRICS_TOKEN>'}` through a `u.Request`, if one is set.

**Logs.** `docker compose logs --since 1h server` (also `migrate`, `postgres`, `neo4j`). Logs carry
event names, counts, timings and kinds of error, never an author's words.

**Finding a story's ids.** Ask the author for the story's address: the part after `/projects/` is
the project id. Its workspace:

```sh
docker compose exec -T postgres psql -U novel -d novel_engine -c \
  "SELECT workspace_id, id, created_at FROM projects WHERE id = '<project id>';"
```

**Diagnostics bundle.** `docker compose exec server python -m backend.devtools.diagnostics`
prints versions and which features are switched on, never secrets or story text.

**Taking a backup first.** Before any step that replaces or removes data, run `./backup.sh`.
