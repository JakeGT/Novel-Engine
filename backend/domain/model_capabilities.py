"""What each model can take (CTX-013): one model's limits, the operator's table, and the
shape of a provider's public model list. Plain data; reading the lists is in
``backend/services/model_capabilities.py``."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from typing import Any

OPENROUTER = "openrouter"
MAX_MODEL_NAME = 300


@dataclass(frozen=True)
class ModelCapability:
    """One model's limits; None where its source does not say."""

    provider: str
    model: str
    sees_images: bool | None = None
    max_images: int | None = None
    context_tokens: int | None = None
    max_output_tokens: int | None = None
    structured: bool | None = None
    # "provider" (its list), "computer" (the connector), or "operator" (the table).
    source: str = "operator"

    def over(self, base: ModelCapability | None) -> ModelCapability:
        """This entry's known values laid over ``base``'s."""
        if base is None:
            return self
        changes = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name not in {"provider", "model"} and getattr(self, item.name) is not None
        }
        return replace(base, **changes)

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


_FIELDS = {
    "images": ("sees_images", bool),
    "max_images": ("max_images", int),
    "context_tokens": ("context_tokens", int),
    "max_output_tokens": ("max_output_tokens", int),
    "structured": ("structured", bool),
}


def parse_operator_table(raw: str) -> dict[str, ModelCapability]:
    """``AI_MODEL_CAPABILITIES``: JSON keyed by ``provider/model`` (a trailing ``*``
    matches the rest of a name). Raises ``ValueError`` saying what is wrong."""
    if not raw.strip():
        return {}
    try:
        table = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("AI_MODEL_CAPABILITIES is not valid JSON") from error
    if not isinstance(table, dict):
        raise ValueError("AI_MODEL_CAPABILITIES must be a JSON object")
    found: dict[str, ModelCapability] = {}
    for key, entry in table.items():
        provider, _, model = str(key).partition("/")
        if not provider or not model or len(model) > MAX_MODEL_NAME or not isinstance(entry, dict):
            raise ValueError(f"AI_MODEL_CAPABILITIES entry {key!r} must be provider/model: {{...}}")
        values: dict[str, Any] = {}
        for name, value in entry.items():
            if name not in _FIELDS:
                raise ValueError(f"AI_MODEL_CAPABILITIES entry {key!r} has unknown {name!r}")
            field_name, kind = _FIELDS[name]
            if kind is bool and not isinstance(value, bool):
                raise ValueError(f"AI_MODEL_CAPABILITIES {key!r}: {name} must be true or false")
            if kind is int and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"AI_MODEL_CAPABILITIES {key!r}: {name} must be a whole number")
            values[field_name] = value
        found[f"{provider}/{model}"] = ModelCapability(
            provider=provider, model=model, source="operator", **values
        )
    return found


# Kept in INTEGER columns (migration 0059): a larger number is not a real limit.
MAX_LISTED_NUMBER = 2**31 - 1
# A number written with more digits than this is not read at all.
MAX_NUMBER_DIGITS = 12


def _positive(value: Any) -> int | None:
    """A positive whole number of at most ``MAX_LISTED_NUMBER``, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and len(value.strip()) > MAX_NUMBER_DIGITS:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 < number <= MAX_LISTED_NUMBER else None


def _openrouter_entry(item: Any) -> ModelCapability | None:
    if not isinstance(item, dict):
        return None
    name = item.get("id")
    if not isinstance(name, str) or not name or len(name) > MAX_MODEL_NAME:
        return None
    architecture = item.get("architecture")
    modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
    sees = (
        any(entry == "image" for entry in modalities if isinstance(entry, str))
        if isinstance(modalities, list)
        else None
    )
    top = item.get("top_provider")
    parameters = item.get("supported_parameters")
    structured = (
        any(
            isinstance(entry, str) and entry in {"structured_outputs", "response_format"}
            for entry in parameters
        )
        if isinstance(parameters, list)
        else None
    )
    return ModelCapability(
        provider=OPENROUTER,
        model=name,
        sees_images=sees,
        context_tokens=_positive(item.get("context_length")),
        max_output_tokens=_positive(top.get("max_completion_tokens"))
        if isinstance(top, dict)
        else None,
        structured=structured,
        source="provider",
    )


def openrouter_capabilities(payload: Any) -> list[ModelCapability]:
    """Each model in OpenRouter's list, with only what the list says. An entry of the
    wrong shape is skipped, never guessed at, and never stops the rest being read."""
    items = payload.get("data") if isinstance(payload, dict) else None
    found: list[ModelCapability] = []
    for item in items if isinstance(items, list) else []:
        try:
            entry = _openrouter_entry(item)
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
        if entry is not None:
            found.append(entry)
    return found


__all__ = [
    "MAX_LISTED_NUMBER",
    "MAX_MODEL_NAME",
    "OPENROUTER",
    "ModelCapability",
    "openrouter_capabilities",
    "parse_operator_table",
]
