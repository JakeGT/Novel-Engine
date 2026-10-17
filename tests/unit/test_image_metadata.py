"""Uploaded pictures lose where and when they were taken, and keep which way up they go
(AST-006). Nothing is decoded: only the file's structure is read."""

import struct

import pytest

from backend.domain.image_metadata import (
    ImageMetadataError,
    orientation_from_tiff,
    strip_jpeg,
    strip_metadata,
    strip_png,
    strip_webp,
)
from tests import images

pytestmark = pytest.mark.unit

PRIVATE = (b"Canon", images.GPS_LATITUDE, b"Lantern Harbor", b"Mara", b"at home", b"8BIM")


def _jpeg_segments(data: bytes) -> list[tuple[int, bytes]]:
    """(marker, payload) for each segment before the scan."""
    found, position = [], 2
    while data[position + 1] != 0xDA:
        marker = data[position + 1]
        length = struct.unpack(">H", data[position + 2 : position + 4])[0]
        found.append((marker, data[position + 4 : position + 2 + length]))
        position += 2 + length
    return found


def _no_private_details(data: bytes) -> None:
    for detail in PRIVATE:
        assert detail not in data, detail


def test_the_sample_carries_gps_and_a_camera() -> None:
    raw = images.tiff()
    assert orientation_from_tiff(raw) == 6
    assert images.GPS_LATITUDE in raw and b"Canon" in raw


def test_a_jpeg_loses_gps_camera_xmp_iptc_and_comments_but_keeps_its_orientation() -> None:
    original = images.jpeg(exif=images.tiff(orientation=6), trailing=b"Exif\x00\x00Canon")
    cleaned = strip_jpeg(original)

    _no_private_details(cleaned)
    segments = _jpeg_segments(cleaned)
    markers = [marker for marker, _ in segments]
    # JFIF first, then the new EXIF block holding the orientation alone.
    assert markers[:2] == [0xE0, 0xE1]
    exif = segments[1][1]
    assert orientation_from_tiff(exif) == 6
    assert len(exif) < 40
    # The colour profile and the tables stay; the comment and IPTC go.
    assert (0xE2, images.ICC) in segments
    assert 0xFE not in markers and 0xED not in markers
    assert {0xDB, 0xC0, 0xC4} <= set(markers)
    # The image data is copied byte for byte, and nothing after its end is kept.
    assert cleaned.endswith(images.JPEG_SCAN + b"\xff\xd9")
    assert cleaned.count(b"Exif") == 1


def test_an_upright_jpeg_gets_no_exif_at_all() -> None:
    cleaned = strip_jpeg(images.jpeg(exif=images.tiff(orientation=1)))
    assert b"Exif" not in cleaned
    cleaned = strip_jpeg(images.jpeg(exif=images.tiff(orientation=None)))
    assert b"Exif" not in cleaned


def test_cleaning_twice_changes_nothing_more() -> None:
    for data, kind in (
        (images.jpeg(exif=images.tiff()), "image/jpeg"),
        (images.png(exif=images.tiff()), "image/png"),
        (images.webp(exif=images.tiff()), "image/webp"),
    ):
        once = strip_metadata(data, kind)
        assert strip_metadata(once, kind) == once


def test_a_png_loses_text_time_and_exif_but_keeps_its_profile_and_orientation() -> None:
    cleaned = strip_png(images.png(exif=images.tiff(orientation=8)))

    _no_private_details(cleaned)
    assert b"tEXt" not in cleaned and b"iTXt" not in cleaned and b"tIME" not in cleaned
    assert b"iCCP" in cleaned
    # The orientation is written back before the image data, where browsers look for it.
    exif_at, idat_at = cleaned.index(b"eXIf"), cleaned.index(b"IDAT")
    assert exif_at < idat_at
    length = struct.unpack(">I", cleaned[exif_at - 4 : exif_at])[0]
    assert orientation_from_tiff(cleaned[exif_at + 4 : exif_at + 4 + length]) == 6 + 2
    assert cleaned.endswith(images.PNG_IDAT + images.PNG_IEND)


def test_a_png_without_metadata_is_unchanged() -> None:
    plain = b"\x89PNG\r\n\x1a\n" + images.PNG_IHDR + images.PNG_IDAT + images.PNG_IEND
    assert strip_png(plain) == plain
    # Anything after the end of the image is dropped.
    assert strip_png(plain + b"tEXtsecret") == plain


def test_a_webp_loses_exif_and_xmp_and_its_flags_say_so() -> None:
    cleaned = strip_webp(images.webp(exif=images.tiff(orientation=3)))

    _no_private_details(cleaned)
    assert b"XMP " not in cleaned and b"ICCP" in cleaned
    flags = cleaned[20]
    assert flags & 0x04 == 0  # no XMP
    assert flags & 0x08 == 0x08  # the orientation-only EXIF
    assert flags & 0x20 == 0x20  # the colour profile
    assert orientation_from_tiff(cleaned[cleaned.index(b"EXIF") + 8 :]) == 3
    assert struct.unpack("<I", cleaned[4:8])[0] == len(cleaned) - 8


def test_a_webp_without_exif_clears_the_exif_flag() -> None:
    cleaned = strip_webp(images.webp(exif=images.tiff(orientation=None)))
    assert b"EXIF" not in cleaned and cleaned[20] & 0x08 == 0


def test_a_simple_webp_is_unchanged() -> None:
    assert strip_webp(images.simple_webp()) == images.simple_webp()


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 32, "image/jpeg"),  # a segment of length zero
        (images.jpeg()[:-40], "image/jpeg"),  # cut off inside the image data
        (b"\xff\xd8\xff\xe1\xff\xff short", "image/jpeg"),  # a segment past the end
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png"),
        (b"\x89PNG\r\n\x1a\n" + images.PNG_IHDR, "image/png"),  # no end
        (b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16, "image/webp"),
        (b"RIFF\xff\x00\x00\x00WEBP", "image/webp"),
        (b"GIF89a", "image/gif"),
    ],
)
def test_a_file_whose_structure_cannot_be_followed_is_refused(data: bytes, kind: str) -> None:
    with pytest.raises(ImageMetadataError):
        strip_metadata(data, kind)


def test_a_broken_exif_block_keeps_no_orientation_and_no_details() -> None:
    broken = b"MM\x00\x2a\x00\x00\xff\xff" + b"Canon"
    cleaned = strip_jpeg(images.jpeg(exif=broken))
    assert b"Exif" not in cleaned and b"Canon" not in cleaned
    assert orientation_from_tiff(b"II\x2a\x00") is None
    assert orientation_from_tiff(b"XX\x00\x2a\x00\x00\x00\x08") is None


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _with(*extra: bytes) -> bytes:
    """The sample JPEG with segments added just before its tables."""
    original = images.jpeg()
    at = original.index(b"\xff\xdb")
    return original[:at] + b"".join(extra) + original[at:]


@pytest.mark.parametrize("marker", [0xF0, 0xF7, 0xFD, 0xC8, 0xDE, 0xE5, 0xEF])
def test_a_jpeg_segment_no_decoder_needs_is_dropped(marker: int) -> None:
    hidden = b"hidden payload " * 4000  # about 60 KB of anything at all
    cleaned = strip_jpeg(_with(_segment(marker, hidden)))
    assert b"hidden payload" not in cleaned
    assert marker not in [m for m, _ in _jpeg_segments(cleaned)]


def test_a_jfif_thumbnail_and_a_jfxx_block_are_dropped() -> None:
    thumbnail = b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x02\x02" + b"Mara" * 3
    original = images.jpeg()
    plain_jfif = _segment(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")
    with_thumb = original.replace(plain_jfif, _segment(0xE0, thumbnail))
    with_thumb = _with_after_jfif(with_thumb, _segment(0xE0, b"JFXX\x00\x10Mara"))
    cleaned = strip_jpeg(with_thumb)
    assert b"Mara" not in cleaned and b"JFXX" not in cleaned
    segments = _jpeg_segments(cleaned)
    assert segments[0] == (0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")


def _with_after_jfif(data: bytes, extra: bytes) -> bytes:
    length = struct.unpack(">H", data[4:6])[0]
    return data[: 4 + length] + extra + data[4 + length :]


def test_a_jpeg_without_a_frame_or_image_data_is_refused() -> None:
    with pytest.raises(ImageMetadataError):
        strip_jpeg(b"\xff\xd8\xff\xd9")
    with pytest.raises(ImageMetadataError):  # tables only, no frame and no scan
        strip_jpeg(b"\xff\xd8" + _segment(0xDB, b"\x00" + bytes(range(64))) + b"\xff\xd9")
    original = images.jpeg()
    frame = _segment(0xC0, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00")
    with pytest.raises(ImageMetadataError):  # a scan with no frame before it
        strip_jpeg(original.replace(frame, b""))


def test_a_png_loses_its_suggested_palette_and_refuses_an_unknown_critical_chunk() -> None:
    plain = b"\x89PNG\r\n\x1a\n" + images.PNG_IHDR + images.PNG_IDAT + images.PNG_IEND
    at = len(b"\x89PNG\r\n\x1a\n") + len(images.PNG_IHDR)
    with_palette = plain[:at] + images.png_chunk(b"sPLT", b"Mara\x00" + b"\x08" * 60) + plain[at:]
    assert strip_png(with_palette) == plain
    unknown = plain[:at] + images.png_chunk(b"ZZZZ", b"hidden") + plain[at:]
    with pytest.raises(ImageMetadataError):
        strip_png(unknown)
