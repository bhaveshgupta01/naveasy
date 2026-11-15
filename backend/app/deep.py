"""Planned mode: LangChain deepagents over NavEasy's seven tools (Technical Design section 10).

For a multi-step request ("find us a place near the Medical Center, I use a wheelchair, budget 2,000") a planner
writes a to-do list (deepagents' write_todos), then hands the work to four subagents, each with its own context:

    building  CoStar building records: the shortlist and building evidence     find_homes, describe_home
    inside    Matterport scans: measured doorways, turning room, layout         check_apartment, describe_layout, plan_furniture
    outside   Vexcel imagery, OpenStreetMap and Valhalla: routes and minutes    route_to, match_neighborhoods
    checker   re-runs every failing finding; anything it can't reproduce is marked "needs a site visit"

The tools are plain typed functions over agent._run_tool, with the UI-actions list scoped to one request, so the
app moves the same way it does for the single agent. Every step (plan, delegation, tool call, check) is recorded
in agent.PROGRESS under the request id, so the app can show the work while it happens and under the answer.

The model never supplies a fact: the subagents read numbers from tools, the checker re-runs failures, and the final
answer's numbers are matched against every tool result before it is returned.

    NAVEASY_DEEP=off          switch the planned mode off (typed requests then go to the single agent)
    NAVEASY_DEEP_CACHE=off    always run live (by default a completed run is cached and replayed, for the demo)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Literal

from . import agent

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "agent_cache"
MODEL = os.getenv("NAVEASY_DEEP_MODEL", "claude-opus-5-5")
Need = Literal["wheelchair", "blind", "deaf", "older"]
Place = Literal["hospital", "clinic", "pharmacy", "grocery", "transit", "restaurant", "school", "park"]

RULES = """Rules that override everything else:
- Every number you state must come from a tool result you received. If a tool has no data, say it is unknown; never estimate.
- Give each claim its evidence ("bathroom door about 29 inches, measured from the 3D scan") and the confidence the tool reports.
- Never call a place "accessible" or "compliant". Say "meets our wheelchair checks" and name what was not checked.
- Grey or missing data means unknown, not bad."""

PLANNER = f"""You are NavEasy's lead broker. A client wants a home in Houston or Los Angeles; you plan the search and delegate the work.
Clients may use a wheelchair, be blind or have low vision, be deaf or hard of hearing, be older adults, or have no particular need.

How you work:
1. Your first action, before any task, is write_todos with a short plan (4 to 6 items) for this client. Update it as items finish.
2. Delegate with the task tool, in this order:
   - building: shortlist homes for the client's needs, area and budget, and get building evidence for the best two (rent, elevator or walk-up, age, scanned units).
   - inside: for the best two homes, the measured inside of each home's best scanned unit (doorways, turning room, rooms reached, layout). Give it the model ids from the building report.
   - outside: for the best home, the routes to the two everyday places that matter most for this client (pharmacy, grocery, transit or hospital), with minutes, crossings and hard spots. For a move from an address, it matches neighborhoods instead.
   - checker: pass it every failing or worrying finding from the reports above, with the home, unit or route it came from, so it can re-run them.
   Keep each task description self-contained: the subagent sees only what you write. Always pass ids exactly as the reports give them
   (building ids look like p9380874 or h215175829; model ids look like m3hCdjdsCRT), never addresses in their place.
3. Then answer the client like a confident broker, in plain spoken sentences (no lists, tables, markdown or symbols; say "dollars", "inches", "minutes"): your pick and why, the runner-up, the rent, what was measured inside, the minutes to everyday places, the main weakness stated plainly, and what to verify before signing. Use the checker's verdicts: anything it marked "needs a site visit" must be said that way. Under 220 words.

{RULES}"""

SUBAGENTS = {
    "building": ("Shortlists homes from CoStar building records and scans for a client's needs, area and budget, and reports the building evidence "
                 "(rent, elevator or walk-up, year built, scanned units with their best model ids) for the best ones.",
                 "You are the building researcher. Use find_homes to shortlist, then describe_home on the best two. Report for each: building id, name, "
                 "rent, score and confidence, vertical access, year built, the best scanned unit's model id, its top measured problems, and the minutes "
                 "to nearby places. Report facts only, briefly."),
    "inside": ("Measures the inside of scanned units from Matterport geometry: doorway widths, turning room, which rooms a wheelchair can reach, "
               "thresholds, layout from the entry door, and furniture plans.",
               "You are the inside inspector. For each model id you are given, call check_apartment for the client's main need (and describe_layout "
               "if the client is blind or asks about the layout). Report per unit: score, the narrowest main doorway, rooms not reachable, turning "
               "room, thresholds, and each problem with its number. Report facts only, briefly."),
    "outside": ("Checks the way outside the home with Vexcel imagery and heights, OpenStreetMap and Valhalla routing: minutes, crossings, slope "
                "and hard spots to everyday places, or neighborhoods that match a current address.",
                "You are the street scout. Call route_to for each place you are asked about (or match_neighborhoods for a move from an address). "
                "building_id must be an id like p9380874 or h215175829, never an address; if you were given only an address, say so. "
                "Report minutes, meters, crossings, the steepest slope, the stretches with no sidewalk record, and each hard spot. Facts only, briefly."),
    "checker": ("Re-runs failing or worrying findings to confirm their numbers, and marks anything it cannot reproduce as needing a site visit.",
                "You are the checker. You get findings from other researchers, each with the home, unit or route it came from. For every finding "
                "that is a failure or a barrier, re-run the tool that produced it (check_apartment for units, route_to for routes, describe_home for "
                "buildings) and compare. Reply with one line per finding: CONFIRMED with the number from your re-run, or NEEDS A SITE VISIT with "
                "why (the re-run disagrees, the confidence is low, the data is missing or estimated, or the scan can't measure it). Never add new facts."),
}
TOOLS_FOR = {"building": ("find_homes", "describe_home"), "inside": ("check_apartment", "describe_layout", "plan_furniture"),
             "outside": ("route_to", "match_neighborhoods"), "checker": ("check_apartment", "route_to", "describe_home")}


def enabled() -> bool:
    if os.getenv("NAVEASY_DEEP", "on").lower() in ("off", "0", "false", "no"):
        return False
    try:
        import deepagents  # noqa: F401
        import langchain_anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def wants_plan(text: str, st: dict) -> bool:
    """A multi-step "find me a place" request (two or more of: a need, a budget, a place or address), a move, or an explicit ask to compare."""
    from . import dialogue
    low = text.lower()
    if re.search(r"\b(compare|plan it|step by step|thorough|check everything|shortlist)\b", low):
        return True
    cmd = dialogue._rules(text, st)
    if cmd["intent"] not in ("find_homes", "moving"):
        return False
    said = bool(cmd["needs"]) + bool(re.search(r"\$\s?\d|\d{3,}\s*(?:dollars|a month)|budget|under \d", low)) + bool(cmd.get("near_place") or cmd.get("from_address"))
    return said >= 2


class Run:
    """One planned request: its UI actions, state, evidence and the steps shown to the user."""

    def __init__(self, context: dict):
        self.actions: list = []
        self.state = dict(context.get("state") or {})
        self.evidence: list[str] = []
        rid = context.get("request_id") or f"r{time.time_ns()}"
        self.steps = agent.PROGRESS.setdefault(rid, [])

    def step(self, kind: str, who: str, text: str, status: str = "ok") -> None:
        self.steps.append({"kind": kind, "who": who, "text": text, "status": status})


def _tools(run: Run, who: str, emit: bool = True) -> list:
    """The seven tools as plain typed functions. emit=False (the checker) re-runs them without moving the app."""

    def call(name: str, a: dict) -> str:
        a = {k: v for k, v in a.items() if v is not None}
        try:
            out = agent._run_tool(name, a, run.actions if emit else [], run.state if emit else None)
        except Exception as e:  # noqa: BLE001 - the subagent sees the error and says the data is missing
            out = {"error": str(getattr(e, "detail", e))[:300]}
        body = json.dumps(out, default=str)
        run.evidence.append(body)
        run.step("tool", who, agent._call_line(name, a), "error" if isinstance(out, dict) and out.get("error") else "ok")
        return body[:12000]

    def find_homes(city: Literal["houston", "la"], profiles: list[Need], max_rent: int | None = None, near: str | None = None, within_km: float | None = None) -> str:
        """Find scanned homes and apartment communities ranked by score for the person's needs, with rent and whether the inside was measured from a 3D scan. near is a named place or address, e.g. 'Texas Medical Center' or 'Midtown'."""
        return call("find_homes", {"city": city, "profiles": profiles, "max_rent": max_rent, "near": near, "within_km": within_km})

    def describe_home(building_id: str, profiles: list[Need]) -> str:
        """One building: a broker's summary built from the data, score parts (elevator or walk-up, age, listing evidence), scanned units with their measured problems, minutes to essentials, and a verify-before-you-sign checklist."""
        return call("describe_home", {"building_id": building_id, "profiles": profiles})

    def check_apartment(model_id: str, profile: Need) -> str:
        """Measured problems inside one scanned unit for one need: doorway widths, rooms a wheelchair can't reach, turning space, thresholds, walking distances, sightlines."""
        return call("check_apartment", {"model_id": model_id, "profile": profile})

    def describe_layout(model_id: str, profile: Need) -> str:
        """A walk-through of one scanned unit from its measured geometry: rooms and sizes, directions from the entry door, windows and sun, door widths, problems."""
        return call("describe_layout", {"model_id": model_id, "profile": profile})

    def route_to(building_id: str, category: Place, profile: Need) -> str:
        """The walking or rolling route from a building to the nearest place of a category for one need: minutes, crossings, slope, hard spots, turn-by-turn steps."""
        return call("route_to", {"building_id": building_id, "category": category, "profile": profile})

    def match_neighborhoods(current_address: str, profiles: list[Need], budget: int | None = None) -> str:
        """For someone moving between Los Angeles and Houston: neighborhoods in the other city where everyday places are as close as at their current address, with homes there."""
        return call("match_neighborhoods", {"current_address": current_address, "profiles": profiles, "budget": budget})

    def plan_furniture(target_model_id: str, profiles: list[Need], pieces: list[str]) -> str:
        """Place the client's furniture in a scanned unit so every room stays reachable: placements, where each piece goes, the score before and after, pieces that won't fit."""
        return call("plan_furniture", {"target_model_id": target_model_id, "profiles": profiles, "pieces": pieces})

    every = {f.__name__: f for f in (find_homes, describe_home, check_apartment, describe_layout, route_to, match_neighborhoods, plan_furniture)}
    return [every[n] for n in TOOLS_FOR[who]]


def curate(actions: list) -> list:
    """Subagents move the app many times; the client should end on one view: the shortlist on the map, the pick's
    panel open, and its route drawn (the pick is the home the outside scout routed from)."""
    first = lambda t: next((a for a in actions if a["type"] == t), None)  # noqa: E731
    last = lambda t: next((a for a in reversed(actions) if a["type"] == t), None)  # noqa: E731
    out = [a for a in (first("set_map_view"), first("highlight_buildings"), last("show_relocation")) if a]
    route = last("show_route")
    pick = (route or {}).get("building_id") or (first("open_building") or {}).get("id")
    if pick:
        out.append({"type": "open_building", "id": pick})
    if route:
        out.append(route)
    return out


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _key(text: str, context: dict) -> str:
    norm = re.sub(r"[^a-z0-9$ ]", "", text.lower()).strip()
    return hashlib.sha1(json.dumps([norm, sorted(context.get("profiles") or []), context.get("city")]).encode()).hexdigest()[:16]


def run(text: str, context: dict) -> dict:
    cached = CACHE / f"deep_{_key(text, context)}.json"
    if cached.exists() and os.getenv("NAVEASY_DEEP_CACHE", "on").lower() not in ("off", "0", "false"):
        return _replay(json.loads(cached.read_text()), context)
    from deepagents import create_deep_agent
    from langchain_anthropic import ChatAnthropic

    r = Run(context)
    t0 = time.time()
    model = ChatAnthropic(model=MODEL, max_tokens=6000, output_config={"effort": "low"}, timeout=120, max_retries=2)
    subagents = [{"name": name, "description": desc, "system_prompt": prompt + "\n\n" + RULES, "tools": _tools(r, name, emit=name != "checker"), "model": model}
                 for name, (desc, prompt) in SUBAGENTS.items()]
    from langchain.agents.middleware import TodoListMiddleware     # deepagents 0.7 no longer adds write_todos by itself
    graph = create_deep_agent(model=model, tools=[], system_prompt=PLANNER, subagents=subagents, middleware=[TodoListMiddleware()], name="naveasy")
    app = {k: context[k] for k in ("city", "profiles") if context.get(k)}
    history = [m for m in r.state.get("history", []) if m.get("role") in ("user", "assistant")][-6:]
    msgs = history + [{"role": "user", "content": f"{text}\n\n(App state: {json.dumps(app)})"}]
    r.step("plan", "planner", "Planning the search")
    final = ""
    planned = False
    for update in graph.stream({"messages": msgs}, stream_mode="updates", config={"recursion_limit": 60}):
        for node, val in (update or {}).items():
            if isinstance(val, dict) and val.get("todos"):
                todos = [t if isinstance(t, dict) else {"content": str(t)} for t in val["todos"]]
                if not planned:
                    planned = True
                    r.step("plan", "planner", "Plan: " + "; ".join(t.get("content", "") for t in todos))
                else:
                    done = sum(1 for t in todos if t.get("status") == "completed")
                    now = next((t.get("content") for t in todos if t.get("status") == "in_progress"), None)
                    r.step("plan", "planner", f"Plan: {done} of {len(todos)} done" + (f"; now: {now}" if now else "."))
            for m in (val or {}).get("messages", []) if isinstance(val, dict) else []:
                for tc in getattr(m, "tool_calls", None) or []:
                    if tc["name"] == "task":
                        who = tc["args"].get("subagent_type", "?")
                        r.step("subagent", who, (tc["args"].get("description") or "")[:300])
                if getattr(m, "type", "") == "tool" and getattr(m, "name", "") == "task":
                    r.step("note", "planner", "Report received: " + _text(m.content)[:240])
                if getattr(m, "type", "") == "ai" and not getattr(m, "tool_calls", None):   # the planner speaking without a tool: the answer
                    t = _text(m.content).strip()
                    if t:
                        final = t
    final = agent.guard(final or "I planned the search but couldn't put the answer together. Please ask again.")
    aud = agent.audit_numbers(final, r.evidence)
    r.step("check", "planner", f"{aud['numbers']} numbers in the answer checked against {len(r.evidence)} tool results"
           + (": all found." if not aud["unsupported"] else f"; not found in any result: {', '.join(aud['unsupported'][:6])}."),
           "ok" if not aud["unsupported"] else "warn")
    secs = round(time.time() - t0)
    r.step("note", "planner", f"Planned answer in {secs} s.")
    r.state["history"] = (history + [{"role": "user", "content": text}, {"role": "assistant", "content": final}])[-12:]
    out = {"text": final, "ui_actions": curate(r.actions), "state": r.state, "steps": list(r.steps), "mode": "deep"}
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"question": text, "context": app, "at": time.strftime("%Y-%m-%d %H:%M"), "seconds": secs, **out}, default=str))
    except OSError:
        pass
    return out


def _replay(saved: dict, context: dict) -> dict:
    """A cached planned run, shown step by step (and labeled as cached) so a demo doesn't wait two minutes."""
    rid = context.get("request_id") or f"r{time.time_ns()}"
    steps = agent.PROGRESS.setdefault(rid, [])
    steps.append({"kind": "note", "who": "planner", "status": "ok",
                  "text": f"Replaying a planned run cached at {saved.get('at')} (it took {saved.get('seconds')} s live). Set NAVEASY_DEEP_CACHE=off to run it live."})
    for s in saved.get("steps", []):
        steps.append(s)
        time.sleep(0.12)
    st = dict(context.get("state") or {})
    for k in ("homes", "home", "unit", "profiles", "city"):
        if k in (saved.get("state") or {}):
            st[k] = saved["state"][k]
    st["history"] = ((st.get("history") or []) + [{"role": "user", "content": saved.get("question", "")}, {"role": "assistant", "content": saved["text"]}])[-12:]
    return {"text": saved["text"], "ui_actions": saved.get("ui_actions", []), "state": st, "steps": list(steps), "mode": "deep", "cached": True}
