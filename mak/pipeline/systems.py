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
import math
import json
import re
import statistics
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from itertools import combinations
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
    # the parts its components drive (UG 66.3: an entity's parts come from its components and its model):
    # {"component", "entry", "code" (literal) or "param" ($x-art-part-type), "limits" {name: value}}
    art_entries: list[dict] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)  # literal variable bindings, e.g. default-max-elevation

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


LIMIT_KEYS = ("min-azimuth", "max-azimuth", "max-azimuth-rate", "min-elevation", "max-elevation",
              "max-elevation-rate", "min-rotation", "max-rotation", "max-rotation-rate", "max-extension",
              "max-extension-rate")
_NUM = re.compile(r"-?\d+(\.\d*)?([eE]-?\d+)?$")


def _component_entries(tree, comp: str | None = None, out: list | None = None) -> list[dict]:
    """Art-part list entries with the component that holds them: the nearest list that is a component
    definition (it has variable-bindings or create-component), e.g. phalanx's `main-gun-joy` -> `primary-gun`."""
    out = [] if out is None else out
    if not isinstance(tree, list):
        return out
    if tree and isinstance(tree[0], str) and any(
            isinstance(x, list) and x and x[0] in ("variable-bindings", "create-component") for x in tree[1:]):
        comp = str(tree[0])
    if tree and tree[0] in ("default-art-part-list", "art-part-list"):
        for e in tree[1:]:
            if not (isinstance(e, list) and e and isinstance(e[0], str)):
                continue
            t = _values(first(e, "art-part-type"))
            t = str(t[0]) if t else ""
            limits = {}
            for k in LIMIT_KEYS:
                f = next((x for x in e[1:] if isinstance(x, list) and x and x[0] == k), None)
                if f is None:
                    continue
                v = _values(f)
                default = _values(first(f, "default"))
                limits[k] = [str(x) for x in v[:1]] + [str(x) for x in default[:1]]
            plist = next((x for x in e[1:] if isinstance(x, list) and x and x[0] == "art-part-param-list"), None)
            out.append({"component": comp, "entry": str(e[0]),
                        "code": int(t) if re.fullmatch(r"\d+", t) else None,
                        "param": t.lstrip("$") if t.startswith("$") else None, "limits": limits,
                        "fixed": plist is not None and len(plist) == 1})   # empty param list: no motion
        return out
    for x in tree:
        _component_entries(x, comp, out)
    return out


def _literal_values(tree) -> dict[str, float]:
    """(DtRwReal (name 1.48)) and the like: literal variable bindings."""
    out = {}
    for f in _all_lists(tree):
        if f and isinstance(f[0], str) and f[0].startswith("DtRw"):
            for b in f[1:]:
                if isinstance(b, list) and len(b) == 2 and isinstance(b[0], str) and _NUM.match(str(b[1])):
                    out[str(b[0])] = float(b[1])
    return out


def _resolve(token: str, values: dict[str, float], depth: int = 0):
    if _NUM.match(token):
        return float(token)
    if token.startswith("$") and depth < 5 and token[1:] in values:
        return values[token[1:]]
    return None


def read_sysdef(path: Path, systems_dir: Path, _seen: set | None = None, _top: bool = True) -> Sysdef:
    """One .sysdef with its templates (`(system-definition (filename ...))`) folded in. The template's
    meta-data parameters count unless the sysdef sets `merge-with-sub-systems False` (users guide 73.1.2).
    Art-part entries come from the templates too; their limits are resolved through the sysdef's own variable
    bindings first (phalanx sets default-max-elevation 1.48 for the template's max-elevation)."""
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
    sd.art_entries = _component_entries(tree)
    sd.values = _literal_values(tree)
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
        sub = read_sysdef(ipath, systems_dir, _seen, _top=False)
        sd.art_entries += sub.art_entries
        for k, v in sub.values.items():
            sd.values.setdefault(k, v)
        if merge:
            for k, p in sub.params.items():
                sd.params.setdefault(k, p)
        sd.categories = sd.categories or sub.categories
        sd.repositories = sd.repositories or sub.repositories
        sd.fixed_codes += sub.fixed_codes
        for k, v in sub.ammunition.items():
            sd.ammunition.setdefault(k, v)
    sd.fixed_codes = sorted(set(sd.fixed_codes))
    if _top:
        for e in sd.art_entries:
            resolved = {}
            for k, toks in e["limits"].items():
                v = next((r for t in toks if (r := _resolve(t, sd.values)) is not None), None)
                if v is not None:
                    resolved[k] = v
            e["limits"] = resolved
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
_REAL = re.compile(r'<real paramName="([^"]+)">(-?[\d.eE+-]+)</real>')


def class_of(group: str, military: bool) -> str:
    """A usage class: the usage group plus military or civilian, e.g. '1:3|military' (stock military ships)."""
    return f"{group}|{'military' if military else 'civilian'}"


def is_military(entity_text: str) -> bool:
    """A weapon system or the "Military" GUI category, as draft_manifest.suggest_donors judges donors."""
    cats = re.search(r'paramName="gui-categories">([^<]*)', entity_text)
    return bool(re.search(r'<componentSystem[^>]*platform="[@$]\(system-dir\)[\/]weapons[\/]', entity_text))         or bool(cats and '"Military"' in cats.group(1))


def platform_rel(platform: str) -> str:
    """'@(system-dir)\\weapons\\x.sysdef' -> 'weapons/x.sysdef' (catalog key, lower case)."""
    return re.sub(r"^[@$]\(system-dir\)[\\/]", "", platform).replace("\\", "/").lower()


@dataclass
class Catalog:
    sysdefs: dict[str, Sysdef]                  # key: platform_rel()
    usage: dict[str, dict]                      # key -> {"groups": {group: n}, "ints": {param: median}}
    repositories: dict[str, str]                # platform .ope file name -> state repository (parameter-type)
    bindings: dict[str, dict] = field(default_factory=dict)   # art-part parameter -> {family: stock uses}
    class_sizes: dict[str, int] = field(default_factory=dict)  # usage class -> stock entities in it

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

    def typical_params(self, key: str) -> dict:
        """Median int and real parameters of stock entities using this sysdef (part bindings left out)."""
        u = self.usage.get(platform_rel(key), {})
        sd = self.get(key)
        out = {**u.get("reals", {}), **u.get("ints", {})}
        return {k: v for k, v in sorted(out.items())
                if not k.endswith("art-part-type") and (sd is None or not sd.params or k in sd.params)}

    def share(self, key: str, cls: str) -> float:
        n = self.class_sizes.get(cls, 0)
        return self.usage.get(platform_rel(key), {}).get("classes", {}).get(cls, 0) / n if n else 0.0

    def preferred_family(self, param: str) -> str | None:
        """The part family stock entities bind this parameter to most."""
        seen = self.bindings.get(param) or {}
        if isinstance(seen, list):                    # older or hand-made catalogs: families without counts
            seen = {f: len(seen) - i for i, f in enumerate(seen)}
        fams = [f for f in sorted(seen, key=lambda f: -seen[f]) if f in CATEGORY_FAMILIES]
        return fams[0] if fams else None

    def to_json(self) -> dict:
        return {"version": CATALOG_VERSION, "sysdefs": {k: asdict(v) for k, v in self.sysdefs.items()},
                "usage": self.usage, "repositories": self.repositories, "bindings": self.bindings,
                "class_sizes": self.class_sizes}

    @classmethod
    def from_json(cls, d: dict) -> "Catalog":
        if d.get("version") != CATALOG_VERSION:
            raise ValueError("old catalog")
        sysdefs = {}
        for k, v in d["sysdefs"].items():
            v = dict(v)
            v["params"] = {n: Param(**p) for n, p in v["params"].items()}
            sysdefs[k] = Sysdef(**v)
        return cls(sysdefs, d["usage"], d["repositories"], d["bindings"], d["class_sizes"])


CATALOG_VERSION = 3


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
    classes: dict[str, Counter] = defaultdict(Counter)
    class_sizes: Counter = Counter()
    ints: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    reals: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    bindings: dict[str, Counter] = defaultdict(Counter)
    for e in sorted(vrfsim.glob("*.entity")):
        s = e.read_text(encoding="utf-8", errors="replace")
        ot = re.search(r'objectType="([^"]+)"', s)
        g = group_of(ot.group(1)) if ot else None
        if not g:
            continue
        cls = class_of(g, is_military(s))
        class_sizes[cls] += 1
        seen_here = set()
        for m in _CS.finditer(s):
            key = platform_rel(m.group(2))
            groups[key][g] += 1
            if key not in seen_here:
                classes[key][cls] += 1
                seen_here.add(key)
            for name, v in _INT.findall(m.group(4) or ""):
                ints[key][name].append(int(v))
                if name.endswith("art-part-type") and name != "parent-art-part-type" and int(v) > 0:
                    bindings[name][family(int(v))] += 1
            for name, v in _REAL.findall(m.group(4) or ""):
                try:
                    reals[key][name].append(float(v))
                except ValueError:
                    pass
    usage = {k: {"groups": dict(groups[k]), "classes": dict(classes[k]),
                 "ints": {n: int(statistics.median(v)) for n, v in ints[k].items() if v},
                 "reals": {n: round(statistics.median(v), 6) for n, v in reals[k].items() if v}} for k in groups}
    return Catalog(sysdefs, usage, repos, {k: dict(v) for k, v in bindings.items()}, dict(class_sizes))


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


# ------------------------------------------------------------------ parts and the components that drive them
def sim_only_parts(manifest: dict, parts: list) -> tuple[list, list[Finding]]:
    """Parts the manifest's `systems` add to the .entity only (`add_parts`; users guide 66.3.1), as artparts.Part
    with node `<mount node>#<code>` (the mount's pivot), and a warning for each: the model has no joint for it,
    so it never shows that motion (a fallback for models built without the editor's articulation roles; Q20)."""
    from .artparts import Part
    out, f = [], []
    by_code = {p.code: p.node for p in parts}
    for e in manifest.get("systems") or []:
        for a in e.get("add_parts") or []:
            parent = a.get("parent")
            pnode = by_code.get(parent) if isinstance(parent, int) else parent
            if pnode is None:
                f.append(Finding("error", "part-sim-only-parent", f"{e['system']}: add_parts {a.get('code')} has parent "
                                 f"{parent!r}, which is neither a manifest part node nor a part added before it"))
                continue
            mount = pnode.split("#")[0]
            node = f"{mount}#{int(a['code'])}"
            part = Part(node, node, int(a["code"]), pnode, a.get("motion") or "elevation", float(a.get("min", 0)),
                        float(a.get("max", 0)), float(a.get("max_rate", 0)), False)
            out.append(part)
            by_code[part.code] = node
            label = artparts.CODES.get(part.code, ("", "part", ""))[1]
            if part.motion == "fixed":
                f.append(Finding("info", "part-sim-only-fixed", f"{mount}: part {part.code} ({label}) for "
                                 f"{e['system']} is a fixed point the stock weapon needs (no motion); it lives in the "
                                 ".entity only, with no visible effect"))
                continue
            what = "pitch" if part.motion == "elevation" else "turn"
            f.append(Finding("warning", "part-sim-only", f"{mount}: no {part.motion} joint in the model; part "
                             f"{part.code} ({label}) for {e['system']} is "
                             f"sim-only. The system moves it in the simulation, but the model will not {what} in 3D. "
                             f"Fix: add the joint under {mount} in Blender (the editor's articulation roles build it) and draft again"))
    return out, f


def part_components(so: ET.Element, catalog: Catalog | None) -> dict[int, tuple[str, str, str]]:
    """Part code -> (systemName, component, art-part entry) for every part a system on the entity drives:
    by a literal code in its sysdef, or by a part parameter bound in the .entity (users guide 66.3)."""
    out: dict[int, tuple[str, str, str]] = {}
    if catalog is None:
        return out
    for cs in system_elements(so):
        sd = catalog.get(cs.get("platform", ""))
        if sd is None:
            continue
        bound = _donor_art(cs)
        for e in sd.art_entries:
            code = e.get("code") if e.get("code") is not None else bound.get(e.get("param") or "")
            if code is not None and code > 0:
                out.setdefault(code, (cs.get("systemName"), e.get("component") or "", e["entry"]))
    return out


def associate_parts(so: ET.Element, catalog: Catalog | None) -> list[Finding]:
    """Name each part a system drives after that component's art-part entry, `<entry>-<code>`, as stock entities
    do (1709 stock descriptors, e.g. 2S1 `azimuth-mount-4096`); a repeated name gets `-<code>` again (stock
    A400M `boom-extension-8192-8192`). Reports which component drives each part, and the parts none drives."""
    f: list[Finding] = []
    lst = so.find("DtSimArtPartDescriptorList")
    if lst is None or catalog is None:
        return f
    owners = part_components(so, catalog)
    descs = lst.findall("DtSimArtPartDescriptor")
    taken = {d.get("paramName") for d in descs
             if int((d.findtext("int[@paramName='art-part-type']") or "0").strip() or 0) not in owners}
    lines, alone = [], []
    for d in descs:
        code = int((d.findtext("int[@paramName='art-part-type']") or "0").strip() or 0)
        if code not in owners:
            alone.append(code)
            continue
        system, comp, entry = owners[code]
        name = f"{entry}-{code}"
        while name in taken:
            name += f"-{code}"
        taken.add(name)
        d.set("paramName", name)
        lines.append(f"{code} -> {system} / {comp} ({name})")
    if lines:
        f.append(Finding("info", "part-component", "parts driven by a system component: " + "; ".join(lines)))
    if alone:
        f.append(Finding("info", "part-visual-only", "no system component drives part(s) "
                         + ", ".join(map(str, alone)) + ": they move in the model only (radars, rudders and deck "
                         "elevators have no stock system)"))
    return f


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
    owned = part_components(so, catalog)
    loose = [int(t) for d in so.findall("DtSimArtPartDescriptorList/DtSimArtPartDescriptor")
             if (t := (d.findtext("int[@paramName='art-part-type']") or "").strip()).isdigit()
             and is_weapon_part(int(t)) and int(t) not in owned]
    if catalog is not None and loose:
        f.append(Finding("info", "system-part-unowned", f"weapon part(s) {', '.join(map(str, loose))} have no weapon "
                         "system driving them: they turn in the model only (add a weapon under 'systems')"))
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


# Stock weapons that stand in for a mount whose own weapon the install lacks (reported as a stand-in).
# Sea Sparrow (RIM-7 / ESSM, Mk 29 trainable box launcher): the install has no such munition; RAM (RIM-116) is the
# trainable launcher it has (open question Q22).
STAND_INS = [
    (re.compile(r"\b(sea ?sparrow|seasparrow|essm|rim ?7|nsssm)\b"), "weapons/ram-missile-launcher.sysdef", "Sea Sparrow"),
]
BASELINE_SHARE = 0.5          # a system at least this share of the class has is part of the class baseline
AZIMUTH_PARAMS = ("turret-art-part-type", "rot-turret-art-part-type")


def baseline(catalog: Catalog, cls: str, repository: str | None, folders=("sensors", "other")) -> list[str]:
    """Sysdefs (catalog keys) that at least BASELINE_SHARE of the stock class has, in the given folders."""
    return sorted((k for k, sd in catalog.sysdefs.items()
                   if sd.folder in folders and sd.allows(repository) and catalog.share(k, cls) >= BASELINE_SHARE),
                  key=lambda k: (-catalog.share(k, cls), k))


def most_common(catalog: Catalog, cls: str, repository: str | None, folder: str) -> str | None:
    keys = [k for k, sd in catalog.sysdefs.items()
            if sd.folder == folder and sd.allows(repository) and catalog.share(k, cls) > 0]
    return max(keys, key=lambda k: (catalog.share(k, cls), k)) if keys else None


def part_owners(catalog: Catalog, repository: str | None) -> dict[int, list[str]]:
    """Part code -> sysdefs whose components drive that literal code (periscope 2048, folding wing 7584 ...)."""
    out: dict[int, list[str]] = defaultdict(list)
    for k, sd in catalog.sysdefs.items():
        if not sd.allows(repository):
            continue
        for e in sd.art_entries:
            if e.get("code") is not None and k not in out[e["code"]]:
                out[e["code"]].append(k)
    return out


def _entry_for(sd: Sysdef, param: str) -> dict | None:
    return next((e for e in sd.art_entries if e.get("param") == param), None)


def _sim_only_part(sd: Sysdef, param: str, parent, used: set[int], catalog: Catalog, mount: str) -> dict | None:
    """A part the model lacks, for the .entity only (users guide 66.3.1): the next free code of the family stock
    entities bind `param` to most, limits from the sysdef component that drives it."""
    fam = catalog.preferred_family(param) or (sd.params[param].category if param in sd.params else None)
    if fam not in CATEGORY_FAMILIES:
        return None
    code = next((b + 32 * i for b in CATEGORY_FAMILIES[fam] for i in range(10) if b + 32 * i not in used), None)
    if code is None:
        return None
    used.add(code)
    entry = _entry_for(sd, param) or {}
    if entry.get("fixed"):            # e.g. RAM's launch point: no motion in the stock component either
        return {"code": code, "parent": parent, "motion": "fixed", "min": 0.0, "max": 0.0, "max_rate": 0.0,
                "reason": f"the stock {entry.get('entry', param)} is a fixed point (no motion) and the model has no "
                          f"part for it under {mount}"}
    motion = "azimuth" if param in AZIMUTH_PARAMS else "elevation"
    lo, hi, rate = artparts.DEFAULT_LIMITS[motion]
    lim = entry.get("limits", {})

    def deg(r: float) -> float:                                      # sysdef limits are radians
        return round(math.degrees(r), 3)
    lo = deg(lim[f"min-{motion}"]) if f"min-{motion}" in lim else lo
    hi = deg(lim[f"max-{motion}"]) if f"max-{motion}" in lim else hi
    rate = deg(lim[f"max-{motion}-rate"]) if f"max-{motion}-rate" in lim else rate
    if hi <= lo:                                                     # a vertical launcher's fixed 0..0
        lo, hi = artparts.DEFAULT_LIMITS[motion][:2]
    return {"code": code, "parent": parent, "motion": motion, "min": lo, "max": hi, "max_rate": rate,
            "reason": f"model has no {'azimuth' if motion == 'azimuth' else 'elevating'} joint for "
                      f"{param.replace('-art-part-type', '')} under {mount}"}


def assign_partial(sd: Sysdef, chain: Chain, catalog: Catalog, used: set[int]):
    """Bind the sysdef's part parameters to the chain where it can (top-down, BIND_ORDER) and add sim-only parts
    for the rest. (bind, add_parts) or None. The top parameter must bind to the mount itself."""
    params = sorted(sd.art_params, key=lambda p: BIND_ORDER.index(p) if p in BIND_ORDER else len(BIND_ORDER))
    best = None
    for k in range(len(params), 0, -1):
        for subset in combinations(params, k):
            if subset[0] != params[0]:
                continue
            b = assign(list(subset), chain, catalog, sd)
            if b:
                best = b
                break
        if best:
            break
    if not best:
        return None
    bind, adds, parent = {}, [], None
    for prm in params:
        if prm in best:
            bind[prm] = best[prm]
            parent = best[prm]
            continue
        part = _sim_only_part(sd, prm, parent, used, catalog, chain.top)
        if part is None:
            return None
        adds.append(part)
        bind[prm] = part["code"]
        parent = part["code"]
    return bind, adds


def draft_systems(donor_xml: str, parts: list[dict], nodes: dict, catalog: Catalog, repository: str | None,
                  group: str, military: bool | None = None) -> tuple[list[dict], list[str]]:
    """Manifest `systems` entries that fit the donor's systems to this model and add the ones it should have,
    each with its evidence in `reason`, and notes on what could not be done. `parts` are manifest
    articulated_parts; `nodes` the glb nodes (pivots; sockets and stations place sensors). `military` picks the
    stock class (default: the model has weapon parts or the donor is military). Systems left out stay as the
    donor has them."""
    entries: list[dict] = []
    notes: list[str] = []
    so = ET.fromstring(donor_xml).find("simObject")
    css = system_elements(so) if so is not None else []
    names = {cs.get("systemName") for cs in css}
    present = {platform_rel(cs.get("platform", "")) for cs in css}
    codes = {int(p["dis_part"]) for p in parts}
    used = set(codes)
    if military is None:
        military = any(is_weapon_part(c) for c in codes) or is_military(donor_xml)
    cls = class_of(group, military)
    what = f"stock {'military' if military else 'civilian'} entities of this kind"

    def new_name(prefix: str) -> str:
        n, name = 1, prefix
        while name in names:
            n += 1
            name = f"{prefix}-{n}"
        names.add(name)
        return name

    def add(key: str, prefix: str, reason: str, **extra) -> dict:
        sd = catalog.get(key)
        params = catalog.typical_params(key)
        e = {"system": new_name(prefix), "sysdef": sd.path if sd else key, **extra,
             **({"params": params} if params else {}), "reason": reason}
        entries.append(e)
        present.add(platform_rel(key))
        return e

    # sensors: where the model sees from (sensor-position feeds sensor-offset, users guide 71.4.2, 72.1.2)
    radars = sorted((p for p in parts if is_radar_part(int(p["dis_part"]))),
                    key=lambda p: -nodes.get(p["node"], {}).get("pivot", [0, 0, 0])[2])
    radar_at = radars[0]["node"] if radars else ("socket_radar" if "socket_radar" in nodes else None)
    eye_at = next((n for n in EYE_NODES if n in nodes), None)

    def sensor_at(sd: Sysdef) -> tuple[str | None, str]:
        stem = Path(sd.path).stem
        if RADAR_SENSOR.search(stem) and radar_at:
            return radar_at, (f"at the radar part {radar_at}" if radars else f"at {radar_at}, the model's radar socket")
        if EYE_SENSOR.search(stem) and eye_at:
            return eye_at, f"at {eye_at}, where the crew looks out"
        return None, ""

    sensors = 0
    for cs in css:
        sd = catalog.get(cs.get("platform", ""))
        if not sd or not sd.is_sensor:
            continue
        sensors += 1
        if "sensor-position" not in sd.params:
            continue
        at, where = sensor_at(sd)
        if at:
            entries.append({"system": cs.get("systemName"), "position_from": at,
                            "reason": f"{Path(sd.path).stem} {where}, not the donor's position"})
        else:
            notes.append(f"{cs.get('systemName')} ({Path(sd.path).stem}): the model has no location for it; the "
                         "donor's sensor-position is kept")

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

    # the model's own weapon mounts: the stock weapon the node is named after (or a reported stand-in). Parts
    # the mount lacks are added to the .entity only (users guide 66.3.1): the weapon aims and fires in the
    # simulation, but the model cannot show what it has no joint for.
    for ch in free:
        words = _node_words(ch.top)
        text = " ".join(sorted(words))
        named = sorted((sd for sd in catalog.sysdefs.values()
                        if sd.is_weapon and sd.allows(repository) and sd.art_params and not sd.fixed_codes
                        and _tokens(sd) & words),
                       key=lambda sd: (-catalog.used_in(sd.path, group), sd.path))
        stand_in = None
        if not named:
            for rx, key, label in STAND_INS:
                sd = catalog.get(key)
                if rx.search(text) and sd and sd.allows(repository):
                    named, stand_in = [sd], label
                    break
        if not named:
            notes.append(f"{ch.top}: no stock weapon is named like it; add one under 'systems' by hand "
                         "(the Simulation Object Editor's Add System dialog lists them, users guide 66.4.1)")
            continue
        sd = next((x for x in named if assign(list(x.art_params), ch, catalog, x)), named[0])
        full = assign(list(sd.art_params), ch, catalog, sd)
        adds: list[dict] = []
        if full:
            bind = full
        else:
            got = assign_partial(sd, ch, catalog, used)
            if not got:
                notes.append(f"{ch.top}: {Path(sd.path).stem} needs {' + '.join(_need(list(sd.art_params), sd))} "
                             "parts and no free codes are left for the missing ones; add the system by hand")
                continue
            bind, adds = got
        stem = Path(sd.path).stem
        why = (f"{ch.top} is a {stand_in} mount; the install has no {stand_in} weapon, so {stem} ('{sd.name}') "
               "stands in (open question Q22)" if stand_in else
               f"{ch.top} is named like {stem} ('{sd.name}'), used by {catalog.used_in(sd.path, group)} stock "
               "entities of this kind")
        moving = [a for a in adds if a.get("motion") != "fixed"]
        fixed = [a for a in adds if a.get("motion") == "fixed"]
        if moving:
            why += ("; sim-only part(s) " + ", ".join(str(a["code"]) for a in moving)
                    + " added because the model has no joint for them (it will not show them moving)")
        if fixed:
            why += ("; fixed sim-only part(s) " + ", ".join(str(a["code"]) for a in fixed)
                    + " (no motion in the stock weapon either; no visible effect)")
        ammo = {var for var in sd.ammunition.values() if var}
        add(sd.path, "weapon", why + ("; ammunition counts are the stock median" if ammo else ""), bind=bind,
            **({"add_parts": adds} if adds else {}))
        if moving:
            notes.append(f"{ch.top}: no elevating joint in the model; {stem} gets sim-only part(s) "
                         f"{', '.join(str(a['code']) for a in moving)}. It aims and fires in the simulation, but the "
                         f"mount will not pitch in 3D. Fix: add the joint under {ch.top} in Blender and draft again")
        weapons += 1

    # one damage and one movement system (users guide 66.4.2-3): the class's most common when the donor has none
    for folder in ("damage", "movement"):
        if not any(k.split("/")[0] == folder for k in present):
            key = most_common(catalog, cls, repository, folder)
            if key:
                shown = "Undamageable" if folder == "damage" else "no movement"
                add(key, folder, f"the donor has no {folder} system (the editor shows {shown}); "
                    f"{Path(key).stem} is the most common on {what} ({catalog.share(key, cls):.0%})")

    # what most stock entities of the class have (users guide 66.4: sensors and additional systems)
    for key in baseline(catalog, cls, repository):
        if key in present:
            continue
        sd = catalog.get(key)
        at, where = sensor_at(sd) if sd.is_sensor else (None, "")
        add(key, "sensor" if sd.is_sensor else "other",
            f"{catalog.share(key, cls):.0%} of {what} have {Path(key).stem}" + (f"; {where}" if at else "")
            + "; parameters are the stock median", **({"position_from": at} if at else {}))
        sensors += sd.is_sensor

    # systems that drive a part the model has (periscope, folding wing, cargo door ...; users guide 66.3)
    owners = part_owners(catalog, repository)
    driven = {e["code"] for k in present if (sd := catalog.get(k)) for e in sd.art_entries if e.get("code")}
    for code in sorted(codes - driven):
        cands = [k for k in owners.get(code, []) if catalog.get(k).folder in ("other", "sensors")]
        moves = [k for k in owners.get(code, []) if catalog.get(k).folder == "movement"]
        if cands:
            key = max(cands, key=lambda k: (catalog.used_in(k, group), k))
            if key not in present:
                comp = next(e["component"] for e in catalog.get(key).art_entries if e.get("code") == code)
                add(key, "other", f"the model has part {code}, which {Path(key).stem} drives (component {comp})")
                driven |= {e["code"] for e in catalog.get(key).art_entries if e.get("code")}
        elif moves:
            notes.append(f"part {code} is driven by movement systems ({', '.join(Path(k).stem for k in moves)}); "
                         "the donor's movement does not drive it, so it stays visual-only unless the movement "
                         "system is changed")

    # a weapon needs a sensor to find targets (users guide 66.4, 73.1)
    if weapons and not sensors:
        cands = sorted((sd for sd in catalog.sysdefs.values() if sd.is_sensor and sd.allows(repository)
                        and "sensor-position" in sd.params),
                       key=lambda sd: (-catalog.used_in(sd.path, group), sd.path))
        if cands:
            sd = cands[0]
            at, where = sensor_at(sd)
            add(sd.path, "sensor", f"the weapons need a sensor (users guide 66.4); {Path(sd.path).stem} is the "
                f"most common on stock entities of this kind ({catalog.used_in(sd.path, group)})",
                **({"position_from": at} if at else {}))
    return entries, notes
