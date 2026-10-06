import io
import json
import logging

import pytest

from backend.core.logging import (
    REDACTED,
    JsonLogFormatter,
    configure_logging,
    get_correlation_id,
    log_event,
    redact_fields,
    redact_text,
    reset_correlation_id,
    set_correlation_id,
)

pytestmark = pytest.mark.unit


def test_redact_text_removes_known_secret_shapes() -> None:
    assert "sk-abcdefghijklmnop" not in redact_text("key sk-abcdefghijklmnop used")
    assert "Bearer abc.def" not in redact_text("Authorization: Bearer abc.def")
    assert redact_text("postgresql://user:s3cr3t@host/db") == (
        f"postgresql://user:{REDACTED}@host/db"
    )
    assert redact_text("password=hunter2") == f"password={REDACTED}"


def test_redact_fields_masks_sensitive_keys_and_recurses() -> None:
    fields = {
        "OPENAI_API_KEY": "sk-abcdefghijklmnop",
        "safe": "value",
        "nested": {"postgres_password": "hunter2", "keep": "ok"},
        "items": ["token=abc123", {"secret": "x"}],
    }
    redacted = redact_fields(fields)
    assert redacted["OPENAI_API_KEY"] == REDACTED
    assert redacted["safe"] == "value"
    assert redacted["nested"]["postgres_password"] == REDACTED
    assert redacted["nested"]["keep"] == "ok"
    assert redacted["items"][0] == f"token={REDACTED}"
    assert redacted["items"][1]["secret"] == REDACTED


def test_json_formatter_emits_structured_redacted_record() -> None:
    token = set_correlation_id("cid-1")
    try:
        record = logging.LogRecord(
            name="backend.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="event happened",
            args=(),
            exc_info=None,
        )
        record.event = "app.startup"  # type: ignore[attr-defined]
        record.fields = {"OPENAI_API_KEY": "sk-abcdefghijklmnop"}  # type: ignore[attr-defined]
        payload = json.loads(JsonLogFormatter().format(record))
    finally:
        reset_correlation_id(token)

    assert payload["event"] == "app.startup"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "backend.test"
    assert payload["correlation_id"] == "cid-1"
    assert payload["timestamp"].endswith("+00:00")
    assert payload["fields"]["OPENAI_API_KEY"] == REDACTED


def test_configure_logging_and_log_event_write_json() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    try:
        log_event(logging.getLogger("backend.test"), "seed.applied", node_count=27, token="abc")
    finally:
        logging.getLogger().handlers.clear()

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "seed.applied"
    assert payload["fields"]["node_count"] == 27
    assert payload["fields"]["token"] == REDACTED


def test_correlation_id_context_defaults_empty() -> None:
    assert get_correlation_id() == ""
    token = set_correlation_id("abc")
    try:
        assert get_correlation_id() == "abc"
    finally:
        reset_correlation_id(token)
    assert get_correlation_id() == ""
