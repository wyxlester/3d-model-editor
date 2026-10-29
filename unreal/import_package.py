"""Import an `me3d.package/1` into the harness project and prove it works (docs/CONTRACT.md s.9).

Runs INSIDE Unreal 5.8, headless, as a commandlet:

    UnrealEditor-Cmd Harness.uproject -run=pythonscript -script=<this file> -unattended -nullrhi

in separate processes, chosen by environment variables (set by run_import_test.py):

    ME3D_PACKAGE=<package dir>  ME3D_PHASE=import   new level, the ship, saved
    ME3D_PHASE=crew  ME3D_CREW_ID=<id>               one distinct crew member, at each of its
                                                    stations, saved (one process per crew id)
    ME3D_PHASE=verify                               load the saved level from disk, check it,
                                                    write <package>/unreal_import_report.json
    ME3D_IMPORT_LOG=<log>[<pathsep><log>...]        the import processes' logs, scanned for
                                                    Error: lines by the verify phase

One import per process because, headless, an Interchange import after an Import Into Level
starts and never finishes (5.8). The checks run in the last process on purpose: everything they
look at has come back from disk, so "the actors survived the save" is proved rather than assumed
(library: unreal-58-landscape-creation-has-no-python-path - Python-spawned actors may not survive
save).

Ship: Interchange *scene* import of the GLB (default glTF scene pipelines). It makes one
StaticMesh asset per glTF mesh and one StaticMeshActor per node, attached in the glTF
hierarchy, with each node's origin as the actor origin - so parts stay separate and pivots
are kept. Crew: Import Into Level, as a user does once install_import_hook.py has set up their
project - the skinned parts combined into one SkeletalMesh (later crew on the first crew's
Skeleton) and the hook (me3d_import_hook.py) putting each item of equipment back on its bone -
then moved to its station.
"""
import json
import math
import os
import sys
import traceback
from pathlib import Path

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
import me3d_frames as fr  # noqa: E402
import me3d_import_hook as hook  # noqa: E402
from install_import_hook import combining_pipeline  # noqa: E402

PKG_DIR = Path(os.environ["ME3D_PACKAGE"]).resolve()
PHASE = os.environ.get("ME3D_PHASE", "import")
CREW_ID = os.environ.get("ME3D_CREW_ID", "")
IMPORT_LOG = os.environ.get("ME3D_IMPORT_LOG", "")
STATE_FILE = PKG_DIR / ".unreal_import_state.json"
REPORT_FILE = PKG_DIR / "unreal_import_report.json"
BEGIN, END = "ME3D_IMPORT_BEGIN", "ME3D_IMPORT_END"
TOL_CM = 1.0
TOL_DEG = 0.5

les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors_ss = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assets_lib = unreal.EditorAssetLibrary


class Checks:
    def __init__(self, items=None):
        self.items = list(items or [])

    def add(self, name, ok, detail=""):
        self.items.append({"name": name, "pass": bool(ok), "detail": str(detail)})
        unreal.log(f"ME3D_CHECK {'PASS' if ok else 'FAIL'} {name}: {detail}")
        return ok


# ------------------------------------------------------------------------------------ helpers
def read_manifest():
    return json.loads((PKG_DIR / "manifest.json").read_text(encoding="utf-8"))


def paths_for(manifest):
    pid = manifest["id"]
    base = f"/Game/ME3D/{pid}"
    return base, f"{base}/L_{pid}"


def validate_manifest(m):
    problems = []
    if m.get("schema") != "me3d.package/1":
        problems.append(f"schema is {m.get('schema')!r}, expected 'me3d.package/1'")
    pid = m.get("id", "")
    if not pid or not all(c.isalnum() or c == "_" for c in pid):
        problems.append(f"id {pid!r} must be [A-Za-z0-9_]+ (Unreal asset names)")
    ship = m.get("ship") or {}
    for key in ("id", "glb", "articulation"):
        if key not in ship:
            problems.append(f"ship.{key} missing")
    for key in ("glb", "articulation", "meta"):
        if key in ship and not (PKG_DIR / ship[key]).is_file():
            problems.append(f"ship.{key} file not found: {ship[key]}")
    crew = m.get("crew")
    if not isinstance(crew, list):
        problems.append("crew must be a list")
        crew = []
    for i, c in enumerate(crew):
        for key in ("id", "glb", "station", "animation", "location_m", "rotation_deg"):
            if key not in c:
                problems.append(f"crew[{i}].{key} missing")
        if "glb" in c and not (PKG_DIR / c["glb"]).is_file():
            problems.append(f"crew[{i}].glb file not found: {c['glb']}")
        for key in ("location_m", "rotation_deg"):
            if key in c and (not isinstance(c[key], list) or len(c[key]) != 3):
                problems.append(f"crew[{i}].{key} must be [x, y, z]")
    return problems


def ue_vec(t):
    return unreal.Vector(float(t[0]), float(t[1]), float(t[2]))


def ue_rot(rpy):
    roll, pitch, yaw = rpy
    return unreal.Rotator(roll=float(roll), pitch=float(pitch), yaw=float(yaw))


def vtuple(v):
    return (v.x, v.y, v.z)


def all_actors():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))


def labelled(actors):
    """{label: actor} for our actors only (skips the level's built-in Brush/volumes/etc.)."""
    out = {}
    for a in actors:
        if isinstance(a, (unreal.StaticMeshActor, unreal.SkeletalMeshActor)) or \
                a.get_class().get_name() == "Actor":
            out[a.get_actor_label()] = a
    return out


def world_aabb(comp):
    lo, hi = comp.get_local_bounds()
    xf = comp.get_world_transform()
    pts = [vtuple(xf.transform_location(unreal.Vector(x, y, z)))
           for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def assets_of(path, cls):
    out = []
    for p in assets_lib.list_assets(path, recursive=True, include_folder=False):
        a = unreal.load_asset(p)
        if isinstance(a, cls):
            out.append(a)
    return out


def axes_close(rot_a, rot_b):
    xa = (rot_a.get_forward_vector(), rot_a.get_right_vector(), rot_a.get_up_vector())
    xb = (rot_b.get_forward_vector(), rot_b.get_right_vector(), rot_b.get_up_vector())
    worst = 0.0
    for a, b in zip(xa, xb):
        dot = max(-1.0, min(1.0, a.x * b.x + a.y * b.y + a.z * b.z))
        worst = max(worst, math.degrees(math.acos(dot)))
    return worst


def import_ship_scene(dest, glb):
    """Interchange scene import with the project's default glTF scene stack. Blocks until done
    (wait_until_all_tasks_done); the actors land in the level that is open."""
    mgr = unreal.InterchangeManager.get_interchange_manager_scripted()
    src = unreal.InterchangeManager.create_source_data(str(glb))
    params = unreal.ImportAssetParameters()
    params.is_automated = True
    params.replace_existing = True
    ok = mgr.import_scene(dest, src, params)
    mgr.wait_until_all_tasks_done(True)
    return ok


def crew_pipeline(base, cid, skeleton=None):
    """The combining pipeline install_import_hook.py sets up for a user's project (one
    SkeletalMesh per crew member) and, for every crew after the first, the first crew's skeleton.
    Its own asset per crew: overrides must be saved assets."""
    return combining_pipeline("/Interchange/Pipelines/DefaultGLTFSceneAssetsPipeline",
                              f"{base}/Pipelines/P_Crew_{cid}", skeleton)


def import_crew_scene(dest, glb, pipeline):
    """Import Into Level of one crew GLB, as a user does once install_import_hook.py has set up
    their project: the combining pipeline above, Epic's glTF and level pipelines, then our hook
    (unreal/me3d_import_hook.py), which puts each item of equipment back on its bone. Returns the
    actors it made. One per process: see run_import_test.py."""
    hook_path = hook.ME3DCrewEquipmentPipeline.get_default_object().get_path_name()
    before = set(all_actors())
    mgr = unreal.InterchangeManager.get_interchange_manager_scripted()
    params = unreal.ImportAssetParameters()
    params.is_automated = True
    params.replace_existing = True
    ov = params.get_editor_property("override_pipelines")
    for p in (pipeline, "/Interchange/Pipelines/DefaultGLTFPipeline.DefaultGLTFPipeline",
              "/Interchange/Pipelines/DefaultSceneLevelPipeline.DefaultSceneLevelPipeline", hook_path):
        ov.append(unreal.SoftObjectPath(p))
    params.set_editor_property("override_pipelines", ov)
    mgr.import_scene(dest, unreal.InterchangeManager.create_source_data(str(glb)), params)
    mgr.wait_until_all_tasks_done(True)
    return [a for a in all_actors() if a not in before]


def clone_crew(rig, items):
    """Another crew member like `rig`: same mesh, each item on the same bone at the same offset."""
    xf = rig.get_actor_transform()
    r = actors_ss.spawn_actor_from_class(unreal.SkeletalMeshActor, xf.translation, xf.rotation.rotator())
    r.skeletal_mesh_component.set_skeletal_mesh_asset(rig.skeletal_mesh_component.get_skeletal_mesh_asset())
    out = []
    for it in items:
        ixf = it.get_actor_transform()
        a = actors_ss.spawn_actor_from_class(unreal.StaticMeshActor, ixf.translation, ixf.rotation.rotator())
        a.static_mesh_component.set_mobility(it.static_mesh_component.mobility)
        a.static_mesh_component.set_static_mesh(it.static_mesh_component.static_mesh)
        if it.get_attach_parent_actor() == rig:
            hook.attach_to_bone(a, r, it.root_component.get_attach_socket_name(),
                                it.root_component.get_relative_transform())
        out.append(a)
    return r, out


VIEW_PREFIX = "ME3D_View_"   # actors only for a person looking at the level; no check counts them


def add_viewing_set(base):
    """Sun, sky and a sea at the waterline (z = 0, contract section 1), so the level makes sense
    when the app's "Open in Unreal" opens it in the editor. Nothing here is part of the package."""
    sun = actors_ss.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 2000),
                                           unreal.Rotator(roll=0, pitch=-35, yaw=-50))
    sun.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)   # live light: nothing to bake
    sun.light_component.set_atmosphere_sun_light(True)
    sky = actors_ss.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 2000), unreal.Rotator(0, 0, 0))
    sky.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    sky.light_component.set_real_time_capture(True)
    air = actors_ss.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    fog = actors_ss.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    water = tools.create_asset("MI_Sea", f"{base}/View", unreal.MaterialInstanceConstant,
                               unreal.MaterialInstanceConstantFactoryNew())
    water.set_editor_property("parent", unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial"))
    unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
        water, "Color", unreal.LinearColor(0.02, 0.09, 0.16, 1.0))
    unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(water, "Roughness", 0.15)
    sea = actors_ss.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    sea.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Plane"))
    sea.static_mesh_component.set_material(0, water)
    sea.set_actor_scale3d(unreal.Vector(400, 400, 1))   # the plane is 1 m: 400 m of sea
    for actor, name in ((sun, "Sun"), (sky, "SkyLight"), (air, "Sky"), (fog, "Haze"), (sea, "Sea")):
        actor.set_actor_label(VIEW_PREFIX + name)


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.is_file() else {}


def save_level(state):
    saved_level = les.save_current_level()
    saved_all = unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    state.setdefault("saved", []).append({"level": bool(saved_level), "dirty_packages": bool(saved_all)})
    state["actors_before_save"] = sorted(labelled(all_actors()))


# ------------------------------------------------------------------------------------ phase 1
def phase_import():
    """The level and the ship."""
    checks = Checks()
    state = {"checks": [], "ship_done": False, "crew_done": [], "crew_assets": {}, "crew_actors": [],
             "reuse_notes": []}
    m = read_manifest()
    problems = validate_manifest(m)
    checks.add("manifest_valid", not problems, "; ".join(problems) or
               f"me3d.package/1 '{m['id']}', ship '{m['ship']['id']}', {len(m['crew'])} crew")
    if problems:
        state["checks"] = checks.items
        return state
    base, level = paths_for(m)
    state["level"] = level
    if not les.new_level(level):
        checks.add("level_created", False, f"new_level({level}) returned False")
        state["checks"] = checks.items
        return state
    add_viewing_set(base)
    unreal.log(BEGIN)
    if not import_ship_scene(f"{base}/Ship/", PKG_DIR / m["ship"]["glb"]):
        checks.add("ship_scene_import", False, "InterchangeManager.import_scene returned False")
    save_level(state)
    unreal.log(END)
    state["checks"] = checks.items
    state["ship_done"] = True
    return state


# ------------------------------------------------------------------------------------ phase 1b
def phase_crew(state, cid):
    """One distinct crew member: import it, then put a copy at each station that uses it."""
    m = read_manifest()
    base, level = paths_for(m)
    if not les.load_level(level):
        raise RuntimeError(f"load_level({level}) failed")
    entries = [(i, c) for i, c in enumerate(m["crew"]) if c["id"] == cid]
    unreal.log(BEGIN)
    shared = state.get("shared_skeleton")
    skeleton = unreal.load_asset(shared) if shared else None
    new = import_crew_scene(f"{base}/Crew/{cid}/", PKG_DIR / entries[0][1]["glb"],
                            crew_pipeline(base, cid, skeleton))
    rigs = [a for a in new if isinstance(a, unreal.SkeletalMeshActor)]
    if not rigs:
        state["reuse_notes"].append(f"{cid}: Import Into Level made no skeletal mesh actor"
                                    + (" (on the shared skeleton)" if skeleton else ""))
        unreal.log(END)
        return
    rig = rigs[0]
    mesh = rig.skeletal_mesh_component.get_skeletal_mesh_asset()
    state["crew_assets"][cid] = [x.get_path_name() for x in assets_of(f"{base}/Crew/{cid}/", unreal.SkeletalMesh)]
    if not shared and mesh is not None:
        state["shared_skeleton"] = mesh.get_editor_property("skeleton").get_path_name()
    items = [a for a in new if isinstance(a, unreal.StaticMeshActor)]
    names = [a.get_actor_label() for a in items]
    # The import hangs the crew off empties for the glTF scene and armature nodes; the crew stands
    # at its station instead, so they go.
    keep = unreal.AttachmentRule.KEEP_WORLD
    loose = unreal.DetachmentRule.KEEP_WORLD
    rig.detach_from_actor(loose, loose, loose)
    for a in new:
        if a not in rigs and a not in items:
            actors_ss.destroy_actor(a)
    copies = [(rig, items)] + [clone_crew(rig, items) for _ in entries[1:]]

    stations = labelled(all_actors())
    anims = assets_of(f"{base}/Crew/{cid}/", unreal.AnimSequence)
    stem = Path(entries[0][1]["glb"]).stem
    for (i, c), (r, its) in zip(entries, copies):
        label = f"Crew__{i}_{cid}"
        r.set_actor_label(label)
        for a, name in zip(its, names):
            a.set_actor_label(f"{label}__{name}")
        r.set_actor_location_and_rotation(ue_vec(fr.blender_to_ue_location_cm(c["location_m"])),
                                          ue_rot(fr.blender_to_ue_rotator(c["rotation_deg"])), False, True)
        comp = r.skeletal_mesh_component
        anim = next((a for a in anims if fr.anim_name_matches(a.get_name(), c["animation"], stem)
                     and mesh is not None and a.get_editor_property("skeleton") == mesh.get_editor_property("skeleton")), None)
        if anim is not None:
            comp.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
            comp.override_animation_data(anim, True, True, 0.0, 1.0)
        station = stations.get(c["station"])
        if station is not None:
            # ride with the part the station is on (a crane operator slews with the crane)
            r.attach_to_actor(station, "", keep, keep, keep, False)
        state["crew_actors"].append({"label": label, "station_found": station is not None,
                                     "anim": anim.get_path_name() if anim else None})
    save_level(state)
    unreal.log(END)
    state["crew_done"].append(cid)


# ------------------------------------------------------------------------------------ phase 2
def check_log(checks):
    """Error: lines between the markers in every import process's log (ship, then each crew)."""
    logs = [Path(p) for p in IMPORT_LOG.split(os.pathsep) if p]
    if not logs or not all(p.is_file() for p in logs):
        return checks.add("import_log_clean", False, f"import log(s) not found: {IMPORT_LOG!r}")
    errors, unfinished = [], []
    for log in logs:
        lines = log.read_text(errors="replace").splitlines()
        inside = False
        for line in lines:
            if BEGIN in line and "ME3D_CHECK" not in line:
                inside = True
            elif END in line:
                inside = False
            elif inside and "Error:" in line:
                errors.append(f"{log.name}: " + line.split("]", 2)[-1].strip())
        if not any(END in ln for ln in lines):
            unfinished.append(log.name)
    detail = f"{len(errors)} Error: line(s) in {len(logs)} import log(s)" + (": " + " | ".join(errors[:5]) if errors else "")
    if unfinished:
        detail += f"; never reached the end marker: {', '.join(unfinished)}"
    return checks.add("import_log_clean", not errors and not unfinished, detail)


def phase_verify():
    state = load_state()
    checks = Checks(state.get("checks", []))
    m = read_manifest()
    if not state.get("ship_done"):
        checks.add("import_phase_completed", False,
                   "the ship import did not finish (see unreal_import.log)" + "; ".join(state.get("errors", [])))
        return checks, state.get("level")
    wanted = list(dict.fromkeys(c["id"] for c in m["crew"]))
    missing_crew = [cid for cid in wanted if cid not in state.get("crew_done", [])]
    checks.add("import_phase_completed", not missing_crew,
               f"ship and {len(wanted)} crew import(s) finished" if not missing_crew else
               f"crew import did not finish for {missing_crew} (see unreal_crew_<id>.log); "
               + "; ".join(state.get("errors", []) + state.get("reuse_notes", [])))
    base, level = paths_for(m)
    check_log(checks)

    if not les.load_level(level):
        checks.add("level_save_reload", False, f"load_level({level}) failed")
        return checks, level
    actors = labelled(all_actors())
    before = state.get("actors_before_save", [])
    missing = sorted(set(before) - set(actors))
    checks.add("level_save_reload", not missing and len(actors) == len(before),
               f"{len(before)} actors before save, {len(actors)} after reload from disk"
               + (f"; missing {missing}" if missing else ""))

    # ---- ship parts: separate meshes, one actor per glTF mesh node
    gltf = fr.read_glb_json(PKG_DIR / m["ship"]["glb"])
    mesh_nodes = fr.glb_mesh_nodes(gltf)
    distinct_meshes = {n["mesh"] for n in gltf.get("nodes", []) if "mesh" in n}
    sm_assets = assets_of(f"{base}/Ship/", unreal.StaticMesh)
    no_actor = [n for n in mesh_nodes if not (isinstance(actors.get(n), unreal.StaticMeshActor)
                and actors[n].static_mesh_component.static_mesh is not None)]
    checks.add("ship_parts_imported", len(sm_assets) == len(distinct_meshes) and not no_actor,
               f"GLB has {len(mesh_nodes)} mesh nodes / {len(distinct_meshes)} meshes; Unreal has "
               f"{len(sm_assets)} StaticMesh assets" + (f"; no mesh actor for {no_actor}" if no_actor else ""))
    wrong_parent = []
    for node, parent in mesh_nodes.items():
        a = actors.get(node)
        if a is None:
            continue
        got = a.get_attach_parent_actor()
        got = got.get_actor_label() if got else None
        if parent is not None and got != parent:
            wrong_parent.append(f"{node}: parent {got}, expected {parent}")
    checks.add("ship_hierarchy", not wrong_parent,
               "; ".join(wrong_parent) or f"all {len(mesh_nodes)} parts attached as in the GLB")

    # ---- articulation: own actor, attached to its parent, pivot, and it moves
    art = json.loads((PKG_DIR / m["ship"]["articulation"]).read_text(encoding="utf-8"))
    for j in art:
        name = f"articulated_part:{j['part']}"
        a = actors.get(j["part"])
        if not isinstance(a, unreal.StaticMeshActor):
            checks.add(name, False, f"no actor named {j['part']} in the level (not in the GLB?)")
            continue
        parent = a.get_attach_parent_actor()
        parent = parent.get_actor_label() if parent else None
        want_pivot = fr.blender_to_ue_location_cm(j["pivot_m"])
        got_pivot = vtuple(a.get_actor_location())
        err = fr.distance(want_pivot, got_pivot)
        comp = a.static_mesh_component
        lo0, hi0 = world_aabb(comp)
        start = a.get_actor_rotation()
        moved, used = 0.0, None
        for deg in (90.0, float(j["max_deg"]), float(j["min_deg"])):
            deg = max(float(j["min_deg"]), min(float(j["max_deg"]), deg))
            if abs(deg) < 1e-6:
                continue
            e = {"x": (deg, 0, 0), "y": (0, deg, 0), "z": (0, 0, deg)}[j["axis"]]
            delta = ue_rot(fr.blender_to_ue_rotator(e))
            a.add_actor_local_rotation(delta, False, False)
            lo1, hi1 = world_aabb(comp)
            a.set_actor_rotation(start, False)
            moved = max(fr.distance(lo0, lo1), fr.distance(hi0, hi1))
            used = deg
            if moved > TOL_CM:
                break
        ok = parent == j["parent"] and err <= TOL_CM and moved > TOL_CM
        checks.add(name, ok,
                   f"parent {parent} (expected {j['parent']}); pivot at "
                   f"({got_pivot[0]:.1f}, {got_pivot[1]:.1f}, {got_pivot[2]:.1f}) cm, "
                   f"{err:.2f} cm from pivot_m; rotating {used} deg about {j['axis']} moved its "
                   f"bounds {moved:.1f} cm")

    # ---- crew assets
    skeletons = {}
    for cid, paths in (state.get("crew_assets") or {}).items():
        meshes = [unreal.load_asset(p) for p in paths]
        meshes = [x for x in meshes if x is not None]
        skel = meshes[0].get_editor_property("skeleton") if meshes else None
        skeletons[cid] = skel
        checks.add(f"crew_skeleton:{cid}", bool(meshes) and skel is not None,
                   f"{len(meshes)} SkeletalMesh, skeleton {skel.get_path_name() if skel else None}")
        crew_gltf = fr.read_glb_json(PKG_DIR / next(c["glb"] for c in m["crew"] if c["id"] == cid))
        skinned = [n.get("name") for n in crew_gltf.get("nodes", []) if "mesh" in n and "skin" in n]
        on_disk = assets_of(f"{base}/Crew/{cid}/", unreal.SkeletalMesh)
        checks.add(f"crew_one_mesh:{cid}", len(on_disk) == 1,
                   f"{len(on_disk)} SkeletalMesh asset(s) from the GLB's {len(skinned)} skinned mesh(es) "
                   f"{skinned}; one means a placed crew member is whole, not just its first part")
        stem = next(Path(c["glb"]).stem for c in m["crew"] if c["id"] == cid)
        anims = [x for x in assets_of(f"{base}/Crew/{cid}/", unreal.AnimSequence)]
        wanted = sorted({c["animation"] for c in m["crew"] if c["id"] == cid})
        glb_clips = fr.glb_animation_names(crew_gltf)
        for anim in wanted:
            hit = [x for x in anims if fr.anim_name_matches(x.get_name(), anim, stem)
                   and x.get_editor_property("skeleton") == skel]
            checks.add(f"crew_animation:{cid}:{anim}", bool(hit),
                       f"AnimSequence {hit[0].get_path_name()} on {skel.get_name() if skel else None}"
                       if hit else f"no AnimSequence for '{anim}' on this skeleton; GLB clips are "
                       f"{glb_clips}; imported {[x.get_name() for x in anims]}")
    distinct = {s.get_path_name() for s in skeletons.values() if s is not None}
    checks.add("crew_shared_skeleton", len(distinct) == 1 if skeletons else True,
               f"{len(skeletons)} crew asset set(s) on {len(distinct)} skeleton(s)"
               + ("; " + "; ".join(state.get("reuse_notes") or []) if state.get("reuse_notes") else ""))

    # ---- crew actors placed and animating
    for i, c in enumerate(m["crew"]):
        label = f"Crew__{i}_{c['id']}"
        name = f"crew_actor:{i}:{c['station']}"
        a = actors.get(label)
        if not isinstance(a, unreal.SkeletalMeshActor):
            checks.add(name, False, f"actor {label} missing after reload")
            continue
        want_loc = fr.blender_to_ue_location_cm(c["location_m"])
        got_loc = vtuple(a.get_actor_location())
        err = fr.distance(want_loc, got_loc)
        ang = axes_close(a.get_actor_rotation(), ue_rot(fr.blender_to_ue_rotator(c["rotation_deg"])))
        comp = a.get_editor_property("skeletal_mesh_component")
        mode = comp.get_editor_property("animation_mode")
        data = comp.get_editor_property("animation_data")
        anim = data.get_editor_property("anim_to_play")
        looping = data.get_editor_property("saved_looping")
        anim_ok = (anim is not None and mode == unreal.AnimationMode.ANIMATION_SINGLE_NODE and looping
                   and fr.anim_name_matches(anim.get_name(), c["animation"], Path(c["glb"]).stem))
        parent = a.get_attach_parent_actor()
        # Independent cross-check: the station empty that Epic's glTF importer placed from the
        # ship GLB. The manifest-vs-actor comparison above uses our own frame conversion both
        # ways, so on its own it could not catch a wrong conversion; this one can.
        st = actors.get(c["station"])
        if st is None:
            st_note, st_ok = f"station {c['station']} not found in the imported ship", False
        else:
            st_err = fr.distance(vtuple(st.get_actor_location()), got_loc)
            st_ang = axes_close(st.get_actor_rotation(), a.get_actor_rotation())
            st_ok = st_err <= TOL_CM and st_ang <= TOL_DEG
            st_note = f"{st_err:.2f} cm and {st_ang:.2f} deg from the ship's own {c['station']}"
        ok = (err <= TOL_CM and ang <= TOL_DEG and st_ok and anim_ok
              and comp.get_skeletal_mesh_asset() is not None)
        checks.add(name, ok,
                   f"{label} at ({got_loc[0]:.1f}, {got_loc[1]:.1f}, {got_loc[2]:.1f}) cm, {err:.2f} cm "
                   f"and {ang:.2f} deg from the manifest, {st_note}; playing "
                   f"{anim.get_name() if anim else None} (looping={looping}, mode={mode.name}); "
                   f"attached to {parent.get_actor_label() if parent else None}")
        check_equipment(checks, actors, i, c, a)
    return checks, level


def rest_centre(rig, item):
    """Centre of an attached item's mesh in the crew's own frame with the skeleton in its rest
    pose: the item's saved offset from its bone, then each bone's rest transform up to the root.
    A crew member with an animation set is posed at that clip's first frame even in the editor,
    so its live position would differ from the GLB's rest pose by however the clip moves."""
    comp = rig.skeletal_mesh_component
    xf, bone = item.root_component.get_relative_transform(), item.root_component.get_attach_socket_name()
    while str(bone) not in ("None", ""):
        xf = unreal.MathLibrary.compose_transforms(xf, comp.get_ref_pose_transform(comp.get_bone_index(bone)))
        bone = comp.get_parent_bone(bone)
    box = item.static_mesh_component.static_mesh.get_bounding_box()
    return vtuple(xf.transform_location((box.min + box.max) * 0.5))


def check_equipment(checks, actors, i, c, rig):
    """Each item the GLB hangs on a bone: its own actor, Movable, attached to the crew member at
    that bone, and where the GLB puts it. The place is worked out from the GLB alone
    (me3d_frames.glb_item_centres_cm), not from Interchange's or the hook's conversion, and
    compared in the crew member's own frame and rest pose (rest_centre), so it holds at any
    station and whatever clip is set; the bone carries the item from there."""
    gltf = fr.read_glb_json(PKG_DIR / c["glb"])
    want = fr.glb_item_centres_cm(gltf)
    for item, bone in fr.glb_bone_attachments(gltf).items():
        name = f"crew_equipment:{i}:{item}"
        a = actors.get(f"Crew__{i}_{c['id']}__{item}")
        if not isinstance(a, unreal.StaticMeshActor):
            checks.add(name, False, f"no actor for {item} (the GLB hangs it on {bone})")
            continue
        parent = a.get_attach_parent_actor()
        socket = str(a.root_component.get_attach_socket_name())
        mobility = a.static_mesh_component.mobility
        if parent != rig:
            checks.add(name, False, f"attached to {parent.get_actor_label() if parent else None}, not to "
                                    f"{rig.get_actor_label()} (the GLB hangs it on {bone})")
            continue
        got = rest_centre(rig, a)
        err = fr.distance(got, want[item])
        ok = socket == bone and mobility == unreal.ComponentMobility.MOVABLE and err <= TOL_CM
        checks.add(name, ok,
                   f"on {rig.get_actor_label()} bone {socket} (GLB: {bone}), {mobility.name}; rest-pose centre "
                   f"({got[0]:.1f}, {got[1]:.1f}, {got[2]:.1f}) cm in the crew's frame, {err:.2f} cm from where "
                   f"the GLB puts it")


def main():
    if PHASE == "import":
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        try:
            state = phase_import()
        except Exception:
            state = {"ship_done": False, "errors": [traceback.format_exc().strip().splitlines()[-1]]}
            unreal.log_error("ME3D import failed:\n" + traceback.format_exc())
        STATE_FILE.write_text(json.dumps(state, indent=1))
        return
    if PHASE == "crew":
        state = load_state()
        if state.get("ship_done"):
            try:
                phase_crew(state, CREW_ID)
            except Exception:
                state.setdefault("errors", []).append(f"{CREW_ID}: " + traceback.format_exc().strip().splitlines()[-1])
                unreal.log_error(f"ME3D crew import {CREW_ID} failed:\n" + traceback.format_exc())
            STATE_FILE.write_text(json.dumps(state, indent=1))
        return
    level = None
    try:
        checks, level = phase_verify()
    except Exception:
        tb = traceback.format_exc()
        checks = Checks()
        checks.add("verify_completed", False, tb.strip().splitlines()[-1])
        unreal.log_error("ME3D verify failed:\n" + tb)
    if level is None:
        try:
            level = paths_for(read_manifest())[1]
        except Exception:
            level = None
    ok = bool(checks.items) and all(c["pass"] for c in checks.items)
    report = {"ok": ok, "engine": unreal.SystemLibrary.get_engine_version().split("-")[0][:3],
              "engine_version": unreal.SystemLibrary.get_engine_version(),
              "level": level, "checks": checks.items}
    REPORT_FILE.write_text(json.dumps(report, indent=1))
    unreal.log(f"ME3D_REPORT ok={ok} -> {REPORT_FILE}")


main()
