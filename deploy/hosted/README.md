# Hosted deployment (novel-engine.jaketalling.com)

One image is the whole site: the API, the migration CLI, and the built web UI, which the API
serves. This stack follows the jaketalling platform's app contract (`apps/README.md` in the
platform repository): no host ports, only the server on `platform-proxy`, file-mounted secrets,
one exact Caddy route, one OIDC client.

Status: deployed on 2026-10-10. The owner signed in through Authentik and created canon on the live site.

| Piece | Where |
|---|---|
| Server | `novel-engine-server:8000` on `platform-proxy` and the internal `novel-engine-data` |
| Stores | This stack's own Postgres 17 + pgvector and Neo4j, on `novel-engine-data` only, no ports |
| Identity | Authentik application `novel-engine`; callback `https://novel-engine.jaketalling.com/auth/callback`, post-logout `https://novel-engine.jaketalling.com/` |
| Secrets | `./secrets/` (git-ignored, directory mode 0700), created by `./init-secrets.sh` |
| Backup | `./backup.sh`: Postgres dump + stored objects. Neo4j is rebuilt from Postgres. Nightly with `systemd/` or cron, old ones removed by `./prune-backups.sh` (see "Scheduled backups"); `./copy-off.sh` copies the newest one off the host (an example to adapt) |
| Restore | `./restore.sh backups/novel-engine-<timestamp>`: checks every file against the manifest first, asks before replacing anything |

## 1. Build the image

```sh
cd ~/Projects/"Novel Engine" && git checkout <commit>
docker build -t novel-engine:<commit> .
```

## 2. Secrets and settings

```sh
cd deploy/hosted
./init-secrets.sh            # copies the OIDC client id/secret from the checkout's .env, generates the rest
sed -i 's/^NOVEL_ENGINE_VERSION=.*/NOVEL_ENGINE_VERSION=<commit>/' .env
```

`secrets/credential_kek` encrypts every stored provider key. **Keep a copy in the password
manager**; without it stored keys cannot be decrypted and must be entered again.

### Error reporting (Sentry), optional

`init-secrets.sh` creates `secrets/sentry_dsn_backend` and `secrets/sentry_dsn_web`, filled from
the checkout's `.env` when it has them and empty otherwise. An empty file switches that side off.
To set or change one later, replace the file's contents (mode 0400) and recreate the server:
`docker compose up -d --wait`. Use two Sentry projects, one for the server and one for the
browser, so their errors and their source maps stay apart.

- The server reports under the release `novel-engine@<NOVEL_ENGINE_VERSION>`; the page is handed
  the browser's DSN and the same release when it is served. Nothing is baked into the image.
- Check it: `docker compose exec server python -m backend.core.observability` sends one test
  event and says whether Sentry accepted it. `/health/capabilities` (private network only) shows
  `sentry_backend`, `sentry_web` and the release.
- Events are scrubbed before they leave on both sides. In each Sentry project also turn on
  **Settings → Security & Privacy → Prevent Storing of IP Addresses**: an address is recorded by
  Sentry on receipt, which no scrubbing here can prevent.
- Source maps, so browser errors show real code: put an auth token in `secrets/sentry_auth_token`,
  add `SENTRY_ORG` and `SENTRY_PROJECT_WEB` to `.env`, then `./upload-sourcemaps.sh <commit>`
  after building. The maps are never served by the site. This script has not yet been run against
  a real Sentry project.

## 3. Start

```sh
docker compose up -d --wait
docker compose ps -a          # migrate exited 0; server, postgres, neo4j healthy
```

## 4. Caddy route (platform repository, `caddy/Caddyfile`)

```caddyfile
# Novel Engine (deploy/hosted in its repository). Only /health/live is public; readiness,
# capabilities and metrics describe internals and stay on the private network.
http://novel-engine.jaketalling.com {
	log {
		output stdout
		format json
	}
	@internal path /health/ready /health/capabilities /health/metrics
	respond @internal "not found" 404
	request_body {
		max_size 10MB
	}
	reverse_proxy novel-engine-server:8000 {
		import upstream_headers
	}
}
```

Validate, then `docker compose restart caddy` (the admin API is off, so there is no live reload;
this briefly interrupts every site behind Caddy).

## 5. Check the Authentik application

The provider must be confidential, sign with RS256, and list exactly the two URIs in the table
above with strict matching; the application must be gated to `platform-admin` and `app-users`.
Its client id is the generated one, not the slug, so `scripts/verify-identity.sh`'s
`check_oidc_app` (which looks providers up by `client_id == slug`) does not cover it as written.

## 6. Publish, then do the sign-in round trip straight away

A browser sign-in cannot be done privately on this platform: Caddy publishes no host port, TLS
exists only at Cloudflare, the session cookies are `Secure`, and Authentik redirects back to the
public `https://` address. So the route goes live first and the round trip follows immediately.
What is exposed in between is listed at the end of this file: the UI shell, `/health/live`, and a
redirect to Authentik, which admits only `platform-admin` and `app-users`.

Add `novel-engine.jaketalling.com -> http://caddy:80` in the Cloudflare tunnel dashboard, check
`curl -fsS https://novel-engine.jaketalling.com/health/live`, then with a browser:

1. Open the site, sign in through Authentik, land back signed in (`/auth/me` shows your user).
2. `docker compose exec server python -m backend.devtools.provision --list-users` shows you.
3. Provision the first workspace and project:
   ```sh
   docker compose exec server python -m backend.devtools.provision \
     --owner-email <your address> --workspace-slug <slug> --workspace-name "<name>" \
     --project-slug <slug> --project-name "<name>"
   ```
4. Reload: the workspace and project appear; create an entity; sign out; confirm the session is gone.
5. Optional: put your `usr_...` id in `PLATFORM_ADMIN_USER_IDS` in `.env` and `docker compose up -d`.

Record the result in `plan/phase-2-evidence.md` (the open "live Authentik round trip" item).

If the round trip fails, remove the Cloudflare route again until it is fixed.

## First sign-in

Anyone Authentik lets in gets an account on first sign-in and is asked to name a first story and a
library of their own. The `backend.devtools.provision` command is no longer needed for that; it
remains for giving someone a workspace by hand.

## Upgrade, roll back, stop

Run `./backup.sh` first. Build the new image, set `NOVEL_ENGINE_VERSION`, `docker compose up -d
--wait`; migrations run before the server starts and are forward-only. To roll back, set the
previous version; if the newer one applied migrations, restore the Postgres dump taken before the
upgrade. `docker compose stop` keeps all volumes.

## Restore drill

Do this on a scratch host (or a copy of the stack) before relying on the backups:

1. `./backup.sh`, then note one story's title and its last change.
2. Make a change you can recognise (add an entry).
3. `./restore.sh backups/novel-engine-<timestamp>` and type `restore`.
4. Rebuild that story's graph (the command is printed at the end), then open the story: the entry
   from step 2 is gone and everything from step 1 is there, including texts, scenes and pictures.
5. A damaged backup is refused before anything changes: change one byte of `objects.tar` in a copy
   of the folder and run `./restore.sh` on the copy.

The credential key is not in the backup. Restoring with a different key keeps everything except
stored provider keys, which must be entered again.

## Scheduled backups

`./backup.sh` takes one backup when run. To take one every night and keep the last 14:

1. Copy the two example units and change `User=` and the paths in the service to match the host
   (paths with a space stay in double quotes):
   ```sh
   sudo cp systemd/novel-engine-backup.service systemd/novel-engine-backup.timer /etc/systemd/system/
   sudoedit /etc/systemd/system/novel-engine-backup.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now novel-engine-backup.timer
   ```
2. Check it once by hand: `sudo systemctl start novel-engine-backup.service`, then
   `journalctl -u novel-engine-backup.service -n 30` shows the checksums and `kept ...` lines,
   and `ls backups/` has a new folder.
3. `systemctl list-timers novel-engine-backup.timer` shows when it runs next.

The service runs `backup.sh` and then `prune-backups.sh`; if the backup fails, nothing is
removed. `BACKUP_KEEP` in the service sets how many complete backups stay (default 14).
`backup.sh` writes the `MANIFEST` last, once both files are checksummed, so a complete backup
is a folder whose `MANIFEST` lists both `novel_engine.dump` and `objects.tar`. Any other folder
is a backup that failed part way, or one still running: it is removed only once a newer backup
has succeeded and nothing in it has changed for 6 hours, so a backup taken by hand while the
nightly one runs is never removed, and the latest failure is always there to look at.
`restore.sh` refuses a folder whose `MANIFEST` does not list both files. A failed run shows in
`systemctl --failed`; a run still going after 2 hours is stopped and counts as failed.

Without systemd, the same with cron (`crontab -e` as the user who owns the checkout):

```cron
30 3 * * * cd "/home/jaket/Projects/Novel Engine/deploy/hosted" && mkdir -p backups && { ./backup.sh && ./prune-backups.sh --keep 14; } >>backups/backup.log 2>&1
```

(`mkdir -p backups` comes first because the shell opens `backups/backup.log` before
`backup.sh` runs, and on a fresh host the folder is not there yet.)

To see what would be removed without removing it: `./prune-backups.sh --keep 14 --dry-run`.

These backups sit on the same disk as the data. Copy them somewhere else as well (another
machine or storage you trust); a backup on a failed disk is no backup. The credential key is
never in a backup: keep it in the password manager. The recovery runbooks are in
`plan/runbooks/`.

### Copying backups off the host

`./copy-off.sh` is an example to adapt. It takes the newest complete backup, checks every file
against its `MANIFEST` (a damaged backup is never copied), and then either:

- copies it into a folder on other storage, `BACKUP_COPY_TO=/mnt/offsite/novel-engine` (a
  mounted disk or a network share): in under a temporary name, checked again there, then given
  its own name, so a copy cut short is never taken for a complete one; or
- runs your own command with the backup folder as `$1`, `BACKUP_COPY_COMMAND`, for example
  `restic -r sftp:backup@offsite:/novel-engine backup "$1"` or
  `rclone copy "$1" offsite-crypt:novel-engine/"$(basename "$1")"`.

`./copy-off.sh --dry-run` says what it would do. It exits non-zero when anything fails, so in the
systemd service put it between the backup and the pruning (an `ExecStart=` line, with
`Environment=BACKUP_COPY_TO=...` or `BACKUP_COPY_COMMAND=...`; the example unit has it commented
out); with cron, `./backup.sh && ./copy-off.sh && ./prune-backups.sh --keep 14`. A failed copy
then stops the pruning and shows in `systemctl --failed`.

A backup holds every story and the encrypted provider keys: send it only somewhere encrypted at
rest (restic encrypts; with rclone use a `crypt` remote). Keep the off-site history where this
host cannot delete it — an append-only restic REST server, or storage with object lock — and
prune it there, not from here. Keep three copies on two kinds of storage, one of them away from
the host. The first real copy is an owner step: record it in `plan/phase-2-evidence.md`.

## What an anonymous visitor can reach

The UI shell and its static assets, `/health/live`, and `/auth/login` (a redirect to Authentik).
Everything under `/api/` answers 401 without a session; the API docs and OpenAPI document are not
served; after sign-in a user with no invitation sees no workspace and cannot create one.
