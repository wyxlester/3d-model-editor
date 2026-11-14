"""Element definition (.leaf) by cloning the donor's, as the Simulation Object Editor does.

Evidence (hand-import test, 2026-11-14): copying Super_Tanker with "New Model from Existing (Copy)" wrote
Entity/HarmonyOfTheSeas.leaf, which is the donor's element definition (stock
Vehicles/Surface/SIRIUS_STAR_OIL_TANKER_LOADED_V7.0/Super Tanker.leaf) with exactly two values changed:
  myName                                  "Super Tanker"     -> "HarmonyOfTheSeas"
  3D DtModelSchema modeldefinition         "SurfaceOilTanker" -> the new model definition
Everything else (myParentName, wake, trail effect, XR and 2D visuals, visualizer UUIDs) is the donor's.

The donor's element definition is found the way VR-Forces does it: donor .entity objectType (minus the
super-type) -> stock *.metx mapping -> element name -> the *.leaf item with that myName.

Boost serialisation detail: class_id / tracking_level / version attributes appear only on the FIRST
occurrence of each class in a file (class ids are numbered in first-encounter order). A donor item taken from
the middle of a large stock .leaf has none, so `normalise_item` strips them and re-applies them in encounter
order, reproducing the editor's file byte-for-byte apart from whitespace.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

ENVELOPE_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
                 '<!DOCTYPE boost_serialization>\n'
                 '<boost_serialization signature="serialization::archive" version="14">\n'
                 '<EntityLeaf class_id="0" tracking_level="1" version="1" object_id="_0">\n'
                 '\t<myElementDefinitions class_id="1" tracking_level="0" version="0">\n'
                 '\t\t<count>1</count>\n'
                 '\t\t<item_version>2</item_version>\n')
ENVELOPE_TAIL = '\t</myElementDefinitions>\n</EntityLeaf>\n</boost_serialization>\n'

BOOST_ATTRS = ("class_id", "tracking_level", "version", "object_id", "object_id_reference", "class_id_reference")
MODEL_SETS = ("myModelSet3D", "myModelSetXR", "myModelSet2D")
# role -> Boost class version, in the order the classes are first met inside an element definition
ROLE_VERSION = {"element": "2", "modelset": "1", "visdefs": "0", "visualizer": "2", "attrs": "0", "attr": "1"}


class LeafError(Exception):
    pass


def _role(el: ET.Element, parent: ET.Element | None) -> str | None:
    if el.tag in MODEL_SETS:
        return "modelset"
    if el.tag == "myVisualizerDefinitions":
        return "visdefs"
    if el.tag == "myAttributes":
        return "attrs"
    if el.tag == "item" and parent is not None:
        return {"myVisualizerDefinitions": "visualizer", "myAttributes": "attr"}.get(parent.tag)
    return None


def normalise_item(item: ET.Element, first_class_id: int = 2) -> ET.Element:
    """Return a copy of one element-definition <item> with Boost class attributes as the first item in a file."""
    item = copy.deepcopy(item)
    for el in item.iter():
        for a in BOOST_ATTRS:
            el.attrib.pop(a, None)
    parents = {c: p for p in item.iter() for c in p}
    next_id = first_class_id
    seen: set[str] = set()
    for el in item.iter():
        role = "element" if el is item else _role(el, parents.get(el))
        if role and role not in seen:
            seen.add(role)
            el.set("class_id", str(next_id))
            el.set("tracking_level", "0")
            el.set("version", ROLE_VERSION[role])
            next_id += 1
    return item


def _attr_items(vis: ET.Element) -> dict[str, ET.Element]:
    return {a.findtext("myName"): a for a in vis.findall("myAttributes/item")}


# Every stock 3D main-model visualizer (and the editor's own) uses this UUID and preUuidIndex 1.
MODEL_SCHEMA_UUID = "f5ff6339-d803-4764-aaa3-480a7676d88d"
MODEL_VISUALIZER_TYPE = "DtEntityArticulatedStateModelVisualizer"


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def _set_count(container: ET.Element) -> None:
    container.find("count").text = str(len(container.findall("item")))


def _add_attr(vis: ET.Element, name: str, value: str) -> None:
    attrs = vis.find("myAttributes")
    a = _sub(attrs, "item")
    _sub(a, "myName", name)
    _sub(a, "myValue", value)
    _set_count(attrs)


def _new_model_visualizer(model_def: str) -> ET.Element:
    vis = ET.Element("item")
    _sub(vis, "myName", "DtModelSchema")
    attrs = _sub(vis, "myAttributes")
    _sub(attrs, "count", "0")
    _sub(attrs, "item_version", "1")
    _sub(vis, "myUuidString", MODEL_SCHEMA_UUID)
    _sub(vis, "myPreUuidIndex", "1")
    _add_attr(vis, "modeldefinition", model_def)
    _add_attr(vis, "type", MODEL_VISUALIZER_TYPE)
    return vis


def retarget(item: ET.Element, name: str, model_def: str, schema: str | None = None, xr: bool = True) -> ET.Element:
    """Rename the element definition and point its 3D (and optionally XR) main model at `model_def`.
    Adds the modeldefinition attribute, or a standard DtModelSchema visualizer, when the donor has none."""
    item = copy.deepcopy(item)
    item.find("myName").text = name
    for s in ["myModelSet3D"] + (["myModelSetXR"] if xr else []):
        visdefs = item.find(f"{s}/myVisualizerDefinitions")
        if visdefs is None:
            if s == "myModelSet3D":
                raise LeafError("donor element definition has no 3D model set")
            continue
        vis = next((v for v in visdefs.findall("item") if v.findtext("myName") == "DtModelSchema"), None)
        if vis is None:
            vis = _new_model_visualizer(model_def)
            if visdefs.find("item_version") is None:          # empty list: count only
                visdefs.insert(list(visdefs).index(visdefs.find("count")) + 1, ET.Element("item_version"))
                visdefs.find("item_version").text = "2"
            pos = list(visdefs).index(visdefs.find("item_version")) + 1
            visdefs.insert(pos, vis)
            _set_count(visdefs)
            continue
        attrs = _attr_items(vis)
        if "modeldefinition" in attrs:
            attrs["modeldefinition"].find("myValue").text = model_def
        else:
            _add_attr(vis, "modeldefinition", model_def)
        if schema and "modeldefinitionschema" in attrs:
            attrs["modeldefinitionschema"].find("myValue").text = schema
    return item


def leaf_text(item: ET.Element) -> str:
    item = copy.deepcopy(item)
    ET.indent(item, space="\t", level=2)
    body = ET.tostring(item, encoding="unicode", short_empty_elements=False)
    return ENVELOPE_HEAD + "\t\t" + body.rstrip() + "\n" + ENVELOPE_TAIL


# ------------------------------------------------------------------ donor lookup
def element_name_for_type(definitions_dir: Path, dis7: str) -> tuple[str, Path] | None:
    """The install's .metx mapping for a 7-field type -> (element definition name, metx file).
    An exact mapping wins; otherwise the best match, where -1 in a mapping stands for any value (stock
    buildings and animals are mapped this way, e.g. the hangar 5:1:0:2:1:1:0 by 5:1:-1:2:1:1:-1). Among
    wildcard matches the one with the most exact fields wins, earlier fields counting more."""
    want = dis7.split(":")
    best: tuple[tuple, str, Path] | None = None
    for p in sorted(Path(definitions_dir).rglob("*.metx")):
        try:
            items = list(ET.parse(p).getroot().iter("item"))
        except ET.ParseError:
            continue
        for it in items:
            first, second = it.findtext("first"), it.findtext("second")
            if not first or not second:
                continue
            if first == dis7:
                return second, p
            have = first.split(":")
            if len(have) != len(want) or any(h not in ("-1", w) for h, w in zip(have, want)):
                continue
            exact = tuple(h == w for h, w in zip(have, want))
            score = (sum(exact), exact)
            if best is None or score > best[0]:
                best = (score, second, p)
    return (best[1], best[2]) if best else None


def find_element(definitions_dir: Path, element: str) -> tuple[ET.Element, Path] | None:
    for p in sorted(Path(definitions_dir).rglob("*.leaf")):
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError:
            continue
        for it in root.findall(".//myElementDefinitions/item"):
            if it.findtext("myName") == element:
                return it, p
    return None


def donor_element(definitions_dir: Path, donor_object_type: str) -> tuple[ET.Element, str, Path]:
    fields = donor_object_type.split(":")
    dis7 = ":".join(fields[1:]) if len(fields) == 8 else donor_object_type
    hit = element_name_for_type(definitions_dir, dis7)
    if not hit:
        raise LeafError(f"no .metx in {definitions_dir} maps the donor type {dis7}")
    element, _ = hit
    found = find_element(definitions_dir, element)
    if not found:
        raise LeafError(f"donor element definition '{element}' (type {dis7}) not found in any .leaf")
    return found[0], element, found[1]
