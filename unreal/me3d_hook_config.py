"""Pure helpers for install_import_hook.py: the glTF pipeline list and the config text it writes.
No `unreal` import here, so tests/unreal can check them without Unreal."""

SECTION = "[/Script/InterchangeEngine.InterchangeProjectSettings]"
INIT_LINE = "import me3d_import_hook  # crew equipment on bones (3D model editor, install_import_hook.py)"


def gltf_pipelines(paths, swap, add=None, drop=None):
    """One translator's pipeline list with Epic's defaults swapped for ours, `add` appended once
    and `drop` taken out. Running it twice changes nothing."""
    out = [swap.get(p, p) for p in paths if p != drop]
    if add and add not in out:
        out.append(add)
    return out


def _code(line):
    return line.split("#", 1)[0].strip()


def with_line(text, line):
    """`text` with `line` appended, unless a line with the same code is already there."""
    if any(_code(x) == _code(line) for x in text.splitlines()):
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def without_line(text, line):
    return "".join(x for x in text.splitlines(True) if _code(x) != _code(line))


def _key(line):
    return line.split("=", 1)[0].strip().lstrip("+-.!")


def without_ini_keys(text, section, keys):
    """`text` with every setting of `keys` in `section` taken out, and the section too if nothing
    else is left in it: the inverse of set_ini_keys on a file that did not set them before."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    start = next((i for i, x in enumerate(lines) if x.strip() == section), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    body = [x for x in lines[start + 1:end] if _key(x) not in keys]
    if any(x.strip() for x in body):
        lines = lines[:start + 1] + body + lines[end:]
    else:
        head, rest = lines[:start], lines[end:]
        if not rest:
            while head and not head[-1].strip():
                head.pop()
        lines = head + rest
    return nl.join(lines) + nl if lines else ""


def set_ini_keys(text, section, values):
    """`text` (an .ini file) with `key=value` for each of `values` in `section`, replacing any
    earlier setting of those keys there (including +key/-key array edits); the section is added at
    the end if missing. Everything else is left as it was, line endings included."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    new = [f"{k}={v}" for k, v in values.items()]
    try:
        start = next(i for i, x in enumerate(lines) if x.strip() == section)
    except StopIteration:
        while lines and not lines[-1].strip():
            lines.pop()
        return nl.join(lines + ([""] if lines else []) + [section] + new) + nl
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    body = [x for x in lines[start + 1:end] if _key(x) not in values]
    tail = []
    while body and not body[-1].strip():
        tail.insert(0, body.pop())
    return nl.join(lines[:start + 1] + body + new + tail + lines[end:]) + nl
