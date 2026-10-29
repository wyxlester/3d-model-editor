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
import base64
import difflib
import asyncio
import copy
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from .builds import Builds, summarise_change
from .catalog import Catalog, CatalogError
from . import crew_modules as cm
from . import design_plan as dp
from . import research_media as rm
from .specmerge import deep_merge, label_for
from .store import NotFound, ProjectStore

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


@dataclass
class ToolOutput:
    """A tool result with pictures for the model to look at (a photo it added, a crop it made)."""
    text: str
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (mime, bytes)


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}


# Lester, 29 Oct: "when user describe a equipment, user will also need to describe how the human
# is holding the equipment". Equipment and where it is carried are one module, so the AI always
# settles the carry with the item, and a missing carry is never a reason to refuse or to ask.
CARRY_RULE = ("Describing an equipment item means saying what it is AND how it is held or carried. If the "
              "user did not say how, pick the sensible carry for that item (a rifle: both_hands; binoculars: "
              "chest; a radio: hip; a small tool: right_hand), do it, and say in the reply which carry you "
              "chose. Never refuse or stop to ask because the carry is missing.")


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
                 log: Log, repo_root: Path, pin_id: str | None = None, mode: str = "build",
                 module: str | None = None):
        self.mode = mode
        # Scoped chat (contract §6): when set, every spec change must stay inside this crew module.
        self.module = module
        self.store, self.builds, self.catalog = store, builds, catalog
        self.pid, self.log, self.repo_root, self.pin_id = pid, log, repo_root, pin_id
        project = store.load(pid)
        self.kind = project["kind"]
        self.base = project["base"]
        self.version = project["version"]
        self.spec = store.spec(pid, self.version) if self.version else store.spec(pid, 1)
        self.built_spec = copy.deepcopy(self.spec)
        self.start_spec = copy.deepcopy(self.spec)
        self.parts = store.parts(pid, self.version)
        self.report = store.report(pid, self.version) or {}
        self.built_versions: list[int] = []
        self.pin_replied: str | None = None  # status set via reply_to_pin
        self.research_saved = False
        self.plan_saved = False
        self.is_custom = self.kind == "ship" and self.base == "custom"
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
            text = {"type": "string"}
            tools = [
                ("save_research", RESEARCH_DOC, _obj({"research": {"type": "object"}}, ["research"])),
                ("add_photo", ADD_PHOTO_DOC, _obj({
                    "image_url": text, "upload_id": text, "title": text, "vessel": text,
                    "views": {"type": "array", "items": {"type": "string", "enum": list(rm.VIEWS)}},
                    "credit": text, "date": text, "observation": text, "source": text,
                    "status": {"type": "string", "enum": list(rm.PHOTO_STATUSES)},
                    "rights": text, "modelling_use": text},
                    ["title", "vessel", "views", "observation", "status", "rights", "modelling_use"])),
                ("remove_photo", "Remove a stored photograph by id (P02), with its close-ups and files. Use it for "
                 "a photo of the wrong vessel or a republished copy of one already added.",
                 _obj({"id": text}, ["id"])),
                ("add_closeup", ADD_CLOSEUP_DOC, _obj({
                    "photo": text, "box": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                    "title": text, "what_it_shows": text, "claims": {"type": "array", "items": text}},
                    ["photo", "box", "title", "what_it_shows"])),
                ("remove_closeup", "Remove a close-up by id (C02) and its file.", _obj({"id": text}, ["id"])),
                ("set_coverage", "Say in one or two plain sentences what the photographs cover and what is missing "
                 "(e.g. 'No true overhead photograph found; the roof is read from elevated three-quarter views.'). "
                 "Which views have photos is worked out from each photo's views; this sets the note only.",
                 _obj({"note": text}, ["note"])),
            ]
            if self.is_custom:
                tools.append(("save_design_plan", self._design_plan_doc(),
                              _obj({"summary": {"type": "array", "items": {"type": "string"}},
                                    "spec": {"type": "object"}}, ["summary", "spec"])))
            if self.kind == "ship":
                # A catalog without categories (older installs, test stand-ins) just offers none.
                get = getattr(self.catalog, "categories", None)
                cats = (get() if callable(get) else {}).get("ship", [])
                tools.append(("set_category", "File this ship in the ship list under one of the catalog's ship "
                              "categories, exactly as written: " + ("; ".join(cats) or "(none defined)") +
                              ". Pick the closest; any other label is refused. Say in your reply where you "
                              "filed it.", _obj({"category": {"type": "string"}}, ["category"])))
            return tools
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
                ("set_params", "Patch the builder's params (deep merge; null deletes). Only known params. A list param "
                 "(e.g. a custom ship's tiers) is replaced whole: send the complete list, not just the changed entry.",
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
        place = {"type": "string", "enum": list(cm.PLACES)}
        equipment_item = {"oneOf": [
            {"type": "string"},
            {"type": "object", "properties": {"name": {"type": "string"}, "color": rgb_or_name, "place": place},
             "required": ["name"]}]}
        accessory_item = {"oneOf": [
            {"type": "string"},
            {"type": "object", "properties": {"name": {"type": "string"}, "color": VEC3}, "required": ["name"]}]}
        return common[:1] + [
            ("set_body", "Patch body fields. Module gender: gender, height_cm, weight, muscle, proportions, "
             "age_years. Module skin: skin. Module hair: hair (a catalog style, \"none\" for bald), hair_color "
             "([r,g,b] 0..1 recolouring hair and eyebrows, e.g. blonde [0.85, 0.7, 0.4]; null for natural).",
             _obj({"patch": {"type": "object"}}, ["patch"])),
            ("set_clothes", "Patch clothes. Module shirt: shirt = {\"style\": one of " + ", ".join(cm.SHIRT_STYLES) +
             ", \"color\": [r,g,b] 0..1} or null. Module pants: pants = {\"style\": one of " +
             ", ".join(cm.PANTS_STYLES) + ", \"color\"} or null. Module outfit: suit (a catalog suit, fills "
             "shirt and pants) or null, suit_color. Module shoes: shoes (catalog, null = barefoot), shoes_color. "
             "Module accessories: hat (catalog) or null, hat_color. Colours are [r,g,b] 0..1 or null. Setting a "
             "shirt or pants clears the suit; choosing a suit clears shirt, pants and their described items.",
             _obj({"patch": {"type": "object"}}, ["patch"])),
            ("set_equipment", "Module equipment: replace the catalog equipment list. Entries are a catalog name "
             "or {\"name\", \"color\", \"place\"} with color one of " + ", ".join(EQUIPMENT_COLOR_NAMES) +
             " or [r,g,b] 0..1. An entry that is already worn keeps its place unless you give one; "
             "life_vest and helmet have a fixed spot and take no place. " + CARRY_RULE,
             _obj({"equipment": {"type": "array", "items": equipment_item}}, ["equipment"])),
            ("set_place", "Module equipment: move one equipment item to " + ", ".join(cm.PLACES) + ". `item` "
             "is a catalog equipment name in the list (e.g. radio) or an equipment custom item id (item_2). "
             "both_hands is a two-handed ready carry for a long item (rifle, boat hook, pole): across the "
             "front of the body, muzzle forward-down, right hand on the grip, left hand on the barrel or "
             "handguard, held in every animation. It needs both hands: move anything held in a hand "
             "elsewhere first. No code change and nothing else moves.",
             _obj({"item": {"type": "string"}, "place": place}, ["item", "place"])),
            ("set_accessories", "Module accessories: replace the accessories list. Entries are a name (" +
             ", ".join(cm.ACCESSORIES) + " or the catalog's accessories options) or {\"name\", \"color\": "
             "[r,g,b] 0..1}. Hats are set_clothes hat.",
             _obj({"accessories": {"type": "array", "items": accessory_item}}, ["accessories"])),
            ("set_animations", "Replace the animation list (not part of any module: main chat only).",
             _obj({"animations": {"type": "array", "items": {"type": "string"}}}, ["animations"])),
            ("add_custom_item", self._custom_item_doc(),
             _obj({"description": {"type": "string"}, "code": {"type": "string"},
                   "module": {"type": "string", "enum": list(cm.CUSTOM_ITEM_MODULES)},
                   "place": place, "replace": {"type": "boolean"}},
                  ["description", "code", "module"])),
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

    def _design_plan_doc(self) -> str:
        docs = self.cat_entry.get("param_docs")
        known = (f"Params the builder knows (with docs and ranges): {json.dumps(docs)}" if docs else
                 "The custom builder's catalog is not available yet: use loa_m, beam_m, depth_m, draught_m, "
                 "bow_form, stern_form and a tiers list as in the contract.")
        return ("Save (or replace) the design plan the user approves before anything is built. Call it after "
                "save_research, whenever the research is good enough to build from or the user asks for a plan.\n"
                "summary: plain-English lines Lester reads on a phone, one fact each: overall size; hull form "
                "(bow, stern); each superstructure tier, deck by deck, with its length, width and height; colours "
                "by name; each detail to add after the build as a custom op (fly-bridge furniture, davits, rails, "
                "antennas); and every assumption with its value and why ('Assumed beam 6.2 m: typical for a 24 m "
                "yacht; no source gives it'). No code, no tool or param names.\n"
                "spec: the custom ship spec it would build: {\"params\": {...}} (plus optional meta). Frame: "
                "metres, origin amidships at the waterline, +X bow, so the hull spans -loa_m/2..+loa_m/2. Tiers "
                "stack from the deck up; each must lie within the hull and on the tier below it, no wider than "
                "the beam. Leave custom_ops empty: details go in the summary. The tool validates and returns "
                "errors to fix.\n" + known)

    def _custom_item_doc(self) -> str:
        return ("Make a module's item from a description when no preset fits (a singlet, shorts, boots, a "
                "torch, a clipboard, gloves). `module` is required: hair, shirt, pants, shoes, equipment or "
                "accessories. `replace` (default true for hair, shirt, pants and shoes; false for equipment and "
                "accessories, where several items coexist) first removes that module's existing custom items; "
                "nothing in any other module is touched. A described hair, shirt, pants or shoes item replaces "
                "that module's preset, and a described shirt or pants clears the outfit (suit).\n"
                "`code` is Python run inside Blender after the human and catalog equipment are built and before "
                "animations bake, with `bpy`, `mathutils` and helper module `crew` in scope.\n"
                "- Worn things (shirt, pants, shoes, hair, gloves): crew.garment over the body regions they "
                "cover, so they fit any body and move with every animation; name them Clothes__<Name>. Modules "
                "shirt, pants and shoes make Clothes__ objects only.\n"
                "- Module equipment (held or carried things): `place` is required, one of " +
                ", ".join(cm.PLACES) + ". " + CARRY_RULE + " Build a low-poly mesh named Equipment__<Name> modelled at the origin: "
                "grip point at the origin, +Z up, +X forward, sizes in metres. Do NOT parent or position it "
                "(no crew.hold, crew.attach or .parent): the builder attaches it by its place, so moving it "
                "later is set_place with no code change. Equipment makes Equipment__ objects only.\n"
                "- Long items carried in both hands (place both_hands: a rifle, a boat hook): model them the "
                "same way, and mark where the hands go with crew.two_hand_grips(obj, grip, support) in the "
                "item's own coordinates: grip = right hand (a rifle's pistol grip and trigger), support = left "
                "hand (its handguard or barrel). Without it the builder uses the origin (if it lies in the back "
                "half) or a third from the back end for the grip, and two thirds for the support.\n"
                "- Accessories that sit rigidly on the body (sunglasses, a badge) may use crew.attach.\n"
                "Names use letters/digits/underscore only, e.g. Clothes__Singlet, Equipment__Torch. Colours via "
                "crew.material. No file, network or OS access (imports of os/sys/subprocess, open(), eval, exec "
                "are rejected). Helpers:\n" + crew_helpers(self.repo_root))

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool. Returns (text for the model, is_error)."""
        blocks, is_error = self.call_content(name, args)
        return "\n".join(b["text"] for b in blocks if b["type"] == "text"), is_error

    def call_content(self, name: str, args: dict) -> tuple[list[dict], bool]:
        """Run one tool. Returns (MCP content blocks: text, plus any pictures, is_error)."""
        out = self._call(name, args)
        text, is_error = out[0], out[1]
        blocks = [{"type": "text", "text": text}]
        for mime, raw in out[2] if len(out) > 2 else []:
            blocks.append({"type": "image", "data": base64.b64encode(raw).decode(), "mimeType": mime})
        return blocks, is_error

    def _call(self, name: str, args: dict) -> tuple:
        self.tool_calls.append(name)
        allowed = {n for n, _, _ in self.tool_specs(self.mode)}
        fn = getattr(self, f"t_{name}", None)
        if fn is None or name not in allowed:
            return f"Unknown tool {name}.", True
        before = copy.deepcopy(self.spec)
        try:
            out = fn(**(args or {}))
            notes = self._check_modules(name, before) if self.kind == "crew" and self.spec != before else []
        except ToolError as e:
            self.spec = before  # a refused call changes nothing
            self.log(f"AI tool {name} refused: {e}")
            return str(e), True
        except rm.MediaError as e:
            self.log(f"AI tool {name} refused: {e}")
            return str(e), True
        except TypeError as e:
            self.spec = before
            return f"Bad arguments for {name}: {e}", True
        self.log(f"AI tool {name}: ok")
        if isinstance(out, ToolOutput):
            return out.text, False, out.images
        text = out if isinstance(out, str) else json.dumps(out, indent=1)
        return (text + (" " + " ".join(notes) if notes else "")), False

    def _check_modules(self, name: str, before: dict) -> list[str]:
        """After a crew spec change: apply the shared module rules (outfit vs shirt/pants, described
        item vs preset), then, in a scoped chat, refuse anything outside the module. Raises ToolError;
        `call` then restores `before`, so a refused change leaves the spec exactly as it was."""
        try:
            self.spec, notes = cm.apply_module_rules(before, self.spec)
        except cm.ModuleRuleError as e:
            raise ToolError(str(e))
        if self.module:
            bad = cm.outside_changes(before, self.spec, self.module)
            if bad:
                owners = sorted({c.module or "no module" for c in bad})
                raise ToolError(
                    f"Refused, nothing changed: you may only change the {self.module} module "
                    f"({cm.MODULE_DOCS[self.module]}). {name} tried to change "
                    f"{', '.join(c.path for c in bad)}, which belong(s) to {', '.join(owners)}. "
                    "Leave those exactly as they are.")
        return notes

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
            **({"modules": cm.MODULE_DOCS, "editing_only_module": self.module} if self.kind == "crew" else {}),
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
            if slot in ("shirt", "pants"):
                self._check_top(slot, v)
                continue
            allowed = self._crew_options(slot)
            if allowed is None and slot not in self.spec.get("clothes", {}):
                raise ToolError(f"Unknown clothes slot {slot!r}")
            if v is not None and allowed is not None and v not in allowed:
                raise ToolError(f"{slot} must be one of {allowed} (or null). For anything else, set the slot "
                                "to null if it is replaced and make the item with add_custom_item.")
        self.spec = self._set_section("clothes", patch)
        return "Clothes updated. Call rebuild."

    def _check_top(self, slot: str, v) -> None:
        """A shirt or pants preset: {"style", "color"} or null (contract §6 Modules)."""
        if v is None:
            return
        styles = self._crew_options(slot) or list(cm.SHIRT_STYLES if slot == "shirt" else cm.PANTS_STYLES)
        styles = [x for x in styles if x is not None]
        if not isinstance(v, dict) or set(v) - {"style", "color"} or v.get("style") not in styles:
            raise ToolError(f"{slot} is {{\"style\": one of {styles}, \"color\": [r,g,b] 0..1}} or null. "
                            f"For a {slot} no style fits, use add_custom_item with module {slot!r}.")
        if v.get("color") is not None and not _rgb(v["color"]):
            raise ToolError(f"{slot} color is [r,g,b] with each value 0..1")

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
        fields = {"equipment": {"name", "color", "place"}}.get(slot, {"name", "color"})
        for v in values:
            if allow_color and isinstance(v, dict):
                extra = set(v) - fields
                if not isinstance(v.get("name"), str) or extra:
                    raise ToolError(f"{slot} entries are a name or an object with {sorted(fields)}, got {v!r}")
                c = v.get("color")
                named_ok = slot == "equipment" and c in EQUIPMENT_COLOR_NAMES
                if c is not None and not named_ok and not _rgb(c):
                    raise ToolError(f"color must be " + (f"one of {EQUIPMENT_COLOR_NAMES} or " if slot == "equipment"
                                                         else "") + f"[r,g,b] 0..1, got {c!r}")
                p = v.get("place")
                if p is not None:
                    self._check_place(v["name"], p)
                names.append(v["name"])
            elif isinstance(v, str):
                names.append(v)
            else:
                raise ToolError(f"{slot} must be a list of names" +
                                (" or {\"name\", \"color\"} objects" if allow_color else ""))
        allowed = self._crew_options(slot)
        if allowed is None and slot == "accessories":
            allowed = list(cm.ACCESSORIES)
        bad = [v for v in names if allowed is not None and v not in allowed]
        if bad:
            raise ToolError(f"Not in the catalog: {bad}. Options: {allowed}" +
                            (". For anything else use add_custom_item." if slot == "equipment" else ""))

    @staticmethod
    def _check_place(name: str, place) -> None:
        if place not in cm.PLACES:
            raise ToolError(f"place must be one of {list(cm.PLACES)}, got {place!r}")
        if name in cm.FIXED_PLACE_EQUIPMENT:
            raise ToolError(f"{name} is worn in one fixed spot and takes no place.")

    @staticmethod
    def _check_hands(spec: dict) -> None:
        """A both_hands carry needs both hands free (contract §6 Placement)."""
        problems = cm.hand_conflicts(spec)
        if problems:
            raise ToolError(" ".join(problems))

    def t_set_equipment(self, equipment: list) -> str:
        self._check_crew()
        self._check_list("equipment", equipment, allow_color=True)
        # An item already worn keeps its place unless one is given: changing what is carried (or
        # its colour) must not move what is already held.
        places: dict[str, list] = {}
        for e in self.spec.get("equipment") or []:
            if isinstance(e, dict) and e.get("place"):
                places.setdefault(e.get("name"), []).append(e["place"])
        kept = []
        for e in equipment:
            name = e if isinstance(e, str) else e["name"]
            old = places.get(name)
            if old and (isinstance(e, str) or "place" not in e):
                e = dict({"name": e} if isinstance(e, str) else e, place=old.pop(0))
            kept.append(e)
        new = deep_merge(self.spec, {"equipment": kept})
        self._check_hands(new)
        self.spec = new
        return "Equipment updated. Call rebuild."

    def t_set_place(self, item: str, place: str) -> str:
        self._check_crew()
        if not isinstance(item, str) or not item:
            raise ToolError("item is an equipment name or an equipment custom item id")
        self._check_place(item, place)
        spec = copy.deepcopy(self.spec)
        for i, e in enumerate(spec.get("equipment") or []):
            if (e if isinstance(e, str) else e.get("name") if isinstance(e, dict) else None) == item:
                spec["equipment"][i] = dict({"name": e} if isinstance(e, str) else e, place=place)
                self._check_hands(spec)
                self.spec = spec
                return f"{item} moved to {place}. Call rebuild."
        for it in spec.get("custom_items") or []:
            if isinstance(it, dict) and it.get("id") == item:
                if cm.item_module(it) != "equipment":
                    raise ToolError(f"{item} is a {cm.item_module(it)} item; only equipment has a place.")
                it["place"] = place
                self._check_hands(spec)
                self.spec = spec
                return f"{item} ({it.get('description')}) moved to {place}. Call rebuild."
        names = [e if isinstance(e, str) else e.get("name") for e in spec.get("equipment") or []
                 if isinstance(e, (str, dict))]
        ids = [it.get("id") for it in spec.get("custom_items") or []
               if isinstance(it, dict) and cm.item_module(it) == "equipment"]
        raise ToolError(f"No equipment item {item!r}. Equipment: {names}; equipment custom items: {ids}.")

    def t_set_accessories(self, accessories: list) -> str:
        self._check_crew()
        self._check_list("accessories", accessories, allow_color=True)
        self.spec = deep_merge(self.spec, {"accessories": accessories})
        return "Accessories updated. Call rebuild."

    def t_set_animations(self, animations: list) -> str:
        self._check_crew()
        self._check_list("animations", animations)
        self.spec = deep_merge(self.spec, {"animations": animations})
        return "Animations updated. Call rebuild."

    def t_add_custom_item(self, description: str, code: str, module: str | None = None,
                          place: str | None = None, replace: bool | None = None) -> str:
        self._check_crew()
        if not isinstance(description, str) or not isinstance(code, str) or not description.strip() or not code.strip():
            raise ToolError("description and code are both required")
        if module not in cm.CUSTOM_ITEM_MODULES:
            raise ToolError(f"module is required: one of {list(cm.CUSTOM_ITEM_MODULES)} (the module this item "
                            "belongs to; describing that module again replaces it, nothing else).")
        if module == "equipment":
            if place is None:
                raise ToolError(f"An equipment item needs a place: one of {list(cm.PLACES)}. " + CARRY_RULE)
            self._check_place("", place)
            if re.search(r"\bcrew\.(hold|attach)\s*\(|\.parent\s*=", code):
                raise ToolError("Equipment code must not parent or position the item (no crew.hold, crew.attach "
                                "or .parent): model it at the origin, grip at the origin, +Z up, +X forward. The "
                                "builder attaches it by its place.")
        elif place is not None:
            raise ToolError("Only equipment items take a place.")
        if replace is not None and not isinstance(replace, bool):
            raise ToolError("replace is true or false")
        check_code(code)
        for name in crew_object_names(code):
            if not CREW_ITEM_RE.match(name):
                raise ToolError(f"Object name {name!r} breaks the naming rule: Clothes__<Name> for worn things, "
                                "Equipment__<Name> for held or attached things ([A-Za-z0-9_]).")
            prefix = name.split("__", 1)[0]
            if module == "equipment" and prefix != "Equipment":
                raise ToolError(f"Equipment items make Equipment__<Name> objects, not {name!r}.")
            if module in ("shirt", "pants", "shoes") and prefix != "Clothes":
                raise ToolError(f"{module} items make Clothes__<Name> objects, not {name!r}.")
        if replace is None:
            replace = module not in cm.MULTI_ITEM_MODULES
        items = list(self.spec.get("custom_items", []))
        removed = [o.get("id") for o in items if replace and cm.item_module(o) == module]
        items = [o for o in items if not (replace and cm.item_module(o) == module)]
        taken = {o.get("id") for o in items}
        i = 1
        while f"item_{i}" in taken:
            i += 1
        item = {"id": f"item_{i}", "module": module, "description": description, "code": code}
        if place is not None:
            item["place"] = place
        items.append(item)
        new = deep_merge(self.spec, {"custom_items": items})
        self._check_hands(new)
        self.spec = new
        return (f"Custom item item_{i} added to {module}" +
                (f", replacing {', '.join(map(str, removed))}" if removed else "") + ". Call rebuild to run it.")

    def t_remove_custom_item(self, id: str) -> str:
        self._check_crew()
        items = list(self.spec.get("custom_items", []))
        kept = [o for o in items if o.get("id") != id]
        if len(kept) == len(items):
            raise ToolError(f"No custom item {id!r}. Ids: {[o.get('id') for o in items]}")
        self.spec = deep_merge(self.spec, {"custom_items": kept})
        return f"Removed {id}. Call rebuild."

    # ---- research ---------------------------------------------------------------------
    def record_material(self, tool: str, args: dict | None, result: Any) -> None:
        """Keep what a web search returned or a page said, as the AI saw it, for review afterwards."""
        a = args if isinstance(args, dict) else {}
        if isinstance(result, list):
            text = "\n".join(str(b.get("text", "")) for b in result if isinstance(b, dict))
        else:
            text = "" if result is None else str(result)
        if tool == "WebSearch":
            self.store.add_material(self.pid, "search", str(a.get("query", "")), text, a)
        elif tool == "WebFetch":
            self.store.add_material(self.pid, "page", str(a.get("url", "")), text, a)

    def t_save_research(self, research: dict) -> str:
        stored = self.store.load(self.pid).get("research") or {}
        media_ids = [m.get("id") for k in ("photos", "closeups") for m in stored.get(k) or []]
        errors = validate_research(research, media_ids)
        if errors:
            raise ToolError("Research not saved. Fix: " + "; ".join(errors))
        research = dict(research, schema="me3d.research/1")
        kept = (" Photographs, close-ups and coverage are kept as stored; change them with add_photo, add_closeup "
                "and set_coverage." if any(k in research for k in rm.MEDIA_KEYS) else "")
        rev = self.store.set_research(self.pid, research)
        self.research_saved = True
        if not self.is_custom:
            return "Research saved." + kept
        plan = self.store.load(self.pid).get("design_plan") or {}
        note = {"stale": " The approved design plan no longer matches it: redraft it with save_design_plan.",
                "draft": " The draft design plan was drafted against older research: redraft it if it changed."
                }.get(plan.get("status"), "")
        return f"Research saved (revision {rev}).{note}{kept}"

    # ---- research photographs and close-ups (contract §12) -----------------------------
    def _media_dir(self, sub: str) -> Path:
        d = self.store.research_dir(self.pid) / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _file_url(self, sub: str, fname: str) -> str:
        return f"/api/files/projects/{self.pid}/research/{sub}/{fname}"

    def _change_media(self, fn: Callable[[dict, dict], tuple[Any, bool]]) -> tuple[Any, bool]:
        """Read-modify-write the research record under the project lock. fn(research, project) changes
        it in place and returns (result, changed). A change recounts photos and coverage; a ToolError
        inside fn saves nothing.

        It does NOT bump the research revision. Lester, 29 Oct ("why can't i approve yet?"): adding
        photographs made his design plan look older than the research, so Approve stayed off although
        nothing the plan rests on had changed. Only save_research (the facts) moves the revision."""
        with self.store.edit(self.pid) as project:
            research = project.get("research") or {"schema": "me3d.research/1"}
            result, changed = fn(research, project)
            if changed:
                rm.refresh(research)
                project["research"] = research
            return result, changed

    @staticmethod
    def _text_field(name: str, value, required: bool = True) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"{name} is required: a plain sentence or phrase.")
        return value.strip()

    def _plan_note(self) -> str:
        if not self.is_custom:
            return ""
        plan = self.store.load(self.pid).get("design_plan") or {}
        return (" The design plan is now older than the research: redraft it with save_design_plan."
                if plan.get("status") in ("draft", "stale") else "")

    def t_add_photo(self, title: str, vessel: str, views: list, observation: str, status: str, rights: str,
                    modelling_use: str, image_url: str | None = None, upload_id: str | None = None,
                    credit: str | None = None, date: str | None = None, source: str | None = None) -> ToolOutput:
        if bool(image_url) == bool(upload_id):
            raise ToolError("Give exactly one of image_url (the direct URL of the image file) or upload_id "
                            "(an image Lester attached).")
        fields = {k: self._text_field(k, v) for k, v in (("title", title), ("vessel", vessel),
                  ("observation", observation), ("rights", rights), ("modelling_use", modelling_use))}
        if not isinstance(views, list) or not views or any(v not in rm.VIEWS for v in views):
            raise ToolError(f"views is a non-empty list drawn from {list(rm.VIEWS)}: the sides the photo shows.")
        if status not in rm.PHOTO_STATUSES:
            raise ToolError(f"status is one of {list(rm.PHOTO_STATUSES)}: baseline (the exact vessel), comparison "
                            "(a sister or similar craft), excluded (kept for the record, not used).")
        accessed = _today()
        if upload_id:
            try:
                meta, raw = self.store.get_upload(self.pid, upload_id)
            except (NotFound, OSError):
                raise ToolError(f"No attachment {upload_id!r} on this project. Use the id of an image Lester "
                                "attached (u_ followed by 8 characters).")
            if not str(meta.get("type", "")).startswith("image/"):
                raise ToolError(f"{meta.get('name')} is not a picture, so it cannot be added as a photograph.")
            image = rm.identify(raw, meta.get("type", ""))
            credit = self._text_field("credit", credit, required=False) or "Supplied by Lester"
            date = self._text_field("date", date, required=False) or f"Attached {accessed}; capture date not given"
            source = (self._text_field("source", source, required=False) or
                      f"Attached by Lester in chat ({meta.get('name')})")
            origin = {"image_url": None, "upload": upload_id}
        else:
            credit = self._text_field("credit", credit)
            date = self._text_field("date", date)
            source = self._text_field("source", source)
            image = rm.download(image_url)
            origin = {"image_url": image_url.strip()}

        def add(research: dict, project: dict):
            photos = research.setdefault("photos", [])
            for p in photos:
                if p.get("sha256") == image.sha256:
                    return p, False
            if len(photos) >= rm.MAX_PHOTOS:
                raise ToolError(f"There are already {rm.MAX_PHOTOS} photographs. Remove weaker ones first.")
            seq = project.setdefault("research_seq", {})
            pid_, seq["P"] = rm.next_id("P", [p.get("id") for p in photos], seq.get("P", 0))
            fname = pid_ + image.ext
            (self._media_dir("photos") / fname).write_bytes(image.data)
            rec = {"id": pid_, "title": fields["title"], "vessel": fields["vessel"], "views": list(dict.fromkeys(views)),
                   "credit": credit, "date": date, "observation": fields["observation"], "source": source,
                   **origin, "status": status, "rights": fields["rights"], "modelling_use": fields["modelling_use"],
                   "width": image.width, "height": image.height, "sha256": image.sha256, "accessed": accessed,
                   "file": self._file_url("photos", fname)}
            photos.append(rec)
            return rec, True

        rec, added = self._change_media(add)
        if not added:
            return ToolOutput(f"That image is already stored as {rec['id']} (same SHA-256), so nothing was added. "
                              f"Use {rec['id']}.")
        look, mime, scale = rm.preview(image.data)
        how = ("Boxes for add_closeup are in the photo's own pixels" +
               (f"; the copy below is shown at 1/{scale:.2f} size, so multiply what you measure on it by "
                f"{scale:.2f}." if scale > 1 else ", the same as the copy below."))
        return ToolOutput(f"Added {rec['id']}: {rec['width']}x{rec['height']} px, SHA-256 {rec['sha256'][:12]}…. "
                          f"Look at it before describing it. {how}{self._plan_note()}", [(mime, look)])

    def t_remove_photo(self, id: str) -> str:
        def remove(research: dict, _project: dict):
            photos = research.get("photos") or []
            hit = next((p for p in photos if p.get("id") == id), None)
            if hit is None:
                raise ToolError(f"No photograph {id!r}. Stored: {[p.get('id') for p in photos] or 'none'}.")
            research["photos"] = [p for p in photos if p is not hit]
            gone = [c for c in research.get("closeups") or [] if c.get("photo") == id]
            research["closeups"] = [c for c in research.get("closeups") or [] if c.get("photo") != id]
            for rec, sub in [(hit, "photos")] + [(c, "closeups") for c in gone]:
                (self.store.research_dir(self.pid) / sub / Path(rec["file"]).name).unlink(missing_ok=True)
            return [c["id"] for c in gone], True

        gone, _ = self._change_media(remove)
        return (f"Removed {id}" + (f" and its close-ups {', '.join(gone)}" if gone else "") +
                f". Update any claim that cites {id} with save_research.")

    def t_add_closeup(self, photo: str, box: list, title: str, what_it_shows: str,
                      claims: list | None = None) -> ToolOutput:
        title = self._text_field("title", title)
        what = self._text_field("what_it_shows", what_it_shows)
        if claims is not None and (not isinstance(claims, list) or
                                   not all(isinstance(c, str) and c.strip() for c in claims)):
            raise ToolError("claims is a list of short sentences this close-up supports.")
        stored = (self.store.load(self.pid).get("research") or {}).get("photos") or []
        src = next((p for p in stored if p.get("id") == photo), None)
        if src is None:
            raise ToolError(f"No photograph {photo!r}. Add it with add_photo first; stored: "
                            f"{[p.get('id') for p in stored] or 'none'}.")
        x, y, w, h = rm.check_box(box, src["width"], src["height"])
        raw = (self.store.research_dir(self.pid) / "photos" / Path(src["file"]).name).read_bytes()
        png = rm.crop(raw, [x, y, w, h])

        def add(research: dict, project: dict):
            if not any(p.get("id") == photo for p in research.get("photos") or []):
                raise ToolError(f"Photograph {photo} was removed meanwhile; nothing cropped.")
            closeups = research.setdefault("closeups", [])
            seq = project.setdefault("research_seq", {})
            cid, seq["C"] = rm.next_id("C", [c.get("id") for c in closeups], seq.get("C", 0))
            (self._media_dir("closeups") / f"{cid}.png").write_bytes(png)
            rec = {"id": cid, "photo": photo, "box": [x, y, w, h], "title": title, "what_it_shows": what,
                   "claims": [c.strip() for c in claims or []], "file": self._file_url("closeups", f"{cid}.png")}
            closeups.append(rec)
            return rec, True

        rec, _ = self._change_media(add)
        look, mime, _scale = rm.preview(png)
        return ToolOutput(f"Close-up {rec['id']} cut from {photo} at {rec['box']} ({w}x{h} px). Check it shows "
                          f"what you meant; if not, remove_closeup {rec['id']} and crop again.{self._plan_note()}",
                          [(mime, look)])

    def t_remove_closeup(self, id: str) -> str:
        def remove(research: dict, _project: dict):
            closeups = research.get("closeups") or []
            hit = next((c for c in closeups if c.get("id") == id), None)
            if hit is None:
                raise ToolError(f"No close-up {id!r}. Stored: {[c.get('id') for c in closeups] or 'none'}.")
            research["closeups"] = [c for c in closeups if c is not hit]
            (self.store.research_dir(self.pid) / "closeups" / Path(hit["file"]).name).unlink(missing_ok=True)
            return id, True

        self._change_media(remove)
        return f"Removed close-up {id}."

    def t_set_coverage(self, note: str) -> str:
        note = self._text_field("note", note)

        def setnote(research: dict, _project: dict):
            research["coverage"] = dict(research.get("coverage") or {}, note=note)
            return None, True

        self._change_media(setnote)
        cov = (self.store.load(self.pid).get("research") or {}).get("coverage") or {}
        missing = [v for v in rm.COVERAGE_VIEWS if not cov.get(v)]
        return "Coverage note saved." + (f" Views with no photograph: {', '.join(missing)}." if missing else "")

    def t_set_category(self, category: str) -> str:
        """Lester, 29 Oct: research files a ship under one of the catalog's ship categories, never an
        invented one. Saved where PATCH /api/projects/{id} saves it (the project's `category`)."""
        if self.kind != "ship":
            raise ToolError("Categories from research are only for ships.")
        cats = self.catalog.categories()["ship"]
        wanted = " ".join(str(category or "").split())
        label = next((c for c in cats if c.casefold() == wanted.casefold()), None)
        if label is None:
            raise ToolError(f"{wanted!r} is not one of the catalog's ship categories, so nothing was changed. "
                            f"Pick the closest of: {'; '.join(cats) or '(none defined)'}.")
        with self.store.edit(self.pid) as project:
            project["category"] = label
        return f"Filed under {label}."

    def t_save_design_plan(self, summary: list, spec: dict) -> str:
        if not self.is_custom:
            raise ToolError("Design plans are only for custom ships.")
        errors = dp.check_summary(summary)
        try:
            default = self.catalog.default_spec("ship", "custom")
        except CatalogError:
            default = None
        full, spec_errors = dp.plan_spec(spec, self.spec, default, self.cat_entry.get("param_docs") or {})
        errors += spec_errors
        if errors:
            raise ToolError("Plan not saved. Fix: " + "; ".join(errors))
        plan = self.store.set_design_plan(self.pid, [s.strip() for s in summary], full)
        self.plan_saved = True
        return (f"Design plan saved as a draft against research revision {plan['research_rev']}. The user approves "
                "it on screen; nothing is built until then.")


RESEARCH_DOC = (
    "Save the research for this vessel. Object fields:\n"
    "subject (str): the one exact baseline hull/class.\n"
    "summary: {verdict (str, 2-4 plain sentences: can we model it and how well), existing_work (str)}.\n"
    "sources: [{id, title, url, status, what_it_proves}] - status is one of " + ", ".join(EVIDENCE_STATUSES) +
    ". Sister craft are 'Comparison only'.\n"
    "claims: [{claim, value?, unit?, sources: [source ids], status, consequence_for_model}].\n"
    "open_questions: [{question, why_it_matters}].\n"
    "articulation: [{part (Group__Part name), motion, axis?, range_deg?: [min,max], priority: P0|P1|P2, "
    "evidence: [source ids]}] including parts that must NOT move (motion 'fixed').\n"
    "A claim may cite stored photographs and close-ups (P01, C02) as well as sources. Photographs, close-ups "
    "and coverage are not part of this record: add_photo, add_closeup and set_coverage manage them, and they "
    "are kept whenever you save."
)

ADD_PHOTO_DOC = (
    "Store one real photograph as research evidence. The server downloads it (or copies Lester's attachment), "
    "checks it is a PNG, JPEG, WebP or GIF of at most 15 MB on a public website, measures and hashes it, and "
    "files it as P01, P02... A copy of a photo already stored (same SHA-256) is not added again; you get its id.\n"
    "image_url: the direct URL of the image file itself (often ending .jpg/.png/.webp; on Wikimedia Commons the "
    "upload.wikimedia.org address), not the page it appears on. Or upload_id: an image Lester attached (then "
    "credit defaults to 'Supplied by Lester').\n"
    "title: what the photo shows, e.g. 'PC 303: bow, cabin and roof fittings'. vessel: the exact vessel or type "
    "pictured. views: which of " + ", ".join(rm.VIEWS) + " it shows. credit: photographer and/or publisher as "
    "credited. date: published and/or captured, as stated ('Published 19 July 2025; capture date not "
    "specified'). observation: what it shows that matters for the model, and what to ignore in it. source: the "
    "page the image appears on. status: baseline (the exact vessel), comparison (a sister or similar craft), "
    "excluded (kept for the record, not used). rights: e.g. 'Copyright retained by credited owner; research "
    "reference, not a texture', or the licence (CC BY-SA 4.0). modelling_use: what it is used for (hull shape, "
    "roof fittings).\n"
    "The result shows you the photo: describe only what you see in it."
)

ADD_CLOSEUP_DOC = (
    "Cut a close-up out of a stored photograph: a fitting or detail that changes the model (an outboard, a "
    "searchlight, a hatch, a rail base). box is [x, y, w, h] in the photo's own pixels (left, top, width, "
    "height), at least 16 px each way and inside the photo; add_photo said how its preview scales. The server "
    "crops exactly that region: no enlargement, sharpening or generated detail. Stored as C01, C02...\n"
    "title: e.g. 'Roof searchlight'. what_it_shows: what the crop proves and what it changes in the model. "
    "claims: optional short sentences it supports. The result shows you the crop: check it."
)


def _claim_status_ok(status: Any) -> bool:
    """Contract §12: a claim's status is one evidence status, or two combined with " + "
    ("Official + Photographed")."""
    if not isinstance(status, str):
        return False
    parts = [x.strip() for x in status.split(" + ")]
    return 1 <= len(parts) <= 2 and all(x in EVIDENCE_STATUSES for x in parts)


def _today() -> str:
    return date.today().isoformat()


def validate_research(r: Any, media_ids: Any = ()) -> list[str]:
    """media_ids: stored photo and close-up ids (P01, C01), which claims may cite like sources."""
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
        if not _claim_status_ok(c.get("status")):
            errs.append(f"claims[{i}].status must be one of {EVIDENCE_STATUSES}, or two joined by ' + '")
        if not c.get("consequence_for_model"):
            errs.append(f"claims[{i}].consequence_for_model is required")
        missing = [x for x in c.get("sources", []) if x not in ids and x not in set(media_ids)]
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
  - Shirts and trousers: a preset style and colour with set_clothes shirt / pants when one fits; otherwise (a singlet, cargo shorts with pockets) add_custom_item with module shirt or pants and crew.garment over the body regions it covers. Either way the suit is set to null for you, so the outfit no longer shows through.
  - Things held or carried (a torch, a clipboard, a rope, a tool belt): add_custom_item with module equipment and a place, a low-poly mesh modelled at the origin and not parented; the builder puts it at its place.
  - Carried in both hands ("carry the rifle in both hands", "left hand on the barrel, right hand on the trigger", a ready carry): place both_hands (set_place for an item that exists). The builder holds it across the body, muzzle forward-down, right hand on the grip and left on the barrel, in every animation. Declare the grip and support with crew.two_hand_grips when you write the item.
  - Gloves, sunglasses, ear defenders, caps: set_accessories, or add_custom_item with module accessories.
- Never answer that something is not available when you can make it. If part of a request truly cannot be built (cloth that flows in the wind, real physics), make the closest thing and say plainly what you made instead.
- The person is nine modules: gender, skin, hair, shirt, pants, outfit, shoes, equipment, accessories (get_project lists what each owns). Where an item is held or carried (its place) is part of equipment. Work out which module or modules the request is about and change only those. Leave every other module exactly as it is: never remove, recolour, replace or reset anything the user did not mention. Matching a photo or a description is still one change per module it mentions, not a rebuild of the whole person.
- Describing a module (hair, shirt, pants, shoes, equipment, accessories) is add_custom_item with that module; it replaces that module's earlier described item and nothing else. Equipment items take a place; moving one is set_place.
- Equipment: """ + CARRY_RULE + """
- An outfit (suit) fills shirt and pants. Setting a shirt or pants clears the suit; choosing a suit clears shirt, pants and their described items. The tools do this for you.
""" + COMMON_RULES

PHOTO_RULES = """
Photographs and close-ups (the standard is a photo atlas: every photo a real stored file with its provenance):
- Look for real photographs of the exact vessel or type: news and official releases, government and navy sites, museums, Wikimedia Commons. Add each useful one with add_photo, giving the image file's own URL and full provenance: title, vessel, views, credit, date, observation, source page, status, rights, modelling use. Prefer the largest copy; never add a republished copy or a crop of a photo already added.
- An image Lester attached is added with add_photo and its upload_id, credit "Supplied by Lester".
- Never describe, cite or count a photo you have not added: the tool shows you each one, so describe what you see in it.
- Record which views are covered and which are missing with set_coverage (a missing top view is a finding, not a failure). Aim for port, starboard, bow, stern, top and details.
- Crop close-ups (add_closeup) of the fittings that change the model: engines, searchlights, hatches, rails, masts, the helm. Boxes are pixels of the photo; each crop is real pixels, never generated or enhanced.
- Claims may cite photos and close-ups (P01, C02). Add photos and close-ups before save_research, so the record cites what is stored."""

RESEARCH_PROMPT = """You research one real vessel so a 3D model of it can be built for a maritime simulator. The user is Lester: ex-navy, not an engineer.
- Use public sources only (WebSearch, WebFetch). Pick ONE exact baseline hull; sister craft are 'Comparison only'; other classes are not evidence.
- Every source gets an evidence status: Official, Photographed, Photo reading, Inference, Comparison only, Secondary lead, Unverified. Your rating is a judgement, never 'verified'.
- Every claim says what it changes in the model (consequence_for_model). Unknowns go to open_questions, not guesses.
- Propose articulation: what moves (P0 essential .. P2 nice), and what must not move.
- Save with save_research (it validates; fix and resave on errors). Then reply in two or three plain sentences: the verdict and the biggest open question.""" + PHOTO_RULES


CUSTOM_RESEARCH_PROMPT = """You research a vessel of any type that the user asked for in their own words (the brief) and usually with photos, so a 3D model of it can be built for a maritime simulator. The user is Lester: ex-navy, the product owner, not an engineer. You never build anything: you research, then draft a design plan the user approves; approving it is what builds the ship.
- Work from the brief, every attached photo, and public sources (WebSearch, WebFetch). If the photo shows a recognisable make or class, find it and make it the baseline; if not, say it is a generic vessel of that type and use comparable craft ('Comparison only') for proportions. Read the photos yourself: hull form (bow, stern, sheer), number of decks, window bands, colours, mast, radar arch, tender, fly-bridge. Those are 'Photo reading' claims.
- Every source and claim gets an evidence status: Official, Photographed, Photo reading, Inference, Comparison only, Secondary lead, Unverified. Never 'verified'.
- Every claim says what it changes in the model. Unknowns go to open_questions as concrete questions Lester can answer in one line, each with what you assume meanwhile ('How long is it? Meanwhile 24 m, from the photo's proportions'). Ask the few that change the model most, not everything.
- Amend, do not restart: the saved research and design plan so far are given below. Each user message is a correction, an answer or more photos: fold it into the research (answered questions become claims with the user as source, status 'Official' when they state a fact about their own vessel), and resave the whole record with save_research.
- When the research is good enough to build from, or the user asks for a plan, draft the design plan with save_design_plan (after save_research). Redraft it whenever the research changes. Keep its summary in plain English: size, hull form, each superstructure tier, colours, details to add, and assumptions with values.
- Articulation: what moves (radar spin, davit, tender crane) and what must not.
- The ship starts filed under "Other" in the ship list. While it is still under "Other", call set_category in the same turn you identify what kind of vessel it is (usually the first turn), before you reply: file it in the closest of the catalog's ship categories (a motor yacht goes under "Passenger and leisure") and say in your reply where you filed it. Do not leave it under "Other" for a later turn. Lester can change it in the ship list.
- Reply in two to four plain sentences: what you found or changed, the most important open question, whether a plan is ready to approve, and the category you filed it under if you filed it this turn.""" + PHOTO_RULES


def custom_research_context(project: dict, spec: dict, cat_entry: dict) -> str:
    """What a custom-ship research turn starts from: the brief, the research and plan so far, and
    the builder's knobs. The AI has no memory between turns, so this is how it amends rather than
    restarts."""
    research = project.get("research")
    plan = project.get("design_plan")
    derived = project.get("derived_from") or {}
    if derived:
        # One brief field (Lester, 29 Oct): on a variant it is what the variant was asked to change;
        # the original's request lives on in the copied research and design plan.
        said = (f"Brief (this is a variant of {derived.get('name')!r}; what it was asked to change): "
                f"{project.get('brief') or '(nothing: an exact copy)'}")
    else:
        said = f"Brief (the user's original request): {project.get('brief') or '(none given)'}"
    lines = [said,
             f"Research revision: {project.get('research_rev', 0)}",
             f"Filed under: {project.get('category') or 'Other'}",
             "Saved research so far: " + (json.dumps(research) if research else "none yet."),
             ]
    if plan:
        lines.append("Design plan so far (status " + str(plan.get("status")) + ", drafted against research revision "
                     f"{plan.get('research_rev')}): " + json.dumps({"summary": plan.get("summary"),
                                                                    "params": (plan.get("spec") or {}).get("params")}))
    else:
        lines.append("Design plan: none yet.")
    lines.append("Current model params: " + json.dumps(spec.get("params") or {}))
    docs = cat_entry.get("param_docs")
    if docs:
        lines.append("Custom builder params (docs and ranges): " + json.dumps(docs))
    return "\n".join(lines) + "\n\n"


def research_media_context(project: dict) -> str:
    """The photographs and close-ups already stored, so a research turn adds to them (and cites
    them) instead of fetching them again. The AI has no memory between turns."""
    research = project.get("research") or {}
    photos, closeups = research.get("photos") or [], research.get("closeups") or []
    if not photos:
        return "Photographs stored so far: none.\n\n"
    lines = ["Photographs stored so far:"]
    lines += [f"- {p['id']}: {p.get('title')} ({p.get('vessel')}; views {', '.join(p.get('views') or [])}; "
              f"{p.get('status')}; {p.get('width')}x{p.get('height')} px; from {p.get('image_url') or p.get('source')})"
              for p in photos]
    if closeups:
        lines.append("Close-ups: " + "; ".join(f"{c['id']} from {c.get('photo')} at {c.get('box')}: {c.get('title')}"
                                                for c in closeups))
    cov = research.get("coverage") or {}
    missing = [v for v in rm.COVERAGE_VIEWS if not cov.get(v)]
    if missing:
        lines.append("Views with no photograph yet: " + ", ".join(missing))
    return "\n".join(lines) + "\n\n"


def plan_build_prompt(plan: dict) -> str:
    """The build-mode turn after an approved plan has been built: add its details."""
    summary = "\n".join(f"- {s}" for s in plan.get("summary") or [])
    return ("The user approved this design plan and the hull and superstructure from it are now built and on "
            f"screen:\n{summary}\n\nAdd every detail the plan lists that the build does not have yet (fly-bridge "
            "furniture, rails, davits, antennas and so on) as custom ops, then rebuild and fix any errors. Do not "
            "change the size, hull form or tiers: they are what the user approved. Reply with what you added.")


def module_prompt(module: str) -> str:
    """Prefix for a scoped chat turn (contract §6: POST /chat with "module"; "placement" is equipment)."""
    module = cm.module_id(module) or module
    text = (f"You are editing only the {module} module ({cm.MODULE_DOCS[module]}). Change nothing else: any "
            "tool call that touches another module is refused and changes nothing. If the request needs "
            "another module, make the {module} part and say in the reply what the user should change in "
            "the other module.").replace("{module}", module)
    if module == "equipment":
        text += " " + CARRY_RULE
    return text + "\n\n"


def _lower_first(s: str) -> str:
    """"Small searchlight" -> "small searchlight", but "PC 303 mast" stays as it is."""
    return s[:1].lower() + s[1:] if s[:2].istitle() else s


def _words(keys) -> str:
    return ", ".join(str(k).replace("_", " ") for k in keys)


def _item_names(items) -> list:
    """Equipment and accessories entries are a name or {"name", "color", "place"}: just the names."""
    return [i.get("name", "an item") if isinstance(i, dict) else i for i in items]


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
        "set_equipment": f"Setting equipment: {_words(_item_names(a.get('equipment') or ['none']))}",
        "set_place": (f"Moving {_words([a.get('item') or 'an item'])} into both hands" if a.get("place") == "both_hands"
                      else f"Moving {_words([a.get('item') or 'an item'])} to the {_words([a.get('place') or 'new place'])}"),
        "set_accessories": f"Setting accessories: {_words(_item_names(a.get('accessories') or ['none']))}",
        "save_design_plan": "Drafting the design plan",
        "set_category": f"Filing it under {a.get('category') or 'a category'}",
        "add_photo": f"Adding a photograph: {_lower_first(str(a.get('title') or 'from the web'))}",
        "remove_photo": f"Removing photograph {a.get('id', '')}".rstrip(),
        "add_closeup": f"Cropping a close-up: {_lower_first(str(a.get('title') or 'a detail'))}",
        "remove_closeup": f"Removing close-up {a.get('id', '')}".rstrip(),
        "set_coverage": "Noting which views the photographs cover",
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


def system_prompt(kind: str, mode: str, base: str | None = None) -> str:
    if mode == "research":
        return CUSTOM_RESEARCH_PROMPT if kind == "ship" and base == "custom" else RESEARCH_PROMPT
    return SHIP_PROMPT if kind == "ship" else CREW_PROMPT


# ---- runners ----------------------------------------------------------------------------

class AgentRunner(Protocol):
    def run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str: ...


class ClaudeAgentRunner:
    """Real runner: the local Claude Code login through claude-agent-sdk (no API key)."""

    # Research does many small steps (searches, page reads, a download and crop per photo): the
    # 29 Oct live run of "do a deep web research" ran out at 24. Building keeps the tighter limit.
    RESEARCH_MAX_TURNS = 60

    def __init__(self, model: str = "claude-opus-5-5", max_turns: int = 24, timeout_s: float = 1200):
        self.model, self.max_turns, self.timeout_s = model, max_turns, timeout_s

    def run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str:
        return asyncio.run(asyncio.wait_for(self._run(session, prompt, mode, log), self.timeout_s))

    async def _run(self, session: AgentSession, prompt: str | list[dict], mode: str, log: Log) -> str:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock,
                                      ToolResultBlock, ToolUseBlock, UserMessage, create_sdk_mcp_server,
                                      query, tool)

        def make(name: str, desc: str, schema: dict):
            @tool(name, desc, schema)
            async def handler(args: dict) -> dict:
                blocks, is_error = await asyncio.to_thread(session.call_content, name, args)
                out: dict = {"content": blocks}
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
            setting_sources=[], max_turns=self.RESEARCH_MAX_TURNS if mode == "research" else self.max_turns, model=self.model,
            system_prompt=system_prompt(session.kind, mode, session.base),
            cwd=str(session.store.version_dir(session.pid, 0).parent),
            # Photos and attachments travel inside messages; the SDK's 1 MB default killed a research
            # turn on a large photo (live sampan run, 29 Oct). Previews are capped too (research_media).
            max_buffer_size=20 * 1024 * 1024)
        texts: list[str] = []
        uses: dict[str, tuple[str, Any]] = {}  # tool-use id -> (tool name, input), to keep what web tools return
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
                        uses[b.id] = (name, b.input)
                        log(f"AI → {name}")
                        doing = describe_tool(name, b.input)
                        if doing:
                            log(f"• {doing}")
            elif isinstance(m, UserMessage) and isinstance(m.content, list):
                # Results of the built-in web tools: keep what the AI read (contract §12, materials).
                for b in m.content:
                    if isinstance(b, ToolResultBlock) and not b.is_error and b.tool_use_id in uses:
                        name, args = uses[b.tool_use_id]
                        if name in ("WebSearch", "WebFetch"):
                            await asyncio.to_thread(session.record_material, name, args, b.content)
            elif isinstance(m, ResultMessage):
                if m.is_error and m.subtype == "error_max_turns":
                    # Out of steps is not a failure: everything the tools saved stays saved.
                    return out_of_steps_reply(texts)
                if m.is_error:
                    raise RuntimeError(f"AI run failed ({m.subtype}): {m.result}")
                final = m.result
        return (final or (texts[-1] if texts else "")).strip()


def out_of_steps_reply(texts: list[str]) -> str:
    """The reply when the AI used all its steps: what it last said, plus a plain note."""
    last = texts[-1].strip() if texts else ""
    note = ("I ran out of steps before finishing. Everything I saved so far is kept; ask me to "
            "continue and I'll pick up from there.")
    return f"{last}\n\n{note}".strip()


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
