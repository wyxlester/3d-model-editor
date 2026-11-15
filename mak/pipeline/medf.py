"""Package the written model as MEDF/MEIF with MAK's medfTool (AddContentToMAK 19.19), for fast loading.

OpenFlight "loads slowly" (ACM 4.x terrain notes) and PNG textures are decoded, mip-mapped and compressed at
every load. medfTool turns the folder into what stock models ship: `<name>.medf` (the model) and one
`<image>_png.meif` per texture, pre-compressed with full mip chains (DXT1 colour, BC5 for `_NML` normal maps,
as on the stock Gerald Ford). "When a MAK ONE application processes a request to load a file ... it checks the
cache and the directory of the requested file for an MEDF version of the file. If it does not exist, it loads
the file in the original format" (19.19), so the `.ommx` keeps naming the `.flt`, and the `.flt` and PNGs stay
beside it as the readable source.

Writes only into the model's own output folder.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .common import Finding

IMAGE_EXTS = ("png", "rgb", "rgba", "dds", "jpg", "jpeg")     # medfTool -m: image extensions to encrypt


def _mb(paths) -> float:
    return sum(p.stat().st_size for p in paths) / 1e6


def package(model_file: Path, tool: str | None, timeout: int = 900) -> list[Finding]:
    model_file = Path(model_file)
    folder = model_file.parent
    if not tool or not Path(tool).exists():
        return [Finding("warning", "medf-unavailable", f"medfTool not found at {tool}; the model ships as "
                        f"{model_file.suffix} + raw textures, which VR-Forces loads slowly (ACM 19.19)")]
    for stale in list(folder.glob("*.medf")) + list(folder.glob("*.meif")):
        stale.unlink()
    sources = [model_file] + [p for p in folder.iterdir() if p.suffix.lower().lstrip(".") in IMAGE_EXTS]
    exts = sorted({p.suffix.lower().lstrip(".") for p in sources[1:]})
    cmd = [tool, "--directory", str(folder), "--norecurse", "-x", model_file.suffix.lstrip(".")]
    for e in exts:
        cmd += ["-m", e]
    cmd += ["--outputDirectory", str(folder)]
    t = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    took = time.time() - t
    medf = folder / f"{model_file.stem}.medf"
    meifs = sorted(folder.glob("*.meif"))
    images = len(sources) - 1
    if res.returncode != 0 or not medf.exists():
        tail = "\n".join((res.stdout + res.stderr).splitlines()[-10:])
        return [Finding("error", "medf-failed", f"medfTool could not package {model_file.name} "
                        f"(exit {res.returncode}):\n{tail}")]
    f = [Finding("info", "medf-packaged",
                 f"{medf.name} + {len(meifs)} .meif ({_mb([medf] + meifs):.1f} MB, from {_mb(sources):.1f} MB) in "
                 f"{took:.0f} s: pre-compressed, mip-mapped textures, which VR-Forces loads instead of the "
                 f"{model_file.suffix} and its images (ACM 19.19)")]
    if len(meifs) < images:
        done = {m.name for m in meifs}
        missing = [p.name for p in sources[1:] if f"{p.stem}_{p.suffix.lstrip('.').lower()}.meif" not in done]
        f.append(Finding("warning", "medf-image-missing", f"{images - len(meifs)} texture(s) not converted to .meif, "
                         f"e.g. {missing[:1]}; they load from the original files"))
    return f
