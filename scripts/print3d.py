"""Table models for the 3D printer: each home as an open-top box, plus four drop-in floor overlays, one per need.

    python scripts/print3d.py                      # the two demo homes at 1:95 into output/print3d/ (fits a 180 mm bed: Bambu A1 mini)
    python scripts/print3d.py --scale 80           # bigger (Drewery Place then needs a 210 x 187 mm bed)
    python scripts/print3d.py --homes TAJdsi5EGmq  # one home

For each home (output/print3d/<slug>/):
    house_furnished.stl   base plate, walls (door gaps open, windows as notches), built-ins from the scan's mesh and
                          the furniture, as one print. Print this for a one-color box.
    house.stl + furniture.stl
                          the same split in two, sharing one origin: load both as parts of one object in the slicer
                          to print the furniture in a second color.
    overlay_<need>.stl    a 1.2 mm floor mat with a slot for every wall and a cut-out for every built-in and piece of
                          furniture. It drops over the walls and lies on the floor. Print each in its need's color.

The overlay code, the same on all four mats: solid = good, raised stripes = caution, open net = problem (the floor
shows through). Raised marks: wheelchair turning circles and barrier bars at doors too narrow for a chair; blind
tactile guide ridges along the main paths from the front door and warning domes where furniture crowds them; deaf
eye posts at the main seat and the kitchen and a dashed sight line to the front door when it can be seen; older
adults the night path from the bed to the bathroom. A raised step at a door gets a row of domes. Every mat carries
its name in print and in braille, and a pull tab.

Everything comes from the same analysis the app shows (pipeline.unit.analyze), so the mats match the heat maps.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import shapely
import trimesh
from manifold3d import CrossSection, FillRule, Manifold, OpType
from PIL import Image, ImageDraw, ImageFont
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, polylabel, unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.furnish import inventory_from, load_furniture, plan_furnishing  # noqa: E402
from pipeline.unit import CELL, _status, analyze, item_polygon, load_plan  # noqa: E402

OUT = ROOT / "output" / "print3d"
NEEDS = ("wheelchair", "blind", "deaf", "older")
# model id, slug, the name on the base, where the furniture comes from
HOMES = {
    "TAJdsi5EGmq": ("narcissus", "7833 NARCISSUS ST", "scan"),
    "yQsPwmzZscg": ("drewery", "DREWERY PLACE 2BR", "plan:TAJdsi5EGmq"),   # Narcissus's furniture, placed for all four needs
    # two Los Angeles one-bedrooms of the same size (7.7 x 4.8 m and 7.8 x 4.8 m), the same furniture in both:
    "3TbWucW9g5n": ("playa", "PLAYA PALMS 1BR", "inv"),        # 151 N Normandie Ave: bedroom and bath doors 27-29 in
    "WyuyoRKZyxW": ("w84th", "833 W 84TH ST 1BR", "inv"),      # doors 33-39 in, three turning circles
}
# a light set: at 1:100 a 37 m2 home with eight pieces is half furniture, and the mats need open floor to read
ONE_BED = ("queen bed", "sofa", "tv stand", "dining table")
PRESETS = {   # homes, scale, wall height (mm)
    "small": (["3TbWucW9g5n", "WyuyoRKZyxW"], 120, 12.0),   # the quick set: two small boxes and eight mats on 2 plates
    "demo": (["TAJdsi5EGmq", "yQsPwmzZscg"], 95, 20.0),     # the app's demo homes, larger
}
LABEL = {"wheelchair": "WHEELCHAIR", "blind": "BLIND", "deaf": "DEAF", "older": "OLDER"}
LETTER = {"wheelchair": "W", "blind": "B", "deaf": "D", "older": "O"}   # when the word has no room
# the app's per-need hues (NEED_COLOR in frontend/src/colors.ts), for the previews; print each mat in the nearest filament
COLOR = {"wheelchair": (0x2a, 0x78, 0xd6), "blind": (0x9c, 0x3f, 0xa6), "deaf": (0xe8, 0x7b, 0xa4), "older": (0xc9, 0x94, 0x00)}

# print dimensions, mm
BASE_T = 2.0          # base plate (most of the print time is here)
WALL_H = 20.0         # wall height above the floor: low enough to see in and reach the mats
WALL_MIN = 1.6        # four perimeters at a 0.4 mm nozzle
SILL_SHARE = 0.55     # windows are notches from this share of the wall height up (about a 1 m sill)
MARGIN = 5.0          # base around the walls
STRIP = 15.0          # label strip along the front (the name, and the key to the mats' letters)
MAT_T = 1.2           # overlay mat
CLEAR = 0.5           # gap between a mat and each wall or piece, per side (room for first-layer squish)
MIN_PIECE_MM2 = 150.0  # floor parts smaller than this (a 1.3 m2 closet at 1:95) get no mat
FURN_H = {  # meters; the scan's mesh tops are noisy (a bed's headboard, a lamp on a table), so one height per kind
    "bed": 0.55, "queen bed": 0.55, "king bed": 0.55, "full bed": 0.55, "twin bed": 0.55, "nightstand": 0.6,
    "dresser": 0.95, "wardrobe": 1.3, "sofa": 0.8, "sectional": 0.8, "loveseat": 0.8, "armchair": 0.8,
    "tv stand": 0.55, "coffee table": 0.42, "bookshelf": 1.3, "piano": 1.1, "dining table": 0.75, "desk": 0.75,
    "console table": 0.75, "stored items": 0.9,
}
ROOM_WORD = {"Living Room": "LIVING", "Dining Room": "DINING", "Kitchen": "KITCHEN", "Bedroom": "BEDROOM",
             "Bathroom": "BATH", "Laundry": "LAUNDRY", "Hallway": "HALL", "Entrance": "ENTRY", "Office": "OFFICE"}
BRAILLE = {"a": "1", "b": "12", "c": "14", "d": "145", "e": "15", "f": "124", "g": "1245", "h": "125", "i": "24",
           "j": "245", "k": "13", "l": "123", "m": "134", "n": "1345", "o": "135", "p": "1234", "q": "12345",
           "r": "1235", "s": "234", "t": "2345", "u": "136", "v": "1236", "w": "2456", "x": "1346", "y": "13456",
           "z": "1356"}
FONT = next((p for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if Path(p).exists()), None)


# ---------------------------------------------------------------- 2D helpers

def polys(g) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) for p in polys(p)]


def clean(g, min_area: float = 0.05):
    g = shapely.make_valid(g) if not g.is_valid else g
    return unary_union([p for p in polys(g) if p.area >= min_area])


def mask_poly(mask: np.ndarray, x0: float, y0: float, cell: float):
    """A boolean grid (row 0 = lowest y) as one geometry in the grid's units."""
    boxes = []
    for r, row in enumerate(mask):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            boxes.append((x0 + a * cell, y0 + r * cell, x0 + b * cell, y0 + (r + 1) * cell))
    if not boxes:
        return Polygon()
    a = np.array(boxes)
    return shapely.union_all(shapely.box(a[:, 0], a[:, 1], a[:, 2], a[:, 3]))


def rect(cx: float, cy: float, along: float, across: float, angle_deg: float) -> Polygon:
    return affinity.rotate(box(cx - along / 2, cy - across / 2, cx + along / 2, cy + across / 2), angle_deg, origin=(cx, cy))


def stripes(region, pitch: float, width: float, angle: float):
    """Parallel bars over a region, `angle` degrees from the x axis."""
    if region.is_empty:
        return Polygon()
    x0, y0, x1, y1 = region.bounds
    cx, cy, R = (x0 + x1) / 2, (y0 + y1) / 2, math.hypot(x1 - x0, y1 - y0) / 2 + pitch
    bars = [box(cx - R, y - width / 2, cx + R, y + width / 2) for y in np.arange(cy - R, cy + R, pitch)]
    return affinity.rotate(unary_union(bars), angle, origin=(cx, cy)).intersection(region)


def text_poly(s: str, cap_mm: float, px_per_mm: int = 24):
    """Text as polygons in mm, lower-left of the ink at (0, 0)."""
    font_px = int(cap_mm * px_per_mm / 0.716)            # Arial's cap height is 0.716 em
    font = ImageFont.truetype(FONT, font_px) if FONT else ImageFont.load_default(font_px)
    l, t, r, b = font.getbbox(s)
    im = Image.new("1", (r - l + 4, b - t + 4), 0)
    ImageDraw.Draw(im).text((2 - l, 2 - t), s, font=font, fill=1)
    m = np.array(im)[::-1]
    # traced pixels that touch only at a corner would extrude to an edge shared by four faces (not printable as STL):
    # a 0.02 mm closing joins them
    g = mask_poly(m, 0, 0, 1.0 / px_per_mm).buffer(0.02, join_style="mitre").buffer(-0.02, join_style="mitre").simplify(0.03)
    x0, y0, _, _ = g.bounds
    return affinity.translate(g, -x0, -y0)


def braille_dots(word: str, x0: float, y_top: float, dot: float = 2.5, cell: float = 6.0) -> list[tuple[float, float]]:
    offs = {"1": (0, 0), "2": (0, -1), "3": (0, -2), "4": (1, 0), "5": (1, -1), "6": (1, -2)}
    out = []
    for i, ch in enumerate(word.lower()):
        for k in BRAILLE.get(ch, ""):
            dx, dy = offs[k]
            out.append((x0 + i * cell + dx * dot, y_top + dy * dot))
    return out


# ---------------------------------------------------------------- 3D helpers

def prism(g, z0: float, z1: float):
    """A 2D shape (mm) to extrude from z0 to z1. Kept as (shape, z0, z1) until solid() builds the part."""
    if g is None or g.is_empty or z1 - z0 <= 1e-6:
        return None
    return (g, round(z0, 4), round(z1, 4))


def _extrude(g, z0: float, z1: float) -> Manifold | None:
    # snapped to a 1 micron grid and stripped of near-collinear points: near-duplicate points leave zero-length edges
    g = shapely.make_valid(shapely.set_precision(g, 0.001))
    rings = []
    for p in polys(g):
        if p.area < 1e-4:
            continue
        rings.append(np.asarray(p.exterior.coords[:-1], float))
        rings.extend(np.asarray(h.coords[:-1], float) for h in p.interiors)
    if not rings:
        return None
    cs = CrossSection(rings, FillRule.EvenOdd).simplify(0.002)
    return None if cs.is_empty() else cs.extrude(z1 - z0).translate([0, 0, z0])


def solid(parts: list, cuts: list = ()) -> Manifold:
    """One part from (shape, z0, z1) pieces, built as a height map: every piece is a column standing on the part's
    base (everything here rests on what is below it, so that is the same solid), and the part is a stack of layers,
    each the 2D union of the pieces reaching at least that high, closed by 0.01 mm. Each layer lies inside the one
    below it, so no two solids ever touch along just an edge (an edge four faces share, which no STL can hold).
    `cuts` (shape, z0, z1) are recesses from z0 up, like the room names engraved in the floor."""
    parts = [p for p in parts if p]
    cuts = [c for c in cuts if c]
    base = min(p[1] for p in parts)
    levels = sorted({p[2] for p in parts} | {c[1] for c in cuts if c[1] > base})
    solids, lo = [], base
    for h in levels:
        g = unary_union([p[0] for p in parts if p[2] >= h - 1e-6])
        cut = [c[0] for c in cuts if c[1] <= lo + 1e-6]
        if cut:
            g = g.difference(unary_union(cut))
        g = g.buffer(0.01, join_style="mitre").buffer(-0.01, join_style="mitre")
        m = _extrude(g, lo, h) if not g.is_empty else None
        if m is not None:
            solids.append(m)
        lo = h
    return Manifold.batch_boolean(solids, OpType.Add) if len(solids) > 1 else solids[0]


def dome(x: float, y: float, z: float, d: float = 2.0, h: float = 1.0) -> list:
    """A rounded bump in two steps: reads as a dome to a fingertip at this size."""
    return [prism(Point(x, y).buffer(d / 2, quad_segs=8), 0, z + h * 0.6),
            prism(Point(x, y).buffer(d * 0.3, quad_segs=6), 0, z + h)]


def write_stl(m: Manifold, path: Path, shift=(0.0, 0.0)) -> dict:
    """Binary STL, checked on the file as written (read back, points merged the way a slicer merges them, every
    edge shared by exactly two faces).

    Where two parts of a solid meet along a line, manifold keeps two vertices at the same position, one per side.
    An STL stores positions only, so a slicer would merge them into one edge with four faces. Each copy is moved
    5 microns toward its own faces instead: the two sides part by far less than a printer resolves."""
    mesh = m.translate([shift[0], shift[1], 0]).to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float64)
    f = np.asarray(mesh.tri_verts)
    _, inv, counts = np.unique(v.astype(np.float32), axis=0, return_inverse=True, return_counts=True)
    shared = np.flatnonzero(counts[inv.ravel()] > 1)
    if len(shared):
        acc = np.zeros_like(v)
        n = np.zeros(len(v))
        np.add.at(acc, f.ravel(), np.repeat(v[f].mean(1), 3, axis=0))
        np.add.at(n, f.ravel(), 1)
        d = acc[shared] / n[shared, None] - v[shared]
        v[shared] += d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12) * 0.005
    trimesh.Trimesh(v, f, process=False).export(path)
    back = trimesh.load(path)
    bb = m.bounding_box()
    return {"file": path.name, "triangles": int(len(back.faces)), "watertight": bool(back.is_watertight),
            "pinch_points_split": int(len(shared)), "volume_cm3": round(m.volume() / 1000, 1),
            "bodies": len(m.decompose()), "size_mm": [round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)]}


# ---------------------------------------------------------------- one home

class Home:
    def __init__(self, model_id: str, scale: float):
        self.id = model_id
        self.slug, self.name, src = HOMES.get(model_id, (model_id, model_id, "scan"))
        self.s = 1000.0 / scale
        self.scale = scale
        self.plan = load_plan(model_id)
        self.fi = self.plan.entry_floor
        self.f = self.plan.floors[self.fi]
        if src.startswith("plan:") or src == "inv":
            inv = inventory_from(src[5:]) if src != "inv" else [{"label": x} for x in ONE_BED]
            r = plan_furnishing(self.plan, inv, list(NEEDS), {})
            self.furniture = [{**p, "floor": self.fi} for p in r["placements"]]
        else:
            self.furniture = load_furniture(model_id) if src == "scan" else []
        self.res = analyze(self.plan, self.furniture)
        x0, y0 = self.f.bounds[0], self.f.bounds[1]
        self.M = lambda g: affinity.affine_transform(g, [self.s, 0, 0, self.s, -x0 * self.s, -y0 * self.s])  # noqa: E731
        self.P = lambda x, y: ((x - x0) * self.s, (y - y0) * self.s)  # noqa: E731
        self._geometry()

    # ---- the box
    def _geometry(self) -> None:
        f, s = self.f, self.s
        ends: dict = {}
        segs = []
        for ax, ay, bx, by, t in f.walls:
            half = max(t * s, WALL_MIN) / 2
            a, b = self.P(ax, ay), self.P(bx, by)
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            segs.append(LineString([a, b]).buffer(half, cap_style="flat"))
            for p in ((ax, ay), (bx, by)):
                ends.setdefault((round(p[0], 2), round(p[1], 2)), []).append((self.P(*p), half, ang))
        joints = [rect(pt[0], pt[1], 2 * h, 2 * h, ang) for v in ends.values() if len(v) > 1 for pt, h, ang in v]
        self.walls = clean(unary_union(segs + joints))
        self.notches = unary_union([rect(*self.P(w["x"], w["y"]), w["width_m"] * s, max(w["wall_thickness"] * s, WALL_MIN) + 1.2,
                                         w["angle_deg"]) for w in f.windows]) if f.windows else Polygon()
        # built-ins: the same cells the analysis blocks, with each one's measured height where the mesh gave one
        self.fixtures = []
        if f.fixture_mask is not None and f.fixture_mask.any():
            g = clean(self.M(mask_poly(f.fixture_mask, f.bounds[0], f.bounds[1], CELL)).buffer(0.2).buffer(-0.2), 1.0)
            items = [(self.M(Polygon(it["polygon"])), it.get("height_m") or 0.9) for it in (f.fixture_items or []) if it.get("polygon")]
            for p in polys(g):
                hs = [h for q, h in items if q.intersects(p)]
                self.fixtures.append((p, min(max(max(hs) if hs else 0.9, 0.3), 1.0) * s))
        self.furn = [(self.M(Polygon(item_polygon(it))), FURN_H.get(it.get("label", ""), 0.7) * s, it.get("label", ""))
                     for it in self.furniture if it.get("floor", 0) == self.fi]
        self.obstacles = unary_union([self.walls] + [p for p, _ in self.fixtures] + [p for p, _, _ in self.furn])
        # base: a rectangle around the walls, the label strip along the front (low y)
        x0, y0, x1, y1 = self.walls.bounds
        self.base = box(x0 - MARGIN, y0 - MARGIN - STRIP, x1 + MARGIN, y1 + MARGIN).buffer(-3).buffer(3)
        self.shift = (-(x0 - MARGIN), -(y0 - MARGIN - STRIP))
        self.rooms = [(r, self.M(r["_poly"])) for r in f.rooms]

    def entry(self):
        """(door center mm, inward unit vector, door width mm, wall angle deg) or None."""
        d = self.plan.entry
        if not d or self.plan.entry_floor != self.fi:
            return None
        a = math.radians(d["angle_deg"])
        n = (-math.sin(a), math.cos(a))
        inside = any(r["_poly"].contains(Point(d["x"] + n[0] * 0.4, d["y"] + n[1] * 0.4)) for r in self.f.rooms)
        if not inside:
            n = (-n[0], -n[1])
        return self.P(d["x"], d["y"]), n, d["width_m"] * self.s, d["angle_deg"]

    def house(self, with_furniture: bool, legend: bool = False) -> Manifold:
        top = BASE_T + WALL_H
        parts = [prism(self.base, 0, BASE_T),
                 prism(self.walls, BASE_T - 0.5, BASE_T + WALL_H * SILL_SHARE),
                 prism(self.walls.difference(self.notches), BASE_T + WALL_H * SILL_SHARE - 0.2, top)]   # a real overlap, not a sliver
        parts += [prism(p, BASE_T - 0.5, BASE_T + h) for p, h in self.fixtures]
        if with_furniture:
            parts += self.furniture_parts()
        # name and scale on the front strip, and the key to the mats' letters when a mat carries one; an arrow at
        # the front door
        bx0, by0, bx1, _ = self.base.bounds
        name = f"{self.name}  1:{self.scale:g}"
        col2 = text_poly("W WHEELCHAIR", 2.8).bounds[2] + 5.0
        # (text, cap height, x from the strip's left edge, y from its bottom); the key is two columns under the name
        lines = [(name, 4.2, 0, None)] if not legend else [
            (name, 3.6, 0, 9.0), ("W WHEELCHAIR", 2.8, 0, 5.2), ("B BLIND", 2.8, col2, 5.2),
            ("D DEAF", 2.8, 0, 1.6), ("O OLDER", 2.8, col2, 1.6)]
        for text, cap, dx, y in lines:
            label = text_poly(text, cap)
            lw, lh = label.bounds[2], label.bounds[3]
            k = min(1.0, (bx1 - bx0 - 12 - dx) / lw)
            label = affinity.scale(label, k, k, origin=(0, 0))
            y = by0 + (STRIP + MARGIN - lh * k) / 2 - 1 if y is None else by0 + y
            parts.append(prism(affinity.translate(label, bx0 + 6 + dx, y), BASE_T - 0.2, BASE_T + 0.8))
        e = self.entry()
        if e:
            (cx, cy), n, w, _ = e
            out = max(WALL_MIN, 2.0) / 2 + 1.0
            tip = (cx - n[0] * out, cy - n[1] * out)
            back = (cx - n[0] * (out + 4.5), cy - n[1] * (out + 4.5))
            side = (-n[1] * 2.6, n[0] * 2.6)
            arrow = Polygon([tip, (back[0] + side[0], back[1] + side[1]), (back[0] - side[0], back[1] - side[1])])
            arrow = arrow.difference(self.walls.buffer(0.3)).intersection(self.base)
            parts.append(prism(arrow, BASE_T - 0.2, BASE_T + 0.8))
        return solid(parts, cuts=[prism(t, BASE_T - 0.45, BASE_T + 1) for t in self.room_words()])

    def furniture_parts(self) -> list:
        return [prism(p, BASE_T - 0.5, BASE_T + h) for p, h, _ in self.furn]

    def room_words(self) -> list:
        """Room names engraved in the floor, where there is open floor for them (seen with the mats off)."""
        out = []
        busy = self.obstacles.buffer(1.0)
        for r, poly in self.rooms:
            word = ROOM_WORD.get(r["type"])
            if not word or r["area_m2"] < 4:
                continue
            free = clean(poly.buffer(-0.8).difference(busy), 2.0)
            if free.is_empty:
                continue
            biggest = max(polys(free), key=lambda p: p.area)
            t = text_poly(word, 2.8)
            tw, th = t.bounds[2], t.bounds[3]
            c = polylabel(biggest, 0.2)
            placed = affinity.translate(t, c.x - tw / 2, c.y - th / 2)
            if biggest.buffer(0.01).contains(placed.envelope):
                out.append(placed)
        return out

    # ---- the overlays
    def overlay(self, need: str) -> tuple[Manifold, dict]:
        heat = self.res["floors"][self.fi]["heat"][need]
        x0, y0 = self.f.bounds[0], self.f.bounds[1]
        cls = {c: self.M(mask_poly(heat == c, x0, y0, CELL)) for c in (2, 3, 4)}
        allowed = clean(unary_union(list(cls.values())).difference(self.obstacles.buffer(CLEAR)), 1.0)
        good, caution, problem = (clean(cls[c].intersection(allowed), 0.5) for c in (4, 3, 2))
        net = unary_union([stripes(problem, 3.6, 1.2, 45), stripes(problem, 3.6, 1.2, -45),
                           problem.difference(problem.buffer(-1.0))])
        mat = unary_union([good, caution, net]).buffer(-0.3).buffer(0.3)      # nothing thinner than 0.6 mm
        # The floor splits where the scan has a wall with no opening (and a narrow closet door can be too tight for
        # the mat): each connected part is its own piece, boxed in by its walls. A piece's footprint is the floor it
        # covers, net holes included; pieces smaller than a closet are left out (fiddly to place, nothing to show).
        pieces, areas = [], []
        for p in polys(mat):
            a = clean(p.buffer(2.0).buffer(-2.0).intersection(allowed), 1.0)
            if a.area >= MIN_PIECE_MM2:
                pieces.append(p)
                areas.append(a)
        kept = unary_union(areas)
        mat = unary_union(pieces)
        inner = kept.buffer(-0.2)

        marks, notes = self.marks(need, inner)
        marks = [(g.intersection(inner), h) for g, h in marks]
        marks = [(g, h) for g, h in marks if not g.is_empty]
        # each mark stands on a solid pad 1 mm wider than itself, so a dome in a hole of the net can't come loose
        feat_parts = [p for g, h in marks for p in (prism(g, 0, MAT_T + h), prism(g.buffer(1.0).intersection(inner), 0, MAT_T))]
        busy = unary_union([g for g, _ in marks]).buffer(1.5) if marks else Polygon()
        tag, plate, braille = self.tag(need, inner, busy, good)
        # a pull tab on every other piece, on a small solid pad at its roomiest open spot
        tabs, tab_pts = [], []
        for a in areas:
            if plate is not None and a.intersects(plate):
                continue
            spot = a.buffer(-3.6).difference(busy)
            if spot.is_empty:
                spot = a.buffer(-2.6)
            if spot.is_empty:
                continue
            c = polylabel(max(polys(spot), key=lambda p: p.area), 0.2)
            tabs += [prism(c.buffer(3.2, quad_segs=12), 0, MAT_T), prism(box(c.x - 1.0, c.y - 2.4, c.x + 1.0, c.y + 2.4), 0, MAT_T + 7.0)]
            tab_pts.append(c)
        # caution stripes stop 0.3 mm short of every mark, the label and the tabs: cleaner to read, and a stripe edge
        # lying exactly along a mark's edge would leave the two solids touching along a line
        keep_off = unary_union([g for g, _ in marks] + ([plate] if plate is not None else []) + [c.buffer(3.2) for c in tab_pts])
        ribs = stripes(caution.intersection(kept).buffer(-0.5), 2.6, 0.8, -45).difference(keep_off.buffer(0.3))
        feat_parts.append(prism(ribs, MAT_T * 0.5, MAT_T + 0.6))
        m = solid([prism(mat, 0, MAT_T)] + feat_parts + tag + tabs)
        info = {"need": need, "pieces": len(areas), "good_cm2": round(good.area / 100, 1), "caution_cm2": round(caution.area / 100, 1),
                "problem_cm2": round(problem.area / 100, 1), "marks": notes,
                "label": braille if plate is not None else "none (no open floor big enough)"}
        self._preview_cache[need] = (mat, ribs, marks, plate, tab_pts)
        return m, info

    def _door(self, x: float, y: float):
        return min(self.f.doors, key=lambda d: math.hypot(d["x"] - x, d["y"] - y))

    def _domes_at_door(self, d, h: float = 1.0) -> list:
        cx, cy = self.P(d["x"], d["y"])
        a = math.radians(d["angle_deg"])
        return [(Point(cx + math.cos(a) * k * 2.6, cy + math.sin(a) * k * 2.6).buffer(1.0, quad_segs=8), h) for k in (-1, 0, 1)]

    def _ridge(self, pts, width: float, h: float, double: bool = False) -> list:
        if len(pts) < 2:
            return []
        line = LineString([self.P(*p) for p in pts]).simplify(0.3)
        if double:
            out = []
            for off in (-1.1, 1.1):
                try:
                    out.append((line.offset_curve(off, join_style="round").buffer(width / 2, cap_style="round"), h))
                except Exception:  # noqa: BLE001  a degenerate offset: fall back to one ridge
                    return [(line.buffer(width / 2), h)]
            return out
        return [(line.buffer(width / 2, cap_style="round"), h)]

    def _nearest_inside(self, x: float, y: float, inner, keep: float = 2.2):
        """The point closest to (x, y) mm with at least `keep` mm of mat around it (a seat point sits on the sofa)."""
        room = inner.buffer(-keep)
        if room.is_empty:
            return None
        p = Point(x, y)
        if room.contains(p):
            return p
        return nearest_points(room, p)[0]

    def marks(self, need: str, inner) -> tuple[list, list]:
        res, f, out, notes = self.res, self.f, [], []
        probs = [p for p in res["problems"] if p["profile"] == need and p.get("floor", self.fi) == self.fi]
        for p in probs:
            if p["kind"] == "threshold" and p.get("x") is not None:
                out += self._domes_at_door(self._door(p["x"], p["y"]))
                notes.append(f"step domes: {p['text']}")
        if need == "wheelchair":
            for t in res["turning_circles"]:
                if t["floor"] != self.fi:
                    continue
                c = Point(*self.P(float(t["x"]), float(t["y"])))
                r = float(t["r"]) * self.s
                out.append((c.buffer(r, quad_segs=24).difference(c.buffer(r - 1.2, quad_segs=24)), 1.2))
            notes.append(f"{len([t for t in res['turning_circles'] if t['floor'] == self.fi])} turning circles (5 ft)")
            for d in f.doors:
                st = _status(d)
                if not d["is_door"] or st == "pass" or d["minor"] or (d["exterior"] and not d.get("entry")):
                    continue
                cx, cy = self.P(d["x"], d["y"])
                bar = rect(cx, cy, d["width_m"] * self.s, 1.6, d["angle_deg"])
                out.append((bar, 5.0 if st == "fail" else 2.5))
                notes.append(f"{'barrier' if st == 'fail' else 'low bar'}: {'/'.join(d['room_types'])} door {d['est_clear_in']:.0f} in clear")
        elif need == "blind":
            main = [p for p in res["paths"] if p["floor"] == self.fi and p["from"] == "entry"]
            for p in main:
                out += self._ridge(p["points"], 0.9, 0.9, double=True)
            notes.append("guide ridges: " + ", ".join(f"entry to {p['to']}" for p in main))
            lines = unary_union([LineString([self.P(*q) for q in p["points"]]) for p in main if len(p["points"]) > 1])
            for p in probs:
                if p["kind"] != "path_obstacle" or lines.is_empty:
                    continue
                piece = next((g for g, _, _ in self.furn if g.contains(Point(*self.P(p["x"], p["y"])))), Point(*self.P(p["x"], p["y"])))
                spot = nearest_points(lines, piece)[0]
                c = self._nearest_inside(spot.x, spot.y, inner, keep=3.0)
                if c is None:
                    continue
                for i in (-1, 0, 1):
                    for j in (-1, 0, 1):
                        out.append((Point(c.x + i * 2.4, c.y + j * 2.4).buffer(0.9, quad_segs=8), 1.0))
                notes.append(f"warning domes: {p['text']}")
        elif need == "deaf":
            vp = res.get("view_points") or {}
            seat = kit = None
            if vp.get("seat"):
                seat = self._nearest_inside(*self.P(*vp["seat"]), inner)
                if seat:
                    out += [(seat.buffer(1.9, quad_segs=12), 7.0), (seat.buffer(1.2, quad_segs=12), 8.0)]
                    notes.append("eye post at the main seat")
            if vp.get("kitchen") and vp.get("kitchen") != vp.get("seat"):
                kit = self._nearest_inside(*self.P(*vp["kitchen"]), inner)
                if kit:
                    out += [(kit.buffer(1.5, quad_segs=12), 4.5)]
                    notes.append("short eye post at the kitchen")
            e = self.entry()
            if e:
                (cx, cy), n, w, _ = e
                bell = Point(cx + n[0] * 4.5, cy + n[1] * 4.5)
                out += [(bell.buffer(2.6, quad_segs=16).difference(bell.buffer(1.6, quad_segs=16)), 1.2),
                        (bell.buffer(0.8, quad_segs=8), 1.6)]
                notes.append("ring at the front door")
                heat = res["floors"][self.fi]["heat"]["deaf"]
                r_, c_ = self.f.rc(self.plan.entry["x"] + n[0] * 0.35, self.plan.entry["y"] + n[1] * 0.35)   # just inside the door
                if seat and heat[r_, c_] == 4:
                    sight = LineString([seat, bell])
                    if not sight.intersects(self.walls.buffer(0.4)):
                        dashes = [sight.interpolate(t, normalized=False) for t in np.arange(3.5, sight.length - 3.5, 3.6)]
                        for p0 in dashes:
                            p1 = sight.interpolate(sight.project(p0) + 2.0)
                            out.append((LineString([p0, p1]).buffer(0.5, cap_style="round"), 0.8))
                        notes.append("dashed sight line: the front door is in view from the main seat")
                    else:
                        notes.append("front door counted as in view, but the straight line crosses a wall corner: no sight line drawn")
                elif seat:
                    notes.append("front door NOT in view from the main seat: no sight line")
        elif need == "older":
            night = [p for p in res["paths"] if p["floor"] == self.fi and p["from"] == "bed" and p["to"] == "bathroom"]
            for p in night:
                out += self._ridge(p["points"], 1.4, 1.2)
                notes.append(f"night path bed to bathroom, {p['meters']} m")
            if not night:
                notes.append("no night path drawn (no bed-to-bathroom walk on this floor)")
        return out, notes

    def tag(self, need: str, inner, busy, good):
        """The mat's name in print and braille on a solid plate, with a pull tab, on open floor away from the marks
        (preferring floor that is solid anyway). Where no spot is long enough: the word without braille, then smaller,
        then the need's letter (W, B, D, O) in print and braille. Returns (parts, plate outline, label kind)."""
        free = inner.buffer(-0.4).difference(busy)
        shapely.prepare(free)
        gp = good.buffer(0.01)
        shapely.prepare(gp)
        x0, y0, x1, y1 = inner.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        full, letter = LABEL[need], LETTER[need]
        for word, cap, with_braille in ((full, 3.6, True), (full, 3.6, False), (full, 2.8, False), (letter, 4.0, True), (letter, 4.0, False)):
            t = text_poly(word, cap)
            tw, th = t.bounds[2], t.bounds[3]
            bw = (len(word) - 1) * 6.0 + 2.5 if with_braille else 0
            w = max(tw, bw) + 3.0 + 4.0                           # 4 mm on the right for the pull tab
            h = th + 2.6 + (6.6 if with_braille else 0)
            best = None
            for rot in (0, 90):
                ww, hh = (w, h) if rot == 0 else (h, w)
                for x in np.arange(x0, x1 - ww, 1.0):
                    for y in np.arange(y0, y1 - hh, 1.0):
                        plate = box(x, y, x + ww, y + hh)
                        if not free.contains(plate):
                            continue
                        key = (0 if gp.contains(plate) else 1, rot, math.hypot(x + ww / 2 - cx, y + hh / 2 - cy))
                        if best is None or key < best[0]:
                            best = (key, rot, x, y, ww, hh)
            if best:
                break
        if not best:
            return [], None, None
        _, rot, x, y, ww, hh = best
        # built at the origin, unrotated: plate (0..w, 0..h), text along the top, braille under it, tab on the right
        parts = [prism(box(0, 0, w, h).buffer(-0.8).buffer(0.8), 0, MAT_T),
                 prism(affinity.translate(t, 1.5, h - 1.3 - th), MAT_T * 0.5, MAT_T + 0.7)]
        if with_braille:
            for bx, by in braille_dots(word, 1.5 + 1.25, h - 1.3 - th - 1.6 - 1.2):
                parts += dome(bx, by, MAT_T, d=1.5, h=0.8)
        parts.append(prism(box(w - 3.4, h / 2 - 2.4, w - 1.4, h / 2 + 2.4), 0, MAT_T + 7.0))

        def place(g):
            if rot:
                g = affinity.translate(affinity.rotate(g, 90, origin=(0, 0)), h, 0)
            return affinity.translate(g, x, y)
        kind = ("letter " + word if word == letter else "word") + (" with braille" if with_braille else ", print only")
        return [(place(g), z0, z1) for g, z0, z1 in (p for p in parts if p)], box(x, y, x + ww, y + hh), kind

    # ---- previews (top view, for checking before printing)
    _preview_cache: dict

    def preview(self, path: Path, need: str | None, px: float = 4.0) -> None:
        bx0, by0, bx1, by1 = self.base.bounds
        W, H = int((bx1 - bx0) * px), int((by1 - by0) * px)
        im = Image.new("RGB", (W, H), (250, 250, 247))
        T = lambda x, y: ((x - bx0) * px, H - (y - by0) * px)  # noqa: E731

        def fill(g, col):
            """Paint a geometry, holes left as they are (a mask, so a room inside the walls' outline isn't erased)."""
            mask = Image.new("L", (W, H), 0)
            md = ImageDraw.Draw(mask)
            for p in polys(g):
                md.polygon([T(*c) for c in p.exterior.coords], fill=255)
                for hole in p.interiors:
                    md.polygon([T(*c) for c in hole.coords], fill=0)
            im.paste(col, (0, 0, W, H), mask)

        fill(self.base, (236, 236, 232))
        for p, _ in self.fixtures:
            fill(p, (170, 160, 150))
        for p, _, _ in self.furn:
            fill(p, (176, 140, 100))
        if need:
            mat, ribs, marks, plate, tabs = self._preview_cache[need]
            col = COLOR[need]
            fill(mat, col)
            fill(ribs, tuple(int(c * 0.62) for c in col))
            for g, _ in marks:
                fill(g, tuple(int(c * 0.35) for c in col))
            if plate is not None:
                fill(plate, (255, 255, 255))
                fill(plate.buffer(-1.0), col)
            for c in tabs:
                fill(c.buffer(3.2), (255, 255, 255))
        fill(self.walls, (40, 40, 40))
        im.save(path)


def pack(items: list, plate: float = 180.0, edge: float = 0.5, gap: float = 4.0) -> list:
    """Shelf packing of (name, solid) onto square plates: rows of parts, a part turned 90 degrees when that fits
    better. Returns plates as lists of (name, placed solid)."""
    use = plate - 2 * edge
    sized = []
    for name, m in items:
        bb = m.bounding_box()
        w, d = bb[3] - bb[0], bb[4] - bb[1]
        m = m.translate([-bb[0], -bb[1], 0])
        if w > use or (d > w and d <= use):            # lay the long side along x
            m = m.rotate([0, 0, 90])
            bb = m.bounding_box()
            m = m.translate([-bb[0], -bb[1], 0])
            w, d = d, w
        if w > use or d > use:
            raise SystemExit(f"{name} is {w:.0f} x {d:.0f} mm: larger than the {plate:g} mm plate; use a smaller --scale")
        sized.append((name, m, w, d))
    sized.sort(key=lambda t: -t[3])
    plates = []      # each: {"rows": [[y, depth, x_used]], "parts": [...]}
    for name, m, w, d in sized:
        placed = False
        for pl in plates:
            for row in pl["rows"]:
                if row[2] + w <= use and d <= row[1]:
                    pl["parts"].append((name, m.translate([edge + row[2], edge + row[0], 0])))
                    row[2] += w + gap
                    placed = True
                    break
            if not placed:
                y = sum(r[1] + gap for r in pl["rows"])
                if y + d <= use:
                    pl["rows"].append([y, d, w + gap])
                    pl["parts"].append((name, m.translate([edge, edge + y, 0])))
                    placed = True
            if placed:
                break
        if not placed:
            plates.append({"rows": [[0.0, d, w + gap]], "parts": [(name, m.translate([edge, edge, 0]))]})
    return [pl["parts"] for pl in plates]


def run(ids: list[str], scale: float, wall: float, out: Path, plate: float = 180.0, estimate: bool = False,
        separate: bool = False) -> None:
    global WALL_H
    WALL_H = wall
    out.mkdir(parents=True, exist_ok=True)
    report, boxes, mats = {}, [], []
    for mid in ids:
        h = Home(mid, scale)
        h._preview_cache = {}
        d = out / h.slug
        d.mkdir(exist_ok=True)
        overlays, mat_files = [], []
        for need in NEEDS:
            m, info = h.overlay(need)
            mat_files.append(write_stl(m, d / f"overlay_{need}.stl", h.shift))
            mats.append((f"{h.slug} {need} mat", m))
            overlays.append(info)
            h.preview(d / f"preview_{need}.png", need)
        legend = any(o["label"].startswith("letter") for o in overlays)
        furnished = h.house(True, legend)
        files = [write_stl(furnished, d / "house_furnished.stl", h.shift),
                 write_stl(h.house(False, legend), d / "house.stl", h.shift)]
        boxes.append((f"{h.slug} box", furnished))
        if h.furn:
            files.append(write_stl(solid(h.furniture_parts()), d / "furniture.stl", h.shift))
        files += mat_files
        h.preview(d / "preview_house.png", None)
        ims = [Image.open(d / f"preview_{n}.png") for n in ("house", *NEEDS)]    # one sheet: the box, then each mat
        sheet = Image.new("RGB", (sum(i.width for i in ims) + 12 * (len(ims) - 1), max(i.height for i in ims)), "white")
        x = 0
        for i in ims:
            sheet.paste(i, (x, 0))
            x += i.width + 12
        sheet.save(out / f"{h.slug}_sheet.png")
        report[h.slug] = {"model_id": mid, "name": h.name, "scale": f"1:{scale:g}", "wall_mm": wall,
                          "scores": {k: v["score"] for k, v in h.res["scores"].items()},
                          "furniture": [lbl for _, _, lbl in h.furn], "files": files, "overlays": overlays}
        print(f"{h.slug}: {h.name}")
        for fi in files:
            print(f"   {fi['file']:<24} {fi['size_mm']} mm  {fi['volume_cm3']} cm3  {fi['bodies']} piece(s)"
                  f"{'' if fi['watertight'] else '  NOT WATERTIGHT'}")
    # ready-to-print plates: in one color everything shares plates (each plate is a print start and a plate swap);
    # with separate=True the boxes and the mats get their own plates, for a spool change between them
    plates, n = [], 0
    for kind, items in ((("boxes", boxes), ("mats", mats)) if separate else (("parts", boxes + mats),)):
        for parts in pack(items, plate):
            n += 1
            m = Manifold.compose([pm for _, pm in parts])
            info = write_stl(m, out / f"plate{n}_{kind}.stl")
            info["parts"] = [nm for nm, _ in parts]
            if estimate:
                from print_time import estimate as est
                sec, grams = est(m)
                info["estimate_h"], info["estimate_g"] = round(sec / 3600, 2), round(grams)
            plates.append(info)
            print(f"   {info['file']:<24} {info['size_mm'][:2]} mm  {len(parts)} part(s)"
                  + (f"  about {info['estimate_h']:.1f} h, {info['estimate_g']} g" if estimate else "")
                  + ("" if info["watertight"] else "  NOT WATERTIGHT"))
    if estimate:
        print(f"   all plates: about {sum(p['estimate_h'] for p in plates):.1f} h, {sum(p['estimate_g'] for p in plates)} g")
    report["plates"] = plates
    (out / "report.json").write_text(json.dumps(report, indent=1, default=float))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preset", choices=list(PRESETS), default="small",
                    help="small: two same-size LA one-bedrooms, 1:100, 12 mm walls (default). demo: the app's demo homes, 1:95")
    ap.add_argument("--homes", nargs="*", help="model ids, instead of the preset's")
    ap.add_argument("--scale", type=float, help="1:N, instead of the preset's")
    ap.add_argument("--wall", type=float, help="wall height in mm, instead of the preset's")
    ap.add_argument("--plate", type=float, default=180, help="printer bed in mm (default 180: Bambu A1 mini)")
    ap.add_argument("--estimate", action="store_true", help="estimate print time per plate (scripts/print_time.py)")
    ap.add_argument("--separate", action="store_true", help="boxes and mats on separate plates (to change spool between them)")
    a = ap.parse_args()
    homes, scale, wall = PRESETS[a.preset]
    run(a.homes or homes, a.scale or scale, a.wall or wall, OUT / a.preset, a.plate, a.estimate, a.separate)
