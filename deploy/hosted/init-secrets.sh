#!/usr/bin/env sh
# Create this stack's secrets and .env (both git-ignored). Safe to re-run: an existing file is
# never overwritten. Prints file names only, never values.
#
#   ./init-secrets.sh [path to a .env holding OIDC_CLIENT_ID and OIDC_CLIENT_SECRET]
#
# The OIDC client id and secret are copied from that .env (default: this checkout's .env) and
# must match the Authentik provider. Everything else is generated here.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
source_env="$(realpath "${1:-$here/../../.env}")"
cd "$here"
umask 077
mkdir -p secrets
chmod 700 secrets

value_of() {
  # Value of KEY in the source .env, without surrounding quotes.
  sed -n "s/^$1=//p" "$source_env" | head -n 1 | sed 's/^"\(.*\)"$/\1/'
}

create() {
  # create NAME MODE COMMAND...: write the command's output to secrets/NAME unless it exists.
  name="$1"
  mode="$2"
  shift 2
  if [ -e "secrets/$name" ]; then
    printf 'kept     secrets/%s\n' "$name"
    return
  fi
  "$@" >"secrets/$name"
  chmod "$mode" "secrets/$name"
  printf 'created  secrets/%s\n' "$name"
}

random_hex() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
# 32 random bytes, URL-safe base64 with padding: the format CREDENTIAL_KEK requires.
random_kek() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'; }
neo4j_auth() { printf 'neo4j/%s' "$(cat secrets/neo4j_password)"; }
oidc_secret() { printf '%s' "$(value_of OIDC_CLIENT_SECRET)"; }

if [ ! -r "$source_env" ]; then
  printf 'cannot read %s\n' "$source_env" >&2
  exit 1
fi
if [ -z "$(value_of OIDC_CLIENT_ID)" ] || [ -z "$(value_of OIDC_CLIENT_SECRET)" ]; then
  printf 'OIDC_CLIENT_ID and OIDC_CLIENT_SECRET must be set in %s\n' "$source_env" >&2
  exit 1
fi

# The server runs as uid 1000 and reads owner-only files. The Postgres and Neo4j images start as
# their own users, so the store passwords are world-readable inside secrets/, which itself is
# owner-only (0700): nobody else on the host can reach them.
create postgres_password 444 random_hex
create neo4j_password 444 random_hex
create neo4j_auth 444 neo4j_auth
create credential_kek 400 random_kek
create oidc_client_secret 400 oidc_secret

if [ -e .env ]; then
  printf 'kept     .env\n'
else
  {
    printf 'NOVEL_ENGINE_VERSION=\n'
    printf 'OIDC_CLIENT_ID=%s\n' "$(value_of OIDC_CLIENT_ID)"
    printf 'PLATFORM_ADMIN_USER_IDS=\n'
  } >.env
  chmod 600 .env
  printf 'created  .env (set NOVEL_ENGINE_VERSION before starting)\n'
fi

printf '%s\n' 'Keep a copy of secrets/credential_kek in the password manager.'
