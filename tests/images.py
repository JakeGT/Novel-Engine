"""Small, well-formed picture files built byte by byte for tests (no image library).

The image data inside is not a real picture; only the container structure is right,
which is all the metadata strip reads."""

from __future__ import annotations

import struct
import zlib

GPS_LATITUDE = struct.pack(">IIIIII", 51, 1, 30, 1, 125, 10)


def tiff(*, orientation: int | None = 6, make: bytes = b"Canon\x00", gps: bool = True) -> bytes:
    """A big-endian TIFF block with a camera make, an orientation and a GPS directory."""
    entries: list[tuple[int, int, int, bytes]] = []
    extra = bytearray()
    count = 1 + (orientation is not None) + gps
    ifd0_size = 2 + 12 * count + 4
    data_at = 8 + ifd0_size

    def place(blob: bytes) -> int:
        offset = data_at + len(extra)
        extra.extend(blob)
        if len(extra) % 2:
            extra.append(0)
        return offset

    make_at = place(make)
    entries.append((0x010F, 2, len(make), struct.pack(">I", make_at)))
    if orientation is not None:
        entries.append((0x0112, 3, 1, struct.pack(">HH", orientation, 0)))
    gps_entry_index = None
    if gps:
        gps_entry_index = len(entries)
        entries.append((0x8825, 4, 1, b"\x00\x00\x00\x00"))
    if gps:
        latitude_at = place(GPS_LATITUDE)
        gps_ifd = (
            struct.pack(">H", 2)
            + struct.pack(">HHI", 1, 2, 2)
            + b"N\x00\x00\x00"
            + struct.pack(">HHII", 2, 5, 3, latitude_at)
            + struct.pack(">I", 0)
        )
        gps_at = place(gps_ifd)
        assert gps_entry_index is not None
        tag, kind, number, _ = entries[gps_entry_index]
        entries[gps_entry_index] = (tag, kind, number, struct.pack(">I", gps_at))
    entries.sort(key=lambda entry: entry[0])
    ifd0 = struct.pack(">H", len(entries))
    for tag, kind, number, value in entries:
        ifd0 += struct.pack(">HHI", tag, kind, number) + value
    ifd0 += struct.pack(">I", 0)
    return b"MM\x00\x2a" + struct.pack(">I", 8) + ifd0 + bytes(extra)


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


ICC = b"ICC_PROFILE\x00\x01\x01" + b"profile-bytes"
JPEG_SCAN = (
    _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
    # Entropy-coded data with a stuffed 0xFF00 and a restart marker inside it.
    + b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"
)


def jpeg(*, exif: bytes | None = None, trailing: bytes = b"") -> bytes:
    """A JPEG with JFIF, EXIF, XMP, IPTC, a colour profile, a comment, tables and a scan."""
    parts = [b"\xff\xd8", _segment(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")]
    if exif is not None:
        parts.append(_segment(0xE1, b"Exif\x00\x00" + exif))
    parts += [
        _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>Lantern Harbor</x:xmpmeta>"),
        _segment(0xED, b"Photoshop 3.0\x008BIM\x04\x04 by Mara"),
        _segment(0xE2, ICC),
        _segment(0xFE, b"taken at home"),
        _segment(0xDB, b"\x00" + bytes(range(64))),
        _segment(0xC0, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00"),
        _segment(0xC4, b"\x00" + b"\x00" * 16),
        JPEG_SCAN,
        b"\xff\xd9",
        trailing,
    ]
    return b"".join(parts)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


PNG_IHDR = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
PNG_IDAT = png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
PNG_IEND = png_chunk(b"IEND", b"")


def png(*, exif: bytes | None = None) -> bytes:
    parts = [
        b"\x89PNG\r\n\x1a\n",
        PNG_IHDR,
        png_chunk(b"iCCP", b"profile\x00\x00" + zlib.compress(b"icc")),
        png_chunk(b"tEXt", b"Author\x00Mara Vell"),
        png_chunk(b"tIME", b"\x07\xea\x0a\x11\x0c\x00\x00"),
        PNG_IDAT,
    ]
    if exif is not None:
        parts.append(png_chunk(b"eXIf", exif))
    parts += [png_chunk(b"iTXt", b"Comment\x00\x00\x00\x00\x00at home"), PNG_IEND]
    return b"".join(parts)


def webp_chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def _riff(body: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


WEBP_IMAGE = webp_chunk(b"VP8 ", b"\x30\x01\x00\x9d\x01\x2a\x01\x00\x01\x00")


def webp(*, exif: bytes | None = None) -> bytes:
    """An extended WebP with a colour profile, EXIF and XMP."""
    flags = 0x20 | (0x08 if exif is not None else 0) | 0x04
    vp8x = webp_chunk(b"VP8X", bytes((flags, 0, 0, 0)) + b"\x00\x00\x00\x00\x00\x00")
    body = vp8x + webp_chunk(b"ICCP", b"icc") + WEBP_IMAGE
    if exif is not None:
        body += webp_chunk(b"EXIF", exif)
    body += webp_chunk(b"XMP ", b"<x:xmpmeta>Lantern Harbor</x:xmpmeta>")
    return _riff(body)


def simple_webp() -> bytes:
    return _riff(WEBP_IMAGE)


def strip_free_jpeg() -> bytes:
    """A JPEG holding nothing a strip would remove."""
    return b"".join(
        [
            b"\xff\xd8",
            _segment(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00"),
            _segment(0xDB, b"\x00" + bytes(range(64))),
            _segment(0xC0, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00"),
            JPEG_SCAN,
            b"\xff\xd9",
        ]
    )
