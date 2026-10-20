"""Typed model context: words, pictures, sound and film as parts (CTX-013, ADR-010).

A request to a model is made of parts. A ``TextPart`` is words; an ``ImagePart`` points at
one exact, authorized picture version (never its bytes, never a lasting public address);
``AudioPart`` and ``VideoPart`` are declared on the same contract and nothing makes them
yet. Every media part carries where it came from, why it is included, its authority and
visibility, and the words to use instead when the picture cannot be sent.

Negotiation decides, part by part, what is actually delivered to the route the request
will use, and records it:

1. the picture itself, when the route can see pictures and it fits the route's limits;
2. the picture's accepted description in words, recorded as a degradation, when the
   author wanted the picture sent and the route cannot take it;
3. a refusal before anything is spent, when the work needs the picture itself (describing
   it, say) and no route can take it.

A route that cannot see pictures is never told it saw one: a part it could not take is
described or only named, and the delivery record says so. Nothing here reads bytes or
calls a model; services load the bytes after negotiation, and the gateway checks the
route's limits again before sending.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class ContextChoice(StrEnum):
    """What the author chose for a picture in the assistant's context."""

    # Send the picture itself to an assistant that can see pictures.
    SEND = "send"
    # Use its accepted description in words.
    DESCRIBE = "describe"
    # Leave it out.
    LEAVE_OUT = "leave_out"


DEFAULT_CHOICE = ContextChoice.DESCRIBE

CHOICE_WORDS: dict[ContextChoice, str] = {
    ContextChoice.SEND: "Send the picture",
    ContextChoice.DESCRIBE: "Use its description",
    ContextChoice.LEAVE_OUT: "Leave out",
}


class Representation(StrEnum):
    """How a part actually reached the model."""

    PICTURE = "picture"
    # The author chose words: the accepted description.
    DESCRIPTION = "description"
    # The author wanted the picture sent; the route cannot see pictures, so the
    # accepted description went instead. A degradation.
    FALLBACK = "fallback"
    # Only that the picture exists, with no description to give. A degradation when the
    # picture was wanted.
    NAMED = "named"
    LEFT_OUT = "left_out"


REPRESENTATION_WORDS: dict[Representation, str] = {
    Representation.PICTURE: "The picture itself is sent.",
    Representation.DESCRIPTION: "Its accepted description is given in words.",
    Representation.FALLBACK: (
        "You chose to send the picture, but the assistant cannot see pictures, so its "
        "accepted description is given in words instead."
    ),
    Representation.NAMED: "Only that it exists is given; it has no accepted description yet.",
    Representation.LEFT_OUT: "You chose to leave it out.",
}


@dataclass(frozen=True)
class TextPart:
    key: str
    text: str
    kind: Literal["text"] = "text"
    purpose: str = ""
    reason: str = ""

    @property
    def tokens(self) -> int:
        return text_tokens(self.text)


@dataclass(frozen=True)
class _MediaPart:
    key: str
    asset_id: str
    version_id: str
    # Of the rendition that would be sent (the web copy when there is one).
    content_type: str
    byte_length: int
    checksum: str
    label: str
    reason: str = ""
    purpose: str = ""
    authority: str = "picture"
    visibility: str = "open"
    # The accepted description, to use when the original cannot be sent; "" when none.
    fallback_text: str = ""
    choice: ContextChoice = DEFAULT_CHOICE
    rendition: str = "original"


@dataclass(frozen=True)
class ImagePart(_MediaPart):
    kind: Literal["image"] = "image"
    width: int | None = None
    height: int | None = None

    @property
    def tokens(self) -> int:
        return image_tokens(self.width, self.height)


@dataclass(frozen=True)
class AudioPart(_MediaPart):
    """Declared on the shared contract; nothing produces sound parts yet."""

    kind: Literal["audio"] = "audio"
    start_ms: int | None = None
    end_ms: int | None = None

    @property
    def tokens(self) -> int:
        return text_tokens(self.fallback_text)


@dataclass(frozen=True)
class VideoPart(_MediaPart):
    """Declared on the shared contract; nothing produces film parts yet."""

    kind: Literal["video"] = "video"
    start_ms: int | None = None
    end_ms: int | None = None

    @property
    def tokens(self) -> int:
        return text_tokens(self.fallback_text)


ContextPart = TextPart | ImagePart | AudioPart | VideoPart
MediaPart = ImagePart | AudioPart | VideoPart

# English runs at roughly four tokens to every three words.
TOKENS_PER_WORD = 4 / 3
# A picture costs about width x height / 750 tokens, up to a model's native size
# (Anthropic vision docs: https://platform.claude.com/docs/en/build-with-claude/vision).
# The largest native size listed is 4,784 tokens; an unknown size is costed as a full
# 1,024-pixel web copy would be at worst, so an estimate never runs low.
PIXELS_PER_TOKEN = 750
MAX_IMAGE_TOKENS = 4784
MIN_IMAGE_TOKENS = 85
UNKNOWN_IMAGE_TOKENS = 1600


def text_tokens(text: str) -> int:
    return math.ceil(len(text.split()) * TOKENS_PER_WORD)


def image_tokens(width: int | None, height: int | None) -> int:
    if not width or not height or width <= 0 or height <= 0:
        return UNKNOWN_IMAGE_TOKENS
    return max(
        MIN_IMAGE_TOKENS, min(MAX_IMAGE_TOKENS, math.ceil(width * height / PIXELS_PER_TOKEN))
    )


@dataclass(frozen=True)
class MediaLimits:
    """What a route can take, as its capabilities declare it (``ProviderCapabilities``)."""

    sees_images: bool = False
    max_images: int = 0
    max_image_bytes: int = 0
    image_types: frozenset[str] = frozenset()
    provider: str = ""
    model: str = ""

    @classmethod
    def of(cls, capabilities: Any) -> MediaLimits:
        return cls(
            sees_images=bool(getattr(capabilities, "sees_images", False)),
            max_images=int(getattr(capabilities, "max_images", 0) or 0),
            max_image_bytes=int(getattr(capabilities, "max_image_bytes", 0) or 0),
            image_types=frozenset(getattr(capabilities, "image_types", frozenset()) or ()),
            provider=str(getattr(capabilities, "provider", "") or ""),
            model=str(getattr(capabilities, "model", "") or ""),
        )


TEXT_ONLY = MediaLimits()


class CannotDeliver(Exception):
    """The work needs a picture itself and the route cannot take it. Nothing was sent."""

    def __init__(self, detail: str, *, key: str) -> None:
        super().__init__(detail)
        self.key = key


@dataclass(frozen=True)
class Delivered:
    """One part as it reached (or did not reach) the model."""

    key: str
    kind: str
    representation: Representation
    reason: str
    asset_id: str = ""
    version_id: str = ""
    rendition: str = ""
    checksum: str = ""
    byte_length: int = 0
    tokens: int = 0
    degraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "representation": self.representation.value,
            "reason": self.reason,
            "asset_id": self.asset_id,
            "version_id": self.version_id,
            "rendition": self.rendition,
            "checksum": self.checksum,
            "byte_length": self.byte_length,
            "tokens": self.tokens,
            "degraded": self.degraded,
        }


@dataclass
class Delivery:
    """Exactly what went to the model for one request: which picture and version, as
    which rendition, or which words instead and why. Kept with the work it served."""

    provider: str = ""
    model: str = ""
    parts: list[Delivered] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return any(part.degraded for part in self.parts)

    @property
    def tokens(self) -> int:
        return sum(part.tokens for part in self.parts)

    def sent(self) -> list[Delivered]:
        return [part for part in self.parts if part.representation is Representation.PICTURE]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "degraded": self.degraded,
            "parts": [part.as_dict() for part in self.parts],
        }


def _fits(part: MediaPart, limits: MediaLimits, already: int) -> str | None:
    """Why a picture cannot go to this route, or None when it can."""
    if not isinstance(part, ImagePart):
        return "The assistant cannot be sent sound or film."
    if not limits.sees_images:
        return "The assistant cannot see pictures."
    if already >= limits.max_images:
        return f"The assistant takes at most {limits.max_images} pictures at once."
    if limits.image_types and part.content_type not in limits.image_types:
        return "The assistant cannot take this kind of picture."
    if limits.max_image_bytes and part.byte_length > limits.max_image_bytes:
        return "The picture is larger than the assistant takes."
    return None


def negotiate(
    parts: Iterable[ContextPart],
    limits: MediaLimits,
    *,
    requires_original: bool = False,
) -> Delivery:
    """Decide, part by part and in order, what reaches the model.

    ``requires_original`` is for work that is about the picture itself: any picture that
    cannot be sent raises ``CannotDeliver`` before anything is loaded or spent."""
    delivery = Delivery(provider=limits.provider, model=limits.model)
    pictures = 0
    for part in parts:
        if isinstance(part, TextPart):
            delivery.parts.append(
                Delivered(
                    key=part.key,
                    kind=part.kind,
                    representation=Representation.DESCRIPTION,
                    reason=part.reason,
                    tokens=part.tokens,
                )
            )
            continue
        base: dict[str, Any] = {
            "key": part.key,
            "kind": part.kind,
            "asset_id": part.asset_id,
            "version_id": part.version_id,
        }
        if part.choice is ContextChoice.LEAVE_OUT and not requires_original:
            delivery.parts.append(
                Delivered(
                    **base,
                    representation=Representation.LEFT_OUT,
                    reason=REPRESENTATION_WORDS[Representation.LEFT_OUT],
                )
            )
            continue
        wanted = requires_original or part.choice is ContextChoice.SEND
        refusal = _fits(part, limits, pictures) if wanted else "described"
        if wanted and refusal is None:
            pictures += 1
            delivery.parts.append(
                Delivered(
                    **base,
                    representation=Representation.PICTURE,
                    reason=REPRESENTATION_WORDS[Representation.PICTURE],
                    rendition=part.rendition,
                    checksum=part.checksum,
                    byte_length=part.byte_length,
                    tokens=part.tokens,
                )
            )
            continue
        if requires_original:
            raise CannotDeliver(
                f"{refusal} This needs the picture itself, so nothing was sent.", key=part.key
            )
        if part.fallback_text.strip():
            representation = Representation.FALLBACK if wanted else Representation.DESCRIPTION
            reason = REPRESENTATION_WORDS[representation]
            if wanted and refusal:
                reason = f"{refusal} Its accepted description is given in words instead."
            delivery.parts.append(
                Delivered(
                    **base,
                    representation=representation,
                    reason=reason,
                    tokens=text_tokens(part.fallback_text),
                    degraded=wanted,
                )
            )
        else:
            reason = REPRESENTATION_WORDS[Representation.NAMED]
            if wanted and refusal:
                reason = f"{refusal} It has no accepted description, so only its name is given."
            delivery.parts.append(
                Delivered(
                    **base,
                    representation=Representation.NAMED,
                    reason=reason,
                    degraded=wanted,
                )
            )
    return delivery


def planned_representation(choice: ContextChoice, has_description: bool) -> Representation:
    """What a picture's choice means before any route is known (the context preview)."""
    if choice is ContextChoice.LEAVE_OUT:
        return Representation.LEFT_OUT
    if choice is ContextChoice.SEND:
        return Representation.PICTURE
    return Representation.DESCRIPTION if has_description else Representation.NAMED


def media_tokens(parts: Sequence[ContextPart], limits: MediaLimits | None = None) -> int:
    """What the parts would cost: as negotiated for ``limits``, or at most (every picture
    the author chose to send counted as sent) when the route is not known yet."""
    if limits is not None:
        return negotiate(parts, limits).tokens
    total = 0
    for part in parts:
        if isinstance(part, TextPart):
            total += part.tokens
        elif part.choice is ContextChoice.SEND:
            total += max(part.tokens, text_tokens(part.fallback_text))
        elif part.choice is ContextChoice.DESCRIBE:
            total += text_tokens(part.fallback_text)
    return total


__all__ = [
    "CHOICE_WORDS",
    "DEFAULT_CHOICE",
    "REPRESENTATION_WORDS",
    "TEXT_ONLY",
    "AudioPart",
    "CannotDeliver",
    "ContextChoice",
    "ContextPart",
    "Delivered",
    "Delivery",
    "ImagePart",
    "MediaLimits",
    "MediaPart",
    "Representation",
    "TextPart",
    "VideoPart",
    "image_tokens",
    "media_tokens",
    "negotiate",
    "planned_representation",
    "text_tokens",
]
