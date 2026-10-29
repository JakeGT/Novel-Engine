"""Build a reproducible release artefact set (OSS-005): ``make release``.

From one commit (``HEAD`` unless ``--ref`` names another) it writes, under
``dist/release/novel-engine-<version>/``:

- ``novel-engine-<version>-source.tar.gz``: the source as committed, with its pinned lock
  files (``uv.lock``, ``frontend/pnpm-lock.yaml``);
- ``novel_engine-<version>-py3-none-any.whl`` and ``novel_engine-<version>.tar.gz``: the
  backend and the connector, built by ``uv build`` from that source;
- ``novel-engine-<version>-frontend.tar.gz``: the built frontend (``frontend/dist``), from
  a frozen install of the pinned lock file;
- ``novel-engine-<version>-python.cdx.json`` and ``…-frontend.cdx.json``: software bills
  of materials in CycloneDX, from the lock files (``uv export --format cyclonedx1.5``,
  ``pnpm sbom --sbom-format cyclonedx``);
- ``BUILDINFO.json``: the commit, the time every file is stamped with, the tools used;
- ``SHA256SUMS``: the SHA-256 of every file above, in the ``sha256sum -c`` format.

Every time written is fixed (``SOURCE_DATE_EPOCH``, reproducible-builds.org): the source
and frontend tarballs, the SBOMs and ``BUILDINFO.json`` carry the commit's time; ``uv
build`` stamps the wheel's members 1980-01-01 (the earliest a zip can hold) and the
sdist's 1970-01-01 (time 0). Archive members are sorted with their owners and modes made
plain, gzip writes no time or name, the SBOMs' serial numbers come from their content,
and no ``VITE_*`` or ``NODE_ENV`` setting from the shell reaches the frontend build, so
building the same commit with the same tools gives the same bytes. ``--check`` builds
twice and compares the checksums.

Nothing is tagged, signed, uploaded or published: that is the owner's.

Files marked ``export-ignore`` in ``.gitattributes`` (agent-tool folders) are left out.

``--worktree`` builds from the files in the working tree instead (tracked ones and new
ones not ignored, never local agent-tool files or ``export-ignore`` ones), for trying a
change before it is committed. Such a build says so in ``BUILDINFO.json``, is written to
``novel-engine-<version>-worktree/`` so it never stands where a release would, and is
not a release.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "novel-engine"
# Files no build carries, committed or not (they belong to coding tools). ``.agent/`` is
# also ``export-ignore`` in ``.gitattributes``; it is listed here for commits made before.
NEVER_SHIPPED = (
    ".agent/",
    ".claude/",
    ".codex/",
    ".gemini/",
    ".mcp.json",
    "GEMINI.md",
    "opencode.jsonc",
)


def shipped(name: str) -> bool:
    return not (name.startswith(NEVER_SHIPPED) or name in NEVER_SHIPPED)


LOCK_FILES = ("uv.lock", "frontend/pnpm-lock.yaml")


class ReleaseError(RuntimeError):
    """Said plainly and the build stops; nothing half-made is left as if it were whole."""


def run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    try:
        done = subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise ReleaseError(f"{args[0]} is not installed or not on PATH") from error
    except subprocess.CalledProcessError as error:
        tail = (error.stderr or error.stdout or "").strip().splitlines()[-15:]
        raise ReleaseError(f"{' '.join(args[:3])} failed:\n" + "\n".join(tail)) from error
    return done.stdout


def git(*args: str) -> str:
    return run(["git", *args], cwd=ROOT).strip()


def version_of(source: Path) -> str:
    with (source / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


# --------------------------------------------------------------------------- source


def resolve(ref: str) -> str:
    """The commit ``ref`` names, as a full SHA. A ref that starts with ``-`` is refused, so
    it can never be read by git as an option."""
    if not ref or ref.startswith("-"):
        raise ReleaseError(f"{ref!r} is not a commit or tag name")
    try:
        commit = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except ReleaseError:
        commit = ""
    if not commit:
        raise ReleaseError(f"there is no commit called {ref!r}")
    return commit


def export_commit(commit: str, into: Path) -> None:
    """The committed tree, exactly: ``git archive`` of the resolved commit, read back
    member by member (``export-ignore`` paths left out by git itself)."""
    data = subprocess.run(
        ["git", "archive", "--format=tar", commit], cwd=ROOT, check=True, capture_output=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        # A folder's own member (".agent") is matched as the folder (".agent/").
        members = [m for m in archive.getmembers() if shipped(m.name) and shipped(m.name + "/")]
        archive.extractall(into, members=members, filter="tar")


def export_ignored(names: list[str]) -> set[str]:
    """Which of ``names`` ``.gitattributes`` marks ``export-ignore`` (as ``git archive``
    would leave them out)."""
    if not names:
        return set()
    done = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"],
        cwd=ROOT,
        input="\0".join(names) + "\0",
        check=True,
        capture_output=True,
        text=True,
    )
    fields = done.stdout.split("\0")
    # -z output: path, attribute, value, for each path.
    return {
        fields[i]
        for i in range(0, len(fields) - 2, 3)
        if fields[i + 2] not in ("unspecified", "unset")
    }


def export_worktree(into: Path) -> None:
    listed = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT)
    names = sorted(set(filter(None, listed.split("\0"))))
    ignored = export_ignored(names)
    for name in names:
        if not shipped(name) or name in ignored:
            continue
        source = ROOT / name
        if not source.exists() and not source.is_symlink():
            continue  # deleted in the working tree, not yet committed
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            os.symlink(os.readlink(source), target)
        else:
            shutil.copy2(source, target)


def _plain(info: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    info.mtime = epoch
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def deterministic_tar(directory: Path, prefix: str, out: Path, epoch: int) -> None:
    """A gzipped tar of ``directory`` under ``prefix/``: sorted members, the given time,
    no owners, plain modes; gzip with no name or time in its header."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        paths = sorted(
            directory.rglob("*"), key=lambda path: path.relative_to(directory).as_posix()
        )
        root = tarfile.TarInfo(prefix)
        root.type = tarfile.DIRTYPE
        archive.addfile(_plain(root, epoch))
        for path in paths:
            name = f"{prefix}/{path.relative_to(directory).as_posix()}"
            info = _plain(archive.gettarinfo(str(path), arcname=name), epoch)
            info.pax_headers = {}
            if info.isfile():
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    with (
        out.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed,
    ):
        packed.write(buffer.getvalue())


# --------------------------------------------------------------------------- SBOMs


def fixed_sbom(raw: str, epoch: int) -> str:
    """A CycloneDX document with its time set to the commit's and its serial number made
    from its own content (a UUID 5), so the same lock file gives the same bytes."""
    document = json.loads(raw)
    document.setdefault("metadata", {})["timestamp"] = datetime.fromtimestamp(epoch, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    document.pop("serialNumber", None)
    digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    document["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'sha256:' + digest)}"
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def python_sbom(source: Path, env: dict[str, str]) -> str:
    return run(
        ["uv", "export", "--format", "cyclonedx1.5", "--frozen", "--no-dev", "--quiet"],
        cwd=source,
        env=env,
    )


def frontend_sbom(source: Path, env: dict[str, str]) -> str:
    return run(
        ["pnpm", "sbom", "--sbom-format", "cyclonedx", "--lockfile-only", "--prod"],
        cwd=source / "frontend",
        env=env,
    )


# --------------------------------------------------------------------------- build


def tool_versions(env: dict[str, str], frontend: Path) -> dict[str, str]:
    """The tools that made the files: a rebuild with the same versions gives the same bytes.
    pnpm is asked from the frontend's own folder, where its pinned version applies."""
    found: dict[str, str] = {"python": sys.version.split()[0]}
    for tool, args, where in (
        ("uv", ["uv", "--version"], ROOT),
        ("node", ["node", "--version"], frontend),
        ("pnpm", ["pnpm", "--version"], frontend),
    ):
        try:
            words = run(args, cwd=where, env=env).split()
            found[tool] = next((w for w in words if w.lstrip("v")[:1].isdigit()), words[-1])
        except ReleaseError:
            found[tool] = "missing"
    return found


def frontend_env(env: dict[str, str]) -> dict[str, str]:
    """The environment the frontend is built in: without the shell's ``VITE_*`` settings
    (Vite writes them into the bundle) and ``NODE_ENV`` (it changes what is built)."""
    return {
        key: value
        for key, value in env.items()
        if not key.startswith("VITE_") and key != "NODE_ENV"
    }


def output_name(stem: str, *, worktree: bool) -> str:
    """The folder a build is written to: a working-tree build never stands where a
    release of the same version would."""
    return f"{stem}-worktree" if worktree else stem


def build(*, ref: str, worktree: bool, out_root: Path, skip_frontend: bool = False) -> Path:
    if worktree:
        commit = git("rev-parse", "HEAD")
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or git("log", "-1", "--format=%ct"))
    else:
        commit = resolve(ref)
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or git("log", "-1", "--format=%ct", commit))
        changed = git("status", "--porcelain", "--untracked-files=no")
        if changed and ref == "HEAD":
            print(
                "Note: tracked files have changes that are not committed; the release is "
                f"built from commit {commit[:12]} without them.",
                file=sys.stderr,
            )
    env = {
        **os.environ,
        "SOURCE_DATE_EPOCH": str(epoch),
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
        # Never ask for input; use the repository's caches when the Makefile set them.
        "CI": "true",
        "PYTHONHASHSEED": "0",
    }
    with tempfile.TemporaryDirectory(prefix="novel-release-") as scratch:
        work = Path(scratch)
        source = work / "source"
        source.mkdir()
        (export_worktree if worktree else lambda into: export_commit(commit, into))(source)
        missing = [name for name in LOCK_FILES if not (source / name).is_file()]
        if missing:
            raise ReleaseError("the source has no pinned lock file: " + ", ".join(missing))
        version = version_of(source)
        stem = f"{NAME}-{version}"
        staging = work / "out"
        staging.mkdir()

        deterministic_tar(source, stem, staging / f"{stem}-source.tar.gz", epoch)

        built = work / "python"
        run(["uv", "build", "--sdist", "--wheel", "--out-dir", str(built)], cwd=source, env=env)
        for artefact in sorted(built.iterdir()):
            if artefact.suffix in {".whl", ".gz"}:
                shutil.copy2(artefact, staging / artefact.name)

        (staging / f"{stem}-python.cdx.json").write_text(
            fixed_sbom(python_sbom(source, env), epoch)
        )

        if not skip_frontend:
            frontend = source / "frontend"
            install = ["pnpm", "install", "--frozen-lockfile", "--prefer-offline"]
            store = env.get("PNPM_STORE_DIR") or str(ROOT / ".cache" / "pnpm-store")
            run([*install, "--store-dir", store], cwd=frontend, env=env)
            run(["pnpm", "run", "build"], cwd=frontend, env=frontend_env(env))
            deterministic_tar(
                frontend / "dist", f"{stem}-frontend", staging / f"{stem}-frontend.tar.gz", epoch
            )
            (staging / f"{stem}-frontend.cdx.json").write_text(
                fixed_sbom(frontend_sbom(source, env), epoch)
            )

        info = {
            "name": NAME,
            "version": version,
            "commit": commit,
            "built_from": "working tree (not a release)" if worktree else "commit",
            "source_date_epoch": epoch,
            "source_date": datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": tool_versions(env, source / "frontend"),
            "frontend": not skip_frontend,
        }
        (staging / "BUILDINFO.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
        sums = [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in sorted(staging.iterdir())
        ]
        (staging / "SHA256SUMS").write_text("\n".join(sums) + "\n")

        target = out_root / output_name(stem, worktree=worktree)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ref", default="HEAD", help="the commit to build (default HEAD)")
    parser.add_argument(
        "--worktree", action="store_true", help="build the working tree (not a release)"
    )
    parser.add_argument("--out", default=str(ROOT / "dist" / "release"), help="where to write")
    parser.add_argument("--check", action="store_true", help="build twice and compare checksums")
    parser.add_argument("--skip-frontend", action="store_true", help="leave out the frontend build")
    args = parser.parse_args(argv)
    try:
        out = Path(args.out)
        first = build(
            ref=args.ref, worktree=args.worktree, out_root=out, skip_frontend=args.skip_frontend
        )
        print(f"Release artefacts in {first}")
        print((first / "SHA256SUMS").read_text(), end="")
        if args.check:
            with tempfile.TemporaryDirectory(prefix="novel-release-check-") as again:
                second = build(
                    ref=args.ref,
                    worktree=args.worktree,
                    out_root=Path(again),
                    skip_frontend=args.skip_frontend,
                )
                one = (first / "SHA256SUMS").read_text()
                two = (second / "SHA256SUMS").read_text()
                if one != two:
                    differ = sorted(set(one.splitlines()) ^ set(two.splitlines()))
                    raise ReleaseError(
                        "two builds of the same source differ:\n" + "\n".join(differ)
                    )
            print("Built twice: every file is the same, byte for byte.")
    except ReleaseError as error:
        print(f"Release not built: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
