"""Removing the hidden details a photo carries, without decoding it (AST-006).

A camera or phone writes where and when a picture was taken, the device, its serial
number, editing history and often a small copy of the picture into the file itself
(EXIF, XMP, IPTC, comments, text chunks). Uploads keep none of it.

The file is taken apart by its container structure only: JPEG segments, PNG chunks and
WebP (RIFF) chunks. The image data is copied byte for byte and nothing is decoded, so no
image codec ever runs on an uploaded file. Kept, because they change how the picture
looks: the colour profile (JPEG APP2 ``ICC_PROFILE``, PNG ``iCCP``, WebP ``ICCP``), the
Adobe colour-transform marker (JPEG APP14) and the PNG colour and transparency chunks.
Besides those, only what a decoder needs is kept (JPEG frames, tables and scans, JFIF
without its thumbnail; the PNG critical chunks), so no unknown segment can carry bytes in.
The one detail kept from EXIF is which way up the picture goes: it is written back as a
new, minimal EXIF block that holds only the orientation, so a photo taken on its side
still shows the right way up. Anything after the end of the image is dropped.

A file whose structure cannot be followed is refused rather than stored as it came.
"""

from __future__ import annotations

import struct
import zlib

ORIENTATION_TAG = 0x0112
_EXIF_HEADER = b"Exif\x00\x00"


class ImageMetadataError(ValueError):
    """The file's structure could not be followed, so it cannot be cleaned."""


# ---------------------------------------------------------------- orientation (TIFF)


def orientation_from_tiff(tiff: bytes) -> int | None:
    """The orientation (1-8) in a TIFF/EXIF block's first directory, or None."""
    if tiff.startswith(_EXIF_HEADER):
        tiff = tiff[len(_EXIF_HEADER) :]
    if len(tiff) < 8:
        return None
    order = tiff[:2]
    if order == b"II":
        endian = "<"
    elif order == b"MM":
        endian = ">"
    else:
        return None
    if struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        return None
    offset = struct.unpack(endian + "I", tiff[4:8])[0]
    if offset + 2 > len(tiff):
        return None
    count = struct.unpack(endian + "H", tiff[offset : offset + 2])[0]
    for index in range(count):
        start = offset + 2 + index * 12
        if start + 12 > len(tiff):
            return None
        tag, kind, number = struct.unpack(endian + "HHI", tiff[start : start + 8])
        if tag != ORIENTATION_TAG:
            continue
        if kind != 3 or number != 1:  # one SHORT, as the EXIF standard says
            return None
        value = struct.unpack(endian + "H", tiff[start + 8 : start + 10])[0]
        return value if 1 <= value <= 8 else None
    return None


def orientation_tiff(orientation: int) -> bytes:
    """A TIFF block whose only entry is the orientation."""
    return (
        b"MM\x00\x2a"
        + struct.pack(">I", 8)
        + struct.pack(">H", 1)
        + struct.pack(">HHIHH", ORIENTATION_TAG, 3, 1, orientation, 0)
        + struct.pack(">I", 0)
    )


def _turned(orientation: int | None) -> int | None:
    """An orientation worth writing back (1 is the default and says nothing)."""
    return orientation if orientation is not None and 2 <= orientation <= 8 else None


# ------------------------------------------------------------------------------ JPEG

# Markers with no length after them.
_STANDALONE = {0x01, *range(0xD0, 0xD8)}
# Start of frame, in every coding (0xC4, 0xC8 and 0xCC are other markers).
_SOF = frozenset({*range(0xC0, 0xD0)} - {0xC4, 0xC8, 0xCC})
# The segments a decoder needs: frames, Huffman and quantisation tables, restart interval,
# start of scan, number of lines, arithmetic conditioning and expand reference. Anything
# else (JPGn, reserved markers, unknown APPn) could carry any bytes, so it is dropped.
_JPEG_STRUCTURE = _SOF | {0xC4, 0xDB, 0xDD, 0xDA, 0xDC, 0xCC, 0xDF}
# JFIF without a thumbnail: identifier, version, units, density and a 0x0 thumbnail.
_JFIF_LENGTH = 14


def _jpeg_keep(marker: int, payload: bytes) -> bytes | None:
    """The segment's payload as it is kept, or None when the segment is dropped."""
    if marker in _JPEG_STRUCTURE:
        return payload
    if marker == 0xE0:  # APP0: JFIF, which says nothing about the picture's origin
        if not payload.startswith(b"JFIF\x00") or len(payload) < _JFIF_LENGTH:
            return None  # JFXX holds only a thumbnail
        return payload[: _JFIF_LENGTH - 2] + b"\x00\x00"  # and no thumbnail here either
    if marker == 0xE2:  # APP2: keep the colour profile only (not FlashPix or MPF)
        return payload if payload.startswith(b"ICC_PROFILE\x00") else None
    if marker == 0xEE:  # APP14: Adobe colour transform; without it CMYK comes out wrong
        return payload if payload.startswith(b"Adobe") else None
    return None


def strip_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8"):
        raise ImageMetadataError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    orientation: int | None = None
    insert_at = len(out)
    position = 2
    size = len(data)
    in_scan = framed = scanned = False
    while position < size:
        if in_scan:
            # Entropy-coded data: copy up to the next marker that is not a stuffed 0xFF00
            # or a restart marker.
            start = position
            while True:
                found = data.find(b"\xff", position)
                if found < 0 or found + 1 >= size:
                    raise ImageMetadataError("the image data does not end")
                following = data[found + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7 or following == 0xFF:
                    position = found + 1 if following == 0xFF else found + 2
                    continue
                break
            out += data[start:found]
            position = found
            in_scan = False
            continue
        if data[position] != 0xFF:
            raise ImageMetadataError("a segment does not start where one should")
        # Fill bytes before a marker are allowed.
        while position < size and data[position] == 0xFF:
            position += 1
        if position >= size:
            raise ImageMetadataError("the file ends inside a marker")
        marker = data[position]
        position += 1
        if marker == 0xD9:  # end of image: anything after it is dropped
            out += b"\xff\xd9"
            break
        if marker in _STANDALONE:
            out += bytes((0xFF, marker))
            continue
        if marker == 0xD8:
            raise ImageMetadataError("a second start of image")
        if position + 2 > size:
            raise ImageMetadataError("the file ends inside a segment")
        length = struct.unpack(">H", data[position : position + 2])[0]
        if length < 2 or position + length > size:
            raise ImageMetadataError("a segment runs past the end of the file")
        payload = data[position + 2 : position + length]
        position += length
        if marker == 0xE1 and payload.startswith(_EXIF_HEADER) and orientation is None:
            orientation = orientation_from_tiff(payload)
        kept = _jpeg_keep(marker, payload)
        if kept is not None:
            out += bytes((0xFF, marker)) + struct.pack(">H", len(kept) + 2) + kept
            if marker == 0xE0 and insert_at == 2:
                insert_at = len(out)  # EXIF goes after JFIF, as readers expect
        if marker in _SOF:
            framed = True
        if marker == 0xDA:
            if not framed:
                raise ImageMetadataError("the image data comes before its frame")
            in_scan = scanned = True
    else:
        raise ImageMetadataError("the file has no end of image")
    if not scanned:
        raise ImageMetadataError("the file holds no image data")
    turned = _turned(orientation)
    if turned is not None:
        body = _EXIF_HEADER + orientation_tiff(turned)
        out[insert_at:insert_at] = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    return bytes(out)


# ------------------------------------------------------------------------------- PNG

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_CRITICAL = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
# Ancillary chunks kept because they change how the picture looks or moves.
_PNG_KEEP = frozenset(
    {
        b"tRNS",
        b"cHRM",
        b"gAMA",
        b"iCCP",
        b"sBIT",
        b"sRGB",
        b"cICP",
        b"mDCV",
        b"mDCv",
        b"cLLI",
        b"cLLi",
        b"bKGD",
        b"hIST",
        b"pHYs",
        b"acTL",
        b"fcTL",
        b"fdAT",
    }
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def strip_png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIGNATURE):
        raise ImageMetadataError("not a PNG")
    chunks: list[tuple[bytes, bytes]] = []
    orientation: int | None = None
    position = len(_PNG_SIGNATURE)
    size = len(data)
    ended = False
    while position < size:
        if position + 8 > size:
            raise ImageMetadataError("the file ends inside a chunk")
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > size or not kind.isalpha():
            raise ImageMetadataError("a chunk runs past the end of the file")
        payload = data[position + 8 : position + 8 + length]
        whole = data[position:end]
        position = end
        if kind == b"eXIf" and orientation is None:
            orientation = orientation_from_tiff(payload)
        # Critical chunks (upper-case first letter) are always kept, and one no reader knows
        # means the picture cannot be shown; ancillary ones are kept only when they are
        # known to change how the picture looks.
        if kind[0:1].isupper() and kind not in _PNG_CRITICAL:
            raise ImageMetadataError("an unknown chunk the picture depends on")
        if kind in _PNG_CRITICAL or kind in _PNG_KEEP:
            chunks.append((kind, whole))
        if kind == b"IEND":
            ended = True
            break
    if not ended or not chunks or chunks[0][0] != b"IHDR":
        raise ImageMetadataError("the file has no header or no end")
    out = bytearray(_PNG_SIGNATURE)
    turned = _turned(orientation)
    for kind, whole in chunks:
        if turned is not None and kind in (b"IDAT", b"fdAT", b"IEND"):
            # Browsers read the orientation only before the image data.
            out += _png_chunk(b"eXIf", orientation_tiff(turned))
            turned = None
        out += whole
    return bytes(out)


# ------------------------------------------------------------------------------ WebP

_WEBP_KEEP = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"})
_VP8X_EXIF = 0x08
_VP8X_XMP = 0x04


def strip_webp(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageMetadataError("not a WebP")
    declared = struct.unpack("<I", data[4:8])[0]
    size = min(len(data), declared + 8)
    if declared < 4 or declared + 8 > len(data):
        raise ImageMetadataError("the file is shorter than it says")
    position = 12
    chunks: list[tuple[bytes, bytes]] = []
    orientation: int | None = None
    while position < size:
        if position + 8 > size:
            raise ImageMetadataError("the file ends inside a chunk")
        kind = data[position : position + 4]
        length = struct.unpack("<I", data[position + 4 : position + 8])[0]
        end = position + 8 + length
        if end > size:
            raise ImageMetadataError("a chunk runs past the end of the file")
        payload = data[position + 8 : end]
        position = end + (length & 1)  # chunks are padded to an even length
        if kind == b"EXIF" and orientation is None:
            orientation = orientation_from_tiff(payload)
        if kind in _WEBP_KEEP:
            chunks.append((kind, payload))
    if not any(kind in (b"VP8 ", b"VP8L", b"ANMF") for kind, _ in chunks):
        raise ImageMetadataError("the file holds no image")
    turned = _turned(orientation)
    has_vp8x = bool(chunks) and chunks[0][0] == b"VP8X"
    if turned is not None and has_vp8x:
        chunks.append((b"EXIF", orientation_tiff(turned)))
    else:
        turned = None  # a simple WebP cannot carry EXIF without being rewritten
    body = bytearray(b"WEBP")
    for kind, payload in chunks:
        if kind == b"VP8X" and len(payload) >= 1:
            flags = payload[0] & ~(_VP8X_EXIF | _VP8X_XMP)
            if turned is not None:
                flags |= _VP8X_EXIF
            payload = bytes((flags,)) + payload[1:]
        body += kind + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            body += b"\x00"
    return b"RIFF" + struct.pack("<I", len(body)) + bytes(body)


def strip_metadata(data: bytes, content_type: str) -> bytes:
    """The picture without its hidden details; raises ``ImageMetadataError`` when the
    file's structure cannot be followed."""
    if content_type == "image/jpeg":
        return strip_jpeg(data)
    if content_type == "image/png":
        return strip_png(data)
    if content_type == "image/webp":
        return strip_webp(data)
    raise ImageMetadataError("not a picture type that can be cleaned")


__all__ = [
    "ImageMetadataError",
    "orientation_from_tiff",
    "orientation_tiff",
    "strip_jpeg",
    "strip_metadata",
    "strip_png",
    "strip_webp",
]
