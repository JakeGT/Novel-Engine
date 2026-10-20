"""Picture processing in a separate, limited process (AST-003, AST-006).

The API never decodes an uploaded picture itself. ``SubprocessImageProcessor`` runs
``image_worker.py`` as a fresh ``python -I`` process for each picture, with an empty
environment (no secrets), the bytes on standard input, and limits the worker applies to
itself before importing Pillow (address space, CPU seconds, no file writes). The parent
caps the bytes before starting it, waits at most a wall-clock timeout and then kills it,
and runs only a few at once. What comes back is either the smaller copies or a failure
code from a fixed list; no decoder message is ever passed on or logged.

``PROCESSOR_VERSION`` names everything that shapes the output (the sizes, the format,
the quality and the decoding rules). Processing is keyed by the source checksum plus
this version, so running it again for the same picture changes nothing, and raising it
makes every picture eligible to be processed again.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.logging import log_event

logger = logging.getLogger(__name__)

PROCESSOR_VERSION = "pictures-2"
# The smaller copies made of every picture: kind -> longest side in pixels.
VARIANT_SIZES: dict[str, int] = {"thumbnail": 256, "web": 1024}
VARIANT_CONTENT_TYPE = "image/webp"
WEBP_QUALITY = 80
_WORKER = Path(__file__).with_name("image_worker.py")


class ImageRejected(Exception):
    """The picture could not be processed; ``code`` is from a fixed vocabulary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProcessorLimits:
    max_bytes: int = 8 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_dimension: int = 12_000
    memory_bytes: int = 1024 * 1024 * 1024
    cpu_seconds: int = 20
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class Rendition:
    kind: str
    width: int
    height: int
    data: bytes
    content_type: str = VARIANT_CONTENT_TYPE


@dataclass(frozen=True)
class ProcessedImage:
    width: int
    height: int
    detected_format: str
    renditions: tuple[Rendition, ...]


def pillow_capability() -> dict[str, Any]:
    """Whether Pillow is installed, its version, and whether it can read and write what
    processing needs. Never raises: a missing decoder degrades, it does not stop startup."""
    try:
        import PIL
        from PIL import features
    except Exception:  # noqa: BLE001 - absence is the answer
        return {"available": False, "version": None, "webp": False, "jpeg": False}
    try:
        webp = bool(features.check("webp"))
        jpeg = bool(features.check("jpg"))
        zlib = bool(features.check("zlib"))
    except Exception:  # noqa: BLE001 - a broken install reads as not capable
        webp = jpeg = zlib = False
    return {
        "available": webp and jpeg and zlib,
        "version": str(getattr(PIL, "__version__", "")),
        "webp": webp,
        "jpeg": jpeg,
        "png": zlib,
    }


class SubprocessImageProcessor:
    """Decode pictures in a fresh, limited process; at most ``concurrency`` at once."""

    version = PROCESSOR_VERSION

    def __init__(self, limits: ProcessorLimits | None = None, *, concurrency: int = 2) -> None:
        self.limits = limits or ProcessorLimits()
        self._slots = asyncio.Semaphore(max(1, concurrency))

    def capability(self) -> dict[str, Any]:
        found = pillow_capability()
        return {**found, "processor_version": PROCESSOR_VERSION, "isolated": True}

    async def process(self, data: bytes) -> ProcessedImage:
        async with self._slots:
            return await asyncio.to_thread(self.process_sync, data)

    def process_sync(self, data: bytes) -> ProcessedImage:
        limits = self.limits
        if len(data) > limits.max_bytes:
            raise ImageRejected("too_large")
        if not data:
            raise ImageRejected("damaged")
        settings = json.dumps(
            {
                "max_bytes": limits.max_bytes,
                "max_pixels": limits.max_pixels,
                "max_dimension": limits.max_dimension,
                "memory_bytes": limits.memory_bytes,
                "cpu_seconds": limits.cpu_seconds,
                "sizes": VARIANT_SIZES,
                "quality": WEBP_QUALITY,
            }
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
                [sys.executable, "-I", "-B", str(_WORKER), settings],
                input=data,
                capture_output=True,
                timeout=limits.timeout_seconds,
                env={"LANG": "C.UTF-8", "PATH": os.defpath},
                cwd="/",
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            # ``run`` kills the process before raising.
            self._log("took_too_long", started)
            raise ImageRejected("took_too_long") from error
        except OSError as error:
            self._log("processor_unavailable", started)
            raise ImageRejected("processor_unavailable") from error
        answer = self._answer(completed)
        if not answer.get("ok"):
            code = str(answer.get("code") or "processor_failed")
            self._log(code, started)
            raise ImageRejected(code)
        try:
            renditions = tuple(
                Rendition(
                    kind=str(item["kind"]),
                    width=int(item["width"]),
                    height=int(item["height"]),
                    data=base64.b64decode(item["data"], validate=True),
                )
                for item in answer["variants"]
            )
            processed = ProcessedImage(
                width=int(answer["width"]),
                height=int(answer["height"]),
                detected_format=str(answer["format"]),
                renditions=renditions,
            )
        except (KeyError, TypeError, ValueError) as error:
            self._log("processor_failed", started)
            raise ImageRejected("processor_failed") from error
        self._log("ok", started)
        return processed

    @staticmethod
    def _answer(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
        if completed.returncode != 0:
            if completed.returncode in (-signal.SIGXCPU, -signal.SIGKILL):
                return {"ok": False, "code": "took_too_long"}
            return {"ok": False, "code": "processor_failed"}
        try:
            answer = json.loads(completed.stdout)
        except ValueError:
            return {"ok": False, "code": "processor_failed"}
        return answer if isinstance(answer, dict) else {"ok": False, "code": "processor_failed"}

    @staticmethod
    def _log(outcome: str, started: float) -> None:
        # Counts and fixed words only: never the picture, its name or a decoder message.
        log_event(
            logger,
            "media.process.decode",
            level=logging.INFO if outcome == "ok" else logging.WARNING,
            outcome=outcome,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


__all__ = [
    "PROCESSOR_VERSION",
    "VARIANT_CONTENT_TYPE",
    "VARIANT_SIZES",
    "ImageRejected",
    "ProcessedImage",
    "ProcessorLimits",
    "Rendition",
    "SubprocessImageProcessor",
    "pillow_capability",
]
