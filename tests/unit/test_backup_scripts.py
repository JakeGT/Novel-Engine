"""The hosted backup only counts as complete once both files are written and checked (OPS-003).

backup.sh runs against a stand-in for docker on PATH; restore.sh is stopped by its checks
before it would call docker at all.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell"),
    pytest.mark.skipif(shutil.which("sha256sum") is None, reason="needs sha256sum"),
]

HOSTED = Path(__file__).resolve().parents[2] / "deploy" / "hosted"
FILES = ("novel_engine.dump", "objects.tar")

# Stands in for `docker compose ...`: writes a little data for the dump and the tar, and
# notes every call so a test can see whether anything was run.
DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >>"$STUB_LOG"
case "$*" in
  *pg_dump*) printf 'dump-bytes' ;;
  *" tar "*) printf 'tar-bytes' ;;
esac
"""


def _stubs(tmp_path: Path, **scripts: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {"docker": DOCKER, **scripts}.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(tmp_path / "docker.log"),
    }


def _run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(HOSTED / script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_backup_writes_its_manifest_last_with_both_files(tmp_path: Path) -> None:
    env = {**_stubs(tmp_path), "BACKUP_DIR": str(tmp_path / "backups")}
    result = _run("backup.sh", env)
    assert result.returncode == 0, result.stderr
    (folder,) = (tmp_path / "backups").iterdir()
    assert not (folder / "MANIFEST.tmp").exists()
    lines = (folder / "MANIFEST").read_text().splitlines()
    assert lines == [
        f"file={name} sha256={hashlib.sha256((folder / name).read_bytes()).hexdigest()} "
        f"bytes={(folder / name).stat().st_size}"
        for name in FILES
    ]


def test_a_backup_whose_checksum_fails_leaves_no_manifest(tmp_path: Path) -> None:
    env = {
        **_stubs(tmp_path, sha256sum="#!/bin/sh\necho 'sha256sum: read error' >&2\nexit 1\n"),
        "BACKUP_DIR": str(tmp_path / "backups"),
    }
    result = _run("backup.sh", env)
    assert result.returncode != 0
    (folder,) = (tmp_path / "backups").iterdir()
    # No MANIFEST: prune and restore treat the folder as a failed backup.
    assert not (folder / "MANIFEST").exists()


def _folder(tmp_path: Path, manifest: str | None = None) -> Path:
    folder = tmp_path / "novel-engine-20261001T010000Z"
    folder.mkdir()
    for name in FILES:
        (folder / name).write_bytes(name.encode())
    if manifest is None:
        manifest = "".join(
            f"file={name} sha256={hashlib.sha256(name.encode()).hexdigest()} bytes=1\n"
            for name in FILES
        )
    (folder / "MANIFEST").write_text(manifest)
    return folder


@pytest.mark.parametrize(
    "manifest",
    [
        "",
        f"file=novel_engine.dump sha256={hashlib.sha256(b'novel_engine.dump').hexdigest()} bytes=1\n",
    ],
    ids=["empty", "one-file"],
)
def test_restore_refuses_an_empty_or_partial_manifest_before_changing_anything(
    tmp_path: Path, manifest: str
) -> None:
    folder = _folder(tmp_path, manifest)
    env = {**_stubs(tmp_path), "RESTORE_YES": "1"}
    result = _run("restore.sh", env, str(folder))
    assert result.returncode == 1
    assert "incomplete" in result.stderr
    assert not (tmp_path / "docker.log").exists()


def test_restore_goes_ahead_with_a_complete_backup(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    env = {**_stubs(tmp_path), "RESTORE_YES": "1"}
    result = _run("restore.sh", env, str(folder))
    assert result.returncode == 0, result.stderr
    assert "compose stop server" in (tmp_path / "docker.log").read_text()
