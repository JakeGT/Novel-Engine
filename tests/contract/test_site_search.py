"""The site's search setup page: platform admins only, key checked then sealed."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from backend.adapters.embedding_api import ApiTextEmbedding, EmbeddingCheck
from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.embedding_site import SiteEmbedder
from backend.domain.identity import utcnow
from backend.domain.site_search import SiteSearchSecret, SiteSearchSetup
from backend.services.embedding_index import EmbeddingIndexService
from backend.services.site_search import SiteSearchService
from tests.embedding_doubles import InMemoryEmbeddingIndexStore

pytestmark = pytest.mark.contract


class FakeSiteStore:
    def __init__(self, *, configured: bool = True) -> None:
        self.setup: SiteSearchSetup | None = None
        self.secret: SiteSearchSecret | None = None
        self.plaintext: str | None = None
        self._configured = configured

    @property
    def is_configured(self) -> bool:
        return self._configured

    def get_setup(self) -> SiteSearchSetup | None:
        return self.setup

    def get_secret(self) -> SiteSearchSecret | None:
        return self.secret

    def current_version_number(self) -> int:
        return self.setup.version if self.setup is not None else 0

    def resolve_plaintext(self) -> str:
        assert self.plaintext is not None
        return self.plaintext

    def save_setup(self, *, provider, model, dimension, base_url, updated_by, verified):
        version = (self.setup.version + 1) if self.setup is not None else 1
        self.setup = SiteSearchSetup(
            provider=provider,
            model=model,
            dimension=dimension,
            base_url=base_url,
            version=version,
            updated_by=updated_by,
            updated_at=utcnow(),
            verified=verified,
        )
        return self.setup

    def save_secret(self, *, provider, host, plaintext_key, created_by):
        self.plaintext = plaintext_key
        self.secret = SiteSearchSecret(
            id="site",
            key_fingerprint="fingerprint",
            key_last4=plaintext_key[-4:],
            key_version=1,
            provider=provider,
            host=host,
            created_by=created_by,
            updated_at=utcnow(),
        )
        return self.secret

    def delete_secret(self) -> None:
        self.secret = None
        self.plaintext = None

    def delete_all(self) -> None:
        self.setup = None
        self.secret = None
        self.plaintext = None


def _build_stack(container, *, provider: str = "minilm", configured: bool = True, built_in=None):
    store = FakeSiteStore(configured=configured)
    container.settings.EMBEDDING_PROVIDER = provider
    if built_in is None:
        built_in = DeterministicEmbedding(dimension=8)
    embedder = SiteEmbedder(settings=container.settings, store=store, built_in=built_in)
    index = EmbeddingIndexService(index=InMemoryEmbeddingIndexStore(), embedder=embedder)
    service = SiteSearchService(
        settings=container.settings, store=store, embedder=embedder, index=index
    )
    container.site_search_store = store
    container.site_embedder = embedder
    container.site_search_service = service
    container.embedding_index_service = index
    return service, store, embedder


def _probe(result: EmbeddingCheck, captured: dict):
    def factory(**kwargs):
        captured.update(kwargs)
        return _FakeProbe(result)

    return factory


class _FakeProbe:
    def __init__(self, result: EmbeddingCheck) -> None:
        self.result = result

    def check(self, word: str) -> EmbeddingCheck:
        return self.result


def _put(client, body: dict):
    return client.put("/api/v1/site/search-setup", json=body)


def test_providers_are_listed(auth_client, fake_container):
    _build_stack(fake_container)
    answer = auth_client.get("/api/v1/site/search-providers")
    assert answer.status_code == 200
    providers = answer.json()
    assert [spec["id"] for spec in providers] == [
        "openai",
        "google",
        "mistral",
        "voyage",
        "openrouter",
        "custom",
    ]
    openai = providers[0]
    assert openai["label"] == "OpenAI"
    assert openai["base_url"] == "https://api.openai.com/v1"
    assert openai["needs_url"] is False
    assert openai["key_hint"] == "Starts with sk-"
    assert openai["models"][0]["default_dimension"] == 1536
    assert openai["dimension_choices"] is None
    voyage = next(spec for spec in providers if spec["id"] == "voyage")
    assert voyage["dimension_choices"] == [256, 512, 1024]
    custom = next(spec for spec in providers if spec["id"] == "custom")
    assert custom["needs_url"] is True and custom["key_optional_when_local"] is True


def test_the_initial_setup_reports_the_servers_model(auth_client, fake_container):
    _build_stack(fake_container)
    state = auth_client.get("/api/v1/site/search-setup").json()
    assert state["source"] == "built_in"
    assert state["editable"] is True
    assert state["kek_configured"] is True
    assert state["setup"] is None
    assert state["built_in"]["model"] == "fake-multimodal"
    assert state["sign_in_needed"] is False


def test_hosted_non_admin_is_refused(hosted_client, hosted_container):
    login = hosted_client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    hosted_client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)

    answer = hosted_client.get("/api/v1/site/search-setup")

    assert answer.status_code == 403
    assert answer.json()["code"] == "forbidden"


def _hosted_admin(hosted_client, hosted_container):
    login = hosted_client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    hosted_client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)
    csrf = hosted_client.cookies.get(hosted_container.settings.CSRF_COOKIE_NAME)
    hosted_client.headers["X-CSRF-Token"] = csrf
    store = hosted_container.auth_service._session_store
    session = next(iter(store.sessions.values()))
    hosted_container.settings.PLATFORM_ADMIN_USER_IDS = session.user_id
    return session


def test_a_change_needs_a_recent_sign_in(hosted_client, hosted_container):
    session = _hosted_admin(hosted_client, hosted_container)
    session.authenticated_at = utcnow() - timedelta(minutes=16)

    answer = _put(
        hosted_client,
        {"provider": "openai", "model": "text-embedding-3-small", "dimension": 1536},
    )

    assert answer.status_code == 403
    assert answer.json()["code"] == "sign_in_again"


def test_a_write_needs_csrf(api_client, fake_container):
    _build_stack(fake_container)
    api_client.get("/auth/login", follow_redirects=False)

    answer = _put(
        api_client,
        {"provider": "openai", "model": "text-embedding-3-small", "dimension": 1536},
    )

    assert answer.status_code == 403


def test_a_refused_key_is_not_kept(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    captured: dict = {}
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=False, status=401), captured),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 400
    assert answer.json()["code"] == "key_refused"
    assert "did not accept that key" in answer.json()["detail"]
    # The fixed word, never an author's text, is what the service received.
    assert captured["model"] == "text-embedding-3-small"
    assert fake_container.site_search_store.setup is None


def test_a_refused_model_is_not_kept(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=False, status=404), {}),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 400
    assert answer.json()["code"] == "model_refused"


def test_a_wrong_size_says_the_size_to_set(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=False, length=1024), {}),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 400
    assert answer.json()["code"] == "size_mismatch"
    assert "gave 1024 numbers, not 1536" in answer.json()["detail"]


def test_an_answer_that_is_not_an_embedding_service_is_refused(
    auth_client, fake_container, monkeypatch
):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=False, status=200, unreadable=True), {}),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 400
    assert answer.json()["code"] == "not_a_service"
    assert "not as an embedding service" in answer.json()["detail"]
    assert fake_container.site_search_store.setup is None


def test_an_unreachable_service_is_kept_unchecked(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=False, failure="ConnectError"), {}),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 200
    assert answer.json()["check"] == "unchecked"
    assert fake_container.site_search_store.setup.verified is False
    assert answer.json()["state"]["setup"]["key"]["set"] is True


def test_a_good_setup_is_saved_and_shown(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 200
    assert answer.json()["check"] == "ok"
    state = answer.json()["state"]
    assert state["source"] == "page"
    assert state["setup"]["provider"] == "openai"
    assert state["setup"]["model"] == "text-embedding-3-small"
    assert state["setup"]["dimension"] == 1536
    assert state["setup"]["key"]["set"] is True
    assert state["setup"]["verified"] is True
    assert "sk-test" not in answer.text


def test_the_saved_setup_shows_only_the_keys_last_four(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )
    saved = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )
    assert saved.status_code == 200

    answer = auth_client.get("/api/v1/site/search-setup")

    assert answer.status_code == 200
    assert answer.json()["setup"]["key"]["set"] is True
    assert answer.json()["setup"]["key"]["hint"] == "real"
    assert "sk-test-not-real" not in saved.text
    assert "sk-test-not-real" not in answer.text
    # The provider's key format is for the providers list, not for the stored key.
    assert answer.json()["setup"]["key"]["hint"] != "Starts with sk-"


def test_changing_the_provider_needs_the_key_again(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )
    _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    answer = _put(
        auth_client,
        {"provider": "mistral", "model": "mistral-embed", "dimension": 1024},
    )

    assert answer.status_code == 400
    assert "typed again" in answer.json()["detail"]


def test_an_unchanged_provider_keeps_the_stored_key(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )
    _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    answer = _put(
        auth_client,
        {"provider": "openai", "model": "text-embedding-3-large", "dimension": 1536},
    )

    assert answer.status_code == 200
    assert answer.json()["check"] == "ok"


def test_the_stored_secret_decides_whether_the_key_still_matches(auth_client, fake_container):
    _service, store, _embedder = _build_stack(fake_container)
    # A setup row that says openai but a key sealed for mistral: the secret, not the
    # previous setup, must decide whether the key can be reused.
    store.setup = SiteSearchSetup(
        provider="openai",
        model="text-embedding-3-small",
        dimension=1536,
        base_url=None,
        version=1,
        updated_by="u1",
        updated_at=utcnow(),
        verified=True,
    )
    store.secret = SiteSearchSecret(
        id="site",
        key_fingerprint="fingerprint",
        key_last4="real",
        key_version=1,
        provider="mistral",
        host="api.mistral.ai",
        created_by="u1",
        updated_at=utcnow(),
    )
    store.plaintext = "sk-test-not-real"

    answer = _put(
        auth_client,
        {"provider": "openai", "model": "text-embedding-3-large", "dimension": 1536},
    )

    assert answer.status_code == 400
    assert "typed again" in answer.json()["detail"]


def test_a_local_setup_deletes_a_key_saved_for_another_provider(
    auth_client, fake_container, monkeypatch
):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )
    first = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )
    assert first.status_code == 200

    answer = _put(
        auth_client,
        {
            "provider": "custom",
            "model": "nomic-embed-text",
            "dimension": 8,
            "base_url": "http://localhost:11434/v1",
        },
    )

    assert answer.status_code == 200
    assert answer.json()["state"]["setup"]["key"]["set"] is False
    assert fake_container.site_search_store.secret is None
    assert fake_container.site_search_store.plaintext is None


def test_a_server_setup_cannot_be_changed(auth_client, fake_container):
    built_in = ApiTextEmbedding(
        base_url="https://api.openai.com/v1",
        api_key="sk-test-not-real",
        model="text-embedding-3-small",
        dimension=8,
    )
    _build_stack(fake_container, provider="api", built_in=built_in)

    answer = _put(
        auth_client,
        {
            "provider": "mistral",
            "model": "mistral-embed",
            "dimension": 1024,
            "api_key": "sk-mistral-not-real",
        },
    )

    assert answer.status_code == 409


def test_a_key_cannot_be_saved_without_a_credential_key(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container, configured=False)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )

    answer = _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    assert answer.status_code == 409
    assert "CREDENTIAL_KEK" in answer.json()["detail"]


def test_auth_me_reports_platform_admin(auth_client):
    answer = auth_client.get("/auth/me").json()
    assert answer["platform_admin"] is True


def test_forgetting_the_setup_removes_it(auth_client, fake_container, monkeypatch):
    _build_stack(fake_container)
    monkeypatch.setattr(
        "backend.services.site_search.api_embedder",
        _probe(EmbeddingCheck(ok=True), {}),
    )
    _put(
        auth_client,
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "dimension": 1536,
            "api_key": "sk-test-not-real",
        },
    )

    answer = auth_client.delete("/api/v1/site/search-setup")

    assert answer.status_code == 200
    assert answer.json()["source"] == "built_in"
    assert answer.json()["setup"] is None
    assert fake_container.site_search_store.secret is None
