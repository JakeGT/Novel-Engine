# Rotating the credential key

Workspaces' provider keys are stored encrypted. Each stored key has its own data key, which is
locked with the server's credential key (`secrets/credential_kek`). Every stored key records
which version of the credential key locked it. The server locks new keys with the current
version and can still open keys locked by older versions it is given, so during a rotation both
the old and the new key are accepted. `backend.devtools.rekey` then moves every stored key to the
new version in place (only each key's own data key is locked again; the stored secret is not
touched), checks that each one opens, and says when the old key can be retired. Owners do not
have to connect their keys again.

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
   docker compose exec -T server python -m backend.devtools.rekey status
   ```
   It prints `current_version`, the versions this server holds (`configured_versions`) and a
   count per version and status. Before any rotation every key is version 1. (The same from the
   database: `SELECT key_version, status, count(*) FROM credentials GROUP BY 1, 2 ORDER BY 1;`.)
2. That you have a copy of the current key in the password manager. Without it, keys it locked
   cannot be opened; owners would have to connect them again.

## What to do

The server reads three settings: `CREDENTIAL_KEK` (the current key, from
`secrets/credential_kek`), `CREDENTIAL_KEK_VERSION` (its version number, 1 by default) and
`CREDENTIAL_PREVIOUS_KEKS` (older keys it may still open, as `version:key` pairs separated by
commas). A secret file named after a setting (in lower case) is read like the setting. An entry
that is not `version:key` (a whole number from 1, a colon, the key), or that lists a version
twice with different keys, stops the server and the rekey command from starting, with a message
that names the entry's place ("Entry 2 of CREDENTIAL_PREVIOUS_KEKS ...") but never its value:
it is never dropped quietly, which would leave every key on that version unopenable.

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
6. **Check both keys work**, then **move every stored key to the new version**:
   ```sh
   docker compose exec -T server python -m backend.devtools.rekey verify
   docker compose exec -T server python -m backend.devtools.rekey rekey            # a dry run
   docker compose exec -T server python -m backend.devtools.rekey rekey --apply
   ```
   `verify` opens every active key with the keys the server holds and compares it with the key
   it recorded; `ok: true` means every one opened. The dry run opens each key on an older
   version with that version's key and writes nothing (`would_move` says how many). `--apply`
   moves them, one at a time, each only after its new lock has been opened with the new key and
   found to hold the same key; each move is a `credential.rekeyed` event in the workspace's
   audit log, written in the same transaction as the move. It is safe to run again or to stop
   part way: a key is either still on its old version, or fully moved and recorded. The server keeps working throughout, and an owner who connects a key
   at the same time is not disturbed. Revoked keys are left on the old version unless you add
   `--include-revoked` (they can never be used; retiring the old key makes them unreadable).
   Anything listed under `unopenable` or `missing_version` was **not** moved: see "If a key
   will not open" below.
7. **Retire the old key** when `rekey verify` prints `active_on_older_versions: 0` and lists the
   old version in `versions_safe_to_remove`. Remove its pair from
   `secrets/credential_previous_keks` (`chmod 0600` before, `chmod 0400` after) and
   `docker compose up -d --wait`, then run `rekey verify` once more: `ok: true`.
   Revoked keys on that version can no longer be opened, which is what you want. If the old key
   was exposed, retire it as soon as `verify` allows: as long as it is listed, the keys it locked
   can be opened with it by anyone who also has a copy of the database (and backups taken before
   the move still can be, with the old key; see below).

If the key was exposed together with a copy of the database, rotating is not enough: the owners
of every workspace should also revoke their keys with their provider and connect new ones.

## If a key will not open

`rekey` and `verify` name credentials (ids only) in two lists:

- `missing_version`: keys locked by a version this server was not given. Add that version's key
  to `secrets/credential_previous_keks` (from the password manager) and run again.
- `unopenable`: the key given for that version does not open it, or it opens to a different key
  than the one recorded. Usually a wrong or mistyped key in the settings: check the pairs against
  the password manager. If the key is right, that one stored key is damaged; its owner connects
  it again on the Assistant page (it is then stored under the new version).

Nothing listed there was changed.

`rekey` refuses to start (exit 2, a plain sentence, no key printed) when no credential key is
set, when an older key is not a valid key, or when the current version number is also listed
among the older keys with a different key (give the new key a new number).

## How to confirm

- `rekey status` shows every active key on the new version, and `rekey verify` prints `ok: true`.
- An owner's assistant still works, before and after step 7.
- The server's log names no key errors:
  `docker compose logs --since 1h server | grep -cE 'CryptoError|CredentialUnavailable'` prints `0`.
- Backups taken before the rotation need the old key to open stored provider keys: keep it in the
  password manager, marked with the date of the last backup that needs it.

Checked on a development machine against a disposable database
(`tests/integration/test_credential_vault.py`, `tests/unit/test_rekey.py`); not yet rehearsed on
the live site. `make rekey REKEY_ARGS='verify'` runs the same command on a local install.
