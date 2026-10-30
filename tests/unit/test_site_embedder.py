"""The delegating SiteEmbedder: which embedder wins, and the key stays out of sight."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from backend.adapters.embedding_api import ApiTextEmbedding
from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.embedding_site import SiteEmbedder
from backend.domain.site_search import SiteSearchSecret, SiteSearchSetup

pytestmark = pytest.mark.unit


class FakeStore:
    def __init__(self, *, setup=None, secret=None, plaintext="", configured=True) -> None:
        self.setup = setup
        self.secret = secret
        self.plaintext = plaintext
        self.configured = configured
        self.unreadable = False

    @property
    def is_configured(self) -> bool:
        return self.configured

    def get_setup(self):
        return self.setup

    def get_secret(self):
        return self.secret

    def current_version_number(self) -> int:
        if self.unreadable:
            raise RuntimeError("the database is down")
        return self.setup.version if self.setup is not None else 0

    def resolve_plaintext(self) -> str:
        from backend.domain.credentials import CredentialUnavailable

        if not self.configured or not self.plaintext:
            raise CredentialUnavailable("no key")
        return self.plaintext


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {"EMBEDDING_PROVIDER": "minilm"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _setup(
    provider="openai", base_url=None, version=1, model="text-embedding-3-small"
) -> SiteSearchSetup:
    from datetime import UTC, datetime

    return SiteSearchSetup(
        provider=provider,
        model=model,
        dimension=8,
        base_url=base_url,
        version=version,
        updated_by="u1",
        updated_at=datetime.now(UTC),
        verified=True,
    )


def _secret(host="api.openai.com") -> SiteSearchSecret:
    from datetime import UTC, datetime

    return SiteSearchSecret(
        id="site",
        key_fingerprint="abc",
        key_last4="abcd",
        key_version=1,
        provider="openai",
        host=host,
        created_by="u1",
        updated_at=datetime.now(UTC),
    )


def test_server_settings_win_when_api_has_a_key() -> None:
    built_in = ApiTextEmbedding(
        base_url="https://api.openai.com/v1",
        api_key="sk-test-not-real",
        model="text-embedding-3-small",
        dimension=8,
    )
    embedder = SiteEmbedder(
        settings=_settings(EMBEDDING_PROVIDER="api"), store=FakeStore(), built_in=built_in
    )
    assert embedder.source == "server"
    assert embedder.current() is built_in


def test_a_page_setup_wins_when_the_server_is_a_plain_model() -> None:
    store = FakeStore(setup=_setup(), secret=_secret(), plaintext="sk-page-not-real")
    embedder = SiteEmbedder(
        settings=_settings(), store=store, built_in=DeterministicEmbedding(dimension=8)
    )
    assert embedder.source == "page"
    assert isinstance(embedder.current(), ApiTextEmbedding)
    assert embedder.current().space.dimension == 8


def test_the_servers_own_model_wins_when_nothing_is_saved() -> None:
    built_in = DeterministicEmbedding(dimension=8)
    embedder = SiteEmbedder(settings=_settings(), store=FakeStore(), built_in=built_in)
    assert embedder.source == "built_in"
    assert embedder.current() is built_in


def test_nothing_is_off() -> None:
    embedder = SiteEmbedder(
        settings=_settings(EMBEDDING_PROVIDER="none"), store=FakeStore(), built_in=None
    )
    assert embedder.source == "off"
    assert not embedder.available()
    assert embedder.health().available is False
    assert embedder.space.model == "off"


def test_a_version_change_switches_the_embedder() -> None:
    store = FakeStore(setup=_setup(), secret=_secret(), plaintext="sk-page-not-real")
    embedder = SiteEmbedder(
        settings=_settings(), store=store, built_in=DeterministicEmbedding(dimension=8)
    )
    assert embedder.source == "page"
    store.setup = _setup(
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        version=2,
        model="mistral-embed",
    )
    store.secret = _secret(host="api.mistral.ai")
    store.plaintext = "sk-mistral-not-real"
    embedder.refresh()
    assert embedder.source == "page"
    assert embedder.current().space.model == "mistral-embed"
    assert embedder.current().space.revision == "api:api.mistral.ai"


def test_the_key_never_appears_in_a_repr_or_log(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore(setup=_setup(), secret=_secret(), plaintext="sk-super-secret-key")
    embedder = SiteEmbedder(
        settings=_settings(), store=store, built_in=DeterministicEmbedding(dimension=8)
    )
    with caplog.at_level(logging.DEBUG):
        embedder.refresh()
    assert "sk-super-secret-key" not in repr(embedder)
    assert "sk-super-secret-key" not in caplog.text
    assert "sk-super-secret-key" not in repr(embedder.current())


def test_a_store_outage_keeps_the_embedder_already_chosen_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(setup=_setup(), secret=_secret(), plaintext="sk-page-not-real")
    built_in = DeterministicEmbedding(dimension=8)
    embedder = SiteEmbedder(settings=_settings(), store=store, built_in=built_in)
    assert embedder.source == "page"

    store.unreadable = True
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            embedder._checked_at = 0
            assert embedder.source == "page"
            assert embedder.available()
    events = [
        record for record in caplog.records if record.getMessage() == "site.search_setup_unreadable"
    ]
    assert len(events) == 1
    assert events[0].fields == {"error_type": "RuntimeError"}

    # The store is readable again: the next poll succeeds and the warning may repeat
    # if it fails again later.
    store.unreadable = False
    store.setup = _setup(
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        version=2,
        model="mistral-embed",
    )
    store.secret = _secret(host="api.mistral.ai")
    store.plaintext = "sk-mistral-not-real"
    embedder._checked_at = 0
    assert embedder.current().space.model == "mistral-embed"
    store.unreadable = True
    with caplog.at_level(logging.WARNING):
        embedder._checked_at = 0
        assert embedder.source == "page"
    again = [
        record for record in caplog.records if record.getMessage() == "site.search_setup_unreadable"
    ]
    assert len(again) == 2


def test_a_store_outage_before_anything_was_chosen_falls_back_to_the_server_model() -> None:
    store = FakeStore()
    store.unreadable = True
    built_in = DeterministicEmbedding(dimension=8)
    embedder = SiteEmbedder(settings=_settings(), store=store, built_in=built_in)

    assert embedder.source == "built_in"
    assert embedder.current() is built_in
    assert embedder.available()


def test_a_store_outage_before_anything_was_chosen_falls_back_to_off() -> None:
    store = FakeStore()
    store.unreadable = True
    embedder = SiteEmbedder(
        settings=_settings(EMBEDDING_PROVIDER="none"), store=store, built_in=None
    )

    assert embedder.source == "off"
    assert not embedder.available()


def test_an_explicit_refresh_may_still_raise_after_a_save() -> None:
    store = FakeStore()
    store.unreadable = True
    embedder = SiteEmbedder(settings=_settings(), store=store, built_in=None)

    with pytest.raises(RuntimeError, match="database is down"):
        embedder.refresh()
