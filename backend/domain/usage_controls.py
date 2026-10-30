"""How much the assistant may do and spend without being asked each time.

Three controls, each chosen by the author for a story and, for the last two, also by a
workspace owner for everyone (the lower of the two applies):

- a *checking profile* (CON-009): how much checking with the assistant runs on its own.
  Checks without a model always run; these add checks with the assistant, within a
  token allowance per scene per day;
- a *confirmation threshold* (OPS-002): a single request estimated above it is sent only
  once the author has confirmed that size;
- a *conversation budget* (AST-EXP-011): the most one conversation with the assistant
  may use, counted from what the provider reported.

The soft threshold asks; the daily allowance and the conversation budget refuse. Nothing
here prices anything: money is shown only when the provider reports what a call cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.domain.errors import DomainError, ValidationError


class CheckProfile(StrEnum):
    # "Only when I ask": checks with the assistant run only when the author asks.
    ON_REQUEST = "on_request"
    # After keeping a draft, the scene is checked with the assistant.
    BALANCED = "balanced"
    # Also after saving, at most once every few minutes.
    THOROUGH = "thorough"


DEFAULT_SCENE_TOKENS = 40_000
MIN_SCENE_TOKENS = 5_000
MAX_SCENE_TOKENS = 1_000_000
DEFAULT_CHECK_MINUTES = 15
MIN_CHECK_MINUTES = 5
# The choices the studio offers run from 5 to 60 minutes; the server allows the same.
MAX_CHECK_MINUTES = 60
MIN_CONFIRM_TOKENS = 1_000
MIN_CONVERSATION_TOKENS = 5_000
MAX_CONTROL_TOKENS = 10_000_000
# How long the allowance per scene for automatic checks covers.
SCENE_ALLOWANCE_SECONDS = 86_400
# A confirmation covers one request of about the size the author was shown: no smaller
# than the estimate, and no more than half again as large. A blanket large value sent up
# front, without having been asked, is not a confirmation.
CONFIRM_SLACK = 1.5


@dataclass(frozen=True)
class AssistantSettings:
    """One story's choices, or (with an empty project) a workspace owner's ceilings."""

    check_profile: CheckProfile = CheckProfile.ON_REQUEST
    scene_tokens: int = DEFAULT_SCENE_TOKENS
    check_minutes: int = DEFAULT_CHECK_MINUTES
    confirm_above_tokens: int | None = None
    conversation_tokens: int | None = None

    def validated(self) -> AssistantSettings:
        if not MIN_SCENE_TOKENS <= self.scene_tokens <= MAX_SCENE_TOKENS:
            raise ValidationError(
                f"Choose between {MIN_SCENE_TOKENS:,} and {MAX_SCENE_TOKENS:,} tokens a scene."
            )
        if not MIN_CHECK_MINUTES <= self.check_minutes <= MAX_CHECK_MINUTES:
            raise ValidationError(
                f"Choose between {MIN_CHECK_MINUTES} and {MAX_CHECK_MINUTES} minutes."
            )
        if self.confirm_above_tokens is not None and not (
            MIN_CONFIRM_TOKENS <= self.confirm_above_tokens <= MAX_CONTROL_TOKENS
        ):
            raise ValidationError(
                f"Choose a size between {MIN_CONFIRM_TOKENS:,} and {MAX_CONTROL_TOKENS:,} "
                "tokens, or switch it off."
            )
        if self.conversation_tokens is not None and not (
            MIN_CONVERSATION_TOKENS <= self.conversation_tokens <= MAX_CONTROL_TOKENS
        ):
            raise ValidationError(
                f"Choose between {MIN_CONVERSATION_TOKENS:,} and {MAX_CONTROL_TOKENS:,} tokens "
                "for a conversation, or switch it off."
            )
        return self


def lower(*limits: int | None) -> int | None:
    """The strictest of the limits that are set; None when none is."""
    found = [value for value in limits if value is not None and value > 0]
    return min(found) if found else None


@dataclass(frozen=True)
class Limits:
    """What applies to one story: the lower of its own choice and the workspace's."""

    confirm_above_tokens: int | None = None
    conversation_tokens: int | None = None
    # Where the confirmation threshold comes from: "story" or "workspace".
    confirm_from: str | None = None


def limits_for(story: AssistantSettings, workspace: AssistantSettings) -> Limits:
    confirm = lower(story.confirm_above_tokens, workspace.confirm_above_tokens)
    source = None
    if confirm is not None:
        source = "story" if confirm == story.confirm_above_tokens else "workspace"
    return Limits(
        confirm_above_tokens=confirm,
        conversation_tokens=lower(story.conversation_tokens, workspace.conversation_tokens),
        confirm_from=source,
    )


class NeedsConfirmation(DomainError):
    """A request is larger than the author asked to be asked about. Nothing was sent.

    ``field_errors["estimated_tokens"]`` carries the estimate, so the same request can be
    sent again with ``X-Confirm-Tokens`` set to it once the author agrees."""

    status_code = 409
    code = "needs_confirmation"
    title = "Confirmation needed"

    def __init__(self, detail: str, *, estimated_tokens: int, threshold_tokens: int) -> None:
        super().__init__(
            detail,
            field_errors={
                "estimated_tokens": str(estimated_tokens),
                "threshold_tokens": str(threshold_tokens),
            },
        )
        self.estimated_tokens = estimated_tokens
        self.threshold_tokens = threshold_tokens


class ConversationBudgetExceeded(DomainError):
    """A conversation has used what one conversation may. Nothing was sent."""

    status_code = 409
    code = "conversation_budget"
    title = "Conversation budget used"


def require_confirmation(
    *, estimated_tokens: int, threshold: int | None, confirmed_tokens: int | None, doing: str
) -> None:
    """Refuse a request over the threshold unless the author confirmed its size: at
    least the estimate and at most ``CONFIRM_SLACK`` times it, so a confirmation answers
    the question asked for this request and is not a blanket permission.

    ``doing`` names the request in the author's words ("Writing these drafts")."""
    if threshold is None or estimated_tokens <= threshold:
        return
    if (
        confirmed_tokens is not None
        and estimated_tokens <= confirmed_tokens <= estimated_tokens * CONFIRM_SLACK
    ):
        return
    raise NeedsConfirmation(
        f"{doing} will use about {estimated_tokens:,} tokens, more than the "
        f"{threshold:,} you asked to be asked about first.",
        estimated_tokens=estimated_tokens,
        threshold_tokens=threshold,
    )


def require_within_conversation(*, used: int, estimated_tokens: int, budget: int | None) -> None:
    if budget is None or used + estimated_tokens <= budget:
        return
    if used == 0:
        raise ConversationBudgetExceeded(
            f"This request alone would use about {estimated_tokens:,} tokens, more than the "
            f"{budget:,} one conversation may use. Nothing was sent. Raise the limit on the "
            "AI assistant page, or do this by hand."
        )
    raise ConversationBudgetExceeded(
        f"This conversation has used {used:,} of the {budget:,} tokens one conversation may "
        f"use, and the next reply would need about {estimated_tokens:,} more. Nothing was "
        "sent, and what you typed is kept. Start a new conversation, or raise the limit on "
        "the AI assistant page."
    )


__all__ = [
    "CONFIRM_SLACK",
    "AssistantSettings",
    "CheckProfile",
    "ConversationBudgetExceeded",
    "Limits",
    "NeedsConfirmation",
    "limits_for",
    "lower",
    "require_confirmation",
    "require_within_conversation",
]
