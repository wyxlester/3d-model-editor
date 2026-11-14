"""Static validation of output/<name>/ against itself and the read-only VR-Forces install.

Checks:
  - every generated XML file parses and has no unfilled ${placeholders}
  - .ommx `filename` resolves to a file inside output/<name>/SharedData
  - .metx DIS type is 7 integers and is not already mapped in the install (collision = error)
  - model definition name does not collide with one in the install
  - the element definition named in .metx exists (in output .leaf, or pending)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from .common import DIS_TYPE_RE, Finding, has_errors, load_config, print_findings

PLACEHOLDER = re.compile(r"\$\{[^}]+\}")


def _parse(path: Path, findings: list[Finding]):
    text = path.read_text(encoding="utf-8")
    if PLACEHOLDER.search(text):
        findings.append(Finding("error", "unfilled-placeholder", f"{path.name}: {PLACEHOLDER.search(text).group(0)}"))
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        findings.append(Finding("error", "xml-parse", f"{path.name}: {e}"))
        return None


def metx_mappings(root) -> list[tuple[str, str]]:
    return [(i.findtext("first"), i.findtext("second")) for i in root.iter("item") if i.find("first") is not None]


def ommx_names(root) -> list[str]:
    return [i.findtext("Name") for i in root.iter("second") if i.find("Name") is not None]


def ommx_params(root) -> dict[str, str]:
    return {i.findtext("first"): i.findtext("second") for i in root.iter("item")
            if i.find("first") is not None and i.find("second") is not None and len(i.find("second")) == 0}


@lru_cache(maxsize=None)
def install_index(definitions_dir: str) -> dict:
    """DIS types and model definition names already present in the install."""
    types: dict[str, str] = {}
    models: set[str] = set()
    base = Path(definitions_dir)
    if not base.exists():
        return {"available": False, "types": types, "models": models}
    for p in base.rglob("*.metx"):
        try:
            for k, v in metx_mappings(ET.parse(p).getroot()):
                types[k] = f"{v} ({p.name})"
        except ET.ParseError:
            pass
    for p in base.rglob("*.ommx"):
        try:
            models.update(n for n in ommx_names(ET.parse(p).getroot()) if n)
        except ET.ParseError:
            pass
    return {"available": True, "types": types, "models": models}


def validate(out_dir: Path, cfg: dict) -> list[Finding]:
    f: list[Finding] = []
    visuals = out_dir / "sms" / "gui" / "visuals"          # SOE layout: Entity/*.leaf+.metx, UserData/*.ommx
    defs = visuals / "Entity"
    shared = out_dir / "SharedData"
    if not defs.exists():
        return [Finding("error", "no-output", f"{defs} does not exist; run generate first")]
    model_defs: set[str] = set()

    idx = install_index(cfg["definitions_dir"])
    if not idx["available"]:
        f.append(Finding("warning", "no-install", f"install not found at {cfg['definitions_dir']}; collision checks skipped"))

    element_names_needed: set[str] = set()
    metx_types: set[str] = set()
    for p in sorted(defs.glob("*.metx")):
        root = _parse(p, f)
        if root is None:
            continue
        for dis, elem in metx_mappings(root):
            metx_types.add(dis)
            if not DIS_TYPE_RE.match(dis or ""):
                f.append(Finding("error", "dis-type-format", f"{p.name}: '{dis}'"))
            elif dis in idx["types"]:
                f.append(Finding("error", "dis-type-collision", f"{dis} already mapped to {idx['types'][dis]}"))
            element_names_needed.add(elem)

    for p in sorted((visuals / "UserData").glob("*.ommx")):
        root = _parse(p, f)
        if root is None:
            continue
        for name in ommx_names(root):
            model_defs.add(name)
            if name in idx["models"]:
                f.append(Finding("error", "model-def-collision", f"model definition '{name}' already exists in the install"))
        fn = ommx_params(root).get("filename")
        if not fn:
            f.append(Finding("error", "ommx-no-filename", f"{p.name}: Entity schema requires 'filename' (guide 14.1)"))
        elif not fn.startswith("$(SHARED_DATA_DIR)/"):
            f.append(Finding("warning", "ommx-abs-path", f"{p.name}: filename should be relative to $(SHARED_DATA_DIR): {fn}"))
        else:
            target = shared / fn.removeprefix("$(SHARED_DATA_DIR)/")
            if not target.exists():
                f.append(Finding("error", "model-file-missing", f"{fn} -> {target} not found (did convert run?)"))

    leaves = sorted(defs.glob("*.leaf"))
    if not leaves and not element_names_needed:
        pass      # a DI-Guy lifeform with no element definition: shown from the .entity's character settings
    elif not leaves:
        f.append(Finding("pending", "no-leaf", f"no .leaf yet; element definitions {sorted(element_names_needed)} unresolved"))
    else:
        found: set[str] = set()
        for p in leaves:
            root = _parse(p, f)
            if root is None:
                continue
            items = root.findall(".//myElementDefinitions/item")
            if items and items[0].get("class_id") is None:
                f.append(Finding("error", "leaf-boost-class-ids", f"{p.name}: first element definition lacks Boost class_id "
                                 "attributes; the file will not deserialise"))
            for item in items:
                name = item.findtext("myName")
                found.add(name)
                for vis in item.findall("myModelSet3D/myVisualizerDefinitions/item"):
                    if vis.findtext("myName") != "DtModelSchema":
                        continue
                    attrs = {a.findtext("myName"): a.findtext("myValue") for a in vis.findall("myAttributes/item")}
                    md = attrs.get("modeldefinition")
                    # a DI-Guy element lists several model definitions, tab separated (random appearances)
                    missing = [x for x in (md or "").split("	") if x and x not in model_defs and x not in idx["models"]]
                    if missing:
                        md = ", ".join(missing)
                        f.append(Finding("error", "leaf-model-def-missing", f"{p.name}: '{name}' 3D model uses model "
                                         f"definition '{md}', defined neither in UserData/*.ommx nor in the install"))
        for name in element_names_needed - found:
            f.append(Finding("error", "element-def-missing", f".metx maps to '{name}' but no .leaf defines it"))

    entities = sorted((out_dir / "sms" / "vrfSim").glob("*.entity"))
    if not entities:
        f.append(Finding("pending", "no-entity", "no .entity simulation object yet"))
    for p in entities:
        root = _parse(p, f)
        if root is not None:
            f += validate_entity(p.name, root, metx_types)
    return f


# ------------------------------------------------------------------ .entity
FIELDS_BY_PARAM = {   # first param-type of the pair -> a field that must be present
    3: "max-extension", 11: "max-azimuth", 13: "max-elevation", 15: "max-rotation",
    5: "max-translation", 7: "max-translation", 9: "max-translation",
}
LIMIT_PAIRS = [("min-azimuth", "max-azimuth"), ("min-elevation", "max-elevation"), ("min-rotation", "max-rotation")]


def _num(el, tag, name):
    x = el.find(f"{tag}[@paramName='{name}']")
    return None if x is None or x.text is None else float(x.text)


def _pos(el, name):
    x = el.find(f"bodyPosition[@paramName='{name}']")
    return None if x is None else tuple(float(x.get(k, 0)) for k in ("forward", "right", "down"))


def validate_entity(fname: str, root, metx_types: set[str]) -> list[Finding]:
    """Consistency of a generated .entity (users guide 64-66, stock 5.1.1 .entity layout)."""
    f: list[Finding] = []
    so = root.find("simObject")
    if so is None:
        return [Finding("error", "entity-no-simobject", f"{fname}: no <simObject>")]

    ot = so.get("objectType", "")
    fields = ot.split(":")
    if len(fields) != 8 or not all(re.match(r"^-?\d+$", x) for x in fields):
        f.append(Finding("error", "entity-objecttype-format", f"{fname}: objectType '{ot}' must be 8 integers (super-type + DIS type)"))
    else:
        if fields[0] != "1":
            f.append(Finding("warning", "entity-supertype", f"{fname}: object super-type {fields[0]} (1 = Individual, users guide D.3.1)"))
        if metx_types and ":".join(fields[1:]) not in metx_types:
            f.append(Finding("error", "entity-objecttype-mismatch", f"{fname}: objectType {ot} does not match the .metx type(s) {sorted(metx_types)}"))
    if so.get("matchType") not in (None, ot):
        f.append(Finding("warning", "entity-matchtype", f"{fname}: matchType {so.get('matchType')} != objectType {ot}"))

    # articulated parts. A lifeform keeps its donor's stock part list (weapon, head ...), which uses shapes
    # these checks do not cover, and the pipeline does not write it.
    lifeform = so.get("platform", "").replace(chr(92), "/").rsplit("/", 1)[-1] in ("Human.ope", "Animal.ope")
    parts = [] if lifeform else so.findall("DtSimArtPartDescriptorList/DtSimArtPartDescriptor")
    codes: dict[int, ET.Element] = {}
    for d in parts:
        code = int(_num(d, "int", "art-part-type") or 0)
        if code in codes:
            f.append(Finding("error", "entity-artpart-duplicate", f"{fname}: art part {code} listed twice"))
        codes[code] = d
    for code, d in codes.items():
        parent = int(_num(d, "int", "parent-part-type") if _num(d, "int", "parent-part-type") is not None else -1)
        if parent not in (-1, 0) and parent not in codes:
            f.append(Finding("error", "entity-artpart-parent", f"{fname}: part {code} has parent {parent}, which is not in the list"))
        params = [int(x.text) for x in d.findall("DtRwList/int") if x.text]
        if len(params) != 2 or params[1] != params[0] + 1:
            f.append(Finding("error", "entity-artpart-params", f"{fname}: part {code} param list {params} is not a (value, rate) pair"))
        elif params[0] in FIELDS_BY_PARAM:
            need = FIELDS_BY_PARAM[params[0]]
            tag = "vector" if need == "max-translation" else "real"
            if d.find(f"{tag}[@paramName='{need}']") is None:
                f.append(Finding("error", "entity-artpart-fields", f"{fname}: part {code} params {params} but no '{need}'"))
        for lo_name, hi_name in LIMIT_PAIRS:
            lo, hi = _num(d, "real", lo_name), _num(d, "real", hi_name)
            if lo is not None and hi is not None and lo > hi:
                f.append(Finding("error", "entity-artpart-limits", f"{fname}: part {code} {lo_name} {lo} > {hi_name} {hi}"))

    # weapon systems refer to art parts by code
    for cs in so.findall("componentSystem"):
        for el in cs.findall("int"):
            name = el.get("paramName", "")
            if name.endswith("art-part-type") and el.text and int(el.text) > 0 and int(el.text) not in codes:
                f.append(Finding("warning", "entity-system-artpart",
                                 f"{fname}: system '{cs.get('systemName')}' uses {name}={el.text}, which this entity has no part for "
                                 "(inherited from the donor?); fix the manifest parts or the system in the SOE"))

    # attach points inside the bounding volume
    bv = so.find("boundingVolume")
    if bv is not None and bv.find("dimensions") is not None:
        dims = {k: float(bv.find("dimensions").get(k, 0)) for k in ("length", "width", "height")}
        off = bv.find("offset")
        c = tuple(float(off.get(k, 0)) for k in ("forward", "right", "down")) if off is not None else (0, 0, 0)
        half = (dims["length"] / 2, dims["width"] / 2, dims["height"] / 2)

        def absolute(code, seen=()):
            d = codes[code]
            ap = _pos(d, "attach-point") or (0, 0, 0)
            parent = int(_num(d, "int", "parent-part-type") or -1)
            if parent in codes and parent not in seen:
                pp = absolute(parent, seen + (code,))
                return tuple(ap[i] + pp[i] for i in range(3))
            return ap
        for code in codes:
            a = absolute(code)
            if any(abs(a[i] - c[i]) > half[i] * 1.05 + 0.05 for i in range(3)):
                f.append(Finding("warning", "entity-attach-outside", f"{fname}: part {code} attach point {tuple(round(v, 3) for v in a)} lies outside the bounding volume"))
        for s in ("left-support", "right-support", "other-support"):
            sp = _pos(so, s)
            if sp and any(abs(sp[i] - c[i]) > half[i] * 1.05 + 0.05 for i in range(2)):
                f.append(Finding("warning", "entity-support-outside", f"{fname}: {s} {sp} lies outside the footprint"))

    geo = so.find("string[@paramName='object-geometry-filename']")
    if geo is not None and geo.text:
        f.append(Finding("warning", "entity-object-geometry", f"{fname}: object-geometry-filename is set ({geo.text}); is it the donor's?"))
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", type=Path, help="output/<name>")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    findings = validate(args.out_dir, load_config())
    if args.json:
        print(json.dumps([x.to_dict() for x in findings], indent=2))
    else:
        print_findings("validate", findings)
    return 1 if has_errors(findings) else 0


if __name__ == "__main__":
    sys.exit(main())
