"""The settings install_import_hook.py writes into an Unreal project.

A mistake here either leaves the hook out of every import (silently: imports still "work") or
damages the project's DefaultEngine.ini, so the text handling is checked without Unreal.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "unreal"))
import me3d_hook_config as c  # noqa: E402

EPIC = "/Interchange/Pipelines/DefaultGLTFSceneAssetsPipeline.DefaultGLTFSceneAssetsPipeline"
OURS = "/Game/ME3D/Interchange/ME3D_GLTFSceneAssetsPipeline.ME3D_GLTFSceneAssetsPipeline"
GLTF = "/Interchange/Pipelines/DefaultGLTFPipeline.DefaultGLTFPipeline"
LEVEL = "/Interchange/Pipelines/DefaultSceneLevelPipeline.DefaultSceneLevelPipeline"
HOOK = "/Game/Python/me3d_import_hook_PY.Default__ME3DCrewEquipmentPipeline"


def test_install_swaps_the_assets_pipeline_and_adds_the_hook_last():
    got = c.gltf_pipelines([EPIC, GLTF, LEVEL], {EPIC: OURS}, add=HOOK)
    assert got == [OURS, GLTF, LEVEL, HOOK]


def test_installing_twice_changes_nothing():
    once = c.gltf_pipelines([EPIC, GLTF, LEVEL], {EPIC: OURS}, add=HOOK)
    assert c.gltf_pipelines(once, {EPIC: OURS}, add=HOOK) == once


def test_remove_puts_epics_list_back():
    installed = [OURS, GLTF, LEVEL, HOOK]
    assert c.gltf_pipelines(installed, {OURS: EPIC}, drop=HOOK) == [EPIC, GLTF, LEVEL]


INI = ("[/Script/EngineSettings.GameMapsSettings]\r\n"
       "GameDefaultMap=/Game/SimBlank/Levels/SimBlank.SimBlank\r\n"
       "\r\n"
       "[/Script/InterchangeEngine.InterchangeProjectSettings]\r\n"
       "ContentImportSettings=(old)\r\n"
       "+SceneImportSettings=(older)\r\n"
       "bStaticMeshUseSmoothEdgesIfSmoothingInformationIsMissing=True\r\n"
       "\r\n"
       "[/Script/Engine.RendererSettings]\r\n"
       "r.Lumen=1\r\n")


def test_ini_keys_replace_earlier_settings_and_keep_the_rest():
    got = c.set_ini_keys(INI, c.SECTION, {"ContentImportSettings": "(new)", "SceneImportSettings": "(scene)"})
    assert got == ("[/Script/EngineSettings.GameMapsSettings]\r\n"
                   "GameDefaultMap=/Game/SimBlank/Levels/SimBlank.SimBlank\r\n"
                   "\r\n"
                   "[/Script/InterchangeEngine.InterchangeProjectSettings]\r\n"
                   "bStaticMeshUseSmoothEdgesIfSmoothingInformationIsMissing=True\r\n"
                   "ContentImportSettings=(new)\r\n"
                   "SceneImportSettings=(scene)\r\n"
                   "\r\n"
                   "[/Script/Engine.RendererSettings]\r\n"
                   "r.Lumen=1\r\n")


def test_ini_section_is_added_when_missing():
    got = c.set_ini_keys("[A]\nx=1\n\n", c.SECTION, {"SceneImportSettings": "(scene)"})
    assert got == "[A]\nx=1\n\n" + c.SECTION + "\nSceneImportSettings=(scene)\n"


def test_writing_the_same_keys_twice_is_stable():
    once = c.set_ini_keys(INI, c.SECTION, {"ContentImportSettings": "(new)"})
    assert c.set_ini_keys(once, c.SECTION, {"ContentImportSettings": "(new)"}) == once


KEYS = {"ContentImportSettings": "(new)", "SceneImportSettings": "(scene)"}
PLAIN = ("[/Script/EngineSettings.GameMapsSettings]\r\n"
         "GameDefaultMap=/Game/SimBlank/Levels/SimBlank.SimBlank\r\n"
         "\r\n"
         "[/Script/Engine.RendererSettings]\r\n"
         "r.Lumen=1\r\n")


def test_remove_gives_back_a_file_that_never_had_the_section():
    installed = c.set_ini_keys(PLAIN, c.SECTION, KEYS)
    assert c.SECTION in installed
    assert c.without_ini_keys(installed, c.SECTION, KEYS) == PLAIN


def test_remove_keeps_other_settings_in_a_section_the_project_already_had():
    before = c.without_ini_keys(INI, c.SECTION, KEYS)   # the project's own line stays
    assert "bStaticMeshUseSmoothEdgesIfSmoothingInformationIsMissing=True" in before
    installed = c.set_ini_keys(before, c.SECTION, KEYS)
    assert c.without_ini_keys(installed, c.SECTION, KEYS) == before


def test_init_unreal_gets_the_import_once_and_loses_it_on_remove():
    mine = "import my_tools\n"
    added = c.with_line(mine, c.INIT_LINE)
    assert added == mine + c.INIT_LINE + "\n"
    assert c.with_line(added, "import me3d_import_hook") == added   # same code, other comment
    assert c.without_line(added, c.INIT_LINE) == mine
