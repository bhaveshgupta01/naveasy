"""NavEasy API: real data from the pipeline (docs/API_CONTRACT.md).

Reads the precomputed tables in data/ (built by pipeline.buildings, pipeline.score_units, pipeline.locality)
and computes apartment heat maps, routes, furnishing plans and relocation matches on request.
If data/ hasn't been built, it falls back to the synthetic files in frontend/public/mock.

Run from the repo root:
    . .venv/bin/activate
    uvicorn backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "frontend" / "public" / "mock"
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")
# .env files from the team kit sometimes name the keys "Anthropic" and "Gemini"; accept both spellings
for _alias, _name in (("Anthropic", "ANTHROPIC_API_KEY"), ("Gemini", "GEMINI_API_KEY")):
    if not os.getenv(_name) and os.getenv(_alias):
        os.environ[_name] = os.environ[_alias]

PROFILES = ("wheelchair", "blind", "deaf", "older")
Profile = Literal["wheelchair", "blind", "deaf", "older"]
ORDER = {"low": 0, "medium": 1, "high": 2}
REAL = (DATA / "buildings.json").exists()
CATEGORY_LABEL = {"hospital": "Hospital", "clinic": "Clinic", "pharmacy": "Pharmacy", "grocery": "Grocery", "transit": "Bus or rail stop",
                  "restaurant": "Restaurants", "school": "School", "park": "Park"}

app = FastAPI(title="NavEasy API", version="0.2.0")
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=2000)   # the building and hex lists are megabytes of JSON
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?", allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- data access

def _mock(name: str):
    path = MOCK / name
    if not path.exists():
        raise HTTPException(404, f"mock file {name} not found; run python scripts/make_mock.py")
    return json.loads(path.read_text())


@lru_cache(maxsize=1)
def buildings() -> dict:
    path = DATA / "buildings_scored.json"
    rows = json.loads((path if path.exists() else DATA / "buildings.json").read_text())
    return {b["id"]: b for b in rows}


@lru_cache(maxsize=2)
def hexes(city: str) -> dict:
    path = DATA / f"hexes_{city}.json"
    if not path.exists():
        raise HTTPException(404, f"no locality data for {city}; run python -m pipeline.locality {city}")
    d = json.loads(path.read_text())
    d["by_id"] = {c["h3"]: c for c in d["cells"]}
    return d


@lru_cache(maxsize=1)
def units_index() -> dict:
    path = DATA / "units_index.json"
    return json.loads(path.read_text()) if path.exists() else {}


@lru_cache(maxsize=48)
def _plan(model_id: str):
    from pipeline.unit import load_plan
    if not (DATA / "matterport" / f"{model_id}.json").exists() and not os.getenv("MATTERPORT_API_KEY"):
        raise HTTPException(404, "no geometry cached for this scan")
    try:
        plan = load_plan(model_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"couldn't load the scan: {e}") from e
    if plan.has_graph and not plan.fixtures and os.getenv("MATTERPORT_API_KEY"):
        # first time this home is opened: fetch its 3D mesh (about 1 MB) for the counters, island, toilet and tub
        from pipeline import fixtures
        try:
            fixtures.attach(plan, fetch=True)
        except Exception:  # noqa: BLE001  no mesh: measure without built-ins, as before
            pass
    return plan


@lru_cache(maxsize=1)
def _unit_rows() -> dict:
    """model id -> its row in the building table, to refresh a unit's scores once it is measured with its built-ins."""
    return {u["model_id"]: u for b in buildings().values() for u in b["units"]}


def _refresh_unit(model_id: str, res: dict) -> None:
    row = _unit_rows().get(model_id)
    if row is not None and row.get("scores"):
        row["scores"] = res["scores"]
    idx = units_index().get(model_id)
    if idx is not None:
        idx["scores"], idx["problems"] = res["scores"], res["problems"]


@lru_cache(maxsize=96)
def _analysis(model_id: str, furnished: bool, chair_cm: int):
    from pipeline.furnish import load_furniture
    from pipeline.unit import analyze
    furniture = load_furniture(model_id) if furnished else []
    return analyze(_plan(model_id), furniture, chair_width=chair_cm / 100 if chair_cm else None), furniture


def _profiles(raw: str) -> list[str]:
    ps = [p.strip() for p in raw.split(",") if p.strip()]
    if not ps or any(p not in PROFILES for p in ps):
        raise HTTPException(422, f"profiles must be a comma list of {PROFILES}")
    return ps


def _combine(scores: dict, profiles: list[str], how: str) -> dict:
    vals = [scores[p]["score"] for p in profiles]
    conf = min((scores[p]["confidence"] for p in profiles), key=ORDER.get)
    return {"score": min(vals) if how == "min" else round(sum(vals) / len(vals)), "confidence": conf}


def _slim(b: dict, ps: list[str], how: str) -> dict:
    keys = ("id", "kind", "name", "address", "city", "lat", "lon", "stories", "elevator", "walkup", "year_built", "rent_min", "rent_max",
            "price", "units_scanned", "scores", "beds", "baths", "sqft")
    out = {k: b.get(k) for k in keys}
    out["measured_inside"] = bool(b.get("inside"))
    out["splat_sid"] = next((u["model_id"] for u in b["units"] if u["model_id"] in _splat_ids()), None)
    out["splat"] = out["splat_sid"] is not None
    try:
        cell = hexes(b["city"])["by_id"].get(b["h3"])
    except HTTPException:
        cell = None
    out["locality_scores"] = cell["scores"] if cell else None
    out["combined"] = _combine(b["scores"], ps, how)
    return out


# ---------------------------------------------------------------- map

@app.get("/health")
def health():
    from backend.app import speech
    return {"ok": True, "mode": "real" if REAL else "mock", "agent": bool(os.getenv("ANTHROPIC_API_KEY")), "voice": bool(os.getenv("GEMINI_API_KEY")),
            "speech": speech.available(), "local_model": __import__("backend.app.brain", fromlist=["model"]).model(),
            "matterport_sdk_key": bool(os.getenv("MATTERPORT_SDK_KEY"))}


@app.get("/meta")
def meta():
    if not REAL:
        return {"mode": "mock"}
    bs = buildings().values()
    idx = units_index()
    out = {"mode": "real", "cities": {}}
    for city in ("houston", "la"):
        rows = [b for b in bs if b["city"] == city]
        out["cities"][city] = {
            "buildings": len(rows), "apartment_communities": sum(1 for b in rows if b["kind"] == "apartment_community"),
            "homes": sum(1 for b in rows if b["kind"] == "home"), "measured_inside": sum(1 for b in rows if b.get("inside")),
            "scans": sum(len(b["units"]) for b in rows)}
    out["units_measured"] = sum(1 for s in idx.values() if s.get("has_graph"))
    out["doorways_measured"] = sum(s["stats"]["doorways"] for s in idx.values() if s.get("has_graph"))
    hero = DATA / "hero.json"
    out["hero"] = json.loads(hero.read_text()) if hero.exists() else {}
    return out


@app.get("/map/buildings")
def map_buildings(city: str = "houston", profiles: str = "wheelchair", combine: Literal["min", "avg"] = "min",
                  passing_only: bool = False, min_score: int = 60, max_rent: int | None = None):
    ps = _profiles(profiles)
    if not REAL:
        data = _mock("buildings.json")
        rows = [{**b, "combined": _combine(b["scores"], ps, combine)} for b in data["buildings"]]
        return {"city": data["city"], "profiles": ps, "combine": combine, "buildings": [b for b in rows if not passing_only or b["combined"]["score"] >= min_score]}
    out = []
    for b in buildings().values():
        if b["city"] != city:
            continue
        s = _slim(b, ps, combine)
        if passing_only and s["combined"]["score"] < min_score:
            continue
        if max_rent and not (b.get("rent_min") and b["rent_min"] <= max_rent):
            continue
        out.append(s)
    return {"city": city, "profiles": ps, "combine": combine, "buildings": out}


@app.get("/map/hexes")
def map_hexes(city: str = "houston", profiles: str = "wheelchair", combine: Literal["min", "avg"] = "min"):
    ps = _profiles(profiles)
    if not REAL:
        data = _mock("hexes.json")
        return {"city": data["city"], "res": data["res"], "profiles": ps, "cells": [{**c, "combined": _combine(c["scores"], ps, combine)} for c in data["cells"]]}
    d = hexes(city)
    # compact rows keep the payload small: [h3, combined score, confidence (0 low, 1 medium, 2 high), live,
    # index into `profiles` of the need with the lowest score here, each need's own score in order]; details: /map/hexes/{cell}
    cells = []
    for c in d["cells"]:
        cb = _combine(c["scores"], ps, combine)
        weakest = min(range(len(ps)), key=lambda i: c["scores"][ps[i]]["score"])
        cells.append([c["h3"], cb["score"], ORDER[cb["confidence"]], 1 if c["live"] else 0, weakest, [c["scores"][p]["score"] for p in ps]])
    return {"city": city, "res": d["res"], "profiles": ps, "combine": combine, "cells": cells, "note": d.get("note")}


@app.get("/map/hexes/{cell}")
def hex_detail(cell: str, city: str = "houston"):
    c = hexes(city)["by_id"].get(cell)
    if not c:
        raise HTTPException(404, "no data for this cell")
    return c


# ---------------------------------------------------------------- buildings

def _checklist(b: dict, unit_problems: list[dict]) -> list[str]:
    out = []
    comp = b["components"]
    if comp["vertical_access"]["value"] < 1:
        out.append("Ask which floor the unit is on and whether there is a working elevator or a step-free ground-floor unit.")
    out.append("Check the route from parking or the street to the unit door: steps, ramp slope, door weight and width.")
    kinds = {p["kind"] for p in unit_problems}
    if "narrow_door" in kinds:
        out.append("Measure the flagged doorways with the door open at 90 degrees; ask whether offset hinges or a wider door are allowed.")
    if "threshold" in kinds:
        out.append("Look at the flagged thresholds; a small ramp may be needed.")
    if "no_turning_space" in kinds or "unreachable_room" in kinds:
        out.append("Check the bathroom with its fixtures in place: turning room, space beside the toilet, shower entry.")
    out.append("We did not check: counter and switch heights, door hardware, grab bars, visual or audible alarms.")
    return out


def _splat_ids() -> set:
    from pipeline import splat as SP
    return SP.splat_ids()


@app.get("/buildings/{building_id}")
def building(building_id: str, profiles: str = "wheelchair", combine: Literal["min", "avg"] = "min"):
    ps = _profiles(profiles)
    if not REAL:
        for b in _mock("buildings.json")["buildings"]:
            if b["id"] == building_id:
                return {**b, "components": {"vertical_access": 0.8, "approach": 0.6, "listing_evidence": 0.3, "age": 0.7}, "units": ["u_sample_001"], "nearest": []}
        raise HTTPException(404, "building not found")
    b = buildings().get(building_id)
    if not b:
        raise HTTPException(404, "building not found")
    idx = units_index()
    units, problems = [], []
    for u in b["units"]:
        s = idx.get(u["model_id"], {})
        item = {"model_id": u["model_id"], "caption": u.get("caption") or s.get("caption"), "status": u.get("status", "pending"),
                "dwelling": u.get("dwelling", True), "scores": u.get("scores"), "stats": u.get("stats"),
                "furnished_scan": (DATA / "furniture" / f"{u['model_id']}.json").exists(),
                "splat": u["model_id"] in _splat_ids(),
                "problems": [p for p in s.get("problems", []) if p["profile"] in ps][:4]}
        problems += [p for p in s.get("problems", []) if p["profile"] in ps]
        units.append(item)
    units.sort(key=lambda u: (not u["dwelling"], u["status"] != "measured", -(min(u["scores"][p]["score"] for p in ps) if u["scores"] else 0)))
    nearest = []
    try:
        cell = hexes(b["city"])["by_id"].get(b["h3"])
        if cell:
            nearest = [{"category": c, "label": CATEGORY_LABEL[c], "minutes": {p: cell["minutes"][p].get(c) for p in ps}, "estimated": not cell["live"]}
                       for c in CATEGORY_LABEL if any(cell["minutes"][p].get(c) is not None for p in ps)]
    except HTTPException:
        cell = None
    return {**{k: v for k, v in b.items() if k != "units"}, "combined": _combine(b["scores"], ps, combine), "units": units, "nearest": nearest,
            "locality_detail": {"scores": {p: cell["scores"][p] for p in ps}, "streets": cell.get("streets"), "live": cell["live"]} if cell else None,
            "checklist": _checklist(b, problems)}


# ---------------------------------------------------------------- apartments

@app.get("/units/{model_id}/heatmap")
def unit_heatmap(model_id: str, profile: Profile = "wheelchair", furnished: bool = False, floor: int | None = None, chair_cm: int = 0):
    if not REAL:
        unit = _mock(f"unit_{model_id}.json")
        heat = unit["heat"].get(profile) or unit["heat"]["wheelchair"]
        return {**unit, "heat": heat["furnished" if furnished else "empty"], "profile": profile, "furnished": furnished}
    from pipeline.unit import unit_json
    plan = _plan(model_id)
    if not plan.has_graph:
        return {"model_id": model_id, "caption": plan.caption, "has_graph": False, "rooms": plan.room_summary,
                "scan_date": (plan.created or "")[:10] or None, "defurnished_id": units_index().get(model_id, {}).get("defurnished"),
                "message": "Matterport didn't measure walls and doorways for this scan, so there is no heat map, door widths or "
                           "problem list. You can still look around in 3D and see Matterport's own 2D floor plan."}
    res, furniture = _analysis(model_id, furnished, chair_cm)
    if floor is not None and not 0 <= floor < len(plan.floors):
        raise HTTPException(422, "no such floor")
    big = sum(f.shape[0] * f.shape[1] for f in plan.floors) > 160_000
    out = unit_json(plan, res, profile, floor, furniture, out_cell=0.1 if big else 0.05)
    if not furnished and not chair_cm and plan.fixtures:
        _refresh_unit(model_id, res)
    out["has_graph"] = True
    out["defurnished_id"] = units_index().get(model_id, {}).get("defurnished")
    out["has_furniture_scan"] = (DATA / "furniture" / f"{model_id}.json").exists()
    out["tour_url"] = f"https://my.matterport.com/show/?m={model_id}&play=1&qs=1"
    out["all_problems"] = res["problems"]
    return out


def _check_id(model_id: str) -> None:
    import re
    if not re.fullmatch(r"[A-Za-z0-9]{6,40}", model_id):
        raise HTTPException(422, "not a Matterport model id")


@app.get("/units/{model_id}/floorplans")
def unit_floorplans(model_id: str):
    """Matterport's own 2D floor plans for any scan, even one without a wall graph: a top-down photo and a schematic
    (walls, doorways, room areas) per floor. Downloaded once and cached; the image links Matterport gives expire."""
    _check_id(model_id)
    folder = DATA / "floorplans" / model_id
    manifest = folder / "floorplans.json"
    if not manifest.exists():
        if not os.getenv("MATTERPORT_API_KEY"):
            raise HTTPException(404, "no Matterport key to fetch floor plans")
        import requests
        from pipeline.clients import matterport
        try:
            fps = matterport.assets(model_id).get("floorplans") or []
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"couldn't reach Matterport: {e}") from e
        folder.mkdir(parents=True, exist_ok=True)
        rows, seen = [], set()
        for fp in fps:
            flags = fp.get("flags") or []
            kind = "schematic" if "schematic" in flags else "photo" if "photogramy" in flags and "alpha" in flags else None
            floor = (fp.get("floor") or {}).get("sequence") or 0
            if not kind or fp.get("format") != "png" or not fp.get("url") or (floor, kind) in seen:
                continue   # the first of each kind per floor is the largest
            seen.add((floor, kind))
            name = f"{floor}_{kind}.png"
            (folder / name).write_bytes(requests.get(fp["url"], timeout=60).content)
            rows.append({"floor": floor, "label": (fp.get("floor") or {}).get("label") or f"Floor {floor + 1}", "kind": kind, "file": name})
        manifest.write_text(json.dumps(sorted(rows, key=lambda r: (r["floor"], r["kind"] != "schematic"))))
    rows = json.loads(manifest.read_text())
    return [{k: r[k] for k in ("floor", "label", "kind")} | {"url": f"/units/{model_id}/floorplan/{r['file']}"} for r in rows]


@app.get("/units/{model_id}/floorplan/{name}")
def unit_floorplan_image(model_id: str, name: str):
    _check_id(model_id)
    path = DATA / "floorplans" / model_id / name
    if not name.endswith(".png") or "/" in name or ".." in name or not path.exists():
        raise HTTPException(404, "no such floor plan")
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/units/{model_id}/summary")
def unit_summary(model_id: str):
    s = units_index().get(model_id)
    if not s:
        raise HTTPException(404, "scan not indexed")
    return s


# ---------------------------------------------------------------- routes

@app.get("/routes")
def routes(building_id: str | None = None, category: str = "pharmacy", profile: Profile = "wheelchair",
           from_lat: float | None = None, from_lon: float | None = None, to_lat: float | None = None, to_lon: float | None = None,
           to_name: str | None = None):
    if not REAL:
        return _mock("route_b000_pharmacy_wheelchair.json")
    from pipeline import routes as R
    from pipeline.buildings import city_of
    if building_id:
        b = buildings().get(building_id)
        if not b:
            raise HTTPException(404, "building not found")
        from_lat, from_lon = b["lat"], b["lon"]
    if from_lat is None:
        raise HTTPException(422, "give building_id or from_lat and from_lon")
    city = city_of(from_lat, from_lon)
    if not city:
        raise HTTPException(422, "origin is outside Houston and Los Angeles")
    try:
        if to_lat is not None:
            r = R.build_route(city, (from_lat, from_lon), (to_lat, to_lon), profile, to_name)
        else:
            if category not in CATEGORY_LABEL:
                raise HTTPException(422, f"category must be one of {list(CATEGORY_LABEL)}")
            r = R.route_to_category(city, from_lat, from_lon, category, profile)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"routing service failed: {e}") from e
    if not r:
        raise HTTPException(404, f"no {category} found nearby")
    return r


@app.get("/aerial")
def aerial(lat: float, lon: float, meters: int = 30):
    """Vexcel top-down photo around a point (cached on disk). Imagery (c) Vexcel Imaging US, Inc."""
    from pipeline.clients import vexcel
    path = DATA / "aerial" / f"{lat:.5f}_{lon:.5f}_{meters}.jpg"
    if not path.exists():
        try:
            img = vexcel.ortho_image(vexcel.point(lat, lon), buffer_m=meters, size="640,640")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"imagery service failed: {e}") from e
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img)
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# ---------------------------------------------------------------- furnishing

@app.get("/furnish/score")
def furnish_score(model_id: str, profiles: str = "wheelchair"):
    if not REAL:
        return _mock(f"furnish_score_{model_id}.json")
    from pipeline.furnish import load_furniture, score_furnishing
    ps = _profiles(profiles)
    if not load_furniture(model_id):
        raise HTTPException(404, "no furniture inventory for this scan yet (run python -m pipeline.furniture fetch/find on it)")
    return _furnish_score_cached(model_id, ",".join(ps))


@lru_cache(maxsize=32)
def _furnish_score_cached(model_id: str, ps: str):
    from pipeline.furnish import score_furnishing
    return score_furnishing(model_id, ps.split(","))


@app.get("/furnish/inventory")
def furnish_inventory(model_id: str):
    from pipeline.furnish import inventory_from
    return {"model_id": model_id, "inventory": inventory_from(model_id)}


class FurnishRequest(BaseModel):
    target_model_id: str
    profiles: list[Profile] = ["wheelchair"]
    inventory: list[dict] = []
    from_model_id: str | None = None      # take the inventory from this furnished scan
    preferences: dict = {}


@app.post("/furnish/plan")
def furnish_plan(req: FurnishRequest):
    if not REAL:
        return _mock(f"furnish_plan_{req.target_model_id}.json")
    from pipeline.furnish import inventory_from, plan_furnishing
    inv = req.inventory or (inventory_from(req.from_model_id) if req.from_model_id else [])
    if not inv:
        raise HTTPException(422, "give an inventory or from_model_id")
    plan = _plan(req.target_model_id)
    if not plan.has_graph:
        raise HTTPException(422, "the target scan has no measured floor plan")
    out = plan_furnishing(req.target_model_id, inv, list(req.profiles), req.preferences)
    out["inventory"] = inv
    return out


class HeatmapRequest(BaseModel):
    model_id: str
    profile: Profile = "wheelchair"
    furniture: list[dict] = []
    chair_cm: int = 0


@app.post("/units/heatmap")
def unit_heatmap_custom(req: HeatmapRequest):
    """Heat map for a custom furniture layout (a furnishing plan, or pieces the user dragged)."""
    from pipeline.unit import analyze, unit_json
    plan = _plan(req.model_id)
    furniture = [{**it, "floor": it.get("floor", plan.entry_floor)} for it in req.furniture]
    res = analyze(plan, furniture, chair_width=req.chair_cm / 100 if req.chair_cm else None)
    out = unit_json(plan, res, req.profile, None, furniture)
    out["has_graph"] = True
    out["all_problems"] = res["problems"]
    return out


# ---------------------------------------------------------------- relocation

class RelocateRequest(BaseModel):
    origin: dict
    profiles: list[Profile] = ["wheelchair"]
    must_haves: list[dict] = []
    budget: int | None = None
    target: Literal["houston", "la"] | None = None


@app.post("/relocate")
def relocate(req: RelocateRequest):
    if not REAL:
        return _mock("relocate_sample.json")
    from pipeline import relocate as RL
    try:
        out = RL.match(req.origin, list(req.profiles), req.must_haves, req.budget, req.target)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if out.get("error"):
        raise HTTPException(404, out["error"])
    return out


# ---------------------------------------------------------------- agent and voice

class ChatRequest(BaseModel):
    messages: list[dict]
    session_id: str | None = None
    context: dict = {}


@app.post("/agent/chat")
def agent_chat(req: ChatRequest):
    if not REAL:
        return _mock("agent_reply_sample.json")
    from backend.app import agent
    return agent.chat(req.messages, req.context)


@app.post("/voice/token")
def voice_token():
    """Mint a short-lived Gemini Live token for the browser (docs/VOICE.md). The API key never leaves the server."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(503, "GEMINI_API_KEY is not set in .env")
    import datetime as dt
    import requests
    now = dt.datetime.now(dt.timezone.utc)
    r = requests.post("https://generativelanguage.googleapis.com/v1beta/auth_tokens", params={"key": key}, timeout=20, json={
        "uses": 1, "expireTime": (now + dt.timedelta(minutes=30)).isoformat(), "newSessionExpireTime": (now + dt.timedelta(minutes=1)).isoformat()})
    if r.status_code != 200:
        raise HTTPException(502, f"token service said {r.status_code}: {r.text[:200]}")
    return {"token": r.json().get("name"), "model": os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")}


def nearest_buildings(lat: float, lon: float, km: float, city: str) -> list[dict]:
    out = []
    for b in buildings().values():
        if b["city"] == city:
            d = math.hypot((b["lat"] - lat) * 110.57, (b["lon"] - lon) * 111.32 * math.cos(math.radians(lat)))
            if d <= km:
                out.append(b)
    return out


# ---------------------------------------------------------------- Vexcel imagery: map tiles and obliques

@app.get("/tiles/{z}/{x}/{y}.jpg")
def aerial_tile(z: int, x: int, y: int):
    """Vexcel aerial map tiles (WMTS) proxied so the API key stays on the server; cached on disk.
    Imagery (c) Vexcel Imaging US, Inc."""
    import requests
    from pipeline.clients import vexcel
    path = DATA / "tiles" / str(z) / str(x) / f"{y}.jpg"
    if not path.exists():
        body = None
        for layer in ("urban", "wide-area"):
            r = requests.get(f"{vexcel.API}/ortho/wmts", headers=vexcel._headers(), timeout=30, params={
                "service": "wmts", "request": "getTile", "layer": layer, "Style": "RGB", "TileMatrixSet": layer,
                "TileMatrix": z, "TileRow": y, "TileCol": x, "format": "image/jpeg"})
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                body = r.content
                break
            if r.status_code == 429:
                raise HTTPException(429, "imagery rate limit")
        if body is None:
            raise HTTPException(404, "no imagery here")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})


@app.get("/oblique")
def oblique(lat: float, lon: float, direction: Literal["north", "east", "south", "west"] = "north", meters: int = 45):
    """Vexcel angled (oblique) photo of a building from one side. 404 where no oblique flight exists."""
    from pipeline.clients import vexcel
    path = DATA / "oblique" / f"{lat:.5f}_{lon:.5f}_{direction}_{meters}.jpg"
    miss = path.with_suffix(".none")
    if miss.exists():
        raise HTTPException(404, "no angled photo from this side")
    if not path.exists():
        try:
            img = vexcel.oblique_image(vexcel.point(lat, lon), f"oblique-{direction}", buffer_m=meters)
        except Exception as e:  # noqa: BLE001
            path.parent.mkdir(parents=True, exist_ok=True)
            miss.write_text(str(e)[:200])
            raise HTTPException(404, "no angled photo from this side") from e
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img)
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})


# ---------------------------------------------------------------- aerial Gaussian splats (outside of the home in 3D)

@app.get("/splats/{sid}.ply")
def splat(sid: str):
    """The home and its lot as a Gaussian splat (cropped from Matterport's drone splat, cached). 3D (c) Matterport."""
    from fastapi.responses import FileResponse
    from pipeline import splat as SP
    if sid not in SP.splat_ids():
        raise HTTPException(404, "no aerial splat for this scan")
    try:
        path, _ = SP.fetch(sid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"couldn't fetch the splat: {e}") from e
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "max-age=604800"})


@app.get("/surface")
def surface(lat: float, lon: float, meters: int = 80, grid: int = 320):
    """A 3D surface of the block around a point: Vexcel surface heights on a grid, to be draped with /surface.jpg.
    Heights are meters above the lowest ground in view, packed as base64 uint16 centimeters, row 0 = north."""
    import base64
    import io
    import numpy as np
    import tifffile
    from pipeline.clients import vexcel
    key = f"{lat:.5f}_{lon:.5f}_{meters}"
    path = DATA / "surface" / f"{key}_{grid}.json"
    if path.exists():
        return json.loads(path.read_text())
    try:
        raw = vexcel.dsm(lat, lon, meters)
        tex = vexcel.ortho_image(vexcel.point(lat, lon), buffer_m=meters, size="1536,1536")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"imagery service failed: {e}") from e
    a = tifffile.imread(io.BytesIO(raw)).astype("float32")
    a[a < -1000] = np.nan
    ground = float(np.nanpercentile(a, 2))
    a = np.nan_to_num(a, nan=ground)
    rows, cols = a.shape
    step_r, step_c = max(1, rows // grid), max(1, cols // grid)
    # keep the tallest point in each block so roof edges stay crisp
    r2, c2 = rows // step_r * step_r, cols // step_c * step_c
    small = a[:r2, :c2].reshape(r2 // step_r, step_r, c2 // step_c, step_c).max(axis=(1, 3))
    h = np.clip((small - ground) * 100, 0, 65535).astype("<u2")
    out = {"lat": lat, "lon": lon, "width_m": 2.0 * meters, "depth_m": 2.0 * meters * rows / cols, "cols": int(h.shape[1]), "rows": int(h.shape[0]),
           "max_m": round(float(h.max()) / 100, 1), "heights": base64.b64encode(h.tobytes()).decode(), "texture": f"/surface.jpg?key={key}",
           "credit": "Surface heights and imagery (c) Vexcel Imaging US, Inc."}
    path.parent.mkdir(parents=True, exist_ok=True)
    (DATA / "surface" / f"{key}.jpg").write_bytes(tex)
    path.write_text(json.dumps(out))
    return out


@app.get("/surface.jpg")
def surface_texture(key: str):
    import re
    if not re.fullmatch(r"[-0-9._]+", key):
        raise HTTPException(422, "bad key")
    path = DATA / "surface" / f"{key}.jpg"
    if not path.exists():
        raise HTTPException(404, "call /surface first")
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})


# ---------------------------------------------------------------- 3D terrain tiles from Vexcel surface heights


@app.get("/dem/{z}/{x}/{y}.png")
def dem_tile(z: int, x: int, y: int):
    from pipeline.terrain import build_dem_tile
    try:
        body = build_dem_tile(z, x, y)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"height service failed: {e}") from e
    if body is None:
        raise HTTPException(404, "no height data here")
    return Response(body, media_type="image/png", headers={"Cache-Control": "max-age=604800"})


@app.get("/crosswalk")
def crosswalk(lat: float, lon: float):
    """Does Vexcel's aerial photo show a painted crosswalk within about 15 m of this point? (cached)"""
    from pipeline.clients import vexcel
    path = DATA / "detect" / f"crosswalk_{lat:.5f}_{lon:.5f}.json"
    if path.exists():
        return json.loads(path.read_text())
    try:
        res = vexcel.detect(vexcel.square(lat, lon, 15), ["crosswalk"], threshold=0.3)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"imagery service failed: {e}") from e
    found = []

    def walk(o):
        if isinstance(o, dict):
            if "confidence" in o and ("bbox" in o or "label" in o):
                found.append(float(o["confidence"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(res)
    out = {"seen": bool(found), "count": len(found), "confidence": round(max(found), 2) if found else None,
           "text": (f"Vexcel's aerial photo shows a painted crosswalk here ({max(found):.0%} confidence)." if found
                    else "No painted crosswalk found in the aerial photo here (trees or shadows can hide one).")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


@app.get("/units/{model_id}/narration")
def unit_narration(model_id: str, profile: Profile = "wheelchair", furnished: bool = False):
    """A spoken walk-through of the unit, built from its measured geometry (for screen readers and voice)."""
    from backend.app import narrate
    return narrate.unit_narration(unit_heatmap(model_id, profile, furnished), profile)


# ---------------------------------------------------------------- open-source speech (runs locally)

from fastapi import Request  # noqa: E402


@app.on_event("startup")
def _warm_speech():
    from backend.app import brain, speech
    speech.warm()
    brain.warm()


@app.post("/speech/transcribe")
async def speech_transcribe(request: Request):
    """A browser recording (webm, mp4 or wav bytes as the body) -> text, with Whisper running on this machine."""
    from fastapi.concurrency import run_in_threadpool
    from backend.app import speech
    if not speech.available()["listen"]:
        raise HTTPException(503, "local speech recognition isn't installed (pip install faster-whisper, and ffmpeg)")
    body = await request.body()
    if len(body) < 800:
        return {"text": "", "seconds": 0}
    try:
        return await run_in_threadpool(speech.transcribe, body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class SayRequest(BaseModel):
    text: str
    speed: float = 1.0


@app.post("/speech/say")
def speech_say(req: SayRequest):
    """Text -> spoken WAV, with Piper running on this machine."""
    from backend.app import speech
    if not speech.available()["speak"]:
        raise HTTPException(503, "local speech isn't installed (pip install piper-tts and put the voice in data/speech)")
    if not req.text.strip():
        raise HTTPException(422, "nothing to say")
    return Response(speech.say(req.text[:3000], req.speed), media_type="audio/wav")
