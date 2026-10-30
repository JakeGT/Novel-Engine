"""The reproducible release (OSS-005): the same input gives the same bytes."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tarfile
import time

import pytest

from scripts.release import build as release
from scripts.release.build import (
    NEVER_SHIPPED,
    ReleaseError,
    deterministic_tar,
    export_ignored,
    fixed_sbom,
    frontend_env,
    output_name,
    resolve,
    shipped,
)

pytestmark = pytest.mark.unit


def _tree(root, stamp: float) -> None:
    (root / "b").mkdir()
    (root / "b" / "two.txt").write_text("two")
    (root / "a.txt").write_text("one")
    (root / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(root / "run.sh", 0o750)
    os.chmod(root / "a.txt", 0o600)
    for path in (root / "a.txt", root / "b" / "two.txt", root / "run.sh"):
        os.utime(path, (stamp, stamp))


def test_a_tarball_of_the_same_files_is_the_same_bytes_whatever_their_times(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _tree(first, 1_000)
    _tree(second, time.time())
    out_one, out_two = tmp_path / "one.tar.gz", tmp_path / "two.tar.gz"
    deterministic_tar(first, "pkg-1", out_one, 1_700_000_000)
    deterministic_tar(second, "pkg-1", out_two, 1_700_000_000)
    assert (
        hashlib.sha256(out_one.read_bytes()).digest()
        == hashlib.sha256(out_two.read_bytes()).digest()
    )
    # gzip carries no time or name.
    assert out_one.read_bytes()[4:8] == b"\0\0\0\0"
    with tarfile.open(out_one) as archive:
        members = archive.getmembers()
    assert [m.name for m in members] == [
        "pkg-1",
        "pkg-1/a.txt",
        "pkg-1/b",
        "pkg-1/b/two.txt",
        "pkg-1/run.sh",
    ]
    assert {m.mtime for m in members} == {1_700_000_000}
    assert {(m.uid, m.gid, m.uname, m.gname) for m in members} == {(0, 0, "", "")}
    modes = {m.name: m.mode for m in members}
    assert modes["pkg-1/a.txt"] == 0o644 and modes["pkg-1/run.sh"] == 0o755
    assert gzip.decompress(out_one.read_bytes())  # a valid gzip stream


def test_an_sbom_gets_the_commits_time_and_a_serial_number_from_its_content():
    raw = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "serialNumber": "urn:uuid:random",
            "metadata": {"timestamp": "2026-10-21T18:28:50.42Z"},
            "components": [{"name": "fastapi", "version": "1.0"}],
        }
    )
    fixed = json.loads(fixed_sbom(raw, 1_700_000_000))
    assert fixed["metadata"]["timestamp"] == "2023-11-14T22:13:20Z"
    assert (
        fixed["serialNumber"].startswith("urn:uuid:") and fixed["serialNumber"] != "urn:uuid:random"
    )
    again = json.loads(raw)
    again["serialNumber"] = "urn:uuid:another"
    again["metadata"]["timestamp"] = "1999-01-01T00:00:00Z"
    assert fixed_sbom(json.dumps(again), 1_700_000_000) == fixed_sbom(raw, 1_700_000_000)
    changed = json.loads(raw)
    changed["components"][0]["version"] = "2.0"
    assert (
        json.loads(fixed_sbom(json.dumps(changed), 1_700_000_000))["serialNumber"]
        != fixed["serialNumber"]
    )


def test_local_agent_tool_files_are_never_shipped():
    assert {".claude/", ".codex/", ".gemini/", ".mcp.json", "GEMINI.md", "opencode.jsonc"} <= set(
        NEVER_SHIPPED
    )


def test_the_tracked_agent_folder_is_never_shipped():
    assert not shipped(".agent/workflows/character-creation.md")
    assert not shipped(".agent/")  # the folder's own member, as ``git archive`` lists it
    assert shipped("AGENTS.md") and shipped("backend/main.py")
    # ``git archive`` leaves it out too, once ``.gitattributes`` is committed.
    assert export_ignored([".agent/workflows/character-creation.md", "README.md"]) == {
        ".agent/workflows/character-creation.md"
    }


def test_a_working_tree_build_never_stands_where_a_release_would():
    assert output_name("novel-engine-0.1.0", worktree=False) == "novel-engine-0.1.0"
    assert output_name("novel-engine-0.1.0", worktree=True) == "novel-engine-0.1.0-worktree"


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-h", ""])
def test_a_ref_that_could_be_read_as_an_option_is_refused(ref, monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(release, "git", lambda *args: calls.append(args) or "")
    with pytest.raises(ReleaseError, match="not a commit or tag name"):
        resolve(ref)
    assert calls == []  # git was never run with it


def test_a_ref_is_resolved_to_its_full_sha_before_it_is_archived():
    head = release.git("rev-parse", "HEAD")
    assert resolve("HEAD") == head and len(head) == 40
    with pytest.raises(ReleaseError, match="no commit called"):
        resolve("no-such-branch-anywhere")


def test_the_shells_vite_settings_and_node_env_never_reach_the_frontend_build():
    env = {"PATH": "/bin", "VITE_API_URL": "http://evil", "NODE_ENV": "development", "CI": "true"}
    assert frontend_env(env) == {"PATH": "/bin", "CI": "true"}
