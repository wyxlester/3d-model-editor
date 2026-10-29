"""Install the crew import hook into an Unreal 5.8 project, once. After that, File > Import Into
Level of a crew GLB gives one crew mesh with every item of equipment on its bone, playing idle.

In the editor, Output Log, box at the bottom set to "Cmd":

    py "E:/Github/3d-model-editor/unreal/install_import_hook.py"
    py "E:/Github/3d-model-editor/unreal/install_import_hook.py" --remove      (takes it all out)

or with the editor closed:

    UnrealEditor-Cmd <Project>.uproject -run=pythonscript -script="<repo>/unreal/install_import_hook.py"

What it changes in the project (all of it undone by --remove):

- Content/Python/me3d_import_hook.py, and a line in Content/Python/init_unreal.py, which Unreal
  runs at start, so the hook loads in every session. The hook lives in the project rather than the
  repo because Unreal names a Python class by its file, and only files in the project get a name
  that stays the same between sessions - the settings below refer to it by that name.
- /Game/ME3D/Interchange/: copies of Epic's two glTF asset pipelines with "Combine Skeletal Meshes"
  set to "Combine All That Use Same Skeleton", so the crew comes in as one skeletal mesh.
- Config/DefaultEngine.ini, Interchange settings: glTF imports use those copies (Content Browser
  and Import Into Level), and Import Into Level also runs the hook. The running editor gets the
  same settings straight away, so no restart is needed.
"""
import importlib.util
import shutil
import sys
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import me3d_hook_config as cfg  # noqa: E402

GLTF_TRANSLATOR = "InterchangeGLTFTranslator"
OURS = "/Game/ME3D/Interchange"
COPIES = {  # Epic's pipeline -> our copy with combining on
    "/Interchange/Pipelines/DefaultGLTFAssetsPipeline": f"{OURS}/ME3D_GLTFAssetsPipeline",
    "/Interchange/Pipelines/DefaultGLTFSceneAssetsPipeline": f"{OURS}/ME3D_GLTFSceneAssetsPipeline",
}


def log(msg):
    unreal.log(f"install_import_hook: {msg}")


def full(package):
    """/Game/X/Y -> /Game/X/Y.Y, the form settings store."""
    return f"{package}.{package.rsplit('/', 1)[-1]}"


def text_of(path):
    return str(path.export_text()).strip('"') if hasattr(path, "export_text") else str(path)


def project_dir(sub):
    return Path(unreal.Paths.convert_relative_path_to_full(sub)).resolve()


def load_hook(installed):
    """Load the hook from the project's copy (not the repo's, which sits next to this script) and
    return the module and the settings path of its pipeline. An editor that already has it loaded
    keeps that one until it restarts."""
    mod = sys.modules.get("me3d_import_hook")
    if mod is None:
        spec = importlib.util.spec_from_file_location("me3d_import_hook", installed)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["me3d_import_hook"] = mod
        spec.loader.exec_module(mod)
    path = mod.ME3DCrewEquipmentPipeline.get_default_object().get_path_name()
    if "_0x" in path:  # see the module docstring: only a file inside the project gets a stable name
        raise RuntimeError(f"the hook was loaded from {mod.__file__}, outside the project, so its name "
                           f"({path}) will not hold between sessions")
    if Path(mod.__file__).read_bytes() != (HERE / "me3d_import_hook.py").read_bytes():
        unreal.log_warning("install_import_hook: this editor is running an older copy of the hook; "
                           "restart it to use the one just installed")
    return mod, path


def combining_pipeline(source, dest, skeleton=None):
    """Save at `dest` a copy of the Interchange assets pipeline `source` (e.g. Epic's
    /Interchange/Pipelines/DefaultGLTFSceneAssetsPipeline) with "Combine All That Use Same
    Skeleton", so a crew's skinned parts become one SkeletalMesh, and, if given, `skeleton` to
    import onto. Reuses a copy already at `dest`. Returns its object path. The Unreal harness
    (import_package.py) uses it too."""
    lib = unreal.EditorAssetLibrary
    if not lib.does_asset_exist(dest):
        # by the loaded object: headless, the asset registry may not have scanned /Interchange yet
        lib.duplicate_loaded_asset(unreal.load_asset(source), dest)
    p = unreal.load_asset(dest)
    if p is None:
        raise RuntimeError(f"could not copy {source} to {dest}")
    p.get_editor_property("mesh_pipeline").set_editor_property(
        "combine_skeletal_meshes_behavior", unreal.InterchangeCombineSkeletalMeshesBehavior.BY_SKELETON)
    if skeleton is not None:
        p.get_editor_property("common_skeletal_meshes_and_animations_properties").set_editor_property("skeleton", skeleton)
    lib.save_asset(dest, only_if_is_dirty=False)
    return p.get_path_name()


def edit_settings(prop, swap, add=None, drop=None):
    """Swap/add/drop pipelines in every glTF entry of every stack of one settings struct
    (content_import_settings or scene_import_settings); returns the struct."""
    settings = unreal.get_default_object(unreal.InterchangeProjectSettings)
    block = settings.get_editor_property(prop)
    stacks = block.get_editor_property("pipeline_stacks")
    for name in list(stacks.keys()):
        stack = stacks[name]
        per = list(stack.get_editor_property("per_translator_pipelines"))
        for tp in per:
            if GLTF_TRANSLATOR in text_of(tp.get_editor_property("translator")):
                paths = [text_of(p) for p in tp.get_editor_property("pipelines")]
                new = cfg.gltf_pipelines(paths, swap, add, drop)
                tp.set_editor_property("pipelines", [unreal.SoftObjectPath(p) for p in new])
                log(f"{prop} '{name}', glTF: {', '.join(p.rsplit('.', 1)[-1] for p in new)}")
        stack.set_editor_property("per_translator_pipelines", per)
        stacks[name] = stack
    block.set_editor_property("pipeline_stacks", stacks)
    settings.set_editor_property(prop, block)   # the running editor
    return block


def read_exact(path):
    """The file as it is on disk, line endings included (read_text turns CRLF into LF)."""
    if not path.is_file():
        return ""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_config(content, scene, removing=False):
    """Install: set both keys, keeping a copy of the file as it was. Remove: if the project did not
    set them before, take them out again (so Epic's defaults apply, as before); otherwise write
    them back with Epic's pipelines. Either way the copy goes."""
    ini = project_dir(unreal.Paths.project_config_dir()) / "DefaultEngine.ini"
    text = read_exact(ini)
    backup = ini.with_name("DefaultEngine.ini.before-me3d")
    values = {"ContentImportSettings": content.export_text(), "SceneImportSettings": scene.export_text()}
    if not removing:
        if ini.is_file() and not backup.exists():
            shutil.copyfile(ini, backup)
        text = cfg.set_ini_keys(text, cfg.SECTION, values)
    else:
        before = read_exact(backup)
        had_them = cfg.without_ini_keys(before, cfg.SECTION, values) != before
        text = cfg.set_ini_keys(text, cfg.SECTION, values) if had_them else cfg.without_ini_keys(text, cfg.SECTION, values)
        backup.unlink(missing_ok=True)
    ini.write_text(text, encoding="utf-8", newline="")
    log(f"wrote the Interchange settings to {ini}")
    saved = project_dir(unreal.Paths.project_saved_dir()) / "Config"
    for user_ini in saved.glob("*/Engine.ini"):
        t = user_ini.read_text(encoding="utf-8", errors="replace")
        if cfg.SECTION in t and ("ContentImportSettings" in t or "SceneImportSettings" in t):
            unreal.log_warning(f"install_import_hook: {user_ini} also sets Interchange import settings and "
                               "overrides DefaultEngine.ini; remove them there (Project Settings > Interchange > Reset)")


def install():
    content_python = project_dir(unreal.Paths.project_content_dir()) / "Python"
    content_python.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "me3d_import_hook.py", content_python / "me3d_import_hook.py")
    init = content_python / "init_unreal.py"
    init.write_text(cfg.with_line(init.read_text(encoding="utf-8") if init.is_file() else "", cfg.INIT_LINE),
                    encoding="utf-8")
    hook = load_hook(content_python / "me3d_import_hook.py")[1]
    for epic, ours in COPIES.items():
        combining_pipeline(epic, ours)
    swap = {full(e): full(o) for e, o in COPIES.items()}
    content = edit_settings("content_import_settings", swap)
    scene = edit_settings("scene_import_settings", swap, add=hook)
    write_config(content, scene)
    log(f"installed: hook {hook}. File > Import Into Level a crew GLB to use it.")


def remove():
    content_python = project_dir(unreal.Paths.project_content_dir()) / "Python"
    hook = None
    if (content_python / "me3d_import_hook.py").is_file():
        hook = load_hook(content_python / "me3d_import_hook.py")[1]
    back = {full(o): full(e) for e, o in COPIES.items()}
    content = edit_settings("content_import_settings", back)
    scene = edit_settings("scene_import_settings", back, drop=hook)
    write_config(content, scene, removing=True)
    init = content_python / "init_unreal.py"
    if init.is_file():
        rest = cfg.without_line(init.read_text(encoding="utf-8"), cfg.INIT_LINE)
        init.write_text(rest, encoding="utf-8") if rest.strip() else init.unlink()
    (content_python / "me3d_import_hook.py").unlink(missing_ok=True)
    for pyc in content_python.glob("__pycache__/me3d_import_hook.*.pyc"):
        pyc.unlink()
    for folder in (content_python / "__pycache__", content_python):   # only if nothing else is in them
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    for ours in COPIES.values():
        if unreal.EditorAssetLibrary.does_asset_exist(ours):
            unreal.EditorAssetLibrary.delete_asset(ours)
    log("removed. Imports use Epic's glTF pipelines again; restart the editor to unload the hook.")


if __name__ == "__main__":
    remove() if "--remove" in sys.argv[1:] else install()
