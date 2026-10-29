"""Spec patching. Rules (contract §10, POST /spec):

- dicts merge recursively;
- lists replace wholesale (never concatenated, so "equipment": [] clears it);
- ``None`` deletes the key (and a ``None`` inside a brand-new subtree is simply dropped);
- anything else replaces.

Neither argument is mutated.
"""
from __future__ import annotations

import copy
import re
from typing import Any

_NOT_ID = re.compile(r"[^a-z0-9]+")
VARIANT_ID_MAX = 40


def deep_merge(base: dict, patch: dict) -> dict:
    if not isinstance(base, dict) or not isinstance(patch, dict):
        raise TypeError("deep_merge needs two dicts")
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict):
            current = out.get(key)
            out[key] = deep_merge(current if isinstance(current, dict) else {}, value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def gltf_to_blender(point: list[float]) -> list[float]:
    """A three.js/glTF point (+Y up) back into the Blender frame (+Z up, +Y port).

    Blender's glTF exporter maps (x, y, z)_blender -> (x, z, -y)_gltf, so the inverse is
    (x, y, z)_gltf -> (x, -z, y)_blender.
    """
    x, y, z = (float(v) for v in point)
    return [x, -z + 0.0, y]


def label_for(path: str) -> str:
    """Contract §2 display label: '__' -> ' › ', '_' -> ' '."""
    return path.replace("__", " › ").replace("_", " ")


def variant_id(name: str, taken: set[str]) -> str:
    """Contract §5: a variant's spec id, from its name, unique among `taken`.
    "PC 303 with 25 mm gun" -> "pc_303_with_25_mm_gun"; "Container ship" when taken -> "container_ship_2"."""
    base = _NOT_ID.sub("_", str(name).lower()).strip("_")[:VARIANT_ID_MAX].strip("_") or "variant"
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"_{n}"
        candidate = base[:VARIANT_ID_MAX - len(suffix)].rstrip("_") + suffix
        if candidate not in taken:
            return candidate
        n += 1


def diff_keys(a: Any, b: Any, prefix: str = "") -> list[str]:
    """Dotted paths whose values differ between two specs (for version summaries)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{prefix}.{k}" if prefix else str(k)
            if k not in a or k not in b:
                out.append(p)
            else:
                out.extend(diff_keys(a[k], b[k], p))
        return out
    return [] if a == b else [prefix or "(root)"]
