import time
import uuid

import pytest

from backend.adapters.system import TimeOrderedIdGenerator, uuid7

pytestmark = pytest.mark.unit


def test_uuid7_has_rfc_version_and_variant() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_time_ordered() -> None:
    first = uuid7()
    time.sleep(0.005)
    second = uuid7()

    assert second > first


def test_id_generator_returns_parseable_uuid_and_optional_prefix() -> None:
    generator = TimeOrderedIdGenerator()

    plain = generator.new_id()
    assert uuid.UUID(plain).version == 7

    prefixed = generator.new_id("char")
    assert prefixed.startswith("char_")
    assert uuid.UUID(prefixed.removeprefix("char_")).version == 7
