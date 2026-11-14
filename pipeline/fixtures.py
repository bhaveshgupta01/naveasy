"""Built-in fixtures from Matterport's 3D mesh: kitchen counters and islands, vanities, toilets, tubs.

The wall graph has walls and doorways but nothing built in, so without this a route to the kitchen can run straight
across the island. Matterport's textured mesh (Model API assets: a 50k-triangle OBJ of about 1 MB) has them. In a
kitchen, bathroom, laundry, pantry or utility room, any level surface 30 cm to 1.3 m above the floor is a counter top,
an island, a vanity, a toilet or a tub rim. Level surfaces in other rooms (beds, sofas, tables) are left out: they
belong to whoever lived there when it was scanned, not to the home. Meshes are cached in data/meshes/.

    python -m pipeline.fixtures <model_id>     # fetch the mesh and print what was found per room
"""
from __future__ import annotations

import sys

import numpy as np
import requests
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from .clients import ROOT, matterport
from .unit import CELL, Plan, load_plan

MESHES = ROOT / "data" / "meshes"
FIXTURE_ROOMS = ("Kitchen", "Bathroom", "Laundry", "Pantry", "Utility Room")
LOW, HIGH = 0.3, 1.3       # m above the floor: toilet seat to the top of a raised counter
STEP_LOW, STEP_HIGH = 0.12, 3.2   # stair treads: from the first step to the landing above
STAIR_RISE = 0.5           # a patch counts as stairs when its level surfaces climb at least this much (a sofa doesn't)
LEVEL = 0.8                # |normal z| above this is a level surface
MIN_CELLS = 20             # drop specks under 0.05 m2


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


def masks(plan: Plan, verts: np.ndarray, faces: np.ndarray) -> dict[int, np.ndarray]:
    """Per floor index: cells covered by a built-in fixture."""
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    up = n[:, 2] > LEVEL * (np.linalg.norm(n, axis=1) + 1e-12)   # tops of things, not the undersides of overhangs
    zc = tri[:, :, 2].mean(1)
    out = {}
    for f in plan.floors:
        z0 = floor_level(f)
        pick = tri[up & (zc - z0 > LOW) & (zc - z0 < HIGH)]
        ny, nx = f.shape
        img = Image.new("L", (nx, ny), 0)
        d = ImageDraw.Draw(img)
        x0, y0 = f.bounds[0], f.bounds[1]
        for t in pick:
            d.polygon([((p[0] - x0) / CELL, (p[1] - y0) / CELL) for p in t], fill=1)
        m = np.array(img, dtype=bool)
        wet = np.isin(f.room_idx, [i for i, r in enumerate(f.rooms) if r["type"] in FIXTURE_ROOMS])
        m = ndi.binary_closing(m, iterations=2) & wet & ~f.wall_mask   # close the seams between triangles
        lab, k = ndi.label(m)
        if k:
            sizes = ndi.sum(m, lab, range(1, k + 1))
            m = np.isin(lab, 1 + np.flatnonzero(sizes >= MIN_CELLS))
        out[f.index] = m
    return out


def attach(plan: Plan, fetch: bool = True) -> bool:
    """Put each floor's fixture mask on the plan (f.fixture_mask). True when the mesh was found and used."""
    if not plan.has_graph:
        return False
    path = mesh_path(plan.model_id, fetch)
    if not path:
        return False
    mesh = load_mesh(path)
    for i, m in masks(plan, *mesh).items():
        plan.floors[i].fixture_mask = m
    for i, m in stairs(plan, *mesh).items():
        plan.floors[i].stairs_mask = m
    plan.fixtures = "mesh"
    return True


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
