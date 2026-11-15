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


WHY_LEAD = {"Faces": "it faces", "Leaves": "it leaves", "Has": "it has", "Its": "its", "Kept": "it's kept", "From": "from"}


def _why_clause(why: str) -> str:
    """A placement's reason, joined onto the sentence that says where the piece goes."""
    first, _, rest = why.partition(" ")
    lead = WHY_LEAD.get(first, first[:1].lower() + first[1:])
    return speakable(_say_in(f"{lead} {rest}".strip()))


def plan_narration(plan: dict, unit: dict, profiles: list[str] | None = None, casual: bool = False) -> dict:
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
    told_shape: dict[str, int] = {}          # a reason said once ("against the wall, clear of your route") isn't repeated per piece
    room_seen: dict[str, int] = {}
    for rid in sorted(by_room, key=room_order):
        room = rooms.get(rid)
        if not room:
            continue
        door = _door_into(unit, room)
        own = door is not None
        if not door:     # an open-plan room: describe it from the front door instead
            door = entry
        room_seen[room["type"]] = room_seen.get(room["type"], 0) + 1
        name = ("" if room_seen[room["type"]] == 1 else "second " if room_seen[room["type"]] == 2 else "other ") + room["type"].lower()
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
            if casual and p.get("needs") and casual_why(p):
                said_ = casual_why(p)
                if said_ in told_shape:
                    told_shape[said_] += 1
                else:
                    told_shape[said_] = 0
                    pieces[-1] += " " + said_
            elif p.get("why") and p.get("needs") and not casual:          # the need it serves, measured: said right after where it goes
                shape = re.sub(r"[\d.]+", "#", p["why"].lower())
                if shape in told_shape:
                    told_shape[shape] += 1
                else:
                    told_shape[shape] = 0
                    clause = _why_clause(p["why"])
                    pieces[-1] += " " + ("It's " if re.match(r"(against|back|clear|close|near|in view|within|beside|next)", clause) else
                                         "It " if re.match(r"(faces|leaves|keeps|stays|sits|has|gives|is)\b", clause) else "") + clause
                    pieces[-1] = pieces[-1][:-1] + "." if not pieces[-1].endswith(".") else pieces[-1]
                    pieces[-1] = pieces[-1].replace(". It's i", ". I").replace(". It ", ". It ")
                    m_ = re.search(r"\. (It's |It )?([a-z])", pieces[-1][-len(clause) - 8:])
                    if m_ and not m_.group(1):
                        idx = len(pieces[-1]) - len(clause) - 8 + m_.start(2)
                        pieces[-1] = pieces[-1][:idx] + pieces[-1][idx].upper() + pieces[-1][idx + 1:]
        lead = (f"In the {name}, coming in through its door. " if own else f"In the {name}, facing in from the front door. ") if door else f"In the {name}. "
        parts.append(lead + " ".join(pieces))
    for p in ([] if casual else plan.get("placements", [])):
        if "bed" in p["label"] and not p.get("why"):
            why = next((r for r in plan.get("reasons", []) if r.startswith("The bed")), None)
            m = re.search(r"with about (\d+) in clear on one side for transfers(?:, and (\d+) m from the bathroom)?", why or "")
            if m:
                parts.append(f"The bed leaves about {m.group(1)} inches clear on one side, for getting in and out"
                             + (f", and the bathroom is {m.group(2)} meters from it." if m.group(2) else "."))
            break
    repeated = sum(told_shape.values())
    if repeated:
        parts.append(f"The same holds for the other {repeated} piece{'s' if repeated != 1 else ''}.")
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
                room_ = {"bedroom": "bedroom", "living": "living room", "dining": "dining area", "work": "room for a desk"}.get(m.group(1), m.group(1))
                bits.append(f"The scan labels no {room_} in this home, so I couldn't place {names}.")
            else:
                bits.append(f"{names[0].upper() + names[1:]} won't fit: {_say_in(reason)}")
        out["wont_fit"] = " ".join(bits)
    if plan.get("score_after") is not None and casual:
        a = plan["score_after"]
        s = "With this layout, everything works for you." if a >= 85 else "This layout works, with a couple of tight spots." if a >= 65 else "It's still tight in places, even laid out this way."
        if plan.get("score_before") is not None and plan["score_before"] <= a - 10:
            s += " That's a lot better than just pushing it all against the walls."
        out["score"] = s
    elif plan.get("score_after") is not None:
        s = f"With this layout the home scores {plan['score_after']} for you"
        if plan.get("ordinary_min") is not None and plan.get("ordinary_max") is not None and plan["ordinary_max"] > plan["ordinary_min"]:
            s += f"; ordinary layouts of the same pieces score {plan['ordinary_min']} to {plan['ordinary_max']}"
        out["score"] = s + "."
    if casual and plan.get("wont_fit"):
        bits, blocked = [], []
        for w in plan["wont_fit"]:
            m = re.match(r"No (\w+) space found", w["reason"])
            name_ = PIECE.get(w["label"], w["label"])
            if m:
                room_ = {"bedroom": "a bedroom", "living": "a living room", "dining": "a dining area", "work": "a room for a desk"}.get(m.group(1), m.group(1))
                bits.append((room_, name_))
            else:
                blocked.append(name_)
        said_ = []
        for room_ in dict.fromkeys(r_ for r_, _ in bits):
            said_.append(f"The scan doesn't show {room_} here, so I couldn't place your {_join(list(dict.fromkeys(n_ for r_, n_ in bits if r_ == room_)))}.")
        if blocked:
            said_.append(f"Your {_join(list(dict.fromkeys(blocked)))} won't fit without blocking the way, so I left {'it' if len(set(blocked)) == 1 else 'them'} out.")
        out["wont_fit"] = " ".join(said_)
    probs = [p for p in plan.get("problems", []) if p.get("severity") == "high" and p.get("profile") in (profiles or [p.get("profile")])]
    if probs:
        out["problems"] = ("One thing to know: " + plain_problem(probs[0]) + ".") if casual else ("Still to know: " + _say_in(probs[0]["text"]))
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


def walk_tour(unit: dict, profile: str = "wheelchair", profiles: list[str] | None = None, casual: bool = False) -> dict | None:
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
        if casual:
            kinds: dict[str, list] = {}
            for f in unit.get("fixture_items") or []:
                if f.get("room") == r["id"] and f.get("kind") not in ("toilet tank", "shelf or ledge", "raised floor or platform"):
                    kinds.setdefault(f["kind"], []).append(f)
            for kind, fs in kinds.items():
                wheres = list(dict.fromkeys(_relative(facing, (f["x"] - at[0], f["y"] - at[1])) for f in fs))[:2]
                many = len(fs) > 1
                what = {"counter": "the counters" if many else "the counter", "vanity": "the vanities" if many else "the vanity", "island": "the island",
                        "toilet": "the toilet", "tub": "the tub", "desk": "the desks" if many else "a desk"}.get(kind, f"the {kind}")
                verdict = ""
                hs = [f.get("height_in") for f in fs if f.get("height_in")]
                if "wheelchair" in profiles and hs and kind in ("counter", "island", "vanity", "desk"):
                    verdict = (", a good height for you" if max(hs) <= 34.5 else ", a bit high for you sitting down" if min(hs) > 34.5
                               else ", one of them at a good height for you")
                bits.append(f"{' and '.join(wheres)}, {what}{verdict}")
            wins = [w for w in unit.get("windows", []) if _near_room(w, r)]
            if wins:
                bits.append(f"{_relative(facing, (wins[0]['x'] - at[0], wins[0]['y'] - at[1]))}, {'a window' if len(wins) == 1 else 'windows'}")
            return bits[:4]
        for f in unit.get("fixture_items") or []:
            if f.get("room") != r["id"] or f.get("kind") in ("toilet tank", "shelf or ledge", "raised floor or platform"):
                continue
            where = _relative(facing, (f["x"] - at[0], f["y"] - at[1]))
            what = FIXTURE_WORD.get(f["kind"], f"a {f['kind']}")
            hi = f" about {f['height_in']:.0f} inches high" if f.get("height_in") and f["kind"] in ("counter", "island", "vanity", "desk", "table") else ""
            if casual:      # a height only matters to someone seated: say whether it suits them
                hi = ((" at a good height for you" if f["height_in"] <= 34.5 else ", a bit high for you sitting down")
                      if "wheelchair" in profiles and f.get("height_in") and f["kind"] in ("counter", "island", "vanity", "desk") else "")
            bits.append(f"{where}, {what}{hi}")
        wins = [w for w in unit.get("windows", []) if _near_room(w, r)]
        if wins:
            sides = list(dict.fromkeys(_relative(facing, (w["x"] - at[0], w["y"] - at[1])) for w in wins))
            bits.append(f"{_join(sides)}, {'a window' if len(wins) == 1 else 'windows'}")
        return bits[:4]

    steps = []
    door_txt = f"the front door, about {entry['est_clear_in']:.0f} inches wide" if entry.get("est_clear_in") else "the front door"
    if casual:
        verdict = {"pass": " It's wide enough for your wheelchair.", "tight": " It's a tight squeeze for a wheelchair.",
                   "fail": " It's too narrow for a wheelchair."}.get(entry.get("status"), "") if "wheelchair" in profiles else ""
        door_txt = "the front door"
    steps.append({"text": f"We start at {door_txt}, facing in." + (verdict if casual else "") + f" You're in the {label(first)}.",
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
        if casual:
            if r["id"] == first["id"] and math.hypot(*vec) < 1.2:
                lead = size_word(r).strip()
            else:
                lead = f"{steps_word(meters)} {_relative(heading, vec)}, {'the middle of ' if r['id'] == first['id'] else ''}{name}." + size_word(r)
            if "wheelchair" in profiles and r["type"] in ("Bedroom", "Bathroom", "Kitchen") and any(t.get("room") == r["id"] for t in unit.get("turning_circles", [])):
                lead += " There's room to turn your wheelchair around here."
        elif r["id"] == first["id"] and math.hypot(*vec) < 1.2:
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
        text = (lead + (" " + ". ".join(b[0].upper() + b[1:] for b in bits) + "." if bits else "")).strip()
        probs = [p for p in unit.get("all_problems") or unit.get("problems") or []
                 if p["profile"] in profiles and p["severity"] != "low" and p.get("floor", floor) == floor and belongs(p, r) and p["id"] not in told_probs
                 and not (casual and p.get("kind") == "counter_height")]      # casual: the built-in's own verdict already said it
        if probs:
            told_probs.update(p["id"] for p in probs[:2])
            text += (" Heads up: " + " Also, ".join(plain_problem(p) + "." for p in probs[:2])) if casual else (" Watch out: " + " ".join(_say_in(p["text"]) for p in probs[:2]))
        steps.append({"text": speakable(text), "x": round(here[0], 2), "y": round(here[1], 2),
                      "look": [round(here[0] + heading[0] * 2, 2), round(here[1] + heading[1] * 2, 2)], "floor": floor})
    rest = [p for p in unit.get("problems") or [] if p["profile"] in profiles and p["severity"] == "high" and p["id"] not in told_probs]
    end = "That's the whole floor." + (f" One more thing to know: {_say_in(rest[0]['text'])}" if rest else "")
    if casual:
        end = ("That's the whole place." + (f" One more thing: {plain_problem(rest[0])}." if rest else "")
               + " It's all from the 3D scan, so it's worth seeing in person. If you want the exact measurements, just ask.")
    steps.append({"text": speakable(end), "x": None, "y": None, "look": None, "floor": floor})
    return {"title": (unit.get("caption") or "This home"), "steps": steps}


def steps_word(m: float) -> str:
    s = steps(m)
    return s[0].upper() + s[1:]


def venue_walk(route: dict, floor_z: dict, casual: bool = False) -> dict | None:
    """The venue's entrance-to-room route as a walk the 3D tour can follow (one stop per spoken phrase, across floors).
    `route` is pipeline.venue.inside_route(); `floor_z` maps the route's floor index to that floor's height."""
    import re
    legs = [leg for leg in route.get("legs") or [] if len(leg.get("points") or []) >= 2]
    if not legs:
        return None
    out = []
    first = legs[0]["points"]
    look0 = first[1]
    out.append({"text": f"We start at the entrance on {legs[0]['floor_label'].lower()}, facing in.", "x": first[0][0], "y": first[0][1],
                "look": look0, "z": floor_z.get(legs[0]["floor"]), "floor": legs[0]["floor"]})
    for leg in legs:
        pts, z = leg["points"], floor_z.get(leg["floor"])
        phrases = [p.strip() for p in (leg.get("words") or "").split(", then ") if p.strip()] or [f"go about {leg['meters']:.0f} meters"]
        # each phrase's stop: as far along the path as the phrases so far say ("go about 5 meters"), scaled to the path's length
        seg = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        total = sum(seg) or 1.0
        said = [float(m.group(1)) if (m := __import__("re").search(r"about (\d+(?:\.\d+)?) meter", ph)) else None for ph in phrases]
        known = sum(x for x in said if x) or 0
        each = [(x if x else (total - known) / max(1, said.count(None))) for x in said]
        scale = total / (sum(each) or 1.0)

        def along(dist):
            for (a, b), L in zip(zip(pts, pts[1:]), seg):
                if dist <= L or L == seg[-1] and (a, b) == (pts[-2], pts[-1]):
                    f = min(1.0, dist / L) if L else 1.0
                    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), (b[0] - a[0], b[1] - a[1])
                dist -= L
            return tuple(pts[-1]), (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        run = 0.0
        for i, ph in enumerate(phrases):
            run += each[i] * scale
            (tx, ty), (vx, vy) = along(run)
            px, py = tx - vx, ty - vy
            d = math.hypot(vx, vy) or 1.0
            lead = (f"On {leg['floor_label'].lower()}" + (f", from {leg['from']}" if leg.get("from") else "") + ": ") if i == 0 and leg is not legs[0] else ""
            text = lead + ph[0].upper() + ph[1:] if not lead else lead + ph
            if i == len(phrases) - 1 and leg.get("to"):
                text += f", to {leg['to']}"
            out.append({"text": speakable(text + "."), "x": round(tx, 2), "y": round(ty, 2), "look": [round(tx + (tx - px) / d * 2, 2), round(ty + (ty - py) / d * 2, 2)],
                        "z": z, "floor": leg["floor"]})
        if leg.get("narrowest_in") and casual:
            n_ = leg["narrowest_in"]
            out[-1]["text"] += (" It's wide enough for a wheelchair the whole way." if n_ >= 36 else " It gets a bit narrow in one spot, but a wheelchair fits."
                                if n_ >= 32 else " One spot on the way is too narrow for a wheelchair.")
        elif leg.get("narrowest_in"):
            ev = (leg.get("narrowest_evidence") or {}).get("says", "")
            out[-1]["text"] += f" The narrowest point on the way is about {leg['narrowest_in']} inches" + (f", {ev}." if ev else ".")
    end = " ".join(route.get("needs_site_visit") or []) or "That's the way."
    bf = route.get("between_floors") or {}
    if casual and bf.get("stairs_found") and not bf.get("elevator_in_scan"):
        end = f"The {re.sub(r'^the ', '', route.get('to', 'room'))} is upstairs, and I only see stairs in the scan, no elevator, so ask someone about a step-free way up."
    out.append({"text": speakable(end), "x": None, "y": None, "look": None, "z": None, "floor": None})
    return {"title": f"From the {route.get('from', 'entrance')} to the {route.get('to', 'room')}", "steps": out}


# ---------------------------------------------------------------- casual: what a friend would say
# The same measurements decide every verdict ("your wheelchair gets through every door" means every main doorway is at
# least 32 inches clear); only the numbers are left out. Ask for the details and the measured version comes back.

def minutes_word(m: float | None) -> str | None:
    if m is None:
        return None
    return ("right around the corner" if m < 2 else "a few minutes away" if m < 6.5 else "about ten minutes away" if m < 13
            else "about fifteen or twenty minutes away" if m < 22 else "a bit of a trek")


def match_word(score: int) -> str:
    return ("a strong match for you" if score >= 80 else "a good match" if score >= 65 else "an okay match, with some catches" if score >= 50
            else "a tough fit for you")


def size_word(r: dict) -> str:
    a, t = r.get("area_m2") or 0, r.get("type")
    small, roomy = {"Bathroom": (3.5, 6.5), "Bedroom": (9, 15), "Living Room": (12, 24), "Kitchen": (7, 16)}.get(t, (None, None))
    if small is None:
        return ""
    return " It's on the small side." if a < small else " It's nice and roomy." if a > roomy else ""


def plain_problem(p: dict) -> str:
    """A measured problem the way a friend would put it: the verdict, not the inches."""
    import re
    t, k = p.get("text", ""), p.get("kind")
    head = t.split(":")[0].strip()
    thing = (head[:1].lower() + head[1:]) if head else "a door"
    if k == "narrow_door":
        return f"the {thing} is too narrow for a wheelchair" if p.get("severity") == "high" else f"the {thing} is a tight squeeze for a wheelchair"
    if k == "threshold":
        return f"there's a little step at the {thing}" if "step" in t else f"there's a raised threshold at the {thing}"
    if k == "unreachable_room":
        m = re.search(r"reach this ([\w ]+?):", t)
        why = "it's on another floor" if "another floor" in t else "a door on the way is too narrow"
        return f"a wheelchair can't get into the {m.group(1) if m else 'room'}, because {why}"
    if k == "no_turning_space":
        m = re.search(r"in this ([\w ]+?)(?: with|\s*\(|\.)", t)
        return f"there isn't room to turn a wheelchair around in the {m.group(1) if m else 'room'}"
    if k == "stairs":
        return "there are stairs inside" if "Stairs here" not in t else "there are stairs here"
    if k == "long_walk":
        return "there's no bathroom on the bedroom's floor" if "No bathroom" in t else "the bathroom is a long walk from the bed"
    if k == "complex_path":
        m = re.search(r"to the (\w+) has", t)
        return f"the way to the {m.group(1) if m else 'rooms'} has a lot of turns to learn"
    if k == "path_obstacle":
        m = re.search(r"The (.+?) sits", t)
        return f"the {m.group(1) if m else 'furniture'} sits close to your walking path"
    if k == "sightline":
        return "you can't see the front door from the main seat, so a flashing doorbell would help"
    if k == "counter_height":
        return f"the {thing} is a bit high to use sitting down"
    first = re.split(r"(?<=[.;])\s", _say_in(t))[0].rstrip(".;")
    return first[:1].lower() + first[1:]


def casual_why(p: dict) -> str:
    needs, label = p.get("needs") or [], p.get("label", "")
    if p.get("caution"):
        return "It's a bit tight there."
    if "wheelchair" in needs and "bed" in label:
        return "There's room beside it to get in and out of your chair."
    if "older" in needs and "bed" in label:
        return "It's close to the bathroom."
    if "deaf" in needs:
        return "You can see the front door from there."
    if "blind" in needs:
        return "It's out of your walking path."
    if "wheelchair" in needs:
        return "It leaves you room to get around it."
    if "older" in needs:
        return "It keeps things close and easy to reach."
    return ""


def casual_list(homes: list[dict], n_found: int) -> str:
    words = ["One", "Two", "Three"]
    lines = []
    for i, h in enumerate(homes[:3]):
        line = f"{words[i]}: {h['name']}" + (f", from about {h['rent']:,} dollars a month" if h.get("rent") else "")
        line += f", {match_word(h['score'])}" + ("" if h.get("inside") else ", though nobody's measured the inside yet") + "."
        lines.append(line)
    k = len(lines)
    ask = "Which one do you want to hear about: " + ("one, two or three?" if k == 3 else "one or two?" if k == 2 else "this one?")
    return f"I found {n_found:,} places. Here are my top {k}. " + " ".join(lines) + " " + ask


def casual_pitch(b: dict, unit: dict | None, profiles: list[str], general: bool = False) -> str:
    """The broker's summary, the way a friend who knows the place would say it."""
    import re
    street = re.sub(r"^\d+\s+", "", (b.get("address") or "").split(",")[0]).strip()
    out = [f"{b['name']}" + (f", on {street}." if street and street.lower() not in b["name"].lower() else ".")]
    if b.get("rent_min"):
        out.append(f"Rent starts around {b['rent_min']:,} dollars a month.")
    score = b["combined"]["score"]
    out.append("Honestly, it's one of the better picks for you." if score >= 75 else "It works, with a couple of catches." if score >= 50
               else "To be honest, it's going to be hard for you.")
    if b.get("elevator"):
        out.append("There are elevators, so no stairs to get up to the apartment.")
    elif b.get("walkup") and (b.get("stories") or 1) > 1:
        out.append("Heads up, it's a walk-up, so expect stairs.")
    if b.get("year_built") and b["year_built"] < 1991:
        out.append("It's an older building, from before the 1991 accessibility rules.")
    probs = [p for p in ((unit or {}).get("all_problems") or (unit or {}).get("problems") or []) if p["profile"] in profiles and p["severity"] != "low"]
    if unit and unit.get("rooms"):
        cap = caption_words((unit.get("caption") or "").split(" – ")[0].strip())
        floors = (unit.get("stats") or {}).get("floors", 1)
        out.append(f"Inside, it's {'a ' + cap if cap and not cap.lower().startswith(('a ', 'the ')) else (cap or 'one unit')}"
                   + (", all on one level." if floors <= 1 else f", over {floors} floors."))
        if not general:
            doors = [d for d in unit.get("doors", []) if d.get("is_door") and not d.get("minor") and not (d.get("exterior") and not d.get("entry"))]
            if "wheelchair" in profiles and doors:
                fail = [d for d in doors if d["status"] == "fail"]
                tight = [d for d in doors if d["status"] == "tight"]
                turns = {t["room"] for t in unit.get("turning_circles", [])}
                need = [r for r in unit["rooms"] if r["type"] in ("Bedroom", "Bathroom", "Kitchen")]
                missing = sorted({r["type"].lower() for r in need if r["id"] not in turns})
                door_line = ("A door or two is too narrow for your chair." if fail else "One or two doors are a tight squeeze." if tight
                             else "Your wheelchair gets through every door")
                out.append(door_line + (f", but there's no room to turn around in the {_join(missing)}." if missing and not fail and not tight else
                                        f" There's no room to turn around in the {_join(missing)}." if missing else
                                        (", and there's room to turn around in every room." if not fail and not tight else " There's room to turn around in every room.")))
            if "blind" in profiles:
                bl = [p for p in probs if p["profile"] == "blind"]
                out.append(("The layout's simple, with easy ways from the door to the bedroom, bathroom and kitchen." if not bl
                            else plain_problem(bl[0])[:1].upper() + plain_problem(bl[0])[1:] + "."))
            if "deaf" in profiles:
                v = ((unit.get("checks") or {}).get("entry_visible_from_seat") or {}).get("value")
                if v is not None:
                    out.append("You can see the front door from the living room." if v >= 1 else "You can't see the front door from the main seating spot.")
            if "older" in profiles:
                m = (unit.get("stats") or {}).get("bed_to_bathroom_m")
                if m is not None:
                    out.append("The bathroom's just a few steps from the bed." if m <= 6 else "The bathroom's a short walk from the bed." if m <= 12
                               else "The bathroom's a long walk from the bed.")
        else:
            out.append("The doors and hallways are a good width.")
    elif not general:
        out.append("Nobody's measured the inside yet, so I only know the building.")
    near = {n["category"]: n["minutes"].get(profiles[0]) for n in b.get("nearest", []) if n["minutes"].get(profiles[0]) is not None}
    bits = [f"{label} {minutes_word(near[c])}" for c, label in (("transit", "a bus stop"), ("grocery", "a grocery store"), ("pharmacy", "a pharmacy")) if c in near]
    if bits:
        out.append("You've got " + _join(bits) + ".")
    high = [p for p in probs if p["severity"] == "high"]
    if high:
        out.append("The main catch: " + plain_problem(high[0]) + ".")
    elif unit and not probs and not b.get("walkup"):
        out.append("I didn't spot a real catch in what the scan can see.")
    out.append("Before you sign, check the way in from the street and the parking in person.")
    return " ".join(out)


def casual_route(r: dict, profiles: list[str] | None = None) -> str:
    dest = r["destination"].get("name") or (r.get("category") or "there")
    how = "roll" if r.get("profile") == "wheelchair" else "walk"
    out = [f"It's about a {r['minutes']:.0f}-minute {how} to {dest}."]
    if r.get("elevation"):
        g = r["elevation"]["max_grade_pct"]
        out.append("It's pretty flat." if g < 2 else "There are a few gentle slopes." if g <= 5 else "There's a steep bit or two on the way.")
    if r.get("crossings"):
        out.append(f"You cross the street {r['crossings']} time{'s' if r['crossings'] != 1 else ''}.")
    nosig = sum(1 for h in r.get("hard_spots", []) if "without signals" in h.get("text", ""))
    if nosig:
        out.append(f"{'A couple' if nosig > 1 else 'One'} of those crossings {'have' if nosig > 1 else 'has'} no signal, so take care there.")
    if sum(s["meters"] for s in r["stretches"] if s["status"] == "grey") > 100:
        out.append("Part of the way has no sidewalk on record, so I can't promise there is one.")
    steps_ = [s for s in r.get("steps", []) if s.get("text")][:6]
    if steps_ and "blind" in (profiles or [r.get("profile")]):
        out.append("Here's the way: " + " ".join(s["text"] if s["text"].endswith(".") else s["text"] + "." for s in steps_))
    elif steps_:
        out.append("Want turn-by-turn directions? Just say so.")
    return " ".join(out)


def casual_venue_doors(d: dict, near: str | None = None) -> str:
    doors = d.get("doors") or []
    if not doors:
        return "The scan doesn't show doors there that I can judge."
    tape = "" if any((x.get("evidence") or {}).get("checked") for x in doors) else " That's from the 3D scan; we haven't checked it with a tape yet."
    if near:
        mine = [x for x in doors if any(near.lower() in r.lower() for r in x.get("rooms", []))]
        if mine:
            bad = [x for x in mine if x["status"] != "pass"]
            return ((f"The {near.lower()} door{'s are' if len(mine) > 1 else ' is'} wide enough for a wheelchair." if not bad else
                     f"One of the {near.lower()} doors is too narrow for a wheelchair.") + tape)
    bad = [x for x in doors if x["status"] != "pass"]
    return (("Every door here is wide enough for a wheelchair." if not bad else
             f"Almost every door here is wide enough for a wheelchair. The exception is a door on {bad[0]['floor_label'].lower()}, which looks too narrow.")
            + tape)


def casual_venue_stairs(s: dict, way: dict | None = None) -> str:
    out = []
    if s.get("stairs"):
        main = max(s["stairs"], key=lambda x: x["area_m2"])
        out.append(f"The main stairs are on {main['floor'].lower()}.")
    if way and way.get("spoken"):
        first = way["spoken"].split(" The narrowest")[0]
        out.append("From the entrance: " + first[0].lower() + first[1:])
    if "not in the scan" in (s.get("elevator") or ""):
        out.append("I don't see an elevator in the scan, so ask someone about a step-free way up.")
    if s.get("raised_parts"):
        out.append(f"Part of {s['raised_parts'][0]['floor'].lower()} is raised a few inches; it might be a step or a ramp, so watch for it.")
    return " ".join(out)


def casual_venue_outside(o: dict) -> str:
    how = "roll" if o.get("profile") == "wheelchair" else "walk"
    out = [f"From {o['from']} it's about a {o['minutes']:.0f}-minute {how}"]
    steep = sum(1 for h in o.get("hard_spots") or [] if "too steep" in h or "Slope" in h)
    out[0] += (", with a few steep bits on the way" if steep > 1 else ", with one steep bit" if steep else "") + "."
    if o.get("crossings"):
        out.append(f"You cross the street {o['crossings']} time{'s' if o['crossings'] != 1 else ''}, and the curbs aren't on record, so take it slow there.")
    out.append("Which door is step-free is something to ask when you arrive.")
    return " ".join(out)
