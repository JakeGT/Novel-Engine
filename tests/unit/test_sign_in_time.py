"""When a hosted sign-in counts as recent, and which return paths are kept."""

from datetime import UTC, datetime, timedelta

from backend.domain.identity import IdentityClaims, utcnow
from backend.services.auth_service import _signed_in_at, safe_return_to


def _claims(auth_time: int | None) -> IdentityClaims:
    return IdentityClaims(issuer="https://id.example", subject="s1", auth_time=auth_time)


def test_a_sign_in_the_provider_answered_from_its_own_session_is_not_recent() -> None:
    an_hour_ago = int((utcnow() - timedelta(hours=1)).timestamp())
    signed_in = _signed_in_at(_claims(an_hour_ago))
    assert signed_in == datetime.fromtimestamp(an_hour_ago, tz=UTC)


def test_without_auth_time_the_sign_in_is_now_and_never_in_the_future() -> None:
    before = utcnow()
    assert before <= _signed_in_at(_claims(None)) <= utcnow()
    tomorrow = int((utcnow() + timedelta(days=1)).timestamp())
    assert _signed_in_at(_claims(tomorrow)) <= utcnow()


def test_a_return_path_with_spaces_or_control_characters_is_ignored() -> None:
    assert safe_return_to("/site") == "/site"
    for unsafe in ("/\t/evil.example", "/\n/evil.example", "/ /evil", "/site\x00", "/\x7f"):
        assert safe_return_to(unsafe) is None
