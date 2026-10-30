"""What each model can take (CTX-013): the operator's table, a provider's list, and the
gateway negotiating per model rather than per route. No network: the list is a stand-in."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from backend.adapters.ai import OpenAIGateway
from backend.domain.model_capabilities import (
    ModelCapability,
    openrouter_capabilities,
    parse_operator_table,
)
from backend.domain.providers import set_request_model
from backend.services.model_capabilities import (
    OPENROUTER_MODELS_URL,
    REFRESH_SECONDS,
    RETRY_SECONDS,
    ModelCapabilityService,
)

pytestmark = pytest.mark.unit

LIST = {
    "data": [
        {
            "id": "vision/model",
            "context_length": 200000,
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "top_provider": {"max_completion_tokens": 8192},
            "supported_parameters": ["max_tokens", "structured_outputs"],
        },
        {
            "id": "words/only",
            "context_length": 64000,
            "architecture": {"input_modalities": ["text"]},
            "top_provider": {"max_completion_tokens": None},
            "supported_parameters": ["max_tokens"],
        },
        {"id": "", "context_length": 5},
        "not a model",
        {"id": "odd/shape", "architecture": "text", "context_length": "lots"},
    ]
}


class _Store:
    def __init__(self) -> None:
        self.saved: dict[str, tuple[list[ModelCapability], float]] = {}

    async def load(self, provider: str):
        return self.saved.get(provider, ([], 0.0))

    async def save(self, provider: str, found):
        self.saved[provider] = (list(found), time.time())


def test_a_providers_list_is_read_for_only_what_it_says():
    found = {item.model: item for item in openrouter_capabilities(LIST)}
    assert set(found) == {"vision/model", "words/only", "odd/shape"}
    vision = found["vision/model"]
    assert vision.sees_images is True and vision.context_tokens == 200000
    assert vision.max_output_tokens == 8192 and vision.structured is True
    assert vision.source == "provider"
    words = found["words/only"]
    assert words.sees_images is False and words.structured is False
    assert words.max_output_tokens is None
    odd = found["odd/shape"]
    assert odd.sees_images is None and odd.context_tokens is None and odd.structured is None
    assert openrouter_capabilities({"data": "nothing"}) == []
    assert openrouter_capabilities([]) == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("{not json", "not valid JSON"),
        ("[1]", "must be a JSON object"),
        ('{"nomodel": {}}', "must be provider/model"),
        ('{"openai/gpt": {"colour": 1}}', "unknown 'colour'"),
        ('{"openai/gpt": {"images": "yes"}}', "must be true or false"),
        ('{"openai/gpt": {"context_tokens": 0}}', "must be a whole number"),
        ('{"openai/gpt": {"max_images": true}}', "must be a whole number"),
    ],
)
def test_a_mistake_in_the_operators_table_is_said_plainly(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_operator_table(raw)


def test_the_settings_refuse_a_broken_table_at_start_up(settings_factory):
    with pytest.raises(ValueError, match="AI_MODEL_CAPABILITIES"):
        settings_factory(AI_MODEL_CAPABILITIES="{oops")
    assert settings_factory(AI_MODEL_CAPABILITIES='{"openai/gpt-4o": {"images": true}}')


def test_the_operators_table_wins_and_the_most_particular_pattern_is_used():
    service = ModelCapabilityService(
        table=json.dumps(
            {
                "openrouter/*": {"max_images": 2},
                "openrouter/vision/*": {"images": False, "context_tokens": 1000},
                "openrouter/vision/model": {"max_images": 1},
            }
        ),
        fetch=None,
    )
    service._remember("openrouter", openrouter_capabilities(LIST), time.time())
    exact = service.lookup("openrouter", "vision/model")
    # The exact entry over the list: its own values, the list's for the rest.
    assert exact is not None and exact.max_images == 1 and exact.sees_images is True
    assert exact.context_tokens == 200000
    other = service.lookup("openrouter", "vision/other")
    assert other is not None and other.sees_images is False and other.context_tokens == 1000
    assert other.model == "vision/other"
    anything = service.lookup("openrouter", "words/only")
    assert anything is not None and anything.max_images == 2 and anything.sees_images is False
    assert service.lookup("openai", "gpt-4o") is None


def test_a_list_is_read_once_a_day_kept_and_a_failure_waits_an_hour():
    now = [1_000_000.0]
    asked: list[str] = []
    store = _Store()

    async def fetch(url: str) -> bytes:
        asked.append(url)
        return json.dumps(LIST).encode()

    service = ModelCapabilityService(store=store, fetch=fetch, clock=lambda: now[0])
    asyncio.run(service.ensure("openrouter"))
    asyncio.run(service.ensure("openrouter"))
    assert asked == [OPENROUTER_MODELS_URL] and service.known_models("openrouter") == 3
    assert len(store.saved["openrouter"][0]) == 3
    # Providers without a public list are never asked.
    asyncio.run(service.ensure("openai"))
    assert asked == [OPENROUTER_MODELS_URL]

    now[0] += REFRESH_SECONDS + 1

    async def broken(url: str) -> bytes:
        asked.append("broken")
        raise OSError("no network")

    service._fetch = broken
    asyncio.run(service.ensure("openrouter"))
    asyncio.run(service.ensure("openrouter"))
    # Tried once; what was known is kept.
    assert asked.count("broken") == 1 and service.known_models("openrouter") == 3
    now[0] += RETRY_SECONDS + 1
    asyncio.run(service.ensure("openrouter"))
    assert asked.count("broken") == 2


def test_a_kept_list_is_used_after_a_restart_without_asking_again():
    store = _Store()
    store.saved["openrouter"] = (openrouter_capabilities(LIST), time.time())

    async def fetch(url: str) -> bytes:
        raise AssertionError("asked again")

    service = ModelCapabilityService(store=store, fetch=fetch)
    asyncio.run(service.ensure("openrouter"))
    assert service.lookup("openrouter", "words/only") is not None


def test_lists_are_not_read_when_the_operator_says_not_to():
    async def fetch(url: str) -> bytes:
        raise AssertionError("asked")

    service = ModelCapabilityService(fetch=fetch, read_lists=False)
    asyncio.run(service.ensure("openrouter"))
    assert service.known_models("openrouter") == 0


def test_a_list_of_thousands_of_models_is_read_quickly():
    """PERFORMANCE RULE: a big list (about 6 MB) is parsed off the event loop, in linear time."""
    big = {"data": [dict(LIST["data"][0], id=f"maker/model-{i}") for i in range(20_000)]}
    raw = json.dumps(big).encode()

    async def fetch(url: str) -> bytes:
        return raw

    service = ModelCapabilityService(fetch=fetch)
    started = time.perf_counter()
    asyncio.run(service.ensure("openrouter"))
    assert service.known_models("openrouter") == 20_000
    assert time.perf_counter() - started < 3.0


# ------------------------------------------------------------------ the gateway


def _gateway(service: ModelCapabilityService, **settings) -> OpenAIGateway:
    gateway = OpenAIGateway(
        api_key="k",
        default_model="vision/model",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        **settings,
    )
    gateway.attach_models(service)
    return gateway


def test_a_model_the_list_says_cannot_see_pictures_gets_words_even_where_allowed():
    service = ModelCapabilityService(fetch=None)
    service._remember("openrouter", openrouter_capabilities(LIST), time.time())
    gateway = _gateway(service, image_input=True, image_max_count=4)
    try:
        set_request_model("vision/model")
        vision = gateway.capabilities()
        assert vision.sees_images and vision.max_context_tokens == 200000
        assert vision.max_output_tokens == 8192 and vision.source == "provider"
        set_request_model("words/only")
        words = gateway.capabilities()
        assert not words.sees_images and words.max_images == 0
        assert words.structured_output is False and words.max_context_tokens == 64000
        set_request_model("unlisted/model")
        unknown = gateway.capabilities()
        # Not in the list: the route's own settings decide, as before.
        assert unknown.sees_images and unknown.source == "route"
    finally:
        set_request_model(None)


def test_the_list_never_sends_pictures_where_the_operator_has_not_allowed_them():
    service = ModelCapabilityService(fetch=None)
    service._remember("openrouter", openrouter_capabilities(LIST), time.time())
    gateway = _gateway(service, image_input=False)
    assert not gateway.capabilities().sees_images


def test_the_operators_table_lowers_how_many_pictures_go():
    service = ModelCapabilityService(
        table='{"openrouter/vision/model": {"max_images": 1}}', fetch=None
    )
    gateway = _gateway(service, image_input=True, image_max_count=4)
    assert gateway.capabilities().max_images == 1


def test_the_model_asked_about_decides_over_the_default():
    """A preview is made outside a request: it names the workspace's chosen model, and
    what that model takes is what is said, not the operator's default."""
    service = ModelCapabilityService(fetch=None)
    service._remember("openrouter", openrouter_capabilities(LIST), time.time())
    gateway = _gateway(service, image_input=True)
    gateway._default_model = "words/only"
    assert not gateway.capabilities().sees_images
    chosen = gateway.capabilities(model="vision/model")
    assert chosen.sees_images and chosen.model == "vision/model"
    assert chosen.max_context_tokens == 200000


def test_an_odd_entry_never_stops_the_rest_and_numbers_stay_in_range():
    from backend.domain.model_capabilities import MAX_LISTED_NUMBER

    raw = (
        '{"data": ['
        '{"id": "dicts/in/parameters", "supported_parameters": [{}, "response_format"],'
        ' "architecture": {"input_modalities": [{}, "image"]}},'
        '{"id": "too/big", "context_length": 1e999,'
        ' "top_provider": {"max_completion_tokens": 99999999999}},'
        '{"id": "many/digits", "context_length": "' + "9" * 4000 + '"},'
        '{"id": "fine", "context_length": 2147483647}'
        "]}"
    )
    found = {item.model: item for item in openrouter_capabilities(json.loads(raw))}
    assert set(found) == {"dicts/in/parameters", "too/big", "many/digits", "fine"}
    assert found["dicts/in/parameters"].structured is True
    assert found["dicts/in/parameters"].sees_images is True
    assert found["too/big"].context_tokens is None
    assert found["too/big"].max_output_tokens is None
    assert found["many/digits"].context_tokens is None
    assert found["fine"].context_tokens == MAX_LISTED_NUMBER


def test_half_a_million_characters_in_one_entry_are_read_quickly():
    """PERFORMANCE RULE: a 500,000-character number or list in one entry costs time in
    proportion to its length, and never stops the list being read."""
    entry = {
        "id": "huge/entry",
        "context_length": "9" * 500_000,
        "architecture": {"input_modalities": ["text"] * 100_000 + ["image"]},
        "supported_parameters": ["x"] * 100_000 + ["structured_outputs"],
    }
    started = time.perf_counter()
    (found,) = openrouter_capabilities({"data": [entry]})
    assert time.perf_counter() - started < 2.0
    assert found.context_tokens is None and found.sees_images and found.structured


def test_a_slow_list_never_holds_a_request_long_and_is_still_read():
    gate = asyncio.Event()

    async def slow(url: str) -> bytes:
        await gate.wait()
        return json.dumps(LIST).encode()

    service = ModelCapabilityService(fetch=slow, wait_seconds=0.05)

    async def go() -> None:
        started = time.perf_counter()
        # Bounded here too, so a request held until the list comes fails rather than hangs.
        await asyncio.wait_for(service.ensure("openrouter"), timeout=2.0)
        # Waited a moment, then went on with nothing known.
        assert time.perf_counter() - started < 1.0
        assert service.known_models("openrouter") == 0
        # A second request while it is still being read shares that reading.
        await service.ensure("openrouter")
        assert len(service._reading) == 1
        gate.set()
        await service._reading["openrouter"]
        assert service.known_models("openrouter") == 3

    asyncio.run(go())
