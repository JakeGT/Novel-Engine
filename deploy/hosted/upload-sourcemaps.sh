#!/usr/bin/env sh
# Upload the site's source maps to Sentry for one build, so browser errors show the
# original code instead of the minified bundle. The maps are never part of the site.
#
#   ./upload-sourcemaps.sh <commit>      # the same value as NOVEL_ENGINE_VERSION
#
# Needs, all yours to provide:
#   secrets/sentry_auth_token   an organisation auth token with the "release" scope
#   SENTRY_ORG, SENTRY_PROJECT_WEB in .env (the organisation and web project slugs)
#   SENTRY_URL in .env only when not using sentry.io's default region address
#
# Run it from the same checkout the image was built from, before or after deploying.
# NOT YET RUN AGAINST A REAL SENTRY PROJECT: check the first upload in Sentry's
# "Source Maps" page for the release.
set -eu

version="${1:?usage: ./upload-sourcemaps.sh <commit>}"
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

setting() { sed -n "s/^$1=//p" .env | head -n 1; }
org="$(setting SENTRY_ORG)"
project="$(setting SENTRY_PROJECT_WEB)"
url="$(setting SENTRY_URL)"
if [ -z "$org" ] || [ -z "$project" ]; then
  printf '%s\n' 'Set SENTRY_ORG and SENTRY_PROJECT_WEB in .env first.' >&2
  exit 1
fi
if [ ! -s secrets/sentry_auth_token ]; then
  printf '%s\n' 'Put a Sentry auth token in secrets/sentry_auth_token (mode 0400) first.' >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# The same build as the image, stopped at the stage that holds the bundle and its maps.
docker build --quiet --target web-maps --output "type=local,dest=$work" ../.. >/dev/null

# The token is passed as a file, so it is never on a command line or in the environment
# of this shell. The release name must match what the site reports: novel-engine@<commit>.
docker run --rm \
  -v "$work:/work:ro" \
  -v "$here/secrets/sentry_auth_token:/run/secrets/sentry_auth_token:ro" \
  -e SENTRY_ORG="$org" -e SENTRY_PROJECT="$project" ${url:+-e SENTRY_URL="$url"} \
  --entrypoint sh \
  "${SENTRY_CLI_IMAGE:-getsentry/sentry-cli:2}" \
  -c 'SENTRY_AUTH_TOKEN="$(cat /run/secrets/sentry_auth_token)" exec sentry-cli sourcemaps upload \
        --release "$0" --url-prefix "~/assets" /work/assets' \
  "novel-engine@$version"

printf 'source maps uploaded for novel-engine@%s\n' "$version"
