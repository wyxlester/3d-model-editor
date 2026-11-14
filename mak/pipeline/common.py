"""Shared helpers: config, manifest, output layout, findings."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"

DIS_TYPE_RE = re.compile(r"^-?\d+(:-?\d+){6}$")  # 7 fields, as used in .metx files
DOMAINS = {"land", "air", "surface", "subsurface", "space", "lifeform"}
FORWARD_AXES = {"+Y", "-Y", "+X", "-X"}

DEFAULT_VRF_DIR = r"C:\MAK\vrforces5.1.1"
DEFAULT_SHARED_DATA = r"C:\MAK\SharedData\18\latest"
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
LIMITS = {"max_triangles": 100000, "max_texture_px": 4096, "max_extent_m": 400.0, "min_extent_m": 0.05}


def make_config(vrf_dir: str | Path | None = None, shared_data: str | Path | None = None) -> dict:
    """All paths the pipeline uses, from one VR-Forces folder and one SharedData version folder.
    Defaults: ME3D_VRF_DIR / ME3D_MAK_SHARED_DATA, else the standard C:\\MAK install (contract §9b)."""
    vrf = Path(vrf_dir or os.environ.get("ME3D_VRF_DIR") or DEFAULT_VRF_DIR)
    shared = Path(shared_data or os.environ.get("ME3D_MAK_SHARED_DATA") or DEFAULT_SHARED_DATA)
    return {
        "vrforces_root": vrf.as_posix(),
        "shared_data_dir": shared.as_posix(),
        "definitions_dir": (shared / "ModelData/Configuration/Definitions/Entity").as_posix(),
        "sms_dir": (vrf / "data/simulationModelSets").as_posix(),
        "donor_sms": "EntityLevel",
        "fbx_node_name_map": (vrf / "appData/importConfig/FBX_NodeNameMap.csv").as_posix(),
        "medf_tool": (vrf / "bin64/medfTool.exe").as_posix(),
        "model_format": os.environ.get("ME3D_MAK_FORMAT", "flt"),
        "blender": os.environ.get("ME3D_BLENDER", DEFAULT_BLENDER),
        "limits": dict(LIMITS),
    }


def load_config(path: Path | None = None) -> dict:
    """A JSON config file if given (same keys as make_config), else make_config()'s defaults."""
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return make_config()


class InputError(ValueError):
    """Bad command-line input; the message tells the user what to do."""


def check_model_dir(arg: Path) -> Path:
    """Model folders look like models/<Name>/ with model.glb + manifest.json. Explain anything else."""
    if arg.suffix.lower() == ".glb":
        name = re.sub(r"[^A-Za-z0-9]", "", arg.stem.title()) or "MyModel"
        folder = arg.parent / name
        raise InputError(
            f"'{arg.as_posix()}' is a .glb file, but the pipeline takes a model folder.\n"
            f"  Each model needs its own folder:\n"
            f"    {folder.as_posix()}/model.glb      <- move and rename the .glb here\n"
            f"    {folder.as_posix()}/manifest.json  <- information that isn't in the mesh\n"
            f"  If the folder also holds Blender exports (parts.json, spec.json, build_report.json), draft the manifest with:\n"
            f"    python -m pipeline.draft_manifest {folder.as_posix()}\n"
            f"  then run:\n"
            f"    python -m pipeline.run {folder.as_posix()}")
    if not arg.is_dir():
        raise InputError(f"model folder '{arg.as_posix()}' does not exist")
    if not (arg / "model.glb").exists() and _is_character(arg):
        return arg                       # humans and animals pick a DI-Guy character; there is no model to import
    if not (arg / "model.glb").exists():
        others = sorted(p.name for p in arg.glob("*.glb"))
        hint = f"rename '{others[0]}' to model.glb" if len(others) == 1 else "put the model there as model.glb"
        raise InputError(f"'{arg.as_posix()}' has no model.glb; {hint}")
    if not (arg / "manifest.json").exists():
        exports = [n for n in ("parts.json", "spec.json", "build_report.json") if (arg / n).exists()]
        how = (f"draft one from {', '.join(exports)} with: python -m pipeline.draft_manifest {arg.as_posix()}"
               if exports else f"copy models/_example/manifest.json there and edit it")
        raise InputError(f"'{arg.as_posix()}' has no manifest.json; {how}")
    return arg


def _is_character(model_dir: Path) -> bool:
    from . import profiles
    try:
        m = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
        return profiles.profile_for(m).route == "diguy"
    except (OSError, ValueError):
        return False


def load_manifest(model_dir: Path) -> dict:
    from . import profiles
    m = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    todo = [k for k, v in m.items() if isinstance(v, str) and v.startswith("TODO")]
    if todo:
        raise InputError(f"{model_dir.as_posix()}/manifest.json still has TODO values: "
                         + "; ".join(f"{k} ({m[k][6:]})" for k in todo))
    problems = []
    if m.get("domain") and m["domain"] not in DOMAINS:
        problems.append(f"domain must be one of {sorted(DOMAINS)}")
    route = "model"
    try:
        profiles.apply_defaults(m)       # kind -> domain, platform .ope, ModelData folder (profiles.py)
        route = profiles.profile_for(m).route
    except profiles.ProfileError as e:
        problems.append(str(e))
    if route == "diguy":
        m.setdefault("forward_axis_in_blender", "+Y")
        if not m.get("character_type"):
            problems.append("a human or animal needs 'character_type' (a DI-Guy character; see "
                            "python -m pipeline.mtl characters)")
    for key in ("name", "display_name", "dis_type", "forward_axis_in_blender"):
        if not m.get(key):
            problems.append(f"manifest missing '{key}'")
    if m.get("dis_type") and not DIS_TYPE_RE.match(m["dis_type"]):
        problems.append("dis_type must be 7 colon-separated integers, e.g. 1:1:225:6:99:1:0")
    if m.get("forward_axis_in_blender") and m["forward_axis_in_blender"] not in FORWARD_AXES:
        problems.append(f"forward_axis_in_blender must be one of {sorted(FORWARD_AXES)}")
    if m.get("name") and not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", m["name"]):
        problems.append("name must be an identifier (letters, digits, underscore)")
    for i, p in enumerate(m.get("articulated_parts") or []):
        if not isinstance(p, dict) or not p.get("node") or p.get("dis_part") in (None, ""):
            problems.append(f"articulated_parts[{i}] needs 'node' and a 'dis_part' code (remove it to leave the part static)")
    if problems:
        raise InputError(f"{model_dir}/manifest.json: " + "; ".join(problems))
    return m


@dataclass
class Layout:
    """Where each artefact goes inside output/<name>/. Mirrors the install so deploy is a copy."""
    root: Path
    name: str
    category: str
    fmt: str = "flt"                     # model file format: "flt" (OpenFlight, no Blender) or "fbx" (Blender)

    @property
    def shared(self) -> Path:            # stands in for $(SHARED_DATA_DIR)
        return self.root / "SharedData"

    @property
    def model_dir(self) -> Path:
        return self.shared / "ModelData" / self.category / self.name

    @property
    def model_file(self) -> Path:
        return self.model_dir / f"{self.name}.{self.fmt}"

    @property
    def fbx(self) -> Path:
        return self.model_dir / f"{self.name}.fbx"

    @property
    def visuals(self) -> Path:
        """<sms>/gui/visuals, where the Simulation Object Editor keeps a custom SMS's visual definitions (UG 64.4;
        confirmed by the hand-import test: Entity/<name>.leaf + .metx, UserData/<name>.ommx)."""
        return self.root / "sms" / "gui" / "visuals"

    @property
    def definitions(self) -> Path:
        return self.visuals / "Entity"

    @property
    def ommx(self) -> Path:
        return self.visuals / "UserData" / f"{self.name}.ommx"

    @property
    def visual_import(self) -> Path:
        """All three definition files in one folder, for SOE File > Import Visual Models (UG 65.2.10), which reads
        every .leaf in the chosen directory."""
        return self.root / "visual-import"

    @property
    def leaf(self) -> Path:
        return self.definitions / f"{self.name}.leaf"

    @property
    def metx(self) -> Path:
        return self.definitions / f"{self.name}.metx"

    @property
    def sms_vrfsim(self) -> Path:
        return self.root / "sms" / "vrfSim"

    def entity(self, display_name: str) -> Path:
        return self.sms_vrfsim / f"{display_name}.entity"

    @property
    def ammunition_additions(self) -> Path:
        """Munitions: the entry to merge into <sms>/ammunitionParams.mtl at deploy (users guide 73)."""
        return self.root / "sms" / "ammunitionParams.additions.mtl"

    @property
    def node_map_additions(self) -> Path:
        """Rows to append to <vrforces>/appData/importConfig/FBX_NodeNameMap.csv at deploy time."""
        return self.root / "appData" / "importConfig" / "FBX_NodeNameMap.additions.csv"

    @property
    def report(self) -> Path:
        return self.root / "report.json"

    def model_shared_path(self) -> str:
        """The value VR-Forces expects in the model definition's `filename` parameter."""
        return f"$(SHARED_DATA_DIR)/ModelData/{self.category}/{self.name}/{self.name}.{self.fmt}"


MODEL_FORMATS = ("flt", "fbx")


def layout_for(manifest: dict, output_root: Path | None = None, fmt: str = "flt") -> Layout:
    if fmt not in MODEL_FORMATS:
        raise InputError(f"model_format must be one of {MODEL_FORMATS}, got '{fmt}'")
    base = output_root or ROOT / "output"
    return Layout(base / manifest["name"], manifest["name"], manifest["model_category_dir"].strip("/"), fmt)


@dataclass
class Finding:
    level: str   # "error" | "warning" | "info" | "pending"
    code: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def print_findings(title: str, findings: list[Finding]) -> None:
    print(f"== {title}")
    for f in findings:
        print(f"  [{f.level.upper():7}] {f.code}: {f.message}")
    if not findings:
        print("  (no findings)")


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
