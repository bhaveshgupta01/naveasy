"""Relocation match (Technical Design section 9): where in the other city are my essentials as close as at home?

    python -m pipeline.relocate "1200 W 7th St, Los Angeles" wheelchair
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from functools import lru_cache

import h3
import requests

from .clients import ROOT, UA
from . import locality
from .buildings import city_of

DATA = ROOT / "data"
CITY_NAME = {"houston": "Houston", "la": "Los Angeles"}
CAT_LABEL = {"hospital": "hospital", "clinic": "clinic", "pharmacy": "pharmacy", "grocery": "grocery", "transit": "bus or rail stop",
             "restaurant": "restaurants", "school": "school", "park": "park"}


def geocode(address: str) -> dict | None:
    cache = DATA / "geocode.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if address in known:
        return known[address]
    r = requests.get("https://nominatim.openstreetmap.org/search", params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
                     headers=UA, timeout=30)
    time.sleep(1)
    hits = r.json() if r.status_code == 200 else []
    if not hits:
        return None
    out = {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]), "name": hits[0].get("display_name")}
    known[address] = out
    cache.write_text(json.dumps(known))
    return out


@lru_cache(maxsize=2)
def _hexes(city: str) -> dict:
    d = json.loads((DATA / f"hexes_{city}.json").read_text())
    return {"meta": d, "cells": {c["h3"]: c for c in d["cells"]}}


@lru_cache(maxsize=1)
def _buildings() -> list:
    p = DATA / "buildings_scored.json"
    return json.loads((p if p.exists() else DATA / "buildings.json").read_text())


@lru_cache(maxsize=2)
def _by_cell(city: str) -> dict:
    out = defaultdict(list)
    for b in _buildings():
        if b["city"] == city:
            out[b["h3"]].append(b)
    return out


def fingerprint(lat: float, lon: float, profiles: list[str], live: bool = True) -> dict:
    """Minutes to the nearest essentials around any point, per profile (live Valhalla, else estimated)."""
    city = city_of(lat, lon)
    if city not in ("houston", "la"):      # Princeton is in city_of for the venue, but has no neighborhood data
        raise ValueError("The address is outside Houston and Los Angeles.")
    pois = _pois(city)
    near = locality.nearest_km(pois, city, lat, lon)
    minutes = locality.live_minutes(city, lat, lon, near, pois, cache_key=f"pt_{lat:.5f}_{lon:.5f}") if live else None
    is_live = bool(minutes)
    if not minutes:
        f = _hexes(city)["meta"]["minutes_per_km"]
        minutes = {p: {c: round(d * f[p], 1) for c, (d, _) in near.items()} for p in locality.PROFILES}
    cell = h3.latlng_to_cell(lat, lon, 9)
    streets = (_hexes(city)["cells"].get(cell) or {}).get("streets")
    return {"city": city, "lat": lat, "lon": lon, "h3": cell, "live": is_live, "minutes": {p: minutes[p] for p in locality.PROFILES},
            "places": {c: {"name": p.get("name"), "lat": p["lat"], "lon": p["lon"]} for c, (_, p) in near.items()}, "streets": streets}


@lru_cache(maxsize=2)
def _pois(city: str):
    return locality.load_pois(city)


def _slowest(minutes: dict, profiles: list[str]) -> dict:
    cats = set().union(*(minutes[p].keys() for p in profiles))
    return {c: max(minutes[p].get(c, 99) for p in profiles) for c in cats}


def _name(city: str, cell: str) -> str:
    homes = [b for nb in h3.grid_disk(cell, 2) for b in _by_cell(city).get(nb, [])]
    names = [b["neighborhood"] for b in homes if b.get("neighborhood")]
    if names:
        return max(set(names), key=names.count)
    zips = [b["address"].split()[-1][:5] for b in homes if b.get("address") and b["address"].split()[-1][:5].isdigit()]
    return f"ZIP {max(set(zips), key=zips.count)}" if zips else "Unnamed area"


def match(origin: dict, profiles: list[str], must_haves: list[dict] | None = None, budget: int | None = None,
          target: str | None = None, top: int = 3, combine_score=None) -> dict:
    must_haves = must_haves or []
    if origin.get("address") and origin.get("lat") is None:
        g = geocode(origin["address"])
        if not g:
            return {"error": f"Couldn't find the address: {origin['address']}"}
        origin = {**origin, **g}
    fp = fingerprint(origin["lat"], origin["lon"], profiles)
    target = target or ("houston" if fp["city"] == "la" else "la")
    home = _slowest(fp["minutes"], profiles)
    cells = _hexes(target)["cells"]
    by_cell = _by_cell(target)
    e_default = _hexes(target)["meta"]["street_default"]
    e_home = locality.street_score(fp["streets"])
    scored, failed_must = [], defaultdict(int)
    same_city = target == fp["city"]
    for cell, blds in by_cell.items():
        rec = cells.get(cell)
        if not rec:
            continue
        if same_city:      # a move within the city: skip the blocks around the current home
            clat, clon = h3.cell_to_latlng(cell)
            if math.hypot((clat - fp["lat"]) * 110.57, (clon - fp["lon"]) * 96.6) < 2.0:
                continue
        there = _slowest(rec["minutes"], profiles)
        ok = True
        for m in must_haves:
            if there.get(m["category"], 99) > m["max_minutes"]:
                failed_must[m["category"]] += 1
                ok = False
        if not ok:
            continue
        homes = [b for nb in h3.grid_disk(cell, 1) for b in by_cell.get(nb, [])]
        if budget:
            homes = [b for b in homes if b.get("rent_min") and b["rent_min"] <= budget]
        if not homes:
            continue

        def hscore(b):
            return min(b["scores"][p]["score"] for p in profiles)
        homes.sort(key=lambda b: (-(b.get("inside") is not None), -hscore(b)))
        homes = sorted(homes, key=lambda b: -hscore(b) - (8 if b.get("inside") else 0))[:3]
        d = 0.0
        for c, (good, zero, w, _) in locality.CATEGORIES.items():
            d += w * min(1.0, max(0.0, there.get(c, 99) - home.get(c, 99)) / (zero - good))
        e_there = locality.street_score(rec.get("streets"))
        s_streets = sum((e_there[p] if e_there else e_default[p]) for p in profiles) / len(profiles)
        if e_home and e_there:
            s_streets = 1 - min(1.0, max(0.0, sum(e_home[p] - e_there[p] for p in profiles) / len(profiles)) / 0.5)
        s_housing = min(1.0, sum(1 for b in homes if hscore(b) >= 60) / 2)
        m_score = 100 * (0.65 * (1 - d) + 0.2 * s_streets + 0.15 * s_housing)
        scored.append((m_score, cell, rec, there, homes))
    scored.sort(key=lambda t: -t[0])
    out, seen = [], set()
    for m_score, cell, rec, there, homes in scored:
        name = _name(target, cell)
        if name in seen:
            continue
        seen.add(name)
        better = sorted((c for c in home if c in there and there[c] < home[c] - 1), key=lambda c: there[c] - home[c])
        worse = sorted((c for c in home if c in there and there[c] > home[c] + 2), key=lambda c: home[c] - there[c])
        reason = f"In {name}, " + (
            f"the {CAT_LABEL[better[0]]} is closer than at home ({there[better[0]]:.0f} min against {home[better[0]]:.0f})" if better else
            "everyday places are about as close as at home")
        if worse:
            reason += f"; the {CAT_LABEL[worse[0]]} is farther ({there[worse[0]]:.0f} min against {home[worse[0]]:.0f})"
        reason += f". {len(homes)} scanned home{'s' if len(homes) != 1 else ''} nearby, best score {max(min(b['scores'][p]['score'] for p in profiles) for b in homes)}."
        lat, lon = h3.cell_to_latlng(cell)
        out.append({"h3": cell, "name": name, "lat": lat, "lon": lon, "match": round(m_score),
                    "comparison": {c: [round(home[c], 1), round(there[c], 1)] for c in locality.CATEGORIES if c in home and c in there},
                    "minutes_estimated": not rec.get("live"),
                    "homes": [{"id": b["id"], "name": b["name"], "score": min(b["scores"][p]["score"] for p in profiles),
                               "rent_min": b.get("rent_min"), "measured_inside": bool(b.get("inside"))} for b in homes],
                    "reason": reason, "worse": [CAT_LABEL[c] for c in worse]})
        if len(out) >= top:
            break
    res = {"origin": {**{k: origin.get(k) for k in ("address", "name")}, "lat": fp["lat"], "lon": fp["lon"], "city": fp["city"],
                      "minutes": home, "live": fp["live"], "places": fp["places"]},
           "target": target, "target_name": CITY_NAME[target], "profiles": profiles, "must_haves": must_haves, "budget": budget, "candidates": out,
           "note": "Look-alike imagery matching isn't used in this score. Minutes marked estimated come from straight-line distance, not a routed walk."}
    if not out and failed_must:
        worst = max(failed_must, key=failed_must.get)
        res["message"] = f"No neighborhood meets every must-have; '{CAT_LABEL[worst]}' ruled out the most. Try relaxing it."
    return res


if __name__ == "__main__":
    r = match({"address": sys.argv[1]}, sys.argv[2].split(","))
    print(json.dumps(r["origin"], indent=1)[:900])
    for c in r["candidates"]:
        print(c["match"], c["name"], c["comparison"], "\n  ", c["reason"], "\n  ", c["homes"])
