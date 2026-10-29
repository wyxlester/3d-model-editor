"""Home-screen routing (POST /api/route): one small, tool-less model call that classifies the
message. Anything unparseable or out of vocabulary becomes "clarify" — a misroute should cost the
user one question, never a wrong action. Home opens the destination at once when it is sure
(Lester, 29 Oct: "the web page should redirect url to either ship or crew"); the screen only opens,
nothing is changed, so a misroute costs a click on the top bar."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Callable

DESTINATIONS = ["ship_research", "ship_build", "ship_variant", "crew", "crew_variant", "animation",
                "assembly", "review", "clarify"]

ROUTE_SYSTEM = """You route a message in a 3D model editor for maritime simulators to one screen.
Destinations:
- ship_research: a new vessel or class to look up before building ("make me a Fearless-class patrol boat").
- ship_build: change a part or dimension of an existing ship model.
- ship_variant: a new ship made from an existing one, keeping the original ("PC 303 but with a 25 mm gun", "a version of the tanker with a second radar").
- crew: a person, their body, clothes or equipment.
- crew_variant: a new person made from an existing crew member, keeping the original ("the coxswain again, but in a helmet").
- animation: how a person moves (walk, brace, salute).
- assembly: putting crew on a ship, stations, crew acting on a ship.
- review: a customer meeting, presenting, exporting, Unreal.
- clarify: you are not sure.
Attached files are listed by name only; you cannot see them, and they travel with the message to the screen you pick. Never say a file is missing.
"reason" is one short plain sentence spoken to the user ("you", never "the user"), saying what you are opening and why.
Answer with ONLY a JSON object: {"destination": "...", "reason": "<one short plain sentence>", "project_id": "<id from the list or null>"}"""

LLM = Callable[[str, str], str]


def parse_route(text: str, project_ids: set[str]) -> dict:
    fallback = {"destination": "clarify",
                "reason": "I couldn't tell which workshop you meant. Which one should I open?",
                "project_id": None}
    if not isinstance(text, str):
        return fallback
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return fallback
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return fallback
    if not isinstance(data, dict) or data.get("destination") not in DESTINATIONS:
        return fallback
    reason = data.get("reason")
    pid = data.get("project_id")
    return {"destination": data["destination"],
            "reason": reason.strip() if isinstance(reason, str) and reason.strip() else fallback["reason"],
            "project_id": pid if pid in project_ids else None}


def route(message: str, projects: list[dict], llm: LLM, attachments: list[str] | None = None) -> dict:
    listing = "\n".join(f"- {p['id']}: {p['kind']} '{p['name']}'" for p in projects) or "(none)"
    files = f"\nAttached files: {', '.join(attachments)}" if attachments else ""
    prompt = f"Existing projects:\n{listing}\n\nMessage: {message}{files}"
    try:
        text = llm(prompt, ROUTE_SYSTEM)
    except Exception as e:  # the model being unreachable must not break Home
        return dict(parse_route("", set()), reason=f"Routing is unavailable ({type(e).__name__}). Which workshop?")
    return parse_route(text, {p["id"] for p in projects})


def claude_llm(model: str) -> LLM:
    def call(prompt: str, system: str) -> str:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

        async def go() -> str:
            opts = ClaudeAgentOptions(tools=[], allowed_tools=[], setting_sources=[], max_turns=1,
                                      model=model, system_prompt=system)
            out, final = [], None
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, AssistantMessage):
                    out += [b.text for b in m.content if isinstance(b, TextBlock)]
                elif isinstance(m, ResultMessage):
                    final = m.result
            return final or "".join(out)
        return asyncio.run(asyncio.wait_for(go(), 60))
    return call
