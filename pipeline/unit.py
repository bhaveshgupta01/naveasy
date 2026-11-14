"""The apartment engine: Matterport wall graph -> 5 cm grid -> per-profile layers, checks and scores.

Technical Design section 7. Everything is in meters, y pointing up, row 0 of every grid = lowest y.

    plan = load_plan(model_id)                 # all floors, static geometry
    result = analyze(plan, furniture=[...])    # checks, problems, scores for all four profiles
    unit_json(plan, result, profile, floor)    # the API shape (docs/API_CONTRACT.md)

Heat values: 0 outside, 1 blocked (wall or furniture), 2 problem, 3 caution, 4 good.
What "problem / caution / good" mean per profile is in LEGENDS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import polygonize, unary_union

from .clients import matterport

CELL = 0.05
CLEAR_DEDUCTION_M = 0.05          # scan widths look like rough openings
DOOR_M, WALK_M, TURN_M = 0.815, 0.915, 1.525   # ADA 404.2.3, 403.5.1, 304.3.1
THRESHOLD_M = 0.03                # ADA 404.2.5 allows 1/2 in (3/4 in existing); scan noise is ~2 cm, so we flag from 3 cm
STEP_M = 0.06
PROFILES = ("wheelchair", "blind", "deaf", "older")
KEY_ROOMS = ("Bedroom", "Bathroom", "Kitchen", "Living Room")
TURN_ROOMS = ("Bedroom", "Bathroom", "Kitchen")
MINOR_ROOMS = ("Closet", "Pantry", "Void", "Balcony", "Patio", "Garage", "Laundry")
ENTRY_PREF = {"Entrance": 0, "Hallway": 1, "Living Room": 2, "Kitchen": 3, "Dining Room": 3, "Other": 4, None: 4}

LEGENDS = {
    "wheelchair": {4: "Reachable with 36 in clear", 3: "Reachable, 32 to 36 in clear", 2: "A wheelchair can't reach this floor"},
    "older": {4: "Within 8 m of the bed, no steps", 3: "8 to 15 m from the bed", 2: "Over 15 m from the bed, or up stairs"},
    "deaf": {4: "Visible from the main seat", 3: "Visible from the kitchen only", 2: "Out of sight from both"},
    "blind": {4: "Clear floor", 3: "Within 30 cm of furniture", 2: "Furniture or a step on a main path"},
}


@dataclass
class Floor:
    index: int
    label: str
    bounds: tuple[float, float, float, float]
    walls: list            # [x1, y1, x2, y2, thickness] with door gaps cut
    doors: list            # dicts, see _parse_floor
    windows: list
    rooms: list            # dicts: id, type, polygon, area_m2, centroid
    shape: tuple[int, int] = (0, 0)
    room_idx: np.ndarray | None = None    # -1 outside, else index into rooms
    wall_mask: np.ndarray | None = None

    def rc(self, x: float, y: float) -> tuple[int, int]:
        r = int((y - self.bounds[1]) / CELL)
        c = int((x - self.bounds[0]) / CELL)
        return min(max(r, 0), self.shape[0] - 1), min(max(c, 0), self.shape[1] - 1)

    def xy(self, r: int, c: int) -> tuple[float, float]:
        return self.bounds[0] + (c + 0.5) * CELL, self.bounds[1] + (r + 0.5) * CELL


@dataclass
class Plan:
    model_id: str
    caption: str
    created: str | None
    geo: dict | None
    floors: list[Floor]
    entry_floor: int = 0
    entry: dict | None = None       # the entry door
    has_graph: bool = True
    room_summary: list = field(default_factory=list)


# ---------------------------------------------------------------- geometry

def _label(room: dict) -> str:
    return ((room.get("classifications") or [{}])[0] or {}).get("label") or room.get("label") or "Other"


def _room_polygon(edge_ids: list[str], edges: dict, verts: dict) -> Polygon | None:
    segs = []
    for e in edge_ids:
        if e in edges:
            a, b = (verts[v["id"]] for v in edges[e]["vertices"])
            if a != b:
                segs.append(LineString([a, b]))
    if len(segs) < 3:
        return None
    polys = list(polygonize(unary_union(segs)))
    if polys:
        return max(polys, key=lambda p: p.area) if len(polys) == 1 else unary_union(polys).buffer(0)
    hull = MultiPoint([c for s in segs for c in s.coords]).convex_hull
    return hull if hull.geom_type == "Polygon" else None


def _parse_floor(index: int, fl: dict) -> Floor | None:
    verts = {v["id"]: (v["position"]["x"], v["position"]["y"]) for v in fl["vertices"]}
    edges = {e["id"]: e for e in fl["edges"]}
    if not verts or not edges:
        return None
    rooms, edge_rooms = [], {}
    for r in fl["rooms"]:
        label = _label(r)
        ids = [e["id"] for e in ((r.get("boundary") or {}).get("edges") or [])]
        poly = _room_polygon(ids, edges, verts)
        if label == "Void" or poly is None or poly.is_empty:
            continue
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda p: p.area)
        ri = len(rooms)
        for e in ids:
            edge_rooms.setdefault(e, []).append(ri)
        c = poly.representative_point()
        rooms.append({"id": r["id"], "type": label, "polygon": [[round(x, 3), round(y, 3)] for x, y in poly.exterior.coords[:-1]],
                      "area_m2": round(poly.area, 2), "centroid": [round(c.x, 3), round(c.y, 3)], "_poly": poly,
                      "elev": (r.get("dimensions") or {}).get("lowerElevation"),
                      "panos": [p["id"] for p in r.get("panoLocations") or []]})
    if not rooms:
        return None

    walls, doors, windows = [], [], []
    door_elevs = sorted(o.get("lowerElevation") or 0 for e in fl["edges"] for o in e.get("openings") or [] if o["type"] == "doorway")
    floor_elev = door_elevs[len(door_elevs) // 2] if door_elevs else 0.0
    for e in fl["edges"]:
        a, b = (verts[v["id"]] for v in e["vertices"])
        length = math.dist(a, b)
        if length < 1e-6:
            continue
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        thick = min(max(e.get("thickness") or 0.1, 0.08), 0.4)
        lerp = lambda u: (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)  # noqa: E731
        cuts = []
        sides = edge_rooms.get(e["id"], [])
        for i, o in enumerate(e.get("openings") or []):
            c, half = o["relativeCenter"], o["width"] / length / 2
            x, y = lerp(c)
            item = {"id": f"{e['id']}_{i}", "x": round(x, 3), "y": round(y, 3), "angle_deg": round(angle, 1),
                    "width_m": round(o["width"], 3), "height_m": round(o.get("height") or 0, 2),
                    "threshold_m": _threshold(o.get("lowerElevation") or 0, floor_elev, [rooms[s_]["elev"] for s_ in sides]),
                    "label": o.get("label"), "wall_thickness": thick,
                    "rooms": [rooms[s]["id"] for s in sides], "room_types": [rooms[s]["type"] for s in sides]}
            if o["type"] == "window":
                windows.append(item)
                continue
            if o["type"] not in ("doorway", "opening") or o["width"] < 0.3:
                continue   # under 30 cm is a scan artifact, not a way through
            cuts.append((max(0, c - half), min(1, c + half)))
            deduct = CLEAR_DEDUCTION_M if o["type"] == "doorway" else 0.0
            clear = o["width"] - deduct
            item.update(kind=o["type"], is_door=o["type"] == "doorway" or o["width"] <= 1.25, clear_m=round(clear, 3), est_clear_in=round(clear / 0.0254, 1),
                        status="pass" if clear >= DOOR_M else ("tight" if clear >= 0.76 else "fail"),
                        exterior=len(sides) <= 1, minor=bool(sides) and all(rooms[s]["type"] in MINOR_ROOMS for s in sides)
                        or any(rooms[s]["type"] in ("Closet", "Pantry") for s in sides)
                        # a narrow door into a tiny untyped room is a closet or utility cupboard
                        or (clear < 0.66 and any(rooms[s]["area_m2"] < 2.5 and rooms[s]["type"] != "Bathroom" for s in sides)))
            doors.append(item)
        if e["type"] != "wall":
            continue  # invisible edges separate rooms but don't block
        t = 0.0
        for c0, c1 in sorted(cuts) + [(1.0, 1.0)]:
            if c0 - t > 1e-4:
                walls.append([*(round(v, 3) for v in (*lerp(t), *lerp(c0))), thick])
            t = max(t, c1)

    xs = [p[0] for p in verts.values()]
    ys = [p[1] for p in verts.values()]
    pad = 0.3
    bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    f = Floor(index, fl.get("label") or f"Floor {index + 1}", bounds, walls, doors, windows, rooms)
    _rasterize(f)
    return f


def _threshold(low: float, floor_elev: float, room_elevs: list) -> float:
    """Sill height above the floor. Scan elevations are noisy, so a sill only counts when it is raised
    against both the adjoining room's floor and the floor's typical door sill; over 30 cm is a data error."""
    rel = [low - floor_elev] + [low - e for e in room_elevs if e is not None]
    t = max(0.0, min(rel))
    return round(t, 3) if t <= 0.3 else 0.0


def _rasterize(f: Floor) -> None:
    x0, y0, x1, y1 = f.bounds
    nx, ny = int(math.ceil((x1 - x0) / CELL)), int(math.ceil((y1 - y0) / CELL))
    f.shape = (ny, nx)
    px = lambda x, y: ((x - x0) / CELL, (y - y0) / CELL)  # noqa: E731
    img = Image.new("I", (nx, ny), 0)
    d = ImageDraw.Draw(img)
    for i, r in sorted(enumerate(f.rooms), key=lambda t: -t[1]["area_m2"]):  # small rooms drawn last, on top
        d.polygon([px(*p) for p in r["polygon"]], fill=i + 1)
    f.room_idx = np.array(img, dtype=np.int32) - 1
    wimg = Image.new("L", (nx, ny), 0)
    wd = ImageDraw.Draw(wimg)
    for ax, ay, bx, by, t in f.walls:
        wd.line([px(ax, ay), px(bx, by)], fill=1, width=max(2, round(t / CELL)))
    f.wall_mask = np.array(wimg, dtype=bool)


def load_plan(model_id: str, raw: dict | None = None) -> Plan:
    m = raw or matterport.geometry(model_id)
    floors = []
    for i, fl in enumerate(sorted(m.get("floors") or [], key=lambda f: f.get("sequence") or 0)):
        f = _parse_floor(i, fl)
        if f:
            f.index = len(floors)
            floors.append(f)
    plan = Plan(model_id, (m.get("name") or "").strip(), m.get("created"), m.get("geocoordinates"), floors,
                has_graph=bool(floors))
    if not floors:
        plan.room_summary = [{"type": _label(r), "area_m2": round((r.get("dimensions") or {}).get("areaFloor") or 0, 1)}
                             for fl in m.get("floors") or [] for r in fl.get("rooms") or []]
        return plan
    _find_entry(plan)
    return plan


def _find_entry(plan: Plan) -> None:
    """The entry is an exterior doorway, preferably into an entrance, hallway or living space, on the lowest floor."""
    best = None
    for f in plan.floors:
        for d in f.doors:
            if not d["exterior"] or not d["is_door"]:
                continue
            rt = d["room_types"][0] if d["room_types"] else None
            if rt in ("Balcony", "Patio", "Bedroom", "Bathroom", "Closet"):
                pref = 8
            else:
                pref = ENTRY_PREF.get(rt, 5)
            wide_penalty = 3 if d["width_m"] > 1.3 else 0     # sliding balcony doors
            key = (f.index if len(plan.floors) > 1 else 0, pref + wide_penalty, abs(d["width_m"] - 0.9))
            if best is None or key < best[0]:
                best = (key, f.index, d)
    if best:
        plan.entry_floor, plan.entry = best[1], best[2]
        best[2]["entry"] = True
    else:
        # no exterior doorway recorded: start from the largest hallway or living space on the lowest floor
        f = plan.floors[0]
        plan.entry_floor = 0
        plan.entry = None


# ---------------------------------------------------------------- analysis helpers

def _furniture_mask(f: Floor, furniture: list) -> np.ndarray:
    ny, nx = f.shape
    img = Image.new("L", (nx, ny), 0)
    d = ImageDraw.Draw(img)
    x0, y0 = f.bounds[0], f.bounds[1]
    for it in furniture:
        if it.get("floor", 0) != f.index or it.get("passable"):
            continue
        d.polygon([((x - x0) / CELL, (y - y0) / CELL) for x, y in item_polygon(it)], fill=1)
    return np.array(img, dtype=bool)


def item_polygon(it: dict) -> list[tuple[float, float]]:
    """Furniture footprint. rect = [x, y, w, d] is the axis-aligned box after rotation."""
    if it.get("polygon"):
        return [tuple(p) for p in it["polygon"]]
    x, y, w, d = it["rect"]
    return [(x, y), (x + w, y), (x + w, y + d), (x, y + d)]


def _door_axes(d: dict) -> tuple[float, float, float, float]:
    a = math.radians(d["angle_deg"])
    return math.cos(a), math.sin(a), -math.sin(a), math.cos(a)   # along-wall (ux, uy), across-wall (nx, ny)


def _door_cells(f: Floor, d: dict, half_along: float, half_across: float) -> np.ndarray:
    ux, uy, nx_, ny_ = _door_axes(d)
    ny, nx = f.shape
    pad = int((half_along + half_across) / CELL) + 2
    r0, c0 = f.rc(d["x"], d["y"])
    rs, re, cs, ce = max(0, r0 - pad), min(ny, r0 + pad + 1), max(0, c0 - pad), min(nx, c0 + pad + 1)
    gy, gx = np.mgrid[rs:re, cs:ce]
    X = f.bounds[0] + (gx + 0.5) * CELL - d["x"]
    Y = f.bounds[1] + (gy + 0.5) * CELL - d["y"]
    m = (np.abs(X * ux + Y * uy) <= half_along) & (np.abs(X * nx_ + Y * ny_) <= half_across)
    out = np.zeros(f.shape, bool)
    out[rs:re, cs:ce] = m
    return out


def _pass_layer(f: Floor, free: np.ndarray, dist: np.ndarray, width: float, door_w: float) -> np.ndarray:
    """Cells where the center of a `width`-wide chair can be. Doors are decided from their measured width,
    not from the 5 cm grid: a door that passes gets a portal through the wall, one that fails is closed.
    Doors only need door_w (ADA allows 32 in at a door); `width` applies to the rest of the route."""
    r = width / 2
    ok = dist >= r - CELL / 2
    for d in f.doors:
        gap = _door_cells(f, d, d["width_m"] / 2 + CELL, d["wall_thickness"] / 2 + CELL)
        if d["clear_m"] >= door_w:
            portal = _door_cells(f, d, CELL * 0.75, d["wall_thickness"] / 2 + r + CELL)
            ok |= portal & free
        else:
            ok &= ~gap
    return ok


def _component(mask: np.ndarray, seed: tuple[int, int] | None) -> np.ndarray:
    if seed is None:
        return np.zeros_like(mask)
    lab, _ = ndi.label(mask, structure=np.ones((3, 3)))
    if lab[seed] == 0:
        # snap the seed to the nearest allowed cell within 0.6 m
        rr, cc = np.nonzero(mask)
        if not len(rr):
            return np.zeros_like(mask)
        d2 = (rr - seed[0]) ** 2 + (cc - seed[1]) ** 2
        i = int(np.argmin(d2))
        if d2[i] * CELL * CELL > 0.6 ** 2:
            return np.zeros_like(mask)
        seed = (rr[i], cc[i])
    return lab == lab[seed]


def _sweep(centers: np.ndarray, free: np.ndarray, radius: float) -> np.ndarray:
    """Floor covered by a disk of `radius` whose center stays on `centers`, without leaking through walls."""
    out = centers.copy()
    cross = ndi.generate_binary_structure(2, 1)
    square = np.ones((3, 3), bool)
    for i in range(int(round(radius / CELL))):
        out = ndi.binary_dilation(out, structure=square if i % 3 == 2 else cross) & free
    return out


def _seed(plan: Plan, f: Floor, free: np.ndarray, radius: float = 0.45) -> tuple[int, int] | None:
    """A cell just inside the entry door (or the middle of the main room if no entry is recorded)."""
    if f.index != plan.entry_floor:
        return None
    d = plan.entry
    if d:
        ux, uy, nx_, ny_ = _door_axes(d)
        for sign in (1, -1):
            for step in (radius + d["wall_thickness"] / 2 + 0.05, 0.3, 0.6, 0.9):
                x, y = d["x"] + sign * nx_ * step, d["y"] + sign * ny_ * step
                r, c = f.rc(x, y)
                if f.room_idx[r, c] >= 0 and free[r, c]:
                    return r, c
    order = sorted(range(len(f.rooms)), key=lambda i: (ENTRY_PREF.get(f.rooms[i]["type"], 6), -f.rooms[i]["area_m2"]))
    for i in order:
        r, c = f.rc(*f.rooms[i]["centroid"])
        if free[r, c]:
            return r, c
    return None


def _graph_dist(mask: np.ndarray, src: tuple[int, int]):
    """Shortest walking distances (m) over `mask` from src, with predecessors for path recovery."""
    ny, nx = mask.shape
    idx = np.arange(ny * nx).reshape(ny, nx)
    rows, cols, w = [], [], []
    for dr, dc, cost in ((0, 1, 1.0), (1, 0, 1.0), (1, 1, 1.4142), (1, -1, 1.4142)):
        a = mask[max(0, -dr):ny - max(0, dr), max(0, -dc):nx - max(0, dc)]
        b = mask[max(0, dr):, max(0, dc):nx - max(0, -dc)] if dc >= 0 else mask[dr:, :nx + dc]
        ia = idx[max(0, -dr):ny - max(0, dr), max(0, -dc):nx - max(0, dc)]
        ib = idx[max(0, dr):, max(0, dc):nx - max(0, -dc)] if dc >= 0 else idx[dr:, :nx + dc]
        if dc < 0:
            a, ia = mask[:ny - dr, -dc:], idx[:ny - dr, -dc:]
        both = a & b
        rows.append(ia[both]); cols.append(ib[both]); w.append(np.full(int(both.sum()), cost * CELL))
    g = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(ny * nx, ny * nx)).tocsr()
    dist, pred = dijkstra(g, directed=False, indices=idx[src], return_predecessors=True)
    return dist.reshape(ny, nx), pred


def _path(f: Floor, pred: np.ndarray, src: tuple[int, int], dst: tuple[int, int]) -> list[list[float]]:
    nx = f.shape[1]
    cur, target, out = dst[0] * nx + dst[1], src[0] * nx + src[1], []
    for _ in range(f.shape[0] * nx):
        out.append(f.xy(cur // nx, cur % nx))
        if cur == target:
            break
        cur = pred[cur]
        if cur < 0:
            return []
    line = LineString(out[::-1]).simplify(0.2) if len(out) > 1 else None
    return [[round(x, 2), round(y, 2)] for x, y in line.coords] if line else []


def _turns(path: list, min_deg: float = 30) -> int:
    n = 0
    for a, b, c in zip(path, path[1:], path[2:]):
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        h2 = math.atan2(c[1] - b[1], c[0] - b[0])
        if abs((math.degrees(h2 - h1) + 180) % 360 - 180) > min_deg:
            n += 1
    return n


def _nearest_true(mask: np.ndarray, rc: tuple[int, int], max_m: float = 3.0) -> tuple[int, int] | None:
    if mask[rc]:
        return rc
    rr, cc = np.nonzero(mask)
    if not len(rr):
        return None
    d2 = (rr - rc[0]) ** 2 + (cc - rc[1]) ** 2
    i = int(np.argmin(d2))
    return (int(rr[i]), int(cc[i])) if d2[i] * CELL * CELL <= max_m ** 2 else None


def _room_cell(f: Floor, ri: int, mask: np.ndarray) -> tuple[int, int] | None:
    """An allowed cell inside room ri, as close to its middle as possible."""
    inside = mask & (f.room_idx == ri)
    if not inside.any():
        return None
    return _nearest_true(inside, f.rc(*f.rooms[ri]["centroid"]), 99)


def _visible(f: Floor, opaque: np.ndarray, origin: tuple[float, float], n_rays: int = 720, max_m: float = 25) -> np.ndarray:
    ang = np.linspace(0, 2 * math.pi, n_rays, endpoint=False)
    steps = np.arange(1, int(max_m / (CELL * 0.5)))
    xs = origin[0] + np.outer(np.cos(ang), steps) * CELL * 0.5
    ys = origin[1] + np.outer(np.sin(ang), steps) * CELL * 0.5
    c = ((xs - f.bounds[0]) / CELL).astype(int)
    r = ((ys - f.bounds[1]) / CELL).astype(int)
    ok = (r >= 0) & (r < f.shape[0]) & (c >= 0) & (c < f.shape[1])
    rr, cc = np.clip(r, 0, f.shape[0] - 1), np.clip(c, 0, f.shape[1] - 1)
    blocked = ~ok | opaque[rr, cc]
    clear = np.cumsum(blocked, axis=1) == 0
    vis = np.zeros(f.shape, bool)
    vis[rr[clear], cc[clear]] = True
    return vis


def _lin(v: float, good: float, zero: float) -> float:
    return float(min(1.0, max(0.0, (zero - v) / (zero - good))))


def _rooms_of(plan: Plan, types) -> list[tuple[Floor, int]]:
    types = (types,) if isinstance(types, str) else types
    return [(f, i) for f in plan.floors for i, r in enumerate(f.rooms) if r["type"] in types]


def _in(v: float) -> str:
    return f"{v / 0.0254:.0f} in"


# ---------------------------------------------------------------- the analysis

def analyze(plan: Plan, furniture: list | None = None, chair_width: float | None = None, layers: bool = True) -> dict:
    """Checks, problems, scores (all four profiles) and, if layers=True, per-floor heat layers."""
    furniture = furniture or []
    door_w = chair_width + 0.115 if chair_width else DOOR_M      # a 0.70 m chair needs ~0.815 m
    walk_w = max(WALK_M, door_w + 0.1)
    out = {"model_id": plan.model_id, "furnished": bool(furniture), "floors": [], "checks": {}, "problems": [],
           "scores": {}, "paths": [], "turning_circles": []}
    if not plan.has_graph:
        return out
    P = out["problems"]
    multi = len(plan.floors) > 1
    stair_rooms = _rooms_of(plan, "Staircase")
    per_floor = []
    for f in plan.floors:
        floor = f.room_idx >= 0
        furn = _furniture_mask(f, furniture)
        blocked = f.wall_mask | furn
        free = floor & ~blocked
        # distance to the nearest obstacle; outside the unit counts as an obstacle
        dist = ndi.distance_transform_edt(free) * CELL
        seed = _seed(plan, f, free)
        # a "tight" door (within 5.5 cm of the limit, i.e. the raw scan width passes) lets the chair through
        # in the 32 in layer only, so everything behind it shows as caution
        p32 = _pass_layer(f, free, dist, door_w, door_w - 0.055)
        p36 = _pass_layer(f, free, dist, walk_w, door_w)
        reach32 = _component(p32, seed)
        reach36 = _component(p36 & reach32, _nearest_true(p36 & reach32, seed, 1.0) if seed and reach32.any() else None)
        cov32 = _sweep(reach32, free, door_w / 2 + 0.3)   # +0.3 m: a chair's footprint reaches into corners
        cov36 = _sweep(reach36, free, walk_w / 2 + 0.3)
        walk = free & (dist >= 0.2)
        for d in f.doors:
            walk |= _door_cells(f, d, max(0.1, d["width_m"] / 2 - 0.2), d["wall_thickness"] / 2 + 0.3) & free
        per_floor.append(dict(f=f, floor=floor, furn=furn, free=free, dist=dist, seed=seed, reach32=reach32,
                              reach36=reach36, cov32=cov32, cov36=cov36, walk=walk))

    ef = per_floor[plan.entry_floor]
    f0: Floor = ef["f"]

    # ---- wheelchair
    def room_reached(fl: dict, ri: int) -> bool:
        return bool((fl["reach32"] & (fl["f"].room_idx == ri)).any())

    doors_all = [(pf, d) for pf in per_floor for d in pf["f"].doors if d["is_door"]]
    main_doors = [(pf, d) for pf, d in doors_all if not d["minor"] and not (d["exterior"] and not d.get("entry"))]
    entry = plan.entry
    entry_val = 0.5 if entry is None else {"pass": 1.0, "tight": 0.5, "fail": 0.0}[_status(entry, door_w)]
    door_vals = [{"pass": 1.0, "tight": 0.5, "fail": 0.0}[_status(d, door_w)] for _, d in main_doors if not d.get("entry")]
    key = [(pf, i) for pf in per_floor for i, r in enumerate(pf["f"].rooms) if r["type"] in KEY_ROOMS]
    key_reached = [room_reached(pf, i) for pf, i in key]
    turn_rooms = [(pf, i) for pf in per_floor for i, r in enumerate(pf["f"].rooms) if r["type"] in TURN_ROOMS]
    turn_ok = []
    for pf, i in turn_rooms:
        fl = pf["f"]
        m = (pf["dist"] >= TURN_M / 2 - CELL / 2) & (fl.room_idx == i)
        # the circle may spill into an open-plan neighbor, but its center must be in this room and reachable
        m_reach = m & pf["cov32"]
        ok = bool(m_reach.any())
        turn_ok.append(ok)
        if ok:
            rr, cc = np.nonzero(m_reach)
            j = int(np.argmax(pf["dist"][rr, cc]))
            x, y = fl.xy(rr[j], cc[j])
            out["turning_circles"].append({"floor": fl.index, "room": fl.rooms[i]["id"], "x": round(x, 2), "y": round(y, 2), "r": TURN_M / 2})
    thr_doors = [d for _, d in main_doors]
    thr_ok = [d["threshold_m"] <= THRESHOLD_M for d in thr_doors]
    total_free = sum(int(pf["free"].sum()) for pf in per_floor) or 1
    reach_share = sum(int(pf["cov36"].sum()) for pf in per_floor) / total_free
    reach32_share = sum(int(pf["cov32"].sum()) for pf in per_floor) / total_free
    share = lambda xs, empty=1.0: (sum(xs) / len(xs)) if xs else empty  # noqa: E731
    # If the chair can't get through the entry door, nothing inside counts as reachable for the score.
    # (The heat map still shows what would be reachable once inside, which is what a wider door would buy.)
    shut_out = entry is not None and entry_val == 0.0
    wc = {
        "entry_door": (0.15, entry_val),
        "doors": (0.25, share(door_vals)),
        "rooms_reachable": (0.25, 0.0 if shut_out else share(key_reached, 0.5)),
        "turning_space": (0.15, 0.0 if shut_out else share(turn_ok, 0.5)),
        "thresholds": (0.10, share(thr_ok)),
        "floor_reachable": (0.10, 0.0 if shut_out else min(1.0, reach_share / 0.85)),
    }

    for pf, d in doors_all:
        fl = pf["f"]
        st = _status(d, door_w)
        where = _door_name(d)
        if st != "pass" and not (d["exterior"] and not d.get("entry")):
            sev = "low" if d["minor"] else ("high" if st == "fail" else "medium")
            P.append({"id": f"door_{d['id']}", "kind": "narrow_door", "severity": sev, "profile": "wheelchair",
                      "floor": fl.index, "x": d["x"], "y": d["y"],
                      "text": f"{where}: about {d['est_clear_in']:.{0 if st == 'fail' else 1}f} in clear; a wheelchair needs {_in(door_w)}."})
        if d["threshold_m"] > THRESHOLD_M and not d["minor"]:
            step = d["threshold_m"] >= STEP_M
            for prof, sev in (("wheelchair", "high" if step else "medium"), ("older", "medium"), ("blind", "medium" if step else "low")):
                P.append({"id": f"thr_{prof}_{d['id']}", "kind": "threshold", "severity": sev, "profile": prof, "floor": fl.index,
                          "x": d["x"], "y": d["y"],
                          "text": f"{where}: the scan shows a {'step' if step else 'raised threshold'} of about {d['threshold_m'] * 100:.0f} cm"
                                  f" ({d['threshold_m'] / 0.0254:.1f} in)" + ("; may need a ramp." if prof == "wheelchair" else "; a trip hazard.")})
    for (pf, i), ok in zip(key, key_reached):
        r = pf["f"].rooms[i]
        if not ok:
            why = "it is on another floor, reached only by stairs" if pf["f"].index != plan.entry_floor else \
                  "a doorway or passage on the way is too narrow" + (" or blocked by furniture" if furniture else "")
            P.append({"id": f"reach_{r['id']}", "kind": "unreachable_room", "severity": "high", "profile": "wheelchair", "floor": pf["f"].index,
                      "x": r["centroid"][0], "y": r["centroid"][1], "text": f"A wheelchair can't reach this {r['type'].lower()}: {why}."})
    for (pf, i), ok in zip(turn_rooms, turn_ok):
        r = pf["f"].rooms[i]
        if not ok and room_reached(pf, i):
            P.append({"id": f"turn_{r['id']}", "kind": "no_turning_space", "severity": "medium", "profile": "wheelchair", "floor": pf["f"].index,
                      "x": r["centroid"][0], "y": r["centroid"][1],
                      "text": f"No 60 in turning circle fits in this {r['type'].lower()}"
                              + (" with the furniture in place." if furniture else " (measured before fixtures).")})

    # ---- paths from the entry and from the bed (older adults, blind)
    paths = {}
    dist_entry = pred_entry = None
    if ef["seed"] is not None:
        src = _nearest_true(ef["walk"], ef["seed"], 1.0)
        if src:
            dist_entry, pred_entry = _graph_dist(ef["walk"], src)
            for kind, types in (("bed", ("Bedroom",)), ("bathroom", ("Bathroom",)), ("kitchen", ("Kitchen",))):
                best = None
                for i, r in enumerate(f0.rooms):
                    if r["type"] in types:
                        cell = _room_cell(f0, i, ef["walk"] & np.isfinite(dist_entry))
                        if cell and (best is None or dist_entry[cell] < best[0]):
                            best = (float(dist_entry[cell]), cell, r)
                if best:
                    pts = _path(f0, pred_entry, src, best[1])
                    paths[kind] = {"from": "entry", "to": kind, "floor": f0.index, "meters": round(best[0], 1), "turns": _turns(pts), "points": pts}
    # bed -> nearest bathroom (largest bedroom on any floor)
    bed_bath = None
    beds = sorted(_rooms_of(plan, "Bedroom"), key=lambda t: -t[0].rooms[t[1]]["area_m2"])
    bed_dist_floor = None
    if beds:
        bf, bi = beds[0]
        pf = per_floor[bf.index]
        src = _room_cell(bf, bi, pf["walk"])
        if src:
            dbed, pbed = _graph_dist(pf["walk"], src)
            bed_dist_floor = (bf.index, dbed)
            best = None
            for i, r in enumerate(bf.rooms):
                if r["type"] == "Bathroom":
                    door_cells = [bf.rc(d["x"], d["y"]) for d in bf.doors if r["id"] in d["rooms"]]
                    cands = [c for c in (_nearest_true(pf["walk"] & np.isfinite(dbed), dc, 0.6) for dc in door_cells) if c]
                    cell = min(cands, key=lambda c: dbed[c]) if cands else _room_cell(bf, i, pf["walk"] & np.isfinite(dbed))
                    if cell and (best is None or dbed[cell] < best[0]):
                        best = (float(dbed[cell]), cell)
            if best:
                pts = _path(bf, pbed, src, best[1])
                bed_bath = best[0]
                paths["bed_bathroom"] = {"from": "bed", "to": "bathroom", "floor": bf.index, "meters": round(best[0], 1), "turns": _turns(pts), "points": pts}
    out["paths"] = list(paths.values())

    # ---- older adults
    key3 = [(f, i) for f, i in _rooms_of(plan, ("Bedroom", "Bathroom", "Kitchen"))]
    on_entry = [f.index == plan.entry_floor for f, _ in key3]
    stairs_val = share(on_entry, 0.5) if multi else (0.7 if stair_rooms else 1.0)
    baths = _rooms_of(plan, "Bathroom")
    bath_vals = []
    for f, i in baths:
        r = f.rooms[i]
        mrr = r["_poly"].minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        side = sorted((math.dist((xs[0], ys[0]), (xs[1], ys[1])), math.dist((xs[1], ys[1]), (xs[2], ys[2]))))
        bath_vals.append(min(1.0, max(0.0, (side[0] - 1.2) / 0.45)) * 0.5 + min(1.0, max(0.0, (r["area_m2"] - 2.8) / 1.7)) * 0.5)
    kitchen_m = paths.get("kitchen", {}).get("meters")
    older = {
        "no_stairs": (0.30, stairs_val),
        "bed_to_bathroom": (0.25, _lin(bed_bath, 6, 18) if bed_bath is not None else (0.5 if not beds or not baths else 0.0)),
        "entry_to_kitchen": (0.15, _lin(kitchen_m, 8, 25) if kitchen_m is not None else 0.5),
        "thresholds": (0.15, share(thr_ok)),
        "bathroom_space": (0.15, max(bath_vals) if bath_vals else 0.5),
    }
    if multi:
        up = [f"{sum(1 for (f, i) in key3 if f.index != plan.entry_floor and f.rooms[i]['type'] == t)} {t.lower()}{'s' if 1 else ''}" for t in ("Bedroom", "Bathroom")]
        n_up = sum(1 for (f, _i), on in zip(key3, on_entry) if not on)
        if n_up:
            P.append({"id": "stairs", "kind": "stairs", "severity": "high", "profile": "older", "floor": plan.entry_floor,
                      "x": f0.rooms[0]["centroid"][0], "y": f0.rooms[0]["centroid"][1],
                      "text": f"{len(plan.floors)} floors: {up[0]} and {up[1]} are on another floor, reached only by stairs."})
            P.append({"id": "stairs_blind", "kind": "stairs", "severity": "medium", "profile": "blind", "floor": plan.entry_floor,
                      "x": f0.rooms[0]["centroid"][0], "y": f0.rooms[0]["centroid"][1],
                      "text": f"Stairs inside the home ({len(plan.floors)} floors)."})
    if bed_bath is not None and bed_bath > 12:
        b = paths["bed_bathroom"]
        P.append({"id": "bed_bath", "kind": "long_walk", "severity": "medium", "profile": "older", "floor": b["floor"],
                  "x": b["points"][len(b["points"]) // 2][0] if b["points"] else 0, "y": b["points"][len(b["points"]) // 2][1] if b["points"] else 0,
                  "text": f"The bathroom is about {bed_bath:.0f} m ({bed_bath * 3.28:.0f} ft) from the main bedroom."})
    elif beds and baths and bed_bath is None and beds[0][0].index not in {f.index for f, _ in baths}:
        P.append({"id": "bed_bath", "kind": "long_walk", "severity": "high", "profile": "older", "floor": beds[0][0].index,
                  "x": beds[0][0].rooms[beds[0][1]]["centroid"][0], "y": beds[0][0].rooms[beds[0][1]]["centroid"][1],
                  "text": "No bathroom on the same floor as the main bedroom."})

    # ---- blind: simple paths, level changes, clutter near paths
    main_paths = [paths[k] for k in ("bed", "bathroom", "kitchen") if k in paths]
    turns_avg = share([p["turns"] for p in main_paths], None) if main_paths else None
    near_furn = np.zeros(1)
    clutter_val, clutter_hits = 1.0, []
    if furniture and main_paths:
        for it in furniture:
            if it.get("floor", 0) != f0.index:
                continue
            poly = Polygon(item_polygon(it))
            for p in main_paths:
                if len(p["points"]) > 1 and LineString(p["points"]).distance(poly) < 0.3:
                    clutter_hits.append(it)
                    break
        n_items = sum(1 for it in furniture if it.get("floor", 0) == f0.index) or 1
        clutter_val = 1 - min(1.0, len(clutter_hits) / max(3, n_items * 0.5))
        for it in clutter_hits[:4]:
            cx, cy = Polygon(item_polygon(it)).centroid.coords[0]
            P.append({"id": f"clutter_{it.get('id')}", "kind": "path_obstacle", "severity": "medium", "profile": "blind", "floor": f0.index,
                      "x": round(cx, 2), "y": round(cy, 2), "text": f"The {it.get('label', 'furniture')} sits within 30 cm of a main path; move it against a wall."})
    level_changes = sum(1 for d in thr_doors if d["threshold_m"] > THRESHOLD_M)
    blind = {
        "simple_paths": (0.45, _lin(turns_avg, 2, 7) if turns_avg is not None else 0.5),
        "level_changes": (0.25, max(0.0, (0.6 if multi or stair_rooms else 1.0) - 0.2 * level_changes)),
        "clear_paths": (0.30, clutter_val),
    }
    for p in main_paths:
        if p["turns"] >= 5:
            mid = p["points"][len(p["points"]) // 2]
            P.append({"id": f"turns_{p['to']}", "kind": "complex_path", "severity": "low", "profile": "blind", "floor": p["floor"], "x": mid[0], "y": mid[1],
                      "text": f"The way from the entry to the {p['to']} has {p['turns']} turns."})

    # ---- deaf: sightlines from the main seat and the kitchen, daylight
    opaque0 = f0.wall_mask | ~(f0.room_idx >= 0)
    tall = [it for it in furniture if it.get("tall") and it.get("floor", 0) == f0.index]
    if tall:
        opaque0 = opaque0 | _furniture_mask(f0, tall)
    seat = next((it for it in furniture if it.get("label") in ("sofa", "sectional") and it.get("floor", 0) == f0.index), None)
    living = [i for i, r in enumerate(f0.rooms) if r["type"] == "Living Room"] or \
             [i for i, r in sorted(enumerate(f0.rooms), key=lambda t: -t[1]["area_m2"]) if r["type"] in ("Kitchen", "Dining Room", "Other", "Loft", "Bedroom")][:1]
    kitchens = [i for i, r in enumerate(f0.rooms) if r["type"] == "Kitchen"]
    vis_seat = vis_kitchen = np.zeros(f0.shape, bool)
    seat_xy = None
    if seat:
        seat_xy = Polygon(item_polygon(seat)).centroid.coords[0]
    elif living:
        cell = _room_cell(f0, living[0], ef["free"])
        seat_xy = f0.xy(*cell) if cell else None
    if seat_xy:
        vis_seat = _visible(f0, opaque0, seat_xy) & (f0.room_idx >= 0)
    kit_xy = None
    if kitchens:
        cell = _room_cell(f0, kitchens[0], ef["free"])
        if cell:
            kit_xy = f0.xy(*cell)
            vis_kitchen = _visible(f0, opaque0, kit_xy) & (f0.room_idx >= 0)
    living_area = np.isin(f0.room_idx, [i for i, r in enumerate(f0.rooms) if r["type"] not in ("Bedroom", "Bathroom", "Closet", "Pantry", "Garage", "Laundry", "Balcony", "Patio")])
    vis_share = float((vis_seat & living_area).sum() / max(1, living_area.sum()))

    def sees_entry(vis: np.ndarray) -> float:
        if not plan.entry or not vis.any():
            return 0.5
        return 1.0 if (vis & _door_cells(f0, plan.entry, plan.entry["width_m"] / 2, plan.entry["wall_thickness"] / 2 + 0.25)).any() else 0.0
    n_windows = sum(len(f.windows) for f in plan.floors)
    day_rooms = _rooms_of(plan, ("Living Room", "Bedroom", "Kitchen"))
    day = [any(f.rooms[i]["id"] in w["rooms"] for w in f.windows) for f, i in day_rooms]
    deaf = {
        "open_sightlines": (0.45, min(1.0, vis_share / 0.75)),
        "entry_visible_from_seat": (0.25, sees_entry(vis_seat)),
        "entry_visible_from_kitchen": (0.10, sees_entry(vis_kitchen)),
        "daylight": (0.20, share(day, 0.5) if n_windows else 0.5),
    }
    if plan.entry and sees_entry(vis_seat) == 0.0 and seat_xy:
        P.append({"id": "entry_sight", "kind": "sightline", "severity": "medium", "profile": "deaf", "floor": f0.index,
                  "x": plan.entry["x"], "y": plan.entry["y"],
                  "text": "The entry door can't be seen from the main seating spot; a visitor or a flashing doorbell there goes unnoticed."})

    # ---- scores
    area_all = sum(r["area_m2"] for f in plan.floors for r in f.rooms) or 1
    untyped = sum(r["area_m2"] for f in plan.floors for r in f.rooms if r["type"] == "Other") / area_all
    conf = {"wheelchair": "high" if plan.entry else "medium", "older": "high" if beds and baths else "medium",
            "blind": "medium" if main_paths else "low", "deaf": "medium" if n_windows and seat_xy else "low"}
    if untyped > 0.5:   # rooms not classified: room-based checks are guesses
        conf = {k: "low" for k in conf}
    for prof, checks in (("wheelchair", wc), ("older", older), ("blind", blind), ("deaf", deaf)):
        out["checks"][prof] = {k: {"weight": w, "value": round(float(v), 2)} for k, (w, v) in checks.items()}
        out["scores"][prof] = {"score": round(100 * sum(w * v for w, v in checks.values())), "confidence": conf[prof]}
    out["stats"] = {
        "doorways": len(doors_all), "doorways_pass": sum(1 for _, d in doors_all if _status(d, door_w) == "pass"),
        "narrowest_main_door_in": min((d["est_clear_in"] for _, d in main_doors), default=None),
        "reach36_share": round(reach_share, 2), "reach32_share": round(reach32_share, 2),
        "bed_to_bathroom_m": round(bed_bath, 1) if bed_bath is not None else None,
        "visible_share": round(vis_share, 2), "floors": len(plan.floors), "windows": n_windows,
        "area_m2": round(sum(r["area_m2"] for f in plan.floors for r in f.rooms), 1),
        "entry_found": plan.entry is not None,
    }
    sev = {"high": 0, "medium": 1, "low": 2}
    seen_p = set()
    P[:] = [p for p in P if not ((p["profile"], round(p["x"], 1), round(p["y"], 1), p["kind"]) in seen_p
                                 or seen_p.add((p["profile"], round(p["x"], 1), round(p["y"], 1), p["kind"])))]
    P.sort(key=lambda p: (sev[p["severity"]], p["profile"]))

    if layers:
        for pf in per_floor:
            fl: Floor = pf["f"]
            base = np.where(pf["floor"], 2, 0)
            base[pf["floor"] & (fl.wall_mask | pf["furn"])] = 1
            wch = base.copy()
            wch[pf["cov32"]] = 3
            wch[pf["cov36"]] = 4
            for d in fl.doors:   # paint failing doorways so narrow doors show on the map
                st = _status(d, door_w)
                if d["is_door"] and st != "pass" and not (d["exterior"] and not d.get("entry")):
                    gap = _door_cells(fl, d, d["width_m"] / 2, d["wall_thickness"] / 2 + 0.2) & pf["free"]
                    wch[gap] = 2 if st == "fail" else np.minimum(wch[gap], 3)
            old = base.copy()
            if bed_dist_floor and bed_dist_floor[0] == fl.index:
                dd = ndi.grey_erosion(np.where(np.isfinite(bed_dist_floor[1]), bed_dist_floor[1], 99.0), size=(9, 9))
                old[pf["free"] & (dd <= 15)] = 3
                old[pf["free"] & (dd <= 8)] = 4
            elif fl.index == plan.entry_floor and not multi:
                old[pf["free"]] = 3
            dea = base.copy()
            if fl.index == f0.index:
                dea[pf["free"] & vis_kitchen] = 3
                dea[pf["free"] & vis_seat] = 4
            bli = base.copy()
            bli[pf["free"]] = 4
            if pf["furn"].any():
                bli[pf["free"] & ndi.binary_dilation(pf["furn"], iterations=6)] = 3
            out["floors"].append({"index": fl.index, "heat": {"wheelchair": wch, "older": old, "deaf": dea, "blind": bli}})
        out["view_points"] = {"seat": [round(v, 2) for v in seat_xy] if seat_xy else None,
                              "kitchen": [round(v, 2) for v in kit_xy] if kit_xy else None}
    return out


def _status(d: dict, door_w: float = DOOR_M) -> str:
    return "pass" if d["clear_m"] >= door_w else ("tight" if d["clear_m"] >= door_w - 0.055 else "fail")


def _door_name(d: dict) -> str:
    if d.get("entry"):
        return "Entry door"
    types = [t for t in d["room_types"] if t not in ("Hallway", "Other", "Living Room", "Entrance")] or \
            [t for t in d["room_types"] if t != "Other"]
    if d["exterior"]:
        return f"Outside door of the {types[0].lower()}" if types else "Outside door"
    return f"{types[0]} door" if types else "Doorway"


# ---------------------------------------------------------------- output shapes

def unit_json(plan: Plan, res: dict, profile: str = "wheelchair", floor: int | None = None, furniture: list | None = None,
              out_cell: float = CELL) -> dict:
    """The /units/{id}/heatmap response for one floor and one profile."""
    fi = plan.entry_floor if floor is None else floor
    f = plan.floors[fi]
    heat = res["floors"][fi]["heat"][profile]
    step = max(1, round(out_cell / CELL))
    if step > 1:
        heat = heat[::step, ::step]
    return {
        "model_id": plan.model_id, "caption": plan.caption, "units": "meters", "profile": profile, "furnished": res["furnished"],
        "floor": fi, "floors": [{"index": x.index, "label": x.label} for x in plan.floors], "entry_floor": plan.entry_floor,
        "scan_date": (plan.created or "")[:10] or None,
        "floor_z": round(float(np.median([r["elev"] for r in f.rooms if r.get("elev") is not None] or [0.0])), 3),
        "bounds": [round(v, 3) for v in f.bounds],
        "walls": [w[:4] for w in f.walls],
        "doors": [{k: d[k] for k in ("id", "x", "y", "angle_deg", "width_m", "est_clear_in", "threshold_m", "kind", "status", "is_door", "exterior")} | {"entry": bool(d.get("entry")), "minor": d["minor"], "room_types": d["room_types"]} for d in f.doors],
        "windows": [{k: w[k] for k in ("x", "y", "angle_deg", "width_m")} for w in f.windows],
        "rooms": [{k: r[k] for k in ("id", "type", "polygon", "area_m2", "centroid", "panos")} for r in f.rooms],
        "furniture": [it for it in (furniture or []) if it.get("floor", 0) == fi],
        "turning_circles": [t for t in res["turning_circles"] if t["floor"] == fi],
        "paths": [p for p in res["paths"] if p["floor"] == fi],
        "view_points": res.get("view_points"),
        "problems": [p for p in res["problems"] if p["profile"] == profile],
        "scores": res["scores"], "checks": res["checks"].get(profile, {}), "stats": res.get("stats"),
        "legend": LEGENDS[profile],
        "heat": {"cell_m": CELL * step, "origin": [round(f.bounds[0], 3), round(f.bounds[1], 3)],
                 "width": int(heat.shape[1]), "height": int(heat.shape[0]), "values": heat.tolist()},
    }


def summary(plan: Plan, res: dict) -> dict:
    """Small per-unit record for the units index (no grids)."""
    if not plan.has_graph:
        return {"model_id": plan.model_id, "caption": plan.caption, "has_graph": False, "rooms": plan.room_summary,
                "scan_date": (plan.created or "")[:10] or None}
    return {"model_id": plan.model_id, "caption": plan.caption, "has_graph": True, "scan_date": (plan.created or "")[:10] or None,
            "scores": res["scores"], "stats": res["stats"],
            "rooms": [{"type": r["type"], "area_m2": r["area_m2"], "floor": f.index} for f in plan.floors for r in f.rooms],
            "problems": [{k: p[k] for k in ("kind", "severity", "profile", "text")} for p in res["problems"] if p["severity"] != "low"][:12]}


def render_png(plan: Plan, res: dict, path, profile: str = "wheelchair", floor: int | None = None, furniture: list | None = None, px_per_m: int = 70) -> None:
    u = unit_json(plan, res, profile, floor, furniture)
    x0, y0, x1, y1 = u["bounds"]
    W, H = int((x1 - x0) * px_per_m), int((y1 - y0) * px_per_m)
    T = lambda x, y: ((x - x0) * px_per_m, H - (y - y0) * px_per_m)  # noqa: E731
    colors = np.array([(255, 255, 255), (205, 205, 205), (240, 130, 130), (245, 205, 115), (160, 220, 160)], dtype=np.uint8)
    vals = np.array(u["heat"]["values"])[::-1]
    im = Image.fromarray(colors[vals]).resize((W, H), Image.NEAREST)
    d = ImageDraw.Draw(im)
    for w in u["walls"]:
        d.line([T(w[0], w[1]), T(w[2], w[3])], fill="black", width=3)
    for t in u["turning_circles"]:
        cx, cy = T(t["x"], t["y"]); r = t["r"] * px_per_m
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(20, 90, 200), width=2)
    for p in u["paths"]:
        if len(p["points"]) > 1:
            d.line([T(*pt) for pt in p["points"]], fill=(90, 60, 200), width=2)
    for it in u["furniture"]:
        d.polygon([T(*p) for p in item_polygon(it)], outline=(80, 50, 20), fill=(190, 160, 120))
        cx, cy = Polygon(item_polygon(it)).centroid.coords[0]
        d.text(T(cx - 0.3, cy), it.get("label", ""), fill=(40, 20, 0))
    for door in u["doors"]:
        col = {"pass": (0, 110, 50), "tight": (150, 100, 0), "fail": (190, 0, 0)}[door["status"]]
        d.text(T(door["x"] + 0.05, door["y"] + 0.15), f"{door['est_clear_in']:.0f}in" + ("*" if door["entry"] else ""), fill=col)
    for r in u["rooms"]:
        d.text(T(r["centroid"][0] - 0.4, r["centroid"][1]), r["type"], fill=(60, 60, 60))
    im.save(path)


if __name__ == "__main__":
    import json
    import sys
    from .clients import ROOT
    mid = sys.argv[1]
    plan = load_plan(mid)
    res = analyze(plan)
    out_dir = ROOT / "data" / "units"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(plan.caption, "| floors", len(plan.floors), "| entry", plan.entry and (plan.entry["room_types"], plan.entry["est_clear_in"]))
    print(json.dumps(res["scores"]))
    print(json.dumps(res["checks"], indent=None))
    print(json.dumps(res.get("stats")))
    for p in res["problems"]:
        print(" ", p["severity"], p["profile"], p["text"])
    for prof in PROFILES:
        render_png(plan, res, out_dir / f"{mid}_{prof}.png", prof)
