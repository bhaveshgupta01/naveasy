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
    for r in sorted(main, key=lambda r: -r["area_m2"])[:6]:
        xs, ys = [p[0] for p in r["polygon"]], [p[1] for p in r["polygon"]]
        w, d = max(xs) - min(xs), max(ys) - min(ys)
        sizes.append(f"the {r['type'].lower()} is about {max(w, d) * 3.281:.0f} by {min(w, d) * 3.281:.0f} feet")
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
