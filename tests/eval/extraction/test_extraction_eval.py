"""Reading prose into suggested changes, against labelled passages (EXT-008).

``test_reading_with_recorded_answers`` runs everywhere: the scripted model replays a
hand-written answer per case, so the harness, the reading pipeline's filtering and the
scorer are exercised and pinned (regenerate with
``NOVEL_EVAL_PIN=1 uv run pytest -q tests/eval/extraction``). It measures no model.

``test_reading_with_the_configured_model`` is the only measurement of a real model. It
is marked ``llm`` (never in ``make test`` or CI) and runs only from ``make eval-model``,
which asks the person to confirm the spend first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.eval.extraction.harness import estimate_tokens, evaluate
from tests.eval.report import record

BASELINE = Path(__file__).with_name("baseline.json")
# Set by ``scripts/eval/run.py --model`` once the person has agreed to the spend.
CONFIRMED = "NOVEL_EVAL_CONFIRMED_TOKENS"


@pytest.mark.eval
@pytest.mark.contract
def test_reading_with_recorded_answers(auth_client, fake_container) -> None:
    measured = evaluate(auth_client, fake_container, model="scripted (recorded answers)")
    record("extraction", measured)
    if os.environ.get("NOVEL_EVAL_PIN"):
        BASELINE.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
    assert measured == json.loads(BASELINE.read_text(encoding="utf-8"))
    # What the replay shows the pipeline does: an invented quote is dropped, while a
    # fact only said aloud, or only near the truth, still reaches review as a fact.
    assert measured["discarded_by_reading"] == 1
    assert measured["said_or_near_made_facts"] == 2
    # Every replayed answer was used, and nothing else was asked.
    assert fake_container.ai.remaining == 0


@pytest.mark.eval
@pytest.mark.llm
@pytest.mark.contract
def test_reading_with_the_configured_model(auth_client, fake_container) -> None:
    if os.environ.get("CI"):
        pytest.skip("A real model is never called in CI.")
    confirmed = int(os.environ.get(CONFIRMED) or 0)
    needed = estimate_tokens()
    if confirmed < needed:
        pytest.skip(
            f"Needs about {needed} tokens of the configured provider; run `make eval-model` "
            "and confirm the spend."
        )
    from backend.adapters.ai import OpenAIGateway
    from backend.core.config import Settings

    settings = Settings()
    live = OpenAIGateway(
        api_key=settings.OPENAI_API_KEY,
        default_model=settings.AI_MODEL,
        provider=settings.AI_PROVIDER,
        base_url=settings.AI_BASE_URL,
    )
    if not live.is_available():
        pytest.skip("No provider is configured (OPENAI_API_KEY or AI_BASE_URL).")
    used = {"prompt": 0, "completion": 0, "calls": 0}

    async def generate_structured(**kwargs: Any) -> Any:
        response = await live.generate_structured(**kwargs)
        used["calls"] += 1
        used["prompt"] += response.usage.prompt_tokens or 0
        used["completion"] += response.usage.completion_tokens or 0
        return response

    # The application keeps its own gateway; only where its answers come from changes.
    fake_container.ai.generate_structured = generate_structured  # type: ignore[method-assign]
    measured = evaluate(
        auth_client,
        fake_container,
        answer=lambda _case, _ids: None,
        model=os.environ.get("NOVEL_EVAL_MODEL_LABEL")
        or f"{settings.AI_PROVIDER}:{settings.AI_MODEL}",
    )
    measured["usage"] = used
    measured["estimated_tokens"] = needed
    record("extraction_model", measured)
    assert measured["cases"] == used["calls"]
