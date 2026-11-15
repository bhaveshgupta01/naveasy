"""Furniture finder (Technical Design section 8): furnished vs furniture-removed panoramas -> floor footprints.

    python -m pipeline.furniture fetch <model_id>     # download cube faces of both views (stored; links expire)
    python -m pipeline.furniture find <model_id>      # carve footprints -> data/furniture/<model_id>.json + .png

Method (space carving on the floor plane):
  1. For every scan position, compare each pixel of the furnished and furniture-removed panorama.
  2. A pixel that looks down at the floor and did NOT change shows free floor: mark that floor cell "seen free".
  3. A floor cell that the plan says is in view of at least one scan position, but was never seen free,
     is hidden under something movable. Connected groups of such cells are furniture footprints.
The cube-face orientation convention is found automatically (the one where predicted floor pixels look like floor).
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np
import requests
from PIL import Image
from scipy import ndimage as ndi
from shapely.geometry import MultiPoint

from .clients import ROOT, matterport
from .unit import CELL, Floor, _visible, load_plan

DATA = ROOT / "data"
N = 384                      # working resolution per cube face
SIDE_FACES = (1, 2, 3, 4)
DETECT_FIXED = False
MAX_RANGE = 6.5              # m: beyond this the floor is seen at too flat an angle


def fetch(model_id: str) -> None:
    raw = matterport.geometry(model_id)
    view = (raw.get("defurnished") or {}).get("id")
    if not view:
        raise SystemExit("this model has no furniture-removed view")
    out = DATA / "panos" / model_id
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for tag, mid in (("f", model_id), ("d", view)):
        for p in matterport.panoramas(mid, "2k"):
            if p["skybox"]["status"] != "available":
                continue
            for face in (1, 2, 3, 4, 5):
                path = out / f"{p['id']}_{tag}_{face}.jpg"
                if not path.exists():
                    path.write_bytes(requests.get(p["skybox"]["children"][face], timeout=90).content)
            if tag == "f":
                meta.append({"id": p["id"], "label": p["label"], "position": p["position"], "rotation": p["rotation"]})
        print(tag, "done", flush=True)
    (out / "panos.json").write_text(json.dumps(meta))


def _quat_matrix(q: dict) -> np.ndarray:
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _side_dirs(face_slot: int, conv: tuple[int, int, int]) -> np.ndarray:
    """Unit rays (camera frame, z up) for every pixel of a side face. conv = (yaw offset in quarter turns, order, flip)."""
    off, order, flip = conv
    yaw = math.radians(90 * (off + order * face_slot))
    u = (np.arange(N) + 0.5) / N * 2 - 1
    v = 1 - (np.arange(N) + 0.5) / N * 2
    uu, vv = np.meshgrid(u * flip, v)
    fwd = np.array([math.cos(yaw), math.sin(yaw), 0.0])
    right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    d = fwd[None, None, :] + uu[..., None] * right[None, None, :] + vv[..., None] * np.array([0, 0, 1.0])
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def _down_dirs(conv: tuple[int, int]) -> np.ndarray:
    rot, flip = conv
    u = (np.arange(N) + 0.5) / N * 2 - 1
    uu, vv = np.meshgrid(u * flip, -u)
    a = math.radians(90 * rot)
    x = uu * math.cos(a) - vv * math.sin(a)
    y = uu * math.sin(a) + vv * math.cos(a)
    d = np.stack([x, y, -np.ones_like(x)], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def _load(model_id: str, pid: str, tag: str, face: int) -> np.ndarray:
    im = Image.open(DATA / "panos" / model_id / f"{pid}_{tag}_{face}.jpg").convert("RGB").resize((N, N), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32)


def _floor_hits(dirs_cam: np.ndarray, R: np.ndarray, pos: dict, floor_z: float):
    d = dirs_cam @ R.T
    down = d[..., 2] < -0.12
    t = np.where(down, (floor_z - pos["z"]) / np.minimum(d[..., 2], -1e-6), np.inf)
    x, y = pos["x"] + d[..., 0] * t, pos["y"] + d[..., 1] * t
    return x, y, down & (t * np.hypot(d[..., 0], d[..., 1]) <= MAX_RANGE)


def _cells(f: Floor, x, y, ok):
    c = ((x - f.bounds[0]) / CELL).astype(np.int64)
    r = ((y - f.bounds[1]) / CELL).astype(np.int64)
    ok = ok & (r >= 0) & (r < f.shape[0]) & (c >= 0) & (c < f.shape[1])
    return np.clip(r, 0, f.shape[0] - 1), np.clip(c, 0, f.shape[1] - 1), ok


def _pano_floor(plan, pano: dict) -> Floor | None:
    for f in plan.floors:
        for r in f.rooms:
            if pano["id"] in r.get("panos", []):
                return f
    return None


def _floor_z(f: Floor) -> float:
    e = [r["elev"] for r in f.rooms if r.get("elev") is not None]
    return float(np.median(e)) if e else 0.0


def calibrate(model_id: str, plan, panos: list[dict]) -> tuple[tuple, tuple]:
    """Pick the cube-face convention under which plan-predicted floor pixels and wall pixels differ most in color."""
    sample = [p for p in panos if _pano_floor(plan, p)][:: max(1, len(panos) // 6)][:6]

    def separation(dirs_fn, faces, conv) -> float:
        scores = []
        for p in sample:
            f = _pano_floor(plan, p)
            R = _quat_matrix(p["rotation"])
            vis = _visible(f, f.wall_mask | ~(f.room_idx >= 0), (p["position"]["x"], p["position"]["y"]), 360, MAX_RANGE)
            for slot, face in enumerate(faces):
                img = _load(model_id, p["id"], "d", face)
                x, y, ok = _floor_hits(dirs_fn(slot, conv) if faces != (5,) else dirs_fn(conv), R, p["position"], _floor_z(f))
                r, c, ok = _cells(f, x, y, ok)
                is_floor = ok & vis[r, c]
                is_wall = ok & ~vis[r, c]
                if is_floor.sum() > 500 and is_wall.sum() > 500:
                    a, b = img[is_floor], img[is_wall]
                    scores.append(float(np.linalg.norm(a.mean(0) - b.mean(0)) / (a.std(0).mean() + b.std(0).mean() + 1)))
        return float(np.mean(scores)) if scores else 0.0

    side = max(((o, s, fl) for o in range(4) for s in (1, -1) for fl in (1, -1)), key=lambda cv: separation(_side_dirs, SIDE_FACES, cv))
    down = max(((r, fl) for r in range(4) for fl in (1, -1)), key=lambda cv: separation(_down_dirs, (5,), cv))
    return side, down


def find(model_id: str, diff_threshold: float = 28.0) -> dict:
    plan = load_plan(model_id)
    panos = json.loads((DATA / "panos" / model_id / "panos.json").read_text())
    side, down = calibrate(model_id, plan, panos)
    print("cube convention: side", side, "down", down, flush=True)
    items, fixed_items, debug = [], [], {}
    for f in plan.floors:
        in_view = np.zeros(f.shape, np.int32)
        seen_free = np.zeros(f.shape, np.int32)
        changed = np.zeros(f.shape, np.int32)
        own_view = np.zeros(f.shape, np.int32)       # in view of a scan position standing in the same room
        floor_like = np.zeros(f.shape, np.int32)     # ... and looking like that room's bare floor
        opaque = f.wall_mask | ~(f.room_idx >= 0)
        used = 0
        for p in panos:
            if _pano_floor(plan, p) is not f:
                continue
            used += 1
            R = _quat_matrix(p["rotation"])
            vis = _visible(f, opaque, (p["position"]["x"], p["position"]["y"]), 720, MAX_RANGE)
            in_view += vis
            free_here = np.zeros(f.shape, bool)
            chg_here = np.zeros(f.shape, bool)
            # fixed elements (counters, toilet, tub): in the furniture-removed image, floor cells of this room
            # that never look like the floor under the tripod are covered by something built in
            pr_, pc_ = f.rc(p["position"]["x"], p["position"]["y"])
            same_room = f.room_idx == f.room_idx[pr_, pc_]
            dn = ndi.uniform_filter(_load(model_id, p["id"], "d", 5), (7, 7, 1))
            xd, yd, okd = _floor_hits(_down_dirs(down), R, p["position"], _floor_z(f))
            ring = okd & (np.hypot(xd - p["position"]["x"], yd - p["position"]["y"]) < 0.9) & \
                (np.hypot(xd - p["position"]["x"], yd - p["position"]["y"]) > 0.35)
            ref = np.median(dn[ring], axis=0) if ring.sum() > 200 else None
            like_here = np.zeros(f.shape, bool)
            for dirs, face in [(_side_dirs(s, side), fc) for s, fc in enumerate(SIDE_FACES)] + [(_down_dirs(down), 5)]:
                a, b = _load(model_id, p["id"], "f", face), _load(model_id, p["id"], "d", face)
                diff = np.abs(ndi.uniform_filter(a, (5, 5, 1)) - ndi.uniform_filter(b, (5, 5, 1))).sum(-1)
                moved = ndi.binary_opening(diff > diff_threshold, iterations=1)
                x, y, ok = _floor_hits(dirs, R, p["position"], _floor_z(f))
                r, c, ok = _cells(f, x, y, ok)
                ok &= vis[r, c]
                free_here[r[ok & ~moved], c[ok & ~moved]] = True
                chg_here[r[ok & moved], c[ok & moved]] = True
                if ref is not None:
                    bb = ndi.uniform_filter(b, (7, 7, 1))
                    lum = bb.sum(-1, keepdims=True) + 1
                    chroma = np.abs(bb / lum - ref / (ref.sum() + 1)).sum(-1)
                    looks = (chroma < 0.045) & (np.abs(lum[..., 0] - ref.sum()) < 0.45 * ref.sum())
                    like_here[r[ok & looks], c[ok & looks]] = True
            # the tripod stood here
            pr, pc = f.rc(p["position"]["x"], p["position"]["y"])
            yy, xx = np.ogrid[:f.shape[0], :f.shape[1]]
            free_here |= ((yy - pr) ** 2 + (xx - pc) ** 2) * CELL * CELL <= 0.3 ** 2
            seen_free += free_here
            changed += chg_here & ~free_here
            if ref is not None:
                own_view += vis & same_room
                floor_like += like_here & same_room
        if not used:
            continue
        floor = (f.room_idx >= 0) & ~f.wall_mask
        occ = floor & (in_view >= 1) & (seen_free == 0) & (changed >= 1)
        occ = ndi.binary_opening(occ, structure=np.ones((3, 3)), iterations=2)
        occ = ndi.binary_closing(occ, structure=np.ones((3, 3)), iterations=3) & floor
        lab, n = ndi.label(occ)
        for i in range(1, n + 1):
            m = lab == i
            area = m.sum() * CELL * CELL
            if area < 0.12:
                continue
            rr, cc = np.nonzero(m)
            pts = MultiPoint([f.xy(r, c) for r, c in zip(rr[::3], cc[::3])])
            rect = pts.minimum_rotated_rectangle
            cx, cy = pts.centroid.x, pts.centroid.y
            ri = int(f.room_idx[f.rc(cx, cy)])
            x0, y0, x1, y1 = pts.bounds
            items.append({"id": f"f{len(items) + 1}", "label": "furniture", "floor": f.index, "area_m2": round(float(area), 2),
                          "rect": [round(x0 - CELL / 2, 2), round(y0 - CELL / 2, 2), round(x1 - x0 + CELL, 2), round(y1 - y0 + CELL, 2)],
                          "polygon": [[round(a, 2), round(b, 2)] for a, b in (rect.exterior.coords[:-1] if rect.geom_type == "Polygon" else [])] or None,
                          "center": [round(cx, 2), round(cy, 2)], "room": f.rooms[ri]["id"] if ri >= 0 else None,
                          "room_type": f.rooms[ri]["type"] if ri >= 0 else None, "source": "finder", "confidence": "medium",
                          "views": int(np.median(in_view[m]))})
        # Built-in fixture detection by floor color is too noisy to trust yet (lighting, reflections): off by default.
        fixed = floor & (own_view >= 1) & (floor_like == 0) & DETECT_FIXED
        fixed = ndi.binary_opening(fixed, structure=np.ones((3, 3)), iterations=3)
        fixed = ndi.binary_closing(fixed, structure=np.ones((3, 3)), iterations=2) & floor
        labf, nf = ndi.label(fixed)
        for i in range(1, nf + 1):
            m = labf == i
            area = m.sum() * CELL * CELL
            if area < 0.2:
                continue
            rr, cc = np.nonzero(m)
            pts = MultiPoint([f.xy(r, c) for r, c in zip(rr[::3], cc[::3])])
            ri = int(f.room_idx[rr[len(rr) // 2], cc[len(cc) // 2]])
            hull = pts.convex_hull.simplify(0.08)
            fixed_items.append({"id": f"x{len(fixed_items) + 1}", "label": "fixed", "floor": f.index, "area_m2": round(float(area), 2),
                                "cells": [[int(r), int(c)] for r, c in zip(rr, cc)],
                                "polygon": [[round(a, 2), round(b_, 2)] for a, b_ in hull.exterior.coords[:-1]] if hull.geom_type == "Polygon" else None,
                                "room_type": f.rooms[ri]["type"] if ri >= 0 else None, "fixed": True, "source": "finder"})
        debug[f.index] = (in_view, seen_free, changed, occ, fixed)
    out = {"model_id": model_id, "convention": {"side": side, "down": down}, "items": items, "fixed": fixed_items}
    (DATA / "furniture").mkdir(exist_ok=True)
    (DATA / "furniture" / f"{model_id}.json").write_text(json.dumps(out))
    _debug_png(plan, panos, items, debug, DATA / "furniture" / f"{model_id}.png")
    return out


def _debug_png(plan, panos, items, debug, path, s: int = 3) -> None:
    from PIL import ImageDraw
    f = plan.floors[plan.entry_floor]
    if f.index not in debug:
        return
    in_view, seen_free, changed, occ, fixed = debug[f.index]
    img = np.full((*f.shape, 3), 255, np.uint8)
    img[f.room_idx >= 0] = (235, 235, 235)
    img[(seen_free > 0)] = (190, 230, 190)
    img[(changed > 0) & (seen_free == 0)] = (250, 215, 160)
    img[fixed] = (120, 120, 200)
    img[occ] = (200, 90, 60)
    img[f.wall_mask] = (0, 0, 0)
    im = Image.fromarray(img[::-1]).resize((f.shape[1] * s, f.shape[0] * s), Image.NEAREST)
    d = ImageDraw.Draw(im)
    T = lambda x, y: ((x - f.bounds[0]) / CELL * s, (f.shape[0] - (y - f.bounds[1]) / CELL) * s)  # noqa: E731
    for p in panos:
        x, y = T(p["position"]["x"], p["position"]["y"])
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(30, 80, 220))
        d.text((x + 5, y - 5), p["label"], fill=(30, 80, 220))
    for it in items:
        if it["floor"] == f.index:
            d.text(T(*it["center"]), f"{it['id']} {it['label']}", fill=(255, 255, 255))
    for r in f.rooms:
        d.text(T(r["centroid"][0] - 0.4, r["centroid"][1]), r["type"], fill=(90, 90, 90))
    im.save(path)

def crops(model_id: str) -> None:
    """Save one labelled crop per found item (from the scan position with the best view) for labeling."""
    plan = load_plan(model_id)
    res = json.loads((DATA / "furniture" / f"{model_id}.json").read_text())
    panos = json.loads((DATA / "panos" / model_id / "panos.json").read_text())
    side = tuple(res["convention"]["side"])
    out = DATA / "furniture" / f"{model_id}_crops"
    out.mkdir(exist_ok=True)
    tiles = []
    for it in res["items"]:
        f = plan.floors[it["floor"]]
        x0, y0, w, d = it["rect"]
        best = None
        for p in panos:
            if _pano_floor(plan, p) is not f:
                continue
            dist = math.hypot(p["position"]["x"] - it["center"][0], p["position"]["y"] - it["center"][1])
            vis = _visible(f, f.wall_mask | ~(f.room_idx >= 0), (p["position"]["x"], p["position"]["y"]), 360, MAX_RANGE)
            if not vis[f.rc(*it["center"])] or dist < 1.0:
                continue
            R = _quat_matrix(p["rotation"])
            for slot, face in enumerate(SIDE_FACES):
                x, y, ok = _floor_hits(_side_dirs(slot, side), R, p["position"], _floor_z(f))
                inside = ok & (x >= x0) & (x <= x0 + w) & (y >= y0) & (y <= y0 + d)
                n = int(inside.sum())
                if n > 40 and (best is None or n > best[0]):
                    rr, cc = np.nonzero(inside)
                    best = (n, p["id"], face, rr.min(), rr.max(), cc.min(), cc.max())
        if not best:
            continue
        _, pid, face, r0, r1, c0, c1 = best
        k = 2048 / N
        h = r1 - r0
        box = (max(0, int((c0 - 12) * k)), max(0, int((r0 - h * 1.6 - 20) * k)), min(2048, int((c1 + 12) * k)), min(2048, int((r1 + 12) * k)))
        im = Image.open(DATA / "panos" / model_id / f"{pid}_f_{face}.jpg").crop(box)
        im.thumbnail((360, 360))
        tile = Image.new("RGB", (370, 390), "white")
        tile.paste(im, (5, 25))
        from PIL import ImageDraw
        ImageDraw.Draw(tile).text((6, 6), f"{it['id']}  {it['room_type']}  {it['rect'][2]}x{it['rect'][3]} m", fill=(0, 0, 0))
        tiles.append(tile)
    cols = 4
    sheet = Image.new("RGB", (370 * cols, 390 * math.ceil(len(tiles) / cols)), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t, (370 * (i % cols), 390 * (i // cols)))
    sheet.save(out / "sheet.jpg", quality=85)
    print("wrote", out / "sheet.jpg", len(tiles), "crops")



if __name__ == "__main__":
    cmd, mid = sys.argv[1], sys.argv[2]
    if cmd == "fetch":
        fetch(mid)
    elif cmd == "crops":
        crops(mid)
    else:
        res = find(mid)
        for it in res["items"]:
            print(it["id"], it["room_type"], it["area_m2"], "m2", it["rect"], "views", it["views"])
