"""What will be sent, and where, before it is sent (CTX-013).

Before a request that sends pictures, or more than the author asked to be asked about
first, the author sees the provider and model it goes to, how many words of the story
go with it, each picture that goes as itself (with its small copy), and in plain words
where it goes: a company's servers, the author's own computer, or the site's own
network. Only names and counts: never a key, never the story's words.

The pictures listed are exactly those the request would send as pictures: the same
negotiation (``negotiate``) the request itself makes, against the same route limits.

The author's answer is bound to what they were shown: the question carries a ``digest`` of
the route and the pictures, and the request is let through only when it comes back
(``X-Confirm-Sending``) with the same one. A job keeps what was agreed to and stops, as it
runs, if it would now send other pictures or send them elsewhere.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from backend.domain.connector import MY_COMPUTER, MY_COMPUTER_LABEL
from backend.domain.context_parts import MediaLimits, negotiate
from backend.domain.errors import DomainError
from backend.domain.identity import AccessScope
from backend.domain.providers import provider_spec, spec_for_base_url
from backend.domain.usage_controls import CONFIRM_SLACK, NeedsConfirmation

# Where a request goes, in the author's words.
COMPANY = "company"
YOUR_COMPUTER = "your_computer"
LOCAL_NETWORK = "local_network"

WHERE_WORDS = {
    COMPANY: "to {label}’s servers, outside this site",
    YOUR_COMPUTER: "to the computer you connected, not to any company",
    LOCAL_NETWORK: "to a model on this site’s own network, not to any company",
}
OPENAI_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class PictureSent:
    asset_id: str
    version_id: str
    name: str


@dataclass(frozen=True)
class SendPreview:
    provider: str
    provider_label: str
    model: str
    # The host it goes to ("openrouter.ai"); "" for the author's computer.
    destination: str
    where: str
    words: int
    estimated_tokens: int
    pictures: list[PictureSent] = field(default_factory=list)
    # How much the model reads at most, and whether this fits (when known).
    context_tokens: int | None = None

    @property
    def remote(self) -> bool:
        return self.where == COMPANY

    @property
    def asks_first(self) -> bool:
        """Pictures would go to a company's servers: the author is asked first."""
        return bool(self.pictures) and self.remote

    @property
    def route_digest(self) -> str:
        return _digest([self.provider, self.model, self.destination, self.where])

    @property
    def picture_versions(self) -> list[str]:
        return sorted({picture.version_id for picture in self.pictures})

    @property
    def digest(self) -> str:
        """What the author is asked about: the route and exactly the pictures that go."""
        return _digest([self.route_digest, self.picture_versions])

    def agreed(self) -> dict[str, Any]:
        """What a job keeps of what was agreed to (or needed no asking)."""
        return {"route": self.route_digest, "pictures": self.picture_versions}

    @property
    def fits(self) -> bool | None:
        if self.context_tokens is None:
            return None
        return self.estimated_tokens <= self.context_tokens

    def where_words(self) -> str:
        return WHERE_WORDS[self.where].format(label=self.provider_label)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_label": self.provider_label,
            "model": self.model,
            "destination": self.destination,
            "where": self.where,
            "where_words": self.where_words(),
            "words": self.words,
            "estimated_tokens": self.estimated_tokens,
            "pictures": [
                {"asset_id": p.asset_id, "version_id": p.version_id, "name": p.name}
                for p in self.pictures
            ],
            "context_tokens": self.context_tokens,
            "fits": self.fits,
            "digest": self.digest,
        }


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()[:32]


# The digest the author's answer came back with (``X-Confirm-Sending``), for the request
# being served. Set by the API for every request that can be confirmed.
_confirmed_sending: ContextVar[str | None] = ContextVar("confirmed_sending", default=None)


def set_confirmed_sending(digest: str | None) -> None:
    _confirmed_sending.set(digest)


def confirmed_sending() -> str | None:
    return _confirmed_sending.get()


UNKNOWN_PROVIDER = "unknown"


def unknown_preview(*, words: int, estimated_tokens: int, media: list[Any]) -> SendPreview:
    """When where a request goes cannot be said: treated as a company's servers, with every
    picture it may send, so the author is asked rather than the pictures simply sent."""
    seen: dict[str, PictureSent] = {}
    for part in media:
        if getattr(part, "version_id", None) and part.version_id not in seen:
            seen[part.version_id] = PictureSent(
                asset_id=part.asset_id,
                version_id=part.version_id,
                name=getattr(part, "label", "") or "",
            )
    return SendPreview(
        provider=UNKNOWN_PROVIDER,
        provider_label="the assistant’s provider",
        model="",
        destination="",
        where=COMPANY,
        words=words,
        estimated_tokens=estimated_tokens,
        pictures=list(seen.values()),
    )


def classify(base_url: str) -> tuple[str, str]:
    """(where, host) for a provider address: the site's own network when the host is a
    loopback or private address or a name with no dot; otherwise a company's servers."""
    host = (urlsplit(base_url).hostname or "").lower()
    if not host:
        return COMPANY, ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        local = "." not in host or host.endswith((".local", ".internal", ".lan", ".localhost"))
        return (LOCAL_NETWORK if local else COMPANY), host
    private = address.is_loopback or address.is_private or address.is_link_local
    return (LOCAL_NETWORK if private else COMPANY), host


@dataclass(frozen=True)
class Route:
    provider: str
    label: str
    model: str
    base_url: str


class SendPreviewService:
    def __init__(self, *, settings: Any, credentials: Any = None, ai: Any = None) -> None:
        self._settings = settings
        self._credentials = credentials
        self._ai = ai

    async def route(self, scope: AccessScope, workspace_id: str) -> Route | None:
        """Where the person's requests go now, without reading a key. None when there is
        no route (nothing would be sent)."""
        choice = None
        if self._credentials is not None:
            try:
                choice = await self._credentials.ai_choice(scope, workspace_id)
            except DomainError:
                choice = None
        if choice is not None and choice.provider == MY_COMPUTER:
            return Route(MY_COMPUTER, MY_COMPUTER_LABEL, choice.model or "", "")
        if getattr(self._settings, "is_hosted", False):
            if choice is None or not choice.provider:
                return None
            spec = provider_spec(choice.provider)
            if spec is None:
                return None
            return Route(
                spec.id, spec.label, choice.model or spec.default_model or "", spec.base_url
            )
        base_url = self._settings.AI_BASE_URL or OPENAI_URL
        spec = spec_for_base_url(base_url)
        # A server the provider list does not know (Ollama, LM Studio, llama.cpp, a proxy)
        # is named by where it is, not by the label the operator happened to give it.
        host = urlsplit(base_url).hostname or base_url
        label = spec.label if spec is not None else f"the model server at {host}"
        return Route(self._settings.AI_PROVIDER, label, self._settings.AI_MODEL, base_url)

    async def preview(
        self,
        scope: AccessScope,
        workspace_id: str,
        *,
        words: int,
        estimated_tokens: int,
        media: list[Any],
        limits: MediaLimits,
    ) -> SendPreview | None:
        route = await self.route(scope, workspace_id)
        if route is None:
            return None
        if route.provider == MY_COMPUTER:
            where, host = YOUR_COMPUTER, ""
        else:
            where, host = classify(route.base_url)
        delivery = negotiate(media, limits)
        names = {part.key: getattr(part, "label", "") for part in media}
        pictures = [
            PictureSent(asset_id=part.asset_id, version_id=part.version_id, name=names[part.key])
            for part in delivery.sent()
        ]
        context_tokens = None
        capabilities = getattr(self._ai, "capabilities", None)
        if capabilities is not None and route.provider != MY_COMPUTER:
            refresh = getattr(self._ai, "refresh_capabilities", None)
            base_url = route.base_url if getattr(self._settings, "is_hosted", False) else None
            # The route's own model: what the workspace chose, not the operator's default.
            model = route.model or None
            if refresh is not None:
                await refresh(base_url=base_url, model=model)
            context_tokens = capabilities(base_url=base_url, model=model).max_context_tokens
        return SendPreview(
            provider=route.provider,
            provider_label=route.label,
            model=route.model,
            destination=host,
            where=where,
            words=words,
            estimated_tokens=estimated_tokens,
            pictures=pictures,
            context_tokens=context_tokens,
        )


def ask_before_sending(
    preview: SendPreview | None,
    *,
    estimated_tokens: int,
    confirmed_tokens: int | None,
    threshold: int | None,
    doing: str,
    confirmed_digest: str | None = None,
) -> None:
    """Refuse, until confirmed, a request that sends pictures to a company's servers,
    even below the size the author asked to be asked about (CTX-013). The confirmation
    is ``X-Confirm-Tokens`` with this request's estimate and ``X-Confirm-Sending`` with
    the question's digest: an answer about other pictures, or another route, is not
    this one. ``confirmed_digest`` defaults to the request's own header."""
    if preview is None or not preview.asks_first:
        return
    digest = confirmed_digest if confirmed_digest is not None else confirmed_sending()
    if (
        confirmed_tokens is not None
        and estimated_tokens <= confirmed_tokens <= estimated_tokens * CONFIRM_SLACK
        and digest == preview.digest
    ):
        return
    count = len(preview.pictures)
    pictures = "a picture" if count == 1 else f"{count} pictures"
    asked = NeedsConfirmation(
        f"{doing} sends {pictures} from your story {preview.where_words()}, with about "
        f"{estimated_tokens:,} tokens of words.",
        estimated_tokens=max(1, estimated_tokens),
        threshold_tokens=threshold or 0,
    )
    asked.extensions = {"sending": preview.as_dict()}
    raise asked


def agreed_sending(preview: SendPreview | None) -> dict[str, Any] | None:
    """What a job keeps (``payload["confirmed_sending"]``) once it passed the question:
    the route and the pictures agreed to, or None when nothing was to go as a picture to
    a company (so any that would, at run time, stop it)."""
    if preview is None or not preview.asks_first:
        return None
    return preview.agreed()


def ask_again_before_sending(
    payload: dict[str, Any], preview: SendPreview | None, *, doing: str
) -> None:
    """At run time, before anything is sent: a job that would now send pictures to a
    company's servers that the author did not agree to (others, or by another route)
    stops, and the author is asked again by starting it again. Work asked for before
    this was kept is not stopped for it."""
    if "confirmed_sending" not in payload or preview is None or not preview.asks_first:
        return
    agreed = payload.get("confirmed_sending")
    if (
        isinstance(agreed, dict)
        and agreed.get("route") == preview.route_digest
        and set(preview.picture_versions) <= set(agreed.get("pictures") or [])
    ):
        return
    count = len(preview.pictures)
    pictures = "a picture" if count == 1 else f"{count} pictures"
    asked = NeedsConfirmation(
        f"{doing} stopped: it would now send {pictures} from your story "
        f"{preview.where_words()}, which you did not agree to. Nothing was sent; start it "
        "again to be asked.",
        estimated_tokens=max(1, preview.estimated_tokens),
        threshold_tokens=0,
    )
    asked.extensions = {"sending": preview.as_dict()}
    raise asked


def with_preview(asked: NeedsConfirmation, preview: SendPreview | None) -> NeedsConfirmation:
    """A size question, carrying what would be sent and where."""
    if preview is not None:
        asked.extensions = {"sending": preview.as_dict()}
    return asked


__all__ = [
    "COMPANY",
    "LOCAL_NETWORK",
    "UNKNOWN_PROVIDER",
    "YOUR_COMPUTER",
    "PictureSent",
    "SendPreview",
    "SendPreviewService",
    "agreed_sending",
    "ask_again_before_sending",
    "ask_before_sending",
    "classify",
    "confirmed_sending",
    "set_confirmed_sending",
    "unknown_preview",
    "with_preview",
]
