"""Spoken descriptions built from the measured data: a home's layout, why a home fits, a route.

Everything here is generated from numbers the pipeline measured (rooms, doors, windows, paths, minutes).
Nothing is invented: when a fact isn't known (which way a scan faces, where fixtures are) the narration
leaves it out or says so. Sentences are short and ordered so they work when read aloud.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
NEED = {"wheelchair": "a wheelchair", "blind": "someone who is blind or has low vision", "deaf": "someone who is deaf or hard of hearing",
        "older": "an older adult"}
COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
SUN = {"east": "morning sun", "south-east": "sun through the morning", "south": "sun for most of the day", "south-west": "afternoon sun",
       "west": "afternoon and evening sun", "north-west": "late evening light", "north": "soft indirect light, no direct sun",
       "north-east": "early morning light"}


def feet(m: float) -> str:
    return f"{m * 3.281:.0f} feet"


def steps(m: float) -> str:
    n = max(1, round(m / 0.7))
    return f"about {n} step{'s' if n != 1 else ''}"


def _article(room: str) -> str:
    return room.lower()


def scan_orientation(model_id: str) -> dict | None:
    """Degrees to add to a model-frame bearing to get a compass bearing, from Matterport's geo alignment (cached).
    None when the scan isn't geo-aligned."""
    path = DATA / "matterport_geo" / f"{model_id}.json"
    if path.exists():
        return json.loads(path.read_text()) or None
    out = None
    try:
        from pipeline.clients import matterport as mp
        b = mp.gql("query($id:ID!){ model(id:$id){ geocoordinates { source rotation{ x y z w } } generatedDescription { summary } } }", {"id": model_id})
        m = (b.get("data") or {}).get("model") or {}
        g = m.get("geocoordinates")
        out = {"summary": (m.get("generatedDescription") or {}).get("summary")}
        if g and g.get("rotation"):
            r = g["rotation"]
            # checked against geoLocationOf: compass bearing = model bearing - 2*atan2(z, w)
            out["offset_deg"] = -math.degrees(2 * math.atan2(r["z"], r["w"]))
            out["source"] = g.get("source")
    except Exception:  # noqa: BLE001
        out = None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out or {}))
    return out


def _compass(dx: float, dy: float, offset: float) -> str:
    bearing = (math.degrees(math.atan2(dx, dy)) + offset) % 360
    return COMPASS[int((bearing + 22.5) // 45) % 8]


def _relative(heading: tuple[float, float], to: tuple[float, float]) -> str:
    """Where `to` lies for someone facing `heading` (model frame: x east-like, y north-like, so left is counter-clockwise)."""
    ang = math.degrees(math.atan2(heading[0] * to[1] - heading[1] * to[0], heading[0] * to[0] + heading[1] * to[1]))
    a = abs(ang)
    side = "left" if ang > 0 else "right"
    if a < 20:
        return "straight ahead"
    if a < 65:
        return f"ahead and to your {side}"
    if a < 115:
        return f"to your {side}"
    if a < 160:
        return f"behind you to your {side}"
    return "behind you"


def _walk(points: list) -> str:
    """Turn-by-turn words for a path through the home: small wiggles are merged so only real turns are spoken."""
    from shapely.geometry import LineString
    if len(points) < 2:
        return ""
    pts = list(LineString(points).simplify(0.6).coords)
    legs = [[b[0] - a[0], b[1] - a[1], math.dist(a, b)] for a, b in zip(pts, pts[1:]) if math.dist(a, b) > 0.05]
    merged = []
    for leg in legs:
        if merged:
            p_ = merged[-1]
            ang = math.degrees(math.atan2(p_[0] * leg[1] - p_[1] * leg[0], p_[0] * leg[0] + p_[1] * leg[1]))
            if abs(ang) < 35 or leg[2] < 0.9:
                merged[-1] = [p_[0] + leg[0], p_[1] + leg[1], p_[2] + leg[2]]
                continue
        merged.append(leg)
    if not merged:
        return ""
    out = [f"go straight {steps(merged[0][2])}"]
    for prev, cur in zip(merged, merged[1:]):
        ang = math.degrees(math.atan2(prev[0] * cur[1] - prev[1] * cur[0], prev[0] * cur[0] + prev[1] * cur[1]))
        out.append(f"turn {'left' if ang > 0 else 'right'} and go {steps(cur[2])}")
    return ", then ".join(out)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def unit_narration(unit: dict, profile: str = "wheelchair", profiles: list[str] | None = None) -> dict:
    """A spoken tour of one floor of a scanned home, in sections so a listener can ask for the part they want.
    `unit` is the /units/{id}/heatmap response."""
    profiles = profiles or [profile]
    if not unit.get("has_graph", True) or not unit.get("rooms"):
        msg = unit.get("message") or "This scan has no measured floor plan, so I can't describe its layout."
        return {"title": unit.get("caption") or "This scan", "paragraphs": [msg], "sections": {"overview": msg}}
    geo = scan_orientation(unit["model_id"]) or {}
    offset = geo.get("offset_deg")
    main = [r for r in unit["rooms"] if r["type"] not in ("Closet", "Pantry", "Other", "Hallway", "Entrance") and r["area_m2"] >= 1.5]
    counts: dict[str, int] = {}
    for r in main:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    listing = _join([f"{n} {t.lower()}s" if n > 1 else f"a {t.lower()}" for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]) if counts else ""
    stats = unit["stats"]
    title = (unit.get("caption") or "This home").split(" – ")[0].strip()
    sec: dict[str, str] = {}

    o = f"{title}. About {stats['area_m2'] * 10.764:,.0f} square feet"
    o += f" on {stats['floors']} floors" if stats["floors"] > 1 else " on one level"
    o += f", with {listing}. " if listing else ". "
    o += " ".join(f"For {NEED[p]} it scores {unit['scores'][p]['score']} out of 100." for p in profiles)
    sec["overview"] = o

    # layout: where things are from the entry door, grouped by direction
    entry = next((d for d in unit["doors"] if d.get("entry")), None)
    if entry:
        every = [r for r in unit["rooms"] if r["area_m2"] >= 1.5]
        first = min(every, key=lambda r: math.dist(r["centroid"], (entry["x"], entry["y"]))) if every else None
        a = math.radians(entry["angle_deg"])
        normal = (-math.sin(a), math.cos(a))
        if first and (first["centroid"][0] - entry["x"]) * normal[0] + (first["centroid"][1] - entry["y"]) * normal[1] < 0:
            normal = (-normal[0], -normal[1])
        lay = f"You come in through a door about {entry['est_clear_in']:.0f} inches wide"
        lay += f", into the {first['type'].lower()}. " if first and first["type"] not in ("Other", "Bathroom", "Closet", "Bedroom", "Garage") else ". "
        groups: dict[str, list[str]] = {}
        seen_types: dict[str, int] = {}
        for r in sorted(main, key=lambda r: math.dist(r["centroid"], (entry["x"], entry["y"]))):
            if first and r["id"] == first["id"]:
                continue
            vec = (r["centroid"][0] - entry["x"], r["centroid"][1] - entry["y"])
            seen_types[r["type"]] = seen_types.get(r["type"], 0) + 1
            name = ("a second " if seen_types[r["type"]] == 2 else "another " if seen_types[r["type"]] > 2 else "the ") + r["type"].lower()
            groups.setdefault(_relative(normal, vec), []).append(f"{name}, {steps(math.hypot(*vec))}")
        order = ["straight ahead", "ahead and to your left", "ahead and to your right", "to your left", "to your right", "behind you to your left", "behind you to your right", "behind you"]
        lay += "Facing in from the doorway: " + ". ".join(f"{d[0].upper() + d[1:]}, {_join(groups[d])}" for d in order if d in groups) + "."
        sec["layout"] = lay
        ways = []
        for p in unit.get("paths", []):
            if p["from"] == "entry" and len(p["points"]) > 1 and p["to"] in ("bed", "bathroom", "kitchen"):
                dest = {"bed": "bedroom", "bathroom": "bathroom", "kitchen": "kitchen"}[p["to"]]
                ways.append(f"To the {dest}: {_walk(p['points'])}. {p['meters']:.0f} meters in all.")
        bb = next((p for p in unit.get("paths", []) if p["from"] == "bed"), None)
        if bb:
            ways.append(f"From the bed to the bathroom is {steps(bb['meters'])}.")
        if ways:
            sec["paths"] = " ".join(ways)
    else:
        sec["layout"] = "The scan doesn't record which door is the entry, so I can't give directions from it."

    # light: windows by room, compass side and the sun they get
    bits = []
    told: dict[str, int] = {}
    for r in sorted(main, key=lambda r: -r["area_m2"])[:6]:
        wins = [x for x in unit.get("windows", []) if _near_room(x, r)]
        if not wins:
            continue
        told[r["type"]] = told.get(r["type"], 0) + 1
        many = counts.get(r["type"], 1) > 1
        name = (("The largest " if told[r["type"]] == 1 else "Another ") if many else "The ") + r["type"].lower()
        bit = f"{name} has {len(wins)} window{'s' if len(wins) > 1 else ''}"
        if offset is not None:
            sides: dict[str, float] = {}
            for x in wins:
                side = _compass(x["x"] - r["centroid"][0], x["y"] - r["centroid"][1], offset)
                sides[side] = sides.get(side, 0) + x["width_m"]
            best = max(sides, key=sides.get)
            bit += f" facing {_join(list(sides))}, so it gets {SUN[best]}"
            if sides[best] >= 2.0:
                bit += f"; the glass there is about {feet(sides[best])} wide"
        bits.append(bit + ".")
    if not unit.get("windows"):
        sec["light"] = "This scan doesn't record windows, so I can't describe the light."
    elif bits:
        sec["light"] = " ".join(bits) + ("" if offset is not None else " The scan isn't aligned to the compass, so I can't say which way they face.")
        dark = [r["type"].lower() for r in main if r["type"] in ("Bedroom", "Living Room", "Kitchen") and not any(_near_room(x, r) for x in unit["windows"])]
        if dark:
            sec["light"] += f" No window is recorded in {_join(['the ' + d for d in dict.fromkeys(dark)])}."
    else:
        sec["light"] = "Windows are recorded, but none on the main rooms of this floor."

    sizes = []
    said_type: dict[str, int] = {}
    for r in sorted(main, key=lambda r: -r["area_m2"])[:6]:
        xs, ys = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
        w, d = max(xs) - min(xs), max(ys) - min(ys)
        said_type[r["type"]] = said_type.get(r["type"], 0) + 1
        which = "the" if counts.get(r["type"], 1) == 1 else ("the larger" if said_type[r["type"]] == 1 else "the other" if counts[r["type"]] == 2 else "another")
        sizes.append(f"{which} {r['type'].lower()} is about {max(w, d) * 3.281:.0f} by {min(w, d) * 3.281:.0f} feet")
    if sizes:
        sec["rooms"] = (_join(sizes) + ".").capitalize()

    doors = [d for d in unit["doors"] if d.get("is_door") and not d.get("minor") and not (d.get("exterior") and not d.get("entry"))]
    if doors:
        ok = sum(1 for d in doors if d["status"] == "pass")
        narrow = min(doors, key=lambda d: d["est_clear_in"])
        sec["doors"] = (f"{ok} of {len(doors)} main doorways are at least 32 inches clear. The narrowest is about {narrow['est_clear_in']:.0f} inches. "
                        f"A wheelchair can cover {stats['reach36_share'] * 100:.0f} percent of the floor with 36 inches of clearance.")
    built = fixtures_sentence(unit.get("fixture_items") or [])
    if built:
        sec["fixtures"] = built
    probs = [p for p in (unit.get("all_problems") or unit.get("problems", [])) if p["profile"] in profiles and p["severity"] != "low"]
    if probs:
        top = probs[:4]
        sec["problems"] = (f"{len(probs)} thing{'s' if len(probs) != 1 else ''} to know. " + " ".join(spoken_inches(p["text"]) for p in top)
                           + (f" And {len(probs) - len(top)} more." if len(probs) > len(top) else ""))
    else:
        sec["problems"] = "I found no problems for your needs in what the scan can measure."
    if built:
        sec["caveat"] = ("This comes from the 3D scan, not a visit. Counter, sink, toilet and tub heights are measured from the scan's 3D mesh "
                         "and are good to about an inch. Grab bars, door hardware, switch heights and alarms aren't checked, so look at those in person.")
    else:
        sec["caveat"] = ("This comes from the 3D scan, not a visit. This scan has no 3D mesh measurements, so the toilet, tub and counters "
                         "aren't measured; check the bathroom and kitchen in person.")
    if geo.get("summary"):
        sec["matterport"] = f"Matterport's own description: {geo['summary']}"
    order = ["overview", "layout", "paths", "rooms", "light", "doors", "fixtures", "problems", "caveat"]
    return {"title": title, "sections": sec, "paragraphs": [sec[k] for k in order if k in sec], "compass_known": offset is not None}


def spoken_inches(text: str) -> str:
    """'about 26 in clear' -> 'about 26 inches clear', for anything read aloud."""
    import re
    return re.sub(r"(\d) in\b", r"\1 inches", text)


SPOKEN_KINDS = ("counter", "island", "vanity", "toilet", "tub", "desk or table", "bench or seat", "fixed seating or bench",
                "counter or appliance", "high table or counter")


def _size(it: dict) -> str:
    w, d = it["width_m"], it["depth_m"]
    if w >= 1.2:
        return f"{w * 3.281:.0f} feet long" if it.get("shape") == "L or U shaped" or d < 0.8 else f"{w * 3.281:.0f} by {d * 3.281:.0f} feet"
    return f"{it['width_in']} by {it['depth_in']} inches"


def fixtures_sentence(items: list[dict]) -> str:
    """The built-ins of one floor, spoken: what each is (inferred), its height and size (measured from the mesh).
    Shelves, toilet tanks, platforms and specks under 0.15 m2 are left out, and one toilet and one tub per room
    (the largest); empty when nothing worth saying was measured."""
    keep, seen = [], set()
    for it in sorted(items, key=lambda i: -i["area_m2"]):
        if it["kind"] not in SPOKEN_KINDS or (it["area_m2"] < 0.15 and it["kind"] != "toilet"):
            continue
        if it["kind"] in ("toilet", "tub"):
            if (it["room"], it["kind"]) in seen:
                continue
            seen.add((it["room"], it["kind"]))
        keep.append(it)
    rooms: dict[str, tuple[str, list[str]]] = {}
    count: dict[tuple, int] = {}
    high = False
    for it in sorted(keep, key=lambda i: (i["room_type"], str(i["room"]), -i["width_m"]))[:8]:
        h = round(it["height_m"] / 0.0254)
        n = count[(it["room"], it["kind"])] = count.get((it["room"], it["kind"]), 0) + 1
        a = ("an" if it["kind"][0] in "aeiou" else "a") if n == 1 else "a second" if n == 2 else "another"
        if it["kind"] == "toilet":
            bit = f"the toilet, its seat about {h} inches high"
        elif it["kind"] == "tub":
            bit = f"the tub, its rim about {h} inches high"
        else:
            bit = f"{a} {it['kind']} about {h} inches high, {_size(it)}"
            if it["kind"] in ("counter", "island", "vanity", "counter or appliance", "desk or table") and h > 34:
                high = True
        if it.get("possibly_movable"):
            bit += ", possibly movable"
        rooms.setdefault(str(it["room"]), (it["room_type"], []))[1].append(bit)
    if not rooms:
        return ""
    types = [t for t, _b in rooms.values()]
    said: dict[str, int] = {}
    parts = []
    for room_type, bits in rooms.values():
        said[room_type] = said.get(room_type, 0) + 1
        name = "an unlabeled area" if room_type == "Other" else room_type.lower()
        if room_type != "Other":
            name = f"the {name}" if types.count(room_type) == 1 else f"{'one' if said[room_type] == 1 else 'another'} {name}"
        listed = bits[0] if len(bits) == 1 else "; ".join(bits[:-1]) + "; and " + bits[-1]
        parts.append(f"In {name} there is {listed}.")
    out = "Built-ins measured from the 3D scan. " + " ".join(parts)
    if high:
        out += " A seated user needs a counter, sink or work surface 34 inches or lower."
    return out + " What each one is comes from its room, size and height, so confirm it on a visit."


def _near_room(win: dict, room: dict) -> bool:
    """A window belongs to the room whose outline it sits on (within 25 cm)."""
    pts = room["polygon"]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy or 1e-9
        t = max(0.0, min(1.0, ((win["x"] - x1) * dx + (win["y"] - y1) * dy) / length2))
        if math.hypot(win["x"] - (x1 + t * dx), win["y"] - (y1 + t * dy)) < 0.25:
            return True
    return False


def home_reason(b: dict, profiles: list[str]) -> str:
    """Why this home ranks where it does, in a few spoken sentences. `b` is the /buildings/{id} response."""
    score = b["combined"]["score"]
    word = "meets most of our checks" if score >= 75 else ("has some barriers" if score >= 50 else "has major barriers")
    import re
    address = re.sub(r"(\d{5})\d{4}\b", r"\1", b["address"])
    out = [f"{b['name']}, at {address}. It scores {score} out of 100 and {word}, with {b['combined']['confidence']} confidence."]
    c = b["components"]
    out.append(f"The building: {c['vertical_access']['why']}; {c['age']['why']}.")
    units = [u for u in b["units"] if u.get("dwelling") and u.get("scores")]
    if units:
        best = max(units, key=lambda u: min(u["scores"][p]["score"] for p in profiles))
        bs = min(best["scores"][p]["score"] for p in profiles)
        line = f"Inside, {len(units)} unit{'s' if len(units) > 1 else ''} measured from 3D scans. The best, {best['caption'] or 'a scanned unit'}, scores {bs}"
        st = best.get("stats") or {}
        if st.get("narrowest_main_door_in"):
            line += f"; its narrowest main doorway is about {st['narrowest_main_door_in']:.0f} inches"
        out.append(line + ".")
        if best.get("problems"):
            out.append("Watch for: " + best["problems"][0]["text"])
    else:
        out.append("No unit here has a measured floor plan yet, so this is building evidence only.")
    near = [n for n in b.get("nearest", []) if n["minutes"].get(profiles[0]) is not None]
    if near:
        pick = [n for n in near if n["category"] in ("pharmacy", "grocery", "transit", "hospital")]
        est = "about " if near[0]["estimated"] else ""
        out.append("Nearby: " + ", ".join(f"{n['label'].lower()} {est}{n['minutes'][profiles[0]]:.0f} minutes" for n in pick) + f" for {NEED[profiles[0]]}.")
    if b.get("rent_min"):
        out.append(f"Rent starts at {b['rent_min']:,} dollars a month.")
    return " ".join(out)


def route_narration(r: dict) -> str:
    dest = r["destination"].get("name") or (r.get("category") or "the destination")
    out = [f"To {dest}: {r['minutes']:.0f} minutes, {r['meters']} meters, for {NEED[r['profile']]}."]
    if r.get("crossings") is not None:
        out.append(f"{r['crossings']} street crossing{'s' if r['crossings'] != 1 else ''}." if r["crossings"] else "No street crossings are recorded on the way.")
    if r.get("elevation"):
        g = r["elevation"]["max_grade_pct"]
        out.append("It is nearly flat." if g < 2 else f"The steepest stretch is about {g:.0f} percent." if g > 5 else "It has gentle slopes, none over 5 percent.")
    unknown = sum(s["meters"] for s in r["stretches"] if s["status"] == "grey")
    if unknown:
        out.append(f"For {unknown} meters the map has no sidewalk record, so I can't promise one.")
    if r["hard_spots"]:
        out.append(f"{len(r['hard_spots'])} spot{'s' if len(r['hard_spots']) != 1 else ''} to watch. " + " ".join(h["text"] for h in r["hard_spots"][:3]))
    steps_ = [s for s in r.get("steps", []) if s.get("text")][:8]
    if steps_:
        out.append("Turn by turn: " + " ".join(f"{s['text']}{'' if s['text'].endswith('.') else '.'}" + (f" Continue {s['meters']} meters." if s["meters"] > 30 else "") for s in steps_))
    return " ".join(out)


# ---------------------------------------------------------------- the broker's voice

PIECE = {"queen bed": "queen bed", "king bed": "king bed", "full bed": "full bed", "twin bed": "twin bed", "bed": "bed", "tv stand": "TV stand"}


def _say_in(text: str) -> str:
    """Abbreviations a speech engine would read badly."""
    import re
    text = re.sub(r"(\d+) in turning", r"\1-inch turning", text)
    return re.sub(r"(\d) in\b", r"\1 inches", text.replace(" m from", " meters from").replace(" ft)", " feet)"))


def _door_into(unit: dict, room: dict) -> dict | None:
    """The doorway you would come into this room through: the entry door if the room has it, else its door nearest the entry."""
    doors = [d for d in unit.get("doors", []) if d.get("is_door", True) and _near_room(d, room) and not (d.get("exterior") and not d.get("entry"))]
    if not doors:
        return None
    entry = next((d for d in unit.get("doors", []) if d.get("entry")), None)
    if entry and any(d is entry or d.get("id") == entry.get("id") for d in doors):
        return entry
    if entry:
        return min(doors, key=lambda d: math.dist((d["x"], d["y"]), (entry["x"], entry["y"])))
    return doors[0]


def _inward(door: dict, room: dict) -> tuple[float, float]:
    a = math.radians(door["angle_deg"])
    n = (-math.sin(a), math.cos(a))
    c = room["centroid"]
    if (c[0] - door["x"]) * n[0] + (c[1] - door["y"]) * n[1] < 0:
        n = (-n[0], -n[1])
    return n


WALL = {"straight ahead": "against the far wall", "ahead and to your left": "against the left wall", "to your left": "against the left wall",
        "behind you to your left": "against the left wall", "ahead and to your right": "against the right wall", "to your right": "against the right wall",
        "behind you to your right": "against the right wall", "behind you": "against the wall with the door"}


def plan_narration(plan: dict, unit: dict, profiles: list[str] | None = None) -> dict:
    """A furnishing plan described the way the home tour is: room by room, standing in each room's doorway and facing in.
    `plan` is plan_furnishing()'s result and `unit` the target's /heatmap response. Every position comes from the plan's
    rectangles; nothing is placed by the narration."""
    import re
    rooms = {r["id"]: r for r in unit.get("rooms", [])}
    entry = next((d for d in unit.get("doors", []) if d.get("entry")), None)
    by_room: dict[str, list] = {}
    for p in plan.get("placements", []):
        by_room.setdefault(p.get("room"), []).append(p)

    def room_order(rid):
        r = rooms.get(rid)
        return math.dist(r["centroid"], (entry["x"], entry["y"])) if r and entry else 0

    parts = []
    for rid in sorted(by_room, key=room_order):
        room = rooms.get(rid)
        if not room:
            continue
        door = _door_into(unit, room)
        own = door is not None
        if not door:     # an open-plan room: describe it from the front door instead
            door = entry
        name = room["type"].lower()
        pieces = []
        for p in by_room[rid]:
            x, y, w, d = p["rect"]
            c = (x + w / 2, y + d / 2)
            label = PIECE.get(p["label"], p["label"])
            if not door:
                pieces.append(f"Your {label} goes in the {name}.")
                continue
            n = _inward(door, room)
            v = (c[0] - door["x"], c[1] - door["y"])
            where = _relative(n, v)
            facing = p.get("facing")
            far = steps(math.hypot(*v))
            if facing:
                wall = WALL[_relative(n, (-facing[0], -facing[1]))]
                side = "left" if n[0] * v[1] - n[1] * v[0] > 0 else "right"
                if wall == "against the wall with the door":
                    pieces.append(f"Your {label} goes against the wall with the door, to the {side} of the door.")
                elif (wall == "against the far wall" and "ahead" in where) or (wall.endswith(f"{side} wall") and side in where and "behind" not in where):
                    pieces.append(f"Your {label} goes {wall}, {where}, {far} in.")
                else:   # the wall it backs onto is a short partition; say where it is rather than a wall that sounds wrong
                    pieces.append(f"Your {label} goes against a wall {where}, {far} in.")
            else:
                pieces.append(f"Your {label} stands clear of the walls, {where}, {far} in.")
        lead = (f"In the {name}, coming in through its door. " if own else f"In the {name}, facing in from the front door. ") if door else f"In the {name}. "
        parts.append(lead + " ".join(pieces))
    for p in plan.get("placements", []):
        if "bed" in p["label"]:
            why = next((r for r in plan.get("reasons", []) if r.startswith("The bed")), None)
            m = re.search(r"with about (\d+) in clear on one side for transfers(?:, and (\d+) m from the bathroom)?", why or "")
            if m:
                parts.append(f"The bed leaves about {m.group(1)} inches clear on one side, for getting in and out"
                             + (f", and the bathroom is {m.group(2)} meters from it." if m.group(2) else "."))
            break
    out = {"placed": " ".join(parts)}
    if plan.get("wont_fit"):
        groups: dict[str, list[str]] = {}
        for w in plan["wont_fit"]:
            groups.setdefault(w["reason"], []).append(PIECE.get(w["label"], w["label"]))
        bits = []
        for reason, labels in groups.items():
            names = _join([f"{labels.count(x)} {x}s" if labels.count(x) > 1 else f"your {x}" for x in dict.fromkeys(labels)])
            m = re.match(r"No (\w+) space found", reason)
            if m:
                bits.append(f"The scan labels no {m.group(1)} in this home, so I couldn't place {names}.")
            else:
                bits.append(f"{names[0].upper() + names[1:]} won't fit: {_say_in(reason)}")
        out["wont_fit"] = " ".join(bits)
    if plan.get("score_after") is not None:
        s = f"With this layout the home scores {plan['score_after']} for you"
        if plan.get("ordinary_min") is not None and plan.get("ordinary_max") is not None and plan["ordinary_max"] > plan["ordinary_min"]:
            s += f"; ordinary layouts of the same pieces score {plan['ordinary_min']} to {plan['ordinary_max']}"
        out["score"] = s + "."
    probs = [p for p in plan.get("problems", []) if p.get("severity") == "high" and p.get("profile") in (profiles or [p.get("profile")])]
    if probs:
        out["problems"] = "Still to know: " + _say_in(probs[0]["text"])
    out["text"] = " ".join(out[k] for k in ("placed", "wont_fit", "score", "problems") if k in out)
    return out


def _weakness(b: dict, unit: dict | None, profiles: list[str]) -> str:
    """The one thing a broker should say before the client finds it: the most serious measured problem, else the biggest unknown."""
    probs = [p for p in ((unit or {}).get("all_problems") or (unit or {}).get("problems") or []) if p["profile"] in profiles]
    high = [p for p in probs if p["severity"] == "high"]
    if high:
        return _say_in(high[0]["text"])
    c = b.get("components") or {}
    if b.get("walkup") or (c.get("vertical_access", {}).get("value", 1) < 0.5 and (b.get("stories") or 1) > 1):
        return f"the building is a walk-up: {c.get('vertical_access', {}).get('why', 'no elevator recorded')}"
    if not unit:
        return "nothing inside has been measured from a 3D scan yet, so the inside is unknown"
    med = [p for p in probs if p["severity"] == "medium"]
    if med:
        return _say_in(med[0]["text"])
    far = [n for n in b.get("nearest", []) if n["category"] in ("pharmacy", "grocery", "transit") and (n["minutes"].get(profiles[0]) or 0) > 20]
    if far:
        return f"the nearest {far[0]['label'].lower()} is about {far[0]['minutes'][profiles[0]]:.0f} minutes away"
    return "I found no barrier in what the scan measures; the entrance and parking haven't been checked yet"


def caption_words(cap: str) -> str:
    """'1BR, 1BA - 742SF' -> 'one-bedroom, one-bath unit'; 'Matisse 1x1.5' -> 'Matisse, 1 bedroom and 1.5 baths'."""
    import re
    cap = re.sub(r"\s*[-–]\s*\d[\d,]*\s*SF\b", "", cap or "").strip()
    words = {"0": "studio", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five"}
    bed = re.search(r"\b(\d)\s*(?:BR|BD|bed(?:room)?s?)\b", cap, re.I)
    bath = re.search(r"\b(\d(?:\.\d)?)\s*(?:BA|bath(?:room)?s?)\b", cap, re.I)
    if bed and bath:
        b = "studio" if bed.group(1) == "0" else f"{words.get(bed.group(1), bed.group(1))}-bedroom"
        return f"{b}, {words.get(bath.group(1), bath.group(1))}-bath unit"
    if re.fullmatch(r"(?i)studio\b.*", cap):
        return "studio"
    m = re.search(r"\b(\d)\s*x\s*(\d(?:\.5)?)\b", cap)
    if m:
        name = cap[:m.start()].strip(" ,-")
        bits = f"{m.group(1)} bedroom{'s' if m.group(1) != '1' else ''} and {m.group(2)} bath{'s' if m.group(2) != '1' else ''}"
        return f"{name}, {bits}" if name else bits
    return cap


def broker_pitch(b: dict, unit: dict | None, profiles: list[str], general: bool = False) -> str:
    """What a good broker says about one listing: rent, layout, what was measured inside, what's close in minutes,
    the main weakness, and what to verify before signing. `b` is the /buildings/{id} response, `unit` the best
    measured unit's /heatmap response (or None)."""
    import re
    address = re.sub(r"(\d{5})\d{4}\b", r"\1", b.get("address") or "").replace(", TX", "").replace(", CA", "")
    out = [f"{b['name']}, at {address}." if address else f"{b['name']}."]
    if b.get("rent_min"):
        out.append(f"Rent starts at {b['rent_min']:,} dollars a month" + (f", and goes up to {b['rent_max']:,}" if b.get("rent_max") and b["rent_max"] > b["rent_min"] else "") + ".")
    else:
        out.append("The listing has no rent on record.")
    weak = _weakness(b, unit, ["older"] if general else profiles)   # with no particular need, effort and stairs still matter
    score = b["combined"]["score"]
    who = "for everyday ease of getting around" if general else "for " + " and ".join(NEED[p] for p in profiles)
    verdict = "It's one of the stronger options" if score >= 75 else ("It's workable, with some barriers" if score >= 50 else "It has major barriers")
    out.append(f"{verdict} {who}: it scores {score} out of 100, with {b['combined']['confidence']} confidence.")
    c = b.get("components") or {}
    bits = [c[k]["why"] for k in ("vertical_access", "age") if c.get(k, {}).get("why")]
    if bits:
        out.append("The building: " + "; ".join(bits) + ".")
    if unit and unit.get("rooms"):
        st = unit.get("stats") or {}
        counts: dict[str, int] = {}
        for r in unit["rooms"]:
            if r["type"] in ("Bedroom", "Bathroom", "Kitchen", "Living Room", "Dining Room", "Office") and r["area_m2"] >= 1.5:
                counts[r["type"]] = counts.get(r["type"], 0) + 1
        rooms = _join([f"{n} {t.lower()}s" if n > 1 else f"a {t.lower()}" for t, n in counts.items()]) if counts else "rooms the scan didn't label"
        cap = caption_words((unit.get("caption") or "").split(" – ")[0].strip())
        line = f"Inside, I measured {('the ' + cap) if cap else 'a unit'} from its 3D scan: about {st.get('area_m2', 0) * 10.764:,.0f} square feet, with {rooms}"
        line += f", on {st['floors']} floors" if st.get("floors", 1) > 1 else ", all on one level"
        out.append(line + ".")
        if st.get("narrowest_main_door_in") and not general and "wheelchair" in profiles:
            out.append(f"The narrowest main doorway is about {st['narrowest_main_door_in']:.0f} inches clear; a wheelchair needs 32.")
        if "wheelchair" in profiles and not general and "turning circle" not in weak:
            turns = {t["room"] for t in unit.get("turning_circles", [])}
            need = [r for r in unit["rooms"] if r["type"] in ("Bedroom", "Bathroom", "Kitchen")]
            missing = sorted({r["type"].lower() for r in need if r["id"] not in turns})
            if need:
                out.append("A five-foot turning circle fits in every bedroom, bathroom and kitchen." if not missing
                           else f"A five-foot turning circle doesn't fit in the {_join(missing)}.")
    elif not general:
        out.append("No unit here has a measured floor plan yet, so what I know is the building record.")
    near = [n for n in b.get("nearest", []) if n["minutes"].get(profiles[0]) is not None and n["category"] in ("transit", "grocery", "pharmacy", "hospital", "park")]
    if near:
        est = "about " if near[0].get("estimated") else ""
        out.append("Close by, " + ("on foot" if general else f"for {NEED[profiles[0]]}") + ": "
                   + _join([f"{n['label'].lower()} right by the door" if n["minutes"][profiles[0]] < 1 else
                            f"{n['label'].lower()} {est}{n['minutes'][profiles[0]]:.0f} minute{'s' if round(n['minutes'][profiles[0]]) != 1 else ''}" for n in near]) + ".")
    out.append("The main weakness: " + weak.rstrip(".") + ".")
    check = [x for x in b.get("checklist", []) if not x.startswith("We did not check")][:2]
    if check:
        out.append("Before you sign: " + " ".join(check))
    return " ".join(out)


def home_list(homes: list[dict], n_found: int, general: bool = False) -> str:
    """A short numbered list for someone listening, ending with the question."""
    words = ["One", "Two", "Three", "Four", "Five"]
    lines = []
    for i, h in enumerate(homes[:3]):
        line = f"{words[i]}: {h['name']}"
        line += f", from {h['rent']:,} dollars a month" if h.get("rent") else ", rent not listed"
        line += f", scores {h['score']}"
        line += "." if h.get("inside") else ", inside not measured yet."
        lines.append(line)
    k = len(lines)
    ask = "Which would you like to hear about: " + ("one, two or three?" if k == 3 else "one or two?" if k == 2 else "this one?")
    return f"I found {n_found:,} homes. Here are the best {k}. " + " ".join(lines) + " " + ask


# ---------------------------------------------------------------- the venue (the building we are standing in)

def venue_doors(d: dict, near: str | None = None) -> str:
    """Door widths at the venue, spoken; each number says whether a tape measure has checked it."""
    doors = d.get("doors") or []
    if not doors:
        return "The scan has no doorways I can measure there."
    checked = [x for x in doors if (x.get("evidence") or {}).get("checked")]
    src = "Checked with a tape measure where noted; the rest are from the scan." if checked else "These are from the 3D scan, not yet checked against a tape measure."
    if near:
        mine = [x for x in doors if any(near.lower() in r.lower() for r in x.get("rooms", []))]
        if mine:
            bits = [f"about {x['clear_in']:.0f} inches clear, between the {_join([r.lower() for r in x['rooms']])}"
                    + (f" ({x['evidence']['says']})" if (x.get("evidence") or {}).get("checked") else "") for x in mine[:3]]
            return f"The {near.lower()} has {len(mine)} doorway{'s' if len(mine) > 1 else ''}: " + "; ".join(bits) + ". A wheelchair needs 32 inches. " + src
    ok = sum(1 for x in doors if x["status"] == "pass")
    ent = [x for x in doors if x.get("entrance")]
    narrow = min(doors, key=lambda x: x["clear_in"])
    out = [f"{ok} of {len(doors)} doorways in the scan are at least 32 inches clear, what a wheelchair needs."]
    if ent:
        out.append(f"The main entrance doors are about {ent[0]['clear_in']:.0f} inches clear.")
    out.append(f"The narrowest is about {narrow['clear_in']:.0f} inches, on {narrow['floor_label'].lower()}, between the {_join([r.lower() for r in narrow.get('rooms') or ['rooms']])}.")
    if d.get("artefacts"):
        out.append(f"{len(d['artefacts'])} narrow gap{'s' if len(d['artefacts']) > 1 else ''} in the scan's walls {'are' if len(d['artefacts']) > 1 else 'is'} too narrow to be a real door, so I left {'them' if len(d['artefacts']) > 1 else 'it'} out; that needs a site visit.")
    return " ".join(out) + " " + src


def venue_stairs(s: dict, way: dict | None = None) -> str:
    out = []
    fl = s.get("floors") or []
    if len(fl) >= 2:
        out.append(f"The scan covers {_join([f['label'].lower() for f in fl])}, about {abs(fl[-1]['height_m'] - fl[0]['height_m']):.0f} meters apart.")
    if s.get("stairs"):
        main = max(s["stairs"], key=lambda x: x["area_m2"])
        out.append(f"The main flight of stairs is on {main['floor'].lower()}, found in the 3D mesh.")
    if way and way.get("spoken"):
        first = way["spoken"].split(" The narrowest")[0]
        out.append("From the entrance: " + first[0].lower() + first[1:] if first else "")
    out.append(f"Elevator: {s.get('elevator') or 'unknown'}.")
    for r in s.get("raised_parts") or []:
        out.append(f"Part of {r['floor'].lower()}, about {r['area_m2']:.0f} square meters, is {r['says']}.")
    return " ".join(x for x in out if x)


def venue_outside(o: dict) -> str:
    out = [f"From {o['from']} to the {o['to']}: about {o['minutes']:.0f} minutes and {o['meters']:,} meters for {NEED.get(o['profile'], 'you')}"
           + (f", with {o['crossings']} street crossing{'s' if o['crossings'] != 1 else ''}." if o.get("crossings") is not None else ".")]
    if o.get("hard_spots"):
        out.append(f"{len(o['hard_spots'])} spot{'s' if len(o['hard_spots']) != 1 else ''} to watch. " + " ".join(_say_in(h) for h in o["hard_spots"][:2])
                   + (f" And {len(o['hard_spots']) - 2} more." if len(o["hard_spots"]) > 2 else ""))
    out += [n for n in o.get("notes") or []][:1]
    if o.get("caveat"):
        out.append(o["caveat"])
    return " ".join(out).replace("%", " percent")


def speakable(text: str) -> str:
    """Units and abbreviations as they should be heard: "44 m" -> "44 meters", "1 meters" -> "1 meter"."""
    import re
    text = re.sub(r"(\d) cm\b", r"\1 centimeters", text)
    text = re.sub(r"(\d) m\b(?![²2])", r"\1 meters", text)
    text = re.sub(r"(\d) m2\b|(\d) m²", lambda m: (m.group(1) or m.group(2)) + " square meters", text)
    text = re.sub(r"\b1 (meters|minutes|steps|inches|feet)\b", lambda m: "1 " + m.group(1)[:-1], text)
    return text



# ---------------------------------------------------------------- the walk: a tour the 3D view can follow

FIXTURE_WORD = {"counter": "a counter", "island": "a kitchen island", "vanity": "a vanity", "toilet": "the toilet", "tub": "the tub",
                "desk": "a desk", "bench": "a bench", "fixed seating": "fixed seating", "table": "a table"}


def _grid_steps(unit: dict):
    """Walking distance over the floor (meters, 4-neighbour BFS on the heat grid) from a point, or None without a grid."""
    from collections import deque
    h = unit.get("heat") or {}
    vals = h.get("values")
    if not vals:
        return None
    cell, (x0, y0) = h["cell_m"], h["origin"]
    ny, nx = len(vals), len(vals[0])
    rc = lambda x, y: (min(max(int((y - y0) / cell), 0), ny - 1), min(max(int((x - x0) / cell), 0), nx - 1))  # noqa: E731

    def nearest_floor(r, c):
        if vals[r][c] >= 2:
            return r, c
        for k in range(1, 30):
            for dr in range(-k, k + 1):
                for dc in (-k, k):
                    for rr, cc in ((r + dr, c + dc), (r + dc, c + dr)):
                        if 0 <= rr < ny and 0 <= cc < nx and vals[rr][cc] >= 2:
                            return rr, cc
        return None

    def from_point(x, y):
        s = nearest_floor(*rc(x, y))
        dist = {}
        if not s:
            return lambda *_: None
        dist[s] = 0
        q = deque([s])
        while q:
            r, c = q.popleft()
            for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if 0 <= rr < ny and 0 <= cc < nx and vals[rr][cc] >= 2 and (rr, cc) not in dist:
                    dist[(rr, cc)] = dist[(r, c)] + 1
                    q.append((rr, cc))

        def to(x2, y2):
            t = nearest_floor(*rc(x2, y2))
            return dist[t] * cell * 1.1 if t in dist else None     # 4-neighbour steps run a little long on diagonals; 1.1 evens it
        return to
    return from_point


def walk_tour(unit: dict, profile: str = "wheelchair", profiles: list[str] | None = None) -> dict | None:
    """A walk through one floor that a 3D view can follow: stand just inside the front door, then go room by room in
    walking order. Each stop has a sentence (where the room is from the way you are facing, its size, the built-ins and
    windows on your left or right, its problems) and the point to stand on and look toward. Built from measured geometry
    only; None when the scan has no entry door or rooms."""
    from shapely.geometry import Point, Polygon
    profiles = profiles or [profile]
    if not unit.get("rooms"):
        return None
    entry = next((d for d in unit.get("doors", []) if d.get("entry")), None)
    if not entry:
        return None
    rooms = [r for r in unit["rooms"] if r["type"] not in ("Closet", "Pantry", "Void") and r["area_m2"] >= (2.0 if r["type"] != "Other" else 6.0)]
    polys = {r["id"]: Polygon(r["polygon"]).buffer(0) for r in unit["rooms"] if len(r["polygon"]) >= 3}
    label = lambda r: "space the scan doesn't name" if r["type"] == "Other" else r["type"].lower()  # noqa: E731
    # the room you step into: the one holding the spot just inside the door (try both sides of the doorway)
    a = math.radians(entry["angle_deg"])
    first, n = None, (-math.sin(a), math.cos(a))
    for sign in (1, -1):
        cand = (entry["x"] + sign * n[0] * 0.9, entry["y"] + sign * n[1] * 0.9)
        hit = next((r for r in unit["rooms"] if r["id"] in polys and polys[r["id"]].contains(Point(cand))), None)
        if hit:
            first, n = hit, (sign * n[0], sign * n[1])
            break
    if first is None:
        first = min(rooms, key=lambda r: math.dist(r["centroid"], (entry["x"], entry["y"])))
        n = _inward(entry, first)
    here = (entry["x"] + n[0] * 0.9, entry["y"] + n[1] * 0.9)
    if first not in rooms:
        rooms = [first] + rooms
    heading = n
    walker = _grid_steps(unit)
    floor = unit.get("floor", 0)

    def belongs(p, r):
        """A door's problem belongs to the room the door is named for ("Bedroom door"); anything else to the room it is in."""
        poly = polys.get(r["id"])
        if not poly:
            return False
        if p["kind"] in ("narrow_door", "threshold"):
            return p["text"].lower().startswith(r["type"].lower()) and poly.buffer(0.3).contains(Point(p["x"], p["y"]))
        return poly.contains(Point(p["x"], p["y"]))

    def stand_point(r):
        c = r["centroid"]
        return (c[0], c[1])

    def size(r):
        xs, ys = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
        w, d = max(xs) - min(xs), max(ys) - min(ys)
        return f"about {max(w, d) * 3.281:.0f} by {min(w, d) * 3.281:.0f} feet"

    def around(r, at, facing):
        bits = []
        for f in unit.get("fixture_items") or []:
            if f.get("room") != r["id"] or f.get("kind") in ("toilet tank", "shelf or ledge", "raised floor or platform"):
                continue
            where = _relative(facing, (f["x"] - at[0], f["y"] - at[1]))
            what = FIXTURE_WORD.get(f["kind"], f"a {f['kind']}")
            hi = f" about {f['height_in']:.0f} inches high" if f.get("height_in") and f["kind"] in ("counter", "island", "vanity", "desk", "table") else ""
            bits.append(f"{where}, {what}{hi}")
        wins = [w for w in unit.get("windows", []) if _near_room(w, r)]
        if wins:
            sides = list(dict.fromkeys(_relative(facing, (w["x"] - at[0], w["y"] - at[1])) for w in wins))
            bits.append(f"{_join(sides)}, {'a window' if len(wins) == 1 else 'windows'}")
        return bits[:4]

    steps = []
    door_txt = f"the front door, about {entry['est_clear_in']:.0f} inches wide" if entry.get("est_clear_in") else "the front door"
    steps.append({"text": f"We start at {door_txt}, facing in. You're in the {label(first)}.",
                  "x": round(here[0], 2), "y": round(here[1], 2), "look": [round(here[0] + n[0] * 2, 2), round(here[1] + n[1] * 2, 2)], "floor": floor})
    seen_types: dict[str, int] = {}
    todo = [r for r in rooms]
    told_probs: set = set()
    while todo:
        dist_from = walker(*here) if walker else None

        def cost(r):
            d = dist_from(*stand_point(r)) if dist_from else None
            return d if d is not None else math.dist(here, stand_point(r)) * 1.3
        r = min(todo, key=cost)
        todo.remove(r)
        to = stand_point(r)
        meters = cost(r)
        vec = (to[0] - here[0], to[1] - here[1])
        seen_types[r["type"]] = seen_types.get(r["type"], 0) + 1
        name = ("the " if seen_types[r["type"]] == 1 else "a second " if seen_types[r["type"]] == 2 else "another ") + label(r)
        if r["id"] == first["id"] and math.hypot(*vec) < 1.2:
            lead = f"Around you, {name}, {size(r)}."
        elif r["id"] == first["id"]:
            lead = f"{steps_word(meters)} {_relative(heading, vec)}, the middle of {name}, {size(r)}."
        else:
            lead = f"{steps_word(meters)} {_relative(heading, vec)}, {name}, {size(r)}."
        if math.hypot(*vec) > 0.3:
            heading = (vec[0] / math.hypot(*vec), vec[1] / math.hypot(*vec))
        here = to
        # on arrival, turn to the most open view: the room's farthest corner (so the 3D camera doesn't stare at a wall);
        # left and right below are said from that same facing, so the words match the screen
        far = max(r["polygon"], key=lambda q: math.dist(q, here), default=None)
        if far and math.dist(far, here) > 1.0:
            turn = (far[0] - here[0], far[1] - here[1])
            heading = (turn[0] / math.hypot(*turn), turn[1] / math.hypot(*turn))
        bits = around(r, here, heading)
        text = lead + (" " + ". ".join(b[0].upper() + b[1:] for b in bits) + "." if bits else "")
        probs = [p for p in unit.get("all_problems") or unit.get("problems") or []
                 if p["profile"] in profiles and p["severity"] != "low" and p.get("floor", floor) == floor and belongs(p, r) and p["id"] not in told_probs]
        if probs:
            told_probs.update(p["id"] for p in probs[:2])
            text += " Watch out: " + " ".join(_say_in(p["text"]) for p in probs[:2])
        steps.append({"text": speakable(text), "x": round(here[0], 2), "y": round(here[1], 2),
                      "look": [round(here[0] + heading[0] * 2, 2), round(here[1] + heading[1] * 2, 2)], "floor": floor})
    rest = [p for p in unit.get("problems") or [] if p["profile"] in profiles and p["severity"] == "high" and p["id"] not in told_probs]
    end = "That's the whole floor." + (f" One more thing to know: {_say_in(rest[0]['text'])}" if rest else "")
    steps.append({"text": speakable(end), "x": None, "y": None, "look": None, "floor": floor})
    return {"title": (unit.get("caption") or "This home"), "steps": steps}


def steps_word(m: float) -> str:
    s = steps(m)
    return s[0].upper() + s[1:]
