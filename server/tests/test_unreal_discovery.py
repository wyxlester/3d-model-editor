"""Finding Unreal 5.8: Windows lists every registered engine, and the wrong version must not win
(a 5.7 folder listed first would import with a different Interchange)."""
import json

from app.config import engine_version, pick_unreal


def engine(root, major, minor):
    d = root / f"UE_{major}.{minor}"
    (d / "Engine/Build").mkdir(parents=True)
    (d / "Engine/Build/Build.version").write_text(json.dumps({"MajorVersion": major, "MinorVersion": minor}))
    return d


def test_the_first_58_engine_wins_over_other_versions_and_junk(tmp_path):
    old, new = engine(tmp_path, 5, 7), engine(tmp_path, 5, 8)
    fallback = tmp_path / "default"
    assert pick_unreal([str(tmp_path / "missing"), str(old), "", str(new)], fallback) == new
    assert engine_version(new) == (5, 8)


def test_no_58_engine_means_the_default_folder(tmp_path):
    fallback = tmp_path / "default"
    assert pick_unreal([str(engine(tmp_path, 5, 7))], fallback) == fallback
