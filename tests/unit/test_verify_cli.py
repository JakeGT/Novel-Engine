import hashlib

import pytest

from backend.devtools.verify import evaluate

pytestmark = pytest.mark.unit


def _checksum(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_consistent_state_reports_no_problems() -> None:
    canonical = '[{"op":"create_entity","id":"ent_1"}]'
    branches = [{"id": "b1", "head_revision": 1}]
    revisions = [{"branch_id": "b1", "revision": 1, "result_head": 1, "commit_id": "c1"}]
    envelopes = [{"commit_id": "c1", "canonical": canonical, "checksum": _checksum(canonical)}]
    watermarks = [{"projection": "graph", "branch_id": "b1", "revision": 1, "status": "current"}]
    assert evaluate(branches, revisions, envelopes, watermarks) == []


def test_detects_head_mismatch() -> None:
    branches = [{"id": "b1", "head_revision": 2}]
    revisions = [{"branch_id": "b1", "revision": 1, "result_head": 1, "commit_id": "c1"}]
    problems = evaluate(branches, revisions, [], [])
    assert any("head" in problem for problem in problems)


def test_detects_revision_result_head_mismatch() -> None:
    revisions = [{"branch_id": "b1", "revision": 2, "result_head": 1, "commit_id": "c1"}]
    problems = evaluate([], revisions, [], [])
    assert any("result_head" in problem for problem in problems)


def test_detects_envelope_checksum_mismatch() -> None:
    envelopes = [{"commit_id": "c1", "canonical": '[{"op":"x"}]', "checksum": "deadbeef"}]
    problems = evaluate([], [], envelopes, [])
    assert any("checksum mismatch" in problem for problem in problems)


def test_detects_watermark_ahead_and_false_current() -> None:
    branches = [{"id": "b1", "head_revision": 1}]
    ahead = [{"projection": "graph", "branch_id": "b1", "revision": 2, "status": "lagging"}]
    assert any("applied" in problem for problem in evaluate(branches, [], [], ahead))

    stale_current = [{"projection": "graph", "branch_id": "b1", "revision": 0, "status": "current"}]
    assert any("current" in problem for problem in evaluate(branches, [], [], stale_current))
