"""Minimal PNG reader and writer (standard library only), for repacking texture maps.

decode() reads non-interlaced 8- and 16-bit greyscale, grey+alpha, RGB, RGBA and 8-bit palette PNGs into
8-bit pixels; encode() writes 8-bit greyscale/grey+alpha/RGB/RGBA with filter 0. Pixels are a flat bytes
object, row by row from the top, `channels` bytes per pixel.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from itertools import accumulate

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}          # colour type -> samples per pixel
COLOUR_TYPE = {1: 0, 2: 4, 3: 2, 4: 6}              # channels -> colour type (writing)


@dataclass
class Pixels:
    width: int
    height: int
    channels: int          # 1 grey, 2 grey+alpha, 3 RGB, 4 RGBA
    data: bytes            # width * height * channels bytes, top row first

    def channel(self, c: int) -> bytes:
        """One channel as width*height bytes."""
        return self.data[c::self.channels]


def is_png(data: bytes) -> bool:
    return data[:8] == SIGNATURE


def size(data: bytes) -> tuple[int, int] | None:
    if not is_png(data) or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


def _chunks(data: bytes):
    pos = 8
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield kind, data[pos + 8:pos + 8 + n]
        pos += 12 + n


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        row = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ftype == 1:      # Sub: running sum per channel
            for c in range(bpp):
                row[c::bpp] = bytes(x & 255 for x in accumulate(row[c::bpp]))
        elif ftype == 2:    # Up
            row = bytearray((a + b) & 255 for a, b in zip(row, prev))
        elif ftype == 3:    # Average
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif ftype == 4:    # Paeth
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif ftype != 0:
            raise ValueError(f"PNG filter type {ftype} unknown")
        out[y * stride:(y + 1) * stride] = row
        prev = row
    return out


def decode(data: bytes) -> Pixels:
    if not is_png(data):
        raise ValueError("not a PNG")
    ihdr, idat, plte, trns = None, [], None, None
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IEND":
            break
    if ihdr is None:
        raise ValueError("PNG has no IHDR")
    w, h, depth, ctype, _comp, _filt, interlace = ihdr
    if interlace:
        raise ValueError("interlaced PNG not supported")
    if ctype not in CHANNELS or depth not in (8, 16) or (ctype == 3 and depth != 8):
        raise ValueError(f"PNG colour type {ctype} / bit depth {depth} not supported")
    ch = CHANNELS[ctype]
    bpp = ch * depth // 8
    px = _unfilter(zlib.decompress(b"".join(idat)), w, h, bpp)
    if depth == 16:                      # keep the high byte of each sample
        px = px[0::2]
    if ctype == 3:                       # palette -> RGB or RGBA
        if plte is None:
            raise ValueError("palette PNG without PLTE")
        alpha = trns or b""
        n = 4 if alpha else 3
        table = [plte[i * 3:i * 3 + 3] + (bytes([alpha[i] if i < len(alpha) else 255]) if alpha else b"")
                 for i in range(len(plte) // 3)]
        return Pixels(w, h, n, b"".join(table[i] for i in px))
    return Pixels(w, h, ch, bytes(px))


def encode(pixels: Pixels, level: int = 6) -> bytes:
    if pixels.channels not in COLOUR_TYPE:
        raise ValueError(f"{pixels.channels} channels")
    stride = pixels.width * pixels.channels
    if len(pixels.data) != stride * pixels.height:
        raise ValueError("pixel data size does not match width x height x channels")

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\0" + pixels.data[y * stride:(y + 1) * stride] for y in range(pixels.height))
    ihdr = struct.pack(">IIBBBBB", pixels.width, pixels.height, 8, COLOUR_TYPE[pixels.channels], 0, 0, 0)
    return SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")


def interleave(*planes: bytes) -> bytes:
    """Join single-channel planes of equal length into interleaved pixels (e.g. R, G, B -> RGB)."""
    n = len(planes)
    out = bytearray(len(planes[0]) * n)
    for c, plane in enumerate(planes):
        out[c::n] = plane
    return bytes(out)
