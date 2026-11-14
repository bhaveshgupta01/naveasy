"""NavEasy agent (Technical Design section 10).

The agent never computes a score itself: every number comes from a tool that calls the same code as the API,
and every tool call also emits the UI actions the web app runs (zoom, highlight, open a unit, draw a route).

With ANTHROPIC_API_KEY set it runs Claude with tools. Without a key it falls back to a small rule-based
parser so the chat panel still works (profiles, budget, "moving from <address>").
"""
from __future__ import annotations

import json
import math
import os
import re

SYSTEM = """You are NavEasy's housing guide for people with disabilities in Houston and Los Angeles.
The person may use a wheelchair, be blind or have low vision, be deaf or hard of hearing, or be an older adult with low stamina. Profiles: wheelchair, blind, deaf, older.

How you work:
- Every number you state must come from a tool result in this conversation. If a tool has no data, say so; never estimate.
- State the evidence with each claim ("bathroom door about 29 in, measured from the 3D scan") and the confidence the tool reports.
- Never call a home "accessible" or "compliant". Say "meets our wheelchair checks" and name what was not checked (door hardware, counter heights, grab bars, alarms).
- Scores are estimates from scans and public maps, not a certification. Grey or missing data means unknown, not bad.
- Ask at most two follow-up questions, and only if you can't act without the answer. Otherwise act on reasonable defaults and say which you used.
- Keep answers short and concrete: a sentence or two of answer, then up to three options, each with its strongest evidence and its main weakness. Plain words, no jargon. Give distances as minutes.
- The app shows what you look up (the map moves, homes light up, routes draw), so refer to it instead of listing everything.
- For a blind or low-vision person, give routes turn by turn and describe each crossing, and describe homes with describe_layout: directions from the entry door, room sizes, where the windows are and the light they get.
- Answers may be read aloud. Write them as speech: no lists, tables, symbols or markdown; say "32 inches", not "32 in".
- End with one short prompt for what they can ask next."""

TOOLS = [
    {"name": "find_homes", "description": "Find scanned homes and apartment communities ranked by score for the person's profiles. Use for any 'find me a place' request. Returns the top homes with scores, rent and whether the inside was measured from a 3D scan.",
     "input_schema": {"type": "object", "properties": {
         "city": {"type": "string", "enum": ["houston", "la"]},
         "profiles": {"type": "array", "items": {"type": "string", "enum": ["wheelchair", "blind", "deaf", "older"]}},
         "max_rent": {"type": "integer", "description": "Monthly budget in dollars"},
         "near": {"type": "string", "description": "A place or address to search near, e.g. 'Texas Medical Center' or 'Midtown'"},
         "within_km": {"type": "number"}}, "required": ["city", "profiles"]}},
    {"name": "describe_home", "description": "Details for one building: score parts (elevator or walk-up, age, listing evidence), scanned units with their measured problems, minutes to essentials, and a verify-before-you-sign checklist.",
     "input_schema": {"type": "object", "properties": {"building_id": {"type": "string"}, "profiles": {"type": "array", "items": {"type": "string"}}}, "required": ["building_id", "profiles"]}},
    {"name": "check_apartment", "description": "Measured problems inside one scanned unit for a profile: doorway widths, rooms a wheelchair can't reach, turning space, thresholds, walking distances, sightlines. Opens the floor-plan heat map in the app.",
     "input_schema": {"type": "object", "properties": {"model_id": {"type": "string"}, "profile": {"type": "string", "enum": ["wheelchair", "blind", "deaf", "older"]}}, "required": ["model_id", "profile"]}},
    {"name": "describe_layout", "description": "A spoken walk-through of one scanned unit built from its measured geometry: rooms and sizes, directions from the entry door, which way windows face and the sun they get, door widths, problems. Use it whenever someone asks what a home is like inside, especially a blind or low-vision person. Read it out close to verbatim; do not add details it doesn't contain.",
     "input_schema": {"type": "object", "properties": {"model_id": {"type": "string"}, "profile": {"type": "string", "enum": ["wheelchair", "blind", "deaf", "older"]}}, "required": ["model_id", "profile"]}},
    {"name": "route_to", "description": "The walking or rolling route from a building to the nearest place of a category, for one profile: minutes, crossings, hard spots, turn-by-turn steps. Draws the route in the app.",
     "input_schema": {"type": "object", "properties": {"building_id": {"type": "string"},
         "category": {"type": "string", "enum": ["hospital", "clinic", "pharmacy", "grocery", "transit", "restaurant", "school", "park"]},
         "profile": {"type": "string", "enum": ["wheelchair", "blind", "deaf", "older"]}}, "required": ["building_id", "category", "profile"]}},
    {"name": "match_neighborhoods", "description": "For someone moving between Los Angeles and Houston: neighborhoods in the other city where everyday places are as close as at their current address, with homes there.",
     "input_schema": {"type": "object", "properties": {"current_address": {"type": "string"}, "profiles": {"type": "array", "items": {"type": "string"}},
         "budget": {"type": "integer"}, "must_haves": {"type": "array", "items": {"type": "object", "properties": {"category": {"type": "string"}, "max_minutes": {"type": "number"}}, "required": ["category", "max_minutes"]}}},
         "required": ["current_address", "profiles"]}},
    {"name": "plan_furniture", "description": "Place furniture in a scanned unit so the person can move through it: returns placements, the score before and after, and pieces that won't fit. Give either a list of pieces or a furnished scan to take them from.",
     "input_schema": {"type": "object", "properties": {"target_model_id": {"type": "string"}, "profiles": {"type": "array", "items": {"type": "string"}},
         "pieces": {"type": "array", "items": {"type": "string"}, "description": "e.g. queen bed, sofa, dining table, desk"}, "from_model_id": {"type": "string"}},
         "required": ["target_model_id", "profiles"]}},
]

PLACES = {"medical center": (29.7079, -95.4010), "texas medical center": (29.7079, -95.4010), "midtown": (29.7437, -95.3795), "downtown": (29.7589, -95.3677),
          "montrose": (29.7453, -95.3964), "galleria": (29.7390, -95.4630), "heights": (29.7989, -95.3987), "museum district": (29.7223, -95.3897),
          "downtown la": (34.0407, -118.2468), "koreatown": (34.0618, -118.3004), "santa monica": (34.0195, -118.4912), "hollywood": (34.0928, -118.3287)}


def _run_tool(name: str, a: dict, actions: list) -> dict:
    from backend.app import main as api
    if name == "find_homes":
        ps = a["profiles"]
        rows = [b for b in api.buildings().values() if b["city"] == a["city"]]
        center = None
        if a.get("near"):
            center = PLACES.get(a["near"].lower().strip())
            if not center:
                from pipeline.relocate import geocode
                g = geocode(a["near"] + (", Houston, TX" if a["city"] == "houston" else ", Los Angeles, CA"))
                center = (g["lat"], g["lon"]) if g else None
            if center:
                km = a.get("within_km") or 3
                rows = [b for b in rows if math.hypot((b["lat"] - center[0]) * 110.57, (b["lon"] - center[1]) * 96.6) <= km]
        if a.get("max_rent"):
            rows = [b for b in rows if b.get("rent_min") and b["rent_min"] <= a["max_rent"]]
        score = lambda b: min(b["scores"][p]["score"] for p in ps)  # noqa: E731
        rows.sort(key=lambda b: -(score(b) + (5 if b.get("inside") else 0)))
        top = rows[:5]
        if top:
            actions.append({"type": "set_map_view", "center": [sum(b["lon"] for b in top) / len(top), sum(b["lat"] for b in top) / len(top)], "zoom": 13.5, "profiles": ps, "city": a["city"]})
            actions.append({"type": "highlight_buildings", "ids": [b["id"] for b in top]})
        return {"matches": len(rows), "search_center_found": bool(center) if a.get("near") else None, "homes": [{
            "building_id": b["id"], "name": b["name"], "kind": b["kind"], "score": score(b), "confidence": api._combine(b["scores"], ps, "min")["confidence"],
            "rent_from": b.get("rent_min"), "inside_measured_from_scan": bool(b.get("inside")),
            "vertical_access": b["components"]["vertical_access"]["why"], "built": b.get("year_built"),
            "best_unit": (b.get("inside") or {}).get(ps[0], {}).get("best_model")} for b in top]}
    if name == "describe_home":
        d = api.building(a["building_id"], ",".join(a["profiles"]))
        actions.append({"type": "open_building", "id": a["building_id"]})
        return {k: d.get(k) for k in ("name", "address", "kind", "stories", "elevator", "walkup", "year_built", "rent_min", "rent_max", "combined", "scores", "components", "nearest", "checklist")} | {
            "units": [{"model_id": u["model_id"], "caption": u["caption"], "status": u["status"], "scores": u["scores"], "problems": [p["text"] for p in u["problems"]]} for u in d["units"][:6]]}
    if name == "check_apartment":
        u = api.unit_heatmap(a["model_id"], a["profile"])
        if not u.get("has_graph"):
            return {"message": u.get("message")}
        actions.append({"type": "open_unit", "model_id": a["model_id"], "profile": a["profile"], "problem_id": (u["problems"] or [{}])[0].get("id")})
        return {"caption": u["caption"], "scan_date": u["scan_date"], "score": u["scores"][a["profile"]], "checks": u["checks"], "stats": u["stats"],
                "problems": [{"severity": p["severity"], "text": p["text"]} for p in u["problems"][:10]],
                "doorways": [{"clear_in": d["est_clear_in"], "status": d["status"], "entry": d["entry"]} for d in u["doors"] if d["is_door"] and not d["minor"]],
                "not_checked": "fixtures (toilet, tub, counters), door hardware, counter heights, grab bars, alarms"}
    if name == "describe_layout":
        from . import narrate
        u = api.unit_heatmap(a["model_id"], a["profile"])
        actions.append({"type": "open_unit", "model_id": a["model_id"], "profile": a["profile"]})
        return narrate.unit_narration(u, a["profile"])
    if name == "route_to":
        r = api.routes(a["building_id"], a["category"], a["profile"])
        actions.append({"type": "show_route", "building_id": a["building_id"], "category": a["category"], "profile": a["profile"]})
        return {k: r[k] for k in ("minutes", "meters", "crossings", "crossings_with_curb_data", "confidence", "notes", "destination", "unknown_share")} | {
            "hard_spots": [h["text"] for h in r["hard_spots"]], "steps": r["steps"],
            "stretches": [{"status": s["status"], "meters": s["meters"], "why": s["why"]} for s in r["stretches"]]}
    if name == "match_neighborhoods":
        from pipeline import relocate as RL
        r = RL.match({"address": a["current_address"]}, a["profiles"], a.get("must_haves"), a.get("budget"))
        if r.get("error"):
            return r
        actions.append({"type": "show_relocation", "request": {"origin": {"address": a["current_address"]}, "profiles": a["profiles"], "budget": a.get("budget"), "must_haves": a.get("must_haves") or []}})
        return {"origin_minutes": r["origin"]["minutes"], "origin_minutes_are_routed": r["origin"]["live"], "target_city": r["target_name"], "message": r.get("message"),
                "neighborhoods": [{k: c[k] for k in ("name", "match", "comparison", "reason", "homes", "minutes_estimated")} for c in r["candidates"]]}
    if name == "plan_furniture":
        from pipeline.furnish import inventory_from, plan_furnishing
        inv = [{"label": p} for p in a.get("pieces") or []] or (inventory_from(a["from_model_id"]) if a.get("from_model_id") else [])
        if not inv:
            return {"error": "No furniture given."}
        r = plan_furnishing(a["target_model_id"], inv, a["profiles"], {})
        actions.append({"type": "open_unit", "model_id": a["target_model_id"], "profile": a["profiles"][0], "tab": "plan", "plan": r})
        return {k: r[k] for k in ("score_empty", "score_before", "score_after", "before_label", "wont_fit", "reasons")} | {
            "placements": [{"label": p["label"], "room": p["room"]} for p in r["placements"]]}
    return {"error": f"unknown tool {name}"}


def chat(messages: list[dict], context: dict | None = None) -> dict:
    context = context or {}
    if not os.getenv("ANTHROPIC_API_KEY"):
        from . import dialogue
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
        try:
            return dialogue.respond(last, context.get("state"), context)
        except Exception as e:  # noqa: BLE001 - a spoken interface must always answer
            return {"text": f"Sorry, I couldn't look that up ({str(getattr(e, 'detail', e))[:120]}). Please try again.", "ui_actions": [], "state": context.get("state") or {}}
    import anthropic
    client = anthropic.Anthropic()
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if context:
        msgs[-1] = {"role": "user", "content": f"{msgs[-1]['content']}\n\n(App state: {json.dumps(context)})"}
    actions: list = []
    for _ in range(8):
        try:
            resp = client.messages.create(model=os.getenv("NAVEASY_AGENT_MODEL", "claude-opus-5-5"), max_tokens=4000, system=SYSTEM, tools=TOOLS,
                                          output_config={"effort": "low"}, messages=msgs)
        except anthropic.RateLimitError:
            return {"text": "The assistant is busy right now. Please try again in a moment.", "ui_actions": actions}
        except anthropic.APIStatusError as e:
            return {"text": f"The assistant couldn't answer (error {e.status_code}). The filters and map still work.", "ui_actions": actions}
        except anthropic.APIConnectionError:
            return {"text": "The assistant can't be reached right now. The filters and map still work.", "ui_actions": actions}
        if resp.stop_reason == "refusal":
            return {"text": "I can't help with that request.", "ui_actions": actions}
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return {"text": "".join(b.text for b in resp.content if b.type == "text").strip(), "ui_actions": actions}
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                try:
                    out, err = _run_tool(b.name, b.input, actions), False
                except Exception as e:  # noqa: BLE001 - report the failure to the model
                    out, err = {"error": str(getattr(e, "detail", e))[:300]}, True
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str)[:12000], "is_error": err})
        msgs.append({"role": "user", "content": results})
    return {"text": "I looked up several things but ran out of steps. Please ask again more specifically.", "ui_actions": actions}


# ---------------------------------------------------------------- no-key fallback

WORDS = {"wheelchair": r"wheelchair|power ?chair|scooter|walker|can'?t (use|climb) stairs", "blind": r"blind|low vision|visually impaired|guide dog|cane",
         "deaf": r"deaf|hard of hearing|hearing (loss|aid)", "older": r"older|elderly|senior|low stamina|my (mom|mother|dad|father|grand)|arthritis|tire"}


def _fallback(messages: list[dict], context: dict) -> dict:
    text = " ".join(m["content"] for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str))
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    ps = [p for p, rx in WORDS.items() if re.search(rx, text, re.I)] or context.get("profiles") or ["wheelchair"]
    budget = re.search(r"\$\s?([\d,]{3,6})", text)
    budget = int(budget.group(1).replace(",", "")) if budget else None
    actions: list = []
    note = " (Simple mode: add ANTHROPIC_API_KEY to .env for the full assistant.)"
    names = ", ".join({"wheelchair": "wheelchair", "blind": "blind or low vision", "deaf": "deaf or hard of hearing", "older": "older adult"}[p] for p in ps)
    move = re.search(r"(?:moving|move|relocat\w+) from (.+?)(?: to (?:houston|la|los angeles)|[.;]|$)", last, re.I)
    try:
        if move:
            r = _run_tool("match_neighborhoods", {"current_address": move.group(1).strip(), "profiles": ps, "budget": budget}, actions)
            if r.get("error"):
                return {"text": r["error"] + note, "ui_actions": actions}
            lines = [f"{n['name']} (match {n['match']}): {n['reason']}" for n in r["neighborhoods"]]
            return {"text": f"For {names}, the closest matches in {r['target_city']}:\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(lines)) + note, "ui_actions": actions}
        city = "la" if re.search(r"\bla\b|los angeles", last, re.I) and not re.search(r"houston", last, re.I) else context.get("city", "houston")
        near = next((k for k in PLACES if k in text.lower()), None)
        r = _run_tool("find_homes", {"city": city, "profiles": ps, "max_rent": budget, "near": near}, actions)
        if not r["homes"]:
            return {"text": f"No scanned homes match ({names}{', under $' + str(budget) if budget else ''}{', near ' + near if near else ''}). Try a higher budget or a wider area." + note, "ui_actions": actions}
        lines = [f"{h['name']}: score {h['score']} ({h['confidence']} confidence), {h['vertical_access']}"
                 + (f", from ${h['rent_from']:,}" if h["rent_from"] else "") + ("" if h["inside_measured_from_scan"] else "; inside not measured") for h in r["homes"][:3]]
        return {"text": f"Top homes for {names}{' near ' + near.title() if near else ''}{' under $' + format(budget, ',') if budget else ''} (lit up on the map):\n"
                        + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(lines))
                        + "\nThese are estimates from scans and public maps, not a certification. Open a home to see its measured doorways." + note, "ui_actions": actions}
    except Exception as e:  # noqa: BLE001
        return {"text": f"I couldn't look that up ({str(getattr(e, 'detail', e))[:120]})." + note, "ui_actions": actions}
