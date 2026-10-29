"""Unreal import hook for crew GLBs from the 3D model editor: equipment stays on its bone.

Installed into a project's Content/Python by install_import_hook.py and loaded at editor start by
init_unreal.py there. Not run from the repo.

A crew GLB hangs each item of equipment (contract section 6: `Equipment__<Name>`, a rigid mesh)
under a bone with a local offset. Unreal's Import Into Level keeps that offset but hangs the item's
actor off the crew's root, at the feet, because bones are not actors. This pipeline runs after the
import has made its actors and, for every static mesh actor whose node hangs directly under a bone
of a skeletal mesh actor from the same import, makes it Movable and attaches it to that bone with
the same local offset, so it sits where the GLB puts it and follows every animation. It also starts
the crew's idle clip, if the import brought one and nothing else is playing, so Simulate shows the
equipment moving with the body.
"""
import unreal

_RECENT = 8      # imports remembered at once; older ones are done
_imports = {}    # node container path -> {"rigs": [...], "items": [...], "anims": [...]}


def _log(msg):
    unreal.log(f"me3d_import_hook: {msg}")


def _scene_node(container, factory_key):
    fn = container.get_factory_node(factory_key)
    for uid in (fn.get_target_node_uids() if fn else []):
        node = container.get_node(uid)
        if node is not None:
            return node
    return None


def _state(container):
    key = container.get_path_name()
    if key not in _imports:
        while len(_imports) >= _RECENT:
            _imports.pop(next(iter(_imports)))
        _imports[key] = {"rigs": [], "items": [], "anims": []}
    return _imports[key]


def attach_to_bone(actor, rig, bone, local=None):
    """Make a static mesh actor Movable (the editor will not attach a Static actor to a moving one)
    and hang it on `bone` of `rig`, `local` being its offset from the bone (without it, the offset
    it already has from its current parent is kept). Also used by the Unreal harness."""
    actor.static_mesh_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    rule = unreal.AttachmentRule.KEEP_RELATIVE
    actor.attach_to_actor(rig, bone, rule, rule, rule, False)
    if local is not None:
        actor.root_component.set_relative_transform(local, False, False)


def _idle(rig, anims):
    mesh = rig.skeletal_mesh_component.get_skeletal_mesh_asset()
    skeleton = mesh.get_editor_property("skeleton") if mesh else None
    return next((a for a in anims if a.get_editor_property("skeleton") == skeleton
                 and a.get_name().lower().endswith("idle")), None)


def _settle(st):
    """Attach every item whose parent bone belongs to a rig seen so far; actors arrive one by one."""
    for item in list(st["items"]):
        actor, bone, local = item
        rig = next((r for r in st["rigs"] if r.skeletal_mesh_component.does_socket_exist(bone)), None)
        if rig is None:
            continue
        st["items"].remove(item)
        # The node's own local transform is its offset from the bone (glTF: relative to the joint).
        attach_to_bone(actor, rig, bone, local)
        _log(f"{actor.get_actor_label()} attached to {rig.get_actor_label()} bone {bone}")
    for rig in st["rigs"]:
        comp = rig.skeletal_mesh_component
        if comp.get_editor_property("animation_data").get_editor_property("anim_to_play") is not None:
            continue
        idle = _idle(rig, st["anims"])
        if idle is not None:
            comp.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
            comp.override_animation_data(idle, True, True, 0.0, 1.0)
            _log(f"{rig.get_actor_label()} plays {idle.get_name()}")


@unreal.uclass()
class ME3DCrewEquipmentPipeline(unreal.InterchangePythonPipelineBase):

    @unreal.ufunction(override=True)
    def scripted_get_pipeline_display_name(self):
        return "ME3D crew: equipment on bones"

    @unreal.ufunction(override=True)
    def scripted_execute_post_import_pipeline(self, base_node_container, factory_node_key, created_asset, is_a_reimport):
        try:
            if isinstance(created_asset, unreal.AnimSequence):
                _state(base_node_container)["anims"].append(created_asset)
            elif isinstance(created_asset, unreal.SkeletalMeshActor):
                st = _state(base_node_container)
                st["rigs"].append(created_asset)
                _settle(st)
            elif isinstance(created_asset, unreal.StaticMeshActor):
                node = _scene_node(base_node_container, factory_node_key)
                parent_uid = node.get_parent_uid() if node else ""
                parent = base_node_container.get_node(parent_uid) if parent_uid else None
                if parent is not None:
                    st = _state(base_node_container)
                    st["items"].append((created_asset, parent.get_display_label(), node.get_custom_local_transform()))
                    _settle(st)
        except Exception as e:  # never break an import over this
            unreal.log_error(f"me3d_import_hook: {e}")
