"""The AI editor: Claude Agent SDK with a closed set of tools that edit one project's spec.

Design:
- All tool *logic* lives on `AgentSession` as plain Python (`session.call(name, args)`), so it is
  unit-testable and a fake agent can drive it deterministically.
- `ClaudeAgentRunner` only adapts those tools to the SDK. The model gets no Bash, Read, Write or
  Edit — `tools=[...]` restricts the toolset; `allowed_tools` merely auto-approves.
- Edits change a *working spec*; `rebuild` turns it into a new version. If the model finishes
  with unbuilt edits, the job rebuilds for it, so a reply never describes a change that is not
  on screen.
"""
from __future__ import annotations

import ast
import difflib
import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from .builds import Builds, summarise_change
from .catalog import Catalog, CatalogError
from .specmerge import deep_merge, label_for
from .store import ProjectStore

Log = Callable[[str], None]

PART_RE = re.compile(r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*__[A-Za-z0-9_]+$")
# Objects a crew custom item creates (contract §6): worn clothes or held/attached equipment.
CREW_ITEM_RE = re.compile(r"^(Clothes|Equipment)__[A-Za-z0-9][A-Za-z0-9_]*$")
# Named colours blender/crew/spec.py accepts for equipment (EQUIPMENT_COLORS there).
EQUIPMENT_COLOR_NAMES = ["orange", "navy", "yellow", "white", "red", "black"]
CREW_HELPERS_WITH_NAME = ("garment", "box", "cylinder", "object_from_bmesh")
EVIDENCE_STATUSES = ["Official", "Photographed", "Photo reading", "Inference", "Comparison only",
                     "Secondary lead", "Unverified"]
OVERRIDE_KEYS = {"offset_m": 3, "rotate_deg": 3, "scale": 3, "color": 3}
# Custom ops run inside Blender on Lester's Mac. They exist to make geometry; nothing else.
FORBIDDEN_CODE = [r"\bimport\s+(os|sys|subprocess|socket|shutil|urllib|requests|http|ctypes|pathlib)\b",
                  r"\bfrom\s+(os|sys|subprocess|socket|shutil|urllib|requests|http|ctypes|pathlib)\b",
                  r"__import__", r"\bopen\s*\(", r"\beval\s*\(", r"\bexec\s*\(",
                  r"bpy\.ops\.wm\.(quit|save|open)", r"bpy\.ops\.export", r"\bcompile\s*\("]


class ToolError(Exception):
    pass


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}


def module_helpers(path: Path, alias: str) -> str:
    """Public functions (and UPPERCASE constants) of a Blender helper module, one line each, read
    with ast so the AI's tool docs follow the module without importing bpy. Degrades to a note
    when the file is missing or broken, so a half-written helper never breaks the agent."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return f"(helper module {alias} not readable; use bpy directly)"
    lines = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            a = node.args
            names = [x.arg for x in a.posonlyargs + a.args]
            defaults = [None] * (len(names) - len(a.defaults)) + list(a.defaults)
            parts = [n if d is None else f"{n}={ast.unparse(d)}" for n, d in zip(names, defaults)]
            if a.vararg:
                parts.append("*" + a.vararg.arg)
            elif a.kwonlyargs:
                parts.append("*")
            parts += [k.arg if d is None else f"{k.arg}={ast.unparse(d)}"
                      for k, d in zip(a.kwonlyargs, a.kw_defaults)]
            if a.kwarg:
                parts.append("**" + a.kwarg.arg)
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            lines.append(f"{alias}.{node.name}({', '.join(parts)})" + (f" — {doc[0]}" if doc else ""))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.isupper() and not name.startswith("_"):
                if isinstance(node.value, ast.Dict):
                    keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                    lines.append(f"{alias}.{name}: keys {', '.join(map(str, keys))}")
                else:
                    lines.append(f"{alias}.{name}")
    return "\n".join(lines) or f"(no public helpers found in {alias})"


def me3d_helpers(repo_root: Path) -> str:
    """Public functions in blender/common.py, as one line each, for the add_custom_op docs."""
    return module_helpers(repo_root / "blender" / "common.py", "me3d")


def crew_helpers(repo_root: Path) -> str:
    """Public functions in blender/crew/custom.py, for the add_custom_item docs."""
    return module_helpers(repo_root / "blender" / "crew" / "custom.py", "crew")


def _rgb(v) -> bool:
    return (isinstance(v, list) and len(v) == 3 and
            all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1 for x in v))


def check_code(code: str) -> None:
    """Shared safety and syntax checks for AI-written Blender Python (ship ops and crew items)."""
    for pat in FORBIDDEN_CODE:
        if re.search(pat, code):
            raise ToolError(f"Code rejected: matches forbidden pattern {pat!r}. Geometry only.")
    try:
        compile(code, "<custom_code>", "exec")
    except SyntaxError as e:
        raise ToolError(f"Python syntax error: {e}")


def crew_object_names(code: str) -> list[str]:
    """Object names a crew custom item sets literally: `.name = '...'`, `objects.new('...')`, and the
    first argument of the crew helpers that create an object when it is a full Group__Name."""
    names = re.findall(r"""\.name\s*=\s*['"]([^'"]+)['"]""", code)
    names += re.findall(r"""objects\.new\(\s*['"]([^'"]+)['"]""", code)
    helper = "|".join(CREW_HELPERS_WITH_NAME)
    names += [n for n in re.findall(rf"""\bcrew\.(?:{helper})\(\s*(?:name\s*=\s*)?['"]([^'"]+)['"]""", code)
              if "__" in n]
    return names


def _options_list(value) -> list | None:
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, list):
        return [v.get("id") or v.get("name") if isinstance(v, dict) else v for v in value]
    return None


class AgentSession:
    """One AI turn against one project."""

    def __init__(self, store: ProjectStore, builds: Builds, catalog: Catalog, pid: str,
                 log: Log, repo_root: Path, pin_id: str | None = None, mode: str = "build"):
        self.mode = mode
        self.store, self.builds, self.catalog = store, builds, catalog
        self.pid, self.log, self.repo_root, self.pin_id = pid, log, repo_root, pin_id
        project = store.load(pid)
        self.kind = project["kind"]
        self.base = project["base"]
        self.version = project["version"]
        self.spec = store.spec(pid, self.version) if self.version else store.spec(pid, 1)
        self.built_spec = copy.deepcopy(self.spec)
        self.parts = store.parts(pid, self.version)
        self.report = store.report(pid, self.version) or {}
        self.built_versions: list[int] = []
        self.pin_replied: str | None = None  # status set via reply_to_pin
        self.research_saved = False
        self.tool_calls: list[str] = []
        try:
            self.cat_entry = catalog.entry(self.kind, self.base)
        except CatalogError:
            self.cat_entry = {}

    # ---- plumbing ---------------------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self.spec != self.built_spec

    def tool_specs(self, mode: str) -> list[tuple[str, str, dict]]:
        if mode == "research":
            return [("save_research", RESEARCH_DOC, _obj({"research": {"type": "object"}}, ["research"]))]
        common = [
            ("get_project", "Read the project: spec, parts (path, label, joint, station, bounds), the last "
             "build report, research summary and open pins. Call this first.", _obj({}, [])),
            ("rebuild", "Run Blender on the current spec. Returns ok, errors, warnings, triangles. Call it "
             "after your last change and fix any errors before finishing.", _obj({}, [])),
            ("reply_to_pin", "Answer a pinned comment. status: 'waiting' (you changed something and the "
             "user should check), 'resolved' (no change needed), 'open' (you need the user's input).",
             _obj({"pin_id": {"type": "string"}, "text": {"type": "string"},
                   "status": {"type": "string", "enum": ["waiting", "resolved", "open"]}},
                  ["pin_id", "text", "status"])),
        ]
        if self.kind == "ship":
            return common[:1] + [
                ("set_params", "Patch the builder's params (deep merge; null deletes). Only known params.",
                 _obj({"patch": {"type": "object"}}, ["patch"])),
                ("set_part_override", "Adjust one existing part after the build. Fields (all optional): "
                 "offset_m [x,y,z] metres in the ship frame (+X bow, +Y port, +Z up), rotate_deg [x,y,z], "
                 "scale [x,y,z], color [r,g,b] 0-1, hidden bool. null removes a field; "
                 "override=null removes all overrides for the part.",
                 _obj({"part": {"type": "string"}, "override": {"type": ["object", "null"]}}, ["part", "override"])),
                ("set_joint_limits", "Set the rotation limits of a moving part (one with a joint).",
                 _obj({"part": {"type": "string"}, "min_deg": {"type": "number"},
                       "max_deg": {"type": "number"}}, ["part", "min_deg", "max_deg"])),
                ("add_custom_op", self._custom_op_doc(),
                 _obj({"description": {"type": "string"}, "code": {"type": "string"}}, ["description", "code"])),
                ("remove_custom_op", "Remove a custom op by id.", _obj({"id": {"type": "string"}}, ["id"])),
            ] + common[1:]
        rgb_or_name = {"oneOf": [{"type": "string"}, VEC3]}
        equipment_item = {"oneOf": [
            {"type": "string"},
            {"type": "object", "properties": {"name": {"type": "string"}, "color": rgb_or_name},
             "required": ["name"]}]}
        return common[:1] + [
            ("set_body", "Patch body fields: gender, height_cm, weight, muscle, age_years, skin, hair (a catalog "
             "style), hair_color ([r,g,b] 0..1 recolouring hair and eyebrows, e.g. blonde [0.85, 0.7, 0.4]; "
             "null for natural).", _obj({"patch": {"type": "object"}}, ["patch"])),
            ("set_clothes", "Patch clothes: suit, shoes, hat from the catalog options (null empties the slot, "
             "e.g. suit null when a custom garment replaces the outfit); suit_color, shoes_color, hat_color "
             "[r,g,b] 0..1 tints, or null.", _obj({"patch": {"type": "object"}}, ["patch"])),
            ("set_equipment", "Replace the catalog equipment list. Entries are a catalog name or "
             "{\"name\", \"color\"} with color one of " + ", ".join(EQUIPMENT_COLOR_NAMES) + " or [r,g,b] 0..1.",
             _obj({"equipment": {"type": "array", "items": equipment_item}}, ["equipment"])),
            ("set_animations", "Replace the animation list.",
             _obj({"animations": {"type": "array", "items": {"type": "string"}}}, ["animations"])),
            ("add_custom_item", self._custom_item_doc(),
             _obj({"description": {"type": "string"}, "code": {"type": "string"}}, ["description", "code"])),
            ("remove_custom_item", "Remove a custom item by id.", _obj({"id": {"type": "string"}}, ["id"])),
        ] + common[1:]

    def _custom_op_doc(self) -> str:
        return ("Add geometry no builder param covers. `code` is Python run inside Blender after the build "
                "and overrides, with `bpy`, `mathutils` and helper module `me3d` in scope. Frame: metres, "
                "+X bow, +Y port, +Z up, origin at the waterline amidships. Name every object you create "
                "Group__Part (or Group__Part__Sub), letters/digits/underscore only, e.g. Mast__Horn; "
                "parent it to the part it sits on. Moving parts: origin at the pivot and custom property "
                "me3d_joint. No file, network or OS access (imports of os/sys/subprocess, open(), eval, "
                "exec are rejected). Helpers:\n" + me3d_helpers(self.repo_root))

    def _custom_item_doc(self) -> str:
        return ("Make clothing or equipment the catalog does not have (a singlet, shorts, a torch, a clipboard). "
                "`code` is Python run inside Blender after the human and catalog equipment are built and before "
                "animations bake, with `bpy`, `mathutils` and helper module `crew` in scope. Worn things: "
                "crew.garment over the body regions they cover, so they fit any body and move with every "
                "animation; name them Clothes__<Name>. Held or attached things: build a low-poly mesh, then "
                "crew.hold(obj, hand) or crew.attach(obj, bone); name them Equipment__<Name>. Names use "
                "letters/digits/underscore only, e.g. Clothes__Singlet, Equipment__Torch. Colours via "
                "crew.material. No file, network or OS access (imports of os/sys/subprocess, open(), eval, exec "
                "are rejected). Helpers:\n" + crew_helpers(self.repo_root))

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool. Returns (text for the model, is_error)."""
        self.tool_calls.append(name)
        allowed = {n for n, _, _ in self.tool_specs(self.mode)}
        fn = getattr(self, f"t_{name}", None)
        if fn is None or name not in allowed:
            return f"Unknown tool {name}.", True
        try:
            out = fn(**(args or {}))
        except ToolError as e:
            self.log(f"AI tool {name} refused: {e}")
            return str(e), True
        except TypeError as e:
            return f"Bad arguments for {name}: {e}", True
        self.log(f"AI tool {name}: ok")
        return out if isinstance(out, str) else json.dumps(out, indent=1), False

    # ---- shared tools -----------------------------------------------------------------
    def t_get_project(self) -> dict:
        project = self.store.load(self.pid)
        parts = [{k: p.get(k) for k in ("path", "label", "parent", "joint", "station", "origin_m",
                                        "bounds_min", "bounds_max")} for p in self.parts]
        research = project.get("research") or {}
        return {
            "kind": self.kind, "version": self.version, "spec": self.spec,
            "unbuilt_changes": self.dirty,
            "parts": parts,
            "build_report": {k: self.report.get(k) for k in ("ok", "errors", "warnings", "triangles",
                                                              "bounds_min", "bounds_max")},
            "research_summary": research.get("summary") if research else None,
            "research_open_questions": research.get("open_questions") if research else None,
            "open_pins": [{"id": p["id"], "part": p["part"], "status": p["status"],
                           "thread": [f"{m['who']}: {m['text']}" for m in p["messages"]]}
                          for p in project["pins"] if p["status"] != "resolved"],
            "catalog_docs": {k: self.cat_entry.get(k) for k in ("param_docs", "options") if k in self.cat_entry},
        }

    def t_rebuild(self) -> dict:
        before = self.built_spec
        summary = summarise_change(before, self.spec, "AI edit")
        n, report = self.builds.build(self.pid, copy.deepcopy(self.spec), summary, self.log)
        self.built_versions.append(n)
        if report.get("ok"):
            self.version = n
            self.built_spec = copy.deepcopy(self.spec)
            self.parts = self.store.parts(self.pid, n)
            self.report = report
        return {"ok": bool(report.get("ok")), "version": n, "errors": report.get("errors", []),
                "warnings": report.get("warnings", []), "triangles": report.get("triangles"),
                "note": "Now on screen." if report.get("ok") else
                        "Failed: the previous version stays on screen. Fix the spec and rebuild."}

    def t_reply_to_pin(self, pin_id: str, text: str, status: str) -> str:
        if status not in ("waiting", "resolved", "open"):
            raise ToolError("status must be waiting, resolved or open")
        try:
            self.store.update_pin(self.pid, pin_id, status=status, message=("AI", text))
        except Exception:
            raise ToolError(f"No pin {pin_id!r} on this project.")
        if pin_id == self.pin_id:
            self.pin_replied = status
        return "Reply posted."

    # ---- ship tools -------------------------------------------------------------------
    def _part(self, part: str) -> dict:
        for p in self.parts:
            if p.get("path") == part:
                return p
        near = difflib.get_close_matches(part, [p["path"] for p in self.parts], n=5, cutoff=0.6)
        raise ToolError(f"No part {part!r} in the current build." +
                        (f" Did you mean: {', '.join(near)}?" if near else " Call get_project for the list."))

    def _known_params(self) -> set:
        docs = self.cat_entry.get("param_docs") or {}
        return set(self.spec.get("params", {})) | set(docs if isinstance(docs, dict) else [])

    def t_set_params(self, patch: dict) -> str:
        if self.kind != "ship":
            raise ToolError("set_params is for ship projects.")
        if not isinstance(patch, dict) or not patch:
            raise ToolError("patch must be a non-empty object")
        unknown = sorted(set(patch) - self._known_params())
        if unknown:
            raise ToolError(f"Unknown params {unknown}. Known: {sorted(self._known_params())}. "
                            "For geometry no param covers, use add_custom_op.")
        docs = self.cat_entry.get("param_docs") or {}
        for k, v in patch.items():
            d = docs.get(k) if isinstance(docs, dict) else None
            if isinstance(d, dict) and isinstance(v, (int, float)) and not isinstance(v, bool):
                if "min" in d and v < d["min"] or "max" in d and v > d["max"]:
                    raise ToolError(f"{k}={v} is outside {d.get('min')}..{d.get('max')}")
        self.spec = deep_merge(self.spec, {"params": patch})
        return f"Params updated: {sorted(patch)} (not built yet; call rebuild)."

    def t_set_part_override(self, part: str, override: dict | None) -> str:
        self._part(part)
        if override is None:
            self.spec = deep_merge(self.spec, {"overrides": {part: None}})
            return f"Overrides removed from {part}."
        if not isinstance(override, dict):
            raise ToolError("override must be an object or null")
        for k, v in override.items():
            if k == "hidden":
                if v is not None and not isinstance(v, bool):
                    raise ToolError("hidden must be true or false")
            elif k in OVERRIDE_KEYS:
                if v is not None and not (isinstance(v, list) and len(v) == 3 and
                                          all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
                    raise ToolError(f"{k} must be three numbers")
                if k == "color" and v is not None and not all(0 <= x <= 1 for x in v):
                    raise ToolError("color components are 0..1")
                if k == "scale" and v is not None and not all(x > 0 for x in v):
                    raise ToolError("scale components must be positive")
            else:
                raise ToolError(f"Unknown override field {k!r}; use offset_m, rotate_deg, scale, color, hidden")
        self.spec = deep_merge(self.spec, {"overrides": {part: override}})
        return f"Override set on {part} ({label_for(part)}). Call rebuild."

    def t_set_joint_limits(self, part: str, min_deg: float, max_deg: float) -> str:
        p = self._part(part)
        if not p.get("joint"):
            raise ToolError(f"{part} is not a moving part (no joint).")
        if not (-360 <= min_deg < max_deg <= 360):
            raise ToolError("need -360 <= min_deg < max_deg <= 360")
        self.spec = deep_merge(self.spec, {"joint_limits": {part: {"min_deg": min_deg, "max_deg": max_deg}}})
        return f"Limits on {part} set to {min_deg}..{max_deg} degrees. Call rebuild."

    def t_add_custom_op(self, description: str, code: str) -> str:
        if self.kind != "ship":
            raise ToolError("custom ops are for ship projects.")
        if not description.strip() or not code.strip():
            raise ToolError("description and code are both required")
        check_code(code)
        for name in re.findall(r"""\.name\s*=\s*['"]([^'"]+)['"]""", code):
            if not (PART_RE.match(name) or name.startswith("station__") or name.startswith("socket_")):
                raise ToolError(f"Object name {name!r} breaks the naming rule Group__Part ([A-Za-z0-9_]).")
        ops = list(self.spec.get("custom_ops", []))
        taken = {o.get("id") for o in ops}
        i = 1
        while f"op_{i}" in taken:
            i += 1
        ops.append({"id": f"op_{i}", "description": description, "code": code})
        self.spec = deep_merge(self.spec, {"custom_ops": ops})
        return f"Custom op op_{i} added. Call rebuild to run it."

    def t_remove_custom_op(self, id: str) -> str:
        ops = list(self.spec.get("custom_ops", []))
        kept = [o for o in ops if o.get("id") != id]
        if len(kept) == len(ops):
            raise ToolError(f"No custom op {id!r}. Ids: {[o.get('id') for o in ops]}")
        self.spec = deep_merge(self.spec, {"custom_ops": kept})
        return f"Removed {id}. Call rebuild."

    # ---- crew tools -------------------------------------------------------------------
    def _crew_options(self, slot: str) -> list | None:
        opts = self.cat_entry.get("options") or {}
        if slot in opts:
            return _options_list(opts[slot])
        clothes = opts.get("clothes")
        if isinstance(clothes, dict) and slot in clothes:
            return _options_list(clothes[slot])
        return None

    def _check_crew(self):
        if self.kind != "crew":
            raise ToolError("This tool is for crew projects.")

    def t_set_body(self, patch: dict) -> str:
        self._check_crew()
        docs = self.cat_entry.get("param_docs") or {}
        body_docs = docs.get("body", docs) if isinstance(docs, dict) else {}
        known = set(self.spec.get("body", {})) | set(body_docs) | {"hair", "hair_color"}
        if not isinstance(patch, dict) or not patch:
            raise ToolError("patch must be a non-empty object")
        for k, v in patch.items():
            if k == "hair_color":
                if v is not None and not _rgb(v):
                    raise ToolError("hair_color is [r,g,b] with each value 0..1 (blonde is about "
                                    "[0.85, 0.7, 0.4]), or null")
                continue
            if k not in known:
                raise ToolError(f"Unknown body field {k!r}. Known: {sorted(known)}")
            d = body_docs.get(k)
            if isinstance(d, dict):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if "min" in d and v < d["min"] or "max" in d and v > d["max"]:
                        raise ToolError(f"{k}={v} is outside {d.get('min')}..{d.get('max')}")
                allowed = _options_list(d.get("options") or d.get("values"))
                if allowed and v not in allowed:
                    raise ToolError(f"{k} must be one of {allowed}")
        self.spec = self._set_section("body", patch)
        return "Body updated. Call rebuild."

    def t_set_clothes(self, patch: dict) -> str:
        self._check_crew()
        if not isinstance(patch, dict) or not patch:
            raise ToolError("patch must be a non-empty object")
        for slot, v in patch.items():
            if slot in ("suit_color", "shoes_color", "hat_color"):
                if v is not None and not _rgb(v):
                    raise ToolError(f"{slot} is [r,g,b] with each value 0..1, or null")
                continue
            allowed = self._crew_options(slot)
            if allowed is None and slot not in self.spec.get("clothes", {}):
                raise ToolError(f"Unknown clothes slot {slot!r}")
            if v is not None and allowed is not None and v not in allowed:
                raise ToolError(f"{slot} must be one of {allowed} (or null). For anything else, set the slot "
                                "to null if it is replaced and make the item with add_custom_item.")
        self.spec = self._set_section("clothes", patch)
        return "Clothes updated. Call rebuild."

    def _set_section(self, section: str, patch: dict) -> dict:
        """Set fields of body/clothes keeping explicit nulls. deep_merge deletes a key on None, and
        the Blender side then refills it from its defaults: {"suit": null} would bring the work suit
        back. Here null must mean "empty slot" / "no tint", so it is stored as null."""
        spec = copy.deepcopy(self.spec)
        current = spec.get(section)
        spec[section] = dict(current) if isinstance(current, dict) else {}
        spec[section].update(copy.deepcopy(patch))
        return spec

    def _check_list(self, slot: str, values: list, allow_color: bool = False) -> None:
        if not isinstance(values, list):
            raise ToolError(f"{slot} must be a list")
        names = []
        for v in values:
            if allow_color and isinstance(v, dict):
                extra = set(v) - {"name", "color"}
                if not isinstance(v.get("name"), str) or extra:
                    raise ToolError(f"{slot} entries are a name or {{\"name\", \"color\"}}, got {v!r}")
                c = v.get("color")
                if c is not None and c not in EQUIPMENT_COLOR_NAMES and not _rgb(c):
                    raise ToolError(f"color must be one of {EQUIPMENT_COLOR_NAMES} or [r,g,b] 0..1, got {c!r}")
                names.append(v["name"])
            elif isinstance(v, str):
                names.append(v)
            else:
                raise ToolError(f"{slot} must be a list of names" +
                                (" or {\"name\", \"color\"} objects" if allow_color else ""))
        allowed = self._crew_options(slot)
        bad = [v for v in names if allowed is not None and v not in allowed]
        if bad:
            raise ToolError(f"Not in the catalog: {bad}. Options: {allowed}" +
                            (". For anything else use add_custom_item." if slot == "equipment" else ""))

    def t_set_equipment(self, equipment: list) -> str:
        self._check_crew()
        self._check_list("equipment", equipment, allow_color=True)
        self.spec = deep_merge(self.spec, {"equipment": equipment})
        return "Equipment updated. Call rebuild."

    def t_set_animations(self, animations: list) -> str:
        self._check_crew()
        self._check_list("animations", animations)
        self.spec = deep_merge(self.spec, {"animations": animations})
        return "Animations updated. Call rebuild."

    def t_add_custom_item(self, description: str, code: str) -> str:
        self._check_crew()
        if not isinstance(description, str) or not isinstance(code, str) or not description.strip() or not code.strip():
            raise ToolError("description and code are both required")
        check_code(code)
        for name in crew_object_names(code):
            if not CREW_ITEM_RE.match(name):
                raise ToolError(f"Object name {name!r} breaks the naming rule: Clothes__<Name> for worn things, "
                                "Equipment__<Name> for held or attached things ([A-Za-z0-9_]).")
        items = list(self.spec.get("custom_items", []))
        taken = {o.get("id") for o in items}
        i = 1
        while f"item_{i}" in taken:
            i += 1
        items.append({"id": f"item_{i}", "description": description, "code": code})
        self.spec = deep_merge(self.spec, {"custom_items": items})
        return f"Custom item item_{i} added. Call rebuild to run it."

    def t_remove_custom_item(self, id: str) -> str:
        self._check_crew()
        items = list(self.spec.get("custom_items", []))
        kept = [o for o in items if o.get("id") != id]
        if len(kept) == len(items):
            raise ToolError(f"No custom item {id!r}. Ids: {[o.get('id') for o in items]}")
        self.spec = deep_merge(self.spec, {"custom_items": kept})
        return f"Removed {id}. Call rebuild."

    # ---- research ---------------------------------------------------------------------
    def t_save_research(self, research: dict) -> str:
        errors = validate_research(research)
        if errors:
            raise ToolError("Research not saved. Fix: " + "; ".join(errors))
        research = dict(research, schema="me3d.research/1")
        self.store.set_research(self.pid, research)
        self.research_saved = True
        return "Research saved."


RESEARCH_DOC = (
    "Save the research for this vessel. Object fields:\n"
    "subject (str): the one exact baseline hull/class.\n"
    "summary: {verdict (str, 2-4 plain sentences: can we model it and how well), existing_work (str)}.\n"
    "sources: [{id, title, url, status, what_it_proves}] - status is one of " + ", ".join(EVIDENCE_STATUSES) +
    ". Sister craft are 'Comparison only'.\n"
    "claims: [{claim, value?, unit?, sources: [source ids], status, consequence_for_model}].\n"
    "open_questions: [{question, why_it_matters}].\n"
    "articulation: [{part (Group__Part name), motion, axis?, range_deg?: [min,max], priority: P0|P1|P2, "
    "evidence: [source ids]}] including parts that must NOT move (motion 'fixed')."
)


def validate_research(r: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(r, dict):
        return ["research must be an object"]
    if not isinstance(r.get("subject"), str) or not r["subject"].strip():
        errs.append("subject (the exact baseline hull) is required")
    s = r.get("summary")
    if not isinstance(s, dict) or not isinstance(s.get("verdict"), str) or not s["verdict"].strip():
        errs.append("summary.verdict is required")
    ids = set()
    sources = r.get("sources")
    if not isinstance(sources, list) or not sources:
        errs.append("sources must be a non-empty list")
        sources = []
    for i, src in enumerate(sources):
        if not isinstance(src, dict) or not src.get("id"):
            errs.append(f"sources[{i}] needs an id")
            continue
        ids.add(src["id"])
        if src.get("status") not in EVIDENCE_STATUSES:
            errs.append(f"sources[{i}].status {src.get('status')!r} must be one of {EVIDENCE_STATUSES}")
        if not src.get("what_it_proves"):
            errs.append(f"sources[{i}].what_it_proves is required")
    claims = r.get("claims")
    if not isinstance(claims, list):
        errs.append("claims must be a list")
        claims = []
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or not c.get("claim"):
            errs.append(f"claims[{i}] needs claim text")
            continue
        if c.get("status") not in EVIDENCE_STATUSES:
            errs.append(f"claims[{i}].status must be one of {EVIDENCE_STATUSES}")
        if not c.get("consequence_for_model"):
            errs.append(f"claims[{i}].consequence_for_model is required")
        missing = [x for x in c.get("sources", []) if x not in ids]
        if missing:
            errs.append(f"claims[{i}] cites unknown sources {missing}")
    if not isinstance(r.get("open_questions"), list):
        errs.append("open_questions must be a list (may be empty)")
    art = r.get("articulation")
    if not isinstance(art, list):
        errs.append("articulation must be a list")
    else:
        for i, a in enumerate(art):
            if not isinstance(a, dict) or not a.get("part") or not a.get("motion"):
                errs.append(f"articulation[{i}] needs part and motion")
    return errs


# ---- prompts ----------------------------------------------------------------------------

COMMON_RULES = """\
The user is Lester: ex-navy, the product owner, not an engineer.
- Part names: every part is Group__Part or Group__Part__Sub, letters/digits/underscore only (e.g. Roof__Searchlight). Its label replaces __ with › and _ with a space (Roof › Searchlight). Crew stations are station__<name>. Moving parts carry a joint; their origin is the pivot.
- Frame: metres; +X bow, +Y port, +Z up; ship origin at the waterline amidships.
- Numbers come from the spec, the research, or the user. Never invent a dimension silently: if you must assume one, say so in the reply with the value and why.
- When the message is about a pin, it names the exact part the user pointed at. Work on that part; if a different part is really meant, say so.
- Start with get_project. After your last change call rebuild, read its errors and fix them. A failed build leaves the previous version on screen.
- If the request is unclear or needs a fact you do not have, change nothing and ask one short question.
- Reply in one to three short plain-English sentences: what changed, with numbers, and anything assumed. No code, no jargon, no tool names."""

SHIP_PROMPT = """You edit one ship model for a maritime simulator. Blender builds the model from a JSON spec (me3d.ship_spec/1); you change the spec only through your tools: params (the builder's own knobs), per-part overrides, joint limits, and custom ops (Blender Python, for geometry no knob covers).
""" + COMMON_RULES

CREW_PROMPT = """You edit one human crew member for a maritime simulator. Blender (MPFB2) builds the person from a JSON spec (me3d.crew_spec/1): body sliders, clothes, equipment and animations, plus custom items (Blender Python, for anything the catalog lacks).
- The catalog options shown by get_project are a starting point, not a limit. Make what is asked for:
  - A colour change: the colour fields (hair_color in set_body; suit_color, shoes_color, hat_color in set_clothes; an equipment entry's color).
  - Hair: a style from the options plus hair_color (blonde is about [0.85, 0.7, 0.4]).
  - Clothing not in the catalog (a singlet, shorts, gloves, a cap): add_custom_item with crew.garment over the body regions it covers. If it replaces the outfit (singlet and shorts), set suit to null.
  - Things held or worn on the body (a torch, a clipboard, a rope, a tool belt): add_custom_item, a low-poly mesh with crew.hold or crew.attach.
- Never answer that something is not available when you can make it. If part of a request truly cannot be built (cloth that flows in the wind, real physics), make the closest thing and say plainly what you made instead.
""" + COMMON_RULES

RESEARCH_PROMPT = """You research one real vessel so a 3D model of it can be built for a maritime simulator. The user is Lester: ex-navy, not an engineer.
- Use public sources only (WebSearch, WebFetch). Pick ONE exact baseline hull; sister craft are 'Comparison only'; other classes are not evidence.
- Every source gets an evidence status: Official, Photographed, Photo reading, Inference, Comparison only, Secondary lead, Unverified. Your rating is a judgement, never 'verified'.
- Every claim says what it changes in the model (consequence_for_model). Unknowns go to open_questions, not guesses.
- Propose articulation: what moves (P0 essential .. P2 nice), and what must not move.
- Save with save_research (it validates; fix and resave on errors). Then reply in two or three plain sentences: the verdict and the biggest open question."""


def _lower_first(s: str) -> str:
    """"Small searchlight" -> "small searchlight", but "PC 303 mast" stays as it is."""
    return s[:1].lower() + s[1:] if s[:2].istitle() else s


def _words(keys) -> str:
    return ", ".join(str(k).replace("_", " ") for k in keys)


def describe_tool(name: str, args: dict | None) -> str | None:
    """One plain line for the progress feed: what the AI is doing with this tool call. None when
    something else already logs it (a rebuild logs its own build line)."""
    a = args if isinstance(args, dict) else {}
    patch = a.get("patch") if isinstance(a.get("patch"), dict) else {}
    part = label_for(str(a.get("part", "")))
    if name == "set_part_override":
        override = a.get("override")
        if override is None:
            return f"Resetting {part}"
        return f"Hiding {part}" if isinstance(override, dict) and override.get("hidden") else f"Adjusting {part}"
    simple = {
        "get_project": "Looking at the current model",
        "rebuild": None,
        "reply_to_pin": "Answering the comment",
        "save_research": "Saving the research",
        "set_params": f"Changing {_words(patch)}",
        "set_joint_limits": f"Setting how far {part} can move",
        "add_custom_op": f"Adding {_lower_first(str(a.get('description') or 'new geometry'))}",
        "remove_custom_op": "Removing an added piece",
        "add_custom_item": f"Making {_lower_first(str(a.get('description') or 'a new item'))}",
        "remove_custom_item": "Removing a made item",
        "set_body": f"Changing the body: {_words(patch)}",
        "set_clothes": f"Changing clothes: {_words(patch)}",
        "set_equipment": f"Setting equipment: {_words(a.get('equipment') or ['none'])}",
        "set_animations": f"Setting moves: {_words(a.get('animations') or ['none'])}",
        "WebSearch": f"Searching the web: {a.get('query', '')}",
        "WebFetch": f"Reading {urlparse(str(a.get('url', ''))).netloc or 'a web page'}",
    }
    return simple[name] if name in simple else f"Using {name}"


def variant_prompt(source: dict, version: int, brief: str) -> str:
    """First build turn of a variant: the project is an exact copy of `source` v`version`."""
    return (f"This project is a new variant of \"{source['name']}\", copied exactly from its v{version}. "
            "Change the copy into the variant described below. Keep everything the description does not "
            "mention. If part of it cannot be done with your tools, do the rest and say plainly what you "
            f"could not do.\n\nWhat is different:\n{brief}")


def variant_research_prompt(source: dict, brief: str, research: dict | None) -> str:
    """Research turn of a variant: extend the original's research to cover what the brief changes."""
    if research:
        start = ("The research for the original, to build on: keep what still applies, change what the "
                 "variant changes, and add sources for the new equipment.\n" + json.dumps(research, indent=1))
    else:
        start = "The original has no research yet, so research the vessel and the changes."
    return (f"This vessel is a variant of \"{source['name']}\". What is different:\n{brief}\n\n{start}\n\n"
            "Save the research for the variant with save_research.")


def system_prompt(kind: str, mode: str) -> str:
    if mode == "research":
        return RESEARCH_PROMPT
    return SHIP_PROMPT if kind == "ship" else CREW_PROMPT


# ---- runners ----------------------------------------------------------------------------

class AgentRunner(Protocol):
    def run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str: ...


class ClaudeAgentRunner:
    """Real runner: the local Claude Code login through claude-agent-sdk (no API key)."""

    def __init__(self, model: str = "claude-opus-5-5", max_turns: int = 24, timeout_s: float = 1200):
        self.model, self.max_turns, self.timeout_s = model, max_turns, timeout_s

    def run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str:
        return asyncio.run(asyncio.wait_for(self._run(session, prompt, mode, log), self.timeout_s))

    async def _run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock,
                                      ToolUseBlock, create_sdk_mcp_server, query, tool)

        def make(name: str, desc: str, schema: dict):
            @tool(name, desc, schema)
            async def handler(args: dict) -> dict:
                text, is_error = await asyncio.to_thread(session.call, name, args)
                out: dict = {"content": [{"type": "text", "text": text}]}
                if is_error:
                    out["is_error"] = True
                return out
            return handler

        specs = session.tool_specs(mode)
        server = create_sdk_mcp_server(name="editor", tools=[make(*s) for s in specs])
        names = [f"mcp__editor__{s[0]}" for s in specs]
        builtins = ["WebSearch", "WebFetch"] if mode == "research" else []
        opts = ClaudeAgentOptions(
            mcp_servers={"editor": server}, tools=builtins + names, allowed_tools=builtins + names,
            setting_sources=[], max_turns=self.max_turns, model=self.model,
            system_prompt=system_prompt(session.kind, mode),
            cwd=str(session.store.version_dir(session.pid, 0).parent))
        texts: list[str] = []
        final: str | None = None
        async def turn():
            # Content blocks (attached pictures and PDFs) go as one streamed user message.
            yield {"type": "user", "session_id": "", "parent_tool_use_id": None,
                   "message": {"role": "user", "content": prompt}}

        async for m in query(prompt=prompt if isinstance(prompt, str) else turn(), options=opts):
            if isinstance(m, AssistantMessage):
                for b in m.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        texts.append(b.text.strip())
                        said = " ".join(b.text.split())
                        log(f"• AI: {said[:240]}{'…' if len(said) > 240 else ''}")
                    elif isinstance(b, ToolUseBlock):
                        name = b.name.removeprefix('mcp__editor__')
                        log(f"AI → {name}")
                        doing = describe_tool(name, b.input)
                        if doing:
                            log(f"• {doing}")
            elif isinstance(m, ResultMessage):
                if m.is_error:
                    raise RuntimeError(f"AI run failed ({m.subtype}): {m.result}")
                final = m.result
        return (final or (texts[-1] if texts else "")).strip()


class ScriptedAgent:
    """Deterministic stand-in for tests: replays tool calls, returns a fixed reply."""

    def __init__(self, steps: list[tuple[str, dict]] | None = None, reply: str = "Done."):
        self.steps = steps or []
        self.reply = reply
        self.prompts: list[str | list[dict]] = []
        self.modes: list[str] = []
        self.results: list[tuple[str, str, bool]] = []

    def run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str:
        self.prompts.append(prompt)
        self.modes.append(mode)
        for name, args in self.steps:
            args = {k: (v.format(pin_id=session.pin_id) if isinstance(v, str) else v) for k, v in args.items()}
            text, err = session.call(name, args)
            self.results.append((name, text, err))
        return self.reply
