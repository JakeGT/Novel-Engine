# Suspected exposure of provider keys or of a workspace's stories

For when someone may have seen what they should not: a provider key, the credential key, a
backup, the database, or one workspace's stories reaching another person. Act first to stop
further exposure, then find out how far it went, then tell the people affected. Write down
what you do and when, as you go.

## What could have been exposed, and what protects it

| Thing | Where it lives | What a copy gives someone |
|---|---|---|
| A workspace's provider key | `credentials`, encrypted with a data key locked by the credential key | Nothing without the credential key as well |
| The credential key | `secrets/credential_kek` on the host, the password manager | With a copy of the database (or a backup): every stored provider key |
| Stories | PostgreSQL, stored objects, backups (and their off-host copies) | Everything the author wrote |
| Access keys (connected assistants) | Stored as hashes only | A leaked key itself works until revoked: [connected-assistants.md](connected-assistants.md) |
| Sign-in sessions | Hashes in `sessions`; the cookie on the person's device | A stolen cookie works until it expires or is removed |
| The identity provider's client secret | `secrets/oidc_client_secret` | Pretending to be this site to the provider: [identity-provider.md](identity-provider.md) |

Logs never carry story text, keys or secrets, so a leaked log is ids, counts and timings.

## A workspace's provider key may have been seen

1. The owner revokes it at the provider, makes a new one, and connects it on the Assistant page
   (the old row is revoked here when the new one is stored).
2. Check what was spent with it: [spend-spike.md](spend-spike.md), query 1, and the provider's
   own usage page.

## The credential key, the database, or a backup may have been copied

1. **Contain.** Find how (a lost disk, a former operator, a copy left somewhere) and close it:
   remove the copy, revoke the access, change the host's own passwords and SSH keys if the
   host itself is in doubt.
2. **Rotate the credential key** and move every stored key to it at once:
   [credential-key-rotation.md](credential-key-rotation.md), steps 1–7, then retire the old key
   as soon as `rekey verify` allows. Rotation alone does not help if the database copy and the
   old key were taken together: anyone holding both can open the keys as they were.
3. **So, if the credential key and a copy of the data may both be out**, every workspace owner
   must also revoke their provider key at the provider and connect a new one. Tell them (below).
4. **Database passwords.** If the database itself was reachable: change `secrets/postgres_password`
   (and the role's password in PostgreSQL), and the Neo4j password, then `docker compose up -d
   --wait`.
5. **Sessions.** End every sign-in, so stolen cookies stop working (everyone signs in again):
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c "DELETE FROM sessions;"
   ```

## One workspace's stories may have reached someone else

1. **Contain.** Remove the person from the workspace (People page) and end their sessions
   ([identity-provider.md](identity-provider.md)); revoke any access key they used
   ([connected-assistants.md](connected-assistants.md)).
2. **How far.** The workspace's audit log names memberships, invitations, keys and deletions;
   the story history names every change, by person. The app does not log each read; the API
   server's access lines name each request's path and status, never its content:
   `docker compose logs --since 72h server | grep '<project id>'`.
3. **If it was the software** (one workspace's request returning another's data): stop the
   server (`docker compose stop server`), keep the logs (`docker compose logs server
   >exposure-$(date -u +%Y%m%dT%H%M%SZ).log`), take a backup, and treat it as a security defect:
   it needs a fix and a test before the server goes back up. Workspace and project scope are
   checked at every store (AGENTS.md, rule 11); the tests under `tests/contract/test_workspace_scope.py`
   are where a new case belongs.

## Telling people

Tell the owners of every workspace affected, in plain words, as soon as you know enough to say
what happened, what may have been seen, what you have done, and what they must do (for example,
revoke and replace their provider key). Do not wait for the full picture to say the first part.
If personal data of people in a jurisdiction with breach-notice rules may be involved, the
operator may have to notify a regulator within a fixed time (72 hours under the GDPR); that is
the owner's decision and record.

## How to confirm

- `docker compose exec -T server python -m backend.devtools.rekey verify` shows every active
  key on the new version, and owners report their assistant working with new provider keys.
- The People pages list only expected members; no unknown access keys are active.
- A write-up exists: what happened, when it was found, what was done, who was told.

Nothing here has been rehearsed on the live site. The commands were checked on a development
machine and the SQL against the current schema (`tests/integration/test_runbook_queries.py`).
