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
import copy
import csv
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from string import Template

from . import artparts, inspect_glb, leaf, mtl, profiles
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
    """Every stock surface-ship model definition (114 of 114) uses Watercraft; all other models use Entity
    (profiles.py)."""
    return profiles.profile_for(manifest).schema


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


def _set_bool(so: ET.Element, name: str, value: bool) -> None:
    el = so.find(f"bool[@paramName='{name}']")
    if el is None:                                   # keep it with the other bools, as the editor writes them
        bools = so.findall("bool")
        el = ET.Element("bool", paramName=name)
        so.insert(list(so).index(bools[-1]) + 1 if bools else len(so), el)
    el.text = "True" if value else "False"


def build_character_entity(donor_xml: str, manifest: dict, table: dict[str, dict]) -> tuple[ET.ElementTree, list[Finding]]:
    """A human or animal: the donor lifeform with a DI-Guy character chosen (Adding Content manual 25.2;
    users guide 66.1.10). Size, supports, systems and articulated parts stay the donor's."""
    f: list[Finding] = []
    root = ET.fromstring(donor_xml)
    so = root.find("simObject")
    if so is None:
        raise ValueError("donor .entity has no <simObject>")
    ot = object_type(manifest)
    so.set("objectType", ot)
    so.set("matchType", ot)

    def set_str(name, value):
        el = so.find(f"string[@paramName='{name}']")
        if el is None:
            el = ET.SubElement(so, "string", paramName=name)
        el.text = value
    character = manifest["character_type"]
    info = table.get(character)
    if table and info is None:
        f.append(Finding("error", "character-unknown", f"'{character}' is not in character_animations_table.mtl; "
                         "list the choices with: python -m pipeline.mtl characters"))
    elif not table:
        f.append(Finding("warning", "character-unchecked", "character_animations_table.mtl not found; "
                         f"'{character}' not checked"))
    donor_character = so.findtext("string[@paramName='character-type']")
    set_str("character-type", character)
    if manifest.get("character_appearance"):
        set_str("character-appearance", manifest["character_appearance"])
    elif donor_character != character:
        set_str("character-appearance", "random")       # the donor's appearance belongs to the donor's character
        set_str("head", "default")
    animation = manifest.get("animation")
    if animation:
        # the table lists what can be tasked; stock entities also use built-in ones (e.g. soldier_17 "stand_ready")
        if info and animation not in info["animations"]:
            f.append(Finding("warning", "animation-unlisted", f"'{animation}' is not in the table's list for "
                             f"'{character}'; check it in the Simulation Object Editor"))
        set_str("animation", animation)
    elif donor_character != character:
        f.append(Finding("warning", "animation-donor", "the default animation is the donor's and the character "
                         "changed; set 'animation' in the manifest or in the Simulation Object Editor"))
    _set_bool(so, "di-guy-enabled", True)
    set_str("gui-label", manifest["display_name"])
    set_str("gui-unique-id", f"ModelSet-vrfpipeline-{manifest['name']}")
    set_str("short-name", short_name(manifest))
    set_str("gui-description", f"Created by vrf-pipeline: DI-Guy character {character}")
    f.append(Finding("info", "character", f"DI-Guy character '{character}'"
                     + (f" ({info['display_name']})" if info else "") + "; no model file is imported for a lifeform"))
    ET.indent(root, space="   ")
    return ET.ElementTree(root), f


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
    profile = profiles.profile_for(manifest)
    if profile.supports == "ground":
        gs = glb.get("ground_supports")     # from the lowest geometry (glb.ground_supports); else from bounds
        if gs:
            left, right, other = gs["left"], gs["right"], gs["other"]
        for name, model_p in (("left-support", left), ("right-support", right), ("other-support", other)):
            el = so.find(f"bodyPosition[@paramName='{name}']")
            if el is not None:   # model_p is already (forward, right, down)
                for k, v in zip(("forward", "right", "down"), model_p):
                    el.set(k, _fmt(v))
        f.append(Finding("info", "supports-estimated", "support points estimated from "
                         + ("the lowest geometry" if gs else "bounds") + "; refine with "
                         "'Calculate Support Points' in the Simulation Object Editor (users guide 66.1.3)"))
    elif profile.supports == "waterline" and glb.get("waterline_supports"):
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
    for name, value in profile.entity_flags.items():      # e.g. cultural features are gravity aligned (UG 66.1.8)
        _set_bool(so, name, value)
    geo = so.find("string[@paramName='object-geometry-filename']")
    if manifest.get("walkable"):
        # Our own OpenFlight model as object geometry, embedded in the terrain, so characters can walk in and
        # around it (users guide 66.1.6-66.1.7). The model path uses the .entity form of the variable.
        if geo is None:
            geo = ET.SubElement(so, "string", paramName="object-geometry-filename")
        geo.text = model_path.replace("$(SHARED_DATA_DIR)", "@(SHARED_DATA_DIR)")
        _set_bool(so, "use-object-geometry", True)
        _set_bool(so, "embed-into-terrain", True)
        nav = so.find("string[@paramName='nav-data-path']")
        if nav is not None:
            nav.text = None      # the donor's navigation data belongs to the donor's geometry
        f.append(Finding("info", "object-geometry", "the model is its own object geometry and embeds into the "
                         "terrain; navigation data is generated when it is placed in a scenario (users guide 66.1.7)"))
    elif geo is not None and geo.text:
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
    profile = profiles.profile_for(manifest)
    character = profile.route == "diguy"      # humans and animals: no model file, the donor's DI-Guy visuals

    if character:
        lay.ommx.unlink(missing_ok=True)
    else:
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
            if character:                 # keep the donor's DI-Guy model definition; only the name changes
                new = copy.deepcopy(item)
                new.find("myName").text = element_def_name(manifest)
                new = leaf.normalise_item(new)
            else:
                new = leaf.normalise_item(leaf.retarget(item, element_def_name(manifest), model_def_name(manifest),
                                                        model_schema(manifest), xr=True))
            lay.leaf.write_text(leaf.leaf_text(new), encoding="utf-8")
            out.append(Finding("info", "wrote-leaf", f"{lay.leaf} (from donor element '{donor_element}' in {src.name}; "
                                                     f"parent '{new.findtext('myParentName')}')"))
        except leaf.LeafError as e:
            if character:
                # Stock animals have no element definition of their own: VR-Forces shows a DI-Guy lifeform from
                # the character named in its .entity (Adding Content manual 25.2.1, 25.2.3).
                lay.metx.unlink(missing_ok=True)
                lay.leaf.unlink(missing_ok=True)
                out.append(Finding("info", "no-element-definition", "the donor has no element definition of its "
                                   "own, so none is written; the character comes from the .entity"))
            else:
                out.append(Finding("error", "leaf-failed", str(e)))

    if lay.visual_import.exists():
        shutil.rmtree(lay.visual_import)
    lay.visual_import.mkdir(parents=True)
    for p in (lay.leaf, lay.metx, lay.ommx):
        if p.exists():
            shutil.copy2(p, lay.visual_import / p.name)

    if glb is None and not character:
        out.append(Finding("pending", "entity-not-generated", "no glb inspection report given"))
    elif donor_xml is not None:
        if character:
            tree, notes = build_character_entity(donor_xml, manifest, mtl.load_characters(cfg))
        else:
            tree, notes = build_entity(donor_xml, manifest, parts, glb, lay.model_shared_path())
        target = lay.entity(manifest["display_name"])
        target.parent.mkdir(parents=True, exist_ok=True)
        tree.write(target, encoding="utf-8", xml_declaration=True)
        out.append(Finding("info", "wrote-entity", f"{target} (donor: {donor})"))
        out += notes
        if profile.kind == "munition":
            out += write_munition_tables(manifest, lay, cfg, donor_xml)
    return out


def write_munition_tables(manifest: dict, lay: Layout, cfg: dict, donor_xml: str) -> list[Finding]:
    """What makes a munition usable (users guide 66.5, 73, 74.2.2): an ammunitionParams.mtl entry, so weapon
    systems can list it, and detonation power. The entry is written as an additions file; merging it into a
    custom simulation model set is a deploy step. Other entities are never edited here."""
    f: list[Finding] = []
    dis7 = manifest["dis_type"]
    if "-1" in dis7.split(":"):
        f.append(Finding("error", "munition-type-wildcard", f"munition type {dis7} must not contain -1 "
                         "(it is used in fire and detonation interactions; users guide 73)"))
        return f
    sms = Path(cfg["sms_dir"]) / cfg.get("donor_sms", "EntityLevel")
    ammo_path = sms / "ammunitionParams.mtl"
    ammo = ammo_path.read_text(encoding="utf-8", errors="replace") if ammo_path.exists() else ""
    donor_type = re.search(r'objectType="1:([^"]+)"', donor_xml)
    donor_entry = mtl.find_ammunition(ammo, donor_type.group(1)) if ammo and donor_type else None
    if ammo and mtl.find_ammunition(ammo, dis7):
        f.append(Finding("error", "munition-type-collision", f"munition type {dis7} is already in {ammo_path.name}"))
        return f
    tags = re.findall(r'"([^"]+)"', re.search(r'paramName="gui-categories">([^<]*)', donor_xml).group(1)) \
        if 'paramName="gui-categories"' in donor_xml else ["3D Model", "Entity", "Munition"]
    entry = mtl.ammunition_entry(manifest["name"], dis7, manifest["display_name"],
                                 donor_entry.text(ammo) if donor_entry else None, tags)
    lay.ammunition_additions.parent.mkdir(parents=True, exist_ok=True)
    lay.ammunition_additions.write_text(entry, encoding="utf-8")
    f.append(Finding("info", "wrote-ammunition-entry", f"{lay.ammunition_additions} ("
                     + (f"from the donor's entry '{donor_entry.name}'" if donor_entry else "plain unguided entry: "
                        "the donor has none") + ")"))
    f.append(Finding("pending", "ammunition-merge", f"merge {lay.ammunition_additions.name} into the custom "
                     "simulation model set's ammunitionParams.mtl at deploy, or no weapon can list this munition"))
    det = "".join((p.read_text(encoding="utf-8", errors="replace") if p.exists() else "")
                  for p in (sms / "detonationParams.mtl", Path(cfg["sms_dir"]) / "base" / "detonationParams.mtl"))
    donor_has = bool(donor_type and mtl.has_detonation_entry(det, donor_type.group(1)))
    f.append(Finding("pending", "detonation-power", "set the detonation power on the munition's Detonation tab in the "
                     "Simulation Object Editor (users guide 74.2.2); "
                     + ("the donor has its own detonation entry to copy from" if donor_has else
                        "the donor relies on a best-match entry, which may not match the new type")))
    f.append(Finding("pending", "weapon-ammunition-list", "add the munition to the ammunition list of each weapon "
                     "system that should fire it (users guide 73); the importer does not edit other entities"))
    return f


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
    glbmod.add_supports(glb, args.model_dir / "model.glb", manifest)
    findings = generate(manifest, layout_for(manifest, fmt=cfg.get("model_format", "flt")), glb, cfg)
    print_findings("generate", findings)
    return 1 if any(x.level == "error" for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
