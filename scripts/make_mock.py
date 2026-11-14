"""Generate SYNTHETIC sample data for frontend development.

Nothing here comes from the licensed CoStar / Matterport / Vexcel data: names,
scores, floor plans and routes are invented. Coordinates are real places in
Midtown Houston only so the map looks right. Every JSON file follows
docs/API_CONTRACT.md, so the frontend can switch from /mock to the real API
without code changes.

Run:  python scripts/make_mock.py      (writes frontend/public/mock/*.json)
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import h3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.floorplan import heat_grid, paint_doors  # same heat logic as real units  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "frontend" / "public" / "mock"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(42)

PROFILES = ["wheelchair", "blind", "deaf", "older"]
CENTER = (29.7437, -95.3795)  # Midtown Houston (lat, lon)


def conf(score: float) -> str:
    return "high" if score > 75 else ("medium" if score > 45 else "low")


def profile_scores(base: float) -> dict:
    out = {}
    for p in PROFILES:
        spread = {"wheelchair": 0, "blind": -15, "deaf": -5, "older": 5}[p]
        s = max(5, min(98, base + spread + random.uniform(-12, 12)))
        out[p] = {"score": round(s), "confidence": "low" if p in ("blind", "deaf") else conf(s)}
    return out


def write(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, indent=1))
    print("wrote", name)


# ---------------------------------------------------------------- buildings
buildings = []
for i in range(40):
    lat = CENTER[0] + random.uniform(-0.012, 0.012)
    lon = CENTER[1] + random.uniform(-0.014, 0.014)
    walkup = random.random() < 0.35
    base = random.uniform(30, 92) - (25 if walkup else 0)
    kind = "apartment_community" if i < 32 else "home"
    buildings.append({
        "id": f"b{i:03d}",
        "kind": kind,
        "name": f"Sample {'Lofts' if kind == 'apartment_community' else 'House'} {i + 1}",
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "stories": 1 if kind == "home" else random.choice([2, 3, 4, 6, 12]),
        "elevator": (not walkup) and kind != "home",
        "walkup": walkup if kind != "home" else None,
        "year_built": random.choice([1972, 1985, 1998, 2008, 2016, 2021]),
        "rent_min": random.choice([1100, 1350, 1600, 1900]),
        "rent_max": random.choice([2100, 2500, 3200]),
        "units_scanned": random.randint(1, 6) if kind != "home" else 1,
        "scores": profile_scores(base),
    })
write("buildings.json", {"city": "houston", "synthetic": True, "buildings": buildings})

# ---------------------------------------------------------------- hex cells
cells = h3.grid_disk(h3.latlng_to_cell(CENTER[0], CENTER[1], 9), 7)
hexes = []
for c in cells:
    clat, clon = h3.cell_to_latlng(c)
    d = math.dist((clat, clon), CENTER) * 111  # km, rough
    base = 88 - d * 22 + random.uniform(-8, 8)
    minutes = {}
    for p in PROFILES:
        slow = {"wheelchair": 1.3, "blind": 1.2, "deaf": 1.0, "older": 1.6}[p]
        minutes[p] = {k: round((v + d * 6) * slow, 1) for k, v in {
            "hospital": 9, "clinic": 5, "pharmacy": 4, "grocery": 6, "transit": 2,
            "restaurant": 3, "school": 7, "park": 5}.items()}
    hexes.append({"h3": c, "scores": profile_scores(base), "minutes": minutes})
write("hexes.json", {"city": "houston", "res": 9, "synthetic": True, "cells": hexes})

# ---------------------------------------------------------------- one apartment (meters)
# A made-up 2-bed unit, 8.6 x 7.2 m. Walls are segments [x1, y1, x2, y2].
W, H = 8.6, 7.2
rooms = [
    {"id": "r1", "type": "Living Room", "polygon": [[0, 0], [5, 0], [5, 3.6], [0, 3.6]]},
    {"id": "r2", "type": "Kitchen", "polygon": [[5, 0], [8.6, 0], [8.6, 3.0], [5, 3.0]]},
    {"id": "r3", "type": "Bedroom", "polygon": [[0, 3.6], [3.6, 3.6], [3.6, 7.2], [0, 7.2]]},
    {"id": "r4", "type": "Bathroom", "polygon": [[3.6, 4.8], [5.4, 4.8], [5.4, 7.2], [3.6, 7.2]]},
    {"id": "r5", "type": "Hallway", "polygon": [[3.6, 3.6], [5.4, 3.6], [5.4, 4.8], [3.6, 4.8]]},
    {"id": "r6", "type": "Bedroom", "polygon": [[5.4, 3.0], [8.6, 3.0], [8.6, 7.2], [5.4, 7.2]]},
]
doors = [  # x, y = door center; axis = wall direction
    {"id": "d_entry", "x": 2.5, "y": 0.0, "axis": "x", "width_m": 0.91, "room_a": "outside", "room_b": "r1"},
    {"id": "d_hall", "x": 4.5, "y": 3.6, "axis": "x", "width_m": 0.86, "room_a": "r1", "room_b": "r5"},
    {"id": "d_bed1", "x": 3.6, "y": 4.2, "axis": "y", "width_m": 0.81, "room_a": "r5", "room_b": "r3"},
    {"id": "d_bath", "x": 4.5, "y": 4.8, "axis": "x", "width_m": 0.71, "room_a": "r5", "room_b": "r4"},
    {"id": "d_bed2", "x": 5.4, "y": 4.2, "axis": "y", "width_m": 0.81, "room_a": "r5", "room_b": "r6"},
]
for d in doors:
    clear_in = (d["width_m"] - 0.05) / 0.0254
    d["kind"] = "doorway"
    d["angle_deg"] = 0 if d["axis"] == "x" else 90
    d["threshold_m"] = 0.0
    d["est_clear_in"] = round(clear_in, 1)
    d["status"] = "pass" if clear_in >= 32 else ("tight" if clear_in >= 30 else "fail")

raw_walls = [  # outer box + interior partitions (before door gaps)
    (0, 0, W, 0), (W, 0, W, H), (W, H, 0, H), (0, H, 0, 0),
    (0, 3.6, 5.0, 3.6), (5.0, 0, 5.0, 3.6), (5.0, 3.0, W, 3.0),
    (3.6, 3.6, 3.6, H), (5.4, 3.0, 5.4, H), (3.6, 4.8, 5.4, 4.8),
]


def cut_doors(seg):
    x1, y1, x2, y2 = seg
    pieces = [seg]
    for d in doors:
        out = []
        for (a, b, c, e) in pieces:
            horiz = abs(b - e) < 1e-9
            if horiz and d["axis"] == "x" and abs(b - d["y"]) < 1e-6 and min(a, c) < d["x"] < max(a, c):
                lo, hi = sorted((a, c))
                out += [(lo, b, d["x"] - d["width_m"] / 2, b), (d["x"] + d["width_m"] / 2, b, hi, b)]
            elif (not horiz) and d["axis"] == "y" and abs(a - d["x"]) < 1e-6 and min(b, e) < d["y"] < max(b, e):
                lo, hi = sorted((b, e))
                out += [(a, lo, a, d["y"] - d["width_m"] / 2), (a, d["y"] + d["width_m"] / 2, a, hi)]
            else:
                out.append((a, b, c, e))
        pieces = out
    return pieces


walls = [list(map(lambda v: round(v, 3), p)) for s in raw_walls for p in cut_doors(s)]


furniture = [  # x, y, w, d in meters (bottom-left corner)
    {"id": "f1", "label": "sofa", "rect": [0.2, 2.6, 2.2, 0.9], "room": "r1"},
    {"id": "f2", "label": "coffee table", "rect": [0.8, 1.6, 1.0, 0.5], "room": "r1"},
    {"id": "f3", "label": "queen bed", "rect": [0.3, 5.0, 1.52, 2.03], "room": "r3"},
    {"id": "f4", "label": "dresser", "rect": [2.4, 6.6, 1.0, 0.5], "room": "r3"},
    {"id": "f5", "label": "dining table", "rect": [5.6, 1.2, 1.2, 0.8], "room": "r2"},
    {"id": "f6", "label": "desk", "rect": [7.3, 6.5, 1.2, 0.6], "room": "r6"},
]


def unit_heat(with_furniture: bool) -> dict:
    """0 outside, 1 blocked, 2 red, 3 amber, 4 green (see docs/API_CONTRACT.md)."""
    obstacles = [f["rect"] for f in furniture] if with_furniture else None
    return paint_doors(heat_grid(walls, rooms, [0, 0, W, H], 0.1, obstacles), doors)


unit = {
    "model_id": "u_sample_001",
    "building_id": "b000",
    "caption": "2BR, 1BA - 667SF (synthetic)",
    "units": "meters",
    "bounds": [0, 0, W, H],
    "walls": walls,
    "doors": doors,
    "windows": [{"x": 0.0, "y": 1.8, "axis": "y", "width_m": 1.2}, {"x": 8.6, "y": 5.0, "axis": "y", "width_m": 1.2}],
    "rooms": rooms,
    "furniture": furniture,
    "turning_circles": [{"x": 2.6, "y": 1.2, "r": 0.76, "room": "r1"}, {"x": 7.0, "y": 4.4, "r": 0.76, "room": "r6"}],
    "problems": [
        {"id": "p1", "severity": "high", "profile": "wheelchair", "x": 4.5, "y": 4.8,
         "text": "Bathroom door about 26 in clear; a wheelchair needs 32 in."},
        {"id": "p2", "severity": "high", "profile": "wheelchair", "x": 4.5, "y": 6.0,
         "text": "No 60 in turning circle fits in the bathroom."},
        {"id": "p3", "severity": "medium", "profile": "older", "x": 2.0, "y": 4.2,
         "text": "Bed to bathroom is 6.5 m with one turn; add a night light on the route."},
        {"id": "p4", "severity": "low", "profile": "deaf", "x": 2.5, "y": 0.3,
         "text": "Entry door is visible from the sofa: good for seeing visitors."},
    ],
    "scores": {"empty": profile_scores(70), "furnished": profile_scores(55)},
    "heat": {"wheelchair": {"empty": unit_heat(False), "furnished": unit_heat(True)}},
}
write("unit_u_sample_001.json", unit)

# ---------------------------------------------------------------- route
pts = [(CENTER[1] + k * 0.0009, CENTER[0] + k * 0.0011) for k in range(9)]
route = {
    "origin": {"building_id": "b000", "lat": pts[0][1], "lon": pts[0][0]},
    "destination": {"category": "pharmacy", "name": "Sample Pharmacy", "lat": pts[-1][1], "lon": pts[-1][0]},
    "profile": "wheelchair",
    "minutes": 19.8, "meters": 1310, "crossings": 6, "confidence": "medium",
    "stretches": [
        {"coords": [list(p) for p in pts[0:4]], "status": "green", "why": "Sidewalk, gentle slope"},
        {"coords": [list(p) for p in pts[3:5]], "status": "red", "why": "No sidewalk mapped; route runs on the road"},
        {"coords": [list(p) for p in pts[4:9]], "status": "amber", "why": "Sidewalk, but curbs not mapped"},
    ],
    "hard_spots": [{"lon": pts[4][0], "lat": pts[4][1], "type": "no_lowered_curb",
                    "text": "Crossing without a mapped lowered curb", "image_url": None}],
}
write("route_b000_pharmacy_wheelchair.json", route)

# ---------------------------------------------------------------- furnishing
write("furnish_score_u_sample_001.json", {
    "model_id": "u_sample_001", "profiles": ["wheelchair"],
    "score_empty": 70, "score_furnished": 55,
    "suggestions": [
        {"item_id": "f2", "action": "remove", "gain": 9, "text": "Remove the coffee table to open a 36 in path from the entry to the hallway."},
        {"item_id": "f4", "action": "move", "to": [0.3, 3.8], "gain": 6, "text": "Move the dresser to the wall beside the bedroom door to free the transfer side of the bed."},
        {"item_id": "f5", "action": "replace", "gain": 4, "text": "Swap the dining table for a 0.9 m round table to keep a turning circle in the kitchen."},
    ],
})
write("furnish_plan_u_sample_001.json", {
    "target_model_id": "u_sample_001", "profiles": ["wheelchair"], "score_before": 55, "score_after": 86,
    "placements": [
        {"item_id": "f1", "label": "sofa", "room": "r1", "rect": [0.2, 2.6, 2.2, 0.9], "rotation_deg": 0},
        {"item_id": "f3", "label": "queen bed", "room": "r3", "rect": [1.9, 5.0, 1.52, 2.03], "rotation_deg": 0},
        {"item_id": "f6", "label": "desk", "room": "r6", "rect": [7.3, 6.5, 1.2, 0.6], "rotation_deg": 0},
    ],
    "wont_fit": [{"label": "piano", "reason": "No wall in the living room is long enough without blocking the 36 in path."}],
    "reasons": ["The bed sits on the east wall so you have 1.2 m on your transfer side and a straight path to the bathroom."],
})

# ---------------------------------------------------------------- relocation
write("relocate_sample.json", {
    "origin": {"label": "Sample address, Los Angeles", "lat": 34.0522, "lon": -118.2437},
    "profiles": ["wheelchair"],
    "candidates": [
        {"h3": hexes[0]["h3"], "name": "Midtown (sample)", "match": 88,
         "comparison": {"pharmacy": [8, 6], "hospital": [12, 19], "transit": [4, 3], "grocery": [9, 7]},
         "homes": ["b000", "b003", "b007"], "reason": "Pharmacy and grocery are closer than at home; the hospital is 7 minutes further."},
        {"h3": hexes[5]["h3"], "name": "Museum District (sample)", "match": 81,
         "comparison": {"pharmacy": [8, 9], "hospital": [12, 10], "transit": [4, 5], "grocery": [9, 11]},
         "homes": ["b011", "b014"], "reason": "Hospital is closer; groceries are 2 minutes further."},
        {"h3": hexes[12]["h3"], "name": "Montrose (sample)", "match": 74,
         "comparison": {"pharmacy": [8, 10], "hospital": [12, 21], "transit": [4, 6], "grocery": [9, 8]},
         "homes": ["b020"], "reason": "Similar streets, fewer accessible homes."},
    ],
})

# ---------------------------------------------------------------- agent
write("agent_reply_sample.json", {
    "text": "These three homes in Midtown meet your wheelchair checks and are within 10 minutes of a pharmacy.",
    "ui_actions": [
        {"type": "set_map_view", "center": [CENTER[1], CENTER[0]], "zoom": 15, "profiles": ["wheelchair"]},
        {"type": "highlight_buildings", "ids": ["b000", "b003", "b007"]},
    ],
})
print("done ->", OUT)
