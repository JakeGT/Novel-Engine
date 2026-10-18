# Rotating the credential key

Workspaces' provider keys are stored encrypted. Each stored key has its own data key, which is
locked with the server's credential key (`secrets/credential_kek`). Every stored key records
which version of the credential key locked it. The server locks new keys with the current
version and can still open keys locked by older versions it is given. That is all the app does
today: it does **not** re-lock existing keys under a new version by itself. A stored key moves to
the new version only when its workspace connects a key again.

Rotate when the key may have been exposed (a leaked backup of `secrets/`, a former operator), or
on a schedule you choose.

## How you notice it is needed

- You decided to rotate, or the key may have been seen by someone who should not have it.
- After a mistake: assistant requests fail with "Something went wrong" or "An unexpected error
  occurred", and the server's log names `CryptoError` or `CredentialUnavailable` (as the
  `error_type` of a `job.failed.internal` event, or in a request's error trace, which reads
  `credential decryption failed` or `no key-encryption key configured for version <n>`). A key
  version the stored keys need is missing from the server's settings, or a key is wrong: put the
  old key back as in steps 3 and 4.

## What to check

1. Which versions are in use, and how many keys each still locks (no keys are shown):
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT key_version, status, count(*) FROM credentials GROUP BY key_version, status ORDER BY 1;"
   ```
   Before any rotation every key is version 1.
2. That you have a copy of the current key in the password manager. Without it, keys it locked
   cannot be opened; owners would have to connect them again.

## What to do

The server reads three settings: `CREDENTIAL_KEK` (the current key, from
`secrets/credential_kek`), `CREDENTIAL_KEK_VERSION` (its version number, 1 by default) and
`CREDENTIAL_PREVIOUS_KEKS` (older keys it may still open, as `version:key` pairs separated by
commas). A secret file named after a setting (in lower case) is read like the setting.

1. **Back up first**: `./backup.sh`. Save the current key in the password manager under its
   version number ("credential key v1").
2. **Make the new key** and keep it in the password manager as the next version:
   ```sh
   head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'; echo
   ```
   (the same format `init-secrets.sh` uses: 32 random bytes, URL-safe base64).
3. **Keep the old key for opening**: write it to `secrets/credential_previous_keks` as
   `1:<old key>` (for a later rotation, add pairs: `1:<v1 key>,2:<v2 key>`), readable only by you:
   ```sh
   (umask 077; printf '1:%s' "$(cat secrets/credential_kek)" >secrets/credential_previous_keks)
   chmod 0400 secrets/credential_previous_keks
   ```
   (For a later rotation the file is already read only: `chmod 0600` it before editing, and
   `chmod 0400` after.)
4. **Tell the stack** (`compose.yml`, which does not carry these yet):
   - under `x-app` → `environment`, add `CREDENTIAL_KEK_VERSION: "2"` (one more than the
     highest version in use);
   - under `x-app` → `secrets`, add `- credential_previous_keks`;
   - in the top-level `secrets:`, add
     `credential_previous_keks: { file: "${SECRETS_DIR:-./secrets}/credential_previous_keks" }`.
5. **Put the new key in place**: the file is read only (mode 0400), so make it writable, replace
   its contents with the new key, and make it read only again; then recreate the server:
   ```sh
   chmod 0600 secrets/credential_kek
   printf '%s' '<new key>' >secrets/credential_kek
   chmod 0400 secrets/credential_kek
   docker compose up -d --wait
   ```
6. **Move each stored key to the new version.** Each workspace owner connects their key again on
   the Assistant page (the same key is fine); it is stored under the new version and the old copy
   is retired. Until they do, their key keeps working through the old version.
7. **Retire the old key** only when step 1's query shows no `active` key on the old version.
   Remove its pair from `secrets/credential_previous_keks` (`chmod 0600` before, `chmod 0400`
   after) and `docker compose up -d --wait`.
   Retired and revoked keys on that version can no longer be opened, which is what you want. If
   the old key was exposed, decide whether to wait: as long as it is listed, the keys it locked can
   be opened with it by anyone who also has a copy of the database.

If the key was exposed together with a copy of the database, rotating is not enough: the owners
of every workspace should also revoke their keys with their provider and connect new ones.

## How to confirm

- Step 1's query shows new keys on the new version.
- An owner whose key is on the old version can still use the assistant (until step 7), and one
  who connected again can too.
- The server's log names no key errors:
  `docker compose logs --since 1h server | grep -cE 'CryptoError|CredentialUnavailable'` prints `0`.
- Backups taken before the rotation need the old key to open stored provider keys: keep it in the
  password manager, marked with the date of the last backup that needs it.

Not built: re-locking every stored key under the new version in one command, so that the old key
could be retired at once without asking owners to connect again.
