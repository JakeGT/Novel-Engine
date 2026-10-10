# Platform guide for new apps

Give this to any agent starting a project that will run on the jaketalling
platform. It says what the platform provides, what your app must do to fit,
and what to hand back so the app can be connected.

Last checked against the live platform: 10 October 2026.

## 1. What you are building for

A small Docker platform that hosts Jake's own apps at
`<app>.jaketalling.com`. It gives every app the same four things, so you do
not build them yourself:

| You get | From | You do not build |
| --- | --- | --- |
| A public HTTPS hostname | Cloudflare Tunnel, then Caddy | TLS, certificates, a reverse proxy |
| Sign-in | Authentik (OIDC) at `auth.jaketalling.com` | Passwords, sign-up, MFA, password reset, social login |
| Who may reach the app | An Authentik group per app, managed at `admin.jaketalling.com` | An allowlist or invitation gate for reaching the app |
| A database, if PostgreSQL 16 is enough | The shared platform Postgres | A Postgres container |

```text
visitor -> Cloudflare -> tunnel -> Caddy -> your web container (HTTP, private network)
                                     |
                                     +-> auth.jaketalling.com (Authentik)
```

The current host is a **development** machine: no production data, and
nobody outside Jake is invited. The same setup will be redeployed on a VPS
later, so everything must be rebuildable from files in Git plus secrets.

## 2. Rules

These are not negotiable; an app that breaks one will not be connected.

1. **No published host ports.** Caddy is the only way in.
2. **Only your HTTP entry service joins `platform-proxy`.** Workers, queues,
   databases and sandboxes stay on your own private networks.
3. **Sign-in is Authentik OIDC only.** No local passwords, no second
   identity provider, no public sign-up.
4. **Secrets are files**, never committed, never in image layers, never
   printed in logs or command lines.
5. **Images are pinned** to an immutable tag or digest. Never `latest`.
6. **The app must survive container replacement** with no data loss: state
   lives in a database or a named volume you have documented.
7. **In the infrastructure repository, an app session touches only its own
   `apps/<slug>/` directory** (its Compose file, README and deploy record).
   Everything else there, including Caddy, Authentik blueprints, the root
   `compose.yml` and scripts, is changed by the platform session from the
   request in section 9. Never commit another session's files.
8. **Do not print or copy secrets**, including into chat, tickets or docs.

## 3. Your container

- One image that serves plain HTTP on one port (8080 by convention). TLS
  ends at Cloudflare.
- Runs as a non-root user, with a read-only root filesystem, all
  capabilities dropped and `no-new-privileges`. Use `tmpfs` for `/tmp`.
- Configuration comes from environment variables. Every secret has a
  `<NAME>_FILE` variant that reads a file under `/run/secrets`.
- Logs go to stdout, one JSON object per line where practical. Never log
  tokens, cookies, secrets or request bodies.
- Database migrations run automatically before the server accepts traffic
  (at start-up, or as a one-shot service the server waits for), and are
  forward-only.
- Set memory and process limits.
- Send your own security headers on HTML responses; Caddy adds none. At
  least a Content-Security-Policy, `X-Content-Type-Options: nosniff`,
  `frame-ancestors 'none'` (or `X-Frame-Options: DENY`) and
  `Referrer-Policy`.
- There is no image registry yet: images are built on the host from a
  commit and tagged with that commit (`myapp-server:<commit>`).

### Health

- `GET /healthz` returns 200 without authentication, quickly, with no
  secrets or internal detail. It is public and probed every 30 seconds, so
  it must be cheap.
- A deeper readiness or metrics endpoint is welcome but must not be public:
  list its path in your request so Caddy answers 404 for it.

### Behind the proxy

Caddy sends three headers and is the only trusted forwarder:

| Header | Value |
| --- | --- |
| `X-Forwarded-Proto` | `https` |
| `X-Forwarded-Host` | the public hostname |
| `X-Forwarded-For` | the single real visitor IP |

Trust these only from `172.23.0.0/16` (the `platform-proxy` subnet), one
hop. Build absolute URLs from a configured `PUBLIC_ORIGIN`, not from
request headers. Mark session cookies `Secure`, `HttpOnly`,
`SameSite=Lax`, and protect state-changing requests against CSRF.

Your container receives plain `http` from Caddy. Frameworks that compare
the request's own origin with the browser's `Origin` header (their built-in
CSRF check), or that only set `Secure` cookies when they think the request
is https, will get this wrong unless told the public origin or told to
trust the forwarded headers. Test a form post through Caddy, not only
locally.

Cloudflare sits in front of everything: a request has about 100 seconds to
start responding, and uploads are limited to 100 MB whatever Caddy allows.

If you stream responses (Server-Sent Events, WebSockets) or accept large
uploads, say so in your request: Caddy needs `flush_interval -1` or a
larger body limit (default request limit to ask for: 10 MB).

## 4. Sign-in

Authentik is an OpenID Connect provider. Use a maintained OIDC client
library; do not hand-roll the protocol.

| Setting | Value |
| --- | --- |
| Issuer | `https://auth.jaketalling.com/application/o/<slug>/` (trailing slash is part of it) |
| Discovery | `<issuer>.well-known/openid-configuration` |
| Client id | `<slug>` |
| Client type | Confidential; secret read from a file |
| Flow | Authorization code with PKCE, `state` and `nonce` |
| Scopes | `openid email profile` (no refresh token is issued; see below) |
| Signing | RS256; verify with the keys from discovery |
| Callback | `https://<host>/auth/callback` (exact match, no wildcards) |
| After logout | `https://<host>` or `https://<host>/`: say which. It is matched character for character, so the trailing slash must be exactly what your app sends |
| Token lifetime | Access and ID tokens last one hour |

Claims you can rely on:

- `sub`: a stable, hashed user id. **Use this as the user key**, never the
  email.
- `email`, `name`, `preferred_username`: for display. Emails are verified
  by the upstream provider before Authentik lets anyone enrol.
- `email_verified` is **always `false`**: Authentik does not assert it. Do
  not require it. If your library or app insists, give it a setting to
  trust this provider's emails (Atelier and Triagent use
  `OIDC_TRUST_EMAIL=true`).
- `groups`: a list of group names. `platform-admin` means a platform
  administrator; treat members as admins of your app. Use no other group
  for decisions: `app-<slug>` only says the person was let in, which you
  already know.

How access works:

- Each app gets a group `app-<slug>`. Authentik only completes sign-in to
  your app for members of that group or of `platform-admin`. Anyone else is
  stopped at Authentik and never reaches your callback.
- Jake grants and removes access, and invites new people, at
  `admin.jaketalling.com`. Your app appears there by itself once its group
  exists; you build nothing for it.
- So your app does not need its own "is this person allowed in" list, and
  must not add one: **anyone who completes sign-in gets an account**, created
  on first sign-in. If the app has an invitation-only sign-up mode for other
  deployments, it must be switchable off here (for example
  `SIGNUP_MODE=open`); otherwise people need to be let in twice.
- The app still owns everything inside: roles, tenancy, workspaces, and
  invitations to a workspace or organisation.
- Removing someone from the group stops new sign-ins. It does not end a
  session your app already issued, and the platform sends no notice when
  access is removed or an account is deactivated. Keep app sessions short
  (hours, not weeks) so removal takes effect. No refresh token is issued
  with the scopes above; if you need one, ask for the `offline_access`
  scope in your request so it is added to your provider.
- Sign-out: clear your own session, then send the browser to the
  provider's `end_session_endpoint` (from discovery) with the after-logout
  address.
- People sign in with Google, GitHub or Discord. A person has one
  Authentik account (extra providers are linked from their account
  settings), so `sub` is the same whichever they use.

Start-up: discover the issuer when the app starts, and retry if it fails.
An app that starts before its provider exists must not stay broken until
someone restarts it.

Local development: support a clearly separate local mode (a fixed dev
user) that cannot be enabled when `APP_ENV=production`.

Testing as an ordinary user: the platform has a non-admin account,
`test-user`, for checking what someone without admin rights sees. Ask Jake
to sign in with it, or to give it your app in the dashboard.

Not provided: machine-to-machine credentials. If the app needs API tokens
for scripts, agents or runners, it issues and checks its own.

Calls from your server to `auth.jaketalling.com` go out over the internet
and through Cloudflare, which refuses some default library user agents
(Python's `urllib`, for one). Send an ordinary `User-Agent` if a call that
works in `curl` gets a 403.

## 5. Data

- **PostgreSQL 16** is available on the `platform-database` network at
  host `postgres`, port 5432. You get one role and one database, both named
  after your slug with hyphens turned into underscores. The role is not a
  superuser and cannot create databases or roles; other apps cannot connect
  to yours.
- Extensions needing superuser, a different major version, or anything not
  in stock Postgres 16 (pgvector, for example): run your own database
  container on your own private network instead, and provide a backup
  script. Novel Engine does this.
- No shared Redis. Bring your own on your private network if you need one.
- Files on disk go in a named volume. List every volume that holds state,
  and what is safe to lose (caches, rebuildable indexes).
- Backups: the platform dumps every database in the platform Postgres. A
  database or volume you run yourself needs your own backup script. On the
  current development host nothing is backed up off the machine.

## 6. Secrets

- One file per secret in a git-ignored `secrets/` directory, mode `0400`,
  mounted as Compose secrets at `/run/secrets/<name>`.
- Provide a script that generates the ones that can be generated, never
  overwrites an existing file, and prints file names only.
- If the app encrypts stored credentials, the encryption key is a secret
  that must also be copied to the password manager; say so in your README.
- The OIDC client secret is generated on your side. The platform reads the
  same file to configure Authentik, so the two cannot disagree.

## 7. Email

- Sign-in mail (password reset, invitations to the platform, sign-in
  codes) is sent by Authentik. Do not duplicate it.
- For product mail, send through Resend with **your app's own**
  sending-only API key, restricted to `notify.jaketalling.com`, stored in
  your secrets. There is no shared mail service.
- From `<slug>@notify.jaketalling.com`. Use SMTP or the HTTP API with
  settings from the environment, so the provider can be changed.
- Links in email point at your app, never at `auth.jaketalling.com`.

## 8. Look and feel

Apps should feel like part of jaketalling.com. Tokens (dark is the
default; support both):

| Token | Dark | Light |
| --- | --- | --- |
| Background | `#0b0c0a` | `#f1ede2` |
| Raised surface | `#131511` | `#e8e3d4` |
| Text | `#ece8dc` | `#1c1b17` |
| Secondary text | `#b3afa2` | `#4f4c44` |
| Muted text | `#75726a` | `#87837a` |
| Accent | `#ff6a2b` | `#cf4318` |
| Text on accent | `#0b0c0a` | `#f1ede2` |

- Fonts: Archivo (variable width; condensed and heavy for headings) and
  IBM Plex Mono for labels, in small uppercase with wide letter-spacing.
- Square corners, thin rules instead of shadows, one accent colour.
- Reference implementations: the website's `app/app.css`, the login theme
  at `authentik/branding/login.css`, and the email base at
  `authentik/templates/email/base.html` in the infrastructure repository.
  The admin dashboard (`admin-jaketalling-com/app/app.css`) is the fullest
  example of an app in this style, including tables, forms and a wider set
  of map colours for telling things apart.

This section is guidance, not a gate.

## 9. What to hand back

When the app is ready to be connected, give the platform session this,
filled in. Do not include secret values.

```text
App name:            (display name, e.g. "Atelier")
Slug:                (lowercase letters, digits and hyphens, starting with a letter or
                     digit, e.g. "novel-engine"; becomes the client id and app-<slug> group)
Hostname:            <something>.jaketalling.com
One-line description:
Source repository and the commit or tag to deploy:
How to build the image(s):

Web service:         container name and port (e.g. myapp-server:8080)
Health path:         (public, e.g. /healthz)
Paths that must NOT be public: (e.g. /internal*, /metrics)
Largest request body:
Streaming:           (SSE or WebSockets? yes/no)

Sign-in:             callback URL, after-logout URL (exactly as the app sends it)
                     how the app decides who is an admin (e.g. groups contains platform-admin)
                     session lifetime
                     extra scopes needed, if any (e.g. offline_access)
First sign-in:       what a newly admitted person gets (an account straight away?
                     an empty workspace? nothing until invited inside the app?)

Database:            platform Postgres (role/database name) | own database | none
Volumes with state:  name, what it holds, how to back it up
Secrets:             file names, which are generated, which must be supplied,
                     which need a password-manager copy
Outbound access:     what the app calls on the internet (model providers, GitHub, ...)
Email:               sender address, if it sends any
Metrics:             private path, port, and whether it needs a token

Smoke test:          the commands or steps that prove it works
Upgrade and rollback: the commands, and what to do if migrations ran
```

Also provide a `compose.yml` for the hosted stack, a `.env.example`, and a
short deployment README (build, start, upgrade, roll back, stop). On the
platform these live in the infrastructure repository at `apps/<slug>/`,
next to the app's `secrets/` directory, so Authentik can read the OIDC
client secret from the same file the app uses. Draft them in your own
repository and they are copied across when the app is connected; after
that your session keeps `apps/<slug>/` up to date (rule 7). Sketch of the
stack:

```yaml
name: myapp
services:
  server:
    image: myapp-server:${MYAPP_VERSION:?set MYAPP_VERSION}
    container_name: myapp-server
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    tmpfs: ["/tmp:size=64m,mode=1777"]
    environment:
      PUBLIC_ORIGIN: https://myapp.jaketalling.com
      OIDC_ISSUER: https://auth.jaketalling.com/application/o/myapp/
      OIDC_CLIENT_ID: myapp
      OIDC_CLIENT_SECRET_FILE: /run/secrets/myapp_oidc_client_secret
      DATABASE_URL_FILE: /run/secrets/myapp_database_url
    secrets: [myapp_oidc_client_secret, myapp_database_url]
    networks:
      proxy: { aliases: [myapp-server] }
      database: {}
    mem_limit: 1g
    pids_limit: 256
    logging:
      driver: json-file
      options: { max-size: 10m, max-file: "3" }
networks:
  proxy: { name: platform-proxy, external: true }
  database: { name: platform-database, external: true }
secrets:
  myapp_oidc_client_secret: { file: ./secrets/myapp_oidc_client_secret }
  myapp_database_url: { file: ./secrets/myapp_database_url }
```

## 10. What the platform session then does

For reference; this happens in the infrastructure repository, not yours.

1. Database role and database (`postgres/init/README.md`), if requested.
2. `authentik/blueprints/app-<slug>.yaml`, copied from `app-atelier.yaml`:
   the `app-<slug>` group, the OIDC provider and the application, gated to
   `platform-admin` and the group. Keep the `permissions:` block on the
   group and the dependency on the admin-dashboard role: they are what let
   the dashboard manage the app. The client secret is mounted into the
   Authentik worker in `compose.yml`.
3. A `check_oidc_app(...)` line in `scripts/verify-identity.sh`; then
   `verify-identity.sh` and `verify-admin-access.sh` must pass.
4. An exact Caddy route in `caddy/Caddyfile`, tested on the private network
   before anything is public.
5. A private sign-in round trip, then the Cloudflare tunnel route
   `<host> -> http://caddy:80`.
6. An uptime probe in `prometheus/prometheus.yml`, labelled with the slug,
   and a scrape job if the app has a metrics endpoint (Prometheus reaches
   it over `platform-proxy`). Container logs are not collected yet; read
   them with `docker logs`.
7. An entry in `apps/README.md`, and an app card on the website if wanted.
8. Access for people: at `admin.jaketalling.com`, where the app now
   appears (by hand if the dashboard is down: `docs/operations.md`,
   *Per-app access*).

## 11. Things that have gone wrong before

- **Passwords with URL-special characters inside a connection URL** broke
  parsing, and the failing client logged the whole URL. Pass user and
  password separately, or URL-encode, and never log connection strings.
- **Rendering expensive pages on every request** pushed the website over
  its CPU limit. Precompute or cache anything that does not change per
  request.
- **A provider created by hand in the Authentik UI** gets a generated
  client id and no grant types. Providers are created from blueprints.
- **An app started before its Authentik provider existed** kept sign-in
  disabled until restarted. Retry discovery.
- **Trusting `X-Forwarded-For` from anywhere** lets a caller fake its IP.
  Trust it from the proxy subnet only.
- **Requiring `email_verified`** locked everyone out: Authentik always
  sends `false`.
- **A second, in-app invitation gate** on top of Authentik's meant people
  with access still could not get in.
- **A framework's own origin check rejected every form post** in
  production only, because the app saw `http` behind the proxy.
- **A server-side call to Authentik got 403 from Cloudflare** because of
  the library's default user agent.
- **A new hostname "not resolving"** on the machine that looked it up
  before the DNS record existed is a cached negative answer:
  `resolvectl flush-caches`.

## 12. Working examples

| App | Shows | Where |
| --- | --- | --- |
| Atelier | The standard shape: platform Postgres, blueprint, file secrets, private worker networks | `apps/atelier/` |
| Admin dashboard | The smallest shape: one stateless container, no database, strict security headers | `apps/admin/`, `admin-jaketalling-com` |
| Triagent | A web server plus an isolated runner with its own egress network | `apps/triagent/` |
| Novel Engine | Own Postgres 17 + pgvector and Neo4j, one-shot migration service. Its stack is kept in its own repository, an exception that predates this guide | its repo, `deploy/hosted/` |
