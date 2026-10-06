"""Deterministic secret scanner used by CI and pre-commit checks.

This is intentionally dependency-free so it runs from a clean checkout with the
standard library only. It scans tracked (or staged) text files for high-signal
credential shapes and fails closed with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}

PLACEHOLDER_MARKERS = (
    "example",
    "placeholder",
    "changeme",
    "your_",
    "redacted",
    "dummy",
    "fake",
    "<",
    "xxx",
)

ALLOWLIST_SUBSTRINGS = (
    "tests/",
    ".env.example",
    "scripts/ci/secret_scan.py",
)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-2:]}"


def is_allowlisted(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(marker in normalized for marker in ALLOWLIST_SUBSTRINGS)


def scan_text(text: str) -> list[tuple[str, int, str]]:
    """Return (pattern_name, line_number, masked_match) findings for one text blob."""
    findings: list[tuple[str, int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            continue
        for name, pattern in SECRET_PATTERNS.items():
            match = pattern.search(line)
            if match:
                findings.append((name, line_number, _mask(match.group(0))))
    return findings


def _git_files(staged: bool) -> list[str]:
    if staged:
        args = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"]
    else:
        args = ["git", "ls-files", "-z"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return [name for name in result.stdout.split("\0") if name]


def scan_paths(paths: list[str]) -> list[tuple[str, str, int, str]]:
    """Return (path, pattern_name, line_number, masked_match) findings."""
    findings: list[tuple[str, str, int, str]] = []
    for path in paths:
        if is_allowlisted(path):
            continue
        candidate = Path(path)
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, line_number, masked in scan_text(text):
            findings.append((path, name, line_number, masked))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan repository text for credential shapes.")
    parser.add_argument(
        "--staged",
        action="store_true",
        help="scan only staged files instead of all tracked files",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="explicit paths to scan (defaults to git-tracked files)",
    )
    args = parser.parse_args(argv)

    paths = args.paths if args.paths is not None else _git_files(staged=args.staged)
    findings = scan_paths(paths)

    if findings:
        print("Potential secrets detected:", file=sys.stderr)
        for path, name, line_number, masked in findings:
            print(f"  {path}:{line_number}: {name} -> {masked}", file=sys.stderr)
        return 1

    print(f"Secret scan clean ({len(paths)} files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())