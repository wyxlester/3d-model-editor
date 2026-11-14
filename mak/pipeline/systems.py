"""Systems of a simulation object (sensors, weapons, other systems): the catalog of what the install offers,
and the edits that fit the donor's systems to our visual model.

What a system is (users guide 71.4, 71.4.2, 71.5): a `componentSystem` element in the `.entity`, named by
`systemName` and pointing at a `.sysdef` under `@(system-dir)`. Its children override the sysdef's `$variables`;
a variable left out takes the sysdef's default (71.5.1). A sensor can be added by copying one and giving it a
unique `systemName` (72.1.1).

What ties a system to the model:
- sensors: `sensor-position`, which feeds the sensor's `sensor-offset` (71.4.2, 72.1.2); body frame
  (forward, right, down) like every other `.entity` position.
- weapons: `*-art-part-type` parameters, each with an `art-part-category` in the sysdef meta-data
  (e.g. mm-gun.template_sysdef: turret-art-part-type = turret, gun-art-part-type = gun). In the install
  1372 of 1383 bound codes match a part in the same `.entity` (the other 11 are -1 parents).

Which systems an object can have: `allowed-state-repository-types` in the sysdef meta-data must name the state
repository of the object's platform, the `.ope`'s `parameter-type` (71.1.4; the SOE filters on it, 66.4).

Read-only on the install. The catalog is cached in the system temp folder (rebuilt in about a second).
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import artparts
from .common import Finding

# ------------------------------------------------------------------ S-expressions (.sysdef, .ope, .mtl)
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([()])|;[^\n]*|([^\s()";]+)')


class Str(str):
    """A quoted string in an S-expression (atoms are plain str)."""


def parse_sexpr(text: str) -> list:
    """Nested lists of atoms (str) and quoted strings (Str). `;` starts a comment; unbalanced parens are
    tolerated (the outermost list is returned as far as it goes)."""
    stack: list[list] = [[]]
    for m in _TOKEN.finditer(text):
        quoted, paren, atom = m.groups()
        if paren == "(":
            stack.append([])
        elif paren == ")":
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        elif quoted is not None:
            stack[-1].append(Str(quoted))
        elif atom is not None:
            stack[-1].append(atom)
    while len(stack) > 1:
        done = stack.pop()
        stack[-1].append(done)
    return stack[0]


def forms(tree, head: str):
    """Every list in `tree` (any depth) whose first item is `head`."""
    if isinstance(tree, list):
        if tree and tree[0] == head:
            yield tree
        for x in tree:
            if isinstance(x, list):
                yield from forms(x, head)


def first(tree, head: str):
    return next(forms(tree, head), None)


def _values(form) -> list:
    return [x for x in (form or [])[1:] if not isinstance(x, list)]


def _all_lists(tree):
    if isinstance(tree, list):
        yield tree
        for x in tree:
            if isinstance(x, list):
                yield from _all_lists(x)


# ------------------------------------------------------------------ catalog
@dataclass
class Param:
    name: str
    type: str                       # DtRwInt, DtRwReal, DtRwBoolean, DtRwString, DtRwOffsetVector, ...
    category: str | None = None     # art-part-category of an art-part-type parameter
    default: list = field(default_factory=list)


@dataclass
class Sysdef:
    path: str                       # relative to the systems folder, forward slashes: "weapons/phalanx-20mm-gun.sysdef"
    name: str = ""                  # meta-data system-name
    description: str = ""
    categories: list[str] = field(default_factory=list)
    repositories: list[str] = field(default_factory=list)
    params: dict[str, Param] = field(default_factory=dict)
    fixed_codes: list[int] = field(default_factory=list)    # literal art-part-type codes inside the sysdef
    ammunition: dict[str, str] = field(default_factory=dict)  # ammunitionParams.mtl name -> amount variable
    includes: list[str] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return self.path.split("/")[0]

    @property
    def art_params(self) -> dict[str, str]:
        return {p.name: p.category for p in self.params.values() if p.category}

    def allows(self, repository: str | None) -> bool:
        return repository is None or "all" in self.repositories or repository in self.repositories

    @property
    def is_sensor(self) -> bool:
        return self.folder == "sensors" or "sensor" in self.categories

    @property
    def is_weapon(self) -> bool:
        return self.folder == "weapons" or "weapon" in self.categories


VECTOR_TYPES = {"DtRwOffsetVector", "DtRwVector"}
XML_TAG = {"DtRwInt": "int", "DtRwReal": "real", "DtRwBoolean": "bool", "DtRwString": "string",
           "DtRwOffsetVector": "bodyPosition"}


def _resolve_include(name: str, systems_dir: Path) -> Path:
    rel = re.sub(r"^\$\(system-dir\)[\\/]", "", name).replace("\\", "/")
    return systems_dir / rel


def read_sysdef(path: Path, systems_dir: Path, _seen: set | None = None) -> Sysdef:
    """One .sysdef with its templates (`(system-definition (filename ...))`) folded in. The template's
    meta-data parameters count unless the sysdef sets `merge-with-sub-systems False` (users guide 73.1.2)."""
    _seen = _seen or set()
    _seen.add(path.resolve())
    tree = parse_sexpr(path.read_text(encoding="utf-8", errors="replace"))
    rel = path.relative_to(systems_dir).as_posix() if path.is_relative_to(systems_dir) else path.name
    sd = Sysdef(rel)
    top = next((x for x in tree if isinstance(x, list)), [])
    meta = next((x for x in top if isinstance(x, list) and x and x[0] == "meta-data"), None)
    if meta is not None:
        sd.name = str((_values(first(meta, "system-name")) or [""])[0])
        sd.description = str((_values(first(meta, "system-description")) or [""])[0])
        sd.categories = [str(x) for x in _values(first(meta, "system-categories"))]
        sd.repositories = [str(x) for x in _values(first(meta, "allowed-state-repository-types"))]
        plist = first(meta, "parameter-data-list") or []
        for pd in plist[1:]:
            if not (isinstance(pd, list) and pd and str(pd[0]).endswith("parameter-data")):
                continue
            name = _values(first(pd, "parameter-name"))
            if not name:
                continue
            cat = _values(first(pd, "art-part-category"))
            cat = str(cat[0]) if cat else None
            if cat and cat.startswith("$"):       # 76mm-navalgun: the category is a variable; the name says it
                cat = next((c for c in ("launcher", "gun", "turret") if c in str(name[0])), None)
            sd.params[str(name[0])] = Param(str(name[0]), str((_values(first(pd, "variable-type")) or [""])[0]),
                                            cat, [str(x) for x in _values(first(pd, "default-value"))])
    merge = meta is None or str((_values(first(meta, "merge-with-sub-systems")) or ["True"])[0]) != "False"
    # literal part codes: (art-part-type 4096) in an art-part list, or a binding such as
    # (turret-art-part-type 4096) (m1a2-multigun.sysdef); such a system only works with those codes
    for f in _all_lists(tree):
        head = f[0] if f and isinstance(f[0], str) else ""
        v = _values(f)
        if head.endswith("art-part-type") and head != "parent-art-part-type" and len(v) == 1 \
                and re.fullmatch(r"\d+", str(v[0])):
            sd.fixed_codes.append(int(v[0]))
    for al in forms(tree, "ammunition-list"):
        for entry in al[1:]:
            if isinstance(entry, list) and entry:
                amount = _values(first(entry, "amount"))
                sd.ammunition[str(entry[0])] = str(amount[0]).lstrip("$") if amount else ""
    for inc in forms(tree, "system-definition"):
        fn = _values(first(inc, "filename"))
        if not fn:
            continue
        ipath = _resolve_include(str(fn[0]), systems_dir)
        sd.includes.append(str(fn[0]))
        if not ipath.exists() or ipath.resolve() in _seen:
            continue
        sub = read_sysdef(ipath, systems_dir, _seen)
        if merge:
            for k, p in sub.params.items():
                sd.params.setdefault(k, p)
        sd.categories = sd.categories or sub.categories
        sd.repositories = sd.repositories or sub.repositories
        sd.fixed_codes += sub.fixed_codes
        for k, v in sub.ammunition.items():
            sd.ammunition.setdefault(k, v)
    sd.fixed_codes = sorted(set(sd.fixed_codes))
    return sd


def group_of(object_type: str) -> str | None:
    """Usage group of an .entity objectType: '1:<domain>' for platforms, '<kind>' otherwise (DIS kinds)."""
    try:
        f = [int(x) for x in object_type.split(":")]
    except ValueError:
        return None
    if len(f) < 3 or f[0] != 1:            # super-type 1 = individual entity (users guide D.3.1)
        return None
    return f"1:{f[2]}" if f[1] == 1 else str(f[1])


def group_for_profile(profile) -> str:
    return f"1:{profile.dis_domain}" if profile.dis_kind == 1 else str(profile.dis_kind)


_CS = re.compile(r'<componentSystem\s+systemName="([^"]*)"\s+platform="([^"]*)"\s*(/>|>(.*?)</componentSystem>)', re.S)
_INT = re.compile(r'<int paramName="([^"]+)">(-?\d+)</int>')


def platform_rel(platform: str) -> str:
    """'@(system-dir)\\weapons\\x.sysdef' -> 'weapons/x.sysdef' (catalog key, lower case)."""
    return re.sub(r"^[@$]\(system-dir\)[\\/]", "", platform).replace("\\", "/").lower()


@dataclass
class Catalog:
    sysdefs: dict[str, Sysdef]                  # key: platform_rel()
    usage: dict[str, dict]                      # key -> {"groups": {group: n}, "ints": {param: median}}
    repositories: dict[str, str]                # platform .ope file name -> state repository (parameter-type)
    bindings: dict[str, list[str]] = field(default_factory=dict)   # art-part parameter -> families stock binds

    def accepts(self, param: str, code: int, category: str | None = None) -> bool:
        """Can `param` bind part `code`? By what stock entities bind it to (e.g. elevating-turret-art-part-type
        takes turret, gun and launcher codes; gun-art-part-type also 8192); else by the sysdef's category."""
        seen = self.bindings.get(param)
        if seen:
            return family(code) in seen
        return category is None or family(code) == category or category not in CATEGORY_FAMILIES

    def get(self, platform_or_rel: str) -> Sysdef | None:
        return self.sysdefs.get(platform_rel(platform_or_rel))

    def used_in(self, key: str, group: str) -> int:
        return self.usage.get(platform_rel(key), {}).get("groups", {}).get(group, 0)

    def typical_int(self, key: str, param: str) -> int | None:
        return self.usage.get(platform_rel(key), {}).get("ints", {}).get(param)

    def to_json(self) -> dict:
        return {"version": CATALOG_VERSION, "sysdefs": {k: asdict(v) for k, v in self.sysdefs.items()},
                "usage": self.usage, "repositories": self.repositories, "bindings": self.bindings}

    @classmethod
    def from_json(cls, d: dict) -> "Catalog":
        if d.get("version") != CATALOG_VERSION:
            raise ValueError("old catalog")
        sysdefs = {}
        for k, v in d["sysdefs"].items():
            v = dict(v)
            v["params"] = {n: Param(**p) for n, p in v["params"].items()}
            sysdefs[k] = Sysdef(**v)
        return cls(sysdefs, d["usage"], d["repositories"], d["bindings"])


CATALOG_VERSION = 1


def _sms_vrfsim(cfg: dict) -> Path:
    return Path(cfg["sms_dir"]) / cfg.get("donor_sms", "EntityLevel") / "vrfSim"


def build_catalog(cfg: dict) -> Catalog:
    vrfsim = _sms_vrfsim(cfg)
    systems_dir = vrfsim / "systems"
    sysdefs: dict[str, Sysdef] = {}
    for p in sorted(systems_dir.rglob("*.sysdef")):
        try:
            sd = read_sysdef(p, systems_dir)
        except (OSError, UnicodeError):
            continue
        sysdefs[sd.path.lower()] = sd
    repos = {}
    for ope in sorted((vrfsim / "platforms").glob("*.ope")):
        m = re.search(r'\(parameter-type\s+"([^"]+)"\)', ope.read_text(encoding="utf-8", errors="replace"))
        if m:
            repos[ope.name] = m.group(1)
    groups: dict[str, Counter] = defaultdict(Counter)
    ints: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    bindings: dict[str, set] = defaultdict(set)
    for e in sorted(vrfsim.glob("*.entity")):
        s = e.read_text(encoding="utf-8", errors="replace")
        ot = re.search(r'objectType="([^"]+)"', s)
        g = group_of(ot.group(1)) if ot else None
        if not g:
            continue
        for m in _CS.finditer(s):
            key = platform_rel(m.group(2))
            groups[key][g] += 1
            for name, v in _INT.findall(m.group(4) or ""):
                ints[key][name].append(int(v))
                if name.endswith("art-part-type") and name != "parent-art-part-type" and int(v) > 0:
                    bindings[name].add(family(int(v)))
    usage = {k: {"groups": dict(groups[k]),
                 "ints": {n: int(statistics.median(v)) for n, v in ints[k].items() if v}} for k in groups}
    return Catalog(sysdefs, usage, repos, {k: sorted(v) for k, v in bindings.items()})


def _fingerprint(cfg: dict) -> str:
    vrfsim = _sms_vrfsim(cfg)
    files = list((vrfsim / "systems").rglob("*.sysdef")) + list(vrfsim.glob("*.entity"))
    newest = max((p.stat().st_mtime for p in files), default=0)
    return hashlib.sha1(f"{vrfsim.resolve()}|{len(files)}|{newest}".encode()).hexdigest()[:16]


def load_catalog(cfg: dict, cache_dir: Path | None = None) -> Catalog | None:
    """The install's system catalog, or None when the simulation model set isn't there (unit tests)."""
    if not (_sms_vrfsim(cfg) / "systems").is_dir():
        return None
    cache_dir = cache_dir or Path(tempfile.gettempdir()) / "vrf-pipeline-cache"
    cache = cache_dir / f"systems-{_fingerprint(cfg)}.json"
    if cache.exists():
        try:
            return Catalog.from_json(json.loads(cache.read_text(encoding="utf-8")))
        except (ValueError, TypeError, KeyError):
            pass
    cat = build_catalog(cfg)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob("systems-*.json"):
        old.unlink(missing_ok=True)
    cache.write_text(json.dumps(cat.to_json(), indent=1), encoding="utf-8")
    return cat


# ------------------------------------------------------------------ part categories
# art-part-category -> Table 58 families (primary and secondary base codes, 10 instances 32 apart; ACM 21.7)
CATEGORY_FAMILIES = {
    "turret": (4096, 5696),
    "gun": (4416, 6016),
    "launcher": (4736, 6336),
}
WEAPON_EXTRA = (8192, 8224, 8256, 8288)    # not in Table 58; stock weapons bind guns and launchers to them


def family(code: int) -> str:
    """'turret' / 'gun' / 'launcher' for those Table 58 families, else the code itself as text."""
    for cat, bases in CATEGORY_FAMILIES.items():
        if any(b <= code < b + 320 for b in bases):
            return cat
    return str(code)


def is_weapon_part(code: int) -> bool:
    return family(code) in CATEGORY_FAMILIES or code in WEAPON_EXTRA


# ------------------------------------------------------------------ applying manifest systems to the .entity
def system_elements(so: ET.Element) -> list[ET.Element]:
    return so.findall("componentSystem")


def _fmt(v: float) -> str:
    v = round(v, 9) + 0.0
    return f"{v:.12g}"


def position_of(node: str, glb: dict, parts: list) -> tuple[float, float, float] | None:
    """Body-frame (forward, right, down) position of a glb node's pivot (a part, socket_* or station__*)."""
    n = glb.get("nodes", {}).get(node)
    if not n:
        return None
    return artparts.model_to_body(n["pivot"])


def _param_tag(sd: Sysdef | None, name: str, value) -> str:
    if sd and name in sd.params and sd.params[name].type in XML_TAG:
        return XML_TAG[sd.params[name].type]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "real"
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return "bodyPosition"
    return "string"


def _set_param(cs: ET.Element, name: str, value, sd: Sysdef | None) -> None:
    el = cs.find(f"*[@paramName='{name}']")
    tag = el.tag if el is not None else _param_tag(sd, name, value)
    if el is None:
        el = ET.Element(tag, paramName=name)
        kids = list(cs)                               # the editor writes parameters sorted by name
        at = next((i for i, k in enumerate(kids) if (k.get("paramName") or "") > name), len(kids))
        cs.insert(at, el)
    if tag == "bodyPosition":
        for k, v in zip(("forward", "right", "down"), value):
            el.set(k, _fmt(float(v)))
    elif tag == "bool":
        el.text = "True" if value else "False"
    elif tag == "real":
        el.text = _fmt(float(value))
    else:
        el.text = str(value)


def _code_for(value, parts: list) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return next((p.code for p in parts if p.node == value), None)


def apply_systems(so: ET.Element, manifest: dict, parts: list, glb: dict,
                  catalog: Catalog | None) -> list[Finding]:
    """Edit the cloned donor systems as the manifest's `systems` list says. Systems it doesn't name are left
    as the donor has them, so a manifest without `systems` changes nothing."""
    out: list[Finding] = []
    entries = manifest.get("systems") or []
    if not entries:
        return out
    by_name = {cs.get("systemName"): cs for cs in system_elements(so)}
    for e in entries:
        name = e["system"]
        cs = by_name.get(name)
        why = f" ({e['reason']})" if e.get("reason") else ""
        if e.get("remove"):
            if cs is not None:
                so.remove(cs)
                by_name.pop(name)
                out.append(Finding("info", "system-removed", f"{name}: removed{why}"))
            continue
        if cs is None:
            if not e.get("sysdef"):
                out.append(Finding("error", "system-unknown", f"systems: '{name}' is not one of the donor's "
                                   f"systems ({', '.join(by_name)}); give a 'sysdef' to add it"))
                continue
            cs = ET.Element("componentSystem", systemName=name, platform="")
            existing = system_elements(so)
            at = list(so).index(existing[-1]) + 1 if existing else len(so)
            so.insert(at, cs)
            by_name[name] = cs
            added = True
        else:
            added = False
        if e.get("sysdef"):
            cs.set("platform", "@(system-dir)\\" + e["sysdef"].replace("/", "\\"))
        sd = catalog.get(cs.get("platform", "")) if catalog else None
        changes = []
        failed = len(out)
        for pname, target in (e.get("bind") or {}).items():
            code = _code_for(target, parts)
            if code is None:
                out.append(Finding("error", "system-bind-unknown", f"{name}: {pname} -> '{target}' is not a "
                                   "manifest part node or a code"))
                continue
            _set_param(cs, pname, code, sd)
            changes.append(f"{pname} = {code}" + (f" ({target})" if not isinstance(target, int) else ""))
        if e.get("position_from"):
            pos = position_of(e["position_from"], glb, parts)
            pname = e.get("position_param") or "sensor-position"
            if pos is None:
                out.append(Finding("error", "system-position-unknown", f"{name}: position_from "
                                   f"'{e['position_from']}' is not a node of the model"))
            else:
                _set_param(cs, pname, pos, sd)
                changes.append(f"{pname} = at {e['position_from']} "
                               f"(forward {pos[0]:.1f}, right {pos[1]:.1f}, down {pos[2]:.1f})")
        for pname, value in (e.get("params") or {}).items():
            _set_param(cs, pname, value, sd)
            changes.append(f"{pname} = {value}")
        if not changes and not added and len(out) > failed:
            continue                      # only errors for this entry: they say it all
        what = "added" if added else "changed"
        out.append(Finding("info", f"system-{what}", f"{name} ({platform_rel(cs.get('platform', ''))}): {what}"
                           + (": " + "; ".join(changes) if changes else "") + why))
    return out


# ------------------------------------------------------------------ checks on a written .entity
MOVEMENT_FOLDER, DAMAGE_FOLDER = "movement", "damage"


def check_entity_systems(so: ET.Element, catalog: Catalog | None, repository: str | None,
                         part_codes: set[int]) -> list[Finding]:
    """Problems VR-Forces would have with the systems in `so` (each message cites the guide)."""
    f: list[Finding] = []
    css = system_elements(so)
    names = Counter(cs.get("systemName") for cs in css)
    for n, c in names.items():
        if c > 1:
            f.append(Finding("error", "system-name-duplicate", f"systemName '{n}' is used {c} times; each must be "
                             "unique (users guide 72.1.1)"))
        if n and n[0].isdigit():
            f.append(Finding("error", "system-name-digit", f"systemName '{n}' starts with a digit (users guide 71.4.1)"))
    folders = Counter(platform_rel(cs.get("platform", "")).split("/")[0] for cs in css)
    for folder, label, sec in ((MOVEMENT_FOLDER, "movement", "66.4.3"), (DAMAGE_FOLDER, "damage", "66.4.2")):
        if folders[folder] > 1:
            f.append(Finding("error", f"system-{label}-count", f"{folders[folder]} {label} systems; an object has "
                             f"one (users guide {sec})"))
    box = _bounding_box(so)
    has_sensor = has_weapon = False
    for cs in css:
        name, plat = cs.get("systemName"), cs.get("platform", "")
        sd = catalog.get(plat) if catalog else None
        folder = platform_rel(plat).split("/")[0]
        has_sensor |= folder == "sensors" or bool(sd and sd.is_sensor)
        has_weapon |= folder == "weapons" or bool(sd and sd.is_weapon)
        if catalog is not None and sd is None:
            f.append(Finding("error", "system-sysdef-missing", f"{name}: {plat} is not in the simulation model set "
                             "(users guide 71.4)"))
            continue
        if sd is not None and not sd.allows(repository):
            f.append(Finding("error", "system-not-allowed", f"{name}: {sd.path} allows "
                             f"{', '.join(sd.repositories)}, not this platform's {repository} (users guide 71.1.4)"))
        for el in cs:
            pname = el.get("paramName") or ""
            if not pname.endswith("art-part-type") or not (el.text or "").strip().lstrip("-").isdigit():
                continue
            code = int(el.text)
            if code < 0:
                continue
            if code not in part_codes:
                f.append(Finding("warning", "entity-system-artpart", f"{name}: {pname} = {code}, but the entity has "
                                 "no such articulated part (inherited from the donor?); bind it to one of the model's "
                                 "parts or remove the system in the manifest's 'systems'"))
            elif catalog is not None and not catalog.accepts(
                    pname, code, sd.params.get(pname, Param(pname, "")).category if sd else None):
                seen = catalog.bindings.get(pname) or (
                    [sd.params[pname].category] if sd and pname in sd.params else [])
                f.append(Finding("warning", "system-artpart-category", f"{name}: {pname} = {code} ({family(code)}); "
                                 f"stock entities bind it to {', '.join(map(str, seen)) or 'other'} parts"))
        pos = cs.find("bodyPosition[@paramName='sensor-position']")
        if pos is not None and box is not None:
            p = [float(pos.get(k, 0)) for k in ("forward", "right", "down")]
            centre, half = box
            if any(abs(p[i] - centre[i]) > half[i] * 1.05 + 1.0 for i in range(3)):
                f.append(Finding("warning", "system-position-outside", f"{name}: sensor-position "
                                 f"{tuple(round(v, 2) for v in p)} is outside the bounding volume"))
    if has_weapon and not has_sensor:
        f.append(Finding("warning", "system-weapon-no-sensor", "the entity has weapons but no sensor; it cannot "
                         "find targets on its own (users guide 66.4, 73.1)"))
    return f


def _bounding_box(so: ET.Element):
    """(centre, half extents) of the .entity bounding volume, body frame (forward, right, down)."""
    bv = so.find("boundingVolume")
    dims = bv.find("dimensions") if bv is not None else None
    if dims is None:
        return None
    off = bv.find("offset")
    centre = tuple(float(off.get(k, 0)) for k in ("forward", "right", "down")) if off is not None else (0, 0, 0)
    half = tuple(float(dims.get(k, 0)) / 2 for k in ("length", "width", "height"))
    return centre, half


def check_ammunition(so: ET.Element, catalog: Catalog | None, known: set[str]) -> list[Finding]:
    """Every ammunition a weapon lists must be in ammunitionParams.mtl (users guide 73.2.1)."""
    f: list[Finding] = []
    if catalog is None or not known:
        return f
    for cs in system_elements(so):
        sd = catalog.get(cs.get("platform", ""))
        for ammo in (sd.ammunition if sd else {}):
            if ammo not in known:
                f.append(Finding("warning", "system-ammunition-unknown", f"{cs.get('systemName')}: {sd.path} lists "
                                 f"'{ammo}', which is not in ammunitionParams.mtl (users guide 73.2.1)"))
    return f


def ammunition_names(mtl_text: str) -> set[str]:
    """Entry names of an ammunitionParams.mtl: the lists inside the top-level (ammunition-params ...)."""
    from . import mtl
    return {e.name for e in mtl.scan(mtl_text, 1) if e.depth == 1}


# ------------------------------------------------------------------ drafting systems from the model
RADAR_SENSOR = re.compile(r"radar")                   # active / passive / airborne-targeting radar, warning receiver
EYE_SENSOR = re.compile(r"^(visual|infrared)-")       # visual-sensor, infrared-sensor (not gimbaled: bound to 15424/15744)
RADAR_PARTS = (5376, 6976)                            # primary / secondary radar families (ACM 21.7)
EYE_NODES = ("socket_bridge_eye", "station__helm")    # where the model's crew looks out (Blender exports)
BIND_ORDER = ("rot-turret-art-part-type", "turret-art-part-type", "elevating-turret-art-part-type",
              "gun-art-part-type", "launcher-art-part-type")
GENERIC_WORDS = {"gun", "guns", "launcher", "launchers", "missile", "missiles", "mm", "naval", "navalgun", "weapon",
                 "weapons", "machine", "cannon", "rocket", "system", "fwd", "aft", "default", "mount", "vertical",
                 "air", "sea", "ship", "anti", "the", "and", "with", "rolling", "airframe", "rim", "ciws"}


@dataclass
class Chain:
    """A weapon mount in the model: a turret/gun/launcher part with the weapon parts beneath it, top first."""
    nodes: list[str]
    codes: list[int]

    @property
    def top(self) -> str:
        return self.nodes[0]


def weapon_chains(parts: list[dict]) -> list[Chain]:
    by_node = {p["node"]: p for p in parts}
    weapon = {n for n, p in by_node.items() if is_weapon_part(int(p["dis_part"]))}
    kids: dict[str | None, list[str]] = defaultdict(list)
    for n in weapon:
        kids[by_node[n].get("parent")].append(n)
    chains = []
    for top in sorted(n for n in weapon if by_node[n].get("parent") not in weapon):
        order, todo = [], [top]
        while todo:
            n = todo.pop(0)
            order.append(n)
            todo += sorted(kids.get(n, []))
        chains.append(Chain(order, [int(by_node[n]["dis_part"]) for n in order]))
    return chains


def assign(params: list[str], chain: Chain, catalog: Catalog, sd: Sysdef | None) -> dict[str, str] | None:
    """Bind each art-part parameter to a different part of the chain, top-down in BIND_ORDER, where stock
    entities bind that parameter to that kind of part. None when the chain can't carry the system."""
    params = sorted(params, key=lambda p: BIND_ORDER.index(p) if p in BIND_ORDER else len(BIND_ORDER))

    def rec(i: int, start: int) -> dict | None:
        if i == len(params):
            return {}
        cat = sd.params[params[i]].category if sd and params[i] in sd.params else None
        for j in range(start, len(chain.nodes)):
            if catalog.accepts(params[i], chain.codes[j], cat):
                rest = rec(i + 1, j + 1)
                if rest is not None:
                    return {params[i]: chain.nodes[j], **rest}
        return None
    return rec(0, 0)


def _tokens(sd: Sysdef) -> set[str]:
    words = re.split(r"[^a-z]+", (Path(sd.path).stem + " " + sd.name).lower())
    return {w for w in words if len(w) >= 3 and w not in GENERIC_WORDS}


def _node_words(node: str) -> set[str]:
    from .draft_manifest import _words
    w = _words(node.split("__")[-1] if "__" in node else node).split()
    return set(w) | {a + b for a, b in zip(w, w[1:])}


def _donor_art(cs: ET.Element) -> dict[str, int]:
    out = {}
    for el in cs:
        pname = el.get("paramName") or ""
        if pname.endswith("art-part-type") and pname != "parent-art-part-type" \
                and (el.text or "").strip().lstrip("-").isdigit() and int(el.text) >= 0:
            out[pname] = int(el.text)
    return out


def is_radar_part(code: int) -> bool:
    return any(b <= code < b + 320 for b in RADAR_PARTS)


def _need(params: list[str], sd: Sysdef | None) -> list[str]:
    return [sd.params[p].category if sd and p in sd.params and sd.params[p].category
            else p.replace("-art-part-type", "") for p in params]


def draft_systems(donor_xml: str, parts: list[dict], nodes: dict, catalog: Catalog, repository: str | None,
                  group: str) -> tuple[list[dict], list[str]]:
    """Manifest `systems` entries that fit the donor's systems to this model, each with its evidence in
    `reason`, and notes on what could not be fitted. `parts` are manifest articulated_parts; `nodes` the glb
    nodes (their pivots pick the highest radar). Systems left out stay as the donor has them."""
    entries: list[dict] = []
    notes: list[str] = []
    so = ET.fromstring(donor_xml).find("simObject")
    css = system_elements(so) if so is not None else []
    names = {cs.get("systemName") for cs in css}
    codes = {int(p["dis_part"]) for p in parts}

    # sensors: where the model sees from (sensor-position feeds sensor-offset, users guide 71.4.2, 72.1.2)
    radars = sorted((p for p in parts if is_radar_part(int(p["dis_part"]))),
                    key=lambda p: -nodes.get(p["node"], {}).get("pivot", [0, 0, 0])[2])
    radar_at = radars[0]["node"] if radars else ("socket_radar" if "socket_radar" in nodes else None)
    eye_at = next((n for n in EYE_NODES if n in nodes), None)
    sensors_kept = 0
    for cs in css:
        sd = catalog.get(cs.get("platform", ""))
        if not sd or not sd.is_sensor:
            continue
        sensors_kept += 1
        stem = Path(sd.path).stem
        if "sensor-position" not in sd.params:
            continue
        if RADAR_SENSOR.search(stem) and radar_at:
            where = f"radar part {radar_at}" if radars else f"{radar_at}, the model's radar socket"
            entries.append({"system": cs.get("systemName"), "position_from": radar_at,
                            "reason": f"{stem} at the {where}, not the donor's position"})
        elif EYE_SENSOR.search(stem) and eye_at:
            entries.append({"system": cs.get("systemName"), "position_from": eye_at,
                            "reason": f"{stem} at {eye_at}, where the crew looks out, not the donor's position"})
        else:
            notes.append(f"{cs.get('systemName')} ({stem}): the model has no location for it; the donor's "
                         "sensor-position is kept")

    # weapons the donor brings: keep, rebind to this model's parts, or remove
    free = weapon_chains(parts)
    weapons = 0
    for cs in css:
        sd = catalog.get(cs.get("platform", ""))
        if not (sd and sd.is_weapon):
            continue
        name, stem = cs.get("systemName"), Path(sd.path).stem
        if sd.fixed_codes and not set(sd.fixed_codes) <= codes:
            entries.append({"system": name, "remove": True,
                            "reason": f"{stem} drives fixed parts {sd.fixed_codes}; the model lacks "
                                      f"{sorted(set(sd.fixed_codes) - codes)}"})
            continue
        art = _donor_art(cs)
        params = list(art) or list(sd.art_params)
        if not params or (art and set(art.values()) <= codes):
            weapons += 1                      # no parts, or the model has the donor's: keep as it is
            free = [ch for ch in free if not set(ch.codes) & set(art.values())]
            continue
        fit = next(((ch, b) for ch in free if (b := assign(params, ch, catalog, sd))), None)
        if fit:
            ch, bind = fit
            free.remove(ch)
            weapons += 1
            entries.append({"system": name, "bind": bind, "reason": f"{stem}: the donor's parts "
                            f"{sorted(art.values())} rebound to this model's {ch.top}"})
        else:
            entries.append({"system": name, "remove": True,
                            "reason": f"{stem} needs {' + '.join(_need(params, sd))} parts (the donor's "
                                      f"{sorted(art.values())}); the model has no free mount that carries them"})

    # the model's own weapon mounts: a stock weapon the node is named after, if the mount can carry it
    n = 1
    for ch in free:
        words = _node_words(ch.top)
        named = sorted((sd for sd in catalog.sysdefs.values()
                        if sd.is_weapon and sd.allows(repository) and _tokens(sd) & words),
                       key=lambda sd: (-catalog.used_in(sd.path, group), sd.path))
        fitting = [(sd, b) for sd in named if sd.art_params and not sd.fixed_codes
                   and (b := assign(list(sd.art_params), ch, catalog, sd))]
        if fitting:
            sd, bind = fitting[0]
            while f"weapon-{n}" in names:
                n += 1
            name = f"weapon-{n}"
            names.add(name)
            params = {var: v for var in sd.ammunition.values()
                      if var and (v := catalog.typical_int(sd.path, var)) is not None}
            entries.append({"system": name, "sysdef": sd.path, "bind": bind, **({"params": params} if params else {}),
                            "reason": f"{ch.top} is named like {Path(sd.path).stem} ('{sd.name}'), used by "
                                      f"{catalog.used_in(sd.path, group)} stock entities of this kind"
                                      + ("; ammunition counts are the stock median" if params else "")})
            weapons += 1
        elif named:
            sd = named[0]
            notes.append(f"{ch.top}: {Path(sd.path).stem} fits by name but needs "
                         f"{' + '.join(_need(list(sd.art_params), sd))} parts, and the mount has "
                         f"{' + '.join(family(c) for c in ch.codes)}. Add the missing joint beneath it in Blender "
                         "and draft again, or add the system under 'systems' by hand")
        else:
            notes.append(f"{ch.top}: no stock weapon is named like it; add one under 'systems' by hand "
                         "(the Simulation Object Editor's Add System dialog lists them, users guide 66.4.1)")

    # a weapon needs a sensor to find targets (users guide 66.4, 73.1)
    if weapons and not sensors_kept:
        sensors = sorted((sd for sd in catalog.sysdefs.values() if sd.is_sensor and sd.allows(repository)
                          and "sensor-position" in sd.params),
                         key=lambda sd: (-catalog.used_in(sd.path, group), sd.path))
        if sensors:
            sd = sensors[0]
            at = radar_at if RADAR_SENSOR.search(Path(sd.path).stem) else eye_at
            name = "sensor" if "sensor" not in names else "sensor-weapons"
            entries.append({"system": name, "sysdef": sd.path, **({"position_from": at} if at else {}),
                            "reason": f"the weapons need a sensor (users guide 66.4); {Path(sd.path).stem} is the "
                                      f"most common on stock entities of this kind "
                                      f"({catalog.used_in(sd.path, group)})"})
    return entries, notes
