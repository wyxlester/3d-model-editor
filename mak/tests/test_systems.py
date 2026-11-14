"""Systems: the sysdef reader, the catalog, applying manifest `systems`, the checks and the drafting rules.
Run with: python -m unittest discover -s tests -v. The sysdefs here are small stand-ins shaped like the stock
ones; tests against the real install skip when it is missing."""
from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import systems  # noqa: E402
from pipeline.artparts import Part  # noqa: E402
from pipeline.common import load_config, system_problems  # noqa: E402

CFG = load_config()
HAVE_INSTALL = (Path(CFG["sms_dir"]) / CFG.get("donor_sms", "EntityLevel") / "vrfSim" / "systems").is_dir()

TEMPLATE = """; a gun template: turret + gun parts
(gun-template
   (systems (main-gun (variable-bindings (DtRwInt (gun-art-part-type $gun-art-part-type)))))
   (meta-data
      (system-name "Gun template")
      (allowed-state-repository-types "surface-entity-param" "ground-vehicle-param")
      (system-categories "weapon")
      (parameter-data-list
         (art-part-type-parameter-data (parameter-name "turret-art-part-type") (variable-type "DtRwInt")
            (art-part-category "turret"))
         (art-part-type-parameter-data (parameter-name "gun-art-part-type") (variable-type "DtRwInt")
            (art-part-category "gun"))
      )
   )
)
"""
GUN = """(test-gun
   (systems
      (main-gun
         (targeting (ammunition-list (TEST-20mm (amount $num-rounds))))
         (system-definition (filename "$(system-dir)\\weapons\\templates\\gun.template_sysdef"))
      )
   )
   (meta-data
      (system-name "Test CIWS gun")  ; a comment with (parens) and "quotes"
      (parameter-data-list
         (int-parameter-data (parameter-name "num-rounds") (variable-type "DtRwInt") (default-value 100))
      )
      (merge-with-sub-systems True)
   )
)
"""
FIXED = """(fixed-gun
   (systems (g (variable-bindings (DtRwInt (turret-art-part-type 4096)) (DtRwInt (gun-art-part-type 4416)))))
   (meta-data (system-name "Fixed") (allowed-state-repository-types "ground-vehicle-param") (system-categories "weapon"))
)
"""
VARCAT = """(var-cat
   (meta-data (system-name "Naval gun") (system-categories "weapon") (allowed-state-repository-types "all")
      (parameter-data-list
         (art-part-type-parameter-data (parameter-name "gun-art-part-type") (variable-type "DtRwInt")
            (art-part-category "$default-gun-art-part-type"))))
)
"""
SENSOR = """(radar
   (meta-data (system-name "Radar") (system-categories "sensor" "object-sensor") (allowed-state-repository-types "all")
      (parameter-data-list
         (vector-parameter-data (parameter-name "sensor-position") (variable-type "DtRwOffsetVector")
            (default-value 0.0 0.0 0.0))
         (real-parameter-data (parameter-name "max-range") (variable-type "DtRwReal"))))
)
"""


def write_systems(root: Path) -> Path:
    files = {"weapons/templates/gun.template_sysdef": TEMPLATE, "weapons/test-gun.sysdef": GUN,
             "weapons/fixed-gun.sysdef": FIXED, "weapons/var-cat.sysdef": VARCAT,
             "sensors/active-radar-sensor.sysdef": SENSOR, "sensors/visual-sensor.sysdef": SENSOR}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def catalog(root: Path) -> systems.Catalog:
    sds = {p.relative_to(root).as_posix().lower(): systems.read_sysdef(p, root) for p in root.rglob("*.sysdef")}
    usage = {"weapons/test-gun.sysdef": {"groups": {"1:3": 5}, "ints": {"num-rounds": 1500}},
             "sensors/active-radar-sensor.sysdef": {"groups": {"1:3": 9}, "ints": {}}}
    bindings = {"turret-art-part-type": ["turret"], "gun-art-part-type": ["8192", "gun"],
                "elevating-turret-art-part-type": ["gun", "launcher", "turret"]}
    return systems.Catalog(sds, usage, {"Surface_Ship.ope": "surface-entity-param"}, bindings)


DONOR = """<?xml version="1.0" encoding="utf-8"?>
<simulationObject><simObject objectType="1:1:3:225:1:1:1:0" platform="@(platform-dir)\\Surface_Ship.ope">
   <DtSimArtPartDescriptorList paramName="articulated-parts"/>
   <boundingVolume paramName="bounding-volume" offsetType="custom">
      <dimensions length="100" width="20" height="30"/><offset forward="0" right="0" down="-10"/>
   </boundingVolume>
   <componentSystem systemName="damage" platform="@(system-dir)\\damage\\surface-default.sysdef"/>
   <componentSystem systemName="sensor" platform="@(system-dir)\\sensors\\active-radar-sensor.sysdef">
      <bodyPosition paramName="sensor-position" forward="0" right="0" down="0"/>
      <real paramName="max-range">10000</real>
   </componentSystem>
   <componentSystem systemName="weapon" platform="@(system-dir)\\weapons\\test-gun.sysdef">
      <int paramName="gun-art-part-type">4416</int>
      <int paramName="num-rounds">4500</int>
      <int paramName="turret-art-part-type">4096</int>
   </componentSystem>
</simObject></simulationObject>
"""


def parts_of(*rows) -> list[dict]:
    return [{"node": n, "dis_part": c, "parent": p} for n, c, p in rows]


def as_parts(rows: list[dict]) -> list[Part]:
    return [Part(r["node"], r["node"], r["dis_part"], r.get("parent"), "azimuth", -1, 1, 1, True) for r in rows]


NODES = {"Mast__Radar": {"pivot": [0.0, 10.0, 25.0]}, "Low__Radar": {"pivot": [0.0, 5.0, 12.0]},
         "socket_bridge_eye": {"pivot": [0.0, 20.0, 15.0]},
         "Weapons__Phalanx_1": {"pivot": [3.0, 30.0, 8.0]}, "Weapons__Phalanx_1_Gun": {"pivot": [3.0, 30.5, 9.0]}}


class TestSexpr(unittest.TestCase):
    def test_comments_strings_and_nesting(self):
        tree = systems.parse_sexpr('(a "x (y) ; z" b ; gone (c)\n (d (e 1)))')
        self.assertEqual(tree, [["a", "x (y) ; z", "b", ["d", ["e", "1"]]]])
        self.assertIsInstance(tree[0][1], systems.Str)

    def test_backslash_paths_and_unbalanced(self):
        tree = systems.parse_sexpr('(f "$(system-dir)\\weapons\\x.sysdef") (g')
        self.assertEqual(str(tree[0][1]), "$(system-dir)\\weapons\\x.sysdef")
        self.assertEqual(tree[1], ["g"])


class TestSysdef(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_systems(Path(self.tmp.name))
        self.cat = catalog(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_template_include_brings_part_parameters(self):
        sd = self.cat.get("@(system-dir)\\weapons\\test-gun.sysdef")
        self.assertEqual(sd.name, "Test CIWS gun")
        self.assertEqual(sd.art_params, {"turret-art-part-type": "turret", "gun-art-part-type": "gun"})
        self.assertEqual(sd.repositories, ["surface-entity-param", "ground-vehicle-param"])  # from the template
        self.assertEqual(sd.ammunition, {"TEST-20mm": "num-rounds"})
        self.assertTrue(sd.is_weapon and sd.allows("surface-entity-param") and not sd.allows("human-param"))
        self.assertEqual(sd.fixed_codes, [])

    def test_fixed_codes_and_variable_category(self):
        self.assertEqual(self.cat.get("weapons/fixed-gun.sysdef").fixed_codes, [4096, 4416])
        self.assertEqual(self.cat.get("weapons/var-cat.sysdef").art_params, {"gun-art-part-type": "gun"})
        self.assertTrue(self.cat.get("weapons/var-cat.sysdef").allows("anything-param"))   # "all"

    def test_accepts_follows_stock_bindings(self):
        self.assertTrue(self.cat.accepts("gun-art-part-type", 8192))
        self.assertTrue(self.cat.accepts("elevating-turret-art-part-type", 4736))   # stock: launcher codes too
        self.assertFalse(self.cat.accepts("turret-art-part-type", 4416))
        self.assertTrue(self.cat.accepts("new-art-part-type", 4736, "launcher"))     # not in stock: by category
        self.assertFalse(self.cat.accepts("new-art-part-type", 4096, "launcher"))

    def test_family(self):
        self.assertEqual([systems.family(c) for c in (4096, 5728, 4448, 6016, 4768, 6336, 5376, 8192)],
                         ["turret", "turret", "gun", "gun", "launcher", "launcher", "5376", "8192"])

    def test_catalog_json_round_trip(self):
        again = systems.Catalog.from_json(self.cat.to_json())
        self.assertEqual(again.get("weapons/test-gun.sysdef").art_params, self.cat.get("weapons/test-gun.sysdef").art_params)
        self.assertEqual(again.bindings, self.cat.bindings)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cat = catalog(write_systems(Path(self.tmp.name)))
        self.so = ET.fromstring(DONOR).find("simObject")
        self.rows = parts_of(("Mast__Radar", 5376, None), ("Weapons__Phalanx_1", 4128, None),
                             ("Weapons__Phalanx_1_Gun", 4448, "Weapons__Phalanx_1"))
        self.glb = {"nodes": NODES}

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, entries):
        return systems.apply_systems(self.so, {"systems": entries}, as_parts(self.rows), self.glb, self.cat)

    def test_no_systems_changes_nothing(self):
        before = ET.tostring(self.so)
        self.assertEqual(systems.apply_systems(self.so, {}, [], self.glb, self.cat), [])
        self.assertEqual(ET.tostring(self.so), before)

    def test_position_bind_remove_add(self):
        f = self.apply([
            {"system": "sensor", "position_from": "Mast__Radar"},
            {"system": "weapon", "bind": {"turret-art-part-type": "Weapons__Phalanx_1", "gun-art-part-type": 4448}},
            {"system": "damage", "remove": True, "reason": "test"},
            {"system": "sensor-2", "sysdef": "sensors/visual-sensor.sysdef", "position_from": "socket_bridge_eye",
             "params": {"max-range": 4000}},
        ])
        self.assertEqual([x.code for x in f], ["system-changed", "system-changed", "system-removed", "system-added"])
        pos = self.so.find("componentSystem[@systemName='sensor']/bodyPosition")
        self.assertEqual((pos.get("forward"), pos.get("right"), pos.get("down")), ("10", "0", "-25"))   # body frame
        w = self.so.find("componentSystem[@systemName='weapon']")
        self.assertEqual(w.find("int[@paramName='turret-art-part-type']").text, "4128")
        self.assertEqual(w.find("int[@paramName='gun-art-part-type']").text, "4448")
        self.assertIsNone(self.so.find("componentSystem[@systemName='damage']"))
        new = self.so.find("componentSystem[@systemName='sensor-2']")
        self.assertEqual(new.get("platform"), "@(system-dir)\\sensors\\visual-sensor.sysdef")
        self.assertEqual([k.get("paramName") for k in new], ["max-range", "sensor-position"])   # sorted, as the SOE writes
        self.assertEqual(new.find("real[@paramName='max-range']").text, "4000")
        names = [cs.get("systemName") for cs in self.so.findall("componentSystem")]
        self.assertEqual(names, ["sensor", "weapon", "sensor-2"])    # added after the donor's systems

    def test_errors(self):
        codes = [x.code for x in self.apply([
            {"system": "nope"}, {"system": "weapon", "bind": {"gun-art-part-type": "NoSuchNode"}},
            {"system": "sensor", "position_from": "NoSuchNode"}])]
        self.assertEqual(codes, ["system-unknown", "system-bind-unknown", "system-position-unknown"])


class TestChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cat = catalog(write_systems(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, xml_text, parts={4096, 4416}, repo="surface-entity-param"):
        so = ET.fromstring(xml_text).find("simObject")
        return {x.code for x in systems.check_entity_systems(so, self.cat, repo, parts)}

    def test_donor_is_clean(self):
        self.assertEqual(self.codes(DONOR), {"system-sysdef-missing"})     # damage isn't in the stand-in catalog

    def test_problems(self):
        bad = DONOR.replace('systemName="damage" platform="@(system-dir)\\damage\\surface-default.sysdef"',
                            'systemName="2nd" platform="@(system-dir)\\weapons\\fixed-gun.sysdef"') \
                   .replace('forward="0" right="0" down="0"', 'forward="0" right="0" down="-200"') \
                   .replace('systemName="sensor" platform', 'systemName="weapon" platform')
        c = self.codes(bad, parts={4416})
        self.assertTrue({"system-name-digit", "system-name-duplicate", "system-not-allowed", "entity-system-artpart",
                         "system-position-outside"} <= c, c)

    def test_weapon_without_sensor(self):
        no_sensor = DONOR.replace(r"sensors\active-radar-sensor.sysdef", r"other\x.sysdef")
        self.assertIn("system-weapon-no-sensor", self.codes(no_sensor))

    def test_category_mismatch(self):
        swapped = DONOR.replace(">4416<", ">4097<")
        self.assertIn("system-artpart-category", self.codes(swapped, parts={4096, 4097}))

    def test_two_movement_systems(self):
        two = DONOR.replace('<componentSystem systemName="damage"',
                            '<componentSystem systemName="m1" platform="@(system-dir)\\movement\\a.sysdef"/>'
                            '<componentSystem systemName="m2" platform="@(system-dir)\\movement\\b.sysdef"/>'
                            '<componentSystem systemName="damage"')
        self.assertIn("system-movement-count", self.codes(two))

    def test_ammunition(self):
        so = ET.fromstring(DONOR).find("simObject")
        self.assertEqual(systems.check_ammunition(so, self.cat, {"TEST-20mm"}), [])
        self.assertEqual([x.code for x in systems.check_ammunition(so, self.cat, {"OTHER"})],
                         ["system-ammunition-unknown"])

    def test_ammunition_names(self):
        self.assertEqual(systems.ammunition_names('(ammunition-params\n (A-1 (warhead -1)) ; (Nope)\n (B (x "(C)")))'),
                         {"A-1", "B"})


class TestDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cat = catalog(write_systems(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, rows):
        return systems.draft_systems(DONOR, rows, NODES, self.cat, "surface-entity-param", "1:3")

    def test_sensor_to_the_highest_radar_and_weapon_rebound(self):
        rows = parts_of(("Low__Radar", 5408, None), ("Mast__Radar", 5376, None),
                        ("Weapons__Phalanx_1", 4128, None), ("Weapons__Phalanx_1_Gun", 4448, "Weapons__Phalanx_1"))
        entries, notes = self.draft(rows)
        by = {e["system"]: e for e in entries}
        self.assertEqual(by["sensor"]["position_from"], "Mast__Radar")
        self.assertEqual(by["weapon"]["bind"], {"turret-art-part-type": "Weapons__Phalanx_1",
                                                "gun-art-part-type": "Weapons__Phalanx_1_Gun"})
        self.assertEqual(len(entries), 2, entries)            # the mount is taken: no extra weapon added
        self.assertEqual(notes, [])

    def test_donor_weapon_kept_when_the_model_has_its_parts(self):
        entries, _ = self.draft(parts_of(("T", 4096, None), ("G", 4416, "T")))
        self.assertFalse(any(e["system"] == "weapon" for e in entries))

    def test_donor_weapon_removed_without_a_mount(self):
        entries, notes = self.draft([])
        self.assertTrue(next(e for e in entries if e["system"] == "weapon")["remove"])
        self.assertEqual(notes, ["sensor (active-radar-sensor): the model has no location for it; the donor's "
                                 "sensor-position is kept"])

    def test_weapon_added_for_a_named_mount(self):
        donor = DONOR.replace(r"weapons\test-gun.sysdef", r"other\x.sysdef")   # the donor is unarmed
        rows = parts_of(("Mast__Radar", 5376, None), ("Weapons__Phalanx_1", 4128, None),
                        ("Weapons__Phalanx_1_Gun", 4448, "Weapons__Phalanx_1"))
        self.cat.sysdefs["weapons/test-gun.sysdef"].name = "Phalanx CIWS"
        entries, notes = systems.draft_systems(donor, rows, NODES, self.cat,
                                               "surface-entity-param", "1:3")
        added = [e for e in entries if e.get("sysdef") == "weapons/test-gun.sysdef"]
        self.assertEqual(len(added), 1, entries)
        self.assertEqual(added[0]["system"], "weapon-1")
        self.assertEqual(added[0]["params"], {"num-rounds": 1500})       # stock median
        self.assertEqual(added[0]["bind"]["gun-art-part-type"], "Weapons__Phalanx_1_Gun")

    def test_turret_only_mount_is_explained(self):
        self.cat.sysdefs["weapons/test-gun.sysdef"].name = "Phalanx CIWS"
        donor = DONOR.replace("test-gun.sysdef", "var-cat.sysdef").replace(">4416<", ">9999<")
        entries, notes = systems.draft_systems(donor, parts_of(("Weapons__Phalanx_1", 4128, None)), NODES, self.cat,
                                               "surface-entity-param", "1:3")
        self.assertFalse(any(e.get("sysdef") for e in entries))
        self.assertTrue(any("needs turret + gun parts" in n for n in notes), notes)

    def test_sensor_added_for_weapons_when_the_donor_has_none(self):
        donor = DONOR.replace('systemName="sensor" platform="@(system-dir)\\sensors\\active-radar-sensor.sysdef"',
                              'systemName="other" platform="@(system-dir)\\other\\x.sysdef"')
        entries, _ = systems.draft_systems(donor, parts_of(("Mast__Radar", 5376, None), ("T", 4096, None),
                                                           ("G", 4416, "T")), NODES, self.cat, "surface-entity-param", "1:3")
        s = next(e for e in entries if e.get("sysdef", "").startswith("sensors/"))
        self.assertEqual((s["system"], s["sysdef"], s["position_from"]),
                         ("sensor", "sensors/active-radar-sensor.sysdef", "Mast__Radar"))


class TestManifestShape(unittest.TestCase):
    def test_problems(self):
        self.assertEqual(system_problems(None), [])
        self.assertEqual(system_problems([{"system": "sensor", "position_from": "x"}]), [])
        p = system_problems([{"system": "a"}, {"system": "a"}, {"nope": 1}, {"system": "1x", "bind": [],
                             "sysdef": "x.txt", "colour": "red"}])
        self.assertEqual(len(p), 6, p)
        self.assertEqual(system_problems({"a": 1}), ["systems must be a list of {system, ...} entries"])


@unittest.skipUnless(HAVE_INSTALL, "VR-Forces simulation model set not present")
class TestInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = systems.build_catalog(CFG)

    def test_phalanx_binds_turret_and_gun_through_its_template(self):
        sd = self.cat.get("weapons/phalanx-20mm-gun.sysdef")
        self.assertEqual(sd.art_params, {"turret-art-part-type": "turret", "gun-art-part-type": "gun"})
        self.assertIn("surface-entity-param", sd.repositories)
        self.assertIn("PGU-28B-20mm", sd.ammunition)

    def test_m1a2_multigun_has_fixed_codes(self):
        self.assertTrue({4096, 4416} <= set(self.cat.get("weapons/m1a2-multigun.sysdef").fixed_codes))

    def test_platform_repositories(self):
        self.assertEqual(self.cat.repositories["Surface_Ship.ope"], "surface-entity-param")
        self.assertEqual(self.cat.repositories["Fixed_Wing_Aircraft.ope"], "fixed-wing-entity-param")

    def test_stock_bindings(self):
        self.assertEqual(self.cat.bindings["turret-art-part-type"], ["turret"])
        self.assertTrue({"gun", "8192"} <= set(self.cat.bindings["gun-art-part-type"]))
        self.assertTrue({"turret", "gun", "launcher"} <= set(self.cat.bindings["elevating-turret-art-part-type"]))

    def test_every_stock_sensor_with_a_position_reads(self):
        sensors = [sd for sd in self.cat.sysdefs.values() if sd.folder == "sensors"]
        self.assertGreaterEqual(len(sensors), 15)
        self.assertIn("sensor-position", self.cat.get("sensors/visual-sensor.sysdef").params)


if __name__ == "__main__":
    unittest.main()
