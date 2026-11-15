"""Furnishing planner (Technical Design section 8).

Mode A  score_furnishing(model_id, profiles)  -> score empty vs furnished + the single changes that gain most
Mode B  plan_furnishing(target_model_id, inventory, profiles, preferences) -> placements, before/after, won't-fit

Both use the apartment engine (pipeline.unit.analyze) as the judge.

    python -m pipeline.furnish score <model_id>
    python -m pipeline.furnish plan <target_model_id> [<source_model_id>]
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from shapely.geometry import Polygon

from .clients import ROOT
from .unit import CELL, DOOR_M, TURN_M, Floor, Plan, _component, _door_cells, _pass_layer, _seed, analyze, item_polygon, load_plan

DATA = ROOT / "data"
# label: (width along the wall, depth out from the wall, tall?, room group)
STANDARD = {
    "bed": (1.52, 2.03, False, "bedroom"), "queen bed": (1.52, 2.03, False, "bedroom"), "king bed": (1.93, 2.03, False, "bedroom"),
    "full bed": (1.37, 1.91, False, "bedroom"), "twin bed": (0.97, 1.91, False, "bedroom"),
    "nightstand": (0.5, 0.45, False, "bedroom"), "dresser": (1.5, 0.5, False, "bedroom"), "wardrobe": (1.0, 0.6, True, "bedroom"),
    "sofa": (2.1, 0.9, False, "living"), "sectional": (2.7, 1.6, False, "living"), "loveseat": (1.5, 0.9, False, "living"),
    "armchair": (0.85, 0.85, False, "living"), "tv stand": (1.5, 0.45, False, "living"), "coffee table": (1.1, 0.6, False, "living"),
    "bookshelf": (0.8, 0.3, True, "living"), "piano": (1.5, 0.65, True, "living"),
    "dining table": (1.5, 1.5, False, "dining"), "desk": (1.2, 0.6, False, "work"),
    "console table": (1.1, 0.4, False, "living"), "stored items": (1.2, 1.0, False, "living"),
}
SMALLER = {"king bed": "queen bed", "queen bed": "full bed", "bed": "full bed", "full bed": "twin bed", "sectional": "sofa",
           "sofa": "loveseat", "loveseat": "armchair", "dresser": "nightstand", "piano": None}
ROOM_GROUPS = {
    "bedroom": ("Bedroom",),
    "living": ("Living Room", "Loft", "Bonus Room", "Other", "Dining Room", "Kitchen"),
    "dining": ("Dining Room", "Kitchen", "Living Room", "Other"),
    "work": ("Office", "Bedroom", "Living Room", "Loft", "Other"),
}
PROFILE_WEIGHT = {"wheelchair": 1.0, "older": 1.0, "blind": 1.0, "deaf": 1.0}


def load_furniture(model_id: str) -> list[dict]:
    """Finder output merged with labels; unlabeled or ignored blobs are dropped."""
    path = DATA / "furniture" / f"{model_id}.json"
    if not path.exists():
        return []
    items = json.loads(path.read_text())["items"]
    lpath = DATA / "furniture" / f"{model_id}.labels.json"
    labels = json.loads(lpath.read_text())["labels"] if lpath.exists() else {}
    out = []
    for it in items:
        label = labels.get(it["id"], None if lpath.exists() else "furniture")
        if not label or it.get("room_type") in ("Closet", "Pantry"):
            continue
        it = {**it, "label": label, "tall": STANDARD.get(label, (0, 0, False))[2]}
        it.pop("polygon", None)          # use the axis-aligned box: simpler to move and explain
        std = STANDARD.get(label)
        if std:
            # the carved blob also swallows rugs, bedding and things beside the piece: trim it to at most
            # 1.2 x the standard size for its label, keeping the blob's center and long direction
            x, y, w, d = it["rect"]
            long_, short_ = max(std[:2]) * 1.2, min(std[:2]) * 1.2
            nw, nd = (min(w, long_), min(d, short_)) if w >= d else (min(w, short_), min(d, long_))
            it["rect"] = [round(x + (w - nw) / 2, 2), round(y + (d - nd) / 2, 2), round(nw, 2), round(nd, 2)]
        # footprints from panoramas are good to 20-30 cm; pull each edge in 10 cm so that error can't
        # close a path that is really open (a wrong "blocked" is worse here than a slightly generous gap)
        x, y, w, d = it["rect"]
        if w > 0.5 and d > 0.5:
            it["rect"] = [round(x + 0.1, 2), round(y + 0.1, 2), round(w - 0.2, 2), round(d - 0.2, 2)]
        out.append(it)
    # heights from the furnished scan's 3D mesh, where it is on disk: the footprint comes from the panoramas,
    # the main surface (mattress, seat, table top) and the top from the mesh
    from .fixtures import load_heights
    try:
        heights = load_heights(model_id, out)
    except Exception:  # noqa: BLE001  a damaged mesh cache must not stop the furniture loading
        heights = {}
    for it in out:
        h = heights.get(it["id"])
        it["height_m"] = h["height_m"] if h else None
        it["surface_m"] = h["surface_m"] if h else None
        it["height_source"] = "mesh" if h else None
    return out


def _combined(res: dict, profiles: list[str]) -> float:
    return min(res["scores"][p]["score"] for p in profiles) if profiles else 0.0


def _avg(res: dict, profiles: list[str]) -> float:
    return sum(res["scores"][p]["score"] for p in profiles) / len(profiles)


def _legal(f: Floor, rect: list[float], others: list[dict], door_zone: np.ndarray | None = None) -> bool:
    x, y, w, d = rect
    r0, c0 = f.rc(x + 0.02, y + 0.02)
    r1, c1 = f.rc(x + w - 0.02, y + d - 0.02)
    if x < f.bounds[0] or y < f.bounds[1] or x + w > f.bounds[2] or y + d > f.bounds[3]:
        return False
    sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
    if (f.room_idx[sl] < 0).any() or f.wall_mask[sl].any():
        return False
    if f.fixture_mask is not None and f.fixture_mask[sl].any():
        return False                                   # not on top of a counter, island, toilet or tub
    if door_zone is not None and door_zone[sl].any():
        return False
    for o in others:
        ox, oy, ow, od = o["rect"]
        if x < ox + ow - 0.02 and ox < x + w - 0.02 and y < oy + od - 0.02 and oy < y + d - 0.02:
            return False
    return True


def _door_zones(f: Floor, depth: float = 1.0) -> np.ndarray:
    z = np.zeros(f.shape, bool)
    for d in f.doors:
        z |= _door_cells(f, d, d["width_m"] / 2 + 0.08, d["wall_thickness"] / 2 + depth)
    return z


# ---------------------------------------------------------------- Mode A

def score_furnishing(model_id: str, profiles: list[str], furniture: list[dict] | None = None, max_suggestions: int = 5) -> dict:
    plan = load_plan(model_id)
    furniture = furniture if furniture is not None else load_furniture(model_id)
    empty = analyze(plan, layers=False)
    cur = analyze(plan, furniture, layers=False)
    base = _avg(cur, profiles)
    suggestions = []
    for it in furniture:
        f = plan.floors[it.get("floor", 0)]
        others = [o for o in furniture if o is not it]
        x, y, w, d = it["rect"]
        moves = [("move", [x + dx, y + dy, w, d], f"{abs(dx or dy) * 100:.0f} cm {('right' if dx > 0 else 'left') if dx else ('up' if dy > 0 else 'down')} on the plan")
                 for dx, dy in ((0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6))]
        moves.append(("rotate", [x + w / 2 - d / 2, y + d / 2 - w / 2, d, w], "a quarter turn"))
        best = None
        for action, rect, how in moves:
            if not _legal(f, rect, others):
                continue
            res = analyze(plan, others + [{**it, "rect": [round(v, 2) for v in rect]}], layers=False)
            gain = _avg(res, profiles) - base
            if gain > 0.9 and (best is None or gain > best[0]):
                best = (gain, action, rect, how, res)
        res = analyze(plan, others, layers=False)
        gain = _avg(res, profiles) - base
        if gain > 2.9 and (best is None or gain > best[0] + 4):     # removing is a last resort
            best = (gain, "remove", None, "", res)
        if best:
            gain, action, rect, how, res = best
            why = _why(cur, res, profiles)
            text = (f"Move the {it['label']} {how}" if action == "move" else
                    f"Turn the {it['label']} {how}" if action == "rotate" else f"Take out the {it['label']}, or swap it for a smaller one")
            suggestions.append({"item_id": it["id"], "label": it["label"], "action": action,
                                "to": [round(v, 2) for v in rect] if rect else None, "gain": round(gain, 1),
                                "text": f"{text}{': ' + why if why else ''} (+{gain:.0f})."})
    suggestions.sort(key=lambda s: -s["gain"])
    return {
        "model_id": model_id, "profiles": profiles,
        "score_empty": {p: empty["scores"][p] for p in profiles}, "score_furnished": {p: cur["scores"][p] for p in profiles},
        "combined_empty": round(_combined(empty, profiles)), "combined_furnished": round(_combined(cur, profiles)),
        "furniture_cost": round(_combined(empty, profiles) - _combined(cur, profiles)),
        "furniture": furniture, "suggestions": suggestions[:max_suggestions],
        "problems": [p for p in cur["problems"] if p["profile"] in profiles and p["severity"] != "low"],
        "note": "Footprints come from comparing the furnished and furniture-removed scans; expect 20 to 30 cm of error.",
    }


CHECK_TEXT = {
    ("wheelchair", "rooms_reachable"): "a wheelchair can then reach every main room",
    ("wheelchair", "floor_reachable"): "it opens a 36 in path",
    ("wheelchair", "turning_space"): "it frees a 60 in turning circle",
    ("wheelchair", "doors"): "it clears a doorway",
    ("blind", "clear_paths"): "it clears a main walking path",
    ("blind", "simple_paths"): "the way through has fewer turns",
    ("deaf", "open_sightlines"): "it opens the view across the living area",
    ("deaf", "entry_visible_from_seat"): "the entry door can be seen from the sofa",
    ("older", "bed_to_bathroom"): "the walk from bed to bathroom gets shorter",
    ("older", "entry_to_kitchen"): "the walk to the kitchen gets shorter",
}


def _why(before: dict, after: dict, profiles: list[str]) -> str:
    best = None
    for p in profiles:
        for k, v in after["checks"][p].items():
            delta = (v["value"] - before["checks"][p][k]["value"]) * v["weight"]
            if delta > 0.004 and (best is None or delta > best[0]):
                best = (delta, CHECK_TEXT.get((p, k), k.replace("_", " ")))
    return best[1] if best else ""


# ---------------------------------------------------------------- Mode B

def _quick(plan: Plan, f: Floor, furn_mask: np.ndarray, base: dict | None = None) -> dict:
    """Fast wheelchair check for one floor: which rooms the chair reaches and where a turning circle fits."""
    floor = f.room_idx >= 0
    fix = f.fixture_mask if f.fixture_mask is not None else False
    free = floor & ~(f.wall_mask | furn_mask | fix)      # built-ins block like walls, as in the engine
    dist = ndi.distance_transform_edt(free) * CELL
    seed = _seed(plan, f, free)
    reach = _component(_pass_layer(f, free, dist, DOOR_M, DOOR_M - 0.055), seed)
    wide = _component(_pass_layer(f, free, dist, 0.915, DOOR_M) & reach, seed if seed and reach[seed] else None)
    rooms = {i for i in np.unique(f.room_idx[reach]) if i >= 0}
    turn = {i for i in np.unique(f.room_idx[reach & (dist >= TURN_M / 2 - CELL / 2)]) if i >= 0}
    wide_rooms = {i for i in np.unique(f.room_idx[wide]) if i >= 0}
    return {"rooms": rooms, "turn": turn, "wide_rooms": wide_rooms, "reach": reach, "wide_share": float(wide.sum()) / max(1, int(free.sum())),
            "dist": dist, "free": free}


def _candidates(f: Floor, room_ids: list[int], w: float, d: float, free_standing: bool, step: float = 0.2):
    """Axis-aligned rectangles with their back to a wall of the allowed rooms (or free-standing on a grid)."""
    out = []
    allowed = np.isin(f.room_idx, room_ids)
    if free_standing:
        rr, cc = np.nonzero(allowed)
        if not len(rr):
            return out
        x0, x1 = f.bounds[0] + cc.min() * CELL, f.bounds[0] + cc.max() * CELL
        y0, y1 = f.bounds[1] + rr.min() * CELL, f.bounds[1] + rr.max() * CELL
        for x in np.arange(x0 + 0.6, x1 - w - 0.5, 0.3):
            for y in np.arange(y0 + 0.6, y1 - d - 0.5, 0.3):
                out.append(([round(float(x), 2), round(float(y), 2), w, d], None))
        return out
    for ax, ay, bx, by, t in f.walls:
        horiz, vert = abs(ay - by) < 0.03, abs(ax - bx) < 0.03
        if not (horiz or vert):
            continue
        length = math.dist((ax, ay), (bx, by))
        for along, deep in ((w, d), (d, w)) if abs(w - d) > 0.05 else ((w, d),):
            if length < along:
                continue
            for s in np.arange(0, length - along + 0.001, step):
                for side in (1, -1):
                    off = t / 2 + 0.03
                    if horiz:
                        x = min(ax, bx) + s
                        y = ay + off if side > 0 else ay - off - deep
                        rect, facing = [x, y, along, deep], (0, side)
                    else:
                        y = min(ay, by) + s
                        x = ax + off if side > 0 else ax - off - deep
                        rect, facing = [x, y, deep, along], (side, 0)
                    cr, cc_ = f.rc(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
                    if allowed[cr, cc_]:
                        out.append(([round(float(v), 2) for v in rect], facing))
    return out


def _rect_mask(f: Floor, rect: list[float]) -> tuple[slice, slice]:
    r0, c0 = f.rc(rect[0] + 0.02, rect[1] + 0.02)
    r1, c1 = f.rc(rect[0] + rect[2] - 0.02, rect[1] + rect[3] - 0.02)
    return slice(r0, r1 + 1), slice(c0, c1 + 1)


def _strip_clear(q: dict, f: Floor, rect: list[float], side: str, depth: float) -> float:
    """Share of a strip beside a rectangle that is free floor the chair can be on."""
    x, y, w, d = rect
    s = {"left": [x - depth, y, depth, d], "right": [x + w, y, depth, d], "bottom": [x, y - depth, w, depth], "top": [x, y + d, w, depth]}[side]
    if s[0] < f.bounds[0] or s[1] < f.bounds[1] or s[0] + s[2] > f.bounds[2] or s[1] + s[3] > f.bounds[3]:
        return 0.0
    sl = _rect_mask(f, s)
    return float(q["free"][sl].mean()) if q["free"][sl].size else 0.0


# ---------------------------------------------------------------- need-aware rules (Technical Design section 8)

SEATS = ("sofa", "sectional", "loveseat", "armchair")
FRONT_USE = ("desk", "dresser", "wardrobe", "bookshelf", "tv stand")      # pieces you have to get in front of
LOW_TRIP = ("coffee table", "rug")                                         # low pieces a blind walker trips over
TRANSFER_M = 0.76            # ADA 305.3 clear floor space, 30 x 48 in, beside the bed on the transfer side
TRANSFER_SHARE = 0.6         # 48 in of an 80 in bed side
ROUTE_CLEAR_M = 0.3          # blind: nothing within 30 cm of a main route
NEED_LABEL = {"wheelchair": "wheelchair", "blind": "blind", "deaf": "deaf", "older": "older adults"}


def _grid_dist(mask: np.ndarray, sources: list[tuple[int, int]]) -> np.ndarray | None:
    """Walking distance (m) over `mask` from the nearest of several cells; inf where it can't be reached."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    if not sources:
        return None
    ny, nx = mask.shape
    idx = np.arange(ny * nx).reshape(ny, nx)
    rows, cols, w = [], [], []
    for dr, dc, cost in ((0, 1, 1.0), (1, 0, 1.0), (1, 1, 1.4142), (1, -1, 1.4142)):
        if dc >= 0:
            a, b, ia, ib = mask[:ny - dr, :nx - dc], mask[dr:, dc:], idx[:ny - dr, :nx - dc], idx[dr:, dc:]
        else:
            a, b, ia, ib = mask[:ny - dr, -dc:], mask[dr:, :nx + dc], idx[:ny - dr, -dc:], idx[dr:, :nx + dc]
        both = a & b
        rows.append(ia[both])
        cols.append(ib[both])
        w.append(np.full(int(both.sum()), cost * CELL))
    g = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(ny * nx, ny * nx)).tocsr()
    return dijkstra(g, directed=False, indices=[int(idx[r, c]) for r, c in sources], min_only=True).reshape(ny, nx)


def _bed_geometry(rect: list[float], facing) -> tuple[tuple[str, str], float, float]:
    """The bed's two long sides, and how much of each side's length to leave out at the low and high ends (the head
    end keeps 0.5 m for a nightstand; a bed with its long side to the wall keeps 0.25 m at each end)."""
    x, y, w, d = rect
    along_x = w >= d
    sides = ("bottom", "top") if along_x else ("left", "right")
    t_low = t_high = 0.25
    if facing:
        if along_x and facing[0] != 0:
            t_low, t_high = (0.5, 0.0) if facing[0] > 0 else (0.0, 0.5)
        elif not along_x and facing[1] != 0:
            t_low, t_high = (0.5, 0.0) if facing[1] > 0 else (0.0, 0.5)
    return sides, t_low, t_high


def _side_strip(rect: list[float], side: str, depth: float, t_low: float = 0.0, t_high: float = 0.0) -> list[float]:
    x, y, w, d = rect
    if side in ("left", "right"):
        return [x - depth if side == "left" else x + w, y + t_low, depth, max(0.05, d - t_low - t_high)]
    return [x + t_low, y - depth if side == "bottom" else y + d, max(0.05, w - t_low - t_high), depth]


def _side_clearance(free: np.ndarray, f: Floor, rect: list[float], side: str, t_low: float = 0.0, t_high: float = 0.0,
                    share: float = TRANSFER_SHARE, reach: float = 1.5) -> float:
    """Clear floor (m) out from one side of a piece that at least `share` of the side's length has."""
    x, y, w, d = rect
    if side in ("left", "right"):
        ts = np.arange(y + t_low + 0.05, y + d - t_high - 0.04, 0.1)
        start = [(x - 0.03 if side == "left" else x + w + 0.03, t) for t in ts]
        step = (-1, 0) if side == "left" else (1, 0)
    else:
        ts = np.arange(x + t_low + 0.05, x + w - t_high - 0.04, 0.1)
        start = [(t, y - 0.03 if side == "bottom" else y + d + 0.03) for t in ts]
        step = (0, -1) if side == "bottom" else (0, 1)
    if not start:
        return 0.0
    clear = []
    for sx, sy in start:
        k = 0.0
        while k < reach:
            px, py = sx + step[0] * k, sy + step[1] * k
            if not (f.bounds[0] <= px < f.bounds[2] and f.bounds[1] <= py < f.bounds[3]) or not free[f.rc(px, py)]:
                break
            k += CELL
        clear.append(k)
    return float(np.percentile(clear, 100 * (1 - share)))


def _rect_free_share(free: np.ndarray, f: Floor, rect: list[float]) -> float:
    x, y, w, d = rect
    if x < f.bounds[0] or y < f.bounds[1] or x + w > f.bounds[2] or y + d > f.bounds[3]:
        return 0.0
    sl = _rect_mask(f, rect)
    return float(free[sl].mean()) if free[sl].size else 0.0


def _front(facing) -> str | None:
    return {(0, 1): "top", (0, -1): "bottom", (1, 0): "right", (-1, 0): "left"}.get(tuple(facing) if facing else None)


def _overlap(a: list[float], b: list[float]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _segment_hits(rect: list[float], p: tuple[float, float], q: tuple[float, float]) -> bool:
    from shapely.geometry import LineString as _L
    from shapely.geometry import box
    x, y, w, d = rect
    return box(x, y, x + w, y + d).intersects(_L([p, q]))


class _Needs:
    """What the need-aware rules measure once per home, on the entry floor of the empty plan."""

    def __init__(self, plan: Plan, f: Floor, profiles: list[str], empty: dict):
        from shapely.geometry import LineString as _L

        from .unit import _nearest_true
        self.ps, self.f = set(profiles), f
        floor = f.room_idx >= 0
        fix = f.fixture_mask if f.fixture_mask is not None else np.zeros(f.shape, bool)
        self.floor = floor
        self.free = floor & ~f.wall_mask & ~fix
        self.opaque = f.wall_mask | ~floor
        self.entry = plan.entry if plan.entry is not None and plan.entry_floor == f.index else None
        seed = _seed(plan, f, self.free) if self.entry else None
        self.inside = f.xy(*seed) if seed else None                   # a spot just inside the front door
        self.entry_xy = (self.entry["x"], self.entry["y"]) if self.entry else None
        kit = [r for r in f.rooms if r["type"] == "Kitchen"]
        self.kitchen = tuple(kit[0]["centroid"]) if kit else None
        # blind: the routes a resident learns in the empty home (door to bed, bathroom, kitchen; bed to bathroom)
        self.routes = [(p, _L(p["points"])) for p in empty.get("paths", []) if p["floor"] == f.index and len(p["points"]) > 1]
        # older: the night walk to the bathroom door, and the walk in from the front door
        self.d_bath = self.d_entry = None
        if "older" in self.ps:
            walk = self.free & (ndi.distance_transform_edt(self.free) * CELL >= 0.2)
            for d in f.doors:
                walk |= _door_cells(f, d, max(0.1, d["width_m"] / 2 - 0.2), d["wall_thickness"] / 2 + 0.3) & self.free
            bath = {r["id"] for r in f.rooms if r["type"] == "Bathroom"}
            srcs = [c for c in (_nearest_true(walk, f.rc(d["x"], d["y"]), 0.8) for d in f.doors if set(d.get("rooms") or []) & bath) if c]
            self.d_bath = _grid_dist(walk, srcs)
            if seed:
                s = _nearest_true(walk, seed, 1.0)
                self.d_entry = _grid_dist(walk, [s]) if s else None

    # -- measurements used both to choose and to explain
    def sees(self, a, b, opaque: np.ndarray | None = None) -> bool:
        """A clear line of sight from a to b (stops 15 cm short of b, so the door's own frame doesn't count)."""
        opaque = self.opaque if opaque is None else opaque
        dist_ = math.dist(a, b)
        if dist_ < 0.2:
            return True
        n = max(2, int((dist_ - 0.15) / (CELL / 2)))
        ts = np.linspace(0, (dist_ - 0.15) / dist_, n)
        rows = ((a[1] + (b[1] - a[1]) * ts - self.f.bounds[1]) / CELL).astype(int).clip(0, self.f.shape[0] - 1)
        cols = ((a[0] + (b[0] - a[0]) * ts - self.f.bounds[0]) / CELL).astype(int).clip(0, self.f.shape[1] - 1)
        return not opaque[rows, cols].any()

    def sees_entry(self, p, opaque=None) -> bool:
        if not self.entry_xy:
            return False
        return self.sees(p, self.entry_xy, opaque) or (self.inside is not None and self.sees(p, self.inside, opaque))

    def route_gap(self, rect: list[float], label: str) -> float | None:
        """How far (m) a piece is from the nearest main route it isn't part of (the bed and nightstands belong to the bed's routes)."""
        from shapely.geometry import box
        if not self.routes:
            return None
        x, y, w, d = rect
        poly = box(x, y, x + w, y + d)
        mine = ("bed" in label or label == "nightstand")
        gaps = [line.distance(poly) for p, line in self.routes if not (mine and "bed" in (p["from"], p["to"]))]
        return min(gaps) if gaps else None

    def night_walk(self, free: np.ndarray, rect: list[float], facing) -> float | None:
        """Walk (m) to the nearest bathroom door from where you get up: the middle of either long side of the bed
        (the engine measures from the same spots, so the plan and its score agree)."""
        from .unit import _nearest_true
        if self.d_bath is None:
            return None
        x, y, w, d = rect
        spots = [(x - 0.35, y + d / 2), (x + w + 0.35, y + d / 2)] if d >= w else [(x + w / 2, y - 0.35), (x + w / 2, y + d + 0.35)]
        ok = np.isfinite(self.d_bath) & free
        best = None
        for p_ in spots:
            if not (self.f.bounds[0] <= p_[0] < self.f.bounds[2] and self.f.bounds[1] <= p_[1] < self.f.bounds[3]):
                continue
            c = _nearest_true(ok, self.f.rc(*p_), 0.6)
            if c and (best is None or self.d_bath[c] < best):
                best = float(self.d_bath[c])
        return best

    def walk_from_entry(self, rect: list[float]) -> float | None:
        if self.d_entry is None:
            return None
        sl = _rect_mask(self.f, [rect[0] - 0.3, rect[1] - 0.3, rect[2] + 0.6, rect[3] + 0.6])
        vals = self.d_entry[sl]
        vals = vals[np.isfinite(vals)]
        return float(vals.min()) if vals.size else None


def _place_all(plan: Plan, f: Floor, inventory: list[dict], profiles: list[str], prefs: dict, accessible: bool,
               rng: "np.random.Generator | None" = None, needs: "_Needs | None" = None, first: tuple = ()) -> tuple[list, list]:
    """Place the pieces one by one, largest first. accessible=False is an ordinary layout (first wall slot that fits);
    otherwise every selected need's hard rules are kept where any slot allows it, and its soft goals are scored."""
    ps = set(profiles)
    wc, bl, df, ol = ("wheelchair" in ps), ("blind" in ps), ("deaf" in ps), ("older" in ps)
    door_zone = _door_zones(f, 1.2 if wc and accessible else 0.9)
    furn = np.zeros(f.shape, bool)
    tall_mask = np.zeros(f.shape, bool)
    q0 = _quick(plan, f, furn)
    placed, wont, keep_out = [], [], []
    by_type = lambda types: [i for i, r in sorted(enumerate(f.rooms), key=lambda t: -t[1]["area_m2"]) if r["type"] in types]  # noqa: E731
    has_living = bool(by_type(("Living Room",)))
    bedrooms = by_type(("Bedroom",))
    beds_placed = 0
    order = sorted(inventory, key=lambda it: (it["label"] not in first, -(it["w"] * it["d"])))   # `first`: pieces placed before the rest
    for n, it in enumerate(order):
        label = it["label"]
        std = STANDARD.get(label, (it["w"], it["d"], False, "living"))
        group = std[3]
        if accessible and label == "rug" and (ol or bl):
            wont.append({"label": label, "reason": "A rug is a trip hazard for " + ("an older adult" if ol else "someone who is blind") + "; leave it out."})
            continue
        if group == "bedroom":
            if "bed" in label:
                room_ids = bedrooms[beds_placed:beds_placed + 1] or bedrooms[:1]
            else:
                room_ids = [f.room_idx[f.rc(p["rect"][0] + p["rect"][2] / 2, p["rect"][1] + p["rect"][3] / 2)] for p in placed if "bed" in p["label"]][-1:] or bedrooms[:1]
        else:
            types = ROOM_GROUPS[group]
            if has_living and group == "living":
                types = ("Living Room", "Loft", "Bonus Room")
            room_ids = by_type(types)
            if group in ("living", "dining") and not has_living:
                room_ids = room_ids[:2]
        if not room_ids:
            wont.append({"label": label, "reason": f"No {group} space found in this home."})
            continue
        bed = next((p for p in placed if "bed" in p["label"]), None)
        sofa = next((p for p in placed if p["label"] in SEATS), None)
        seat_pt = (sofa["rect"][0] + sofa["rect"][2] / 2, sofa["rect"][1] + sofa["rect"][3] / 2) if sofa else None
        # blind: a table stands against a wall, not in the middle of a room
        free_standing = label == "dining table" and not (accessible and bl)
        cands = _candidates(f, room_ids, it["w"], it["d"], free_standing=free_standing)
        if not accessible:
            # an ordinary layout: any wall slot that fits, with no thought for clear paths
            if rng is not None:
                cands = [cands[i] for i in rng.permutation(len(cands))]
            else:
                cands = cands[:: max(1, len(cands) // 40)]
            best = None
            for rect, facing in cands:
                if _legal(f, rect, placed, door_zone if label != "rug" else None) and not (
                        std[2] and any(math.dist((rect[0] + rect[2] / 2, rect[1] + rect[3] / 2), (w_["x"], w_["y"])) < w_["width_m"] / 2 + 0.4 for w_ in f.windows)):
                    best = (0.0, rect, facing, 0, [])
                    break
        else:
            best = _choose(plan, f, it, label, std, cands, placed, door_zone, furn, tall_mask, keep_out, q0, needs, ps, prefs, bed, sofa, seat_pt)
        if not best:
            smaller = SMALLER.get(label)
            hint = ""
            while smaller:
                sw, sd = STANDARD[smaller][:2]
                if any(_legal(f, r, placed, door_zone) for r, _ in _candidates(f, room_ids, sw, sd, False)):
                    hint = f" A {smaller} ({sw:.2f} x {sd:.2f} m) would fit."
                    break
                smaller = SMALLER.get(smaller)
            wont.append({"label": label, "reason": f"No spot for a {it['w']:.2f} x {it['d']:.2f} m {label} keeps every room reachable and the doors clear.{hint}"})
            continue
        _, rect, facing, viol, broken = best
        if accessible and bl and label in LOW_TRIP and "blind_route" in broken:
            gap = needs.route_gap(rect, label) if needs else None
            wont.append({"label": label, "reason": f"Every spot for the {label} is within 30 cm of a walking route from the front door"
                                                   + (f" (the best is {gap * 100:.0f} cm)" if gap is not None else "") + ", where someone who is blind would walk into it. Leave it out, or use a small side table against a wall."})
            continue
        furn[_rect_mask(f, rect)] = True
        if std[2]:
            tall_mask[_rect_mask(f, rect)] = True
        if "bed" in label:
            beds_placed += 1
        item = {"id": f"p{len(placed) + 1}", "item_id": it.get("id"), "label": label, "floor": f.index, "rect": rect,
                "room": f.rooms[int(f.room_idx[f.rc(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)])]["id"],
                "rotation_deg": 0 if rect[2] >= rect[3] else 90, "tall": std[2], "facing": facing}
        if accessible and broken:
            item["caution"] = _broken_text(broken, label)
        placed.append(item)
        if accessible and "bed" in label and (wc or ol):
            # keep the transfer space beside the bed free of the pieces that come after it
            free_now = needs.free & ~furn if needs else q0["free"] & ~furn
            sides, t0, t1 = _bed_geometry(rect, facing)
            side = max(sides, key=lambda s_: _side_clearance(free_now, f, rect, s_, t0, t1))
            strip = _side_strip(rect, side, TRANSFER_M, t0, t1)
            mask = np.zeros(f.shape, bool)
            if f.bounds[0] <= strip[0] and f.bounds[1] <= strip[1] and strip[0] + strip[2] <= f.bounds[2] and strip[1] + strip[3] <= f.bounds[3]:
                mask[_rect_mask(f, strip)] = True
            # only real floor in the bed's own room: the strip must not reach through a wall into the next room
            bed_room = f.room_idx[f.rc(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)]
            keep_out.append(mask & free_now & (f.room_idx == bed_room))
            item["transfer_side"] = side
    return placed, wont


def _broken_text(broken: list[str], label: str) -> str:
    words = {"blind_route": "it sits within 30 cm of a walking route", "deaf_line": "it stands between the sofa and the front door",
             "deaf_seat": "no sofa spot here has a clear view of the front door", "wc_front": "there's less than 30 inches clear in front of it",
             "transfer": "no spot leaves 30 by 48 inches clear beside the bed", "wc_turn": "it takes the room for a 60 inch turning circle",
             "wc_wide": "it narrows a way through to under 36 inches", "keep_out": "it sits in the space kept clear beside the bed"}
    return f"The best spot for the {label} still breaks a rule: " + "; ".join(words.get(b, b) for b in broken) + "."


def _choose(plan, f, it, label, std, cands, placed, door_zone, furn, tall_mask, keep_out, q0, needs, ps, prefs, bed, sofa, seat_pt):
    """The best slot for one piece: fewest broken hard rules first, then the highest score. The cheap rules are checked
    on every slot; the wheelchair reach check (a flood fill) only on the slots that keep the most rules."""
    wc, bl, df, ol = ("wheelchair" in ps), ("blind" in ps), ("deaf" in ps), ("older" in ps)
    free_now = (needs.free if needs else q0["free"]) & ~furn
    rows = []
    for rect, facing in cands:
        if not _legal(f, rect, placed, door_zone):
            continue
        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        if std[2] and any(math.dist((cx, cy), (w_["x"], w_["y"])) < w_["width_m"] / 2 + 0.4 for w_ in f.windows):
            continue                                       # tall pieces don't block windows
        broken, score = [], 0.0
        if any(k[_rect_mask(f, rect)].any() for k in keep_out):
            broken.append("keep_out")
        # ---- blind: nothing within 30 cm of a learned route; prefer more room than that
        if bl and needs:
            gap = needs.route_gap(rect, label)
            if gap is not None:
                if gap < ROUTE_CLEAR_M:
                    broken.append("blind_route")
                score += 3 * min(gap, 1.0)
        # ---- deaf: the main seat sees the front door; tall pieces stay off that line
        if df and needs and needs.entry_xy:
            if label in SEATS and sofa is None:
                if needs.sees_entry((cx, cy), needs.opaque | tall_mask):
                    score += 6
                    if facing and (needs.entry_xy[0] - cx) * facing[0] + (needs.entry_xy[1] - cy) * facing[1] > 0:
                        score += 4                         # and faces it, not just sees it over a shoulder
                else:
                    broken.append("deaf_seat")
                if needs.kitchen and needs.sees((cx, cy), needs.kitchen):
                    score += 3
            if std[2] and seat_pt and (_segment_hits(rect, seat_pt, needs.entry_xy) or (needs.inside and _segment_hits(rect, seat_pt, needs.inside))):
                broken.append("deaf_line")
            if label == "desk" and facing:
                chair = (cx + facing[0] * 0.6, cy + facing[1] * 0.6)
                view = (-facing[0], -facing[1])                     # sitting at the desk, facing the wall
                dx, dy = needs.entry_xy[0] - chair[0], needs.entry_xy[1] - chair[1]
                ang = math.degrees(math.acos(max(-1, min(1, (view[0] * dx + view[1] * dy) / (math.hypot(dx, dy) or 1)))))
                if needs.sees_entry(chair) and ang <= 120:
                    score += 5
        # ---- wheelchair and older: room to transfer beside the bed; wheelchair: room in front of what you use
        if "bed" in label and (wc or ol):
            sides, t0, t1 = _bed_geometry(rect, facing)
            clear = max(_side_clearance(free_now, f, rect, s_, t0, t1) for s_ in sides)
            if clear < TRANSFER_M:
                broken.append("transfer")
            score += 6 * min(clear, 1.2)
        if wc and label in FRONT_USE and facing:
            front = _front(facing)
            if front and _rect_free_share(free_now, f, _side_strip(rect, front, TRANSFER_M)) < 0.7:
                broken.append("wc_front")
        # ---- older: the shortest night walk from the bed to the bathroom; a seat near the front door
        if ol and needs:
            if "bed" in label:
                walk = needs.night_walk(free_now, rect, facing)
                score -= 2.5 * walk if walk is not None else 20
            if label in SEATS and sofa is None:
                walk = needs.walk_from_entry(rect)
                if walk is not None:
                    score -= 1.0 * walk
        # ---- every need: the pieces' own sense (nightstand by the bed, TV facing the sofa, table near the kitchen)
        if label == "nightstand" and bed:
            bx, by, bw, bd = bed["rect"]
            gap_ = max(bx - (rect[0] + rect[2]), rect[0] - (bx + bw), by - (rect[1] + rect[3]), rect[1] - (by + bd), 0)
            score += 30 - 25 * gap_
        if label == "tv stand" and sofa and facing:
            sx, sy = seat_pt
            dist_ = math.dist((cx, cy), (sx, sy))
            toward = (sx - cx) * facing[0] + (sy - cy) * facing[1]
            score += (10 if 2.0 <= dist_ <= 4.5 else 0) + (14 if toward > 1.5 else -10)
        if label == "coffee table" and sofa and not bl:
            score -= 3 * abs(math.dist((cx, cy), seat_pt) - 1.2)
        if label == "desk":
            wd = min((math.dist((cx, cy), (w_["x"], w_["y"])) for w_ in f.windows), default=9)
            score += 4 if wd < 1.5 else 0
        if label == "dining table":
            kit = [r for r in f.rooms if r["type"] == "Kitchen"]
            score -= 0.8 * min((math.dist((cx, cy), k["centroid"]) for k in kit), default=0)
        rows.append([len(broken), score, rect, facing, broken])
    if not rows:
        return None
    if label == "dining table":
        # by the kitchen (Technical Design section 8): a table in the living room takes the seating's walls
        room_of = lambda r_: f.rooms[int(f.room_idx[f.rc(r_[2][0] + r_[2][2] / 2, r_[2][1] + r_[2][3] / 2)])]["type"]  # noqa: E731
        if any(room_of(r_) in ("Kitchen", "Dining Room") for r_ in rows):
            for r_ in rows:
                if room_of(r_) not in ("Kitchen", "Dining Room"):
                    r_[1] -= 10
    # the best-scoring slots first, fewest broken rules first; after 160 checked, stop at the first one that keeps every room reachable
    rows.sort(key=lambda r: (r[0], -r[1]))
    best = None
    for k, (viol, score, rect, facing, broken) in enumerate(rows):
        if best is not None and (k >= 160 or viol > rows[0][0]):
            break
        trial = furn.copy()
        trial[_rect_mask(f, rect)] = True
        q = _quick(plan, f, trial)
        if not q0["rooms"] <= q["rooms"]:
            continue                                       # a main room became unreachable (every need)
        broken = list(broken)
        lost_turn = len(q0["turn"] - q["turn"])
        if wc and lost_turn:
            broken.append("wc_turn")
        if wc and not q0["wide_rooms"] <= q["wide_rooms"]:
            broken.append("wc_wide")
        total = 30 * q["wide_share"] - (0 if wc else 6 * lost_turn) + score
        if "bed" in label:
            want = prefs.get("transfer_side")
            sides, t0, t1 = _bed_geometry(rect, facing)
            if want in ("left", "right") and sides == ("left", "right") and _strip_clear(q, f, rect, want, 0.92) > 0.9:
                total += 3
        if best is None or (len(broken), -total) < (best[3], -best[0]):
            best = (total, rect, facing, len(broken), broken)
    return best


def _m(v: float) -> str:
    n = max(1, round(v))
    return f"{n} meter{'s' if n != 1 else ''}"


def _explain(plan: Plan, f: Floor, placed: list, needs: _Needs, profiles: list[str], after: dict) -> list[str]:
    """One sentence per piece naming the need it serves, from measurements of the final layout; and the plan's
    reasons, need by need, checked on the engine's analysis of the furnished home."""
    furn = np.zeros(f.shape, bool)
    tall = np.zeros(f.shape, bool)
    for p in placed:
        furn[_rect_mask(f, p["rect"])] = True
        if p.get("tall"):
            tall[_rect_mask(f, p["rect"])] = True
    free = needs.free & ~furn
    seat = next((p for p in placed if p["label"] in SEATS), None)
    seat_pt = (seat["rect"][0] + seat["rect"][2] / 2, seat["rect"][1] + seat["rect"][3] / 2) if seat else None
    facts: dict[str, dict] = {}
    for p in placed:
        label, rect, facing = p["label"], p["rect"], p.get("facing")
        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        say: dict[str, str] = {}
        gap = needs.route_gap(rect, label)
        if gap is not None and gap >= ROUTE_CLEAR_M:
            say["blind"] = (f"Against the wall, {gap:.1f} meters from your walking route from the front door, so the route stays as you learned it."
                            if facing else f"{gap:.1f} meters from your walking route from the front door.")
        if "bed" in label:
            sides, t0, t1 = _bed_geometry(rect, facing)
            clear = max(_side_clearance(free, f, rect, s_, t0, t1) for s_ in sides)
            facts["bed_clear_in"] = {"v": clear / 0.0254}
            if clear >= TRANSFER_M:
                say["wheelchair"] = f"Leaves about {clear / 0.0254:.0f} inches clear along one long side, room to transfer from a wheelchair (30 by 48 inches is the minimum)."
            walk = needs.night_walk(free, rect, facing)
            if walk is not None:
                facts["night_m"] = {"v": walk}
                say["older"] = f"Its open side is about {_m(walk)}' walk from the bathroom door, the shortest night walk this home allows, with {clear / 0.0254:.0f} inches clear to get up."
        elif label in SEATS and p is seat and needs.entry_xy:
            d_entry = math.dist((cx, cy), needs.entry_xy)
            if needs.sees_entry((cx, cy), needs.opaque | tall):
                kit = needs.kitchen and needs.sees((cx, cy), needs.kitchen)
                faces = facing and (needs.entry_xy[0] - cx) * facing[0] + (needs.entry_xy[1] - cy) * facing[1] > 0
                say["deaf"] = (f"{'Faces' if faces else 'Has a clear view of'} the front door, about {_m(d_entry)} away, so you can see who comes in"
                               + (", and the kitchen too." if kit else "."))
            else:
                say["deaf"] = "No sofa spot in this home has a clear view of the front door; a flashing doorbell would help."
            walk = needs.walk_from_entry(rect)
            if walk is not None:
                say["older"] = f"About {_m(walk)}' walk from the front door, a place to sit down when you come in."
            say.setdefault("wheelchair", "Back to the wall, so the middle of the room stays open for turning.")
        elif p.get("tall") and seat_pt and needs.entry_xy:
            if not _segment_hits(rect, seat_pt, needs.entry_xy):
                say["deaf"] = "Kept off the line between the sofa and the front door, so it doesn't block the view."
        if label in FRONT_USE and facing:
            front = _front(facing)
            depth = _side_clearance(free, f, rect, front, share=0.7) if front else 0
            if depth >= TRANSFER_M:
                say["wheelchair"] = f"About {depth / 0.0254:.0f} inches of clear floor in front, to roll up and reach it."
        if label == "desk" and facing and needs.entry_xy:
            chair = (cx + facing[0] * 0.6, cy + facing[1] * 0.6)
            if needs.sees_entry(chair, needs.opaque | tall):
                say["deaf"] = "From the desk chair you can see the front door."
        if label == "dining table":
            open_ = max(_side_clearance(free, f, rect, s_, share=0.5) for s_ in ("left", "right", "bottom", "top"))
            if open_ >= TRANSFER_M:
                say.setdefault("wheelchair", f"About {open_ / 0.0254:.0f} inches clear at its most open side, to pull up to it in a wheelchair.")
        if label == "nightstand":
            say.setdefault("wheelchair", "Beside the bed, within reach.")
            say.setdefault("older", "Beside the bed, within reach at night.")
        if label == "tv stand" and seat_pt:
            say.setdefault("deaf", f"Faces the sofa about {math.dist((cx, cy), seat_pt):.1f} meters away.")
        for need in profiles:
            if need in say:
                p["why"], p["needs"] = say[need], [n for n in profiles if say.get(n) == say[need]] or [need]
                break
        else:
            p["why"], p["needs"] = ("Against a wall." if facing else "Clear of the walls."), []
    # ---- the plan's reasons, need by need, checked on the furnished home
    reasons = []
    ch = after["checks"]
    if "wheelchair" in profiles:
        turn = ch["wheelchair"]["turning_space"]["value"]
        bit = "every main room stays reachable" if ch["wheelchair"]["rooms_reachable"]["value"] >= 1 else "not every main room is reachable (a doorway is too narrow, not the furniture)"
        bit += "; a 60 inch turning circle still fits in the bedroom, bathroom and kitchen" if turn >= 1 else "; a 60 inch turning circle doesn't fit in every bedroom, bathroom and kitchen"
        if "bed_clear_in" in facts:
            bit += f"; the bed leaves about {facts['bed_clear_in']['v']:.0f} inches clear on one side for a transfer"
        reasons.append("Wheelchair: " + bit + ".")
    if "blind" in profiles:
        n_near = sum(1 for p in placed if (lambda g: g is not None and g < ROUTE_CLEAR_M)(needs.route_gap(p["rect"], p["label"])))
        turns = {x["to"]: x["turns"] for x in after["paths"] if x["from"] == "entry"}
        reasons.append("Blind: " + (f"no piece is within 30 cm of the routes from the front door, so they stay the routes of the empty home"
                                    if not n_near else f"{n_near} piece{'s are' if n_near > 1 else ' is'} within 30 cm of a route; no spot avoided it")
                       + (f" ({', '.join(f'{v} turns to the {k}' for k, v in turns.items())})" if turns else "")
                       + "; every piece is against a wall.")
    if "deaf" in profiles:
        seen = ch["deaf"]["entry_visible_from_seat"]["value"] >= 1
        reasons.append("Deaf: " + ("the front door can be seen from the sofa" if seen else "the front door can't be seen from the sofa in this home")
                       + "; tall pieces are kept off that line of sight.")
    if "older" in profiles:
        bit = f"the bed's open side is about {facts['night_m']['v']:.0f} meters from the bathroom door" if "night_m" in facts else "the bed is placed for the shortest walk to the bathroom"
        reasons.append("Older adults: " + bit + "; no rugs; the sofa is placed near the front door to sit down on the way in.")
    return reasons


def plan_furnishing(target: "str | Plan", inventory: list[dict], profiles: list[str], preferences: dict | None = None) -> dict:
    """Place a person's furniture for their needs. `target` is a model id, or a Plan already loaded."""
    prefs = preferences or {}
    plan = target if isinstance(target, Plan) else load_plan(target)
    f = plan.floors[plan.entry_floor]
    inv = []
    for i, it in enumerate(inventory):
        label = (it.get("label") or "furniture").lower()
        std = STANDARD.get(label)
        w, d = (it.get("w"), it.get("d")) if it.get("w") and it.get("d") else (std[:2] if std else (1.0, 0.6))
        inv.append({"id": it.get("id") or f"i{i + 1}", "label": label, "w": round(float(w), 2), "d": round(float(d), 2)})
    empty = analyze(plan, layers=False)
    needs = _Needs(plan, f, profiles, empty)
    placed, wont = _place_all(plan, f, inv, profiles, prefs, accessible=True, needs=needs)
    # placing largest first can leave a later piece no room (a table takes the sofa's only wall, or a bed placed for
    # the night walk leaves the table nowhere to stand): try again with every piece that hasn't fit so far placed
    # first, up to two more times, and keep the layout that fits the most
    first: tuple = ()
    for _ in range(2):
        missing = tuple(w["label"] for w in wont if w["reason"].startswith("No spot"))
        if not missing:
            break
        first = tuple(dict.fromkeys(first + missing))
        p2, w2 = _place_all(plan, f, inv, profiles, prefs, accessible=True, needs=needs, first=first)
        if len(w2) < len(wont):
            placed, wont = p2, w2
    after = analyze(plan, placed, layers=False)
    reasons = _explain(plan, f, placed, needs, profiles, after)
    # The comparison: 15 ordinary layouts of the same pieces (each piece in a random wall slot that fits).
    # We report their range and show the middle one, so the baseline isn't one hand-picked bad layout.
    rng = np.random.default_rng(7)
    trials = []
    for _ in range(15):
        lay, _w = _place_all(plan, f, inv, profiles, prefs, accessible=False, rng=rng)
        trials.append((_combined(analyze(plan, lay, layers=False), profiles), lay))
    trials.sort(key=lambda t: t[0])
    typical = trials[len(trials) // 2][1]
    before = analyze(plan, typical, layers=False)
    ordinary = [round(t[0]) for t in trials]
    return {
        "target_model_id": plan.model_id, "profiles": profiles, "floor": f.index,
        "score_empty": round(_combined(empty, profiles)),
        "score_before": round(_combined(before, profiles)), "score_after": round(_combined(after, profiles)),
        "scores_before": {p: before["scores"][p] for p in profiles}, "scores_after": {p: after["scores"][p] for p in profiles},
        "before_label": "The middle of 15 ordinary layouts (each piece in a random wall slot that fits)",
        "ordinary_scores": ordinary, "ordinary_min": ordinary[0], "ordinary_max": ordinary[-1],
        "ordinary_blocked": sum(1 for v in ordinary if v < round(_combined(after, profiles)) - 10),
        "placements": placed, "typical_placements": typical, "wont_fit": wont,
        "reasons": reasons,
        "problems": [p for p in after["problems"] if p["profile"] in profiles and p["severity"] != "low"],
    }


def inventory_from(model_id: str) -> list[dict]:
    """The movable pieces found in a furnished scan, snapped to standard sizes."""
    out = []
    for it in load_furniture(model_id):
        std = STANDARD.get(it["label"])
        w, d = sorted(it["rect"][2:], reverse=True)
        if std:
            sw, sd = sorted(std[:2], reverse=True)
            # trust the label's standard size unless the scan clearly shows a bigger piece
            w, d = (max(w, sw), max(d, sd)) if w > sw * 1.25 else (sw, sd)
            w, d = (std[0], std[1]) if (w, d) == (sw, sd) else (w, d)
        out.append({"id": it["id"], "label": it["label"], "w": round(w, 2), "d": round(d, 2)})
    return out


if __name__ == "__main__":
    from .unit import render_png
    cmd, mid = sys.argv[1], sys.argv[2]
    profs = ["wheelchair"]
    if cmd == "score":
        r = score_furnishing(mid, profs)
        print("empty", r["combined_empty"], "furnished", r["combined_furnished"])
        for s in r["suggestions"]:
            print(" ", s["text"])
        plan = load_plan(mid)
        render_png(plan, analyze(plan, r["furniture"]), DATA / "furniture" / f"{mid}_furnished.png", "wheelchair", furniture=r["furniture"])
    else:
        inv = inventory_from(sys.argv[3]) if len(sys.argv) > 3 else [{"label": x} for x in ("queen bed", "nightstand", "dresser", "sofa", "tv stand", "dining table", "desk", "armchair")]
        print(inv)
        r = plan_furnishing(mid, inv, profs, {})
        print("empty", r["score_empty"], "typical", r["score_before"], "planned", r["score_after"])
        for p in r["placements"]:
            print(" ", p["label"], p["rect"])
        print("won't fit:", r["wont_fit"])
        print("\n".join(r["reasons"]))
        plan = load_plan(mid)
        render_png(plan, analyze(plan, r["placements"]), DATA / "furniture" / f"{mid}_plan.png", "wheelchair", furniture=r["placements"])
        render_png(plan, analyze(plan, r["typical_placements"]), DATA / "furniture" / f"{mid}_typical.png", "wheelchair", furniture=r["typical_placements"])
