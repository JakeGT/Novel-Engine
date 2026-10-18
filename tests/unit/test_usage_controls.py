"""The rules behind asking first, conversation budgets and reported costs (OPS-002, AST-EXP-011)."""

import math

import pytest

from backend.adapters.ai import usage_from
from backend.domain.errors import ValidationError
from backend.domain.usage_controls import (
    AssistantSettings,
    CheckProfile,
    ConversationBudgetExceeded,
    NeedsConfirmation,
    limits_for,
    lower,
    require_confirmation,
    require_within_conversation,
)

pytestmark = pytest.mark.unit


def test_a_request_over_the_threshold_needs_at_least_its_size_confirmed() -> None:
    require_confirmation(estimated_tokens=9_000, threshold=None, confirmed_tokens=None, doing="X")
    require_confirmation(estimated_tokens=900, threshold=1_000, confirmed_tokens=None, doing="X")
    with pytest.raises(NeedsConfirmation) as raised:
        require_confirmation(
            estimated_tokens=1_500, threshold=1_000, confirmed_tokens=1_499, doing="Writing it"
        )
    assert raised.value.estimated_tokens == 1_500
    assert raised.value.field_errors == {"estimated_tokens": "1500", "threshold_tokens": "1000"}
    assert str(raised.value).startswith("Writing it will use about 1,500 tokens")
    require_confirmation(
        estimated_tokens=1_500, threshold=1_000, confirmed_tokens=1_500, doing="Writing it"
    )


def test_a_confirmation_answers_one_request_of_about_the_size_shown() -> None:
    # Half again as large is still the same question; a blanket large value is not.
    require_confirmation(
        estimated_tokens=1_500, threshold=1_000, confirmed_tokens=2_250, doing="Writing it"
    )
    with pytest.raises(NeedsConfirmation):
        require_confirmation(
            estimated_tokens=1_500, threshold=1_000, confirmed_tokens=2_251, doing="Writing it"
        )
    with pytest.raises(NeedsConfirmation):
        require_confirmation(
            estimated_tokens=1_500, threshold=1_000, confirmed_tokens=10_000_000, doing="X"
        )


def test_thorough_checks_wait_between_5_and_60_minutes_as_the_studio_offers() -> None:
    AssistantSettings(check_minutes=60).validated()
    with pytest.raises(ValidationError, match="between 5 and 60 minutes"):
        AssistantSettings(check_minutes=61).validated()


def test_a_conversation_is_refused_past_its_budget_with_what_it_used() -> None:
    require_within_conversation(used=10_000, estimated_tokens=10_000, budget=None)
    require_within_conversation(used=1_000, estimated_tokens=4_000, budget=5_000)
    with pytest.raises(ConversationBudgetExceeded, match="1,001 of the 5,000"):
        require_within_conversation(used=1_001, estimated_tokens=4_000, budget=5_000)
    with pytest.raises(ConversationBudgetExceeded, match="alone"):
        require_within_conversation(used=0, estimated_tokens=6_000, budget=5_000)


def test_the_lower_of_the_story_and_the_workspace_applies() -> None:
    assert lower(None, None) is None
    assert lower(None, 7) == 7
    assert lower(9, 7) == 7
    story = AssistantSettings(confirm_above_tokens=50_000, conversation_tokens=None)
    workspace = AssistantSettings(confirm_above_tokens=20_000, conversation_tokens=80_000)
    found = limits_for(story, workspace)
    assert found.confirm_above_tokens == 20_000 and found.confirm_from == "workspace"
    assert found.conversation_tokens == 80_000


def test_settings_out_of_bounds_are_refused_in_plain_words() -> None:
    assert AssistantSettings(check_profile=CheckProfile.BALANCED).validated().scene_tokens > 0
    for bad in (
        AssistantSettings(scene_tokens=10),
        AssistantSettings(check_minutes=1),
        AssistantSettings(confirm_above_tokens=5),
        AssistantSettings(conversation_tokens=5),
    ):
        with pytest.raises(ValidationError):
            bad.validated()


def test_a_cost_is_kept_only_when_the_provider_reports_one() -> None:
    # OpenRouter's usage, with ``cost`` in credits worth a dollar each.
    reported = usage_from(
        {"prompt_tokens": 1_000, "completion_tokens": 50, "cost": 0.00042, "is_byok": False}
    )
    assert reported.cost_usd == pytest.approx(0.00042)
    assert usage_from({"prompt_tokens": 10, "completion_tokens": 5}).cost_usd is None
    for odd in ("free", -1, math.inf, True, None):
        assert usage_from({"prompt_tokens": 1, "cost": odd}).cost_usd is None
