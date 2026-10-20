"""The separate, limited picture decoder (AST-003, AST-006).

Every test runs the real worker process on small pictures Pillow makes here; nothing is
read from disk and no network is used."""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from backend.adapters.image_processor import (
    PROCESSOR_VERSION,
    VARIANT_CONTENT_TYPE,
    ImageRejected,
    ProcessorLimits,
    SubprocessImageProcessor,
    pillow_capability,
)

pytestmark = pytest.mark.unit


def _png(width: int, height: int, mode: str = "RGB", color: object = "teal") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _jpeg_with_orientation(width: int, height: int, orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "CameraMaker"  # a make, which must not survive
    out = io.BytesIO()
    Image.new("RGB", (width, height), "orange").save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def _with_dimensions(png: bytes, width: int, height: int) -> bytes:
    """The same PNG with its header claiming another size (CRC fixed, so it parses)."""
    start = 8  # signature
    length = struct.unpack(">I", png[start : start + 4])[0]
    assert png[start + 4 : start + 8] == b"IHDR" and length == 13
    body = struct.pack(">II", width, height) + png[start + 16 : start + 8 + length]
    chunk = b"IHDR" + body
    crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    return png[:start] + struct.pack(">I", 13) + chunk + crc + png[start + 8 + length + 4 :]


def _processor(**limits: object) -> SubprocessImageProcessor:
    return SubprocessImageProcessor(ProcessorLimits(**limits))  # type: ignore[arg-type]


def _rejected(processor: SubprocessImageProcessor, data: bytes) -> str:
    with pytest.raises(ImageRejected) as caught:
        processor.process_sync(data)
    return caught.value.code


def test_a_picture_becomes_a_thumbnail_and_a_web_copy_with_no_metadata() -> None:
    result = _processor().process_sync(_png(2000, 1000))

    assert (result.width, result.height, result.detected_format) == (2000, 1000, "PNG")
    sizes = {copy.kind: (copy.width, copy.height) for copy in result.renditions}
    assert sizes == {"thumbnail": (256, 128), "web": (1024, 512)}
    for copy in result.renditions:
        assert copy.content_type == VARIANT_CONTENT_TYPE
        opened = Image.open(io.BytesIO(copy.data))
        assert opened.format == "WEBP"
        assert not opened.getexif()
        assert "icc_profile" not in opened.info and "exif" not in opened.info


def test_a_small_picture_is_never_made_larger() -> None:
    result = _processor().process_sync(_png(100, 60))
    assert {(copy.width, copy.height) for copy in result.renditions} == {(100, 60)}


def test_a_photo_is_turned_the_right_way_up_and_loses_its_camera_details() -> None:
    # Orientation 6: stored on its side, shown turned a quarter.
    result = _processor().process_sync(_jpeg_with_orientation(400, 200, 6))

    assert (result.width, result.height) == (200, 400)
    web = next(copy for copy in result.renditions if copy.kind == "web")
    opened = Image.open(io.BytesIO(web.data))
    assert opened.size == (200, 400)
    assert not opened.getexif()


def test_a_transparent_picture_keeps_its_transparency() -> None:
    result = _processor().process_sync(_png(50, 50, mode="RGBA", color=(0, 0, 0, 0)))
    thumbnail = Image.open(io.BytesIO(result.renditions[0].data))
    assert thumbnail.mode == "RGBA"


def test_a_decompression_bomb_is_refused() -> None:
    # Small on disk, many pixels: over the ceiling the worker sets, so Pillow's own
    # warning is raised as an error before the pixels are decoded.
    bomb = _png(3000, 3000, mode="1", color=0)
    assert len(bomb) < 50_000
    processor = _processor(max_pixels=4_000_000, max_dimension=20_000)
    assert _rejected(processor, bomb) == "too_many_pixels"


def test_a_header_claiming_a_huge_size_is_refused_without_decoding() -> None:
    lying = _with_dimensions(_png(8, 8), 100_000, 100_000)
    assert _rejected(_processor(), lying) == "too_many_pixels"


def test_one_side_too_long_is_refused_even_with_few_pixels() -> None:
    strip = _png(13_000, 1)
    assert _rejected(_processor(), strip) == "too_many_pixels"


def test_a_file_of_another_type_is_refused() -> None:
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    bmp = io.BytesIO()
    Image.new("RGB", (10, 10)).save(bmp, format="BMP")
    processor = _processor()
    assert _rejected(processor, gif.getvalue()) == "unsupported_type"
    assert _rejected(processor, bmp.getvalue()) == "unsupported_type"
    assert _rejected(processor, b"<svg onload=alert(1)>") == "unsupported_type"


def test_a_cut_short_file_is_refused_as_damaged() -> None:
    whole = _png(300, 300, color="purple")
    # Noise so the pixel data is long enough to cut through.
    noisy = io.BytesIO()
    Image.effect_noise((300, 300), 64).convert("RGB").save(noisy, format="PNG")
    data = noisy.getvalue()
    assert _rejected(_processor(), data[: len(data) // 2]) == "damaged"
    assert _rejected(_processor(), whole[:40]) == "damaged"
    assert _rejected(_processor(), b"") == "damaged"


def test_more_bytes_than_allowed_are_refused_before_the_worker_starts() -> None:
    assert _rejected(_processor(max_bytes=1000), _png(400, 400) + b"\x00" * 2000) == "too_large"


def test_a_worker_that_takes_too_long_is_stopped() -> None:
    # No interpreter starts and finishes in a millisecond: the wall clock stops it.
    assert _rejected(_processor(timeout_seconds=0.001), _png(64, 64)) == "took_too_long"


def test_a_worker_without_enough_memory_fails_with_a_fixed_code() -> None:
    # Far too little address space to import the decoder: it never succeeds, and what
    # comes back is a code from the fixed list, never a message from inside.
    code = _rejected(_processor(memory_bytes=16 * 1024 * 1024), _png(64, 64))
    assert code in {"out_of_memory", "processor_failed"}


def test_the_same_picture_gives_the_same_copies() -> None:
    data = _png(500, 300, color="navy")
    first = _processor().process_sync(data)
    second = _processor().process_sync(data)
    assert [copy.data for copy in first.renditions] == [copy.data for copy in second.renditions]


def test_capability_names_pillow_and_its_formats() -> None:
    found = pillow_capability()
    assert found["available"] is True
    assert found["webp"] and found["jpeg"] and found["png"]
    version = tuple(int(part) for part in str(found["version"]).split(".")[:2])
    assert version >= (12, 3)
    assert SubprocessImageProcessor().capability()["processor_version"] == PROCESSOR_VERSION


@pytest.mark.asyncio
async def test_the_async_entry_runs_off_the_event_loop() -> None:
    result = await _processor().process(_png(300, 300))
    assert result.width == 300


def _opened(result: object, kind: str = "web") -> Image.Image:
    copy = next(c for c in result.renditions if c.kind == kind)  # type: ignore[attr-defined]
    opened = Image.open(io.BytesIO(copy.data))
    opened.load()
    return opened


def test_a_16_bit_greyscale_picture_is_scaled_not_turned_white() -> None:
    # 40000 of 65535 is a mid grey; a plain conversion clips it to 255 (white).
    out = io.BytesIO()
    Image.new("I;16", (64, 64), 40000).save(out, format="PNG")
    assert Image.open(io.BytesIO(out.getvalue())).mode.startswith("I")

    red, green, blue = (
        _opened(_processor().process_sync(out.getvalue())).convert("RGB").getpixel((32, 32))
    )
    assert abs(red - 156) <= 3 and red == green == blue


def test_a_transparent_colour_in_a_greyscale_or_colour_png_stays_see_through() -> None:
    grey = Image.new("L", (64, 64), 7)
    grey.paste(200, (0, 0, 32, 64))
    colour = Image.new("RGB", (64, 64), (1, 2, 3))
    colour.paste((250, 10, 10), (0, 0, 32, 64))
    deep = Image.new("I;16", (64, 64), 300)
    deep.paste(60000, (0, 0, 32, 64))
    for picture, transparent in ((grey, 7), (colour, (1, 2, 3)), (deep, 300)):
        out = io.BytesIO()
        picture.save(out, format="PNG", transparency=transparent)
        web = _opened(_processor().process_sync(out.getvalue()))
        assert web.mode == "RGBA", picture.mode
        assert web.getpixel((48, 32))[3] == 0, picture.mode  # the transparent colour
        assert web.getpixel((8, 32))[3] == 255, picture.mode  # everything else


def test_a_large_but_allowed_picture_fits_in_the_default_memory_limit() -> None:
    # 40 million see-through pixels (the most allowed) as a WebP: decoding needs the
    # full-size pixels once; holding them three or four times ran out of address space
    # and failed as out_of_memory, which is never retried.
    out = io.BytesIO()
    Image.new("RGBA", (8000, 5000), (10, 120, 200, 128)).save(
        out, format="WEBP", lossless=True, method=0
    )
    result = _processor().process_sync(out.getvalue())

    assert (result.width, result.height) == (8000, 5000)
    sizes = {copy.kind: (copy.width, copy.height) for copy in result.renditions}
    assert sizes == {"thumbnail": (256, 160), "web": (1024, 640)}
    assert _opened(result, "thumbnail").mode == "RGBA"


def test_the_size_reported_is_the_size_of_the_copies_when_turning_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.adapters import image_worker

    def broken(self: Image.Image, method: object) -> Image.Image:
        raise ValueError("cannot turn")

    monkeypatch.setattr(Image.Image, "transpose", broken)
    limits = {
        "max_bytes": 8 * 1024 * 1024,
        "max_pixels": 40_000_000,
        "max_dimension": 12_000,
        "sizes": {"thumbnail": 256, "web": 1024},
        "quality": 80,
    }
    answer = image_worker.render(_jpeg_with_orientation(400, 200, 6), limits)

    # Not turned, so not reported as turned: the copies are 400 wide, and so is the size.
    web = next(item for item in answer["variants"] if item["kind"] == "web")
    assert (answer["width"], answer["height"]) == (400, 200)
    assert (web["width"], web["height"]) == (400, 200)
