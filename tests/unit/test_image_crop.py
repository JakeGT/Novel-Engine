"""Cutting one part of a picture in the limited worker, for the search index (CTX-012,
AST-006): the part as fractions of the picture as shown, only the pixels, never a file's
details; a box off the picture is refused before any worker starts."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.adapters.image_processor import (
    ImageRejected,
    ProcessorLimits,
    SubprocessImageProcessor,
)

pytestmark = pytest.mark.unit


def _png() -> bytes:
    picture = Image.new("RGB", (400, 200), (10, 10, 200))
    for x in range(200, 300):
        for y in range(100, 200):
            picture.putpixel((x, y), (220, 20, 20))
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()


def test_a_part_is_cut_from_the_picture_as_its_own_small_copy() -> None:
    processor = SubprocessImageProcessor(ProcessorLimits(sandbox="off"))
    cut = processor.crop_sync(_png(), (0.5, 0.5, 0.25, 0.5), side=64)
    assert cut.kind == "region" and cut.content_type == "image/webp"
    assert (cut.width, cut.height) == (64, 64)
    with Image.open(io.BytesIO(cut.data)) as shown:
        red, green, blue = shown.convert("RGB").getpixel((32, 32))
        assert red > 150 and blue < 100  # only the red corner
        assert not shown.info.get("exif")


@pytest.mark.parametrize(
    "box", [(1.0, 0.0, 0.1, 0.1), (0.0, 0.0, 0.0, 0.5), (-0.1, 0.0, 0.5, 0.5), (0.0, 0.0, 1.5, 1.0)]
)
def test_a_box_off_the_picture_is_refused_before_decoding(box) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ImageRejected):
        SubprocessImageProcessor().crop_sync(_png(), box)
