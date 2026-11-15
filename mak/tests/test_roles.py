"""The articulation roles (catalog/articulation/roles.json, contract §3b) against the MAK pipeline and install.

A role's chain is what the editor asks Blender to build before anything is exported. These tests check that a
chain built to a role is one the MAK export can use whole:
- its example names get the Table 58 codes the role lists (draft_manifest.suggest_code, the export's own rule);
- its motions are what from_package.motion_of reads off the joint axes;
- a weapon role's chain binds its stock sysdef with no sim-only part (systems.assign; Q20 is what happens when
  it does not), and its limits match the sysdef component's (needs the install; skipped without it).
"""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

MAK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MAK))

from from_package import motion_of  # noqa: E402
from pipeline import draft_manifest, profiles, systems  # noqa: E402
from pipeline.common import load_config  # noqa: E402

ROLES = json.loads((MAK.parent / "catalog" / "articulation" / "roles.json").read_text(encoding="utf-8"))["roles"]
CFG = load_config()
HAVE_INSTALL = (Path(CFG["sms_dir"]) / CFG.get("donor_sms", "EntityLevel") / "vrfSim" / "systems").is_dir()


def chain(role: dict) -> list[dict]:
    """The role's example instance as joints: name, parent, motion read back from the axis."""
    out, parent = [], None
    for j in role["joints"]:
        name = f"{role['example']}__{j['suffix']}" if j["suffix"] else role["example"]
        lo, hi = j["limits"]
        motion, _ = motion_of({"axis": j["axis"], "kind": j["kind"], "min_deg": lo, "max_deg": hi})
        out.append({"node": name, "parent": parent, "motion": motion, "role_motion": j["motion"], "limits": (lo, hi)})
        parent = name
    return out


def domain(role: dict) -> str:
    return profiles.PROFILES[role["kinds"][0]].domain


def codes(role: dict) -> list[int | None]:
    used: set[int] = set()
    out = []
    for j in chain(role):
        c = draft_manifest.suggest_code(j["node"], used, None, j["motion"], domain(role))
        if c is not None:
            used.add(c)
        out.append(c)
    return out


class TestRoles(unittest.TestCase):
    def test_every_role_is_well_formed(self):
        ids = [r["id"] for r in ROLES]
        self.assertEqual(len(ids), len(set(ids)))
        for r in ROLES:
            self.assertTrue(set(r["kinds"]) <= set(profiles.PROFILES), r["id"])
            self.assertRegex(r["example"], r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*__[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*$")
            self.assertEqual(r["joints"][0]["suffix"], "", r["id"])
            for j in r["joints"]:
                self.assertIn(j["axis"], ("x", "y", "z"))
                self.assertLessEqual(j["limits"][0], j["limits"][1])

    def test_motions_match_what_the_export_reads_off_the_axis(self):
        for r in ROLES:
            for j in chain(r):
                want = {"spin": ("visual", "azimuth")}.get(j["role_motion"], (j["role_motion"],))
                self.assertIn(j["motion"], want, (r["id"], j))

    def test_example_names_get_the_listed_codes(self):
        for r in ROLES:
            listed = (r.get("evidence") or {}).get("mak", {}).get("codes")
            if listed:
                self.assertEqual(codes(r)[:len(listed)], listed, r["id"])


@unittest.skipUnless(HAVE_INSTALL, "VR-Forces simulation model set not present")
class TestRolesAgainstTheInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = systems.build_catalog(CFG)

    def weapon_roles(self):
        return [r for r in ROLES if (r.get("evidence") or {}).get("mak", {}).get("sysdef")]

    def test_weapon_chains_bind_their_sysdef_whole(self):
        """No sim-only part: every part parameter of the stock weapon binds to a joint of the chain."""
        self.assertGreaterEqual(len(self.weapon_roles()), 4)
        for r in self.weapon_roles():
            sd = self.cat.get(r["evidence"]["mak"]["sysdef"])
            self.assertIsNotNone(sd, r["id"])
            parts = [{"node": j["node"], "parent": j["parent"], "dis_part": c}
                     for j, c in zip(chain(r), codes(r)) if c is not None]
            chains = systems.weapon_chains(parts)
            self.assertEqual(len(chains), 1, (r["id"], parts))
            bound = systems.assign(list(sd.art_params), chains[0], self.cat, sd)
            self.assertIsNotNone(bound, (r["id"], sd.art_params, parts))
            self.assertEqual(set(bound), set(sd.art_params), r["id"])

    def test_weapon_limits_match_the_sysdef_components(self):
        """Each moving joint's default limits are the sysdef component's (within a degree), so the model and the
        simulation agree on how far it turns."""
        for r in self.weapon_roles():
            sd = self.cat.get(r["evidence"]["mak"]["sysdef"])
            limits = {}
            for e in sd.art_entries:
                for motion in ("azimuth", "elevation"):
                    lo, hi = e["limits"].get(f"min-{motion}"), e["limits"].get(f"max-{motion}")
                    if lo is not None and hi is not None and hi > lo:
                        limits.setdefault(motion, []).append((math.degrees(lo), math.degrees(hi)))
            for j in r["joints"]:
                lo, hi = j["limits"]
                if lo == hi or j["motion"] not in limits:
                    continue
                self.assertTrue(any(abs(lo - a) <= 1 and abs(hi - b) <= 1 for a, b in limits[j["motion"]])
                                or (j["motion"] == "azimuth" and lo <= -179 and hi >= 179),
                                (r["id"], j["suffix"], (lo, hi), limits[j["motion"]]))


if __name__ == "__main__":
    unittest.main()
