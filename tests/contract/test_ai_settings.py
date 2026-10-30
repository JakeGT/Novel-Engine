"""A workspace chooses which AI provider and model its assistant uses."""

import pytest

from backend.services.canon_character_workflow import CharacterQuestion, CharacterQuestionSheet
from tests.contract.test_workspace_scope import (
    _create_project,
    _create_workspace,
    _hosted_login,
    _publish_schema,
)

pytestmark = pytest.mark.contract

SHEET = CharacterQuestionSheet(
    questions=[CharacterQuestion(id="q1", question="Where?", category="place")]
)


def _studio(client, container, slug: str = "studio") -> tuple[dict[str, str], str]:
    csrf = _hosted_login(client, container)
    workspace_id = _create_workspace(client, csrf, slug)
    project_id = _create_project(client, csrf, workspace_id, "novel")
    _publish_schema(client, csrf, workspace_id, project_id)
    return {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}, project_id


def _connect(client, headers, provider: str, key: str) -> None:
    created = client.post(
        "/api/v1/credentials",
        json={"provider": provider, "label": provider, "key": key},
        headers=headers,
    )
    assert created.status_code == 201, created.text


def _ask(client, headers, project_id: str):
    return client.post(
        f"/api/v1/projects/{project_id}/assist/questions",
        json={"description": "A harbor pilot."},
        headers=headers,
    )


def test_the_major_providers_are_offered_with_where_to_get_a_key(hosted_client, hosted_container):
    _hosted_login(hosted_client, hosted_container)

    providers = hosted_client.get("/api/v1/ai/providers").json()

    assert [item["id"] for item in providers] == [
        "openrouter",
        "anthropic",
        "openai",
        "google",
        "xai",
        "mistral",
        "deepseek",
    ]
    assert all(item["key_url"].startswith("https://") for item in providers)
    # No provider address or other routing detail is part of the contract.
    assert all("base_url" not in item for item in providers)


def test_a_single_connected_key_is_used_with_its_suggested_model(hosted_client, hosted_container):
    headers, project_id = _studio(hosted_client, hosted_container)
    assert hosted_client.get("/api/v1/ai/settings", headers=headers).json() == {
        "provider": None,
        "model": None,
        "chosen": False,
    }
    assert _ask(hosted_client, headers, project_id).status_code == 503

    _connect(hosted_client, headers, "anthropic", "sk-ant-secret-key-000001")

    assert hosted_client.get("/api/v1/ai/settings", headers=headers).json() == {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "chosen": False,
    }
    hosted_container.ai.queue(SHEET)
    assert _ask(hosted_client, headers, project_id).status_code == 200
    call = hosted_container.ai.calls[-1]
    assert call["base_url"] == "https://api.anthropic.com/v1/"
    assert call["api_key"] == "sk-ant-secret-key-000001"
    assert call["route_model"] == "claude-sonnet-5-5"


def test_an_owner_picks_the_provider_and_model_from_the_providers_own_list(
    hosted_client, hosted_container
):
    headers, project_id = _studio(hosted_client, hosted_container)
    _connect(hosted_client, headers, "openrouter", "sk-or-secret-key-000001")
    _connect(hosted_client, headers, "openai", "sk-openai-secret-key-0001")
    hosted_container.ai.models = ["gpt-large", "gpt-small"]

    listed = hosted_client.get("/api/v1/ai/models", params={"provider": "openai"}, headers=headers)

    assert listed.json() == {"models": ["gpt-large", "gpt-small"], "listed": True}
    # The list is read with that provider's key, at that provider's address.
    assert hosted_container.ai.model_list_calls[-1] == {
        "api_key": "sk-openai-secret-key-0001",
        "base_url": "https://api.openai.com/v1",
    }
    assert "secret" not in listed.text

    chosen = hosted_client.put(
        "/api/v1/ai/settings", json={"provider": "openai", "model": "gpt-large"}, headers=headers
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json() == {"provider": "openai", "model": "gpt-large", "chosen": True}

    hosted_container.ai.queue(SHEET)
    assert _ask(hosted_client, headers, project_id).status_code == 200
    call = hosted_container.ai.calls[-1]
    assert call["base_url"] == "https://api.openai.com/v1"
    assert call["api_key"] == "sk-openai-secret-key-0001"
    assert call["route_model"] == "gpt-large"


def test_a_provider_with_no_suggested_model_needs_one_chosen_before_use(
    hosted_client, hosted_container
):
    headers, project_id = _studio(hosted_client, hosted_container)
    _connect(hosted_client, headers, "google", "google-secret-key-000001")

    assert hosted_client.get("/api/v1/ai/settings", headers=headers).json() == {
        "provider": "google",
        "model": None,
        "chosen": False,
    }
    refused = _ask(hosted_client, headers, project_id)
    assert refused.status_code == 503 and "No model has been chosen" in refused.text
    assert hosted_container.ai.calls == []


def test_a_choice_must_name_a_connected_provider_and_a_plausible_model(
    hosted_client, hosted_container
):
    headers, _ = _studio(hosted_client, hosted_container)
    _connect(hosted_client, headers, "openrouter", "sk-or-secret-key-000001")

    for body in (
        {"provider": "mistral", "model": "mistral-large-latest"},  # no key for it
        {"provider": "nonesuch", "model": "x"},
        {"provider": "openrouter", "model": "two words"},
        {"provider": "openrouter", "model": "../../etc/passwd"},
    ):
        refused = hosted_client.put("/api/v1/ai/settings", json=body, headers=headers)
        assert refused.status_code == 400, (body, refused.text)
    listing = hosted_client.get(
        "/api/v1/ai/models", params={"provider": "mistral"}, headers=headers
    )
    assert listing.status_code == 400


def test_a_provider_that_will_not_list_models_still_lets_one_be_typed(
    hosted_client, hosted_container
):
    from backend.ports import AIProviderError

    headers, _ = _studio(hosted_client, hosted_container)
    _connect(hosted_client, headers, "anthropic", "sk-ant-secret-key-000001")
    hosted_container.ai.models = AIProviderError("model provider request failed")

    listed = hosted_client.get(
        "/api/v1/ai/models", params={"provider": "anthropic"}, headers=headers
    )

    assert listed.status_code == 200 and listed.json() == {"models": [], "listed": False}


def test_removing_the_chosen_key_falls_back_to_one_that_remains(hosted_client, hosted_container):
    headers, _ = _studio(hosted_client, hosted_container)
    _connect(hosted_client, headers, "openrouter", "sk-or-secret-key-000001")
    _connect(hosted_client, headers, "deepseek", "sk-deepseek-secret-key-01")
    hosted_client.put(
        "/api/v1/ai/settings",
        json={"provider": "deepseek", "model": "deepseek-chat"},
        headers=headers,
    )
    keys = hosted_client.get("/api/v1/credentials", headers=headers).json()
    deepseek = next(item for item in keys if item["provider"] == "deepseek")

    hosted_client.post(f"/api/v1/credentials/{deepseek['id']}/revoke", headers=headers)

    settings = hosted_client.get("/api/v1/ai/settings", headers=headers).json()
    assert settings["provider"] == "openrouter" and settings["chosen"] is False


def test_ai_settings_require_a_session(api_client) -> None:
    assert api_client.get("/api/v1/ai/settings").status_code == 401
    assert api_client.get("/api/v1/ai/providers").status_code == 401


def test_an_owner_sets_a_daily_allowance_and_it_is_shown_before_it_is_spent(
    hosted_client, hosted_container
):
    headers, project_id = _studio(hosted_client, hosted_container)
    _connect(hosted_client, headers, "openrouter", "sk-or-key")

    before = hosted_client.get("/api/v1/ai/usage", headers=headers).json()
    assert before["used_tokens"] == 0 and before["workspace_limit_tokens"] is None
    assert before["limit_tokens"] == before["site_limit_tokens"] == 2_000_000

    too_small = hosted_client.put("/api/v1/ai/usage", json={"daily_tokens": 5}, headers=headers)
    assert too_small.status_code == 422

    chosen = hosted_client.put("/api/v1/ai/usage", json={"daily_tokens": 20_000}, headers=headers)
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["limit_tokens"] == 20_000

    # One call is counted against it.
    hosted_container.ai.queue(SHEET)
    assert _ask(hosted_client, headers, project_id).status_code == 200
    spent = hosted_client.get("/api/v1/ai/usage", headers=headers).json()
    assert spent["requests"] == 1

    # Once it is used up the assistant refuses plainly, before calling the provider.
    # (The scripted model reports no tokens, so a spent record stands in for real use.)
    first = hosted_container.usage.records[0]
    hosted_container.usage.records.append(
        first.model_copy(update={"id": "usage-spent", "prompt_tokens": 25_000})
    )
    assert hosted_client.get("/api/v1/ai/usage", headers=headers).json()["used_tokens"] == 25_000
    calls = len(hosted_container.ai.calls)
    refused = _ask(hosted_client, headers, project_id)
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "usage_limit"
    assert "everything can still be done by hand" in refused.json()["detail"]
    assert len(hosted_container.ai.calls) == calls

    # Removing the workspace's own limit leaves the site's.
    cleared = hosted_client.put("/api/v1/ai/usage", json={"daily_tokens": None}, headers=headers)
    assert cleared.json()["limit_tokens"] == 2_000_000
    assert _ask_ok(hosted_client, hosted_container, headers, project_id)


def _ask_ok(client, container, headers, project_id: str) -> bool:
    container.ai.queue(SHEET)
    return _ask(client, headers, project_id).status_code == 200
