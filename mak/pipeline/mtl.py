"""MAK .mtl tables: parenthesised lists with `;` comments, e.g. ammunitionParams.mtl,
character_animations_table.mtl. Read-only helpers plus the munition and character lookups built on them.

    python -m pipeline.mtl characters [filter]     # DI-Guy characters a human or animal can use

Munitions (users guide 66.5, 73, 74.2.2): a weapon can only fire ammunition listed in the simulation model set's
`ammunitionParams.mtl`; each entry's `munition-type` is a 7-field object type with no -1 fields.
Characters (Adding Content manual 25.2.2-25.2.3; users guide 66.1.10): the Simulation Object Editor offers the
characters listed in appData/settings/vrfSim/character_animations_table.mtl.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Entry:
    name: str        # first token after the opening parenthesis
    start: int       # index of "("
    end: int         # index just past the matching ")"
    depth: int       # 0 = top level

    def text(self, source: str) -> str:
        return source[self.start:self.end]


def scan(source: str, max_depth: int = 2) -> list[Entry]:
    """Every list down to `max_depth`, in file order. Skips `;` comments and "strings"."""
    out: list[Entry] = []
    stack: list[Entry] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c == ";":
            i = source.find("\n", i)
            i = n if i < 0 else i
        elif c == '"':                       # strings may hold \" (the stock ammunition table has one)
            i += 1
            while i < n and source[i] != '"':
                i += 2 if source[i] == "\\" else 1
            i += 1
        elif c == "(":
            m = re.match(r"\(\s*([^\s()\"]*)", source[i:])
            e = Entry(m.group(1) if m else "", i, -1, len(stack))
            stack.append(e)
            if e.depth <= max_depth:
                out.append(e)
            i += 1
        elif c == ")":
            if stack:
                stack.pop().end = i + 1
            i += 1
        else:
            i += 1
    return [e for e in out if e.end > 0]


# ---------------------------------------------------------------- munitions
def munition_type_text(dis7: str) -> str:
    return " ".join(dis7.split(":"))


def find_ammunition(source: str, dis7: str) -> Entry | None:
    """The ammunition-params entry whose munition-type is `dis7` (colon form)."""
    want = re.compile(r"\(\s*munition-type\s+" + r"\s+".join(re.escape(x) for x in dis7.split(":")) + r"\s*\)")
    for e in scan(source, 1):
        if e.depth == 1 and want.search(e.text(source)):
            return e
    return None


def ammunition_entry(name: str, dis7: str, label: str, donor_text: str | None, tags: list[str]) -> str:
    """One ammunition-params entry. Cloned from the donor munition's entry when it has one (its warhead,
    guidance mode and targets carry over); otherwise a plain unguided entry with no target list."""
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "munition"
    mt = munition_type_text(dis7)
    if donor_text:
        text = re.sub(r"^\(\s*\S+", f"({key}", donor_text.strip(), count=1)
        text = re.sub(r"\(\s*munition-type[^)]*\)", f"(munition-type {mt})", text, count=1)
        if re.search(r"\(\s*gui-label[^)]*\)", text):
            text = re.sub(r"\(\s*gui-label[^)]*\)", lambda _: f'(gui-label "{label}")', text, count=1)
        return "   " + text + "\n"
    tag_text = " ".join(f'"{t}"' for t in tags)
    return (f"   ({key} \n      (munition-type {mt})\n      (warhead -1)\n      (guidance-mode  0)\n"
            f'      (gui-label "{label}")\n      (targets )\n      (tags {tag_text})\n   )\n')


def merge_ammunition(table: str, addition: str) -> tuple[str, bool]:
    """`addition` (one entry) appended inside (ammunition-params ...). Returns (text, changed); unchanged when
    an entry with the same munition-type is already there."""
    m = re.search(r"\(\s*munition-type\s+([^)]*)\)", addition)
    if not m:
        raise ValueError("addition has no munition-type")
    dis7 = ":".join(m.group(1).split())
    if not table.strip():
        table = "(ammunition-params \n)\n"
    if find_ammunition(table, dis7):
        return table, False
    roots = [e for e in scan(table, 0) if e.name == "ammunition-params"]
    if not roots:
        raise ValueError("no (ammunition-params ...) list in the table")
    close = roots[0].end - 1
    head = table[:close].rstrip(" \t")
    if not head.endswith("\n"):
        head += "\n"
    return head + addition.rstrip("\n") + "\n" + table[close:], True


def has_detonation_entry(source: str, dis7: str) -> bool:
    want = re.compile(r"\(\s*munition-type\s+" + r"\s+".join(re.escape(x) for x in dis7.split(":")) + r"\s*\)")
    return bool(want.search(re.sub(r";[^\n]*", "", source)))


# ---------------------------------------------------------------- DI-Guy characters
def characters(source: str) -> dict[str, dict]:
    """character type -> {"display_name", "animations": [names]} from character_animations_table.mtl."""
    out: dict[str, dict] = {}
    for e in scan(source, 1):
        if e.depth != 1:
            continue
        body = e.text(source)
        dn = re.search(r'\(\s*display-name\s+"([^"]*)"', body)
        out[e.name] = {"display_name": dn.group(1) if dn else e.name,
                       "animations": re.findall(r'\(\s*animation-name\s+"([^"]*)"', body)}
    return out


def character_table_path(cfg: dict) -> Path:
    return Path(cfg["vrforces_root"]) / "appData" / "settings" / "vrfSim" / "character_animations_table.mtl"


def load_characters(cfg: dict) -> dict[str, dict]:
    p = character_table_path(cfg)
    return characters(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else {}


def main(argv=None) -> int:
    from .common import load_config
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "characters":
        print(__doc__)
        return 2
    table = load_characters(load_config())
    if not table:
        print("character_animations_table.mtl not found (is VR-Forces installed?)", file=sys.stderr)
        return 1
    needle = args[1].lower() if len(args) > 1 else ""
    for name, info in sorted(table.items()):
        if needle in name.lower() or needle in info["display_name"].lower():
            print(f"{name:32} {info['display_name']}  ({len(info['animations'])} animations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
