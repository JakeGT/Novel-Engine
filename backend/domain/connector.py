"""A model on the author's own computer, reached through a connector (SCN-002A, ADR-025).

The connector is a small program the person runs next to their model server (Ollama,
LM Studio, a llama.cpp server). It only makes outbound HTTPS requests to this site, so
nothing on the person's computer is opened to the internet:

1. **Registration** follows the OAuth 2.0 device authorization grant (RFC 8628). The
   connector asks for a code; the site answers with a long, secret *device code* (kept
   only as its digest) and a short *user code* such as ``WDJB-MJHT`` from the RFC's
   20-consonant alphabet. The connector shows the user code; the person, signed in,
   types it in the app, sees the computer's name and confirms it. The connector polls
   with its device code (``authorization_pending`` / ``slow_down`` / ``access_denied`` /
   ``expired_token``) and, once confirmed, receives a device credential exactly once.
2. **The credential** has the access-key format (ADR-024) with its own prefix, so secret
   scanners can match it. Only its SHA-256 digest is stored. The connector rotates it
   every day; the old one keeps working for a few minutes so a lost answer does not lock
   the computer out. A credential not rotated for 30 days stops working. An owner can
   disconnect a computer at any time; removing the member who connected it does too.
3. **Requests** wait in a queue for the computer. The connector holds a long poll open,
   takes the next request, sends it to its local model server, and returns the answer.
   Only two calls are relayed: listing models and a chat completion.
4. **Updates** are never downloaded. The connector asks which versions the site accepts
   and refuses to take requests when it is too old; the site refuses an old one too.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

# --- versions -------------------------------------------------------------------------

# The connector version this site was written against, and the oldest it still accepts.
LATEST_CONNECTOR_VERSION = "1.0.0"
MINIMUM_CONNECTOR_VERSION = "1.0.0"
# Raised when the request or answer shapes the connector relays change incompatibly.
PROTOCOL_VERSION = 1

_VERSION = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.match((value or "").strip())
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def version_accepted(value: str, minimum: str = MINIMUM_CONNECTOR_VERSION) -> bool:
    """A connector this site will work with: a plain ``x.y.z`` no older than the minimum."""
    found, floor = parse_version(value), parse_version(minimum)
    return found is not None and floor is not None and found >= floor


# --- registration codes (RFC 8628) ------------------------------------------------------

# RFC 8628 section 6.1: consonants only, so no word is spelled and nothing looks alike.
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE_LENGTH = 8  # 20**8, about 34.5 bits
CODE_LIFETIME = timedelta(minutes=10)
POLL_INTERVAL_SECONDS = 5
SLOW_DOWN_STEP_SECONDS = 5
# Wrong codes one person may type in ten minutes before being asked to wait (RFC 8628 5.1).
WRONG_CODES_PER_WINDOW = 5
WRONG_CODE_WINDOW_SECONDS = 600
# Codes one address may ask for in ten minutes.
CODES_PER_ADDRESS = 10


def generate_user_code() -> str:
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def generate_device_code() -> str:
    return secrets.token_urlsafe(32)


def normalize_user_code(value: str) -> str | None:
    """What the person typed, as stored: capitals, no dashes or spaces (RFC 8628 6.1)."""
    cleaned = re.sub(r"[\s\-]", "", (value or "")).upper()
    if len(cleaned) != USER_CODE_LENGTH or any(c not in USER_CODE_ALPHABET for c in cleaned):
        return None
    return cleaned


def display_user_code(code: str) -> str:
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


class CodeStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    ISSUED = "issued"


@dataclass
class PendingCode:
    device_code_hash: str
    user_code: str
    device_name: str
    connector_version: str
    status: CodeStatus
    created_at: datetime
    expires_at: datetime
    interval_seconds: int = POLL_INTERVAL_SECONDS
    workspace_id: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    device_id: str | None = None
    last_polled_at: datetime | None = None

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at


# --- the device credential --------------------------------------------------------------

CREDENTIAL_PREFIX = "nvldev_"
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_RANDOM_LENGTH = 43  # 62**43 > 2**256
_CHECKSUM_LENGTH = 6
CREDENTIAL_LENGTH = len(CREDENTIAL_PREFIX) + _RANDOM_LENGTH + _CHECKSUM_LENGTH
# The connector is told to rotate once its credential is a day old ...
ROTATE_AFTER = timedelta(days=1)
# ... a credential not rotated for this long stops working ...
CREDENTIAL_MAX_AGE = timedelta(days=30)
# ... and the one it replaced keeps working this long, in case the answer was lost.
ROTATION_GRACE = timedelta(minutes=10)
MAX_DEVICES_PER_WORKSPACE = 10
MAX_DEVICE_NAME = 80

# Format characters (bidirectional overrides, zero-width marks) and unassigned or private
# ones are dropped, and control characters and line breaks become spaces, so a name cannot
# be made to read as something else on the confirm screen.
_DROPPED = frozenset({"Cf", "Cs", "Co", "Cn"})
_SPACED = frozenset({"Cc", "Zl", "Zp"})


def clean_device_name(value: str) -> str:
    """The name a connector gave, as shown: no hidden characters, single spaces, short."""
    kept: list[str] = []
    for character in value or "":
        category = unicodedata.category(character)
        if category in _SPACED:
            kept.append(" ")
        elif category not in _DROPPED:
            kept.append(character)
    return " ".join("".join(kept).split())[:MAX_DEVICE_NAME]


def _base62(number: int, width: int) -> str:
    digits: list[str] = []
    while number:
        number, digit = divmod(number, 62)
        digits.append(_ALPHABET[digit])
    return "".join(reversed(digits)).rjust(width, "0")


def _checksum(random_part: str) -> str:
    return _base62(zlib.crc32(random_part.encode("ascii")), _CHECKSUM_LENGTH)


def generate_credential() -> str:
    random_part = _base62(secrets.randbits(256), _RANDOM_LENGTH)
    return f"{CREDENTIAL_PREFIX}{random_part}{_checksum(random_part)}"


def credential_well_formed(value: str) -> bool:
    if len(value) != CREDENTIAL_LENGTH or not value.startswith(CREDENTIAL_PREFIX):
        return False
    body = value[len(CREDENTIAL_PREFIX) :]
    if any(character not in _ALPHABET for character in body):
        return False
    random_part, checksum = body[:_RANDOM_LENGTH], body[_RANDOM_LENGTH:]
    return secrets.compare_digest(_checksum(random_part), checksum)


class DeviceStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class RevokedBecause(StrEnum):
    OWNER = "owner"  # disconnected in the app
    ACCESS_LOST = "access_lost"  # the member who connected it left the workspace
    EXPIRED = "expired"  # not rotated for CREDENTIAL_MAX_AGE
    # The credential it replaced was used to rotate after the new one was in use: one of
    # the two copies is in someone else's hands (RFC 9700 section 4.14.2).
    REUSED = "reused"


# --- what the computer can do -----------------------------------------------------------


class ServerKind(StrEnum):
    OLLAMA = "ollama"
    LM_STUDIO = "lmstudio"
    LLAMA_CPP = "llamacpp"
    OTHER = "openai-compatible"


SERVER_LABELS = {
    ServerKind.OLLAMA: "Ollama",
    ServerKind.LM_STUDIO: "LM Studio",
    ServerKind.LLAMA_CPP: "llama.cpp",
    ServerKind.OTHER: "A model server",
}
MAX_MODELS = 200
MAX_MODEL_NAME = 200


@dataclass(frozen=True)
class LocalModel:
    name: str
    # The context the server said this model has, when it said.
    context_tokens: int | None = None


@dataclass(frozen=True)
class DeviceCapabilities:
    """What the connector found on the computer. Reported by the connector, so treated as
    a description to show and to plan with, never as permission for anything."""

    server: ServerKind = ServerKind.OTHER
    models: tuple[LocalModel, ...] = ()
    # Whether the server keeps to an answer shape it is given (a JSON schema). When not,
    # the shape is described in the prompt and the answer is checked here.
    structured_output: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "server": self.server.value,
            "models": [
                {"name": model.name, "context_tokens": model.context_tokens}
                for model in self.models
            ],
            "structured_output": self.structured_output,
        }

    @classmethod
    def from_json(cls, data: Any) -> DeviceCapabilities:
        """Read what a connector reported, keeping only well-formed parts."""
        if not isinstance(data, dict):
            return cls()
        try:
            server = ServerKind(str(data.get("server", ServerKind.OTHER.value)))
        except ValueError:
            server = ServerKind.OTHER
        models: list[LocalModel] = []
        seen: set[str] = set()
        raw_models = data.get("models")
        for raw in raw_models if isinstance(raw_models, list) else []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name or len(name) > MAX_MODEL_NAME or name in seen:
                continue
            context = raw.get("context_tokens")
            tokens = (
                context
                if isinstance(context, int) and not isinstance(context, bool) and context > 0
                else None
            )
            models.append(LocalModel(name=name, context_tokens=tokens))
            seen.add(name)
            if len(models) >= MAX_MODELS:
                break
        return cls(
            server=server,
            models=tuple(models),
            structured_output=data.get("structured_output") is True,
        )

    def context_for(self, model: str | None) -> int | None:
        return next((m.context_tokens for m in self.models if m.name == model), None)


@dataclass
class Device:
    id: str
    workspace_id: str
    name: str
    registered_by: str
    status: DeviceStatus
    credential_hash: str
    credential_issued_at: datetime
    created_at: datetime
    connector_version: str = ""
    capabilities: DeviceCapabilities = field(default_factory=DeviceCapabilities)
    last_seen_at: datetime | None = None
    in_use: bool = False
    previous_hash: str | None = None
    previous_valid_until: datetime | None = None
    revoked_reason: RevokedBecause | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None
    # When the current credential was last presented; None until it is first used, which
    # tells a lost rotation answer apart from a copied old credential.
    credential_last_used_at: datetime | None = None

    def online(self, now: datetime) -> bool:
        return (
            self.status is DeviceStatus.ACTIVE
            and self.last_seen_at is not None
            and now - self.last_seen_at <= OFFLINE_AFTER
        )


# --- the channel ------------------------------------------------------------------------

# How long the connector's poll is held open when nothing is waiting.
LONG_POLL_SECONDS = 25
# A computer that has not polled for this long is offline: requests fail at once.
OFFLINE_AFTER = timedelta(seconds=LONG_POLL_SECONDS + 50)
# How long a request may wait for the computer to take it.
PICKUP_SECONDS = 20
# How long a taken request may run before the site stops waiting (local models are slow).
ANSWER_SECONDS = 600
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_ANSWER_BYTES = 4 * 1024 * 1024
# A finished request whose answer was never read (its waiter died) keeps the words no longer.
FINISHED_WORDS_SECONDS = 300
# Polls one computer may hold open at once (a long poll and a heartbeat), and make a minute.
MAX_OPEN_POLLS = 2
POLLS_PER_MINUTE = 60
# While nothing waits, the queue is checked less and less often, up to this far apart.
MAX_POLL_STEP_SECONDS = 1.0

# The only calls relayed to the person's model server: (method, path below /v1).
RELAYED_CALLS: frozenset[tuple[str, str]] = frozenset(
    {("POST", "/chat/completions"), ("GET", "/models")}
)


class RequestStatus(StrEnum):
    WAITING = "waiting"
    TAKEN = "taken"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class ChannelRequest:
    id: str
    workspace_id: str
    device_id: str
    actor_user_id: str
    kind: str  # "POST /chat/completions" or "GET /models"
    status: RequestStatus
    created_at: datetime
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    result_status: int | None = None
    failure: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    taken_at: datetime | None = None
    finished_at: datetime | None = None
    # The story the request was for, when there was one, so deleting it clears the request.
    project_id: str | None = None


# --- the route -------------------------------------------------------------------------

# The provider id a workspace chooses for "a model on my own computer".
MY_COMPUTER = "my_computer"
MY_COMPUTER_LABEL = "My own computer"
DEVICE_SCHEME = "device://"


def device_address(workspace_id: str, device_id: str) -> str:
    """The base URL the gateway is given for a computer: never sent anywhere, only read."""
    return f"{DEVICE_SCHEME}{workspace_id}/{device_id}"


def parse_device_address(base_url: str | None) -> tuple[str, str] | None:
    if not base_url or not base_url.startswith(DEVICE_SCHEME):
        return None
    rest = base_url[len(DEVICE_SCHEME) :].strip("/")
    workspace_id, _, device_id = rest.partition("/")
    if not workspace_id or not device_id or "/" in device_id:
        return None
    return workspace_id, device_id


__all__ = [
    "ANSWER_SECONDS",
    "CODE_LIFETIME",
    "CREDENTIAL_PREFIX",
    "LATEST_CONNECTOR_VERSION",
    "MINIMUM_CONNECTOR_VERSION",
    "MY_COMPUTER",
    "ChannelRequest",
    "CodeStatus",
    "Device",
    "DeviceCapabilities",
    "DeviceStatus",
    "LocalModel",
    "PendingCode",
    "RequestStatus",
    "RevokedBecause",
    "ServerKind",
    "clean_device_name",
    "credential_well_formed",
    "device_address",
    "generate_credential",
    "generate_user_code",
    "normalize_user_code",
    "parse_device_address",
    "version_accepted",
]
