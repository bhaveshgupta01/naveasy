"""A rough print-time estimate for a Bambu Lab A1 mini, from the part itself (when no slicer is at hand).

    python scripts/print_time.py output/print3d/small/plate1_boxes.stl ...

Slices the part every layer and adds up the paths a slicer lays down, at the A1 mini's "0.20mm Standard" PLA
settings, with acceleration on short moves:
    walls   2 loops along every contour (outer 200 mm/s, inner 300 mm/s)
    solid   infill within 1.0 mm (5 layers) of a top or 0.6 mm (3 layers) of a bottom, 250 mm/s
    sparse  15 % infill elsewhere, 270 mm/s
    speeds capped by a 21 mm3/s PLA flow limit; the first layer at 50 mm/s
    per layer 1.2 s (z move, retract, wipe) + 0.25 s per island; per print 5 min to start (heat, level, clean)
Not a slicer: expect Bambu Studio's number to differ by up to about a third. Use it to compare options.
"""
from __future__ import annotations

import math
import sys

import numpy as np
from manifold3d import JoinType, Manifold, Mesh

W = 0.42          # line width, mm
ACC = 10000.0     # mm/s2 (outer walls 5000)
START_S = 5 * 60


def _run(L: float, v: float, a: float) -> float:
    """Time to cover L mm from a stop to a stop at top speed v and acceleration a."""
    if L <= 0:
        return 0.0
    return L / v + v / a if L > v * v / a else 2 * math.sqrt(L / a)


def _loops(cs, v: float, a: float) -> float:
    """Every contour once; a corner sharper than 35 degrees is a stop."""
    t = 0.0
    for ring in cs.to_polygons():
        P = np.asarray(ring)
        if len(P) < 3:
            continue
        seg = np.roll(P, -1, axis=0) - P
        lens = np.linalg.norm(seg, axis=1)
        cos = np.einsum("ij,ij->i", seg, np.roll(seg, 1, axis=0)) / np.maximum(lens * np.roll(lens, 1), 1e-12)
        stops = np.flatnonzero(np.degrees(np.arccos(np.clip(cos, -1, 1))) > 35)
        if len(stops) == 0:
            t += _run(lens.sum(), v, a)
        else:
            t += sum(_run(r.sum(), v, a) for r in np.split(np.roll(lens, -stops[0]), stops[1:] - stops[0]))
    return t


def _fill(cs, density: float, v: float, a: float) -> tuple[float, float]:
    """Straight lines back and forth at 45 degrees over each island: (time, length)."""
    t = L = 0.0
    for part in cs.decompose():
        A = part.area()
        if A < 0.05:
            continue
        u = np.concatenate([np.asarray(r) for r in part.to_polygons()]) @ np.array([1, -1]) / math.sqrt(2)
        across = max(u.max() - u.min(), W)
        t += max(1, across * density / W) * _run(A / across, v, a)
        L += A * density / W
    return t, L


def estimate(m: Manifold, layer: float = 0.20) -> tuple[float, float]:
    """(seconds, grams of PLA) for one print of `m`."""
    top = m.bounding_box()[5]
    vmax = 21.0 / (W * layer)
    total, length = START_S, 0.0
    for i in range(int(math.ceil(top / layer))):
        z = (i + 0.5) * layer
        cs = m.slice(z)
        if cs.is_empty():
            continue
        vo, vi, vs, vp = (50, 50, 50, 50) if i == 0 else (min(200, vmax), min(300, vmax), min(250, vmax), min(270, vmax))
        core = cs.offset(-2 * W, JoinType.Miter)
        above = m.slice(z + 1.0)
        below = m.slice(z - 0.6) if z > 0.6 else None
        covered = core ^ above if not above.is_empty() else core - core
        covered = covered ^ below if below is not None and not below.is_empty() else covered - covered
        t_solid, l_solid = _fill(core - covered, 1.0, vs, ACC)
        t_sparse, l_sparse = _fill(covered, 0.15, vp, ACC)
        perim = sum(np.linalg.norm(np.roll(np.asarray(r), -1, 0) - np.asarray(r), axis=1).sum() for r in cs.to_polygons())
        total += (_loops(cs, vo, 5000) + _loops(cs.offset(-W, JoinType.Miter), vi, ACC) + t_solid + t_sparse
                  + 1.2 + 0.25 * len(cs.decompose()))
        length += 2 * perim + l_solid + l_sparse
    return total, length * W * layer * 1.24 / 1000


def load(path: str) -> Manifold:
    import trimesh
    t = trimesh.load(path)
    return Manifold(Mesh(vert_properties=np.asarray(t.vertices, np.float32), tri_verts=np.asarray(t.faces, np.uint32)))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        s, g = estimate(load(p))
        print(f"{p}: about {s / 3600:.1f} h, {g:.0f} g")
