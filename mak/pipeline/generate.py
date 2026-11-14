"""Generate the VR-Forces files for one model into output/<name>/, laid out like a custom SMS.

Layout (matches what the Simulation Object Editor wrote in the hand-import test):
  sms/gui/visuals/UserData/<name>.ommx   model definition: schema Watercraft (surface) or Entity, filename only
  sms/gui/visuals/Entity/<name>.metx     DIS type -> element definition <name>
  sms/gui/visuals/Entity/<name>.leaf     element definition: the donor's, renamed, 3D/XR model -> <name> (leaf.py)
  sms/vrfSim/<display name>.entity       donor .entity cloned (users guide 65.3.1); type, parts, size, labels replaced
  visual-import/                         the three definition files together, for SOE File > Import Visual Models
  SharedData/ModelData/<category>/<name>/<name>.flt|.fbx   the model (convert stage)
  appData/importConfig/FBX_NodeNameMap.additions.csv       FBX route only
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from string import Template

from . import artparts, inspect_glb, leaf
from . import glb as glbmod
from .artparts import Part
from .common import (TEMPLATES, Finding, InputError, Layout, check_model_dir, layout_for, load_config, load_manifest,
                     print_findings)

INDIVIDUAL_SUPERTYPE = 1   # users guide D.3.1: VR-Forces object super-type, 1 = Individual


def _render(template: str, **values) -> str:
    return Template((TEMPLATES / template).read_text(encoding="utf-8")).substitute(**values)


def model_def_name(manifest: dict) -> str:
    # manual 16.1: prefix model definitions with their category, e.g. "Wheeled..."
    return manifest["name"]


def element_def_name(manifest: dict) -> str:
    # the editor names the element definition after the model (hand import: "HarmonyOfTheSeas")
    return manifest["name"]


def model_schema(manifest: dict) -> str:
    """Every stock surface-ship model definition (114 of 114) uses Watercraft; other vehicles use Entity."""
    return "Watercraft" if manifest["domain"] == "surface" else "Entity"


def short_name(manifest: dict) -> str:
    """Label text. DIS entity markings carry 11 characters, so cut on a word boundary at 11."""
    if manifest.get("short_name"):
        return manifest["short_name"]
    out = ""
    for word in manifest["display_name"].split():
        if len((out + " " + word).strip()) > 11:
            break
        out = (out + " " + word).strip()
    return out or manifest["display_name"][:11]


def object_type(manifest: dict) -> str:
    """8-field .entity objectType = super-type + 7-field DIS type (users guide D.3.1)."""
    return f"{INDIVIDUAL_SUPERTYPE}:{manifest['dis_type']}"


def donor_path(cfg: dict, donor: str) -> Path:
    return Path(cfg["sms_dir"]) / cfg.get("donor_sms", "EntityLevel") / "vrfSim" / f"{donor}.entity"


# ------------------------------------------------------------------ .entity pieces
def _fmt(v: float) -> str:
    v = round(v, 9) + 0.0          # + 0.0 turns -0.0 into 0.0
    return f"{v:.12g}"             # stock files carry ~13 significant digits (e.g. 3.14159265359)


def _body(parent: ET.Element, name: str, p) -> None:
    f, r, d = p
    ET.SubElement(parent, "bodyPosition", paramName=name, forward=_fmt(f), right=_fmt(r), down=_fmt(d))


def _real(parent, name, v):
    ET.SubElement(parent, "real", paramName=name).text = _fmt(v)


def art_part_element(part: Part, parts: list[Part], glb: dict) -> ET.Element | None:
    """One DtSimArtPartDescriptor, in the shapes used by stock .entity files."""
    if part.motion == "visual":
        return None
    by_node = {p.node: p for p in parts}
    nodes = glb["nodes"]
    pivot = nodes[part.node]["pivot"]
    parent = by_node.get(part.parent) if part.parent else None
    # attach-point is relative to the parent part's attach point (as in stock files, e.g. 2S1 4128 -> 4416)
    origin = nodes[parent.node]["pivot"] if parent else [0.0, 0.0, 0.0]
    rel = [pivot[i] - origin[i] for i in range(3)]
    end = (0.0, 0.0, 0.0)
    if artparts.is_weapon(part.code) and nodes[part.node]["subtree_bounds"]:
        end = (nodes[part.node]["subtree_bounds"]["max"][1] - pivot[1], 0.0, 0.0)   # muzzle: furthest forward point

    family = {4096: "primary-turret", 4416: "primary-gun", 4736: "primary-launcher"}
    stem = next((s for b, s in family.items() if b <= part.code < b + 320), "art-part")
    el = ET.Element("DtSimArtPartDescriptor", paramName=f"{stem}-{part.code}")
    plist = ET.SubElement(el, "DtRwList", paramName="art-part-param-list")
    for v in artparts.MOTION_PARAMS[part.motion]:
        ET.SubElement(plist, "int", paramName="param-type").text = str(v)
    _body(el, "attach-point", artparts.model_to_body(rel))
    _body(el, "end-point", end)
    ET.SubElement(el, "int", paramName="art-part-type").text = str(part.code)
    ET.SubElement(el, "int", paramName="parent-part-type").text = str(parent.code if parent else -1)

    lo, hi, rate = (artparts.to_radians(part.motion, v) for v in (part.min, part.max, part.max_rate))
    m = part.motion
    if m in ("azimuth", "elevation"):
        _real(el, m, 0)
        if m == "azimuth":
            _real(el, "azimuth-neutral-offset", 0)
        _real(el, f"{m}-rate", 0)
        _real(el, f"max-{m}", hi)
        _real(el, f"max-{m}-rate", rate)
        _real(el, f"min-{m}", lo)
    elif m == "rotation":
        _real(el, "max-rotation", hi)
        _real(el, "max-rotation-rate", rate)
        _real(el, "min-rotation", lo)
        _real(el, "position", 0)
        _real(el, "rotation-rate", 0)
    elif m == "extension":
        _real(el, "extension", 0)
        _real(el, "extension-rate", 0)
        _real(el, "max-extension", hi)
        _real(el, "max-extension-rate", rate)
    else:  # translate_x / _y / _z: vectors in model axes, as in stock periscopes
        axis = m[-1]

        def vec(name, v):
            vals = {"x": 0.0, "y": 0.0, "z": 0.0, axis: v}
            ET.SubElement(el, "vector", paramName=name, **{k: _fmt(x) for k, x in vals.items()})
        ET.SubElement(el, "string", paramName="comment")
        vec("max-translation", hi)
        vec("max-translation-rate", rate)
        vec("min-translation", lo)
        vec("translation", lo)
        vec("translation-rate", 0)
        return el
    ET.SubElement(el, "string", paramName="comment")
    return el


def supports_and_volume(bounds: dict) -> tuple[dict, tuple, tuple]:
    """Bounding volume and three ground-contact supports from model bounds (users guide 66.1.1, 66.1.3).
    Supports follow the stock pattern (front-left, front-right, rear-centre); the SOE can recompute them."""
    mn, mx, ext = bounds["min"], bounds["max"], bounds["extent"]
    cx, cy, cz = [(mn[i] + mx[i]) / 2 for i in range(3)]
    volume = {"length": ext[1], "width": ext[0], "height": ext[2],
              "offset": artparts.model_to_body((cx, cy, cz))}
    ground = -mn[2]
    front, rear, side = cy + 0.3 * ext[1], cy - 0.35 * ext[1], 0.48 * ext[0]
    left = (front, cx - side, ground)
    right = (front, cx + side, ground)
    other = (rear, cx, ground)
    return volume, (left, right), other


def build_entity(donor_xml: str, manifest: dict, parts: list[Part], glb: dict, model_path: str) -> tuple[ET.ElementTree, list[Finding]]:
    f: list[Finding] = []
    root = ET.fromstring(donor_xml)
    so = root.find("simObject")
    if so is None:
        raise ValueError("donor .entity has no <simObject>")
    ot = object_type(manifest)
    so.set("objectType", ot)
    so.set("matchType", ot)

    # articulated parts
    old = so.find("DtSimArtPartDescriptorList")
    idx = list(so).index(old) if old is not None else 0
    if old is not None:
        so.remove(old)
    lst = ET.Element("DtSimArtPartDescriptorList", paramName="articulated-parts")
    for p in parts:
        el = art_part_element(p, parts, glb)
        if el is not None:
            lst.append(el)
    so.insert(idx, lst)

    # size and supports
    volume, (left, right), other = supports_and_volume(glb["bounds_mak_frame"])
    bv = so.find("boundingVolume")
    if bv is not None:
        bv.set("offsetType", "custom")
        dims, off = bv.find("dimensions"), bv.find("offset")
        if dims is not None:
            for k in ("length", "width", "height"):
                dims.set(k, _fmt(volume[k]))
        if off is not None:
            for k, v in zip(("forward", "right", "down"), volume["offset"]):
                off.set(k, _fmt(v))
    if manifest["domain"] == "land":
        for name, model_p in (("left-support", left), ("right-support", right), ("other-support", other)):
            el = so.find(f"bodyPosition[@paramName='{name}']")
            if el is not None:   # model_p is already (forward, right, down)
                for k, v in zip(("forward", "right", "down"), model_p):
                    el.set(k, _fmt(v))
        f.append(Finding("info", "supports-estimated", "support points estimated from bounds; refine with "
                         "'Calculate Support Points' in the Simulation Object Editor (users guide 66.1.3)"))
    elif manifest["domain"] == "surface" and glb.get("waterline_supports"):
        ws = glb["waterline_supports"]   # already body frame (forward, right, down), down = 0 at the waterline
        for name, key in (("left-support", "left"), ("right-support", "right"), ("other-support", "other")):
            el = so.find(f"bodyPosition[@paramName='{name}']")
            if el is not None:
                for k, v in zip(("forward", "right", "down"), ws[key]):
                    el.set(k, _fmt(v))
        f.append(Finding("info", "supports-waterline",
                         f"support points on the waterline from the hull ({ws['waterline_length']} m x "
                         f"{ws['waterline_beam']} m), as on stock ships; not the donor's"))
    else:
        f.append(Finding("warning", "supports-donor", "support points kept from the donor entity; "
                         "set them in the Simulation Object Editor (users guide 66.1.3)"))

    # labels and references back to the donor
    def set_str(name, value):
        el = so.find(f"string[@paramName='{name}']")
        if el is not None:
            el.text = value
    set_str("gui-label", manifest["display_name"])
    set_str("gui-unique-id", f"ModelSet-vrfpipeline-{manifest['name']}")
    set_str("short-name", short_name(manifest))
    set_str("gui-description", f"Imported by vrf-pipeline from {model_path}")
    geo = so.find("string[@paramName='object-geometry-filename']")
    if geo is not None and geo.text:
        geo.text = None   # object geometry must be OpenFlight/MEDF/GDB (users guide 66.1.6); don't inherit the donor's
        use = so.find("bool[@paramName='use-object-geometry']")
        if use is not None:
            use.text = "False"   # the donor's flag would point at geometry we just removed
        nav = so.find("string[@paramName='nav-data-path']")
        if nav is not None and nav.text:
            nav.text = None      # navigation data is generated from the donor's object geometry (users guide 62.4)

    ET.indent(root, space="   ")
    return ET.ElementTree(root), f


def write_node_map_additions(path: Path, parts: list[Part]) -> list[list[str]]:
    rows = artparts.map_additions(parts)
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([["Node", "Comment", "Type", "Description"], *rows])
    elif path.exists():
        path.unlink()
    return rows


# ------------------------------------------------------------------ main entry
def generate(manifest: dict, lay: Layout, glb: dict | None = None, cfg: dict | None = None,
             node_map: artparts.NodeNameMap | None = None) -> list[Finding]:
    out: list[Finding] = []
    cfg = cfg or load_config()
    if lay.fmt != "fbx":
        node_map = artparts.NodeNameMap()   # OpenFlight carries @dis comments itself; node names are not mapped
    elif node_map is None:
        node_map = artparts.load_node_map(cfg.get("fbx_node_name_map"))
    parts = artparts.resolve_parts(manifest, node_map)
    stale = lay.shared / "ModelData" / "Configuration"        # definitions location used before the SMS layout
    if stale.exists():
        shutil.rmtree(stale)
    lay.definitions.mkdir(parents=True, exist_ok=True)
    lay.ommx.parent.mkdir(parents=True, exist_ok=True)

    lay.ommx.write_text(_render("model_definition.ommx.tmpl",
                                model_def_name=model_def_name(manifest), schema=model_schema(manifest),
                                filename=lay.model_shared_path()), encoding="utf-8")
    out.append(Finding("info", "wrote-ommx", f"{lay.ommx} (schema {model_schema(manifest)})"))

    lay.metx.write_text(_render("entity_type_map.metx.tmpl",
                                dis_type=manifest["dis_type"],
                                element_def_name=element_def_name(manifest)), encoding="utf-8")
    out.append(Finding("info", "wrote-metx", str(lay.metx)))

    rows = write_node_map_additions(lay.node_map_additions, parts if lay.fmt == "fbx" else [])
    if rows:
        out.append(Finding("pending", "node-map-additions",
                           f"{len(rows)} part(s) have no name in FBX_NodeNameMap.csv; append {lay.node_map_additions.name} "
                           "to the installed map at deploy, or VR-Forces will not articulate them visually"))

    donor = manifest.get("donor_entity")
    dpath = donor_path(cfg, donor) if donor else None
    donor_xml = dpath.read_text(encoding="utf-8") if dpath and dpath.exists() else None
    if donor_xml is None:
        out.append(Finding("error", "donor-missing", f"donor .entity not found: {dpath}"))

    # element definition: the donor's, renamed and pointed at our model (leaf.py)
    if donor_xml is not None:
        ot = re.search(r'objectType="([^"]+)"', donor_xml)
        try:
            item, donor_element, src = leaf.donor_element(Path(cfg["definitions_dir"]), ot.group(1) if ot else "")
            new = leaf.normalise_item(leaf.retarget(item, element_def_name(manifest), model_def_name(manifest),
                                                    model_schema(manifest), xr=True))
            lay.leaf.write_text(leaf.leaf_text(new), encoding="utf-8")
            out.append(Finding("info", "wrote-leaf", f"{lay.leaf} (from donor element '{donor_element}' in {src.name}; "
                                                     f"parent '{new.findtext('myParentName')}')"))
        except leaf.LeafError as e:
            out.append(Finding("error", "leaf-failed", str(e)))

    if lay.visual_import.exists():
        shutil.rmtree(lay.visual_import)
    lay.visual_import.mkdir(parents=True)
    for p in (lay.leaf, lay.metx, lay.ommx):
        if p.exists():
            shutil.copy2(p, lay.visual_import / p.name)

    if glb is None:
        out.append(Finding("pending", "entity-not-generated", "no glb inspection report given"))
    elif donor_xml is not None:
        tree, notes = build_entity(donor_xml, manifest, parts, glb, lay.model_shared_path())
        target = lay.entity(manifest["display_name"])
        target.parent.mkdir(parents=True, exist_ok=True)
        tree.write(target, encoding="utf-8", xml_declaration=True)
        out.append(Finding("info", "wrote-entity", f"{target} (donor: {donor})"))
        out += notes
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model_dir", type=Path)
    args = ap.parse_args(argv)
    try:
        manifest = load_manifest(check_model_dir(args.model_dir))
    except InputError as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2
    cfg = load_config()
    glb = inspect_glb.analyse(args.model_dir / "model.glb", manifest["forward_axis_in_blender"])
    if manifest["domain"] == "surface":
        glb["waterline_supports"] = glbmod.waterline_supports(
            glbmod.load(args.model_dir / "model.glb", manifest["forward_axis_in_blender"]))
    findings = generate(manifest, layout_for(manifest, fmt=cfg.get("model_format", "flt")), glb, cfg)
    print_findings("generate", findings)
    return 1 if any(x.level == "error" for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
