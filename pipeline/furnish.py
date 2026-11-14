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
    free = floor & ~(f.wall_mask | furn_mask)
    dist = ndi.distance_transform_edt(free) * CELL
    seed = _seed(plan, f, free)
    reach = _component(_pass_layer(f, free, dist, DOOR_M, DOOR_M - 0.055), seed)
    wide = _component(_pass_layer(f, free, dist, 0.915, DOOR_M) & reach, seed if seed and reach[seed] else None)
    rooms = {i for i in np.unique(f.room_idx[reach]) if i >= 0}
    turn = {i for i in np.unique(f.room_idx[reach & (dist >= TURN_M / 2 - CELL / 2)]) if i >= 0}
    return {"rooms": rooms, "turn": turn, "reach": reach, "wide_share": float(wide.sum()) / max(1, int(free.sum())), "dist": dist, "free": free}


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


def _place_all(plan: Plan, f: Floor, inventory: list[dict], profiles: list[str], prefs: dict, accessible: bool,
               rng: "np.random.Generator | None" = None) -> tuple[list, list]:
    door_zone = _door_zones(f, 1.2 if "wheelchair" in profiles and accessible else 0.9)
    furn = np.zeros(f.shape, bool)
    q0 = _quick(plan, f, furn)
    placed, wont = [], []
    by_type = lambda types: [i for i, r in sorted(enumerate(f.rooms), key=lambda t: -t[1]["area_m2"]) if r["type"] in types]  # noqa: E731
    has_living = bool(by_type(("Living Room",)))
    bedrooms = by_type(("Bedroom",))
    beds_placed = 0
    order = sorted(inventory, key=lambda it: -(it["w"] * it["d"]))
    for n, it in enumerate(order):
        label = it["label"]
        std = STANDARD.get(label, (it["w"], it["d"], False, "living"))
        group = std[3]
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
        best = None
        bed = next((p for p in placed if "bed" in p["label"]), None)
        sofa = next((p for p in placed if p["label"] in ("sofa", "sectional", "loveseat")), None)
        cands = _candidates(f, room_ids, it["w"], it["d"], free_standing=(label == "dining table"))
        if not accessible:
            # an ordinary layout: any wall slot that fits, with no thought for clear paths
            if rng is not None:
                cands = [cands[i] for i in rng.permutation(len(cands))]
            else:
                cands = cands[:: max(1, len(cands) // 40)]
        for rect, facing in cands:
            if not _legal(f, rect, placed, door_zone if (accessible or label != "rug") else None):
                continue
            sl = _rect_mask(f, rect)
            trial = furn.copy()
            trial[sl] = True
            score = 0.0
            if accessible:
                q = _quick(plan, f, trial)
                if not q0["rooms"] <= q["rooms"]:
                    continue                                   # a main room became unreachable
                lost_turn = len(q0["turn"] - q["turn"])
                score = 30 * q["wide_share"] - 6 * lost_turn
                front = {(0, 1): "top", (0, -1): "bottom", (1, 0): "right", (-1, 0): "left"}.get(facing)
                if front and _strip_clear(q, f, rect, front, 0.9) < 0.7 and "wheelchair" in profiles:
                    score -= 5                                 # can't roll up to its front
                if "bed" in label:
                    sides = ("left", "right") if facing and facing[0] == 0 else ("bottom", "top")
                    want = prefs.get("transfer_side")
                    clear = [_strip_clear(q, f, rect, s_, 0.92) for s_ in sides]
                    score += 8 * max(clear) + (3 if want in ("left", "right") and clear[0 if want == "left" else 1] > 0.9 else 0)
                    baths = [r for r in f.rooms if r["type"] == "Bathroom"]
                    if baths and "older" in profiles:
                        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
                        score -= 0.6 * min(math.dist((cx, cy), b["centroid"]) for b in baths)
            else:
                score = -n * 0.0
            cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
            if label == "nightstand" and bed:
                bx, by, bw, bd = bed["rect"]
                gap = max(bx - (rect[0] + rect[2]), rect[0] - (bx + bw), by - (rect[1] + rect[3]), rect[1] - (by + bd), 0)
                score += 30 - 25 * gap
            if label == "tv stand" and sofa and facing:
                sx, sy = sofa["rect"][0] + sofa["rect"][2] / 2, sofa["rect"][1] + sofa["rect"][3] / 2
                dist_ = math.dist((cx, cy), (sx, sy))
                toward = (sx - cx) * facing[0] + (sy - cy) * facing[1]
                score += (10 if 2.0 <= dist_ <= 4.5 else 0) + (14 if toward > 1.5 else -10)
            if label == "coffee table" and sofa:
                score -= 3 * abs(math.dist((cx, cy), (sofa["rect"][0] + sofa["rect"][2] / 2, sofa["rect"][1] + sofa["rect"][3] / 2)) - 1.2)
            if label in ("sofa", "sectional", "loveseat") and accessible and "deaf" in profiles and plan.entry and f.index == plan.entry_floor:
                from .unit import _visible
                vis = _visible(f, f.wall_mask | ~(f.room_idx >= 0), (cx, cy), 240, 15)
                score += 6 if (vis & _door_cells(f, plan.entry, plan.entry["width_m"] / 2, plan.entry["wall_thickness"] / 2 + 0.25)).any() else 0
            if label == "desk" and (prefs.get("works_from_home") or True):
                wd = min((math.dist((cx, cy), (w_["x"], w_["y"])) for w_ in f.windows), default=9)
                score += 4 if wd < 1.5 else 0
            if std[2] and any(math.dist((cx, cy), (w_["x"], w_["y"])) < w_["width_m"] / 2 + 0.4 for w_ in f.windows):
                continue                                       # tall pieces don't block windows
            if label == "dining table":
                kit = [r for r in f.rooms if r["type"] == "Kitchen"]
                score -= 0.8 * min((math.dist((cx, cy), k["centroid"]) for k in kit), default=0)
            if best is None or score > best[0]:
                best = (score, rect, facing)
                if not accessible:
                    break                                      # typical layout: first slot that fits
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
        _, rect, facing = best
        furn[_rect_mask(f, rect)] = True
        if "bed" in label:
            beds_placed += 1
        placed.append({"id": f"p{len(placed) + 1}", "item_id": it.get("id"), "label": label, "floor": f.index, "rect": rect,
                       "room": f.rooms[int(f.room_idx[f.rc(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)])]["id"],
                       "rotation_deg": 0 if rect[2] >= rect[3] else 90, "tall": std[2], "facing": facing})
    return placed, wont


def _reasons(plan: Plan, f: Floor, placed: list, res: dict, profiles: list[str], prefs: dict) -> list[str]:
    out = []
    q = _quick(plan, f, np.zeros(f.shape, bool))
    for p in placed:
        room = next(r for r in f.rooms if r["id"] == p["room"])["type"].lower()
        if "bed" in p["label"]:
            sides = ("left", "right") if p["facing"] and p["facing"][0] == 0 else ("bottom", "top")
            furn = np.zeros(f.shape, bool)
            for o in placed:
                furn[_rect_mask(f, o["rect"])] = True
            qq = _quick(plan, f, furn)
            best = max(sides, key=lambda s: _strip_clear(qq, f, p["rect"], s, 0.92))
            bb = next((x for x in res["paths"] if x["to"] == "bathroom" and x["from"] == "bed"), None)
            out.append(f"The bed is against a wall in the {room} with about 36 in clear on one side for transfers"
                       + (f", and {bb['meters']:.0f} m from the bathroom." if bb else "."))
        elif p["label"] in ("sofa", "sectional", "loveseat"):
            out.append(f"The {p['label']} has its back to a wall, so the middle of the room stays open"
                       + (" and the entry door stays in view." if "deaf" in profiles and res["checks"]["deaf"]["entry_visible_from_seat"]["value"] == 1 else "."))
        elif p["label"] == "tv stand":
            out.append("The TV stand faces the seating from the opposite wall.")
        elif p["label"] == "nightstand":
            out.append("The nightstand is beside the bed, within reach.")
        elif p["label"] == "dining table":
            out.append("The dining table stands clear of the walls near the kitchen, with room to pass around it.")
        elif p["label"] == "desk":
            out.append("The desk is against a wall" + (", near a window." if any(math.dist((p["rect"][0], p["rect"][1]), (w["x"], w["y"])) < 2 for w in f.windows) else "."))
    if "blind" in profiles:
        out.append("Every piece except the dining table sits against a wall, so walking paths stay straight and predictable.")
    return out


def plan_furnishing(target_model_id: str, inventory: list[dict], profiles: list[str], preferences: dict | None = None) -> dict:
    prefs = preferences or {}
    plan = load_plan(target_model_id)
    f = plan.floors[plan.entry_floor]
    inv = []
    for i, it in enumerate(inventory):
        label = (it.get("label") or "furniture").lower()
        std = STANDARD.get(label)
        w, d = (it.get("w"), it.get("d")) if it.get("w") and it.get("d") else (std[:2] if std else (1.0, 0.6))
        inv.append({"id": it.get("id") or f"i{i + 1}", "label": label, "w": round(float(w), 2), "d": round(float(d), 2)})
    empty = analyze(plan, layers=False)
    placed, wont = _place_all(plan, f, inv, profiles, prefs, accessible=True)
    after = analyze(plan, placed, layers=False)
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
        "target_model_id": target_model_id, "profiles": profiles, "floor": f.index,
        "score_empty": round(_combined(empty, profiles)),
        "score_before": round(_combined(before, profiles)), "score_after": round(_combined(after, profiles)),
        "scores_before": {p: before["scores"][p] for p in profiles}, "scores_after": {p: after["scores"][p] for p in profiles},
        "before_label": "The middle of 15 ordinary layouts (each piece in a random wall slot that fits)",
        "ordinary_scores": ordinary, "ordinary_min": ordinary[0], "ordinary_max": ordinary[-1],
        "ordinary_blocked": sum(1 for v in ordinary if v < round(_combined(after, profiles)) - 10),
        "placements": placed, "typical_placements": typical, "wont_fit": wont,
        "reasons": _reasons(plan, f, placed, after, profiles, prefs),
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
