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
| Backup | `./backup.sh`: Postgres dump + stored objects. Neo4j is rebuilt from Postgres |

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

## What an anonymous visitor can reach

The UI shell and its static assets, `/health/live`, and `/auth/login` (a redirect to Authentik).
Everything under `/api/` answers 401 without a session; the API docs and OpenAPI document are not
served; after sign-in a user with no invitation sees no workspace and cannot create one.
