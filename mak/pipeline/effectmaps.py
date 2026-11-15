"""glTF PBR maps -> MAK effect maps (AddContentToMAK 12.4), written beside the diffuse texture.

MAK finds effect maps by file name: for a diffuse `X.png` it loads `X_NML.png` (normal) and one composite map
whose name lists its codes in red, green, blue order (12.4.1). The stock PBR ships (Gerald Ford, Arleigh Burke)
use `X_MTL_GLS_AO.png`: R metalness, G gloss (= 1 - roughness, lighting.frag.glsl), B ambient occlusion.

From a glTF material:
  normalTexture                     -> X_NML.png, RGBA: R = X, G = Y, B = Z, A = Y. MAK's normal_map shader reads
                                       `.ra` ("RGB and RG (BC5) normal maps"), so Y goes in alpha too; G keeps the
                                       usual layout. glTF normals are +Y up (OpenGL); `normal_green: "down"` flips Y.
  metallicRoughnessTexture (G, B)   -> X_MTL_GLS_AO.png R = metal x metallicFactor, G = 255 - roughness x factor
  occlusionTexture (R)              ->                   B = 1 + strength x (occlusion - 1)
A material with only factors on one side (e.g. a normal map but no metallic-roughness texture) gets constant
values there. Effect maps must be PNG: the standard library cannot decode JPEG.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import png
from .glb import Image, Material

DEFAULTS = {"enabled": True, "normal_green": "up"}
WHITE_PX = 4


@dataclass
class MapResult:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def options(cfg: dict | None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("effect_maps") or {})}


def white_png() -> bytes:
    """Small white diffuse to carry effect maps for a material with no base texture; the face colour tints it."""
    return png.encode(png.Pixels(WHITE_PX, WHITE_PX, 3, b"\xff" * (WHITE_PX * WHITE_PX * 3)))


def _table(fn) -> bytes:
    return bytes(max(0, min(255, round(fn(v)))) for v in range(256))


def _decode(img: Image, slot: str, mat: Material, warnings: list[str]) -> png.Pixels | None:
    if img.data is None:
        warnings.append(f"material '{mat.name}' {slot}: image '{img.name}' is external; effect map not written")
        return None
    if not png.is_png(img.data):
        warnings.append(f"material '{mat.name}' {slot}: image '{img.name}' is not a PNG ({img.mime}); "
                        "effect map not written (texture-jpeg-effect)")
        return None
    try:
        return png.decode(img.data)
    except ValueError as e:
        warnings.append(f"material '{mat.name}' {slot}: image '{img.name}' could not be read ({e})")
        return None


def _resample(px: png.Pixels, w: int, h: int) -> png.Pixels:
    """Nearest-neighbour resize, for maps of one material that differ in size."""
    if (px.width, px.height) == (w, h):
        return px
    ch = px.channels
    rows = []
    for y in range(h):
        sy = y * px.height // h
        src = px.data[sy * px.width * ch:(sy + 1) * px.width * ch]
        rows.append(b"".join(src[(x * px.width // w) * ch:(x * px.width // w + 1) * ch] for x in range(w)))
    return png.Pixels(w, h, ch, b"".join(rows))


def _plane(px: png.Pixels, c: int) -> bytes:
    """Channel c, or the grey channel for 1-2 channel images."""
    if px.channels <= 2:
        return px.channel(0)
    return px.channel(c)


def normal_map(px: png.Pixels, scale: float = 1.0, green: str = "up") -> png.Pixels:
    r, g = _plane(px, 0), _plane(px, 1)
    b = _plane(px, 2) if px.channels >= 3 else b"\xff" * (px.width * px.height)
    if scale != 1.0:
        t = _table(lambda v: 127.5 + (v - 127.5) * scale)
        r, g = r.translate(t), g.translate(t)
    if green == "down":
        g = g.translate(_table(lambda v: 255 - v))
    return png.Pixels(px.width, px.height, 4, png.interleave(r, g, b, g))


def mtl_gls_ao(mat: Material, mr: png.Pixels | None, occ: png.Pixels | None,
               size: tuple[int, int] | None = None) -> png.Pixels:
    """size: for a material with neither map, the size to give the constant map (that of its normal map, so
    one material's maps match, as on stock models)."""
    ref = mr or occ
    w, h = (ref.width, ref.height) if ref else (size or (WHITE_PX, WHITE_PX))
    n = w * h
    if mr is not None:
        mr = _resample(mr, w, h)
        metal = _plane(mr, 2).translate(_table(lambda v: v * mat.metallic))
        gloss = _plane(mr, 1).translate(_table(lambda v: 255 - v * mat.roughness))
    else:
        metal = bytes([round(255 * max(0.0, min(1.0, mat.metallic)))]) * n
        gloss = bytes([round(255 * max(0.0, min(1.0, 1 - mat.roughness)))]) * n
    if occ is not None:
        occ = _resample(occ, w, h)
        s = mat.occlusion_strength
        ao = _plane(occ, 0).translate(_table(lambda v: 255 + s * (v - 255)))
    else:
        ao = b"\xff" * n
    return png.Pixels(w, h, 3, png.interleave(metal, gloss, ao))


def write_effect_maps(mat: Material, images: list[Image], diffuse: Path, opts: dict) -> MapResult:
    """Write <diffuse stem>_NML.png and <diffuse stem>_MTL_GLS_AO.png for one material."""
    res = MapResult()
    stem = diffuse.with_suffix("")
    cache: dict[int, png.Pixels | None] = {}

    def get(i: int | None, slot: str) -> png.Pixels | None:
        if i is None:
            return None
        if i not in cache:
            cache[i] = _decode(images[i], slot, mat, res.warnings)
        return cache[i]

    nml = get(mat.normal_image, "normal map")
    if nml is not None:
        out = Path(f"{stem}_NML.png")
        out.write_bytes(png.encode(normal_map(nml, mat.normal_scale, opts.get("normal_green", "up"))))
        res.files.append(out)
    mr, occ = get(mat.mr_image, "metallic-roughness map"), get(mat.occlusion_image, "occlusion map")
    if mr is not None and occ is not None and (mr.width, mr.height) != (occ.width, occ.height):
        res.warnings.append(f"material '{mat.name}': metallic-roughness {mr.width}x{mr.height} and occlusion "
                            f"{occ.width}x{occ.height} differ in size; occlusion resampled (texture-map-size)")
    if mr is not None or occ is not None or nml is not None:
        out = Path(f"{stem}_MTL_GLS_AO.png")
        out.write_bytes(png.encode(mtl_gls_ao(mat, mr, occ, (nml.width, nml.height) if nml else None)))
        res.files.append(out)
    return res
