#!/usr/bin/env sh
# Back up Novel Engine: its Postgres (authoritative canon, identity, tenancy, encrypted
# credentials) and its stored objects. Neo4j is a projection rebuilt from Postgres and is skipped.
# Writes to $BACKUP_DIR/novel-engine-<UTC timestamp>/ (default ./backups, git-ignored) and prints
# checksums.
set -eu

cd "$(dirname "$0")"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_DIR:-./backups}/novel-engine-$stamp"
umask 077
mkdir -p "$target"

docker compose exec -T postgres pg_dump -U novel -d novel_engine --format=custom \
  >"$target/novel_engine.dump"
docker compose exec -T server tar -C /var/lib/novel-engine -cf - objects \
  >"$target/objects.tar"

for file in novel_engine.dump objects.tar; do
  printf 'file=%s sha256=%s bytes=%s\n' "$file" \
    "$(sha256sum "$target/$file" | cut -d' ' -f1)" "$(wc -c <"$target/$file")"
done | tee "$target/MANIFEST"
printf 'backup written to %s\n' "$target"
printf '%s\n' 'The credential key (secrets/credential_kek) is not in this backup; keep it in the password manager.'
