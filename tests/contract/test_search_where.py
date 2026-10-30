"""Authors are told where their writing goes to be searchable by meaning (GET /ai/search)."""

import httpx
import pytest

from backend.adapters.embedding_api import ApiTextEmbedding
from backend.adapters.embedding_fake import DeterministicEmbedding

pytestmark = pytest.mark.contract


def _service(**overrides: object) -> ApiTextEmbedding:
    options: dict[str, object] = {
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-not-real",
        "model": "text-embedding-3-small",
        "dimension": 8,
        "name": "OpenAI",
        "transport": httpx.MockTransport(lambda request: httpx.Response(500)),
    }
    options.update(overrides)
    return ApiTextEmbedding(**options)  # type: ignore[arg-type]


def test_a_service_is_named_with_its_host_and_never_its_key(auth_client, fake_container):
    fake_container.multimodal_embedder = _service()
    fake_container.vectors.searchable = lambda: None

    answer = auth_client.get("/api/v1/ai/search")

    assert answer.status_code == 200
    assert answer.json() == {
        "kind": "service",
        "name": "OpenAI",
        "host": "api.openai.com",
        "in_use": False,
    }
    assert "sk-test" not in answer.text


def test_a_model_on_this_machine_keeps_the_words_on_the_server(auth_client, fake_container):
    fake_container.multimodal_embedder = _service(base_url="http://localhost:11434/v1", api_key="")
    fake_container.vectors.searchable = lambda: object()

    answer = auth_client.get("/api/v1/ai/search").json()

    assert answer == {"kind": "server", "name": None, "host": None, "in_use": True}


def test_a_model_on_the_server_itself_is_said_so(auth_client, fake_container):
    fake_container.multimodal_embedder = DeterministicEmbedding(dimension=8)
    fake_container.vectors.searchable = lambda: object()

    assert auth_client.get("/api/v1/ai/search").json()["kind"] == "server"


def test_search_by_meaning_off_or_not_set_up_is_off(auth_client, fake_container):
    fake_container.multimodal_embedder = None
    assert auth_client.get("/api/v1/ai/search").json() == {
        "kind": "off",
        "name": None,
        "host": None,
        "in_use": False,
    }
    # A service with no key cannot be used, so nothing is sent and it is off.
    fake_container.multimodal_embedder = _service(api_key="")
    assert auth_client.get("/api/v1/ai/search").json()["kind"] == "off"


def test_it_needs_a_session(api_client):
    assert api_client.get("/api/v1/ai/search").status_code == 401
