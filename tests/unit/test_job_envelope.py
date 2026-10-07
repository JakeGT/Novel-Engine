import pytest

from backend.domain.identity import AuthMode
from backend.domain.jobs import (
    AUTHORIZATION_POLICY_VERSION,
    AuthorizationSnapshot,
    JobAuthorizationError,
    JobEnvelope,
)

pytestmark = pytest.mark.unit


def _envelope(**overrides) -> JobEnvelope:
    values = {
        "job_id": "job_1",
        "kind": "character.questions",
        "actor_user_id": "usr_1",
        "workspace_id": "ws_1",
        "project_id": "prj_1",
        "auth_mode": AuthMode.LOCAL,
        "authorization": AuthorizationSnapshot(
            policy_version=AUTHORIZATION_POLICY_VERSION,
            actor_user_id="usr_1",
            role="owner",
        ),
        "correlation_id": "cid_1",
        "idempotency_key": "idem_1",
    }
    values.update(overrides)
    return JobEnvelope(**values)


def test_scope_is_rebuilt_from_persisted_fields_not_payload() -> None:
    envelope = _envelope(payload={"workspace_id": "ws_evil", "project_id": "prj_evil"})

    scope = envelope.scope()

    assert scope.workspace_id == "ws_1"
    assert scope.project_id == "prj_1"
    assert scope.actor_user_id == "usr_1"


def test_revalidate_accepts_current_policy() -> None:
    _envelope().revalidate()  # does not raise


def test_revalidate_rejects_stale_policy() -> None:
    envelope = _envelope(
        authorization=AuthorizationSnapshot(policy_version="0", actor_user_id="usr_1", role="owner")
    )

    with pytest.raises(JobAuthorizationError, match="stale"):
        envelope.revalidate()


def test_revalidate_rejects_missing_scope() -> None:
    with pytest.raises(JobAuthorizationError, match="missing"):
        _envelope(project_id="").revalidate()


def test_revalidate_rejects_actor_mismatch() -> None:
    envelope = _envelope(
        authorization=AuthorizationSnapshot(
            policy_version=AUTHORIZATION_POLICY_VERSION,
            actor_user_id="usr_other",
            role="owner",
        )
    )

    with pytest.raises(JobAuthorizationError, match="actor"):
        envelope.revalidate()
