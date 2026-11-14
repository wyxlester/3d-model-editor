"""Kind profiles (pipeline/profiles.py): each kind against the install, then one full run per kind.
Run with:  python -m unittest discover -s tests -v
Tests that need the VR-Forces install skip without it."""
from __future__ import annotations

import collections
import json
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
from pipeline import draft_manifest, generate, glb as glbmod, mtl, profiles, run  # noqa: E402
from pipeline.common import InputError, load_config, load_manifest  # noqa: E402

CFG = load_config()
VRFSIM = Path(CFG["sms_dir"]) / CFG.get("donor_sms", "EntityLevel") / "vrfSim"
DEFS = Path(CFG["definitions_dir"])
HAVE_INSTALL = (VRFSIM / "Super_Tanker.entity").exists() and DEFS.exists()
HAVE_MEDF = Path(CFG["medf_tool"]).exists()


def box(lo, hi):
    """A closed box as glb triangles (x right, y forward, z up)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    c = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    n, uv = ((0, 0, 1),) * 3, ((0, 0),) * 3
    return [glbmod.Triangle(tuple(c[i] for i in t), n, uv, None)
            for a, b, cc, d in quads for t in ((a, b, cc), (a, cc, d))]


def model(*boxes):
    tris = [t for b in boxes for t in box(*b)]
    return glbmod.Model({"Body": glbmod.Node("Body", None, triangles=tris)}, ["Body"], [], [], [])


class TestProfiles(unittest.TestCase):
    def test_old_manifests_map_to_a_kind(self):
        self.assertEqual(profiles.kind_of({"domain": "surface"}), "surface_ship")
        self.assertEqual(profiles.kind_of({"domain": "land"}), "ground_vehicle")
        self.assertEqual(profiles.kind_of({"kind": "building", "domain": "land"}), "building")
        with self.assertRaises(profiles.ProfileError):
            profiles.kind_of({"kind": "spaceship"})
        with self.assertRaises(profiles.ProfileError):
            profiles.kind_of({})

    def test_defaults_come_from_the_kind_and_the_manifest_wins(self):
        m = profiles.apply_defaults({"kind": "building"})
        self.assertEqual((m["domain"], m["platform_ope"], m["model_category_dir"]),
                         ("land", "Cultural_Feature.ope", "Structures/Buildings"))
        m = profiles.apply_defaults({"kind": "building", "model_category_dir": "Structures/Hangars"})
        self.assertEqual(m["model_category_dir"], "Structures/Hangars")
        m = profiles.apply_defaults({"kind": "munition", "munition_class": "bomb"})
        self.assertEqual((m["platform_ope"], m["model_category_dir"]), ("Bomb.ope", "Munitions/Bombs"))
        with self.assertRaises(profiles.ProfileError):
            profiles.profile_for({"kind": "munition", "munition_class": "arrow"})

    def test_dis_type_must_fit_the_kind(self):
        self.assertIsNone(profiles.dis_type_problem({"kind": "surface_ship", "dis_type": "1:3:15:80:1:0:0"}))
        self.assertIn("kind 1", profiles.dis_type_problem({"kind": "building", "dis_type": "1:1:225:1:1:1:0"}))
        self.assertIn("domain 3", profiles.dis_type_problem({"kind": "fixed_wing", "dis_type": "1:3:225:1:1:1:0"}))
        self.assertIsNone(profiles.dis_type_problem({"kind": "munition", "dis_type": "2:9:225:1:1:1:0"}))

    def test_only_the_ship_is_marked_verified(self):
        self.assertEqual([k for k, p in profiles.PROFILES.items() if p.status == "verified"], ["surface_ship"])

    @unittest.skipUnless(HAVE_INSTALL, "VR-Forces install not present")
    def test_profiles_match_the_stock_entities(self):
        """Each profile's DIS kind and domain is what at least 90% of the stock entities on its platform use,
        and its model definition schema is what the stock model definitions in its folder use."""
        by_ope = collections.defaultdict(collections.Counter)
        for p in VRFSIM.glob("*.entity"):
            m = re.search(r'objectType="1:(-?\d+):(-?\d+):[^"]*"[^>]*platform="[^"]*[/\\]([^"/\\]+)"',
                          p.read_text(encoding="utf-8", errors="replace"))
            if m:
                by_ope[m.group(3)][(int(m.group(1)), int(m.group(2)))] += 1
        seen = [(profiles.PROFILES[k], profiles.PROFILES[k].platform_ope) for k in profiles.KINDS if k != "munition"]
        seen += [(profiles.profile_for({"kind": "munition", "munition_class": c}), ope)
                 for c, (ope, _) in profiles.MUNITION_CLASSES.items() if c in ("missile", "bomb", "naval_mine")]
        for prof, ope in seen:
            counts = by_ope[ope]
            total = sum(counts.values())
            self.assertGreater(total, 0, f"no stock entity uses {ope}")
            fit = sum(n for (kind, dom), n in counts.items()
                      if kind == prof.dis_kind and prof.dis_domain in (None, dom))
            self.assertGreaterEqual(fit / total, 0.9, f"{prof.kind}: {dict(counts)} on {ope}")
        for prof in profiles.PROFILES.values():
            if prof.route != "model":
                continue
            folder = DEFS / prof.category
            schemas = collections.Counter(s for p in folder.rglob("*.ommx")
                                          for s in re.findall(r"<Schema>([^<]+)</Schema>",
                                                              p.read_text(encoding="utf-8", errors="replace")))
            if schemas:
                self.assertEqual(schemas.most_common(1)[0][0], prof.schema, f"{prof.kind}: {dict(schemas)}")


class TestGroundSupports(unittest.TestCase):
    def test_tricycle_gear_aircraft(self):
        # fuselage around the origin (centre of gravity), two main wheels behind it, a nose wheel ahead
        m = model(((-1, -6, -0.5), (1, 6, 1.5)), ((-2.2, -1.2, -2), (-1.8, -0.8, -1.5)),
                  ((1.8, -1.2, -2), (2.2, -0.8, -1.5)), ((-0.1, 4.0, -2), (0.1, 4.4, -1.5)))
        gs = glbmod.ground_supports(m)
        self.assertEqual(gs["left"], [-1.0, -2.2, 2.0])       # main gear, 2 m below the origin
        self.assertEqual(gs["right"], [-1.0, 2.2, 2.0])
        self.assertEqual(gs["other"], [4.2, 0.0, 2.0])        # nose wheel, on the centreline

    def test_flat_base_uses_front_corners_and_rear_centre(self):
        gs = glbmod.ground_supports(model(((-5, -10, 0), (5, 10, 8))))     # a building
        self.assertEqual((gs["left"], gs["right"], gs["other"]), ([10.0, -5.0, 0.0], [10.0, 5.0, 0.0], [-10.0, 0.0, 0.0]))


class TestMtl(unittest.TestCase):
    TABLE = ('(ammunition-params \n   ;; comment with (parens\n   (first-one \n      (munition-type 2 2 225 1 4 0 0)\n'
             '      (warhead -1)\n      (gui-label "First \\"One\\"")\n      (targets \n         (target-type 1 1 -1 -1 -1 -1 -1)\n'
             '      )\n      (tags "Entity" "Munition")\n   )\n)\n')

    def test_find_clone_and_merge(self):
        e = mtl.find_ammunition(self.TABLE, "2:2:225:1:4:0:0")
        self.assertEqual(e.name, "first-one")
        self.assertIsNone(mtl.find_ammunition(self.TABLE, "2:2:225:1:5:0:0"))
        new = mtl.ammunition_entry("My Missile", "2:2:225:1:5:0:0", "My Missile", e.text(self.TABLE), [])
        self.assertIn("(my-missile", new)
        self.assertIn("(munition-type 2 2 225 1 5 0 0)", new)
        self.assertIn('(gui-label "My Missile")', new)
        self.assertIn("(target-type 1 1 -1 -1 -1 -1 -1)", new)       # the donor's targets carry over
        merged, changed = mtl.merge_ammunition(self.TABLE, new)
        self.assertTrue(changed)
        self.assertEqual([x.name for x in mtl.scan(merged, 1) if x.depth == 1], ["first-one", "my-missile"])
        self.assertEqual(mtl.merge_ammunition(merged, new), (merged, False))      # merging twice changes nothing
        empty, _ = mtl.merge_ammunition("", new)                                # a new custom SMS has no table
        self.assertEqual([x.name for x in mtl.scan(empty, 1)], ["ammunition-params", "my-missile"])

    def test_plain_entry_without_a_donor(self):
        new = mtl.ammunition_entry("Dumb Bomb", "2:9:225:2:9:0:0", "Dumb Bomb", None, ["Entity", "Munition"])
        self.assertIn("(warhead -1)", new)
        self.assertIn('(tags "Entity" "Munition")', new)

    def test_characters(self):
        t = mtl.characters('(character-animations\n (dog\n  (display-name "Dog")\n  (animations\n   (animation\n'
                           '    (animation-name "sit")\n   )\n   (animation\n    (animation-name "stand")\n   )\n  )\n )\n)')
        self.assertEqual(t, {"dog": {"display_name": "Dog", "animations": ["sit", "stand"]}})


@unittest.skipUnless(HAVE_INSTALL, "VR-Forces install not present")
class TestOneRunPerKind(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_kind(self, manifest: dict, glb: bool = True, **fixture):
        d = self.tmp / "models" / manifest["name"]
        d.mkdir(parents=True)
        if glb:
            make_glb(d / "model.glb", **fixture)
        (d / "manifest.json").write_text(json.dumps(manifest))
        result = run.run(d, output_root=self.tmp / "output", fmt="flt", mak_check=HAVE_MEDF)
        errors = [x for st in result["stages"].values() for x in st if x["level"] == "error"]
        self.assertFalse(errors, errors)
        out = self.tmp / "output" / manifest["name"]
        so = ET.parse(out / "sms" / "vrfSim" / f"{manifest['display_name']}.entity").getroot().find("simObject")
        codes = {x["code"] for st in result["stages"].values() for x in st}
        return result, out, so, codes

    @staticmethod
    def support(so, name):
        e = so.find(f"bodyPosition[@paramName='{name}']")
        return tuple(float(e.get(k)) for k in ("forward", "right", "down"))

    def test_fixed_wing_aircraft(self):
        m = {"name": "TestPlane", "display_name": "Test Plane", "kind": "fixed_wing", "dis_type": "1:2:225:99:99:99:0",
             "donor_entity": "Boeing 737-400", "forward_axis_in_blender": "+Y", "articulated_parts": []}
        result, out, so, codes = self.run_kind(m, lift=-1.0)        # origin inside the model, as at the CG
        self.assertEqual(result["kind"], "fixed_wing")
        self.assertIn("kind-untested", codes)
        self.assertTrue((out / "SharedData/ModelData/Vehicles/FixedWing/TestPlane/TestPlane.flt").exists())
        self.assertIn("<Schema>Entity</Schema>", (out / "sms/gui/visuals/UserData/TestPlane.ommx").read_text())
        self.assertIn("Fixed_Wing_Aircraft.ope", so.get("platform"))
        self.assertEqual(self.support(so, "left-support")[2], 1.0)          # supports 1 m below the origin
        self.assertEqual(self.support(so, "left-support")[1], -self.support(so, "right-support")[1])

    def test_building_can_be_walkable(self):
        m = {"name": "TestHall", "display_name": "Test Hall", "kind": "building", "dis_type": "5:1:225:99:99:99:0",
             "donor_entity": "Aircraft_Hangar", "forward_axis_in_blender": "+Y", "articulated_parts": [],
             "walkable": True}
        _, out, so, codes = self.run_kind(m)
        self.assertTrue((out / "SharedData/ModelData/Structures/Buildings/TestHall/TestHall.flt").exists())
        self.assertIn("Cultural_Feature.ope", so.get("platform"))
        flag = lambda n: so.findtext(f"bool[@paramName='{n}']")     # noqa: E731
        self.assertEqual((flag("gravity-aligned"), flag("embed-into-terrain"), flag("use-object-geometry")),
                         ("True", "True", "True"))
        self.assertEqual(so.findtext("string[@paramName='object-geometry-filename']"),
                         "@(SHARED_DATA_DIR)/ModelData/Structures/Buildings/TestHall/TestHall.flt")
        self.assertEqual(self.support(so, "left-support")[2], 0.0)
        self.assertIn("object-geometry", codes)

    def test_building_without_walkable_drops_the_donors_geometry(self):
        m = {"name": "TestShed", "display_name": "Test Shed", "kind": "building", "dis_type": "5:1:225:99:99:98:0",
             "donor_entity": "Aircraft_Hangar", "forward_axis_in_blender": "+Y", "articulated_parts": []}
        _, _, so, _ = self.run_kind(m)
        self.assertFalse(so.findtext("string[@paramName='object-geometry-filename']"))
        self.assertEqual(so.findtext("bool[@paramName='use-object-geometry']"), "False")

    def test_munition_gets_an_ammunition_entry(self):
        m = {"name": "TestMissile", "display_name": "Test Missile", "kind": "munition", "munition_class": "missile",
             "dis_type": "2:2:225:1:99:99:0", "donor_entity": "AGM-65_Maverick_Missile",
             "forward_axis_in_blender": "+Y", "articulated_parts": []}
        result, out, so, codes = self.run_kind(m, lift=-1.0)
        self.assertTrue((out / "SharedData/ModelData/Munitions/Missiles/TestMissile/TestMissile.flt").exists())
        self.assertIn("Missile.ope", so.get("platform"))
        self.assertEqual(so.get("objectType"), "1:2:2:225:1:99:99:0")
        entry = (out / "sms" / "ammunitionParams.additions.mtl").read_text()
        self.assertIn("(testmissile", entry)
        self.assertIn("(munition-type 2 2 225 1 99 99 0)", entry)
        self.assertIn("(target-type 1 1 -1 -1 -1 -1 -1)", entry)            # the Maverick's targets
        self.assertTrue({"ammunition-merge", "detonation-power", "weapon-ammunition-list"} <= set(result["pending"]))

    def test_munition_type_may_not_have_wildcards(self):
        m = {"name": "BadMissile", "display_name": "Bad Missile", "kind": "munition", "dis_type": "2:2:225:1:99:-1:0",
             "donor_entity": "AGM-65_Maverick_Missile", "forward_axis_in_blender": "+Y", "articulated_parts": []}
        d = self.tmp / "models" / "BadMissile"
        make_glb(d / "model.glb", lift=-1.0)
        (d / "manifest.json").write_text(json.dumps(m))
        result = run.run(d, output_root=self.tmp / "output", fmt="flt", mak_check=False)
        self.assertIn("munition-type-wildcard", {x["code"] for x in result["stages"]["generate"]})
        self.assertFalse(result["ok"])

    def test_human_is_a_diguy_character_with_no_model(self):
        m = {"name": "TestSailor", "display_name": "Test Sailor", "kind": "human", "dis_type": "3:1:225:99:99:99:0",
             "donor_entity": "Afghan Police Officer", "character_type": "soldier_17", "animation": "stand_ready"}
        result, out, so, codes = self.run_kind(m, glb=False)
        self.assertFalse((out / "SharedData").exists())                    # nothing to import
        self.assertFalse((out / "sms/gui/visuals/UserData/TestSailor.ommx").exists())
        self.assertIn("<myName>TestSailor</myName>", (out / "sms/gui/visuals/Entity/TestSailor.leaf").read_text())
        self.assertIn("3:1:225:99:99:99:0", (out / "sms/gui/visuals/Entity/TestSailor.metx").read_text())
        self.assertIn("Human.ope", so.get("platform"))
        self.assertEqual(so.findtext("string[@paramName='character-type']"), "soldier_17")
        self.assertEqual(so.findtext("bool[@paramName='di-guy-enabled']"), "True")
        self.assertIn("no-model", codes)

    def test_animal_and_unknown_character(self):
        m = {"name": "TestDog", "display_name": "Test Dog", "kind": "animal", "dis_type": "3:1:225:99:98:99:0",
             "donor_entity": "Bomb Sniffing Dog", "character_type": "dog"}
        _, _, so, _ = self.run_kind(m, glb=False)
        self.assertIn("Animal.ope", so.get("platform"))
        self.assertEqual(so.findtext("string[@paramName='character-appearance']"), "german_shepherd")   # donor's, same character
        d = self.tmp / "models" / "TestCat"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(dict(m, name="TestCat", display_name="Test Cat",
                                                         character_type="no_such_character")))
        result = run.run(d, output_root=self.tmp / "output", fmt="flt", mak_check=False)
        self.assertIn("character-unknown", {x["code"] for x in result["stages"]["generate"]})

    def test_character_manifest_needs_a_character(self):
        d = self.tmp / "models" / "Nobody"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"name": "Nobody", "display_name": "Nobody", "kind": "human",
                                                     "dis_type": "3:1:225:99:99:97:0", "donor_entity": "civilian"}))
        with self.assertRaises(InputError) as e:
            load_manifest(d)
        self.assertIn("character_type", str(e.exception))

    def test_donor_suggestions_follow_the_kind(self):
        bounds = {"extent": [30.0, 40.0, 12.0]}
        hangars = draft_manifest.suggest_donors(CFG, "building", bounds)
        self.assertTrue(hangars)
        for d in hangars:
            self.assertIn("Cultural_Feature.ope", (VRFSIM / f"{d['entity']}.entity").read_text(encoding="utf-8", errors="replace"))
        bombs = draft_manifest.suggest_donors(CFG, "munition", {"extent": [0.4, 2.5, 0.4]}, ope="Bomb.ope", armed=True)
        self.assertTrue(bombs)


if __name__ == "__main__":
    unittest.main()
