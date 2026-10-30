#!/usr/bin/env sh
# Copy the newest complete backup written by ./backup.sh off this host (OPS-003). An example
# to adapt: the backups in ./backups sit on the same disk as the data they protect.
#
#   ./copy-off.sh [--dry-run] [backups/novel-engine-<timestamp>]
#
# Without a folder it takes the newest complete one (a MANIFEST listing both files). Every
# file is checked against the MANIFEST first, so a damaged backup is never copied. Then
# one of these, whichever is set:
#
#   BACKUP_COPY_TO=/mnt/offsite/novel-engine
#       A folder on other storage (a mounted disk, a network share). The backup is copied
#       in under a temporary name, checked again there, and only then given its own name,
#       so a copy cut short is never taken for a complete one. A backup already there,
#       and complete, is left as it is.
#   BACKUP_COPY_COMMAND='restic -r sftp:backup@offsite:/novel-engine backup "$1"'
#       Any command, run by sh with the backup folder as $1: restic, rclone, rsync over
#       ssh. It must exit non-zero when the copy fails.
#
# The copy holds every story and the encrypted provider keys: send it only somewhere
# encrypted at rest that you trust (restic encrypts; for rclone use a crypt remote). The
# credential key (secrets/credential_kek) is never in a backup and is never copied; keep it
# in the password manager. Old copies are not removed here: let the other side keep its own
# history (restic forget, or the storage's own retention), ideally where this host cannot
# delete it (an append-only or object-locked target). Exits 0 when copied (or already
# there), 1 when the copy or a check fails, 2 when nothing is set up.
set -eu

cd "$(dirname "$0")"
dry_run=0
folder=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    -*) echo "Unknown option: $1 (use --dry-run and, if you like, a backup folder)" >&2; exit 2 ;;
    *) folder="$1"; shift ;;
  esac
done

if [ -z "${BACKUP_COPY_TO:-}" ] && [ -z "${BACKUP_COPY_COMMAND:-}" ]; then
  echo "Nowhere to copy to: set BACKUP_COPY_TO (a folder) or BACKUP_COPY_COMMAND (a command)." >&2
  exit 2
fi
if [ -n "${BACKUP_COPY_TO:-}" ] && [ -n "${BACKUP_COPY_COMMAND:-}" ]; then
  echo "Set BACKUP_COPY_TO or BACKUP_COPY_COMMAND, not both." >&2
  exit 2
fi

complete() {
  # A folder is a complete backup when its MANIFEST lists both files.
  [ -f "$1/MANIFEST" ] || return 1
  for file in novel_engine.dump objects.tar; do
    grep -q "^file=$file sha256=[0-9a-f]\{64\} " "$1/MANIFEST" || return 1
  done
}

verify() {
  # Every file the MANIFEST lists is there and matches its checksum.
  while IFS= read -r line; do
    file="$(printf '%s\n' "$line" | sed -n 's/^file=\([^ ]*\) .*/\1/p')"
    want="$(printf '%s\n' "$line" | sed -n 's/.* sha256=\([0-9a-f]*\) .*/\1/p')"
    [ -n "$file" ] && [ -n "$want" ] || { echo "Unreadable MANIFEST line in $1." >&2; return 1; }
    [ -f "$1/$file" ] || { echo "$file is missing from $1." >&2; return 1; }
    have="$(sha256sum "$1/$file" | cut -d' ' -f1)"
    [ "$have" = "$want" ] || { echo "$file in $1 does not match its checksum." >&2; return 1; }
  done <"$1/MANIFEST"
}

if [ -z "$folder" ]; then
  dir="${BACKUP_DIR:-./backups}"
  [ -d "$dir" ] || { echo "No backup folder at $dir: run ./backup.sh first." >&2; exit 1; }
  # Newest first (the timestamp in the name sorts the same way as time); read line by
  # line so a folder path with spaces stays whole.
  folder="$(find "$dir" -mindepth 1 -maxdepth 1 -type d -name 'novel-engine-[0-9]*T[0-9]*Z' -print |
    sort -r | while IFS= read -r candidate; do
      if complete "$candidate"; then printf '%s\n' "$candidate"; break; fi
    done)"
  [ -n "$folder" ] || { echo "No complete backup in $dir: run ./backup.sh first." >&2; exit 1; }
fi
complete "$folder" || { echo "$folder is not a complete backup (no MANIFEST listing both files)." >&2; exit 1; }
verify "$folder" || { echo "Not copied: the backup is damaged. Take a new one with ./backup.sh." >&2; exit 1; }
name="$(basename "$folder")"
printf 'checked %s\n' "$name"

if [ -n "${BACKUP_COPY_COMMAND:-}" ]; then
  if [ "$dry_run" -eq 1 ]; then
    printf 'would run the copy command for %s\n' "$name"
    exit 0
  fi
  if sh -c "$BACKUP_COPY_COMMAND" copy-off "$folder"; then
    printf 'copied %s with the copy command\n' "$name"
    exit 0
  fi
  echo "The copy command failed for $name; nothing here was changed." >&2
  exit 1
fi

dest="$BACKUP_COPY_TO"
[ -d "$dest" ] || { echo "$dest is not a folder (is the disk mounted?)." >&2; exit 1; }
if [ -d "$dest/$name" ] && complete "$dest/$name" && verify "$dest/$name"; then
  printf '%s is already copied to %s\n' "$name" "$dest"
  exit 0
fi
if [ "$dry_run" -eq 1 ]; then
  printf 'would copy %s to %s\n' "$name" "$dest"
  exit 0
fi
incoming="$dest/.incoming-$name"
rm -rf "$incoming"
umask 077
mkdir -p "$incoming"
for file in novel_engine.dump objects.tar MANIFEST; do
  cp "$folder/$file" "$incoming/$file"
done
verify "$incoming" || { rm -rf "$incoming"; echo "The copy of $name did not match; it was removed." >&2; exit 1; }
# A damaged earlier copy under the same name is replaced only by one that checked out.
rm -rf "$dest/$name"
mv "$incoming" "$dest/$name"
printf 'copied %s to %s\n' "$name" "$dest"
