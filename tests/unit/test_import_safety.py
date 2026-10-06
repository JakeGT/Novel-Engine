import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

_IMPORT_SCRIPT = r"""
import builtins
import os
import socket


def _deny_network(*args, **kwargs):
    raise AssertionError("network access during import")


socket.socket.connect = _deny_network
socket.create_connection = _deny_network

import neo4j


def _deny_driver(*args, **kwargs):
    raise AssertionError("Neo4j driver constructed during import")


neo4j.GraphDatabase.driver = _deny_driver

_real_open = builtins.open
_cwd = os.path.realpath(os.getcwd())


def _guard_open(file, mode="r", *args, **kwargs):
    if any(flag in mode for flag in ("w", "a", "x")):
        target = os.path.realpath(str(file))
        if target == _cwd or target.startswith(_cwd + os.sep):
            raise AssertionError(f"persistent write during import: {target}")
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _guard_open

import backend.api.endpoints  # noqa: E402
import backend.adapters.ai  # noqa: E402
import backend.adapters.artifacts  # noqa: E402
import backend.adapters.embedding  # noqa: E402
import backend.adapters.graph  # noqa: E402
import backend.adapters.postgres  # noqa: E402
import backend.container  # noqa: E402
import backend.graph.manager  # noqa: E402
import backend.graph.traversal  # noqa: E402
import backend.graph.vector_search  # noqa: E402
import backend.main  # noqa: E402
import backend.ports  # noqa: E402
import backend.services.character_service  # noqa: E402
import backend.services.world_service  # noqa: E402

print("IMPORT_SAFE")
"""


def test_importing_application_performs_no_external_work(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_SCRIPT],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert "IMPORT_SAFE" in result.stdout
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "backend").exists()
