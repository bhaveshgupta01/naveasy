"""Route from A to B for one profile (Technical Design section 6).

Valhalla picks the path; trace_attributes describes every stretch; cached OpenStreetMap crossings,
kerbs, signals and benches along the line become hard spots. Unknown stays grey, never green.

    python -m pipeline.routes 29.7437 -95.3795 pharmacy wheelchair
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from functools import lru_cache

import numpy as np
from scipy.spatial import cKDTree

from .clients import ROOT, routing
from . import locality

DATA = ROOT / "data"
WALKWAY_USES = {"footway", "sidewalk", "path", "pedestrian", "pedestrian_crossing", "cycleway", "track", "living_street", "elevator", "escalator"}
BUSY = {"motorway", "trunk", "primary", "secondary", "tertiary"}
ROUGH = {"compacted", "dirt", "gravel", "path", "impassable"}
ORDER = {"green": 0, "grey": 1, "amber": 2, "red": 3}
SPEED_KMH = {"wheelchair": None, "blind": None, "deaf": None, "older": 3.0}


def decode6(s: str) -> list[list[float]]:
    """Valhalla polyline, precision 6 -> [[lon, lat], ...]."""
    out, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += d
            else:
                lon += d
        out.append([lon / 1e6, lat / 1e6])
    return out


def _m(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    return lon * 111_320 * math.cos(math.radians(lat0)), lat * 110_570


@lru_cache(maxsize=2)
def _streets(city: str):
    path = DATA / "osm" / f"streets_{city}.json"
    if not path.exists():
        return None
    s = json.loads(path.read_text())
    lat0 = (s["bbox"][0] + s["bbox"][2]) / 2
    groups = {"crossing": [], "kerb": [], "bench": [], "sidewalk": []}
    for n in s["nodes"]:
        t = n["tags"]
        if t.get("highway") == "crossing":
            groups["crossing"].append(n)
        if "kerb" in t:
            groups["kerb"].append(n)
        if t.get("amenity") == "bench":
            groups["bench"].append(n)
    for w in s["ways"]:
        if w["tags"].get("highway") == "footway" and w["tags"].get("footway") == "sidewalk":
            for lon, lat in w["geom"][:: max(1, len(w["geom"]) // 6)]:
                groups["sidewalk"].append({"lat": lat, "lon": lon, "tags": {}})
    trees = {k: (cKDTree(np.array([_m(n["lon"], n["lat"], lat0) for n in v])) if v else None, v) for k, v in groups.items()}
    no_sidewalk = {w["id"] for w in s["ways"] if w["tags"].get("sidewalk") in ("no", "none")}
    return {"bbox": s["bbox"], "lat0": lat0, "trees": trees, "no_sidewalk": no_sidewalk}


def _near(st, kind: str, lon: float, lat: float, radius_m: float) -> list[dict]:
    tree, items = st["trees"][kind]
    if tree is None:
        return []
    return [items[i] for i in tree.query_ball_point(_m(lon, lat, st["lat0"]), radius_m)]


def _stretch_status(e: dict, profile: str, sidewalk_near: bool | None, tagged_none: bool = False) -> tuple[str, str]:
    use, grade = e.get("use"), abs(e.get("weighted_grade") or 0)
    name = (e.get("names") or [None])[0]
    on = f" on {name}" if name else ""
    if use == "steps":
        return ("red", "Steps") if profile == "wheelchair" else ("amber", "Steps")
    if grade > 5:
        if profile in ("wheelchair", "older"):
            return "red", f"Slope about {grade:.0f}%{' (over 8.3%: too steep for a wheelchair)' if grade > 8.3 and profile == 'wheelchair' else ''}{on}"
        if profile == "blind":
            return "amber", f"Slope about {grade:.0f}%{on}"
    if use in WALKWAY_USES:
        if e.get("surface") in ROUGH and profile in ("wheelchair", "older"):
            return "amber", f"Unpaved or rough path ({e['surface']})"
        return "green", "Sidewalk or walkway" if use != "pedestrian_crossing" else "Marked crossing"
    # the route runs along a street
    if e.get("sidewalk") in ("both", "left", "right"):
        return "green", f"Sidewalk{on}"
    if sidewalk_near:
        return "amber", f"Follows the street{on}; a sidewalk is mapped beside it"
    if tagged_none:
        if e.get("road_class") in BUSY:
            return ("amber" if profile == "deaf" else "red"), f"No sidewalk{on}; the route runs on a busy street"
        return "amber", f"No sidewalk{on}; quiet street"
    # not mapped is not the same as not there: unknown stays grey
    return "grey", f"Sidewalk not mapped{on}; check on a visit"


def _crossing_spot(n: dict, kerbs: list[dict], profile: str) -> dict | None:
    t = n["tags"]
    kerb = t.get("kerb") or next((k["tags"]["kerb"] for k in kerbs if k["tags"].get("kerb")), None)
    signals = t.get("crossing") == "traffic_signals" or t.get("crossing:signals") == "yes"
    unmarked = t.get("crossing") == "unmarked" or t.get("crossing:markings") == "no"
    spot = None
    if kerb in ("raised", "yes") and profile in ("wheelchair", "older"):
        spot = ("no_lowered_curb", "red" if profile == "wheelchair" else "amber", "Crossing with a raised curb (no curb ramp recorded).")
    elif profile == "blind" and not signals:
        spot = ("no_signal", "red" if not unmarked else "red", "Crossing without traffic signals" + (" and without markings." if unmarked else "."))
    elif profile == "blind" and signals and t.get("traffic_signals:sound") != "yes":
        spot = ("no_audible_signal", "amber", "Signalized crossing; no audible signal recorded"
                + (" and no tactile paving." if t.get("tactile_paving") == "no" else "."))
    elif unmarked and profile in ("wheelchair", "older", "deaf") and not signals:
        spot = ("unmarked_crossing", "amber", "Unmarked crossing without signals.")
    if not spot:
        return None
    return {"lon": n["lon"], "lat": n["lat"], "type": spot[0], "severity": spot[1], "text": spot[2], "kerb": kerb,
            "signals": signals, "osm_id": n["id"], "image_url": None}


def nearest_place(city: str, lat: float, lon: float, category: str, profile: str) -> dict | None:
    """The quickest place of a category for this profile among the 4 closest by straight line."""
    pois = _pois(city)
    if category not in pois:
        return None
    x, y = locality.to_xy(city, lat, lon)
    k = min(4, len(pois[category]["items"]))
    d, idx = pois[category]["tree"].query([float(x), float(y)], k=k)
    cands = [pois[category]["items"][int(i)] for i in np.atleast_1d(idx)]
    if len(cands) == 1:
        return cands[0]
    try:
        m = routing._valhalla("sources_to_targets", {
            "sources": [{"lat": lat, "lon": lon}], "targets": [{"lat": c["lat"], "lon": c["lon"]} for c in cands],
            "costing": "pedestrian", "costing_options": {"pedestrian": routing.PROFILE_COSTING[profile]}})
        times = [c.get("time") if c.get("time") is not None else 1e9 for c in m["sources_to_targets"][0]]
        return cands[int(np.argmin(times))]
    except Exception:  # noqa: BLE001
        return cands[0]


@lru_cache(maxsize=2)
def _pois(city: str):
    return locality.load_pois(city)


def build_route(city: str, origin: tuple[float, float], dest: tuple[float, float], profile: str,
                dest_name: str | None = None, category: str | None = None, use_cache: bool = True) -> dict:
    key = hashlib.md5(f"{city}{origin}{dest}{profile}".encode()).hexdigest()[:16]
    cache = DATA / "routes" / f"{key}.json"
    if use_cache and cache.exists():
        return json.loads(cache.read_text())
    r = routing.route(origin, dest, profile)
    leg = r["trip"]["legs"][0]
    coords = decode6(leg["shape"])
    time.sleep(0.3)
    try:
        tr = routing._valhalla("trace_attributes", {
            "encoded_polyline": leg["shape"], "costing": "pedestrian", "shape_match": "map_snap",
            "costing_options": {"pedestrian": routing.PROFILE_COSTING[profile]}})
        edges, tcoords = tr["edges"], decode6(tr["shape"])
    except Exception:  # noqa: BLE001
        edges, tcoords = [], coords
    st = _streets(city)
    in_box = lambda lon, lat: bool(st) and st["bbox"][0] <= lat <= st["bbox"][2] and st["bbox"][1] <= lon <= st["bbox"][3]  # noqa: E731

    stretches, known_m, total_m = [], 0.0, 0.0
    for e in edges:
        seg = tcoords[e["begin_shape_index"]: e["end_shape_index"] + 1]
        if len(seg) < 2:
            continue
        mid = seg[len(seg) // 2]
        near = (len(_near(st, "sidewalk", mid[0], mid[1], 14)) > 0) if in_box(*mid) else None
        status, why = _stretch_status(e, profile, near, bool(st) and e.get("way_id") in st["no_sidewalk"])
        meters = e["length"] * 1000
        total_m += meters
        known_m += meters if status != "grey" else 0
        if stretches and stretches[-1]["status"] == status and stretches[-1]["why"] == why:
            stretches[-1]["coords"].extend(seg[1:])
            stretches[-1]["meters"] = round(stretches[-1]["meters"] + meters)
        else:
            stretches.append({"coords": [list(c) for c in seg], "status": status, "why": why, "meters": round(meters)})
    if not stretches:
        stretches = [{"coords": coords, "status": "grey", "why": "Street details unavailable for this route", "meters": round(leg["summary"]["length"] * 1000)}]

    # crossings, kerbs and benches along the line
    hard_spots, crossings, kerb_known = [], [], 0
    bench_gap = None
    if st and coords and in_box(*coords[len(coords) // 2]):
        seen = set()
        dense = [coords[0]]
        for a, b in zip(coords, coords[1:]):
            n = max(1, int(math.hypot(*(np.subtract(_m(*b, st["lat0"]), _m(*a, st["lat0"])))) / 8))
            dense.extend([[a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n] for i in range(1, n + 1)])
        last_bench_m, walked, worst_gap = 0.0, 0.0, 0.0
        for a, b in zip(dense, dense[1:]):
            walked += math.hypot(*(np.subtract(_m(*b, st["lat0"]), _m(*a, st["lat0"]))))
            for n in _near(st, "crossing", b[0], b[1], 7):
                if n["id"] in seen:
                    continue
                seen.add(n["id"])
                kerbs = _near(st, "kerb", n["lon"], n["lat"], 9)
                has_kerb = bool(n["tags"].get("kerb") or kerbs)
                kerb_known += has_kerb
                crossings.append(n)
                spot = _crossing_spot(n, kerbs, profile)
                if spot:
                    spot["at_m"] = round(walked)
                    hard_spots.append(spot)
            if _near(st, "bench", b[0], b[1], 20):
                worst_gap = max(worst_gap, walked - last_bench_m)
                last_bench_m = walked
        worst_gap = max(worst_gap, walked - last_bench_m)
        bench_gap = round(worst_gap)
    for s in stretches:
        if s["status"] == "red":
            mid = s["coords"][len(s["coords"]) // 2]
            hard_spots.append({"lon": mid[0], "lat": mid[1], "type": "stretch", "severity": "red", "text": s["why"] + f" ({s['meters']} m).", "image_url": None})
    if profile == "older" and bench_gap and bench_gap > 400:
        hard_spots.append({"lon": coords[len(coords) // 2][0], "lat": coords[len(coords) // 2][1], "type": "no_bench", "severity": "amber",
                           "text": f"About {bench_gap} m without a mapped bench to rest on.", "image_url": None})

    # slope from Vexcel's measured ground heights (Valhalla's own grades are coarse and often read as flat)
    elevation = None
    try:
        from .terrain import ground_along
        elevation = ground_along(coords)
    except Exception:  # noqa: BLE001 - heights are a bonus; a route without them is still a route
        elevation = None
    if elevation:
        limit = 5.0
        g, dist = elevation["grade_pct"], elevation["dist_m"]
        i = 0
        while i < len(g):
            if abs(g[i]) > limit:
                j = i
                while j < len(g) and abs(g[j]) > limit * 0.8:
                    j += 1
                length = dist[min(j, len(dist) - 1)] - dist[i]
                rise = abs(elevation["height_m"][min(j, len(g) - 1)] - elevation["height_m"][i])
                # the two ends of a route often sit inside a building footprint, where "ground" is a roof or deck
                inner = dist[i] > 40 and dist[min(j, len(dist) - 1)] < dist[-1] - 40
                if length >= 20 and rise >= 1.2 and inner:
                    peak = max(abs(v) for v in g[i:j])
                    k = min(len(coords) - 1, int(len(coords) * (dist[i] + length / 2) / max(1, dist[-1])))
                    if peak > 15:      # streets are almost never this steep: more likely a bridge, deck or stairs seen from above
                        sev, text = "amber", f"The aerial heights show a sharp level change here (about {rise:.0f} m over {length:.0f} m): possibly a bridge, deck or stairs. Check it."
                    else:
                        sev = "red" if profile in ("wheelchair", "older") else "amber"
                        too = " (over 8.3%: too steep for most wheelchairs)" if peak > 8.3 and profile == "wheelchair" else ""
                        text = f"Slope of about {peak:.0f}% for {length:.0f} m{too}, measured from aerial heights; ADA's limit for a walkway is 5%."
                    hard_spots.append({"lon": coords[k][0], "lat": coords[k][1], "type": "slope", "severity": sev, "at_m": dist[i], "text": text, "image_url": None})
                i = j
            else:
                i += 1
    hard_spots.sort(key=lambda h: h.get("at_m", 1e9))

    seconds = leg["summary"]["time"]
    if profile == "older":
        seconds = leg["summary"]["length"] / 3.0 * 3600
    share_known = known_m / total_m if total_m else 0
    kerb_share = kerb_known / len(crossings) if crossings else None
    conf = "medium" if share_known > 0.8 and (kerb_share or 0) >= 0.5 else "low"
    if profile in ("blind", "deaf"):
        conf = "low"
    worst = max((s["status"] for s in stretches), key=ORDER.get)
    out = {
        "id": key, "city": city, "profile": profile, "category": category,
        "origin": {"lat": origin[0], "lon": origin[1]}, "destination": {"lat": dest[0], "lon": dest[1], "name": dest_name},
        "minutes": round(seconds / 60, 1), "meters": round(leg["summary"]["length"] * 1000),
        "crossings": len(crossings) if st and crossings is not None else None,
        "crossings_with_curb_data": kerb_known, "longest_gap_without_bench_m": bench_gap,
        "confidence": conf, "worst": worst,
        "stretches": stretches, "hard_spots": hard_spots,
        "steps": [{"text": m.get("instruction"), "meters": round(m["length"] * 1000)} for m in leg["maneuvers"]],
        "unknown_share": round(1 - share_known, 2),
        "elevation": elevation,
        "notes": ([] if kerb_share is None or kerb_share >= 0.99 else
                  [f"Curbs are recorded at {kerb_known} of {len(crossings)} crossings; the rest are unknown."]) +
                 ([] if st and coords and in_box(*coords[len(coords) // 2]) else ["Crossing and curb details aren't cached for this area yet."]),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def route_to_category(city: str, lat: float, lon: float, category: str, profile: str) -> dict | None:
    place = nearest_place(city, lat, lon, category, profile)
    if not place:
        return None
    return build_route(city, (lat, lon), (place["lat"], place["lon"]), profile, place.get("name") or category.title(), category)


if __name__ == "__main__":
    lat, lon, cat, prof = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4]
    r = route_to_category("houston", lat, lon, cat, prof)
    print(json.dumps({k: v for k, v in r.items() if k not in ("stretches", "steps")}, indent=1))
    for s in r["stretches"]:
        print(" ", s["status"], s["meters"], "m", s["why"])
