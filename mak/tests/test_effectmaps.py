"""PBR maps -> MAK effect maps (pipeline/png.py, pipeline/effectmaps.py, openflight texture slots).
Run with:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import random
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixture import PBR_NORMAL, PBR_ORM, make_glb  # noqa: E402
from pipeline import artparts, effectmaps, glb, inspect_glb, openflight, png, run  # noqa: E402
from pipeline.common import load_config  # noqa: E402

CFG = load_config()


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def filtered_png(w: int, h: int, ch: int, data: bytes, filters: list[int]) -> bytes:
    """Encode with the given filter type per row (the PNG spec's forward filters), to test decoding."""
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[ch]
    stride, raw, prev = w * ch, bytearray(), bytes(w * ch)
    for y in range(h):
        row = data[y * stride:(y + 1) * stride]
        ft = filters[y % len(filters)]
        out = bytearray()
        for i, x in enumerate(row):
            a = row[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if ft == 0:
                pred = 0
            elif ft == 1:
                pred = a
            elif ft == 2:
                pred = b
            elif ft == 3:
                pred = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((x - pred) & 255)
        raw += bytes([ft]) + out
        prev = row
    return (png.SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


class TestPng(unittest.TestCase):
    def test_all_filters_decode(self):
        rnd = random.Random(7)
        for ch in (1, 2, 3, 4):
            data = bytes(rnd.randrange(256) for _ in range(9 * 6 * ch))
            p = png.decode(filtered_png(9, 6, ch, data, [0, 1, 2, 3, 4]))
            self.assertEqual((p.width, p.height, p.channels, p.data), (9, 6, ch, data), ch)

    def test_round_trip(self):
        p = png.Pixels(3, 2, 4, bytes(range(24)))
        self.assertEqual(png.decode(png.encode(p)), p)
        self.assertEqual(png.size(png.encode(p)), (3, 2))

    def test_palette_and_16_bit(self):
        plte = bytes([10, 20, 30, 200, 100, 50])
        raw = b"\0" + bytes([0, 1]) + b"\0" + bytes([1, 0])
        data = (png.SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 3, 0, 0, 0)) + chunk(b"PLTE", plte)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        self.assertEqual(png.decode(data).data, bytes([10, 20, 30, 200, 100, 50, 200, 100, 50, 10, 20, 30]))
        raw16 = b"\0" + bytes([0x12, 0x34, 0xAB, 0xCD])
        data16 = (png.SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 16, 0, 0, 0, 0))
                  + chunk(b"IDAT", zlib.compress(raw16)) + chunk(b"IEND", b""))
        self.assertEqual(png.decode(data16).data, bytes([0x12, 0xAB]))

    def test_not_png(self):
        with self.assertRaises(ValueError):
            png.decode(b"\xff\xd8\xff\xe0 jpeg")


class TestMaps(unittest.TestCase):
    def test_orm_repacked_to_mtl_gls_ao(self):
        mat = glb.Material("m", metallic=1.0, roughness=1.0)
        orm = png.Pixels(2, 1, 3, bytes([200, 64, 255, 0, 255, 0]))         # R AO, G roughness, B metal
        out = effectmaps.mtl_gls_ao(mat, orm, orm)
        self.assertEqual(out.data, bytes([255, 191, 200, 0, 0, 0]))           # R metal, G gloss, B AO

    def test_factors_scale_maps(self):
        mat = glb.Material("m", metallic=0.5, roughness=0.5, occlusion_strength=0.5)
        orm = png.Pixels(1, 1, 3, bytes([0, 200, 200]))
        self.assertEqual(effectmaps.mtl_gls_ao(mat, orm, orm).data, bytes([100, 155, 128]))

    def test_constant_map_without_textures(self):
        mat = glb.Material("m", metallic=0.25, roughness=0.5)
        out = effectmaps.mtl_gls_ao(mat, None, None, (2, 2))
        self.assertEqual((out.width, out.height, out.data[:3]), (2, 2, bytes([64, 128, 255])))

    def test_normal_layout_and_options(self):
        n = png.Pixels(1, 1, 3, bytes(PBR_NORMAL))
        self.assertEqual(effectmaps.normal_map(n).data, bytes([100, 180, 230, 180]))   # Y also in alpha (.ra)
        self.assertEqual(effectmaps.normal_map(n, green="down").data, bytes([100, 75, 230, 75]))
        flat = effectmaps.normal_map(png.Pixels(1, 1, 3, bytes([200, 128, 255])), scale=0.5).data
        self.assertEqual(flat[0], 164)


class TestFltSlots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, opts=None):
        model = glb.load(make_glb(self.tmp / "a.glb", pbr=True), "+Y")
        res = openflight.write_flt(model, self.tmp / "out" / "T.flt", [], "T", None, effect_maps=opts)
        return model, res, openflight.read_flt(res.path)

    def test_glb_reads_pbr_slots(self):
        m = glb.load(make_glb(self.tmp / "a.glb", pbr=True), "+Y")
        paint, plain, only = m.materials
        self.assertEqual((paint.image, paint.normal_image, paint.mr_image, paint.occlusion_image), (0, 1, 2, 2))
        self.assertTrue(paint.has_effect_maps and only.has_effect_maps and not plain.has_effect_maps)
        self.assertEqual((only.image, only.normal_image), (None, 1))

    def test_siblings_and_slots(self):
        model, res, f = self.write()
        names = [p.name for p in res.textures]
        # tex0 = checker for the plain material; maps go on a copy; white diffuse for the maps-only material
        self.assertEqual(names, ["T_tex0.png", "T_tex1.png", "T_tex2.png"])
        self.assertEqual(f.textures, names)
        self.assertEqual(sorted(p.name for p in res.effect_maps),
                         ["T_tex1_MTL_GLS_AO.png", "T_tex1_NML.png", "T_tex2_MTL_GLS_AO.png", "T_tex2_NML.png"])
        self.assertEqual(res.textures[1].read_bytes(), res.textures[0].read_bytes())
        self.assertEqual(png.decode(res.textures[2].read_bytes()).data, b"\xff" * (4 * 4 * 3))
        nml = png.decode((self.tmp / "out" / "T_tex1_NML.png").read_bytes())
        self.assertEqual(nml.data[:4], bytes([*PBR_NORMAL, PBR_NORMAL[1]]))
        mtl = png.decode((self.tmp / "out" / "T_tex1_MTL_GLS_AO.png").read_bytes())
        self.assertEqual(mtl.data[:3], bytes([PBR_ORM[2], 255 - PBR_ORM[1], PBR_ORM[0]]))
        only = png.decode((self.tmp / "out" / "T_tex2_MTL_GLS_AO.png").read_bytes())
        self.assertEqual(only.data[:3], bytes([64, 128, 255]))
        # effect-only images (normal, ORM) are not written as plain textures
        self.assertEqual(len(list((self.tmp / "out").glob("T_tex*.png"))), 7)
        # faces: hull -> tex1, turret -> tex2, gun -> tex0
        tex_by_mat = {}
        data = res.path.read_bytes()
        pos = 0
        while pos + 4 <= len(data):
            op, n = struct.unpack_from(">hH", data, pos)
            if op == openflight.OP["face"]:
                tex, mat = struct.unpack_from(">hh", data, pos + 4 + 24)
                tex_by_mat[mat] = tex
            pos += struct.unpack_from(">i", data, pos + 4)[0] if op == openflight.OP["vpalette"] else n
        self.assertEqual(tex_by_mat, {0: 1, 1: 0, 2: 2})

    def test_disabled_keeps_plain_textures(self):
        _, res, _ = self.write({"enabled": False})
        self.assertEqual([p.name for p in res.textures], ["T_tex0.png"])
        self.assertEqual(res.effect_maps, [])

    def test_check_flt_texture_missing(self):
        _, res, f = self.write()
        folder = res.path.parent
        codes = {x.code for x in openflight.check_flt(f, [], None, folder=folder)}
        self.assertEqual(codes, {"flt-effect-maps"})
        (folder / "T_tex2.png").unlink()
        self.assertIn("flt-texture-missing", {x.code for x in openflight.check_flt(f, [], None, folder=folder)})


class TestInspect(unittest.TestCase):
    def test_jpeg_size_and_effect_warning(self):
        jpeg = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"JF"
                + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 48, 64) + b"\x03\x00\x00")
        self.assertEqual(inspect_glb._png_size(jpeg), (64, 48))
        tmp = Path(tempfile.mkdtemp())
        try:
            r = inspect_glb.analyse(make_glb(tmp / "a.glb", pbr=True), "+Y")
            codes = lambda: [x.code for x in inspect_glb.check(r, None, CFG["limits"])]  # noqa: E731
            self.assertNotIn("texture-jpeg-effect", codes())
            r["textures"][1]["mime"] = "image/jpeg"        # the normal map
            self.assertIn("texture-jpeg-effect", codes())
        finally:
            shutil.rmtree(tmp)

    def test_used_as_recorded(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            r = inspect_glb.analyse(make_glb(tmp / "a.glb", pbr=True), "+Y")
            self.assertEqual([t["used_as"] for t in r["textures"]],
                             [["base"], ["normal"], ["metallic-roughness", "occlusion"]])
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(Path(CFG.get("sms_dir", "")).exists(), "VR-Forces install not present")
class TestEndToEnd(unittest.TestCase):
    def test_run_writes_effect_maps(self):
        from test_openflight import MANIFEST       # the wheeled test box (2S1 donor)
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "models" / MANIFEST["name"]
            make_glb(d / "model.glb", pbr=True)
            (d / "manifest.json").write_text(json.dumps(MANIFEST))
            r = run.run(d, output_root=tmp / "output", fmt="flt",
                        mak_check=Path(CFG.get("medf_tool", "")).exists())
            self.assertTrue(r["ok"], json.dumps(r["stages"], indent=1))
            codes = {x["code"] for x in r["stages"]["verify_model"]}
            self.assertIn("flt-effect-maps", codes)
            self.assertNotIn("flt-texture-missing", codes)
            folder = next((tmp / "output").rglob("*.flt")).parent
            self.assertTrue((folder / f"{MANIFEST['name']}_tex1_NML.png").exists())
            if Path(CFG.get("medf_tool", "")).exists():      # packaged like stock models (ACM 19.19)
                self.assertEqual([x["code"] for x in r["stages"]["package"]], ["medf-packaged"])
                name = MANIFEST["name"]
                self.assertTrue((folder / f"{name}.medf").exists())
                for stem in (f"{name}_tex0", f"{name}_tex1", f"{name}_tex1_NML", f"{name}_tex1_MTL_GLS_AO"):
                    self.assertTrue((folder / f"{stem}_png.meif").exists(), stem)
                r2 = run.run(d, output_root=tmp / "output", fmt="flt", mak_check=False, medf_package=False)
                self.assertNotIn("package", r2["stages"])
        finally:
            shutil.rmtree(tmp)


class TestMedf(unittest.TestCase):
    def test_unavailable_tool_warns(self):
        from pipeline import medf
        f = medf.package(Path("nowhere/x.flt"), "C:/no/medfTool.exe")
        self.assertEqual([(x.level, x.code) for x in f], [("warning", "medf-unavailable")])


if __name__ == "__main__":
    unittest.main()
