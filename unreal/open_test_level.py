"""Run by the app's "Build and Open in Unreal", inside the editor with its window, to show the
level the Unreal test built: load it if the editor opened something else, and point the camera at the
ship from above the port quarter.

The server starts the editor as

    UnrealEditor <repo>/unreal/HarnessProject/Harness.uproject <level> -ExecCmds="py <this file> <level>"

-ExecCmds rather than -ExecutePythonScript: the latter shows a "running script" notice until the
script ends, and closes the editor when it does unless told to stay - Cancel on that notice would
quit Unreal. A console command runs once, on an early tick, and leaves the editor alone, so the
work waits here for the viewport to exist.
"""
import math
import sys

import unreal

LEVEL = sys.argv[1] if len(sys.argv) > 1 else ""
VIEW_PREFIX = "ME3D_View_"   # import_package.py: sky, sun and sea, not part of the ship
WAIT_TICKS = 20

les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
state = {"ticks": 0, "handle": None, "loading": False}


def frame():
    shown = [a for a in eas.get_all_level_actors()
             if isinstance(a, (unreal.StaticMeshActor, unreal.SkeletalMeshActor))
             and not a.get_actor_label().startswith(VIEW_PREFIX)]
    if not shown:
        return
    boxes = [a.get_actor_bounds(False) for a in shown]   # (centre, half extent)
    lo = [min(getattr(c, k) - getattr(e, k) for c, e in boxes) for k in "xyz"]
    hi = [max(getattr(c, k) + getattr(e, k) for c, e in boxes) for k in "xyz"]
    centre = unreal.Vector(*[(lo[i] + hi[i]) / 2 for i in range(3)])
    size = max(math.dist(lo, hi), 300.0)
    # behind and to port of the bow (-Y is port in Unreal), a little above, looking at the middle
    eye = centre + unreal.Vector(-0.55 * size, -0.75 * size, 0.35 * size)
    ues.set_level_viewport_camera_info(eye, unreal.MathLibrary.find_look_at_rotation(eye, centre))


def tick(_dt):
    state["ticks"] += 1
    if state["ticks"] < WAIT_TICKS:
        return
    try:
        world = ues.get_editor_world()
        if LEVEL and (world is None or world.get_path_name().split(".")[0] != LEVEL):
            if not state["loading"]:
                state["loading"] = True
                les.load_level(LEVEL)
            if state["ticks"] < WAIT_TICKS * 5:   # give the newly loaded level a few ticks
                return
        frame()
        unreal.log(f"open_test_level: showing {LEVEL}")
    except Exception as e:  # never leave a half-opened editor over a camera move
        unreal.log_warning(f"open_test_level: {e}")
    unreal.unregister_slate_post_tick_callback(state["handle"])


state["handle"] = unreal.register_slate_post_tick_callback(tick)
