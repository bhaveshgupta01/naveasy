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

SYSTEM = """You are NavEasy, a housing guide who talks like a knowledgeable real estate broker, for homes in Houston and Los Angeles.
You serve everyone. Many clients use a wheelchair, are blind or have low vision, are deaf or hard of hearing, or are older adults with low stamina (profiles: wheelchair, blind, deaf, older); others have no particular need.
About a property, answer the way a good broker would: specific and confident, built from the measured facts: the rent, the layout, what was measured inside, what's nearby in minutes, the main weakness stated plainly, and what to verify before signing. describe_home returns a broker_summary built from the data; use it.

How you work:
- Every number you state must come from a tool result in this conversation. If a tool has no data, say so; never estimate.
- Talk like a knowledgeable friend, not a report: lead with the plain verdict for this person ("your wheelchair gets through every door", "there isn't room to turn around in the bathroom"), and build every verdict only from what the tools returned. Keep numbers to the few that matter (rent, minutes, and the one measurement behind a real problem), say once that it comes from the 3D scan, and offer the exact measurements if they want them. If they ask for details or exact numbers, give them with their evidence and confidence. Unless they ask for details, keep the whole answer under about 120 words.
- Never call a home "accessible" or "compliant". Say "meets our wheelchair checks" and name what was not checked (door hardware, counter heights, grab bars, alarms).
- Scores are estimates from scans and public maps, not a certification. Grey or missing data means unknown, not bad.
- Ask at most two follow-up questions, and only if you can't act without the answer. Otherwise act on reasonable defaults and say which you used.
- Keep answers short and concrete: a sentence or two of answer, then up to three options, each with its strongest evidence and its main weakness. Plain words, no jargon. Give distances as minutes.
- The app shows what you look up (the map moves, homes light up, routes draw), so refer to it instead of listing everything.
- For a blind or low-vision person, give routes turn by turn and describe each crossing, and describe homes with describe_layout: directions from the entry door, room sizes, where the windows are and the light they get.
- Answers may be read aloud. Write them as speech: no lists, tables, symbols or markdown; say "32 inches", not "32 in".
- End with one short prompt for what they can ask next.
- The venue: the judges are standing in the Julis Romo Rabinowitz Building in Princeton (the HackPrinceton venue). For questions about "this building", the classroom, its doors, stairs, or getting here from the station or parking, use the venue tool. Its numbers come from a 3D scan and each says whether a tape measure has checked it; repeat that."""

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

TOOLS.append({"name": "venue", "description": "The HackPrinceton venue, the Julis Romo Rabinowitz Building in Princeton, measured from its 3D scan as a public building: "
               "summary, the way from the entrance to a room (classroom, landing, stairs) with the narrowest point, door widths, stairs and level changes, "
               "or the walk from Princeton station (the Dinky) or the nearest parking lot. Each number says whether a tape measure has checked it.",
               "input_schema": {"type": "object", "properties": {
                   "ask": {"type": "string", "enum": ["summary", "inside_route", "doors", "stairs", "outside_route"]},
                   "to_place": {"type": "string", "description": "for inside_route: classroom, landing or stairs"},
                   "near": {"type": "string", "description": "for doors: a room, e.g. classroom"},
                   "origin": {"type": "string", "enum": ["dinky", "parking"], "description": "for outside_route"},
                   "profile": {"type": "string", "enum": ["wheelchair", "blind", "deaf", "older"]}}, "required": ["ask"]}})

PLACES = {"medical center": (29.7079, -95.4010), "texas medical center": (29.7079, -95.4010), "midtown": (29.7437, -95.3795), "downtown": (29.7589, -95.3677),
          "montrose": (29.7453, -95.3964), "galleria": (29.7390, -95.4630), "heights": (29.7989, -95.3987), "museum district": (29.7223, -95.3897),
          "downtown la": (34.0407, -118.2468), "koreatown": (34.0618, -118.3004), "santa monica": (34.0195, -118.4912), "hollywood": (34.0928, -118.3287)}


def _remember(state: dict | None, name: str, a: dict, out: dict) -> None:
    """What a tool found becomes part of the conversation: the homes listed, the home and unit being discussed."""
    if state is None or not isinstance(out, dict) or out.get("error"):
        return
    if name == "find_homes" and out.get("homes"):
        state["homes"] = [{"id": h["building_id"], "name": h["name"], "score": h["score"]} for h in out["homes"]]
        state["profiles"], state["city"] = a["profiles"], a["city"]
        state["home"], state["unit"] = None, None
    elif name in ("describe_home", "route_to"):
        state["home"] = a["building_id"]
    elif name in ("check_apartment", "describe_layout"):
        state["unit"] = a["model_id"]
    elif name == "plan_furniture":
        state["unit"] = a["target_model_id"]


def _run_tool(name: str, a: dict, actions: list, state: dict | None = None) -> dict:
    out = _tool(name, a, actions)
    _remember(state, name, a, out)
    return out


def _tool(name: str, a: dict, actions: list) -> dict:
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
        from . import dialogue, narrate
        d = api.building(a["building_id"], ",".join(a["profiles"]))
        actions.append({"type": "open_building", "id": a["building_id"]})
        uid = dialogue._best_unit(a["building_id"], a["profiles"])
        best = None
        if uid:
            try:
                best = api.unit_heatmap(uid, a["profiles"][0])
                best = best if best.get("has_graph") else None
            except Exception:  # noqa: BLE001
                best = None
        return {"broker_summary": narrate.broker_pitch(d, best, a["profiles"])} | {k: d.get(k) for k in ("name", "address", "kind", "stories", "elevator", "walkup", "year_built", "rent_min", "rent_max", "combined", "scores", "components", "nearest", "checklist")} | {
            "units": [{"model_id": u["model_id"], "caption": u["caption"], "status": u["status"], "scores": u["scores"], "problems": [p["text"] for p in u["problems"]]} for u in d["units"][:6]]}
    if name == "check_apartment":
        u = api.unit_heatmap(a["model_id"], a["profile"])
        if not u.get("has_graph"):
            return {"message": u.get("message")}
        actions.append({"type": "open_unit", "model_id": a["model_id"], "profile": a["profile"], "problem_id": (u["problems"] or [{}])[0].get("id")})
        return {"caption": u["caption"], "scan_date": u["scan_date"], "score": u["scores"][a["profile"]], "checks": u["checks"], "stats": u["stats"],
                "problems": [{"severity": p["severity"], "text": p["text"]} for p in u["problems"][:10]],
                "doorways": [{"clear_in": d["est_clear_in"], "status": d["status"], "entry": d["entry"]} for d in u["doors"] if d["is_door"] and not d["minor"]],
                "built_ins": [{k: f.get(k) for k in ("kind", "room_type", "width_in", "depth_in", "height_in", "measured", "kind_source", "possibly_movable")}
                              for f in u.get("fixture_items") or [] if f.get("kind") not in ("toilet tank", "shelf or ledge", "raised floor or platform")][:12],
                "built_ins_note": "Dimensions measured from the 3D mesh, good to about an inch; what each item is was inferred from the room, size and height."
                                  if u.get("fixture_items") else "No 3D mesh for this scan, so built-ins (counters, toilet, tub) were not measured.",
                "not_checked": "grab bars, door hardware, switch heights, visual or audible alarms"}
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
        from . import narrate
        r = plan_furnishing(a["target_model_id"], inv, a["profiles"], {})
        actions.append({"type": "open_unit", "model_id": a["target_model_id"], "profile": a["profiles"][0], "tab": "plan", "plan": r})
        spoken = narrate.plan_narration(r, api.unit_heatmap(a["target_model_id"], a["profiles"][0]), a["profiles"])["text"]
        return {k: r[k] for k in ("score_empty", "score_before", "score_after", "before_label", "wont_fit", "reasons")} | {
            "placements": [{"label": p["label"], "room": p["room"]} for p in r["placements"]], "where_each_piece_goes": spoken}
    if name == "venue":
        from pipeline import venue as V

        from . import narrate
        prof = a.get("profile") or "wheelchair"
        s = V.venue_summary()
        actions.append({"type": "set_map_view", "center": [s["lon"], s["lat"]], "zoom": 17.6, "city": "princeton"})
        actions.append({"type": "open_building", "id": V.VENUE_ID})
        ask = a.get("ask", "summary")
        if ask == "inside_route":
            actions.append({"type": "open_unit", "model_id": V.PRIMARY, "profile": prof})
            r = V.inside_route("entrance", a.get("to_place") or "classroom", prof)
            return {k: r.get(k) for k in ("from", "to", "meters", "needs_site_visit", "between_floors", "spoken")} | {"legs": [{k: leg.get(k) for k in leg if k != "points"} for leg in r.get("legs", [])]}
        if ask == "doors":
            actions.append({"type": "open_unit", "model_id": V.PRIMARY, "profile": prof})
            d = V.door_widths(near=a.get("near"))
            return {"spoken": narrate.venue_doors(d, a.get("near")), "doors": [{k: x.get(k) for k in ("floor_label", "rooms", "clear_in", "status", "entrance", "evidence")} for x in d["doors"]],
                    "artefacts": len(d.get("artefacts") or []), "how_measured": d.get("how_measured")}
        if ask == "stairs":
            st = V.stairs()
            return {"spoken": narrate.venue_stairs(st, V.inside_route("entrance", "stairs", prof)), "elevator": st.get("elevator"), "raised_parts": st.get("raised_parts")}
        if ask == "outside_route":
            o = V.outside_route(a.get("origin") or "dinky", prof)
            if o.get("route"):
                actions.append({"type": "draw_route", "route": o["route"], "building_id": V.VENUE_ID})
            return {k: o.get(k) for k in ("from", "to", "minutes", "meters", "crossings", "hard_spots", "confidence", "notes", "caveat")}
        return {k: s.get(k) for k in ("name", "address", "floors", "area_m2", "entrances", "doorways", "doorways_pass", "narrowest_door_in", "stairs",
                                      "route_to_classroom", "scores", "score_note", "problems", "ground_truth", "location_evidence")}
    return {"error": f"unknown tool {name}"}


# When Claude can't be used (no credit left, a bad key), answer on this machine instead, and stop trying for ten minutes
_DOWN = {"until": 0.0, "why": ""}


def claude_down() -> bool:
    import time
    return time.time() < _DOWN["until"]


def _mark_down(err: Exception | str) -> bool:
    """True (and Claude is skipped for a while) when the error means it can't answer at all, not a passing hiccup."""
    import time
    msg = str(err)
    if re.search(r"credit balance|billing|authentication|invalid x-api-key|permission|401|403", msg, re.I):
        _DOWN.update(until=time.time() + (1800 if "credit" in msg.lower() else 600),
                     why="no Anthropic credit left" if "credit" in msg.lower() else "the Anthropic key was refused")
        return True
    return False


# request id -> the steps of the work in progress, so the app can show them while it waits (GET /agent/progress/{id})
PROGRESS: dict[str, list] = {}


def progress(rid: str) -> list:
    if len(PROGRESS) > 200:                      # keep the last runs only
        for k in list(PROGRESS)[:-100]:
            PROGRESS.pop(k, None)
    return PROGRESS.get(rid, [])


def route(text: str, context: dict) -> str:
    """Which path answers: "local" (voice, the guided conversation, no key), "deep" (planned, multi-step home
    searches), or "claude" (one tool-calling agent, for everything else typed)."""
    from . import deep, dialogue
    st = context.get("state") or {}
    if context.get("channel") == "voice" or text.startswith("__") or st.get("guide") in dialogue.GUIDE_STAGES or st.get("pending_needs"):
        return "local"      # voice, the guided conversation, and signals from the app (__start__, __ui_needs__) stay local
    if os.getenv("NAVEASY_DEEP", "on").lower() not in ("off", "0", "false", "no") and deep.cached(text, context) and deep.wants_plan(text, st):
        return "deep"       # a cached planned run replays without calling any model, so it works even with Claude down
    prov = deep.provider()
    if not prov or os.getenv("NAVEASY_AGENT", "").lower() == "local":
        return "local"
    if deep.enabled() and deep.wants_plan(text, st):
        return "deep"
    return prov          # "claude" or "gemini": one tool-calling agent


def chat(messages: list[dict], context: dict | None = None) -> dict:
    """The one entry point, channel-neutral: text and state in, text, UI actions and state out."""
    context = context or {}
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    how = route(last.strip(), context)
    from . import dialogue

    def local(note: str | None = None) -> dict:
        out = dialogue.respond(last, context.get("state"), context)
        out["mode"] = "local"
        if note:
            out["steps"] = [{"kind": "note", "who": "agent", "status": "warn", "text": note}]
        return out
    try:
        from . import deep
        if how == "deep":
            try:
                return deep.run(last, context)
            except Exception as e:  # noqa: BLE001 - the planned mode is optional; fall back to the single agent, then to this machine
                if _mark_down(e):
                    if deep.provider() == "gemini":       # Claude is out of credit: plan the same request on Gemini
                        try:
                            out = deep.run(last, context)
                            out.setdefault("steps", []).insert(0, {"kind": "note", "who": "planner", "status": "warn",
                                                                   "text": f"Claude is unavailable ({_DOWN['why']}), so Gemini planned this."})
                            return out
                        except Exception as e2:  # noqa: BLE001
                            return local(f"Claude and Gemini couldn't answer ({str(e2)[:80]}), so this was answered on this machine.")
                    return local(f"Claude is unavailable ({_DOWN['why']}), so this was answered on this machine.")
                out = claude(last, context)
                if out.get("unavailable"):
                    return local(f"Claude is unavailable ({_DOWN['why'] or 'error'}), so this was answered on this machine.")
                out["steps"] = [{"kind": "note", "text": f"Planned mode failed ({str(e)[:120]}); answered by the single agent instead."}] + out.get("steps", [])
                return out
        if how == "claude":
            out = claude(last, context)
            if not out.get("unavailable"):
                return out
            how = "gemini" if deep.provider() == "gemini" else "local"
            if how == "local":
                return local(f"Claude is unavailable ({_DOWN['why'] or 'error'}), so this was answered on this machine.")
        if how == "gemini":
            try:
                return deep.single(last, context)
            except Exception as e:  # noqa: BLE001
                return local(f"Gemini couldn't answer ({str(e)[:80]}), so this was answered on this machine.")
        return local()
    except Exception as e:  # noqa: BLE001 - a spoken interface must always answer
        return {"text": f"Sorry, I couldn't look that up ({str(getattr(e, 'detail', e))[:120]}). Please try again.", "ui_actions": [], "state": context.get("state") or {}, "mode": how}


NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def audit_numbers(text: str, evidence: list[str]) -> dict:
    """Every number in the answer should appear in a tool result. Returns the ones that don't (unit conversions and
    list numbering aside), so an answer that invents a figure is caught before anyone hears it."""
    blob = " ".join(evidence)
    found = {n.replace(",", "") for n in NUM.findall(blob)}
    # numbers the tools carry as floats are spoken rounded: 13.8 minutes -> 14; 0.86 -> 86 percent
    for n in list(found):
        try:
            v = float(n)
        except ValueError:
            continue
        found |= {str(round(v)), str(int(v)), str(round(v * 100)), f"{round(v * 3.281)}", f"{round(v / 0.0254)}", f"{round(v * 10.764)}", f"{round(v * 10.764):,}".replace(",", "")}
    said = [n.replace(",", "") for n in NUM.findall(text)]
    small = {str(i) for i in range(0, 11)}           # list numbering, "one of three", "5-foot circle"
    loose = [n for n in said if n not in found and n not in small and n not in ("32", "36", "60", "34", "1991", "2010")]
    return {"numbers": len(said), "unsupported": sorted(set(loose))}


WORDS_NOT_TO_USE = [(re.compile(r"\b(?:fully |ADA[- ])?(?:wheelchair[- ])?accessible\b", re.I), "workable"),
                    (re.compile(r"\b(?:ADA[- ])?complian(?:t|ce)\b", re.I), "meets our checks")]


def guard(text: str) -> str:
    """The project never calls a place accessible or compliant; a model occasionally does, so the words are replaced."""
    for rx, sub in WORDS_NOT_TO_USE:
        text = rx.sub(sub, text)
    return text


def _call_line(name: str, a: dict) -> str:
    keys = {"find_homes": ("city", "near", "max_rent"), "describe_home": ("building_id",), "check_apartment": ("model_id", "profile"),
            "describe_layout": ("model_id",), "route_to": ("building_id", "category"), "match_neighborhoods": ("current_address",),
            "plan_furniture": ("target_model_id",), "venue": ("ask", "to_place", "near", "origin")}.get(name, ())
    return name + "(" + ", ".join(f"{k}={a[k]}" for k in keys if a.get(k) is not None) + ")"


def claude(text: str, context: dict) -> dict:
    """One Claude agent with the seven tools. The conversation so far rides in state["history"] (text only)."""
    import anthropic
    st = dict(context.get("state") or {})
    history = [m for m in st.get("history", []) if m.get("role") in ("user", "assistant") and m.get("content")][-12:]
    app = {k: context[k] for k in ("city", "profiles", "screen") if context.get(k)}
    msgs = history + [{"role": "user", "content": f"{text}\n\n(App state: {json.dumps(app, default=str)[:3000]})"}]
    client = anthropic.Anthropic()
    actions: list = []
    steps: list = PROGRESS.setdefault(context.get("request_id") or f"r{id(context)}", [])
    evidence: list[str] = []
    reply = None
    for _ in range(8):
        try:
            resp = client.beta.messages.create(model=os.getenv("NAVEASY_AGENT_MODEL", "claude-opus-5-5"), max_tokens=4000, system=SYSTEM, tools=TOOLS,
                                               output_config={"effort": "low"}, messages=msgs,
                                               betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except anthropic.RateLimitError:
            reply = "The assistant is busy right now. Please try again in a moment."
            break
        except anthropic.APIStatusError as e:
            if _mark_down(e) or e.status_code >= 500:
                return {"unavailable": True}
            reply = f"The assistant couldn't answer (error {e.status_code}). The filters and map still work."
            break
        except anthropic.APIConnectionError:
            return {"unavailable": True}
        if resp.stop_reason == "refusal":
            reply = "I can't help with that request."
            break
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            reply = "".join(b.text for b in resp.content if b.type == "text").strip()
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                try:
                    out, err = _run_tool(b.name, b.input, actions, st), False
                except Exception as e:  # noqa: BLE001 - report the failure to the model
                    out, err = {"error": str(getattr(e, "detail", e))[:300]}, True
                body = json.dumps(out, default=str)[:12000]
                evidence.append(body)
                steps.append({"kind": "tool", "who": "agent", "text": _call_line(b.name, b.input), "status": "error" if err else "ok"})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": body, "is_error": err})
        msgs.append({"role": "user", "content": results})
    if reply is None:
        reply = "I looked up several things but ran out of steps. Please ask again more specifically."
    reply = guard(reply)
    if evidence:
        aud = audit_numbers(reply, evidence)
        steps.append({"kind": "check", "who": "agent", "status": "ok" if not aud["unsupported"] else "warn",
                      "text": f"{aud['numbers']} numbers in the answer checked against the tool results"
                              + (": all found." if not aud["unsupported"] else f"; not found in any result: {', '.join(aud['unsupported'][:6])}.")})
    st["history"] = (history + [{"role": "user", "content": text}, {"role": "assistant", "content": reply}])[-12:]
    return {"text": reply, "ui_actions": actions, "state": st, "steps": list(steps), "mode": "claude"}


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
