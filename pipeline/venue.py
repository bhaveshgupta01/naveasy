"""The building we are standing in: HackPrinceton's venue, the Julis Romo Rabinowitz Building, Princeton.

A public building is not a home: there is no flat entry door, bedroom, bathroom or kitchen. This module runs the
apartment engine's grid (pipeline/unit.py) in a public-building mode and reports what matters there:

- door clear widths, with doorways too narrow to be real doors (under 0.5 m) set aside as scan artefacts;
- corridor clear widths: the narrowest point on the best route between two places (a widest-path search);
- turning room for a wheelchair (a 1.525 m circle) in the rooms people go to;
- stairs, landings and level changes, measured on Matterport's 3D mesh rather than taken from door sill heights;
- routes from the entrances to named rooms (the classroom, the landings, the stairs).

Nothing is stated as a fact unless it was measured; what the scan can't settle says "needs a site visit".
Our own tape-measure checks live in ground_truth/princeton.json (docs/GROUND_TRUTH.md); once filled, every number
that was checked says so, and disagreements are flagged.

Location: Matterport's geocoordinates for both scans (40.34909, -74.65547) fall inside OpenStreetMap's footprint of
the "Julis Romo Rabinowitz Building" (way 1086964029, Washington Road). The other candidate, 40.3497 -74.6521,
sits among Mudd Library, the Computer Science Building and Sherrerd Hall, about 300 m east. We use OSM's center.

The two scans are one capture: the same 66 scan positions (within 1.4 cm), so one frame. P3S6VUTP3qB carries the
room labels (Hallway, Classroom, two Landings); Matterport offers no mesh download for it, so the 3D mesh comes from
R1FX6EmdcDt, which has more doorways but almost no labels.

    python -m pipeline.venue                 # print the summary, doors, stairs and the main routes
"""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from .clients import ROOT
from .unit import (CELL, DOOR_M, LEGENDS, PROFILES, TURN_M, WALK_M, Plan, _door_axes, _door_cells, _graph_dist, _nearest_true,
                   _path, _room_cell, _status, analyze, load_plan)

DATA = ROOT / "data"
GROUND_TRUTH = ROOT / "ground_truth" / "princeton.json"

VENUE_ID = "princeton_rabinowitz"
NAME = "Julis Romo Rabinowitz Building"
ADDRESS = "Washington Road, Princeton, NJ 08544"
PRIMARY = "P3S6VUTP3qB"          # has the room labels (Hallway, Classroom, Landing)
SECOND = "R1FX6EmdcDt"           # same capture, more doorways, fewer labels
MODELS = (PRIMARY, SECOND)
MESH_FROM = {PRIMARY: SECOND}    # same frame, so R1FX's mesh measures P3S6 too
LAT, LON = 40.3491177, -74.655448     # OSM footprint center (way 1086964029)
ARTEFACT_DOOR_M = 0.5            # narrower than any real door: a break in the scan's wall line
STEP_REAL_M = 0.02               # a mesh level change under 2 cm is flat
RAISED_M = (0.08, 0.3)           # a part of a floor this much higher than the rest is a level change
STAIR_START, STAIR_TOP, STAIR_RISE, MIN_STAIR_CELLS = 0.2, 1.6, 1.0, 800
LIMITS = "ADA 2010: 32 in clear at a door (404.2.3), 36 in along a route (403.5.1), a 60 in turning circle (304.3.1)."
SITE_VISIT = "needs a site visit"

# Vexcel only answers inside these boxes for our key (west, south, east, north), per the organizers on Nov 14.
VEXCEL_BOXES = {
    "princeton": (-74.6943, 40.3157, -74.5981, 40.3714),
    "la": (-119.0134, 33.0945, -116.8949, 34.3154),
    "houston": (-96.2185, 29.0173, -94.5758, 30.3804),
    "northern_california": (-124.7437, 38.0655, -118.9869, 41.9596),
    "gulf_coast": (-88.33, 29.50, -84.80, 30.80),
}


def vexcel_region(lat: float, lon: float) -> str | None:
    """Which allowed Vexcel region a point is in, or None. Check before any Vexcel call: outside, it is refused."""
    for name, (w, s, e, n) in VEXCEL_BOXES.items():
        if w <= lon <= e and s <= lat <= n:
            return name
    return None


def _in(m: float) -> str:
    return f"{m / 0.0254:.0f} inches"


# ---------------------------------------------------------------- ground truth (our tape measurements)

def ground_truth() -> dict:
    """Our own tape-measure checks: {"checked_on": date or null, "items": {key: {"tape_in": number or null, ...}}}."""
    if not GROUND_TRUTH.exists():
        return {"checked_on": None, "items": {}}
    try:
        return json.loads(GROUND_TRUTH.read_text())
    except ValueError:
        return {"checked_on": None, "items": {}}


def evidence(key: str, scan_in: float | None) -> dict:
    """How a measured number stands against our tape: checked and agreeing, checked and disagreeing, or unchecked."""
    gt = ground_truth()
    item = (gt.get("items") or {}).get(key) or {}
    tape = item.get("tape_in")
    if tape is None or scan_in is None:
        return {"source": "3D scan", "checked": False, "scan_in": scan_in,
                "says": "from the scan, not yet checked against a tape measure"}
    diff = scan_in - float(tape)
    agree = abs(diff) <= 1.5        # inches: the scan's noise on a door is about 2 cm
    on = gt.get("checked_on") or "the walk-through"
    return {"source": "3D scan and tape measure", "checked": True, "scan_in": scan_in, "tape_in": tape, "diff_in": round(diff, 1),
            "agrees": agree,
            "says": (f"checked with a tape measure on {on}: {tape} inches" if agree else
                     f"the scan says {scan_in:.0f} inches but our tape measure on {on} read {tape}; we trust the tape")}


# ---------------------------------------------------------------- loading the scan as a public building

def _patched_geometry(raw: dict) -> dict:
    """Matterport's classifier calls the classroom and the landings "Other"; the person who edited the scan named
    them. Use the human label when the classifier has no better word."""
    m = copy.deepcopy(raw)
    for fl in m.get("floors") or []:
        for r in fl.get("rooms") or []:
            human = (r.get("label") or "").strip()
            cls = r.get("classifications") or []
            if human and (not cls or (cls[0] or {}).get("label") in (None, "Other")):
                r["classifications"] = [{"label": human, "confidence": 1.0}] + cls[1:]
    return m


def _mesh(model_id: str):
    from . import fixtures
    for mid in (model_id, MESH_FROM.get(model_id)):
        if not mid:
            continue
        try:
            path = fixtures.mesh_path(mid, fetch=False)
        except Exception:  # noqa: BLE001
            path = None
        if path:
            return fixtures.load_mesh(path), mid
    return None, None


def _up_tris(verts: np.ndarray, faces: np.ndarray, level: float = 0.95) -> np.ndarray:
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return tri[n[:, 2] > level * (np.linalg.norm(n, axis=1) + 1e-12)]


def _height_map(f, tris: np.ndarray, highest: bool) -> np.ndarray:
    """Height of the highest (or lowest) level surface over each grid cell, NaN where there is none."""
    ny, nx = f.shape
    img = Image.new("F", (nx, ny), float("nan"))
    d = ImageDraw.Draw(img)
    x0, y0 = f.bounds[0], f.bounds[1]
    z = tris[:, :, 2].mean(1)
    for k in np.argsort(z if highest else -z):   # drawn last wins
        d.polygon([((p[0] - x0) / CELL, (p[1] - y0) / CELL) for p in tris[k]], fill=float(z[k]))
    return np.array(img)


def floor_heights(plan: Plan, raw: dict, mesh) -> dict[int, float]:
    """Each floor's main walking surface (m, mesh frame): the most common height of level surfaces 1.0 to 1.8 m
    below that floor's scan positions (the camera stands on the floor). Camera height minus 1.37 m without a mesh."""
    panos = {p["id"]: p["position"]["z"] for p in raw.get("panoLocations") or []}
    tris = _up_tris(*mesh) if mesh is not None else None
    out = {}
    for f in plan.floors:
        zs = [panos[p] for r in f.rooms for p in r.get("panos") or [] if p in panos]
        if not zs:
            continue
        cam = float(np.median(zs))
        guess = cam - 1.37
        if tris is not None:
            c = tris.mean(1)
            area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
            x0, y0, x1, y1 = f.bounds
            sel = (c[:, 2] > cam - 1.8) & (c[:, 2] < cam - 1.0) & (c[:, 0] > x0) & (c[:, 0] < x1) & (c[:, 1] > y0) & (c[:, 1] < y1)
            if sel.sum() > 30:
                hist, edges = np.histogram(c[sel, 2], bins=np.arange(cam - 1.8, cam - 1.0, 0.02), weights=area[sel])
                guess = float(edges[int(np.argmax(hist))] + 0.01)
        out[f.index] = round(guess, 3)
    return out


def surface_maps(plan: Plan, mesh, levels: dict[int, float]) -> dict[int, np.ndarray]:
    """Per floor: the height of the walking surface over each cell (lowest level surface within 15 cm below to
    30 cm above the floor's main height), NaN where the mesh has none."""
    out = {}
    if mesh is None:
        return out
    tris = _up_tris(*mesh)
    zc = tris[:, :, 2].mean(1)
    for f in plan.floors:
        z0 = levels.get(f.index)
        if z0 is None:
            continue
        pick = tris[(zc > z0 - 0.15) & (zc < z0 + 0.3)]
        out[f.index] = _height_map(f, pick, False) if len(pick) else np.full(f.shape, np.nan)
    return out


def check_steps(plan: Plan, surfaces: dict[int, np.ndarray]) -> None:
    """For every doorway, compare the floor on both sides in the 3D mesh (a strip 20 to 60 cm out). The scan's own
    sill heights put 13 cm 'steps' at several doors; the mesh says whether the floor really changes level there."""
    for f in plan.floors:
        s = surfaces.get(f.index)
        for d in f.doors:
            d["threshold_scan_m"] = d["threshold_m"]
            d["step_source"] = "scan"
            if s is None:
                continue
            ux, uy, nx_, ny_ = _door_axes(d)
            sides = []
            for sign in (1, -1):
                band = _door_cells(f, {**d, "x": d["x"] + sign * nx_ * 0.4, "y": d["y"] + sign * ny_ * 0.4},
                                   max(0.15, d["width_m"] / 2 - 0.1), 0.2)
                v = s[band & np.isfinite(s)]
                sides.append(float(np.median(v)) if v.size >= 6 else None)
            d["mesh_sides_m"] = [None if v is None else round(v, 3) for v in sides]
            if None in sides:
                continue            # one side unscanned (outside, or behind a closed door): the mesh can't check it
            step = abs(sides[0] - sides[1])
            d["mesh_step_m"] = round(step, 3)
            d["step_source"] = "mesh"
            d["threshold_m"] = round(step, 3) if step >= STEP_REAL_M else 0.0


def raised_zones(plan: Plan, surfaces: dict[int, np.ndarray], levels: dict[int, float]) -> list[dict]:
    """Parts of a floor that sit 8 to 30 cm above the rest (a raised section reached by a step or a ramp)."""
    out = []
    for f in plan.floors:
        s = surfaces.get(f.index)
        if s is None:
            continue
        z0 = levels[f.index]
        floor = (f.room_idx >= 0) & ~f.wall_mask
        up = floor & np.isfinite(s) & (s - z0 > RAISED_M[0]) & (s - z0 < RAISED_M[1])
        up = ndi.binary_opening(up, iterations=3)
        main = floor & np.isfinite(s) & (np.abs(s - z0) < 0.04)
        lab, k = ndi.label(up)
        for c in range(1, k + 1):
            zone = lab == c
            area = zone.sum() * CELL * CELL
            if area < 4:
                continue
            edge = ndi.binary_dilation(zone, iterations=3) & main
            rise = float(np.median(s[zone]) - np.median(s[edge])) if edge.any() else float(np.median(s[zone]) - z0)
            rr, cc = np.nonzero(zone)
            er, ec = np.nonzero(edge) if edge.any() else (rr, cc)
            ex, ey = f.xy(int(np.median(er)), int(np.median(ec)))
            zx, zy = f.xy(int(np.median(rr)), int(np.median(cc)))
            out.append({"floor": f.index, "area_m2": round(area, 1), "rise_m": round(rise, 3), "x": round(zx, 2), "y": round(zy, 2),
                        "edge_x": round(ex, 2), "edge_y": round(ey, 2), "edge_m": round(edge.sum() * CELL / 3, 1)})
    return out


def mark_artefacts(plan: Plan, raw: dict | None = None, levels: dict[int, float] | None = None) -> None:
    """Set aside doorways that can't be what they seem. They stay listed, marked with why, but don't count as doors
    or problems: under 0.5 m wide (a gap where the scan's wall line broke), or with a sill a floor away from the
    floor they are drawn on (a door of the floor below, drawn on the wrong floor). Openings under 1.95 m tall are
    kept but marked low (a pass-through or a window seat, not a doorway)."""
    sills = {}
    for fl in (raw or {}).get("floors") or []:
        for e in fl.get("edges") or []:
            for i, o in enumerate(e.get("openings") or []):
                sills[f"{e['id']}_{i}"] = o.get("lowerElevation")
    for f in plan.floors:
        z0 = (levels or {}).get(f.index)
        for d in f.doors:
            sill = sills.get(d["id"])
            why = None
            if d["width_m"] < ARTEFACT_DOOR_M:
                why = f"{d['width_m'] * 100:.0f} cm wide: too narrow to be a real door"
            elif z0 is not None and sill is not None and abs(sill - z0) > 1.0:
                why = f"its sill is {abs(sill - z0):.1f} m {'below' if sill < z0 else 'above'} this floor: a doorway of another floor drawn here"
            if why:
                d["artefact"], d["artefact_why"] = True, why
                d["is_door"] = False
                d["minor"] = True
            elif 0 < d.get("height_m", 2.1) < 1.95:
                d["low_opening"] = True


def stairs_mask(plan: Plan, mesh, levels: dict[int, float]) -> dict[int, np.ndarray]:
    """Stairs anywhere on a floor (a public building doesn't label a Staircase room): connected patches of level
    surfaces 20 cm to 1.6 m above the floor that climb at least 1 m through several tread heights. Tables and
    chairs top out under a meter; the 14 cm raised part of a floor starts below 20 cm."""
    out = {}
    if mesh is None:
        return out
    tris = _up_tris(*mesh, level=0.8)
    zc = tris[:, :, 2].mean(1)
    cx, cy = tris[:, :, 0].mean(1), tris[:, :, 1].mean(1)
    for f in plan.floors:
        z0 = levels.get(f.index)
        if z0 is None:
            continue
        x0, y0, x1, y1 = f.bounds
        pick = tris[(zc - z0 > STAIR_START) & (zc - z0 < STAIR_TOP) & (cx > x0) & (cx < x1) & (cy > y0) & (cy < y1)]
        if not len(pick):
            out[f.index] = np.zeros(f.shape, bool)
            continue
        hi, lo = _height_map(f, pick, True), _height_map(f, pick, False)
        m = ndi.binary_closing(np.isfinite(hi), iterations=2) & (f.room_idx >= 0) & ~f.wall_mask
        lab, k = ndi.label(m)
        keep = []
        for c in range(1, k + 1):
            cell = lab == c
            if cell.sum() < MIN_STAIR_CELLS:
                continue
            h, low = hi[cell & np.isfinite(hi)], lo[cell & np.isfinite(lo)]
            if len(h) and h.max() - low.min() >= STAIR_RISE and len(np.unique(np.round(h / 0.15))) >= 5:
                keep.append(c)
        out[f.index] = np.isin(lab, keep)
    return out


def _walked_on(f) -> bool:
    return any(r.get("panos") for r in f.rooms)


def _entrances(plan: Plan, geo_offset: float | None) -> list[dict]:
    """The ways in. The scan doesn't say which doors are the building's entrances, so we take, per floor somebody
    walked, the widest real doorway out of the scanned area; both are assumptions to confirm on site."""
    out = []
    for f in plan.floors:
        if not f.doors or not _walked_on(f):
            continue        # Matterport's stub floor below the scan: nobody stood there
        cands = [d for d in f.doors if not d.get("artefact") and (d["exterior"] or "Other" in d["room_types"])]
        if f.index == min(g.index for g in plan.floors if _walked_on(g)):
            pick = max(cands, key=lambda d: d["width_m"], default=None)          # the glazed double doors
        else:
            xs = [d for d in cands if d["exterior"]]
            pick = max(xs, key=lambda d: d["x"], default=None)                   # the doors on the raised side
        if pick:
            side = _compass_side(f, pick, geo_offset)
            out.append({"floor": f.index, "door": pick, "name": f"{side} doors, {f.label.lower()}".strip() if side else f"doors on {f.label.lower()}"})
    return out


def _compass_side(f, d: dict, offset: float | None) -> str:
    if offset is None:
        return ""
    x0, y0, x1, y1 = f.bounds
    dx, dy = d["x"] - (x0 + x1) / 2, d["y"] - (y0 + y1) / 2
    bearing = (math.degrees(math.atan2(dx, dy)) + offset) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((bearing + 22.5) // 45) % 8]


def _geo_offset(model_id: str) -> float | None:
    path = DATA / "matterport_geo" / f"{model_id}.json"
    if path.exists():
        try:
            return (json.loads(path.read_text()) or {}).get("offset_deg")
        except ValueError:
            return None
    return None


@lru_cache(maxsize=4)
def venue_plan(model_id: str = PRIMARY) -> Plan:
    """The scan as a public building: human room labels, artefact doorways set aside, steps checked on the mesh,
    stairs found anywhere, raised parts of floors found, the entrances chosen. Cached."""
    from .clients import matterport
    raw = matterport.geometry(model_id)
    plan = load_plan(model_id, _patched_geometry(raw), fixtures=False)
    mesh, mesh_from = _mesh(model_id)
    levels = floor_heights(plan, raw, mesh)
    surfaces = surface_maps(plan, mesh, levels)
    mark_artefacts(plan, raw, levels)
    check_steps(plan, surfaces)
    for i, m in stairs_mask(plan, mesh, levels).items():
        plan.floors[i].stairs_mask = m
    for f in plan.floors:
        f.fixture_mask = np.zeros(f.shape, bool)
        for d in f.doors:
            d["entry"] = False
    plan.fixtures = "mesh" if mesh is not None else None
    offset = _geo_offset(model_id) if _geo_offset(model_id) is not None else _geo_offset(PRIMARY)
    ents = _entrances(plan, offset)
    for e in ents:
        e["door"]["entry"] = True
    if ents:
        plan.entry_floor, plan.entry = ents[0]["floor"], ents[0]["door"]
    plan.venue = {"levels": levels, "mesh_from": mesh_from, "entrances": ents, "raised": raised_zones(plan, surfaces, levels),  # type: ignore[attr-defined]
                  "geo_offset_deg": offset}
    return plan


# ---------------------------------------------------------------- measuring

def _free(f) -> np.ndarray:
    stairs = f.stairs_mask if f.stairs_mask is not None else np.zeros(f.shape, bool)
    return (f.room_idx >= 0) & ~f.wall_mask & ~stairs


def _portals(f, mask: np.ndarray, free: np.ndarray, reach: float) -> np.ndarray:
    """Let routes through every real doorway (door widths are judged on their own measured width)."""
    out = mask.copy()
    for d in f.doors:
        if d.get("artefact"):
            continue
        out |= _door_cells(f, d, max(CELL, d["width_m"] / 2 - 0.1), d["wall_thickness"] / 2 + reach + CELL) & free
    return out


@lru_cache(maxsize=8)
def _dist(model_id: str, floor: int) -> np.ndarray:
    f = venue_plan(model_id).floors[floor]
    return ndi.distance_transform_edt(_free(f)) * CELL


def places(model_id: str = PRIMARY) -> dict[str, dict]:
    """Named places a person can ask for: entrances, rooms by label, the stairs, the raised part of a floor."""
    plan = venue_plan(model_id)
    out: dict[str, dict] = {}
    for i, e in enumerate(plan.venue["entrances"]):
        d = e["door"]
        key = "entrance" if i == 0 else f"entrance {i + 1}"
        out[key] = {"kind": "entrance", "name": e["name"], "floor": e["floor"], "x": d["x"], "y": d["y"], "door": d}
    seen: dict[str, int] = {}
    for f in plan.floors:
        for r in f.rooms:
            if r["type"] in ("Other", "Hallway", "Void") or r["area_m2"] < 2:
                continue
            t = r["type"].lower()
            seen[t] = seen.get(t, 0) + 1
            key = t if seen[t] == 1 else f"{t} {seen[t]}"
            out[key] = {"kind": "room", "name": f"the {t}", "floor": f.index, "x": r["centroid"][0], "y": r["centroid"][1], "room": r}
        if f.stairs_mask is not None and f.stairs_mask.any():
            lab, k = ndi.label(f.stairs_mask)
            for c in range(1, k + 1):
                rr, cc = np.nonzero(lab == c)
                x, y = f.xy(int(np.median(rr)), int(np.median(cc)))
                key = "stairs" if "stairs" not in out else f"stairs {c}"
                out[key] = {"kind": "stairs", "name": "the stairs", "floor": f.index, "x": round(x, 2), "y": round(y, 2),
                            "area_m2": round(len(rr) * CELL * CELL, 1)}
    return out


def _bottleneck(f, dist: np.ndarray, free: np.ndarray, a: tuple[int, int], b: tuple[int, int]) -> float:
    """The widest clear width w such that a and b stay connected through floor at least w wide (doorways count as
    open; they are reported on their own). Binary search on the distance transform."""
    lo, hi = 0.3, 4.0
    for _ in range(14):
        mid = (lo + hi) / 2
        m = _portals(f, dist >= mid / 2, free, mid / 2)
        lab, _ = ndi.label(m, structure=np.ones((3, 3)))
        sa, sb = _nearest_true(m, a, 0.8), _nearest_true(m, b, 0.8)
        if sa and sb and lab[sa] == lab[sb] and lab[sa] > 0:
            lo = mid
        else:
            hi = mid
    return round(lo, 2)


def _words(points: list) -> str:
    """Turn-by-turn for a path: small wiggles merged so only real turns are spoken."""
    legs = []
    for a, b in zip(points, points[1:]):
        v = [b[0] - a[0], b[1] - a[1], math.dist(a, b)]
        if legs:
            p = legs[-1]
            ang = math.degrees(math.atan2(p[0] * v[1] - p[1] * v[0], p[0] * v[0] + p[1] * v[1]))
            if abs(ang) < 35 or v[2] < 1.0:
                legs[-1] = [p[0] + v[0], p[1] + v[1], p[2] + v[2]]
                continue
        legs.append(v)
    if not legs:
        return ""
    out = [f"go straight about {legs[0][2]:.0f} meters"]
    for p, v in zip(legs, legs[1:]):
        ang = math.degrees(math.atan2(p[0] * v[1] - p[1] * v[0], p[0] * v[0] + p[1] * v[1]))
        out.append(f"turn {'left' if ang > 0 else 'right'} and go about {v[2]:.0f} meters")
    return ", then ".join(out)


def _leg(model_id: str, floor: int, a_xy, b_xy, profile: str) -> dict | None:
    """One floor's part of a route: path points, meters, turns, the narrowest corridor and the doors passed."""
    plan = venue_plan(model_id)
    f = plan.floors[floor]
    free = _free(f)
    dist = _dist(model_id, floor)
    reach = DOOR_M / 2 if profile == "wheelchair" else 0.2
    walk = _portals(f, free & (dist >= reach), free, reach)
    a, b = _nearest_true(walk, f.rc(*a_xy), 1.5), _nearest_true(walk, f.rc(*b_xy), 2.5)
    if not a or not b:
        return None
    dd, pred = _graph_dist(walk, a)
    if not np.isfinite(dd[b]):
        return {"floor": floor, "connected": False, "narrowest_m": _bottleneck(f, dist, free, a, b)}
    pts, turns = _path(f, pred, a, b, walk)
    passed = []
    route = _dense(f, pts)
    for d in f.doors:
        if d.get("artefact") or not d["is_door"]:
            continue
        cells = _door_cells(f, d, d["width_m"] / 2, d["wall_thickness"] / 2 + 0.3)
        if any(cells[r, c] for r, c in route):
            passed.append(d)
    return {"floor": floor, "connected": True, "meters": round(float(dd[b]), 1), "turns": turns, "points": pts,
            "narrowest_m": _bottleneck(f, dist, free, a, b), "doors": passed, "words": _words(pts)}


def _dense(f, pts: list) -> list[tuple[int, int]]:
    out = []
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.dist(a, b) / CELL))
        out += [f.rc(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(n + 1)]
    return out


def _door_fact(d: dict, f) -> dict:
    key = f"door_{d['id']}"
    status = "artefact" if d.get("artefact") else _status(d)
    return {"key": key, "floor": f.index, "floor_label": f.label, "x": d["x"], "y": d["y"], "rooms": d["room_types"],
            "scan_width_in": round(d["width_m"] / 0.0254, 1), "clear_in": d["est_clear_in"], "status": status,
            "entrance": bool(d.get("entry")), "evidence": evidence(key, d["est_clear_in"]),
            "step": _step_fact(d)}


def _step_fact(d: dict) -> dict | None:
    scan = d.get("threshold_scan_m") or 0
    if d.get("step_source") == "mesh":
        if d.get("mesh_step_m", 0) >= STEP_REAL_M:
            return {"cm": round(d["mesh_step_m"] * 100), "source": "3D mesh", "confirmed": False,
                    "says": f"the 3D mesh shows the floor about {d['mesh_step_m'] * 100:.0f} cm higher on one side; step or ramp {SITE_VISIT}"}
        if scan > 0.03:
            return {"cm": 0, "source": "3D mesh", "confirmed": False,
                    "says": f"the scan's sill height suggests a {scan * 100:.0f} cm step, but the 3D mesh shows the floor level on both sides; likely a scan artefact, {SITE_VISIT} to be sure"}
        return None
    if scan > 0.03:
        return {"cm": round(scan * 100), "source": "scan sill height", "confirmed": False,
                "says": f"the scan suggests a step of about {scan * 100:.0f} cm; one side isn't in the 3D mesh, so {SITE_VISIT}"}
    return None


# ---------------------------------------------------------------- the analysis in the engine's shape

def analyze_public(plan: Plan, layers: bool = True) -> dict:
    """Public-building checks in the same shape as unit.analyze(), so unit_json and the floor plan views render it.
    Wheelchair heat: reachable floor from each floor's entrance (an upper floor counts as reached by a step-free
    way up, which the scan can't show). Scores measure only what the scan shows inside, per need."""
    ents = plan.venue["entrances"]
    by_floor = {e["floor"]: e for e in ents}
    base = None
    layers_by_floor = {}
    for f in plan.floors:
        e = by_floor.get(f.index)
        if e:
            plan.entry_floor, plan.entry = f.index, e["door"]
        res = analyze(plan, layers=layers)
        if base is None:
            base = res
        if layers:
            layers_by_floor[f.index] = res["floors"][f.index]
    if ents:
        plan.entry_floor, plan.entry = ents[0]["floor"], ents[0]["door"]
    out = {"model_id": plan.model_id, "furnished": False, "public": True, "floors": [layers_by_floor[f.index] for f in plan.floors] if layers else [],
           "turning_circles": [], "paths": [], "problems": [], "checks": {}, "scores": {}, "view_points": None}
    P = out["problems"]

    # doors
    real = [(f, d) for f in plan.floors for d in f.doors if d["is_door"] and not d.get("artefact") and _walked_on(f)]
    for f in plan.floors:
        for d in f.doors:
            if d.get("artefact"):
                P.append({"id": f"artefact_{d['id']}", "kind": "scan_artefact", "severity": "low", "profile": "wheelchair", "floor": f.index,
                          "x": d["x"], "y": d["y"], "text": f"A {d['width_m'] * 100:.0f} cm gap in the scan's wall line here is too narrow to be a real door; {SITE_VISIT} to see what is there."})
    for f, d in real:
        st = _status(d)
        if st != "pass":
            P.append({"id": f"door_{d['id']}", "kind": "narrow_door", "severity": "high" if st == "fail" else "medium", "profile": "wheelchair",
                      "floor": f.index, "x": d["x"], "y": d["y"],
                      "text": f"Doorway on {f.label.lower()} ({', '.join(t.lower() for t in d['room_types']) or 'edge of the scan'}): about {d['est_clear_in']:.0f} inches clear; a wheelchair needs 32. {evidence('door_' + d['id'], d['est_clear_in'])['says'].capitalize()}."})
        sf = _step_fact(d)
        if sf:
            for prof, sev in (("wheelchair", "medium"), ("older", "medium"), ("blind", "low")):
                P.append({"id": f"step_{prof}_{d['id']}", "kind": "threshold", "severity": sev if sf["cm"] else "low", "profile": prof, "floor": f.index,
                          "x": d["x"], "y": d["y"], "text": f"Doorway on {f.label.lower()}: {sf['says']}."})
    # raised parts of floors
    for z in plan.venue["raised"]:
        for prof in ("wheelchair", "older", "blind"):
            P.append({"id": f"raised_{prof}_{z['floor']}_{int(z['x'])}", "kind": "level_change", "severity": "medium", "profile": prof, "floor": z["floor"],
                      "x": z["edge_x"], "y": z["edge_y"],
                      "text": f"Part of {plan.floors[z['floor']].label.lower()} (about {z['area_m2']:.0f} square meters) is about {z['rise_m'] * 100:.0f} cm higher than the rest, measured on the 3D mesh. Whether a step or a ramp joins them {SITE_VISIT}."})
    # stairs and floors
    pl = places(plan.model_id)
    stairs = [p for p in pl.values() if p["kind"] == "stairs"]
    walked = [f for f in plan.floors if _walked_on(f)]
    for s in stairs:
        for prof, sev in (("wheelchair", "high"), ("older", "medium"), ("blind", "medium")):
            P.append({"id": f"stairs_{prof}_{s['floor']}_{int(s['x'])}", "kind": "stairs", "severity": sev, "profile": prof, "floor": s["floor"], "x": s["x"], "y": s["y"],
                      "text": f"Stairs here, found in the 3D mesh (about {s['area_m2']:.0f} square meters of treads) between {plan.floors[s['floor']].label.lower()} and the floor above."})
    if len(walked) > 1:
        P.append({"id": "elevator_unknown", "kind": "vertical_access", "severity": "high", "profile": "wheelchair", "floor": walked[0].index,
                  "x": walked[0].rooms[0]["centroid"][0], "y": walked[0].rooms[0]["centroid"][1],
                  "text": f"This scan covers {len(walked)} floors joined by stairs; it doesn't show an elevator. Whether there is a step-free way between floors {SITE_VISIT}."})

    # routes from the entrance to each named room
    targets = [(k, p) for k, p in pl.items() if p["kind"] in ("room",)]
    for k, p in targets:
        r = inside_route("entrance", k, "wheelchair", plan.model_id)
        for leg in r.get("legs", []):
            if leg.get("points"):
                out["paths"].append({"from": "entrance" if leg is r["legs"][0] else leg.get("from", "landing"), "to": k, "floor": leg["floor"], "meters": leg["meters"],
                                     "turns": leg["turns"], "points": leg["points"], "narrowest_in": round(leg["narrowest_m"] / 0.0254)})
    # turning room in the rooms people go to
    turn_ok = []
    for f in walked:
        dist = _dist(plan.model_id, f.index)
        for i, r in enumerate(f.rooms):
            if r["type"] in ("Classroom", "Landing", "Hallway") or (r["type"] == "Other" and r["area_m2"] > 20):
                m = (dist >= TURN_M / 2) & (f.room_idx == i)
                ok = bool(m.any())
                turn_ok.append(ok)
                if ok:
                    rr, cc = np.nonzero(m)
                    j = int(np.argmax(dist[rr, cc]))
                    x, y = f.xy(rr[j], cc[j])
                    out["turning_circles"].append({"floor": f.index, "room": r["id"], "x": round(x, 2), "y": round(y, 2), "r": TURN_M / 2})
                elif r["area_m2"] > 4:
                    P.append({"id": f"turn_{r['id']}", "kind": "no_turning_space", "severity": "medium", "profile": "wheelchair", "floor": f.index,
                              "x": r["centroid"][0], "y": r["centroid"][1], "text": f"No 60 inch turning circle fits in the {r['type'].lower()} between the walls the scan records."})

    narrow_routes = [p["narrowest_in"] for p in out["paths"]]
    share = lambda xs, empty=1.0: (sum(xs) / len(xs)) if xs else empty  # noqa: E731
    door_vals = [{"pass": 1.0, "tight": 0.5, "fail": 0.0}[_status(d)] for _, d in real]
    steps = [d for _, d in real if _step_fact(d) and _step_fact(d)["cm"]]
    wc = {"doors": (0.3, share(door_vals)),
          "corridors": (0.25, share([1.0 if w >= 36 else 0.5 if w >= 32 else 0.0 for w in narrow_routes], 0.5)),
          "turning_space": (0.15, share(turn_ok, 0.5)),
          "level_changes": (0.15, max(0.0, 1 - 0.25 * (len(steps) + len(plan.venue["raised"])))),
          "between_floors": (0.15, 0.5 if len(walked) > 1 else 1.0)}
    older = {"between_floors": (0.4, 0.5 if len(walked) > 1 else 1.0), "level_changes": (0.3, wc["level_changes"][1]),
             "route_length": (0.3, share([1.0 if p["meters"] <= 60 else 0.5 for p in out["paths"]], 0.5))}
    blind = {"stairs_and_level_changes": (0.5, max(0.0, 1 - 0.2 * (len(stairs) + len(steps) + len(plan.venue["raised"])))),
             "simple_routes": (0.5, share([1.0 if p["turns"] <= 4 else 0.5 for p in out["paths"]], 0.5))}
    deaf = {"open_sightlines": (1.0, 0.5)}
    conf = {"wheelchair": "medium", "older": "low", "blind": "low", "deaf": "low"}
    for prof, checks in (("wheelchair", wc), ("older", older), ("blind", blind), ("deaf", deaf)):
        out["checks"][prof] = {k: {"weight": w, "value": round(float(v), 2)} for k, (w, v) in checks.items()}
        out["scores"][prof] = {"score": round(100 * sum(w * v for w, v in checks.values())), "confidence": conf[prof]}
    out["score_note"] = ("Public-building checks measured inside this scan only: doors, corridor widths on the routes from the entrance, "
                         "turning room, level changes and stairs. Not a home score and not a certification; the outside approach, "
                         "restrooms, elevators and signage are not checked.")
    out["stats"] = {
        "doorways": len(real), "doorways_pass": sum(1 for _, d in real if _status(d) == "pass"),
        "narrowest_main_door_in": min((d["est_clear_in"] for _, d in real), default=None),
        "reach36_share": _reach_share(out, plan),
        "reach32_share": None, "bed_to_bathroom_m": None, "visible_share": None, "floors": len(walked),
        "windows": sum(len(f.windows) for f in plan.floors), "area_m2": round(sum(r["area_m2"] for f in walked for r in f.rooms), 1),
        "entry_found": bool(ents), "artefact_doorways": sum(1 for f in plan.floors for d in f.doors if d.get("artefact")),
        "narrowest_route_in": min(narrow_routes, default=None), "stairs": len(stairs), "raised_zones": len(plan.venue["raised"]),
    }
    sev = {"high": 0, "medium": 1, "low": 2}
    P.sort(key=lambda p: (sev[p["severity"]], p["profile"]))
    return out


def _reach_share(res: dict, plan: Plan) -> float | None:
    if not res["floors"]:
        return None
    good = sum(int((fl["heat"]["wheelchair"] == 4).sum()) for fl in res["floors"])
    floor = sum(int((fl["heat"]["wheelchair"] >= 2).sum()) for fl in res["floors"])
    return round(good / floor, 2) if floor else None


# ---------------------------------------------------------------- what the agent and the voice flow call

def _find(name: str, model_id: str) -> tuple[str, dict] | None:
    pl = places(model_id)
    key = (name or "").lower().strip().removeprefix("the ")
    if key in pl:
        return key, pl[key]
    for k, p in pl.items():
        if key and (key in k or k in key or key in p["name"]):
            return k, p
    if key in ("door", "front door", "main entrance", "way in", "entry"):
        return "entrance", pl.get("entrance")
    return None


def inside_route(from_place: str = "entrance", to_place: str = "classroom", profile: str = "wheelchair", model_id: str = PRIMARY) -> dict:
    """How to get from one named place to another inside the venue, for a need. Returns legs per floor with meters,
    turns, turn-by-turn words, the narrowest corridor and the doors passed, each with its evidence, and what needs a
    site visit (between floors, the scan shows stairs but no elevator)."""
    plan = venue_plan(model_id)
    a, b = _find(from_place, model_id), _find(to_place, model_id)
    if not a or not b or not a[1] or not b[1]:
        return {"error": f"I don't know a place called {from_place if not a else to_place} in this scan.", "places": sorted(places(model_id))}
    (ka, pa), (kb, pb) = a, b
    out = {"from": pa["name"], "to": pb["name"], "profile": profile, "legs": [], "needs_site_visit": [], "limits": LIMITS}
    if pa["floor"] == pb["floor"]:
        leg = _leg(model_id, pa["floor"], (pa["x"], pa["y"]), (pb["x"], pb["y"]), profile)
        legs = [leg] if leg else []
    else:
        up = pb["floor"] > pa["floor"]
        stairs = [p for p in places(model_id).values() if p["kind"] == "stairs" and p["floor"] == min(pa["floor"], pb["floor"])]
        landing = [p for p in places(model_id).values() if p["kind"] == "room" and p["room"]["type"] == "Landing" and p["floor"] == max(pa["floor"], pb["floor"])]
        s = min(stairs, key=lambda p: math.dist((p["x"], p["y"]), (pa["x"], pa["y"])), default=None)
        top = min(landing, key=lambda p: math.dist((p["x"], p["y"]), (pb["x"], pb["y"])), default=None)
        legs = []
        if s:
            leg = _leg(model_id, pa["floor"], (pa["x"], pa["y"]), (s["x"], s["y"]), "walk")
            if leg:
                leg["to"] = "the foot of the stairs" if up else "the stairs"
                legs.append(leg)
        out["between_floors"] = {"from_floor": plan.floors[pa["floor"]].label, "to_floor": plan.floors[pb["floor"]].label,
                                 "rise_m": round(abs(plan.venue["levels"].get(pb["floor"], 0) - plan.venue["levels"].get(pa["floor"], 0)), 1),
                                 "stairs_found": bool(s), "elevator_in_scan": False}
        out["needs_site_visit"].append(f"The {pb['name'].removeprefix('the ')} is on {plan.floors[pb['floor']].label.lower()}, about "
                                       f"{out['between_floors']['rise_m']:.0f} meters {'up' if up else 'down'}. The scan shows stairs between the floors and no elevator; "
                                       f"whether there is a step-free way {SITE_VISIT}.")
        if top or True:
            start = top or next(iter(e for e in plan.venue["entrances"] if e["floor"] == pb["floor"]), None)
            if start:
                sx, sy = (start["x"], start["y"]) if "x" in start else (start["door"]["x"], start["door"]["y"])
                leg = _leg(model_id, pb["floor"], (sx, sy), (pb["x"], pb["y"]), profile)
                if leg:
                    leg["from"] = "the landing at the top of the stairs" if top else start.get("name")
                    legs.append(leg)
    for leg in legs:
        if not leg.get("connected", True):
            out["needs_site_visit"].append(f"On {plan.floors[leg['floor']].label.lower()} the scan shows no way through at least 32 inches wide; "
                                           f"the narrowest point is about {leg['narrowest_m'] / 0.0254:.0f} inches.")
        leg["narrowest_in"] = round(leg["narrowest_m"] / 0.0254)
        leg["narrowest_evidence"] = evidence(f"corridor_{ka}_{kb}_{leg['floor']}", leg["narrowest_in"])
        f = plan.floors[leg["floor"]]
        leg["doors"] = [_door_fact(d, f) for d in leg.get("doors", [])]
        leg["floor_label"] = f.label
    out["legs"] = legs
    out["meters"] = round(sum(leg.get("meters", 0) for leg in legs), 1)
    words = []
    for leg in legs:
        if leg.get("words"):
            words.append(f"On {leg['floor_label'].lower()}{' from ' + leg['from'] if leg.get('from') else ''}: {leg['words']}"
                         + (f", to {leg['to']}" if leg.get("to") else f", to {pb['name']}") + ".")
            if leg["narrowest_in"]:
                words.append(f"The narrowest point on the way is about {leg['narrowest_in']} inches"
                             f"{', wide enough for a wheelchair' if leg['narrowest_in'] >= 36 else ', tight for a wheelchair' if leg['narrowest_in'] >= 32 else ', too narrow for a wheelchair'}; {leg['narrowest_evidence']['says']}.")
    out["spoken"] = " ".join(words + out["needs_site_visit"])
    return out


def door_widths(near: str | None = None, model_id: str = PRIMARY) -> dict:
    """Every real doorway (clear width = scan width minus 2 inches, against ADA's 32 inches), the artefact gaps, and
    the steps; with near="classroom" (or any place name) only the doors of that place, nearest first."""
    plan = venue_plan(model_id)
    facts = [_door_fact(d, f) for f in plan.floors if _walked_on(f) for d in f.doors]
    if near:
        p = _find(near, model_id)
        if p and p[1]:
            pl = p[1]
            facts = [x for x in facts if x["floor"] == pl["floor"]]
            facts.sort(key=lambda x: math.dist((x["x"], x["y"]), (pl["x"], pl["y"])))
            facts = facts[:4]
    real = [x for x in facts if x["status"] != "artefact"]
    return {"doors": real, "artefacts": [x for x in facts if x["status"] == "artefact"],
            "narrowest_in": min((x["clear_in"] for x in real), default=None), "limits": LIMITS,
            "how_measured": "Width of the doorway in Matterport's wall graph minus 2 inches, because the scan measures the frame, not the clear space with the door open."}


def stairs(model_id: str = PRIMARY) -> dict:
    """Where the stairs are (found in the 3D mesh), the landings, the raised part of a floor, and the floors."""
    plan = venue_plan(model_id)
    pl = places(model_id)
    lv = plan.venue["levels"]
    return {"stairs": [{"floor": plan.floors[p["floor"]].label, "x": p["x"], "y": p["y"], "area_m2": p["area_m2"], "source": "3D mesh"} for p in pl.values() if p["kind"] == "stairs"],
            "landings": [{"floor": plan.floors[p["floor"]].label, "x": p["x"], "y": p["y"], "area_m2": p["room"]["area_m2"], "source": "room label in the scan"}
                         for p in pl.values() if p["kind"] == "room" and p["room"]["type"] == "Landing"],
            "floors": [{"label": plan.floors[i].label, "height_m": z} for i, z in sorted(lv.items())],
            "raised_parts": [{**z, "floor": plan.floors[z["floor"]].label, "says": f"about {z['rise_m'] * 100:.0f} cm higher than the rest of the floor; step or ramp {SITE_VISIT}"} for z in plan.venue["raised"]],
            "elevator": f"not in the scan; {SITE_VISIT}"}


def venue_summary(model_id: str = PRIMARY) -> dict:
    """The venue in one record: what was scanned, the measured headline numbers, and what needs a site visit."""
    plan = venue_plan(model_id)
    res = analyze_public(plan, layers=False)
    d = door_widths(model_id=model_id)
    r = inside_route("entrance", "classroom", "wheelchair", model_id)
    gt = ground_truth()
    return {"id": VENUE_ID, "name": NAME, "address": ADDRESS, "lat": LAT, "lon": LON, "scan": model_id, "mesh_from": plan.venue["mesh_from"],
            "floors": [f.label for f in plan.floors if _walked_on(f)], "area_m2": res["stats"]["area_m2"],
            "entrances": [{"name": e["name"], "floor": plan.floors[e["floor"]].label, "clear_in": e["door"]["est_clear_in"], "assumed": True} for e in plan.venue["entrances"]],
            "doorways": res["stats"]["doorways"], "doorways_pass": res["stats"]["doorways_pass"], "narrowest_door_in": d["narrowest_in"],
            "artefact_gaps": res["stats"]["artefact_doorways"], "stairs": stairs(model_id), "route_to_classroom": r.get("spoken"),
            "scores": res["scores"], "score_note": res["score_note"],
            "problems": [p["text"] for p in res["problems"] if p["profile"] == "wheelchair" and p["severity"] != "low"][:8],
            "ground_truth": {"checked_on": gt.get("checked_on"), "items_checked": sum(1 for v in (gt.get("items") or {}).values() if v.get("tape_in") is not None)},
            "location_evidence": "Matterport's coordinates fall inside OpenStreetMap's footprint of the Julis Romo Rabinowitz Building (way 1086964029).",
            "legend": LEGENDS["wheelchair"]}


def building_record() -> dict:
    """The venue as a row of the building table (the shape main.buildings() serves), so the app can open it."""
    import h3
    plan = venue_plan(PRIMARY)
    res = analyze_public(plan, layers=False)
    units = []
    for mid in MODELS:
        try:
            p = venue_plan(mid)
            rr = res if mid == PRIMARY else analyze_public(p, layers=False)
            units.append({"model_id": mid, "caption": "Labeled scan (classroom, landings)" if mid == PRIMARY else "Second processing of the same scan (more doorways)",
                          "status": "measured", "dwelling": True, "scores": rr["scores"], "stats": rr["stats"]})
        except Exception:  # noqa: BLE001 - a second scan that won't load must not hide the first
            continue
    floors = len([f for f in plan.floors if _walked_on(f)])
    return {"id": VENUE_ID, "kind": "public_building", "name": f"{NAME} (HackPrinceton venue)", "address": ADDRESS, "city": "princeton",
            "lat": LAT, "lon": LON, "h3": h3.latlng_to_cell(LAT, LON, 9), "stories": floors + 1, "elevator": None, "walkup": None,
            "year_built": None, "rent_min": None, "rent_max": None, "units_scanned": len(units), "public": True,
            "components": {"vertical_access": {"value": 0.5, "why": f"{floors} scanned floors joined by stairs; no elevator in the scan ({SITE_VISIT})"},
                           "approach": {"value": 0.5, "why": "outside approach not checked inside the scan"},
                           "listing_evidence": {"value": 0.5, "why": "a university building, not a listing"},
                           "age": {"value": 0.5, "why": "construction year not in our data"}},
            "scores": res["scores"], "building_scores": res["scores"], "score_note": res["score_note"],
            "inside": {"units_measured": len(units), **{p: {"best": res["scores"][p]["score"], "best_model": PRIMARY, "share_pass": 1.0 if res["scores"][p]["score"] >= 75 else 0.0,
                                                            "confidence": res["scores"][p]["confidence"]} for p in PROFILES}},
            "units": units}


# ---------------------------------------------------------------- outside: from the station or parking to the door

OUTSIDE_ORIGINS = {
    # OpenStreetMap: Princeton station ("the Dinky"), and the Lot 21 garage / the Fine Hall lot area west of Washington Road
    "princeton station": (40.34322, -74.65987, "Princeton station (the Dinky)"),
    "dinky": (40.34322, -74.65987, "Princeton station (the Dinky)"),
    "parking": (40.34601, -74.65215, "the parking area on the east side of Washington Road"),
    "nassau hall": (40.34871, -74.65930, "Nassau Hall"),
}


def outside_route(origin_name: str = "princeton station", profile: str = "wheelchair") -> dict:
    """Walking or rolling route from a named origin to the venue's doors, via Valhalla, with crossings from our
    Princeton OpenStreetMap cache and slope from Vexcel surface heights where the key allows it."""
    from . import routes as R
    key = origin_name.lower().strip().removeprefix("the ")
    o = OUTSIDE_ORIGINS.get(key) or next((v for k, v in OUTSIDE_ORIGINS.items() if k in key or key in k), None)
    if not o:
        return {"error": f"I don't know where {origin_name} is.", "known": sorted(OUTSIDE_ORIGINS)}
    r = R.build_route("princeton", (o[0], o[1]), (LAT, LON), profile, NAME, None)
    return {"from": o[2], "to": NAME, "profile": profile, "minutes": r["minutes"], "meters": r["meters"], "crossings": r["crossings"],
            "hard_spots": [h["text"] for h in r["hard_spots"]], "steps": r["steps"], "confidence": r["confidence"],
            "slope_checked": bool(r.get("elevation")), "notes": r["notes"], "route": r,
            "caveat": f"The route ends at the building's footprint; which door is step-free {SITE_VISIT}."}


if __name__ == "__main__":
    import sys
    mid = sys.argv[1] if len(sys.argv) > 1 else PRIMARY
    s = venue_summary(mid)
    print(json.dumps({k: v for k, v in s.items() if k not in ("stairs",)}, indent=1, default=str))
    print(json.dumps(stairs(mid), indent=1, default=str))
    for x in door_widths(model_id=mid)["doors"]:
        print(" door", x["floor_label"], x["rooms"], x["clear_in"], x["status"], (x["step"] or {}).get("says"))
