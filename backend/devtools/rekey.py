"""Re-lock stored provider keys under a new credential key (OPS-004).

Each stored provider key has its own data key, locked with the server's credential key
(``CREDENTIAL_KEK``, version ``CREDENTIAL_KEK_VERSION``). During a rotation the server is
given the new key as the current one and the old one in ``CREDENTIAL_PREVIOUS_KEKS``, so
keys on either version keep working. This command moves every stored key to the current
version in place, so the old key can be retired without asking owners to connect again.

Commands (counts and credential ids only are printed; never a key, its last characters,
its fingerprint or a credential key):

* ``status``  how many stored keys each version locks, by status, and which versions this
              server holds.
* ``rekey``   re-lock every active key on an older version under the current one. Without
              ``--apply`` it opens each key with its old version and writes nothing (a dry
              run). ``--include-revoked`` moves revoked keys too.
* ``verify``  open every active key with the keys this server holds and compare it with the
              key it recorded; says which older versions no active key needs any more and
              can be removed. Exits 1 when any key cannot be opened.

See ``plan/runbooks/credential-key-rotation.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from backend.core.config import Settings
from backend.core.config import settings as default_settings

OPERATOR_ACTOR = "operator:rekey"


class SettingsProblem(RuntimeError):
    """The credential-key settings cannot be used for a rotation (said in plain words)."""


def check_settings(settings: Settings) -> None:
    """Refuse settings that would make a rotation lose or misread keys."""
    if not settings.CREDENTIAL_KEK:
        raise SettingsProblem(
            "No credential key is set (CREDENTIAL_KEK), so stored keys cannot be opened."
        )
    try:
        previous = settings.previous_credential_keks
    except ValueError as error:
        # The message names the entry's place in the list, never what it holds.
        raise SettingsProblem(str(error)) from None
    current = settings.CREDENTIAL_KEK_VERSION
    if current in previous and previous[current] != settings.CREDENTIAL_KEK:
        raise SettingsProblem(
            f"Version {current} is the current version and is also listed among the older "
            "keys with a different key. Give the new key a new version number."
        )
    from backend.core.crypto import CryptoError, check_kek

    for version, kek in {**previous, current: settings.CREDENTIAL_KEK}.items():
        try:
            check_kek(kek)
        except CryptoError as error:
            raise SettingsProblem(
                f"The credential key for version {version} is not a valid key (32 bytes, "
                "URL-safe base64)."
            ) from error


def _counts(report: Any) -> list[dict[str, Any]]:
    return [asdict(item) for item in report]


async def run(
    command: str,
    settings: Settings,
    *,
    apply: bool = False,
    include_revoked: bool = False,
    container: Any = None,
) -> tuple[int, dict[str, Any]]:
    """Run one command; returns the exit code and what to print."""
    try:
        check_settings(settings)
    except SettingsProblem as problem:
        return 2, {"ok": False, "problem": str(problem)}

    owned = container is None
    engine: Any = None
    if owned:
        # Only the two stores it needs, so the command works even when another part of the
        # server cannot start.
        from types import SimpleNamespace

        from backend.adapters.credentials import PostgresCredentialVault
        from backend.adapters.tenancy_store import PostgresTenancyStore
        from backend.core.database import create_engine, create_session_factory

        engine = create_engine(settings)
        factory = create_session_factory(engine)
        container = SimpleNamespace(
            credentials=PostgresCredentialVault(
                factory,
                kek=settings.CREDENTIAL_KEK,
                key_version=settings.CREDENTIAL_KEK_VERSION,
                previous_keks=settings.previous_credential_keks,
            ),
            tenancy=PostgresTenancyStore(factory),
        )
    try:
        vault = container.credentials
        if command == "status":
            return 0, {
                "current_version": vault.current_version,
                "configured_versions": vault.configured_versions,
                "keys": _counts(await vault.key_versions()),
            }
        if command == "rekey":
            # Each move's audit event is written with the move itself, in one transaction.
            report = await vault.rekey(
                dry_run=not apply, include_revoked=include_revoked, audit_actor=OPERATOR_ACTOR
            )
            return (0 if report.ok else 1), {
                "ok": report.ok,
                "dry_run": report.dry_run,
                "current_version": report.current_version,
                "examined": report.examined,
                "already_current": report.already_current,
                ("would_move" if report.dry_run else "moved"): len(report.rekeyed),
                "skipped_revoked": report.skipped_revoked,
                "missing_version": {str(k): v for k, v in report.missing_version.items()},
                "unopenable": report.unopenable,
            }
        if command == "verify":
            check = await vault.verify(include_revoked=include_revoked)
            return (0 if check.ok else 1), {
                "ok": check.ok,
                "current_version": check.current_version,
                "configured_versions": check.configured_versions,
                "checked": check.checked,
                "opened": check.opened,
                "missing_version": {str(k): v for k, v in check.missing_version.items()},
                "unopenable": check.unopenable,
                "active_on_older_versions": check.old_active,
                "versions_safe_to_remove": check.retirable_versions,
                "keys": _counts(check.by_version),
            }
        raise ValueError(f"unknown command {command!r}")
    finally:
        if engine is not None:
            await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.rekey",
        description="Re-lock stored provider keys under the current credential key.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="stored keys by credential-key version")
    rekey = sub.add_parser("rekey", help="move stored keys to the current version")
    rekey.add_argument("--apply", action="store_true", help="write (default: a dry run)")
    rekey.add_argument("--include-revoked", action="store_true")
    verify = sub.add_parser("verify", help="open every stored key with the keys held here")
    verify.add_argument("--include-revoked", action="store_true")
    args = parser.parse_args(argv)

    code, result = asyncio.run(
        run(
            args.command,
            default_settings,
            apply=getattr(args, "apply", False),
            include_revoked=getattr(args, "include_revoked", False),
        )
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
