"""Cache OpenStreetMap data once (Overpass is flaky): places for both cities, street features for central Houston.

    python -m pipeline.osm pois houston|la|princeton
    python -m pipeline.osm streets [houston|princeton]   # crossings, kerbs, signals, benches, sidewalks, roads

Outputs: data/osm/pois_<city>.json, data/osm/streets_<city>.json
"""
from __future__ import annotations

import json
import sys

from .clients import ROOT
from .clients.routing import CATEGORY_FILTERS, _matches, overpass

OUT = ROOT / "data" / "osm"
BBOX = {  # south, west, north, east
    "houston": (29.45, -95.85, 30.20, -95.00),
    "la": (33.60, -118.75, 34.40, -117.60),
    "princeton": (40.3157, -74.6943, 40.3714, -74.5981),   # campus and town center: Vexcel's box for our key
}
CENTRAL = (29.675, -95.48, 29.83, -95.31)     # inside the loop: Midtown, Downtown, Montrose, Medical Center, Heights
STREET_BOX = {"houston": CENTRAL, "princeton": BBOX["princeton"]}


def pois(city: str) -> None:
    s, w, n, e = BBOX[city]
    out, seen = [], set()
    # split the box in 4 so each query stays small
    ms, mw = (s + n) / 2, (w + e) / 2
    for box in ((s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)):
        b = ",".join(str(v) for v in box)
        parts = [f"nwr{f}({b});" for fs in CATEGORY_FILTERS.values() for f in fs]
        data = overpass(f"[out:json][timeout:170];({''.join(parts)});out tags center;")
        for el in data["elements"]:
            t = el.get("tags", {})
            cat = next((c for c, fs in CATEGORY_FILTERS.items() if _matches(t, fs)), None)
            la = el.get("lat") or (el.get("center") or {}).get("lat")
            lo = el.get("lon") or (el.get("center") or {}).get("lon")
            key = f"{el['type']}/{el['id']}"
            if cat and la and key not in seen:
                seen.add(key)
                out.append({"id": key, "category": cat, "name": t.get("name"), "lat": la, "lon": lo,
                            "wheelchair": t.get("wheelchair")})
        print(city, box, len(out), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"pois_{city}.json").write_text(json.dumps(out))


def streets(city: str = "houston") -> None:
    box = STREET_BOX[city]
    b = ",".join(str(v) for v in box)
    nodes = overpass(f"""[out:json][timeout:170];(
        node[highway=crossing]({b}); node[kerb]({b}); node[highway=traffic_signals]({b});
        node[amenity=bench]({b}); node[tactile_paving]({b});
    );out;""")["elements"]
    print("nodes", len(nodes), flush=True)
    ways = overpass(f"""[out:json][timeout:170];(
        way[highway=footway]({b}); way[highway~"^(residential|tertiary|secondary|primary|unclassified|living_street)$"]({b});
        way[highway=steps]({b});
    );out tags geom;""")["elements"]
    print("ways", len(ways), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"streets_{city}.json").write_text(json.dumps({
        "bbox": box,
        "nodes": [{"id": n["id"], "lat": n["lat"], "lon": n["lon"], "tags": n.get("tags", {})} for n in nodes],
        "ways": [{"id": w["id"], "tags": w.get("tags", {}), "geom": [[p["lon"], p["lat"]] for p in w.get("geometry", [])]} for w in ways],
    }))


if __name__ == "__main__":
    if sys.argv[1] == "pois":
        pois(sys.argv[2])
    else:
        streets(sys.argv[2] if len(sys.argv) > 2 else "houston")
