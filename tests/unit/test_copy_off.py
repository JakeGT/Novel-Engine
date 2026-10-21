"""Copying the newest complete backup off the host, checked before and after (OPS-003)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        shutil.which("sh") is None or shutil.which("sha256sum") is None,
        reason="needs a POSIX shell and sha256sum",
    ),
]

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "hosted" / "copy-off.sh"
FILES = ("novel_engine.dump", "objects.tar")


def _backup(root: Path, stamp: str, *, complete: bool = True, damaged: bool = False) -> Path:
    folder = root / f"novel-engine-{stamp}"
    folder.mkdir(parents=True)
    lines = []
    for name in FILES:
        data = f"{name} of {stamp}".encode()
        (folder / name).write_bytes(data)
        lines.append(f"file={name} sha256={hashlib.sha256(data).hexdigest()} bytes={len(data)}\n")
    if complete:
        (folder / "MANIFEST").write_text("".join(lines))
    if damaged:
        (folder / "objects.tar").write_bytes(b"changed")
    return folder


def _run(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("BACKUP_")}
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        env={**clean, "BACKUP_DIR": str(root), **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_newest_complete_backup_is_copied_and_checked(tmp_path: Path) -> None:
    root = tmp_path / "back ups"
    _backup(root, "20261019T030000Z")
    _backup(root, "20261020T030000Z")
    _backup(root, "20261021T030000Z", complete=False)  # failed part way: not it
    dest = tmp_path / "off site"
    dest.mkdir()

    done = _run(root, BACKUP_COPY_TO=str(dest))

    assert done.returncode == 0, done.stderr
    assert "copied novel-engine-20261020T030000Z" in done.stdout
    copied = dest / "novel-engine-20261020T030000Z"
    assert sorted(p.name for p in copied.iterdir()) == ["MANIFEST", *FILES]
    assert not list(dest.glob(".incoming-*"))
    again = _run(root, BACKUP_COPY_TO=str(dest))
    assert again.returncode == 0 and "already copied" in again.stdout


def test_a_damaged_backup_is_never_copied(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    folder = _backup(root, "20261020T030000Z", damaged=True)
    dest = tmp_path / "dest"
    dest.mkdir()
    done = _run(root, str(folder), BACKUP_COPY_TO=str(dest))
    assert done.returncode == 1
    assert "the backup is damaged" in done.stderr
    assert list(dest.iterdir()) == []


def test_a_damaged_earlier_copy_is_replaced_by_a_good_one(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    _backup(root, "20261020T030000Z")
    dest = tmp_path / "dest"
    bad = _backup(dest, "20261020T030000Z", damaged=True)
    done = _run(root, BACKUP_COPY_TO=str(dest))
    assert done.returncode == 0, done.stderr
    assert (bad / "objects.tar").read_bytes() == b"objects.tar of 20261020T030000Z"


def test_a_copy_command_gets_the_backup_folder_and_its_failure_shows(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    folder = _backup(root, "20261020T030000Z")
    record = tmp_path / "called"
    ok = _run(root, BACKUP_COPY_COMMAND=f'printf "%s" "$1" > "{record}"')
    assert ok.returncode == 0, ok.stderr
    assert Path(record.read_text()).resolve() == folder.resolve()
    failed = _run(root, BACKUP_COPY_COMMAND="exit 3")
    assert failed.returncode == 1 and "copy command failed" in failed.stderr


def test_nothing_set_up_or_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    _backup(root, "20261020T030000Z")
    unset = _run(root)
    assert unset.returncode == 2 and "Nowhere to copy to" in unset.stderr
    both = _run(root, BACKUP_COPY_TO=str(tmp_path), BACKUP_COPY_COMMAND="true")
    assert both.returncode == 2
    dest = tmp_path / "dest"
    dest.mkdir()
    dry = _run(root, "--dry-run", BACKUP_COPY_TO=str(dest))
    assert dry.returncode == 0 and "would copy" in dry.stdout
    assert list(dest.iterdir()) == []


def test_a_missing_destination_says_so(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    _backup(root, "20261020T030000Z")
    done = _run(root, BACKUP_COPY_TO=str(tmp_path / "not mounted"))
    assert done.returncode == 1 and "is the disk mounted" in done.stderr
