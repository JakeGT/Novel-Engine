# The shared sign-in service: outage, key changes, and invitation abuse

The hosted site does not keep passwords. People sign in at the shared identity provider
(Authentik, `OIDC_ISSUER`); Novel Engine checks the ID token it gets back and then keeps its own
sign-in session (`sessions`, a cookie; 12 hours on the hosted site and 14 days by default,
`SESSION_TTL_SECONDS`; renewed every hour). Authentik sends no notice when someone's access is
removed there, so their session here ends within that time. Who may use which workspace is decided here, by invitations and memberships, never by the
identity provider. The provider's own recovery and MFA policy belongs to the provider (IDN-006,
an owner decision).

## The identity provider is down or unreachable

**How you notice.** New sign-ins fail (the sign-in page errors, or the provider's page does not
load); people already signed in keep working. The server's log shows the callback or discovery
failing: `docker compose logs --since 1h server | grep -E 'auth\.|ConnectError|ConnectTimeout'`.

**What to check.**
1. The provider itself: open `<OIDC_ISSUER>/.well-known/openid-configuration` in a browser,
   and from the server container (it must reach it too):
   ```sh
   docker compose exec -T server python -c "
   import os, urllib.request as u
   print(u.urlopen(os.environ['OIDC_ISSUER'].rstrip('/') + '/.well-known/openid-configuration', timeout=5).status)"
   ```
2. The server's clock (`date -u` on the host): a clock far off makes every token look expired.

**What to do.** Fix or wait for the provider; nothing in Novel Engine needs changing, and no
data is at risk. Do not lengthen `SESSION_TTL_SECONDS` to get round an outage. Tell authors that
those already signed in can keep working. The server reads the provider's discovery document
once per process: if the provider came back at a different address, correct `OIDC_ISSUER` and
`docker compose up -d --wait`.

**How to confirm.** A fresh sign-in in a private window works; the log shows no new errors.

## The provider changes its signing keys

The provider's public keys are fetched from its key list and kept for five minutes; a token
signed with a key not in the list makes the server fetch the list again at once, so a planned
key rollover in Authentik needs nothing here. If sign-ins fail with `ID token validation failed`
in the log right after a rollover, the provider is signing with a key it does not publish yet:
fix it there. Restarting the server clears every cached key.

## Changing the client secret

When the provider's client secret for Novel Engine must change (it may have been seen, or on a
schedule):
1. Make the new secret in the provider (Authentik: the Novel Engine provider, "Client secret").
2. On the host: `chmod 0600 secrets/oidc_client_secret`, write the new value
   (`printf '%s' '<new secret>' >secrets/oidc_client_secret`), `chmod 0400` it again.
3. `docker compose up -d --wait`.
4. Sign in once in a private window. Sessions already open are unaffected.

## Invitation abuse

An invitation link lets whoever holds it join a workspace with the invitation's role, once,
until it expires; only a hash of it is stored. Abuse looks like members nobody expected, or an
invitation used by someone it was not meant for.

**What to check.**
1. The workspace's audit log (People page, or):
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT created_at, actor_user_id, action, target_id FROM audit_events
      WHERE workspace_id = '<workspace id>'
        AND (action LIKE 'invitation.%' OR action LIKE 'membership.%')
      ORDER BY created_at DESC LIMIT 50;"
   ```
2. Invitations still open, and who accepted the others:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT id, role, created_at, expires_at, accepted_at, accepted_by_user_id, revoked_at
      FROM invitations WHERE workspace_id = '<workspace id>' ORDER BY created_at DESC;"
   ```

**What to do.**
1. The owner revokes every open invitation on the People page ("Invitations not yet used").
2. The owner removes anyone who should not be a member; their access ends on their next
   request. To end their sessions at once:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "DELETE FROM sessions WHERE user_id = '<user id>';"
   ```
3. If the person also should not be able to sign in at all, disable them in the provider.
4. If they read or changed stories: the story history shows every change they made, by name;
   the owner reverts what they need to. If they could have seen secrets or keys, follow
   [suspected-exposure.md](suspected-exposure.md).
5. New invitations: send them to an email address, with the smallest role that works, and
   share the link only directly with the person.

**How to confirm.** The People page lists only the expected members and no open invitations;
the audit log shows the revocations and removals.

Not built: an expiry shorter than the default chosen per invitation, and limits on how many
invitations an owner can make. The SQL above was checked against the current schema
(`tests/integration/test_runbook_queries.py`); none has been used on the live site.
