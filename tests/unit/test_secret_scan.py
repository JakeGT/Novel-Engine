from pathlib import Path

import pytest

from scripts.ci import secret_scan

pytestmark = pytest.mark.unit


def test_scan_text_detects_credential_shapes() -> None:
    text = "\n".join(
        [
            "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx",
            "aws = AKIAIOSFODNN7REALKEY",
            "-----BEGIN RSA PRIVATE KEY-----",
        ]
    )
    findings = secret_scan.scan_text(text)
    names = {name for name, _, _ in findings}
    assert "openai_key" in names
    assert "aws_access_key" in names
    assert "private_key" in names


def test_scan_text_ignores_placeholder_lines() -> None:
    text = "\n".join(
        [
            "OPENAI_API_KEY=sk-<your-key-here>",
            "token = changeme-abcdefghijklmnopqrst",
        ]
    )
    assert secret_scan.scan_text(text) == []


def test_allowlist_covers_fixtures_and_examples() -> None:
    assert secret_scan.is_allowlisted("tests/unit/test_x.py")
    assert secret_scan.is_allowlisted(".env.example")
    assert not secret_scan.is_allowlisted("backend/main.py")


def test_scan_paths_reports_and_skips(tmp_path: Path) -> None:
    secret_file = tmp_path / "leak.txt"
    secret_file.write_text("key sk-abcdefghijklmnopqrstuvwx\n", encoding="utf-8")
    clean_file = tmp_path / "clean.txt"
    clean_file.write_text("nothing to see\n", encoding="utf-8")

    findings = secret_scan.scan_paths([str(secret_file), str(clean_file)])
    assert [name for _, name, _, _ in findings] == ["openai_key"]


def test_main_returns_nonzero_on_findings(tmp_path: Path, capsys) -> None:
    leak = tmp_path / "leak.env"
    leak.write_text("aws = AKIAIOSFODNN7REALKEY\n", encoding="utf-8")
    assert secret_scan.main(["--paths", str(leak)]) == 1
    assert "aws_access_key" in capsys.readouterr().err

    clean = tmp_path / "clean.env"
    clean.write_text("safe = value\n", encoding="utf-8")
    assert secret_scan.main(["--paths", str(clean)]) == 0
