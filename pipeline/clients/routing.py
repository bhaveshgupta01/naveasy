"""OpenStreetMap (Overpass) places + Valhalla accessible routing. Public servers, no keys.

Be polite: about 1 request per second, cache everything. Overpass returned 502s and
timeouts during testing, so we try a mirror and keep queries small.
"""
from __future__ import annotations

import json
import time

import requests

from . import UA, with_backoff

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
VALHALLA = "https://valhalla1.openstreetmap.de"

# NavEasy profile -> Valhalla pedestrian costing options
PROFILE_COSTING = {
    "wheelchair": {"type": "wheelchair"},
    "blind": {"type": "blind"},
    "deaf": {"type": "foot"},
    "older": {"type": "foot", "walking_speed": 3.0},
}

CATEGORY_FILTERS = {
    "hospital": ['[amenity=hospital]', '[healthcare=urgent_care]'],
    "clinic": ['[amenity=clinic]', '[amenity=doctors]'],
    "pharmacy": ['[amenity=pharmacy]', '[shop=chemist]'],
    "grocery": ['[shop=supermarket]'],
    "transit": ['[highway=bus_stop]', '[public_transport=platform]'],
    "restaurant": ['[amenity~"^(restaurant|cafe|fast_food)$"]'],
    "school": ['[amenity~"^(school|college|university)$"]'],
    "park": ['[leisure=park]'],
}


def overpass(query: str) -> dict:
    last = None
    for url in OVERPASS:
        r = with_backoff(lambda: requests.post(url, data={"data": query}, headers=UA, timeout=180), retries=2)
        if r.status_code == 200:
            return r.json()
        last = r
        time.sleep(1)
    raise RuntimeError(f"Overpass failed: HTTP {last.status_code if last is not None else '?'}")


def places_near(lat: float, lon: float, radius_m: int = 1500) -> list[dict]:
    parts = [f"nwr(around:{radius_m},{lat},{lon}){f};" for fs in CATEGORY_FILTERS.values() for f in fs]
    data = overpass(f"[out:json][timeout:90];({''.join(parts)});out tags center;")
    out = []
    for e in data["elements"]:
        t = e.get("tags", {})
        cat = next((c for c, fs in CATEGORY_FILTERS.items() if _matches(t, fs)), None)
        la = e.get("lat") or (e.get("center") or {}).get("lat")
        lo = e.get("lon") or (e.get("center") or {}).get("lon")
        if cat and la:
            out.append({"osm_id": f"{e['type']}/{e['id']}", "category": cat, "name": t.get("name"), "lat": la, "lon": lo})
    return out


def _matches(tags: dict, filters: list[str]) -> bool:
    import re
    for f in filters:
        k, op, v = re.match(r'\[(\w+)(~?=)"?([^"\]]+)"?\]', f).groups()
        if k in tags and (tags[k] == v if op == "=" else re.search(v, tags[k])):
            return True
    return False


def _valhalla(endpoint: str, payload: dict) -> dict:
    r = with_backoff(lambda: requests.get(f"{VALHALLA}/{endpoint}", params={"json": json.dumps(payload)},
                                          headers=UA, timeout=60))
    r.raise_for_status()
    return r.json()


def route(a: tuple[float, float], b: tuple[float, float], profile: str = "wheelchair", alternates: int = 0) -> dict:
    """a, b = (lat, lon). Tested: Midtown Houston wheelchair route 1.31 km in 19.8 min."""
    return _valhalla("route", {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
                               "costing": "pedestrian",
                               "costing_options": {"pedestrian": PROFILE_COSTING[profile]},
                               "alternates": alternates, "units": "kilometers"})


def isochrone(lat: float, lon: float, profile: str = "wheelchair", minutes=(5, 10, 15)) -> dict:
    """Walking-time zones as GeoJSON polygons. Tested: 5/10/15-minute wheelchair zones in Midtown."""
    return _valhalla("isochrone", {"locations": [{"lat": lat, "lon": lon}], "costing": "pedestrian",
                                   "costing_options": {"pedestrian": PROFILE_COSTING[profile]},
                                   "contours": [{"time": m} for m in minutes], "polygons": True})
