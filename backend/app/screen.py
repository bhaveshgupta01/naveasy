"""Answer questions about what is on screen right now.

The web app sends a small snapshot of what the person is looking at (the selected home, the open unit, the
route drawn, the relocation results, the list, the map legend). We turn everything shown there into plain
sentences ("facts"), find the ones that match the question, and say them. Retrieval only: every answer is a
sentence built from the data on screen, so it can't state something that isn't there.

When a local open-source model is running (brain.py), dialogue.py lets it word the answer from the retrieved
facts; it is never a source of facts.
"""
from __future__ import annotations

import math
import re

from . import narrate

STOP = set("a an the is are was were be been do does did of in on at to for from with and or but it its this that these those there here what which who whom "
           "how when where can could would should will i me my we our you your they them their he she his her as by about into than then so if any some "
           "tell say show give please know want need have has had get got am not no yes also just like much many more most".split())
SYN = {
    "rent": "rent price cost dollars month expensive cheap afford", "price": "rent price cost dollars", "cost": "rent price cost dollars",
    "old": "built year age old new", "built": "built year age", "age": "built year age", "year": "built year",
    "elevator": "elevator elevators lift walkup stairs", "lift": "elevator elevators", "stairs": "stairs walkup elevator steps floors", "floors": "floors stories story stairs",
    "stories": "stories floors story", "tall": "stories floors",
    "score": "score scores points rating", "rating": "score scores", "good": "score meets barriers problems", "bad": "score barriers problems",
    "safe": "score barriers problems crossing", "why": "score because building inside components",
    "door": "door doors doorway doorways inches wide narrow", "doorway": "door doors doorway doorways inches", "wide": "wide width inches door doorway",
    "narrow": "narrow narrowest inches door doorway", "fit": "inches door doorway wheelchair reach",
    "bathroom": "bathroom toilet", "bedroom": "bedroom bed", "kitchen": "kitchen", "room": "room rooms bedroom bathroom kitchen living",
    "big": "square feet size area large", "size": "square feet size area", "large": "square feet size area", "small": "square feet size area",
    "sunny": "sun light window facing morning afternoon", "dark": "light sun window", "view": "window windows facing",
    "window": "window windows light sun facing", "sun": "sun light window facing morning afternoon", "light": "light sun window", "bright": "light sun window",
    "pharmacy": "pharmacy", "hospital": "hospital", "clinic": "clinic doctor", "doctor": "clinic", "grocery": "grocery supermarket", "supermarket": "grocery",
    "bus": "bus rail stop transit", "transit": "bus rail stop transit", "train": "rail bus stop", "school": "school", "park": "park", "restaurant": "restaurants", "food": "restaurants grocery",
    "far": "minutes meters distance away", "close": "minutes meters distance near", "near": "minutes near nearby", "distance": "minutes meters", "long": "minutes meters time",
    "time": "minutes", "walk": "minutes meters route sidewalk",
    "sidewalk": "sidewalk sidewalks", "crossing": "crossing crossings signals curb", "curb": "curb curbs lowered crossing", "signal": "signals crossing",
    "slope": "slope steep percent flat hill", "steep": "slope steep percent", "hill": "slope steep percent flat", "flat": "slope flat percent",
    "neighborhood": "neighborhood streets sidewalks", "area": "neighborhood square feet", "street": "streets sidewalks crossing",
    "unit": "unit units scanned measured", "apartment": "unit units home", "many": "count number units homes doorways",
    "address": "address located", "located": "address", "confidence": "confidence", "sure": "confidence estimate", "accurate": "confidence estimate scan",
    "color": "color green amber red grey", "green": "green", "red": "red", "amber": "amber", "grey": "grey unknown", "gray": "grey unknown", "yellow": "amber",
    "pillar": "pillar taller", "data": "data source scan matterport vexcel costar", "source": "data source", "measure": "measured scan", "measured": "measured scan",
    "problem": "problem problems watch barrier", "issue": "problem problems", "wrong": "problem problems", "barrier": "problem problems barriers",
    "turn": "turning circle turns", "wheelchair": "wheelchair", "blind": "blind vision", "deaf": "deaf hearing", "older": "older adult stamina",
    "expensive": "rent price cost dollars", "cheap": "rent price cost dollars", "afford": "rent price cost dollars", "pay": "rent price cost dollars",
    "confident": "confidence estimate", "certain": "confidence estimate", "trust": "confidence estimate", "reliable": "confidence estimate",
    "around": "turning circle", "spin": "turning circle", "reach": "reach reachable", "enter": "reach reachable door entry", "because": "why because",
    "check": "verify check checklist", "verify": "verify check checklist", "visit": "verify check", "furniture": "furniture pieces layout sofa bed",
    "match": "match neighborhood", "moving": "match neighborhood now there", "available": "available units rent", "best": "best top highest score",
    "worst": "narrowest lowest problem", "home": "home building", "building": "building home", "listed": "listed homes showing", "showing": "listed homes showing",
}
GLOSSARY = [
    ("score scores points rating calculated mean", "Every score runs from 0 to 100. 75 or more means it meets most of our checks, 50 to 74 means some barriers, under 50 means major barriers. A home's score is half building evidence and half the best unit measured inside, when one is measured."),
    ("confidence sure accurate estimate", "Confidence says how much evidence is behind a score. High means it was measured from a 3D scan with the entry door found. Low means building records or sparse map data only. These are estimates, not a certification."),
    ("color green amber red dots dot pillar taller map homes home marker", "On the map, green homes score 75 or more, amber 50 to 74, red under 50. Solid dots are measured inside from a scan; hollow ones are building evidence only. In 3D, each home is a pillar and taller means a higher score."),
    ("color heat neighborhood shade lighter darker faint", "The colored wash on the map is the neighborhood score: stronger color means everyday places are quicker to reach for the selected need. Faint or missing means no data, not inaccessible."),
    ("floor plan green amber red hatch color mean", "On a floor plan for a wheelchair, green floor is reachable with 36 inches clear, amber is reachable with 32 to 36 inches or behind a tight door, and red hatched floor can't be reached. Door labels show the estimated clear width."),
    ("route line color green amber red grey dashed", "On a route, green stretches have a sidewalk or walkway, amber needs caution, red is a barrier, and grey dashed means there is no sidewalk record, so it is unknown."),
    ("data source where come from matterport vexcel costar openstreetmap", "Building facts come from CoStar. Room, door and window measurements come from Matterport 3D scans. Aerial photos and ground heights come from Vexcel. Places, sidewalks and crossings come from OpenStreetMap, and bus stops from the transit agencies. Routes come from Valhalla."),
    ("door width measured inches how estimate", "Door widths are the scanned opening minus 2 inches, because scans measure the frame, not the clear space with the door open. A wheelchair needs 32 inches clear."),
    ("not checked fixtures toilet tub counter grab bars", "We don't measure fixtures such as the toilet, tub and counters, door hardware, counter heights, grab bars or alarms. Check those on a visit."),
]


def _tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _expand(tokens: list[str]) -> dict[str, float]:
    bag: dict[str, float] = {}
    for t in tokens:
        bag[t] = max(bag.get(t, 0), 1.0)
        for s in _tokens(SYN.get(t, "")):
            bag[s] = max(bag.get(s, 0), 0.6)
    return bag


def facts_for(screen: dict, profiles: list[str]) -> list[dict]:
    """Every sentence that describes what is on screen. Each fact: {text, about, extra keywords}."""
    from . import main as api
    ps = profiles or ["wheelchair"]
    out: list[dict] = []

    def add(about: str, text: str, kw: str = "", weight: float = 1.0):
        if text:
            out.append({"about": about, "text": text.strip(), "kw": kw, "w": weight})

    for kw, text in GLOSSARY:
        add("how to read NavEasy", text, kw, 0.85)

    lst = screen.get("list") or {}
    if lst.get("count") is not None:
        top = lst.get("top") or []
        add("the list", f"{lst['count']:,} homes are listed for your needs" + (f". The top ones are {narrate._join([f'{h['name']} with {h['score']}' for h in top[:3]])}." if top else "."),
            "listed showing homes count many best top highest")
    if screen.get("hex"):
        h = screen["hex"]
        add("the neighborhood you tapped", f"The neighborhood cell you tapped scores {h.get('score')}. {'Routed walking times' if h.get('live') else 'Times estimated from distance'}: {', '.join(h.get('text') or [])}.",
            "neighborhood cell minutes pharmacy hospital clinic grocery bus school park")

    bid = screen.get("selected_building")
    if bid:
        try:
            b = api.building(bid, ",".join(ps))
        except Exception:  # noqa: BLE001
            b = None
        if b:
            name = b["name"]
            add(name, f"{name} is at {re.sub(r'(\\d{5})\\d{4}\\b', r'\\1', b['address'])}.", "address located where", 1.1)
            add(name, f"{name} scores {b['combined']['score']} out of 100 for your needs, with {b['combined']['confidence']} confidence.", "score rating good", 1.2)
            p0 = ps[0]
            why = f"{name} scores {b['scores'][p0]['score']} for {narrate.NEED[p0]} because the building itself scores {b['building_scores'][p0]['score']}: {b['components']['vertical_access']['why']}; {b['components']['age']['why']}; {b['components']['approach']['why']}."
            if b.get("inside"):
                why += f" The best unit measured inside scores {b['inside'][p0]['best']}, and the two are averaged."
            add(name, why, "why because reason explain score how come", 1.25)
            for p in ("wheelchair", "blind", "deaf", "older"):
                add(name, f"For {narrate.NEED[p]}, {name} scores {b['scores'][p]['score']}: {b['building_scores'][p]['score']} for the building"
                          + (f" and {b['inside'][p]['best']} for the best unit inside." if b.get("inside") else ", with no unit measured inside."), f"score {p}")
            kind = "a home" if b["kind"] == "home" else "an apartment community"
            add(name, f"It is {kind}" + (f" with {b['stories']} stories" if b.get("stories") else "") + (f", built in {b['year_built']}" if b.get("year_built") else "") + ".", "built year old age stories floors kind")
            if b.get("rent_min"):
                add(name, f"Rent runs from {b['rent_min']:,} to {b['rent_max']:,} dollars a month" + (f", with {b['units_available']} units available." if b.get("units_available") else "."), "rent price cost available")
            elif b.get("price"):
                add(name, f"It is listed for sale at {b['price']:,} dollars.", "price cost sale")
            if b.get("total_units"):
                add(name, f"The community has {b['total_units']} units in total.", "units total many count")
            labels = {"vertical_access": "Getting upstairs", "approach": "The approach from the street", "listing_evidence": "Listing evidence", "age": "Building age"}
            for k, c in b["components"].items():
                add(name, f"{labels.get(k, k)}: {c['why']}.", {"vertical_access": "elevator elevators stairs walkup lift floors", "approach": "entrance parking steps ramp approach",
                                                               "listing_evidence": "listing accessibility mention", "age": "built year old age fair housing"}.get(k, ""))
            units = [u for u in b["units"] if u.get("dwelling")]
            measured = [u for u in units if u.get("scores")]
            add(name, f"{len(units)} unit{'s' if len(units) != 1 else ''} were scanned here and {len(measured)} {'have' if len(measured) != 1 else 'has'} a measured floor plan.", "units scanned measured many count")
            for u in measured[:6]:
                sc = min(u["scores"][p]["score"] for p in ps)
                st = u.get("stats") or {}
                add(name, f"The unit {u['caption'] or 'scan'} scores {sc}" + (f"; its narrowest main doorway is about {st['narrowest_main_door_in']:.0f} inches" if st.get("narrowest_main_door_in") else "")
                    + (f". {u['problems'][0]['text']}" if u.get("problems") else "."), "unit doorway narrow problem inside " + (u["caption"] or ""))
            for n in b.get("nearest", []):
                m = n["minutes"].get(ps[0])
                if m is not None:
                    add(name, f"The nearest {n['label'].lower()} is {'about ' if n['estimated'] else ''}{m:.0f} minutes away for {narrate.NEED[ps[0]]}.", f"{n['category']} minutes far close near distance")
            loc = b.get("locality_detail")
            if loc:
                sc = min(v["score"] for v in loc["scores"].values())
                add(name, f"The neighborhood around it scores {sc} for your needs, from {'routed' if loc.get('live') else 'estimated'} walking times and street data.", "neighborhood score area")
                s = loc.get("streets") or {}
                if s:
                    add(name, f"Around it, sidewalks are mapped along about {s['sidewalk_coverage'] * 100:.0f} percent of streets.", "sidewalk sidewalks streets")
                    if s.get("signalized_share") is not None:
                        add(name, f"About {s['signalized_share'] * 100:.0f} percent of nearby crossings have signals, out of {s.get('crossings', 0)} mapped crossings.", "crossing crossings signals")
                    if s.get("lowered_kerb_share") is not None:
                        add(name, f"Of {s.get('kerbs_tagged', 0)} curbs recorded nearby, {s['lowered_kerb_share'] * 100:.0f} percent are lowered or flush.", "curb curbs lowered ramp")
                    add(name, f"There are {s.get('benches_per_km', 0)} benches per kilometer of street nearby.", "bench benches rest sit")
            add(name, "Before you sign: " + " ".join(b.get("checklist", [])[:3]), "verify check visit sign checklist", 0.9)

    r = screen.get("route")
    if r and r.get("building_id"):
        try:
            rt = api.routes(r["building_id"], r.get("category") or "pharmacy", r.get("profile") or ps[0])
        except Exception:  # noqa: BLE001
            rt = None
        if rt:
            dest = rt["destination"].get("name") or rt.get("category")
            add("the route", f"The route to {dest} takes {rt['minutes']:.0f} minutes and is {rt['meters']} meters, for {narrate.NEED[rt['profile']]}, with {rt['confidence']} confidence.", "route minutes meters far long time distance " + (rt.get("category") or ""), 1.2)
            if rt.get("crossings") is not None:
                add("the route", f"The route has {rt['crossings']} street crossings; curbs are recorded at {rt['crossings_with_curb_data']} of them.", "crossing crossings curb curbs many count how", 1.3)
            if rt.get("elevation"):
                add("the route", f"Its steepest sustained slope is {rt['elevation']['max_grade_pct']} percent and it climbs {rt['elevation']['climb_m']} meters in total. The walkway limit is 5 percent.", "slope steep hill flat percent climb")
            grey = sum(s["meters"] for s in rt["stretches"] if s["status"] == "grey")
            if grey:
                add("the route", f"For {grey} of its {rt['meters']} meters the map has no sidewalk record, so that part is unknown.", "sidewalk unknown grey dashed")
            for i, h in enumerate(rt["hard_spots"][:6]):
                add("the route", f"Hard spot {i + 1}: {h['text']}", "hard spot problem crossing barrier watch")
            add("the route", "Turn by turn: " + " ".join(s["text"] for s in rt.get("steps", [])[:8] if s.get("text")), "turn directions way steps left right", 0.9)

    u = screen.get("unit")
    if u and u.get("model_id"):
        prof = u.get("profile") or ps[0]
        try:
            unit = api.unit_heatmap(u["model_id"], prof)
        except Exception:  # noqa: BLE001
            unit = None
        if unit and unit.get("has_graph", True):
            n = narrate.unit_narration(unit, prof, ps if prof in ps else [prof])
            title = n["title"]
            kws = {"overview": "size square feet rooms score big", "layout": "layout where entry door rooms left right ahead", "paths": "path way reach get steps bedroom bathroom kitchen",
                   "rooms": "room size feet big bedroom bathroom kitchen living", "light": "window windows sun light facing bright", "doors": "door doors doorway wide narrow inches wheelchair",
                   "problems": "problem problems issue barrier watch wrong", "caveat": "fixtures toilet tub counter check visit", "matterport": "description summary"}
            for k, text in n["sections"].items():
                add(title, text, kws.get(k, ""), 1.15 if k in ("overview", "problems") else 1.0)
            for p in ("wheelchair", "blind", "deaf", "older"):
                add(title, f"For {narrate.NEED[p]}, this unit scores {unit['scores'][p]['score']} with {unit['scores'][p]['confidence']} confidence.", f"score {p}")
            names = {"entry_door": "the entry door width", "doors": "inside doorways", "rooms_reachable": "main rooms reachable", "turning_space": "60 inch turning space",
                     "thresholds": "thresholds", "floor_reachable": "floor reachable at 36 inches", "no_stairs": "no stairs inside", "bed_to_bathroom": "bed to bathroom distance",
                     "entry_to_kitchen": "entry to kitchen distance", "bathroom_space": "bathroom size", "simple_paths": "simple paths", "level_changes": "no level changes",
                     "clear_paths": "paths clear of furniture", "open_sightlines": "open sightlines", "entry_visible_from_seat": "entry visible from the main seat",
                     "entry_visible_from_kitchen": "entry visible from the kitchen", "daylight": "windows in main rooms"}
            for k, c in unit.get("checks", {}).items():
                add(title, f"On {names.get(k, k)}, this unit gets {c['value'] * 100:.0f} percent.", k.replace("_", " ") + " turning circle stairs threshold reach")
            for d in unit["doors"]:
                if d.get("is_door") and not d.get("minor") and not (d.get("exterior") and not d.get("entry")):
                    st = {"pass": "wide enough for a wheelchair", "tight": "tight for a wheelchair", "fail": "too narrow for a wheelchair, which needs 32 inches"}[d["status"]]
                    kinds = [t.lower() for t in d.get("room_types", []) if t not in ("Hallway", "Other", "Living Room", "Entrance")] or [t.lower() for t in d.get("room_types", []) if t != "Other"]
                    which = "The entry door" if d.get("entry") else (f"The {kinds[0]} door" if kinds else "A doorway")
                    add(title, f"{which} is about {d['est_clear_in']:.0f} inches clear, {st}.", "door doorway inches wide how " + ("entry front" if d.get("entry") else " ".join(kinds)), 1.1)
            circles = {c.get("room") for c in unit.get("turning_circles", [])}
            for r_ in unit["rooms"]:
                if r_["type"] in ("Bedroom", "Bathroom", "Kitchen"):
                    fits = r_["id"] in circles
                    add(title, f"A 60 inch wheelchair turning circle {'fits' if fits else 'does not fit, or cannot be reached,'} in the {r_['type'].lower()}"
                               + (" (measured before fixtures such as the toilet and tub)." if r_["type"] == "Bathroom" else "."),
                        f"turn turning around circle spin wheelchair room {r_['type'].lower()}", 1.15)
            if unit.get("scan_date"):
                add(title, f"The scan was made on {unit['scan_date']}.", "scan date when old recent")
            bb = unit["stats"].get("bed_to_bathroom_m")
            if bb is not None:
                add(title, f"From the bed to the bathroom is {bb:.0f} meters, {narrate.steps(bb)}.", "bed bathroom distance far night")

    rel = screen.get("relocation")
    if rel and rel.get("candidates"):
        add("moving", f"For your move, the best matching neighborhoods are {narrate._join([f'{c['name']} with a match of {c['match']}' for c in rel['candidates']])}.", "match neighborhood moving best where", 1.1)
        for c in rel["candidates"]:
            add("moving", f"{c['name']}: {c.get('reason', '')}", "match neighborhood " + c["name"])
            for cat, pair in (c.get("comparison") or {}).items():
                add("moving", f"In {c['name']}, the {cat} is {pair[1]:.0f} minutes away, against {pair[0]:.0f} minutes where you live now.", f"{cat} minutes now there compare " + c["name"], 0.8)
    return out


def answer(question: str, screen: dict | None, profiles: list[str], top: int = 6) -> dict | None:
    """The best matching on-screen facts as a spoken answer, or None when nothing on screen matches."""
    facts = facts_for(screen or {}, profiles)
    q = _expand(_tokens(question))
    if not q or not facts:
        return None
    # rarer words count for more
    df: dict[str, int] = {}
    docs = []
    for f in facts:
        toks = set(_tokens(f["text"] + " " + f["kw"] + " " + f["about"]))
        docs.append(toks)
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n = len(facts)
    scored = []
    for f, toks in zip(facts, docs):
        s = sum(w * math.log(1 + n / df[t]) for t, w in q.items() if t in toks) * f["w"]
        if s > 0:
            scored.append((s, f))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    best = scored[0][0]
    strong = sum(w * math.log(1 + n / max(1, df.get(t, 1))) for t, w in q.items() if w >= 1.0) or 1.0
    if best < 0.45 * strong and best < 2.2:
        return None
    picked = [f for s, f in scored[:2] if s >= 0.9 * best]
    said = " ".join(dict.fromkeys(f["text"] for f in picked))
    return {"text": said, "facts": [f["text"] for f in picked], "about": picked[0]["about"],
            "all": list(dict.fromkeys(f["text"] for _s, f in scored[:top]))}
