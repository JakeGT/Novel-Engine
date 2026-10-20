"""The picture decoder, run as its own short-lived process (AST-006).

This file is executed as a script by ``image_processor.py`` with ``python -I`` (no
environment variables, no user site, the current directory not on the import path) and
an empty environment, so it can import nothing but the standard library and Pillow, and
it sees no secret. Before Pillow is even imported it lowers its own limits: address
space, CPU seconds, no files may be written, no core dumps. The parent also stops it
after a wall-clock timeout.

It reads the picture from standard input and writes one JSON object to standard output:
either the smaller copies (WebP, base64) with the picture's size and format, or a short
failure code. It never writes the picture's content, or an error's message, anywhere
else: an error message from a decoder can quote the file.

Decoding follows Pillow's security handbook
(https://pillow.readthedocs.io/en/latest/handbook/security.html): the byte count is
capped before decoding; only PNG, JPEG and WebP are tried (``Image.open(formats=...)``);
``MAX_IMAGE_PIXELS`` is set and a ``DecompressionBombWarning`` is an error; each side
and the pixel count are checked from the header before the pixels are read; a JPEG is
read at a reduced scale when only small copies are wanted. The orientation a photo
carries is applied, and every copy is written from bare pixels, so no EXIF, XMP, text
or colour profile reaches it. A 16-bit greyscale picture is scaled to 8 bits, and a
transparent colour becomes an alpha channel. To stay inside the memory limit the
full-size pixels are held once: nothing is copied that does not need to be, and each
copy is made from the larger one before it.
"""

from __future__ import annotations

import base64
import io
import json
import struct
import sys
import warnings
from typing import Any

ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")


class Rejected(Exception):
    """The picture cannot be used; ``code`` says why in a fixed vocabulary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _looks_allowed(data: bytes) -> bool:
    """Whether the leading bytes are those of a PNG, JPEG or WebP file."""
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


# EXIF orientation -> Pillow's ``Image.Transpose`` value (5 to 8 swap the sides).
_TRANSPOSE: dict[int, int] = {2: 0, 3: 3, 4: 1, 5: 5, 6: 4, 7: 6, 8: 2}
_QUARTER_TURNS = frozenset({2, 4, 5, 6})  # ROTATE_90, ROTATE_270, TRANSPOSE, TRANSVERSE


def bare_pixels(image: Any) -> Any:
    """The picture as 8-bit RGB, or RGBA when any of it is see-through.

    A 16-bit greyscale PNG is scaled down to 8 bits first (a plain conversion clips
    everything above 255 to white), and a single transparent colour (a PNG ``tRNS``
    chunk, in any mode) becomes an alpha channel rather than being dropped. An image
    already in the wanted mode is used as it is, not copied."""
    from PIL import Image, ImageMath

    mode = str(image.mode)
    transparency = image.info.get("transparency")
    if mode == "I" or mode.startswith("I;16"):
        wide = image if mode == "I" else image.convert("I")
        grey = wide.point(lambda value: value * (1 / 256)).convert("L")
        if isinstance(transparency, int):
            alpha = ImageMath.lambda_eval(
                lambda args: (args["wide"] != transparency) * 255, wide=wide
            ).convert("L")
            return Image.merge("RGBA", (grey, grey, grey, alpha))
        return grey.convert("RGB")
    has_alpha = mode in ("RGBA", "RGBa", "LA", "La", "PA") or transparency is not None
    target = "RGBA" if has_alpha else "RGB"
    if mode == target and transparency is None:
        return image
    return image.convert(target)


def limit_resources(limits: dict[str, Any]) -> None:
    """Lower this process's own limits; it cannot raise them again."""
    import resource

    memory = int(limits.get("memory_bytes") or 0)
    if memory > 0:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    cpu = int(limits.get("cpu_seconds") or 0)
    if cpu > 0:
        # The soft limit sends SIGXCPU; the hard limit a second later kills outright.
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def render(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    """Decode one picture within ``limits`` and make its smaller copies."""
    from PIL import Image, UnidentifiedImageError

    max_bytes = int(limits["max_bytes"])
    max_pixels = int(limits["max_pixels"])
    max_dimension = int(limits["max_dimension"])
    sizes: dict[str, int] = {str(k): int(v) for k, v in dict(limits["sizes"]).items()}
    quality = int(limits.get("quality", 80))

    if not data:
        raise Rejected("damaged")
    if len(data) > max_bytes:
        raise Rejected("too_large")

    Image.MAX_IMAGE_PIXELS = max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    damaged = (OSError, SyntaxError, ValueError, struct.error, EOFError, IndexError)

    try:
        image: Image.Image = Image.open(io.BytesIO(data), formats=list(ALLOWED_FORMATS))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise Rejected("too_many_pixels") from error
    except UnidentifiedImageError as error:
        # The file says it is one of ours but cannot be followed: cut short or broken.
        raise Rejected("damaged" if _looks_allowed(data) else "unsupported_type") from error
    except damaged as error:
        raise Rejected("damaged") from error

    detected = str(image.format or "")
    if detected not in ALLOWED_FORMATS:
        raise Rejected("unsupported_type")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise Rejected("damaged")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise Rejected("too_many_pixels")

    largest = max(sizes.values())
    try:
        if getattr(image, "n_frames", 1) > 1:
            image.seek(0)  # a moving picture: its first frame only
        if detected == "JPEG":
            # Decode at the smallest power-of-two scale still at least this large.
            image.draft(image.mode, (largest, largest))
        image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise Rejected("too_many_pixels") from error
    except damaged as error:
        raise Rejected("damaged") from error

    # Turned the right way up by hand, not with ``exif_transpose``: that rewrites the
    # metadata after moving the pixels, so a failure there could leave the two disagreeing.
    # The size reported is the size of what was actually produced.
    try:
        orientation = int(image.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 - an unreadable orientation is ignored, not fatal
        orientation = 1
    method = _TRANSPOSE.get(orientation)
    if method is not None:
        try:
            # Rebinding drops the unturned pixels: only one full-size copy is kept.
            image = image.transpose(Image.Transpose(method))
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 - shown as stored rather than not at all
            method = None
    if method in _QUARTER_TURNS:
        width, height = height, width

    try:
        bare = bare_pixels(image)
    except damaged as error:
        raise Rejected("damaged") from error
    del image
    # Nothing the file carried travels with the pixels.
    bare.info.clear()

    # The largest copy is made first and each smaller one from the copy before it, in
    # place, so the full-size pixels are never held twice.
    variants: list[dict[str, Any]] = []
    current = bare
    for kind, side in sorted(sizes.items(), key=lambda item: -item[1]):
        current.thumbnail((side, side), Image.Resampling.LANCZOS)
        current.info.clear()
        out = io.BytesIO()
        current.save(out, format="WEBP", quality=quality, method=4)
        variants.append(
            {
                "kind": kind,
                "width": current.width,
                "height": current.height,
                "data": base64.b64encode(out.getvalue()).decode("ascii"),
            }
        )
    variants.reverse()
    return {
        "ok": True,
        "width": width,
        "height": height,
        "format": detected,
        "variants": variants,
    }


def main() -> int:
    limits = json.loads(sys.argv[1])
    limit_resources(limits)
    data = sys.stdin.buffer.read(int(limits["max_bytes"]) + 1)
    try:
        answer = render(data, limits)
    except Rejected as rejected:
        answer = {"ok": False, "code": rejected.code}
    except MemoryError:
        answer = {"ok": False, "code": "out_of_memory"}
    except Exception:  # noqa: BLE001 - only a fixed code leaves this process
        answer = {"ok": False, "code": "processor_failed"}
    sys.stdout.buffer.write(json.dumps(answer).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
