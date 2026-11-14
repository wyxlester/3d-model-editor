"""What the Simulation Object Editor does, as rules the generator must follow (hand-import test, 2026-11-14).

The editor's own files are MAK-derived, so they are not kept in this public repository. Instead:
- the leaf rule is checked against the INSTALLED donor: our .leaf = the donor's element definition with only
  myName and the main-model references changed (what the editor wrote, line for line);
- the .ommx/.metx shapes are checked against the exact text the editor wrote (our own templates);
- leaf-building logic is checked on a small synthetic element definition written here.
Tests that need VR-Forces skip without it.
"""
from __future__ import annotations

import copy
import re
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixture import make_glb  # noqa: E402
from pipeline import generate, glb, inspect_glb, leaf, openflight, validate  # noqa: E402
from pipeline.common import layout_for, load_config  # noqa: E402

CFG = load_config()
DEFS = Path(CFG["definitions_dir"])
TANKER = generate.donor_path(CFG, "Super_Tanker")
HAVE_INSTALL = TANKER.exists() and DEFS.exists()

# A small element definition in the stock shape (written for these tests).
SYNTH = """<item><myName>Donor</myName><myParentName>Large Ship</myParentName>
<myModelSet3D><myModelSet>0</myModelSet><myVisualizerDefinitions><count>2</count><item_version>2</item_version>
<item><myName>DtModelSchema</myName><myAttributes><count>2</count><item_version>1</item_version>
<item><myName>modeldefinition</myName><myValue>DonorModel</myValue></item>
<item><myName>type</myName><myValue>DtEntityArticulatedStateModelVisualizer</myValue></item></myAttributes>
<myUuidString>f5ff6339-d803-4764-aaa3-480a7676d88d</myUuidString><myPreUuidIndex>1</myPreUuidIndex></item>
<item><myName>DtTrailEffectSchema</myName><myAttributes><count>0</count><item_version>1</item_version></myAttributes>
<myUuidString>e3d97037-4845-40cb-bd36-07e69146339c</myUuidString><myPreUuidIndex>18</myPreUuidIndex></item>
</myVisualizerDefinitions></myModelSet3D>
<myModelSetXR><myModelSet>1</myModelSet><myVisualizerDefinitions><count>0</count></myVisualizerDefinitions></myModelSetXR>
<myModelSet2D><myModelSet>5</myModelSet><myVisualizerDefinitions><count>0</count></myVisualizerDefinitions></myModelSet2D>
</item>"""

# What the editor wrote for the hand import (2026-11-14), apart from the names.
EDITOR_METX = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<!DOCTYPE boost_serialization>
<boost_serialization signature="serialization::archive" version="14">
<EntityTypeMap class_id="0" tracking_level="0" version="0">
<myMappings class_id="1" tracking_level="0" version="0">
<count>1</count>
<item_version>0</item_version>
<item class_id="2" tracking_level="0" version="0">
<first>{dis}</first>
<second>{name}</second>
</item>
</myMappings>
</EntityTypeMap>
</boost_serialization>"""


def lines(text: str) -> list[str]:
    return [l.strip() for l in text.strip().splitlines() if l.strip()]


class TestLeafLogic(unittest.TestCase):
    def test_class_ids_go_on_first_occurrences(self):
        item = leaf.normalise_item(ET.fromstring(SYNTH))
        self.assertEqual((item.get("class_id"), item.get("version")), ("2", "2"))
        self.assertEqual(item.find("myModelSet3D").get("class_id"), "3")
        self.assertIsNone(item.find("myModelSetXR").get("class_id"))
        first_vis = item.find("myModelSet3D/myVisualizerDefinitions/item")
        self.assertEqual(first_vis.get("class_id"), "5")
        self.assertIsNone(item.findall("myModelSet3D/myVisualizerDefinitions/item")[1].get("class_id"))

    def test_normalise_is_idempotent(self):
        once = leaf.normalise_item(ET.fromstring(SYNTH))
        self.assertEqual(ET.tostring(leaf.normalise_item(once)), ET.tostring(once))

    def test_retarget_changes_name_and_models_only(self):
        out = leaf.retarget(ET.fromstring(SYNTH), "harmony", "harmony", xr=True)
        self.assertEqual(out.findtext("myName"), "harmony")
        self.assertEqual(out.findtext("myParentName"), "Large Ship")
        vis3d = out.find("myModelSet3D/myVisualizerDefinitions/item/myAttributes/item/myValue")
        self.assertEqual(vis3d.text, "harmony")
        xr = out.find("myModelSetXR/myVisualizerDefinitions")
        self.assertEqual((xr.findtext("count"), xr.find("item/myUuidString").text), ("1", leaf.MODEL_SCHEMA_UUID))

    def test_retarget_adds_missing_modeldefinition(self):
        item = ET.fromstring(SYNTH)
        attrs = item.find("myModelSet3D/myVisualizerDefinitions/item/myAttributes")
        attrs.remove(attrs.findall("item")[0])
        attrs.find("count").text = "1"
        out = leaf.retarget(item, "X", "M", xr=False)
        a = {i.findtext("myName"): i.findtext("myValue")
             for i in out.find("myModelSet3D/myVisualizerDefinitions/item/myAttributes").findall("item")}
        self.assertEqual(a["modeldefinition"], "M")


@unittest.skipUnless(HAVE_INSTALL, "VR-Forces install not present")
class TestAgainstInstalledDonor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.m = {"name": "harmony_of_the_seas", "display_name": "Harmony of the Seas", "domain": "surface",
                 "dis_type": "1:3:15:80:1:0:0", "donor_entity": "Super_Tanker", "model_category_dir": "Vehicles/Surface",
                 "forward_axis_in_blender": "+Y", "articulated_parts": []}
        cls.lay = layout_for(cls.m, cls.tmp, "flt")
        cls.findings = generate.generate(cls.m, cls.lay, inspect_glb.analyse(make_glb(cls.tmp / "a.glb"), "+Y"), CFG)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_leaf_is_the_donors_with_name_and_models_changed(self):
        ot = re.search(r'objectType="([^"]+)"', TANKER.read_text(encoding="utf-8")).group(1)
        item, element, src = leaf.donor_element(DEFS, ot)
        donor = lines(src.read_text(encoding="utf-8"))
        ours = lines(self.lay.leaf.read_text(encoding="utf-8"))
        self.assertEqual(len(ours), len(donor))
        diffs = [(d, o) for d, o in zip(donor, ours) if d != o]
        self.assertEqual(diffs[0], (f"<myName>{element}</myName>", "<myName>harmony_of_the_seas</myName>"))
        self.assertTrue(all(o == "<myValue>harmony_of_the_seas</myValue>" for _, o in diffs[1:]), diffs)
        self.assertEqual(len(diffs), 3)          # name, 3D model, XR model

    def test_metx_and_ommx_shapes(self):
        self.assertEqual(lines(self.lay.metx.read_text(encoding="utf-8")),
                         lines(EDITOR_METX.format(dis="1:3:15:80:1:0:0", name="harmony_of_the_seas")))
        ommx = self.lay.ommx.read_text(encoding="utf-8")
        self.assertIn("<Schema>Watercraft</Schema>", ommx)
        self.assertEqual(ommx.count("<first>"), 2)    # the definition and its single `filename` parameter
        self.assertEqual(self.lay.ommx.relative_to(self.lay.root).as_posix(),
                         "sms/gui/visuals/UserData/harmony_of_the_seas.ommx")

    def test_entity_fixes(self):
        so = ET.parse(self.lay.entity(self.m["display_name"])).getroot().find("simObject")
        self.assertEqual(so.get("objectType"), "1:1:3:15:80:1:0:0")
        self.assertEqual(so.find("bool[@paramName='use-object-geometry']").text, "False")
        self.assertEqual(so.find("string[@paramName='short-name']").text, "Harmony of")

    def test_validates_without_leaf_errors(self):
        self.lay.model_file.parent.mkdir(parents=True, exist_ok=True)
        self.lay.model_file.write_bytes(b"stub")
        codes = {x.code for x in validate.validate(self.lay.root, CFG) if x.level == "error"}
        self.assertEqual(codes, set())


class TestFaceColours(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_faces_carry_material_colour(self):
        model = glb.load(make_glb(self.tmp / "a.glb", textured=True), "+Y")
        f = openflight.read_flt(openflight.write_flt(model, self.tmp / "a.flt", [], "T").path)
        self.assertEqual(set(f.face_colours), {(0, openflight.packed_colour((0.2, 0.4, 0.6), 1.0))})
        self.assertEqual(openflight.check_flt(f, [], None, openflight.expected_colours(model)), [])

    def test_white_faces_are_flagged(self):
        model = glb.load(make_glb(self.tmp / "a.glb", textured=True), "+Y")
        f = openflight.read_flt(openflight.write_flt(model, self.tmp / "a.flt", [], "T").path)
        f.face_colours = {(0, openflight.WHITE): 36}       # what the first OpenFlight writer produced
        self.assertEqual([x.code for x in openflight.check_flt(f, [], None, openflight.expected_colours(model))],
                         ["flt-face-colour"])


class TestShortName(unittest.TestCase):
    def test_word_boundary_at_11(self):
        sn = lambda d, **k: generate.short_name({"display_name": d, **k})
        self.assertEqual(sn("Harmony of the Seas"), "Harmony of")
        self.assertEqual(sn("Supercalifragilistic"), "Supercalifr")
        self.assertEqual(sn("Harmony of the Seas", short_name="HOTS"), "HOTS")


if __name__ == "__main__":
    unittest.main()
