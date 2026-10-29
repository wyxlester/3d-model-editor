"""What the user changes in research before a ship is built (contract §12, Review): the sources they
cross out, and their own values for the design plan's params. Pure functions."""
from __future__ import annotations

from numbers import Real
from typing import Any


def _is_number(v: Any) -> bool:
    return isinstance(v, Real) and not isinstance(v, bool)


def _is_colour(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(_is_number(x) for x in v)


def check_param(name: str, value: Any, defaults: dict, docs: dict) -> str | None:
    """None if `value` is a fair setting for the param `name`, else a sentence saying why not.
    Only lengths, plain numbers and [r, g, b] colours are offered to the user."""
    if name not in defaults and name not in docs:
        return f"{name} is not a setting of this ship"
    default = defaults.get(name)
    if _is_colour(default) or name.endswith("_color"):
        if not _is_colour(value) or not all(0 <= x <= 1 for x in value):
            return f"{name} must be a colour: three numbers from 0 to 1"
        return None
    if _is_number(default) or (default is None and _is_number(value)):
        if not _is_number(value):
            return f"{name} must be a number"
        if name.endswith("_m") and value <= 0:
            return f"{name} must be more than 0 metres"
        return None
    return f"{name} cannot be set here"


def excluded_note(project: dict) -> str:
    """The line a research turn starts with when the user crossed sources out; empty otherwise."""
    gone = project.get("excluded_sources") or []
    if not gone:
        return ""
    titles = {s.get("id"): s.get("title", "") for s in (project.get("research") or {}).get("sources") or []}
    listed = "; ".join(f"{i} ({titles.get(i, '')})" for i in gone)
    return (f"The user crossed out these sources: {listed}. Do not rely on them: keep them in the sources list "
            "but rest no claim, number or design-plan value on them alone. Re-source or drop what depended only "
            "on them, redraft the design plan if there is one, and say what changed.\n\n")


def claim_rests_only_on(claim: dict, excluded: list[str]) -> bool:
    """A claim whose every cited source was crossed out."""
    cited = claim.get("sources") if isinstance(claim.get("sources"), list) else []
    return bool(cited) and all(s in excluded for s in cited)
