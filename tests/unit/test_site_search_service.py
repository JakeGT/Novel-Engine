"""The site search service: off-loop work, the key check, and a build's outcome."""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest

from backend.adapters.embedding_api import EmbeddingCheck
from backend.domain.identity import utcnow
from backend.domain.site_search import SiteSearchSecret, SiteSearchSetup
from backend.services.embedding_index import BuildCancelled
from backend.services.site_search import SiteSearchService

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self) -> None:
        self.setup: SiteSearchSetup | None = None
        self.secret: SiteSearchSecret | None = None
        self.calls: dict[str, int] = {}

    @property
    def is_configured(self) -> bool:
        return True

    def _mark(self, name: str) -> None:
        self.calls[name] = threading.get_ident()

    def get_setup(self) -> SiteSearchSetup | None:
        self._mark("get_setup")
        return self.setup

    def get_secret(self) -> SiteSearchSecret | None:
        self._mark("get_secret")
        return self.secret

    def resolve_plaintext(self) -> str:
        self._mark("resolve_plaintext")
        return "sk-test-not-real"

    def save_setup(self, *, provider, model, dimension, base_url, updated_by, verified):
        self._mark("save_setup")
        self.setup = SiteSearchSetup(
            provider=provider,
            model=model,
            dimension=dimension,
            base_url=base_url,
            version=(self.setup.version + 1 if self.setup is not None else 1),
            updated_by=updated_by,
            updated_at=utcnow(),
            verified=verified,
        )
        return self.setup

    def save_secret(self, *, provider, host, plaintext_key, created_by):
        self._mark("save_secret")
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
        self._mark("delete_secret")
        self.secret = None

    def delete_all(self) -> None:
        self._mark("delete_all")
        self.setup = None
        self.secret = None

    def current_version_number(self) -> int:
        self._mark("current_version_number")
        return self.setup.version if self.setup is not None else 0


class _Embedder:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def _mark(self, name: str) -> None:
        self.calls[name] = threading.get_ident()

    @property
    def source(self) -> str:
        self._mark("source")
        return "page"

    def available(self) -> bool:
        self._mark("available")
        return True

    def built_in_info(self) -> dict[str, object] | None:
        self._mark("built_in_info")
        return None

    def refresh(self) -> None:
        self._mark("refresh")


class _Index:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def status(self):
        return SimpleNamespace(
            active=None,
            building=None,
            building_lag=None,
            searchable=False,
            lag={"total": 0},
        )

    async def start_build(self, *, note: str | None = None):
        return SimpleNamespace(id="generation-1")

    async def build_all(self, generation_id: str) -> None:
        if self.error is not None:
            raise self.error

    async def activate(self, generation_id: str) -> None:
        return None

    async def cancel(self) -> None:
        return None


class _Probe:
    def __init__(self, captured: dict, calls: dict[str, int]) -> None:
        self._captured = captured
        self._calls = calls

    def check(self, word: str) -> EmbeddingCheck:
        self._calls["probe_check"] = threading.get_ident()
        return EmbeddingCheck(ok=True)


def _service(monkeypatch, *, index: _Index | None = None):
    store, embedder = _Store(), _Embedder()
    captured: dict = {}
    probe_calls: dict[str, int] = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return _Probe(captured, probe_calls)

    monkeypatch.setattr("backend.services.site_search.api_embedder", factory)
    service = SiteSearchService(
        settings=SimpleNamespace(is_hosted=False),
        store=store,
        embedder=embedder,
        index=index or _Index(),
    )
    return service, store, embedder, captured, probe_calls


@pytest.mark.asyncio
async def test_store_embedder_and_probe_work_runs_off_the_event_loop(monkeypatch) -> None:
    service, store, embedder, captured, probe_calls = _service(monkeypatch)
    loop_thread = threading.get_ident()

    await service.save(
        body=SimpleNamespace(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
            base_url=None,
            api_key="sk-test-not-real",
        ),
        actor_user_id="u1",
        sign_in_needed=False,
    )
    await service.state(sign_in_needed=False)
    await service.save(
        body=SimpleNamespace(
            provider="openai",
            model="text-embedding-3-large",
            dimension=1536,
            base_url=None,
            api_key=None,
        ),
        actor_user_id="u1",
        sign_in_needed=False,
    )
    await service.save(
        body=SimpleNamespace(
            provider="custom",
            model="nomic-embed-text",
            dimension=8,
            base_url="http://localhost:11434/v1",
            api_key=None,
        ),
        actor_user_id="u1",
        sign_in_needed=False,
    )
    await service.forget(sign_in_needed=False)

    assert captured["timeout_seconds"] == 15.0
    expected_store_calls = {
        "get_setup",
        "get_secret",
        "resolve_plaintext",
        "save_setup",
        "save_secret",
        "delete_secret",
        "delete_all",
    }
    assert expected_store_calls <= set(store.calls)
    expected_embedder_calls = {"source", "available", "built_in_info", "refresh"}
    assert expected_embedder_calls <= set(embedder.calls)
    assert "probe_check" in probe_calls

    threads = [*store.calls.values(), *embedder.calls.values(), *probe_calls.values()]
    assert threads and all(thread != loop_thread for thread in threads)


@pytest.mark.asyncio
async def test_a_failed_build_logs_its_error_type_once_and_is_retrieved(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    service, _store, _embedder, _captured, _probe = _service(
        monkeypatch, index=_Index(RuntimeError("worker crashed"))
    )

    with caplog.at_level(logging.ERROR):
        await service.start_build(sign_in_needed=False)
        assert service._build_task is not None
        try:
            await service._build_task
        except RuntimeError:
            pass
        await asyncio.sleep(0)

    records = [
        record for record in caplog.records if record.getMessage() == "site.search_build_failed"
    ]
    assert len(records) == 1
    assert records[0].fields == {"error_type": "RuntimeError"}
    assert service._build_task.exception() is not None


@pytest.mark.asyncio
async def test_a_cancelled_build_logs_stopped_not_failed(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    service, _store, _embedder, _captured, _probe = _service(
        monkeypatch, index=_Index(BuildCancelled("the person asked it to stop"))
    )

    with caplog.at_level(logging.INFO):
        await service.start_build(sign_in_needed=False)
        assert service._build_task is not None
        try:
            await service._build_task
        except BuildCancelled:
            pass
        await asyncio.sleep(0)

    stopped = [
        record for record in caplog.records if record.getMessage() == "site.search_build_stopped"
    ]
    failed = [
        record for record in caplog.records if record.getMessage() == "site.search_build_failed"
    ]
    assert len(stopped) == 1 and not failed
    assert stopped[0].fields == {"error_type": "BuildCancelled"}
