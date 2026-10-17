#!/usr/bin/env sh
# Restore Novel Engine from a folder written by ./backup.sh.
#
#   ./restore.sh backups/novel-engine-20261017T120000Z
#
# Checks every file against the backup's MANIFEST before touching anything. Then it stops
# the server, replaces the PostgreSQL database with the dump, replaces the stored objects,
# and starts the server again (migrations run before it serves). Neo4j is a projection of
# PostgreSQL and may hold changes made after the backup, so each story's graph is rebuilt
# afterwards (the last lines say how). The credential key (secrets/credential_kek) must be
# the one that was in use when the backup was taken, or stored provider keys cannot be read.
set -eu

cd "$(dirname "$0")"
source_dir="${1:?give the backup folder, e.g. backups/novel-engine-<timestamp>}"
[ -f "$source_dir/MANIFEST" ] || { echo "No MANIFEST in $source_dir: not a backup folder." >&2; exit 1; }

# 1. Every file is what the backup wrote, before anything is changed.
while IFS= read -r line; do
  file="$(printf '%s\n' "$line" | sed -n 's/^file=\([^ ]*\) .*/\1/p')"
  want="$(printf '%s\n' "$line" | sed -n 's/.* sha256=\([0-9a-f]*\) .*/\1/p')"
  [ -n "$file" ] && [ -n "$want" ] || { echo "Unreadable MANIFEST line: $line" >&2; exit 1; }
  [ -f "$source_dir/$file" ] || { echo "Missing $file: the backup is incomplete." >&2; exit 1; }
  have="$(sha256sum "$source_dir/$file" | cut -d' ' -f1)"
  [ "$have" = "$want" ] || { echo "$file does not match its checksum: the backup is damaged." >&2; exit 1; }
  printf 'checked %s\n' "$file"
done <"$source_dir/MANIFEST"

if [ "${RESTORE_YES:-}" != "1" ]; then
  printf 'This replaces the current database and stored objects with the backup.\n'
  printf 'Take a fresh ./backup.sh first if the current state matters. Type "restore" to go on: '
  read -r answer
  [ "$answer" = "restore" ] || { echo "Nothing was changed."; exit 1; }
fi

# 2. Stop the server so nothing writes while the data is replaced.
docker compose stop server

# 3. The database: dropped and recreated from the dump.
docker compose exec -T postgres dropdb -U novel --if-exists --force novel_engine
docker compose exec -T postgres createdb -U novel novel_engine
docker compose exec -T postgres pg_restore -U novel -d novel_engine --no-owner \
  <"$source_dir/novel_engine.dump"

# 4. The stored objects: replaced as a whole, inside the server's volume.
docker compose run --rm --no-deps -T --entrypoint sh server -c \
  'rm -rf /var/lib/novel-engine/objects && tar -C /var/lib/novel-engine -xf -' \
  <"$source_dir/objects.tar"

# 5. Start again; migrations run first.
docker compose up -d --wait server
printf 'Restored from %s.\n' "$source_dir"
printf '%s\n' 'Rebuild each story'"'"'s graph from the restored database:' \
  '  docker compose exec server python -m backend.devtools.project --workspace <id> --project <id> --rebuild'
