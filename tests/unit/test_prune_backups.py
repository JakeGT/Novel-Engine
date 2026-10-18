"""Removing old hosted backups keeps the newest complete ones and nothing else (OPS-003)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell"),
]

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "hosted" / "prune-backups.sh"


HOUR = 3600
FILES = ("novel_engine.dump", "objects.tar")


def _age(folder: Path, hours: float) -> None:
    """Make everything in a folder look last written some hours ago."""
    when = time.time() - hours * HOUR
    for path in [*folder.iterdir(), folder]:
        os.utime(path, (when, when))


def _backup(
    root: Path, stamp: str, *, complete: bool = True, manifest: str | None = None, hours: float = 24
) -> Path:
    folder = root / f"novel-engine-{stamp}"
    folder.mkdir()
    for name in FILES:
        (folder / name).write_bytes(b"data")
    if manifest is None and complete:
        manifest = "".join(f"file={name} sha256={'a' * 64} bytes=4\n" for name in FILES)
    if manifest is not None:
        (folder / "MANIFEST").write_text(manifest)
    _age(folder, hours)
    return folder


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        env={**os.environ, "BACKUP_DIR": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_keeps_the_newest_complete_backups_and_nothing_else_is_touched(tmp_path: Path) -> None:
    root = tmp_path / "back ups"
    root.mkdir()
    for day in range(1, 6):
        _backup(root, f"202610{day:02d}T010000Z")
    _backup(root, "20261003T120000Z", complete=False)
    _backup(root, "20261006T010000Z", complete=False)
    (root / "notes").mkdir()
    (root / "readme.txt").write_text("mine")

    result = _run(root, "--keep", "2")

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in root.iterdir()) == [
        "notes",
        "novel-engine-20261004T010000Z",
        "novel-engine-20261005T010000Z",
        # The newest attempt failed part way: it stays, to be looked at.
        "novel-engine-20261006T010000Z",
        "readme.txt",
    ]


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    for day in range(1, 4):
        _backup(tmp_path, f"202610{day:02d}T010000Z")
    result = _run(tmp_path, "--keep", "1", "--dry-run")
    assert result.returncode == 0
    assert result.stdout.count("would remove") == 2
    assert len(list(tmp_path.iterdir())) == 3


@pytest.mark.parametrize("keep", ["0", "two", ""])
def test_refuses_a_count_that_could_remove_every_backup(tmp_path: Path, keep: str) -> None:
    _backup(tmp_path, "20261001T010000Z")
    result = _run(tmp_path, "--keep", keep)
    assert result.returncode == 2
    assert (tmp_path / "novel-engine-20261001T010000Z").exists()


@pytest.mark.parametrize(
    "manifest",
    [
        "",
        f"file=novel_engine.dump sha256={'a' * 64} bytes=4\n",
        "file=novel_engine.dump sha256= bytes=4\nfile=objects.tar sha256= bytes=4\n",
    ],
    ids=["empty", "one-file", "no-checksums"],
)
def test_an_empty_or_partial_manifest_is_not_a_complete_backup(
    tmp_path: Path, manifest: str
) -> None:
    _backup(tmp_path, "20261001T010000Z")
    _backup(tmp_path, "20261002T010000Z")
    _backup(tmp_path, "20261003T010000Z", manifest=manifest)

    result = _run(tmp_path, "--keep", "1")

    assert result.returncode == 0, result.stderr
    # The broken newest one does not count as the one to keep, so the last good backup
    # stays; the broken one stays too, as the newest attempt, to be looked at.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "novel-engine-20261002T010000Z",
        "novel-engine-20261003T010000Z",
    ]


def test_a_backup_still_being_written_is_never_removed(tmp_path: Path) -> None:
    # A manual backup started just before the nightly one, which then finished first.
    running = _backup(tmp_path, "20261001T032900Z", complete=False, hours=0)
    _backup(tmp_path, "20261001T033000Z", hours=0)

    result = _run(tmp_path, "--keep", "1")

    assert result.returncode == 0, result.stderr
    assert running.exists()
    assert "may still be running" in result.stdout

    _age(running, 7)
    assert _run(tmp_path, "--keep", "1").returncode == 0
    assert not running.exists()
