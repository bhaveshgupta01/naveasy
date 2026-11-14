"""Matterport wall graph -> NavEasy unit JSON (same shape as frontend/public/mock/unit_*.json).

    python -m pipeline.floorplan <model_id>   # writes data/units/<model_id>.json + .png

What it does (Technical Design, section 7):
  1. Pull floors, walls, openings and rooms from the Model API (meters).
  2. Cut doorway gaps into the walls; estimate ADA clear width = scan width - 5 cm
     (scan widths look like rough openings; to be confirmed with Matterport).
  3. Rebuild each room's polygon from its boundary edges.
  4. Compute the wheelchair heat grid: 2 x distance-to-nearest-obstacle = local clear width.
     4 green >= 0.915 m (36 in), 3 amber >= 0.815 m (32 in), 2 red, 1 blocked, 0 outside.
Furniture, fixed fixtures and reachability (flood fill from the entry) are TODO.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPoint, Point, Polygon

from .clients import ROOT, matterport

CLEAR_DEDUCTION_M = 0.05
GREEN_M, AMBER_M = 0.915, 0.815


def _ring(edge_ids: list[str], edges: dict, verts: dict) -> list[list[float]]:
    """Chain boundary edges into an ordered polygon ring; fall back to a convex hull."""
    pairs = [tuple(v["id"] for v in edges[e]["vertices"]) for e in edge_ids if e in edges]
    if not pairs:
        return []
    adj: dict[str, list[str]] = {}
    for a, b in pairs:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    start = pairs[0][0]
    ring, prev, cur = [start], None, start
    for _ in range(len(pairs) + 1):
        nxt = [n for n in adj.get(cur, []) if n != prev]
        if not nxt or nxt[0] == start:
            break
        prev, cur = cur, nxt[0]
        ring.append(cur)
    pts = [list(verts[v]) for v in ring if v in verts]
    if len(pts) < 3 or len(ring) < len(pairs):
        hull = MultiPoint([verts[v] for p in pairs for v in p if v in verts]).convex_hull
        pts = [list(c) for c in hull.exterior.coords[:-1]] if hull.geom_type == "Polygon" else pts
    return [[round(x, 3), round(y, 3)] for x, y in pts]


def build_unit(model_id: str, floor_index: int = 0, cell: float = 0.1) -> dict:
    m = matterport.geometry(model_id)
    floor = sorted(m["floors"], key=lambda f: f.get("sequence") or 0)[floor_index]
    verts = {v["id"]: (v["position"]["x"], v["position"]["y"]) for v in floor["vertices"]}
    edges = {e["id"]: e for e in floor["edges"]}

    walls, doors, windows = [], [], []
    for e in floor["edges"]:
        a, b = (verts[v["id"]] for v in e["vertices"])
        length = math.dist(a, b) or 1e-9
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        lerp = lambda u: (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
        cuts = []
        for i, o in enumerate(e.get("openings") or []):
            c, half = o["relativeCenter"], o["width"] / length / 2
            cuts.append((max(0, c - half), min(1, c + half)))
            x, y = lerp(c)
            item = {"id": f"{e['id']}_{i}", "x": round(x, 3), "y": round(y, 3), "angle_deg": round(angle, 1),
                    "width_m": round(o["width"], 3), "threshold_m": round(o.get("lowerElevation") or 0, 3)}
            if o["type"] == "window":
                windows.append(item)
            elif o["type"] in ("doorway", "opening"):
                clear_in = (o["width"] - CLEAR_DEDUCTION_M) / 0.0254
                item.update(kind=o["type"], est_clear_in=round(clear_in, 1),
                            status="pass" if clear_in >= 32 else ("tight" if clear_in >= 30 else "fail"))
                doors.append(item)
        if e["type"] != "wall":
            continue  # invisible edges separate rooms but don't block
        t = 0.0
        for c0, c1 in sorted(cuts) + [(1.0, 1.0)]:
            if c0 > t:
                walls.append([round(v, 3) for v in (*lerp(t), *lerp(c0))])
            t = max(t, c1)

    rooms = []
    for r in floor["rooms"]:
        label = ((r.get("classifications") or [{}])[0] or {}).get("label") or r.get("label") or "Other"
        if label == "Void":
            continue
        poly = _ring([e["id"] for e in ((r.get("boundary") or {}).get("edges") or [])], edges, verts)
        area = round(Polygon(poly).area, 2) if len(poly) >= 3 else None
        rooms.append({"id": r["id"], "type": label, "polygon": poly, "area_m2": area})

    xs = [p[0] for p in verts.values()]
    ys = [p[1] for p in verts.values()]
    bounds = [min(xs), min(ys), max(xs), max(ys)]
    return {
        "model_id": model_id, "caption": m.get("name"), "units": "meters", "bounds": [round(v, 3) for v in bounds],
        "geocoordinates": m.get("geocoordinates"), "walls": walls, "doors": doors, "windows": windows,
        "rooms": rooms, "furniture": [], "turning_circles": [], "problems": _problems(doors),
        "heat": {"wheelchair": {"empty": paint_doors(heat_grid(walls, rooms, bounds, cell), doors)}},
    }


def _problems(doors: list[dict]) -> list[dict]:
    out = []
    for d in doors:
        if d["kind"] == "doorway" and d["status"] != "pass":
            out.append({"id": f"door_{d['id']}", "severity": "high" if d["status"] == "fail" else "medium",
                        "profile": "wheelchair", "x": d["x"], "y": d["y"],
                        "text": f"Doorway about {d['est_clear_in']:.0f} in clear; a wheelchair needs 32 in."})
        if d.get("threshold_m", 0) > 0.013:
            out.append({"id": f"thr_{d['id']}", "severity": "medium", "profile": "wheelchair", "x": d["x"],
                        "y": d["y"], "text": f"Threshold about {d['threshold_m'] * 100:.1f} cm; over 1.3 cm needs a ramp."})
    return out


def paint_doors(grid: dict, doors: list[dict]) -> dict:
    """Paint failing doorways red (2) and tight ones amber (3) so narrow doors show on the map."""
    c, (ox, oy) = grid["cell_m"], grid["origin"]
    vals = np.array(grid["values"])
    ny, nx = vals.shape
    gx, gy = np.meshgrid(ox + (np.arange(nx) + 0.5) * c, oy + (np.arange(ny) + 0.5) * c)
    for d in doors:
        if d.get("kind", "doorway") != "doorway" or d["status"] == "pass":
            continue
        a = math.radians(d.get("angle_deg", 0))
        along = (gx - d["x"]) * math.cos(a) + (gy - d["y"]) * math.sin(a)
        across = -(gx - d["x"]) * math.sin(a) + (gy - d["y"]) * math.cos(a)
        gap = (np.abs(along) <= d["width_m"] / 2) & (np.abs(across) <= 0.15) & (vals >= 2)
        vals[gap] = 2 if d["status"] == "fail" else 3
    return {**grid, "values": vals.tolist()}


def heat_grid(walls: list, rooms: list, bounds: list, cell: float, obstacles: list | None = None) -> dict:
    """obstacles: furniture or fixtures as axis-aligned rectangles [x, y, w, d] in meters."""
    x0, y0, x1, y1 = bounds
    nx, ny = max(1, round((x1 - x0) / cell)), max(1, round((y1 - y0) / cell))
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * cell, y0 + (np.arange(ny) + 0.5) * cell)
    px, py = gx.ravel(), gy.ravel()
    dmin = np.full(px.shape, np.inf)
    for x_1, y_1, x_2, y_2 in walls:
        dx, dy = x_2 - x_1, y_2 - y_1
        L = dx * dx + dy * dy or 1e-12
        t = np.clip(((px - x_1) * dx + (py - y_1) * dy) / L, 0, 1)
        dmin = np.minimum(dmin, np.hypot(px - (x_1 + t * dx), py - (y_1 + t * dy)))
    for ox, oy, ow, od in obstacles or []:
        ddx = np.maximum.reduce([ox - px, np.zeros_like(px), px - (ox + ow)])
        ddy = np.maximum.reduce([oy - py, np.zeros_like(py), py - (oy + od)])
        dmin = np.minimum(dmin, np.hypot(ddx, ddy))
    inside = np.zeros(px.shape, bool)
    for r in rooms:
        if len(r["polygon"]) >= 3:
            poly = Polygon(r["polygon"])
            inside |= np.fromiter((poly.contains(Point(a, b)) for a, b in zip(px, py)), bool, px.size)
    d = dmin.reshape(ny, nx)
    ok36 = sweep(d >= GREEN_M / 2, GREEN_M / 2, cell)  # floor a 36 in wide chair can cover
    ok32 = sweep(d >= AMBER_M / 2, AMBER_M / 2, cell)  # floor a 32 in wide chair can cover
    vals = np.where(ok36, 4, np.where(ok32, 3, 2))
    vals = np.where(d < 0.06, 1, vals)
    vals = np.where(inside.reshape(ny, nx), vals, 0)
    return {"cell_m": cell, "origin": [round(x0, 3), round(y0, 3)], "width": nx, "height": ny,
            "values": vals.tolist()}


def sweep(centers: np.ndarray, radius_m: float, cell: float) -> np.ndarray:
    """Cells covered by a disk of radius_m centered on any allowed center (morphological dilation).

    A cell is reachable by a chair of that half-width if the chair's center can sit within
    radius_m of it. This avoids painting every wall edge red.
    """
    r = int(math.ceil(radius_m / cell))
    out = np.zeros_like(centers)
    ny, nx = centers.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if (dx * dx + dy * dy) * cell * cell > radius_m * radius_m:
                continue
            ys, yd = slice(max(0, -dy), ny - max(0, dy)), slice(max(0, dy), ny - max(0, -dy))
            xs, xd = slice(max(0, -dx), nx - max(0, dx)), slice(max(0, dx), nx - max(0, -dx))
            out[yd, xd] |= centers[ys, xs]
    return out


def render_png(unit: dict, path: Path, px_per_m: int = 60) -> None:
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = unit["bounds"]
    W, H = int((x1 - x0) * px_per_m) + 40, int((y1 - y0) * px_per_m) + 40
    T = lambda x, y: (20 + (x - x0) * px_per_m, H - 20 - (y - y0) * px_per_m)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    g = unit["heat"]["wheelchair"]["empty"]
    colors = {2: (240, 120, 120), 3: (245, 200, 110), 4: (150, 215, 150)}
    c = g["cell_m"]
    for j, row in enumerate(g["values"]):
        for i, v in enumerate(row):
            if v in colors:
                ax, ay = T(g["origin"][0] + i * c, g["origin"][1] + (j + 1) * c)
                d.rectangle([ax, ay, ax + c * px_per_m, ay + c * px_per_m], fill=colors[v])
    for w in unit["walls"]:
        d.line([T(w[0], w[1]), T(w[2], w[3])], fill="black", width=3)
    for door in unit["doors"]:
        d.text(T(door["x"], door["y"]), f"{door['est_clear_in']:.0f}in", fill=(0, 0, 160))
    for r in unit["rooms"]:
        if r["polygon"]:
            cx = sum(p[0] for p in r["polygon"]) / len(r["polygon"])
            cy = sum(p[1] for p in r["polygon"]) / len(r["polygon"])
            d.text(T(cx, cy), r["type"], fill=(60, 60, 60))
    im.save(path)


if __name__ == "__main__":
    model_id = sys.argv[1]
    out_dir = ROOT / "data" / "units"
    out_dir.mkdir(parents=True, exist_ok=True)
    unit = build_unit(model_id)
    (out_dir / f"{model_id}.json").write_text(json.dumps(unit))
    render_png(unit, out_dir / f"{model_id}.png")
    print(f"{unit['caption']}: {len(unit['walls'])} wall segments, {len(unit['doors'])} doors, "
          f"{len(unit['rooms'])} rooms, {len(unit['problems'])} problems -> {out_dir}")
