"""Built-in fixtures from Matterport's 3D mesh: kitchen counters and islands, vanities, toilets, tubs.

The wall graph has walls and doorways but nothing built in, so without this a route to the kitchen can run straight
across the island. Matterport's textured mesh (Model API assets: a 50k-triangle OBJ of about 1 MB) has them. In a
kitchen, bathroom, laundry, pantry or utility room, any level surface 30 cm to 1.3 m above the floor is a counter top,
an island, a vanity, a toilet or a tub rim. Level surfaces in other rooms (beds, sofas, tables) are left out: they
belong to whoever lived there when it was scanned, not to the home. Meshes are cached in data/meshes/.

Each built-in is also measured as its own item (items()): width and depth from the patch's minimum rotated
rectangle, and the height of its top above the floor next to it, both from the mesh. What the item is (counter,
island, vanity, toilet, tub; in a public building desk or table, bench or seating) is inferred from the room, the
size and the height, and the output says so. Movable furniture found from the panoramas gets a height the same
way (furniture_heights()).

    python -m pipeline.fixtures <model_id>     # fetch the mesh and print what was found per room
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np
import requests
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from shapely.geometry import MultiPoint

from .clients import ROOT, matterport
from .unit import CELL, Plan, load_plan

MESHES = ROOT / "data" / "meshes"
FIXTURE_ROOMS = ("Kitchen", "Bathroom", "Laundry", "Pantry", "Utility Room")
# rooms of a public building where level surfaces are desks, tables, benches or fixed seating (never added to the
# blocked floor: they may be movable, so the heat map is unchanged; they are listed as items only)
PUBLIC_ROOMS = ("Classroom", "Office", "Conference Room", "Lobby", "Auditorium", "Library", "Lounge", "Reception",
                "Hallway", "Landing", "Entrance", "Dining Room", "Other")
LOW, HIGH = 0.3, 1.3       # m above the floor: toilet seat to the top of a raised counter
STEP_LOW, STEP_HIGH = 0.12, 3.2   # stair treads: from the first step to the landing above
STAIR_RISE = 0.5           # a patch counts as stairs when its level surfaces climb at least this much (a sofa doesn't)
LEVEL = 0.8                # |normal z| above this is a level surface
MIN_CELLS = 20             # drop specks under 0.05 m2
SEAT_TOP = 0.62            # m: level tops below this are seat height (toilet, tub rim, bench); above, counter or desk height

# Reach limits, from the 2010 ADA Standards (text checked on access-board.gov/ada, Nov 14 2026):
#   902.3   dining and work surfaces: 28 in (710 mm) minimum, 34 in (865 mm) maximum above the finish floor
#   804.3   in residential units required to comply with 809, at least one 30 in (760 mm) wide section of counter
#   804.3.2   ... is a kitchen work surface 34 in (865 mm) maximum above the finish floor
#   606.3   lavatories and sinks: the front of the higher of the rim or counter 34 in (865 mm) maximum
#   904.4.1 sales and service counters, parallel approach: a 36 in long section 36 in (915 mm) high maximum
#   604.4   toilet seats 17 to 19 in (430 to 485 mm): only a sanity range for what we call a toilet
WORK_MAX_M = 0.865
SERVICE_MAX_M = 0.915
SECTION_MIN_M = 0.76
# The 50k mesh puts level tops within about an inch (ceilings come out 0 to 2% low against 8, 9 and 10 ft), so
# unit._reach judges by the whole inch: one inch over a limit is "measure it", two or more is a problem.


def mesh_path(model_id: str, fetch: bool = True):
    """The 50k-triangle mesh on disk, downloading it once. None when the scan has no such mesh."""
    path = MESHES / f"{model_id}_50k.obj"
    none = MESHES / f"{model_id}.none"   # remembered: this scan has no small mesh
    if path.exists():
        return path
    if none.exists() or not fetch:
        return None
    MESHES.mkdir(parents=True, exist_ok=True)
    mesh = next((m for m in matterport.assets(model_id).get("meshes") or [] if m.get("resolution") == "50k" and m.get("url")), None)
    if not mesh:
        none.write_text("")
        return None
    r = requests.get(mesh["url"], timeout=120)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path


def load_mesh(path) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (n, 3; x east, y north, z up, meters, the wall graph's frame) and triangles (m, 3)."""
    cache = path.with_suffix(".npz")
    if cache.exists():
        z = np.load(cache)
        return z["v"], z["f"]
    verts, faces = [], []
    with open(path) as fh:
        for line in fh:
            if line.startswith("v "):
                verts.append(line.split()[1:4])
            elif line.startswith("f "):
                ids = [int(t.split("/")[0]) for t in line.split()[1:]]
                faces.extend([ids[0], ids[k], ids[k + 1]] for k in range(1, len(ids) - 1))   # fan polygons into triangles
    v = np.array(verts, dtype=np.float32)
    f = np.array(faces, dtype=np.int64) - 1
    np.savez_compressed(cache, v=v, f=f)
    return v, f


def floor_level(f) -> float:
    return float(np.median([r["elev"] for r in f.rooms if r.get("elev") is not None] or [0.0]))


def _height_map(f, tris: np.ndarray, highest: bool) -> np.ndarray:
    """Height of the highest (or lowest) level surface over each cell, NaN where there is none."""
    ny, nx = f.shape
    img = Image.new("F", (nx, ny), float("nan"))
    d = ImageDraw.Draw(img)
    x0, y0 = f.bounds[0], f.bounds[1]
    z = tris[:, :, 2].mean(1)
    for k in np.argsort(z if highest else -z):   # drawn last wins
        d.polygon([((p[0] - x0) / CELL, (p[1] - y0) / CELL) for p in tris[k]], fill=float(z[k]))
    return np.array(img)


def stairs(plan: Plan, verts: np.ndarray, faces: np.ndarray) -> dict[int, np.ndarray]:
    """Per floor index: where the stairs are inside rooms labeled Staircase (which can be a whole open-plan area)."""
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    up = n[:, 2] > LEVEL * (np.linalg.norm(n, axis=1) + 1e-12)   # treads face up; the ceiling faces down
    zc = tri[:, :, 2].mean(1)
    out = {}
    for f in plan.floors:
        rooms = [i for i, r in enumerate(f.rooms) if r["type"] == "Staircase"]
        if not rooms:
            out[f.index] = np.zeros(f.shape, bool)
            continue
        z0 = floor_level(f)
        top = min(STEP_HIGH, min((floor_level(g) - z0 for g in plan.floors if floor_level(g) > z0 + 1), default=STEP_HIGH) - 0.15)
        pick = tri[up & (zc - z0 > STEP_LOW) & (zc - z0 < top)]
        hi, lo = _height_map(f, pick, True), _height_map(f, pick, False)
        m = ndi.binary_closing(np.isfinite(hi), iterations=3) & np.isin(f.room_idx, rooms) & ~f.wall_mask
        lab, k = ndi.label(m)
        keep = [c for c in range(1, k + 1)
                if (lab == c).sum() >= MIN_CELLS and np.nanmax(np.where(lab == c, hi, np.nan)) - np.nanmin(np.where(lab == c, lo, np.nan)) >= STAIR_RISE]
        out[f.index] = np.isin(lab, keep)
    return out


def _level_tops(verts: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Triangles, whether each faces up (a top, not the underside of an overhang or a ceiling), and their mean height."""
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    up = n[:, 2] > LEVEL * (np.linalg.norm(n, axis=1) + 1e-12)
    return tri, up, tri[:, :, 2].mean(1)


def _surface_mask(f, pick: np.ndarray, room_types) -> np.ndarray:
    """Cells under the given level triangles, inside rooms of the given types, seams closed and specks dropped."""
    ny, nx = f.shape
    img = Image.new("L", (nx, ny), 0)
    d = ImageDraw.Draw(img)
    x0, y0 = f.bounds[0], f.bounds[1]
    for t in pick:
        d.polygon([((p[0] - x0) / CELL, (p[1] - y0) / CELL) for p in t], fill=1)
    m = np.array(img, dtype=bool)
    rooms = np.isin(f.room_idx, [i for i, r in enumerate(f.rooms) if r["type"] in room_types])
    m = ndi.binary_closing(m, iterations=2) & rooms & ~f.wall_mask   # close the seams between triangles
    lab, k = ndi.label(m)
    if k:
        sizes = ndi.sum(m, lab, range(1, k + 1))
        m = np.isin(lab, 1 + np.flatnonzero(sizes >= MIN_CELLS))
    return m


def masks(plan: Plan, verts: np.ndarray, faces: np.ndarray) -> dict[int, np.ndarray]:
    """Per floor index: cells covered by a built-in fixture."""
    tri, up, zc = _level_tops(verts, faces)
    out = {}
    for f in plan.floors:
        z0 = floor_level(f)
        out[f.index] = _surface_mask(f, tri[up & (zc - z0 > LOW) & (zc - z0 < HIGH)], FIXTURE_ROOMS)
    return out


def is_public(plan: Plan) -> bool:
    """A scan of a public building, not a home: no bedroom and no kitchen anywhere (a studio still has a kitchen)."""
    types = {r["type"] for f in plan.floors for r in f.rooms}
    return bool(types) and not types & {"Bedroom", "Kitchen"}


def _kind(room: str, h: float, w: float, d: float, at_wall: bool, public_room: bool, area: float) -> tuple[str, bool]:
    """What a level patch probably is, from its room, height and size: (kind, possibly movable)."""
    if public_room:
        if area > 6:
            return "raised floor or platform", False
        if h < SEAT_TOP:
            return ("fixed seating or bench" if w >= 1.8 else "bench or seat"), True
        if h < 0.84:
            return "desk or table", True
        return ("counter" if at_wall else "high table or counter"), not at_wall
    if room == "Bathroom":
        if h < SEAT_TOP:
            if w >= 1.2 and d >= 0.5:
                return "tub", False
            if w <= 0.85 and w * d <= 0.5:
                return "toilet", False
            return "low built-in", False
        if d < 0.33 and w < 0.7 and h < 0.85:
            return "toilet tank", False   # a vanity is 18 in deep or more; a tank lid is about 9 in
        if d < 0.36 or h > 1.0:
            return "shelf or ledge", False
        return "vanity", False
    if room in ("Kitchen", "Pantry"):
        if h < SEAT_TOP:
            return "low surface", True
        if h < 0.8 and not at_wall:
            return "table", True          # a kitchen table left in a furnished scan, at dining height
        if d < 0.3:
            return "shelf or ledge", False  # a window sill, backsplash ledge or pass-through, not a work surface
        return ("counter" if at_wall else "island"), False
    if room in ("Laundry", "Utility Room"):
        return ("counter or appliance" if h >= SEAT_TOP else "low surface"), False
    return "built-in", False


def items(plan: Plan, verts: np.ndarray, faces: np.ndarray, fixture_masks: dict[int, np.ndarray] | None = None,
          public: bool | None = None) -> dict[int, list[dict]]:
    """Per floor index: each built-in as its own measured item.

    The patches are the built-in mask (kitchens, bathrooms, laundry, pantry, utility), split into seat-height and
    counter-height parts so a toilet beside a vanity is two items. In a public building (public=True, or no bedroom
    and no kitchen anywhere) level surfaces in classrooms, offices, halls and landings are listed too, as desks,
    tables, benches or seating that may be movable; they are not added to the blocked floor.
    Heights are the top's median height minus the floor's height in a ring around the item, both from the mesh."""
    public = is_public(plan) if public is None else public
    fixture_masks = fixture_masks if fixture_masks is not None else masks(plan, verts, faces)
    tri, up, zc = _level_tops(verts, faces)
    out: dict[int, list[dict]] = {}
    for f in plan.floors:
        z0 = floor_level(f)
        rel = zc - z0
        tops = tri[up & (rel > LOW) & (rel < HIGH)]
        hi = _height_map(f, tops, True)
        # the floor: the lowest level surface over each cell, allowing sunken rooms and split levels on this floor
        floor_z = _height_map(f, tri[up & (rel > -0.8) & (rel < 0.2)], False)
        m = fixture_masks.get(f.index)
        m = np.zeros(f.shape, bool) if m is None else m
        pub = _surface_mask(f, tops, PUBLIC_ROOMS) & ~m if public else np.zeros(f.shape, bool)
        # every cell of a patch gets the height of the nearest measured top (seams between triangles have none)
        known = np.isfinite(hi)
        if not known.any():
            out[f.index] = []
            continue
        _, (ir, ic) = ndi.distance_transform_edt(~known, return_indices=True)
        filled = hi[ir, ic]
        found = []
        for src, mask in (("built-in", m), ("public", pub)):
            for band in (filled - z0 < SEAT_TOP, filled - z0 >= SEAT_TOP):
                lab, k = ndi.label(mask & band)
                for c in range(1, k + 1):
                    comp = lab == c
                    if comp.sum() < MIN_CELLS:
                        continue
                    it = _measure(f, comp, hi, floor_z, z0, src == "public")
                    if it:
                        found.append(it)
        found.sort(key=lambda it: (it["room_type"], -it["width_m"]))
        for j, it in enumerate(found):
            it["id"] = f"fx{f.index}_{j + 1}"
        out[f.index] = found
    return out


def _measure(f, comp: np.ndarray, hi: np.ndarray, floor_z: np.ndarray, z0: float, public_room: bool) -> dict | None:
    """One patch as an item, or None when it isn't a usable surface (a sill or ledge under 10 cm deep) or its
    height can't be placed against a floor."""
    rr, cc = np.nonzero(comp)
    pts = [f.xy(r, c) for r, c in zip(rr, cc)]
    rect = MultiPoint(pts).minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return None                                    # all cells in one line: a 5 cm sill, not a surface
    corners = list(rect.exterior.coords)[:4]
    sides = [math.dist(corners[k], corners[(k + 1) % 4]) for k in range(2)]
    long_k = int(np.argmax(sides))
    w, d = max(sides) + CELL, min(sides) + CELL          # cell centers sit half a cell inside the patch's edge
    area = float(comp.sum()) * CELL * CELL
    a, b = corners[long_k], corners[(long_k + 1) % 4]
    angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180
    top = float(np.nanmedian(np.where(comp, hi, np.nan)))
    ri = int(np.bincount(f.room_idx[comp][f.room_idx[comp] >= 0]).argmax()) if (f.room_idx[comp] >= 0).any() else -1
    room = f.rooms[ri] if ri >= 0 else {"id": None, "type": "Other", "elev": None}
    shape = "straight"
    if area < 0.6 * w * d and top - z0 >= SEAT_TOP and room["type"] != "Bathroom":
        # an L or U shaped counter: its depth is the widest inscribed band, its length the run along the walls
        # (not a tub, whose rim is a hollow rectangle around a lower basin)
        d = 2 * float(ndi.distance_transform_edt(np.pad(comp, 1))[1:-1, 1:-1].max()) * CELL
        w = area / max(d, CELL)
        shape = "L or U shaped"
    if d < 0.1:
        return None
    # the floor beside it: 10 to 60 cm out from the patch, inside a room, not in a wall; the lower quarter of what
    # is seen there, so a rug, a step or the toe kick doesn't lift it
    ring = ndi.binary_dilation(comp, iterations=12) & ~ndi.binary_dilation(comp, iterations=2) & (f.room_idx >= 0) & ~f.wall_mask
    near = floor_z[ring & np.isfinite(floor_z)]
    if near.size >= 10:
        base, base_src = float(np.percentile(near, 25)), "mesh"
    elif room.get("elev") is not None:
        base, base_src = float(room["elev"]), "room"
    else:
        base, base_src = z0, "floor"
    h = top - base
    if not LOW - 0.12 <= h <= HIGH + 0.12 and base_src != "floor":
        base, base_src = z0, "floor"                   # a base from somewhere else on a split level: use the floor's
        h = top - base
    if not LOW - 0.12 <= h <= HIGH + 0.12:
        return None
    at_wall = bool((ndi.binary_dilation(comp, iterations=4) & f.wall_mask).any())
    kind, movable = _kind(room["type"], h, w, d, at_wall, public_room, area)
    cx, cy = rect.centroid.x, rect.centroid.y
    return {"id": "", "kind": kind, "floor": f.index, "room": room["id"], "room_type": room["type"],
            "x": round(cx, 2), "y": round(cy, 2), "width_m": round(w, 2), "depth_m": round(d, 2), "height_m": round(h, 3),
            "width_in": round(w / 0.0254), "depth_in": round(d / 0.0254), "height_in": round(h / 0.0254, 1),
            "shape": shape, "angle_deg": round(angle, 1), "area_m2": round(area, 2),
            "polygon": [[round(x, 2), round(y, 2)] for x, y in corners], "at_wall": at_wall,
            "possibly_movable": movable, "floor_ref": base_src,
            "measured": "width, depth and height measured from the 3D mesh", "kind_source": "inferred from room, size and height"}


def furniture_heights(plan: Plan, verts: np.ndarray, faces: np.ndarray, furniture: list[dict]) -> dict[str, dict]:
    """Heights of movable pieces (found from the panoramas, footprints only) from the furnished scan's mesh:
    the main level surface (a mattress, a seat, a table top) and the top. {item id: {surface_m, height_m}}."""
    tri, up, zc = _level_tops(verts, faces)
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
    cx, cy = tri[:, :, 0].mean(1), tri[:, :, 1].mean(1)
    out = {}
    for it in furniture:
        fi = it.get("floor", 0)
        if not 0 <= fi < len(plan.floors) or not it.get("rect"):
            continue
        z0 = floor_level(plan.floors[fi])
        x, y, w, d = it["rect"]
        inside = up & (cx >= x) & (cx <= x + w) & (cy >= y) & (cy <= y + d) & (zc - z0 > 0.12) & (zc - z0 < 2.3)
        if area[inside].sum() < 0.05:          # under 0.05 m2 of level top seen: no height
            continue
        z, wt = zc[inside] - z0, area[inside]
        order = np.argsort(z)
        cum = np.cumsum(wt[order]) / wt.sum()
        surface = float(z[order][np.searchsorted(cum, 0.5)])
        top = float(z[order][min(len(z) - 1, np.searchsorted(cum, 0.95))])
        out[it["id"]] = {"surface_m": round(surface, 2), "height_m": round(top, 2)}
    return out


def attach(plan: Plan, fetch: bool = True, public: bool | None = None) -> bool:
    """Put each floor's fixture mask on the plan (f.fixture_mask), the stairs (f.stairs_mask) and each measured
    built-in (f.fixture_items). True when the mesh was found and used. public: see items()."""
    if not plan.has_graph:
        return False
    path = mesh_path(plan.model_id, fetch)
    if not path:
        return False
    mesh = load_mesh(path)
    fm = masks(plan, *mesh)
    for i, m in fm.items():
        plan.floors[i].fixture_mask = m
    for i, m in stairs(plan, *mesh).items():
        plan.floors[i].stairs_mask = m
    for i, its in items(plan, *mesh, fixture_masks=fm, public=public).items():
        plan.floors[i].fixture_items = its
    plan.fixtures = "mesh"
    return True


def load_heights(model_id: str, furniture: list[dict]) -> dict[str, dict]:
    """furniture_heights() for a scan's found furniture, cached in data/furniture/<id>.heights.json.
    Empty when the scan's mesh isn't on disk (never downloads)."""
    cache = ROOT / "data" / "furniture" / f"{model_id}.heights.json"
    if cache.exists():
        return json.loads(cache.read_text())
    path = mesh_path(model_id, fetch=False)
    if not path or not furniture:
        return {}
    out = furniture_heights(load_plan(model_id, fixtures=False), *load_mesh(path), furniture)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    mid = sys.argv[1]
    plan = load_plan(mid)
    print("mesh used:", attach(plan))
    for f in plan.floors:
        if f.fixture_mask is None:
            continue
        for i, r in enumerate(f.rooms):
            cells = int((f.fixture_mask & (f.room_idx == i)).sum())
            if cells:
                print(f"  {f.label} {r['type']:<12} {cells * CELL * CELL:.1f} m2 of built-ins ({r['area_m2']} m2 room)")
            steps = int((f.stairs_mask & (f.room_idx == i)).sum()) if f.stairs_mask is not None else 0
            if steps:
                print(f"  {f.label} {r['type']:<12} {steps * CELL * CELL:.1f} m2 of stairs ({r['area_m2']} m2 room)")
        for it in f.fixture_items or []:
            print(f"    {it['id']:<7} {it['room_type']:<11} {it['kind']:<22} {it['width_in']:>3} x {it['depth_in']:>3} in, top {it['height_in']:>5} in"
                  f" (floor from {it['floor_ref']}){'  possibly movable' if it['possibly_movable'] else ''}")
