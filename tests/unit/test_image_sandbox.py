"""The picture worker's sandbox beyond its limits (AST-006).

The worker runs for real. Where this machine gives it the whole sandbox (Linux with
Landlock and seccomp on x86-64 or arm64) the tests check it can open no file, make no
file, reach no network and start no process, and that pictures still decode; elsewhere
those tests are skipped and the rest check that it degrades and says so."""

from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.adapters import image_worker
from backend.adapters.image_processor import (
    ImageRejected,
    ProcessorLimits,
    SubprocessImageProcessor,
)
from backend.api.health import _picture_capability

pytestmark = pytest.mark.unit

_FULL = SubprocessImageProcessor(ProcessorLimits(sandbox="best")).sandbox_status()
needs_sandbox = pytest.mark.skipif(
    _FULL.get("level") != "full", reason="this machine gives no Landlock and seccomp sandbox"
)


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_the_filter_is_well_formed(machine: str) -> None:
    program = image_worker.seccomp_program(machine)
    assert program is not None
    assert len(program) < 4096  # the kernel's limit on one filter
    for code, jt, jf, _k in program:
        assert 0 <= jt <= 255 and 0 <= jf <= 255
        assert code in (0x20, 0x15, 0x35, 0x45, 0x06)
    # It starts by checking the architecture and stops any other ABI outright.
    assert program[0] == (0x20, 0, 0, 4)
    assert program[2] == (0x06, 0, 0, 0x80000000)
    # Every path out ends in a return.
    assert program[-1][0] == 0x06


def test_an_unknown_architecture_gets_no_filter_rather_than_a_wrong_one() -> None:
    assert image_worker.seccomp_program("riscv64") is None


def test_the_denied_lists_name_the_same_calls_on_both_architectures() -> None:
    # arm64 has no legacy open/mkdir/fork calls; everything else lines up one to one.
    assert len(image_worker._DENY_X86_64) - len(image_worker._DENY_AARCH64) == 14
    assert len(set(image_worker._DENY_X86_64)) == len(image_worker._DENY_X86_64)
    assert len(set(image_worker._DENY_AARCH64)) == len(image_worker._DENY_AARCH64)


@pytest.mark.parametrize(
    ("deny", "calls"),
    [
        # kill tkill tgkill rt_sigqueueinfo rt_tgsigqueueinfo pidfd_send_signal pidfd_open
        # pidfd_getfd (arch/x86/entry/syscalls/syscall_64.tbl)
        (image_worker._DENY_X86_64, (62, 200, 234, 129, 297, 424, 434, 438)),
        # the same on arm64 (include/uapi/asm-generic/unistd.h)
        (image_worker._DENY_AARCH64, (129, 130, 131, 138, 240, 424, 434, 438)),
    ],
    ids=["x86_64", "aarch64"],
)
def test_no_way_to_signal_or_reach_another_process_is_left_open(deny, calls) -> None:
    assert set(calls) <= set(deny)


def _probe(mode: str) -> dict[str, object]:
    processor = SubprocessImageProcessor(ProcessorLimits(sandbox=mode))
    return processor.sandbox_status(refresh=True)


def test_with_the_sandbox_off_the_probe_really_can_do_these_things() -> None:
    """The probe is honest: without a sandbox nothing is refused (or the test is moot)."""
    status = _probe("off")
    assert status["level"] == "limits_only"
    assert status["refused"] == {
        "read_files": False,
        "write_files": False,
        "network": False,
        "new_process": False,
    }


@needs_sandbox
def test_a_sandboxed_worker_can_open_no_file_or_socket_and_start_no_process() -> None:
    status = _probe("best")
    assert status["level"] == "full"
    layers = status["layers"]
    assert isinstance(layers, dict)
    assert layers["no_new_privs"] is True
    assert layers["landlock"]["active"] is True
    assert layers["seccomp"]["active"] is True
    assert status["refused"] == {
        "read_files": True,
        "write_files": True,
        "network": True,
        "new_process": True,
    }


def _jpeg_turned(orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "orange").save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def _encoded(image: Image.Image, fmt: str, **extra: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **extra)
    return out.getvalue()


@needs_sandbox
@pytest.mark.parametrize(
    ("data", "size"),
    [
        (_encoded(Image.new("RGB", (30, 10), "teal"), "PNG"), (30, 10)),
        (_jpeg_turned(6), (20, 40)),
        (_encoded(Image.new("RGBA", (12, 12), (1, 2, 3, 4)), "WEBP"), (12, 12)),
        (_encoded(Image.new("I;16", (8, 8), 40000), "PNG"), (8, 8)),
        (_encoded(Image.new("P", (8, 8), 1), "PNG", transparency=1), (8, 8)),
        (_encoded(Image.new("L", (9, 9), 7), "GIF"), None),
    ],
)
def test_pictures_still_decode_inside_the_sandbox(
    data: bytes, size: tuple[int, int] | None
) -> None:
    processor = SubprocessImageProcessor(ProcessorLimits(sandbox="require"))
    if size is None:
        with pytest.raises(ImageRejected) as refused:
            processor.process_sync(data)
        assert refused.value.code == "unsupported_type"
        return
    processed = processor.process_sync(data)
    assert (processed.width, processed.height) == size
    assert processor.last_sandbox_level == "full"


def test_a_required_sandbox_that_is_missing_refuses_the_picture_in_plain_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = SubprocessImageProcessor(ProcessorLimits(sandbox="require"))

    def missing(settings: str, data: bytes) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'{"ok": false, "code": "sandbox_unavailable", "sandbox": "partial"}',
            stderr=b"",
        )

    monkeypatch.setattr(processor, "_run", missing)
    with pytest.raises(ImageRejected) as refused:
        processor.process_sync(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    assert refused.value.code == "processor_unavailable"
    assert processor.last_sandbox_level == "partial"


def test_a_worker_that_cannot_start_reports_an_unknown_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = SubprocessImageProcessor()

    def broken(settings: str, data: bytes) -> subprocess.CompletedProcess[bytes]:
        raise OSError("no interpreter")

    monkeypatch.setattr(processor, "_run", broken)
    status = processor.sandbox_status(refresh=True)
    assert status == {"mode": "best", "level": "unknown"}


def test_the_sandbox_degrades_off_linux_without_breaking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    found = image_worker.apply_sandbox("best")
    assert found["level"] == "limits_only"
    assert found["layers"]["landlock"]["reason"] == "not_linux"


def _container(status: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(MEDIA_PROCESSING_ENABLED=True),
        image_processor=SimpleNamespace(sandbox_status=lambda: status),
    )


def test_health_says_in_plain_words_when_the_sandbox_is_partial() -> None:
    capability = _picture_capability(_container({"mode": "best", "level": "partial"}))  # type: ignore[arg-type]
    sandbox = capability["sandbox"]
    assert isinstance(sandbox, dict)
    assert sandbox["level"] == "partial"
    assert "only part of the sandbox" in str(sandbox["reason"])
    full = _picture_capability(_container({"mode": "best", "level": "full"}))  # type: ignore[arg-type]
    assert full["sandbox"]["reason"] is None  # type: ignore[index]
