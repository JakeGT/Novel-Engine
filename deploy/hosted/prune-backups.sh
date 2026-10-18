#!/usr/bin/env sh
# Remove old backup folders written by ./backup.sh, keeping the newest complete ones.
#
#   ./prune-backups.sh [--keep N] [--dry-run]
#
# Looks only at folders named novel-engine-<UTC timestamp> in $BACKUP_DIR (default
# ./backups). A complete backup has a MANIFEST that lists both its files, and both files;
# the newest N complete ones are kept (default 14, or $BACKUP_KEEP). Any other folder is a
# backup that failed part way, or one still being written: it is removed only when a newer
# complete backup exists and nothing in it has changed for 6 hours, so a backup running
# now (a manual one next to the nightly one) is never removed, and the last failed attempt
# is always left for you to look at. Nothing else in the folder is touched. --dry-run only
# says what would go.
set -eu

cd "$(dirname "$0")"
keep="${BACKUP_KEEP:-14}"
dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep)
      [ $# -ge 2 ] || { echo "--keep needs a number" >&2; exit 2; }
      keep="$2"
      shift 2
      ;;
    --dry-run) dry_run=1; shift ;;
    *) echo "Unknown option: $1 (use --keep N or --dry-run)" >&2; exit 2 ;;
  esac
done
case "$keep" in
  ''|*[!0-9]*) echo "--keep must be a whole number, got: $keep" >&2; exit 2 ;;
esac
[ "$keep" -ge 1 ] || { echo "--keep must be at least 1: this never removes every backup." >&2; exit 2; }

dir="${BACKUP_DIR:-./backups}"
[ -d "$dir" ] || { echo "No backup folder at $dir; nothing to do."; exit 0; }

# Newest first. The timestamp in the name sorts the same way as time.
list="$(find "$dir" -mindepth 1 -maxdepth 1 -type d -name 'novel-engine-[0-9]*T[0-9]*Z' -print | sort -r)"

remove() {
  if [ "$dry_run" = 1 ]; then
    printf 'would remove %s (%s)\n' "$1" "$2"
  else
    rm -rf -- "$1"
    printf 'removed %s (%s)\n' "$1" "$2"
  fi
}

# Complete: a MANIFEST with a checksum line for each file, and both files there.
is_complete() {
  [ -s "$1/MANIFEST" ] && [ -s "$1/novel_engine.dump" ] && [ -s "$1/objects.tar" ] &&
    grep -q '^file=novel_engine\.dump sha256=[0-9a-f]\{64\} ' "$1/MANIFEST" &&
    grep -q '^file=objects\.tar sha256=[0-9a-f]\{64\} ' "$1/MANIFEST"
}

# Something in the folder was written in the last 6 hours: it may still be being written.
is_recent() {
  [ -n "$(find "$1" -mmin -360 -print 2>/dev/null | head -n 1)" ]
}

complete=0
newest_complete_seen=0
printf '%s\n' "$list" | while IFS= read -r folder; do
  [ -n "$folder" ] || continue
  if is_complete "$folder"; then
    complete=$((complete + 1))
    newest_complete_seen=1
    if [ "$complete" -le "$keep" ]; then
      printf 'kept    %s\n' "$folder"
    else
      remove "$folder" "older than the newest $keep"
    fi
  elif is_recent "$folder"; then
    printf 'kept    %s (incomplete; written to in the last 6 hours, so it may still be running)\n' "$folder"
  elif [ "$newest_complete_seen" = 1 ]; then
    remove "$folder" "incomplete, and a newer backup succeeded"
  else
    printf 'kept    %s (incomplete; the newest attempt, left for you to look at)\n' "$folder"
  fi
done
