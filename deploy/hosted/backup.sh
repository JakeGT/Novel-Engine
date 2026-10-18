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

# The MANIFEST marks the backup complete, so it is written last, and only once both files
# are there and checksummed: it goes in under a temporary name and is renamed at the end.
# prune-backups.sh and restore.sh treat a folder without it as a backup that failed.
manifest="$target/MANIFEST.tmp"
: >"$manifest"
for file in novel_engine.dump objects.tar; do
  [ -s "$target/$file" ] || { echo "$file is empty: the backup failed." >&2; exit 1; }
  sum="$(sha256sum "$target/$file")" || { echo "Could not checksum $file: the backup failed." >&2; exit 1; }
  sum="${sum%% *}"
  [ -n "$sum" ] || { echo "Could not checksum $file: the backup failed." >&2; exit 1; }
  bytes="$(wc -c <"$target/$file" | tr -d ' ')"
  printf 'file=%s sha256=%s bytes=%s\n' "$file" "$sum" "$bytes" >>"$manifest"
done
mv "$manifest" "$target/MANIFEST"
cat "$target/MANIFEST"
printf 'backup written to %s\n' "$target"
printf '%s\n' 'The credential key (secrets/credential_kek) is not in this backup; keep it in the password manager.'
