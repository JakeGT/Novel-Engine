"""The access key format (QRY-003): scannable prefix, 256 random bits, offline checksum."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from backend.core.security import hash_token
from backend.domain.access_keys import (
    KEY_LENGTH,
    KEY_PREFIX,
    MAX_LIFETIME_DAYS,
    generate_key,
    key_hint,
    lifetime,
    well_formed,
)
from scripts.ci import secret_scan

pytestmark = pytest.mark.unit


def test_a_new_key_has_the_prefix_random_part_and_checksum() -> None:
    key = generate_key()
    assert key.startswith(KEY_PREFIX) and len(key) == KEY_LENGTH
    assert re.fullmatch(r"nvlpat_[0-9A-Za-z]{49}", key)
    assert well_formed(key)
    assert key_hint(key) == key[-4:]


def test_keys_do_not_repeat_and_their_digests_differ() -> None:
    keys = {generate_key() for _ in range(200)}
    assert len(keys) == 200
    assert len({hash_token(key) for key in keys}) == 200


def test_a_mistyped_or_made_up_key_fails_the_checksum() -> None:
    key = generate_key()
    flipped = key[:10] + ("A" if key[10] != "A" else "B") + key[11:]
    assert not well_formed(flipped)
    assert not well_formed(key[:-1])
    assert not well_formed("ghp_" + key[len(KEY_PREFIX) :])
    assert not well_formed(key[:-6] + "!!!!!!")
    assert not well_formed("")


def test_the_secret_scanner_recognises_a_key() -> None:
    key = generate_key()
    findings = secret_scan.scan_text(f"token = {key}")
    assert [name for name, _line, _masked in findings] == ["novel_engine_access_key"]


def test_a_key_lasts_ninety_days_unless_said_and_never_over_a_year() -> None:
    assert lifetime(None) == timedelta(days=90)
    assert lifetime(MAX_LIFETIME_DAYS) == timedelta(days=365)
    for days in (0, -1, MAX_LIFETIME_DAYS + 1):
        with pytest.raises(ValueError):
            lifetime(days)
