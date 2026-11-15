"""Locality heat map (Technical Design section 5): H3 res-9 cells -> minutes to essentials -> score per profile.

    python -m pipeline.locality houston [--live]     # --live: Valhalla matrix for the hero neighborhood
    python -m pipeline.locality la

Outside the hero neighborhood, minutes are ESTIMATES: straight-line distance x a detour factor / profile speed,
calibrated on the live cells. Every cell says which it is.

Writes data/hexes_<city>.json.
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict

import h3
import numpy as np
from scipy.spatial import cKDTree

from .clients import ROOT
from .clients import routing

DATA = ROOT / "data"
PROFILES = ("wheelchair", "blind", "deaf", "older")
# category: (good within, zero at, weight, nth nearest)
CATEGORIES = {
    "hospital": (15, 45, 0.20, 1), "clinic": (10, 30, 0.15, 1), "pharmacy": (10, 30, 0.15, 1),
    "grocery": (10, 30, 0.15, 1), "transit": (5, 15, 0.15, 1), "restaurant": (10, 30, 0.08, 3),
    "school": (15, 40, 0.07, 1), "park": (10, 30, 0.05, 1),
}
# minutes per straight-line km before calibration: 1.3 detour / speed (km/h) * 60
MIN_PER_KM = {"wheelchair": 1.3 / 4.0 * 60, "blind": 1.3 / 4.0 * 60, "deaf": 1.3 / 5.1 * 60, "older": 1.3 / 3.0 * 60}
HERO = {"houston": (29.7437, -95.3795, 1.6)}      # Midtown: lat, lon, radius km
CENTER = {"houston": (29.76, -95.37), "la": (34.05, -118.25)}
COSTING = {"wheelchair": "wheelchair", "blind": "blind", "deaf": "foot"}   # older = foot at 3 km/h


def to_xy(city: str, lat, lon):
    lat0 = CENTER[city][0]
    return np.asarray(lon) * 111.32 * math.cos(math.radians(lat0)), np.asarray(lat) * 110.57


def load_pois(city: str) -> dict:
    pois = json.loads((DATA / "osm" / f"pois_{city}.json").read_text())
    gtfs = DATA / "gtfs" / f"{city}_stops.txt"
    if gtfs.exists():
        import csv
        with open(gtfs, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    pois.append({"id": f"gtfs/{r['stop_id']}", "category": "transit", "name": r.get("stop_name"),
                                 "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"]), "source": "gtfs"})
                except (KeyError, ValueError):
                    pass
    by_cat = defaultdict(list)
    for p in pois:
        by_cat[p["category"]].append(p)
    out = {}
    for c, items in by_cat.items():
        x, y = to_xy(city, [p["lat"] for p in items], [p["lon"] for p in items])
        out[c] = {"items": items, "tree": cKDTree(np.c_[x, y])}
    return out


def score_from_minutes(minutes: dict) -> float:
    """Essentials part, 0..1: weighted sum of category scores (section 5 formula)."""
    s = 0.0
    for c, (good, zero, w, _) in CATEGORIES.items():
        t = minutes.get(c)
        if t is not None:
            s += w * min(1.0, max(0.0, (zero - t) / (zero - good)))
    return s


def nearest_km(pois: dict, city: str, lat: float, lon: float) -> dict:
    """Straight-line km to the nth nearest place per category, with that place."""
    x, y = to_xy(city, lat, lon)
    out = {}
    for c, (_, _, _, nth) in CATEGORIES.items():
        if c not in pois:
            continue
        d, i = pois[c]["tree"].query([float(x), float(y)], k=nth)
        d, i = (d[-1], i[-1]) if nth > 1 else (d, i)
        if np.isfinite(d):
            out[c] = (float(d), pois[c]["items"][int(i)])
    return out


def street_measures(city: str) -> dict:
    """Per res-9 cell (smoothed over its ring): sidewalk coverage, kerbs, signals, tactile, audible, benches."""
    path = DATA / "osm" / f"streets_{city}.json"
    if not path.exists():
        return {}
    s = json.loads(path.read_text())
    raw = defaultdict(lambda: defaultdict(float))
    for w in s["ways"]:
        t, g = w["tags"], w["geom"]
        hw = t.get("highway")
        kind = "sidewalk" if hw == "footway" and t.get("footway") == "sidewalk" else ("road" if hw not in ("footway", "steps") else None)
        if not kind or len(g) < 2:
            continue
        for (lo1, la1), (lo2, la2) in zip(g, g[1:]):
            km = math.hypot((lo2 - lo1) * 96.6, (la2 - la1) * 110.57)
            raw[h3.latlng_to_cell((la1 + la2) / 2, (lo1 + lo2) / 2, 9)][kind] += km
            if kind == "road" and t.get("sidewalk") in ("both", "left", "right", "yes"):
                raw[h3.latlng_to_cell((la1 + la2) / 2, (lo1 + lo2) / 2, 9)]["sidewalk"] += km * (2 if t["sidewalk"] == "both" else 1)
    for n in s["nodes"]:
        t = n["tags"]
        c = raw[h3.latlng_to_cell(n["lat"], n["lon"], 9)]
        if t.get("highway") == "crossing":
            c["crossings"] += 1
            if t.get("crossing") == "traffic_signals" or t.get("crossing:signals") == "yes":
                c["signalized"] += 1
            if t.get("crossing:markings", "no") != "no" or t.get("crossing") in ("marked", "zebra", "traffic_signals"):
                c["marked"] += 1
        if "kerb" in t:
            c["kerb_tagged"] += 1
            c["kerb_ok"] += t["kerb"] in ("lowered", "flush")
        if "tactile_paving" in t:
            c["tactile_tagged"] += 1
            c["tactile_yes"] += t["tactile_paving"] == "yes"
        if "traffic_signals:sound" in t:
            c["sound_tagged"] += 1
            c["sound_yes"] += t["traffic_signals:sound"] == "yes"
        if t.get("amenity") == "bench":
            c["benches"] += 1
    out = {}
    for cell in list(raw):
        agg = defaultdict(float)
        for nb in h3.grid_disk(cell, 1):
            for k, v in raw.get(nb, {}).items():
                agg[k] += v
        road = agg["road"]
        if road < 0.3:
            continue
        frac = lambda a, b, n=3: (agg[a] / agg[b]) if agg[b] >= n else None  # noqa: E731
        out[cell] = {
            "sidewalk_coverage": round(min(1.0, agg["sidewalk"] / (1.5 * road)), 2),
            "lowered_kerb_share": frac("kerb_ok", "kerb_tagged"), "kerbs_tagged": int(agg["kerb_tagged"]),
            "signalized_share": frac("signalized", "crossings"), "marked_share": frac("marked", "crossings"),
            "tactile_share": frac("tactile_yes", "tactile_tagged"), "audible_share": frac("sound_yes", "sound_tagged"),
            "benches_per_km": round(agg["benches"] / road, 1), "crossings": int(agg["crossings"]),
        }
        for k, v in out[cell].items():
            if isinstance(v, float):
                out[cell][k] = round(v, 2)
    return out


def street_score(m: dict | None) -> dict | None:
    if not m:
        return None
    g = lambda k, d: m[k] if m.get(k) is not None else d  # noqa: E731
    sw = m["sidewalk_coverage"]
    return {
        "wheelchair": 0.5 * sw + 0.3 * g("lowered_kerb_share", 0.5) + 0.2 * g("marked_share", 0.4),
        "blind": 0.3 * sw + 0.3 * g("signalized_share", 0.3) + 0.2 * g("tactile_share", 0.3) + 0.2 * g("audible_share", 0.2),
        "deaf": 0.5 * sw + 0.5 * g("signalized_share", 0.3),
        "older": 0.5 * sw + 0.25 * min(1.0, m["benches_per_km"] / 4) + 0.25 * g("marked_share", 0.4),
    }


def live_minutes(city: str, lat: float, lon: float, near: dict, pois: dict, cache_key: str | None = None) -> dict | None:
    """Valhalla walking minutes from a point to the closest candidates of each category, per profile."""
    cache = DATA / "valhalla" / f"{cache_key}.json" if cache_key else None
    if cache and cache.exists():
        return json.loads(cache.read_text())
    x, y = to_xy(city, lat, lon)
    targets, cats = [], []
    for c, (_, _, _, nth) in CATEGORIES.items():
        if c not in pois:
            continue
        k = min(len(pois[c]["items"]), nth + 2)
        d, idx = pois[c]["tree"].query([float(x), float(y)], k=k)
        for dist, i in zip(np.atleast_1d(d), np.atleast_1d(idx)):
            if dist < 4.0:
                p = pois[c]["items"][int(i)]
                targets.append({"lat": p["lat"], "lon": p["lon"]})
                cats.append(c)
    if not targets:
        return None
    out = {}
    for prof, costing in COSTING.items():
        try:
            m = routing._valhalla("sources_to_targets", {
                "sources": [{"lat": lat, "lon": lon}], "targets": targets, "costing": "pedestrian",
                "costing_options": {"pedestrian": {"type": costing}}})
        except Exception as e:  # noqa: BLE001
            print("valhalla failed", prof, e, flush=True)
            return None
        times = defaultdict(list)
        for cell, c in zip(m["sources_to_targets"][0], cats):
            if cell.get("time") is not None:
                times[c].append(cell["time"] / 60)
        out[prof] = {c: round(sorted(v)[min(len(v), CATEGORIES[c][3]) - 1], 1) for c, v in times.items()}
        time.sleep(0.6)
    out["older"] = {c: round(v * 5.1 / 3.0, 1) for c, v in out["deaf"].items()}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out))
    return out


def build(city: str, live: bool = False) -> None:
    pois = load_pois(city)
    print({c: len(v["items"]) for c, v in pois.items()}, flush=True)
    rows = [b for b in json.loads((DATA / "buildings.json").read_text()) if b["city"] == city]
    cells = set()
    for b in rows:
        cells.update(h3.grid_disk(b["h3"], 5))       # about 1.5 km around every scanned building
    streets = street_measures(city)
    print(len(cells), "cells;", len(streets), "with street measures", flush=True)
    hero = HERO.get(city)
    out, calib = [], defaultdict(list)
    cells = sorted(cells)
    n_live = 0
    for cell in cells:
        lat, lon = h3.cell_to_latlng(cell)
        near = nearest_km(pois, city, lat, lon)
        rec = {"h3": cell, "km": {c: round(d, 2) for c, (d, _) in near.items()}, "live": False}
        if live and hero and math.hypot((lat - hero[0]) * 110.57, (lon - hero[1]) * 96.6) <= hero[2]:
            lm = live_minutes(city, lat, lon, near, pois, cache_key=f"hex_{cell}")
            if lm:
                rec["minutes"], rec["live"] = lm, True
                n_live += 1
                for p in PROFILES:
                    for c, t in lm[p].items():
                        if c in near and near[c][0] > 0.15:
                            calib[p].append(t / near[c][0])
                if n_live % 20 == 0:
                    print("live cells", n_live, flush=True)
        out.append(rec)
    factors = {p: (float(np.median(calib[p])) if len(calib[p]) >= 30 else MIN_PER_KM[p]) for p in PROFILES}
    print("minutes per straight-line km:", {p: round(v, 1) for p, v in factors.items()}, "(defaults", MIN_PER_KM, ")", flush=True)
    known_e = [street_score(m) for m in streets.values()]
    e_default = {p: float(np.median([e[p] for e in known_e])) if known_e else 0.5 for p in PROFILES}
    for rec in out:
        if not rec["live"]:
            rec["minutes"] = {p: {c: round(d * factors[p], 1) for c, d in rec["km"].items()} for p in PROFILES}
        m = streets.get(rec["h3"])
        e = street_score(m)
        rec["streets"] = m
        rec["scores"] = {}
        for p in PROFILES:
            ess = score_from_minutes(rec["minutes"][p])
            val = 100 * (0.7 * ess + 0.3 * (e[p] if e else e_default[p]))
            if p in ("wheelchair", "older"):
                conf = "medium" if rec["live"] and e else "low"
                if p == "wheelchair" and m and m["kerbs_tagged"] < 3:
                    conf = "low"
            else:
                conf = "low"
            rec["scores"][p] = {"score": round(val), "confidence": conf}
        del rec["km"]
    (DATA / f"hexes_{city}.json").write_text(json.dumps({
        "city": city, "res": 9, "minutes_per_km": factors, "street_default": e_default,
        "note": "live = Valhalla walking times; otherwise estimated from straight-line distance", "cells": out}))
    sc = [r["scores"]["wheelchair"]["score"] for r in out]
    print(f"{len(out)} cells, {n_live} live; wheelchair score median {np.median(sc):.0f}, p10 {np.percentile(sc, 10):.0f}, p90 {np.percentile(sc, 90):.0f}")


if __name__ == "__main__":
    build(sys.argv[1], "--live" in sys.argv)
