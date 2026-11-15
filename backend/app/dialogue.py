"""The conversation manager: voice-first, and usable with no cloud key.

Each turn has three steps:
  1. Understand. A local open-source language model (brain.py, through Ollama) turns what was said into a
     command: intent, needs, budget, a place, and the qualities wanted ("safe", "near a bus stop", "cheap").
     If no model is running, keyword rules do the same job less flexibly.
  2. Act. The command runs against the measured data: rank homes, describe one, narrate a unit, route, compare.
  3. Speak. The answer is built from those numbers (narrate.py), and comes with the UI actions that move the app.

The conversation's thread lives in a small state that rides along with each request: needs, city, budget,
priorities, the homes just listed, the home and unit being discussed. What is on screen counts too.
With ANTHROPIC_API_KEY set, agent.py uses Claude with tools instead.
"""
from __future__ import annotations

import math
import re

from . import brain, narrate

NEEDS = {"wheelchair": r"wheel ?chair|power ?chair|scooter|walker|can(?:'|no)?t (?:use|climb|do) stairs|paraly",
         "blind": r"\bblind\b|low vision|can(?:'|no)?t see|visually impaired|guide dog|white cane",
         "deaf": r"\bdeaf\b|hard of hearing|hearing (?:loss|aid|impair)|can(?:'|no)?t hear",
         "older": r"\bolder\b|elderly|senior|low stamina|\b(?:mom|mother|dad|father|grand\w+)\b|arthritis|tires? (?:easily|quickly)"}
NEED_NAME = {"wheelchair": "wheelchair", "blind": "blind or low vision", "deaf": "deaf or hard of hearing", "older": "older adult"}
CATS = {"pharmacy": r"pharmac|drug ?store|medicine", "clinic": r"clinic|doctor", "grocery": r"grocer|supermarket|food shop|market",
        "transit": r"\bbus\b|transit|train|rail|metro|public transport", "hospital": r"hospital|emergency|\ber\b", "school": r"school|college|university",
        "park": r"\bpark\b", "restaurant": r"restaurant|cafe|coffee|eat out|dining"}
CAT_WORD = {"pharmacy": "pharmacy", "clinic": "clinic", "grocery": "grocery", "transit": "bus or rail stop", "hospital": "hospital", "school": "school", "park": "park", "restaurant": "restaurants"}
PRIORITY_RULES = {"safe_streets": r"\bsafe|safety|secure|dangerous", "cheap": r"cheap|affordable|low rent|inexpensive|budget friendly", "new_building": r"\bnew(?:er)?\b|modern|recent",
                  "elevator": r"elevator|lift", "single_story": r"no stairs|single stor|one level|ground floor|one floor", "bright": r"sunny|bright|lots of light|natural light|big windows",
                  "quiet": r"quiet|peaceful|calm", "spacious": r"spacious|roomy|\bbig\b|large"}
PRIORITY_WORD = {"safe_streets": "safer streets", "cheap": "lower rent", "new_building": "newer buildings", "elevator": "an elevator", "single_story": "one level",
                 "spacious": "more space", "close_to_transit": "a bus or rail stop close by", "close_to_pharmacy": "a pharmacy close by", "close_to_hospital": "a hospital close by",
                 "close_to_clinic": "a clinic close by", "close_to_grocery": "a grocery close by", "close_to_park": "a park close by", "close_to_school": "a school close by",
                 "close_to_restaurants": "restaurants close by"}
NO_DATA = {"quiet": "I have no noise data, so I can't rank by quiet.",
           "bright": "I can't rank homes by light yet, but ask me about the windows and sun in any home and I'll describe them."}
ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1}
GENERIC_PLACES = r"^(?:a |an |the |any |some )?(?:safe|quiet|nice|good|decent|great|better|best|clean|friendly|walkable|accessible|affordable|cheap)\b|^(?:a|an|any|some)\s|locality|localities|neighbou?rhood$|\barea$|^(?:me|here|there|it|town|the city|houston|los angeles|la)$"
HELP = ("Tell me what you need in your own words. For example: I use a wheelchair and want a safe area near a bus stop under 1,800 dollars. "
        "Then ask about any home: what it is like inside, the windows and sun, how to get to the pharmacy, or the neighborhood. "
        "You can also ask about anything on the screen, say next for another home, or tell me you are moving from an address.")


# ---------------------------------------------------------------- 1. understand

def _rules(text: str, st: dict) -> dict:
    """Keyword version of brain.parse(), for when no language model is running."""
    low = text.lower()
    cmd = {"intent": None, "needs": [p for p, rx in NEEDS.items() if re.search(rx, low)], "city": None, "max_rent": None, "near_place": None,
           "priorities": [p for p, rx in PRIORITY_RULES.items() if re.search(rx, low)], "bedrooms": None, "which_home": None, "destination": None, "topic": None, "from_address": None}
    cat = next((c for c, rx in CATS.items() if re.search(rx, low)), None)
    mv = re.search(r"(?:moving|move|relocat\w*)\s+(?:from|out of)\s+(.+?)(?:\s+to\s+(?:houston|los angeles|la)\b|[.;!?]|$)", text, re.I)
    route_words = re.search(r"route|how (?:do|can|would) (?:i|we|she|he)|get to|way to|walk to|roll to|go to|directions|take me", low)
    find_words = re.search(r"\b(find|search|look(?:ing)? for|show|need|want|recommend|suggest|give me|get me)\b.*\b(home|homes|apartment|apartments|place|places|unit|condo|house|somewhere|live|rent)\b"
                           r"|apartments? (?:near|in|around)|homes? (?:near|in|around)|near me|around me|somewhere to live", low)
    if re.search(r"\b(repeat|say that again|again please|pardon)\b", low):
        cmd["intent"] = "repeat"
    elif re.fullmatch(r"\W*(help|what can (?:you|i) (?:do|say|ask)\??|options)\W*", low):
        cmd["intent"] = "help"
    elif mv:
        cmd["intent"], cmd["from_address"] = "moving", mv.group(1).strip(" ,")
    elif cat and route_words:
        cmd["intent"], cmd["destination"] = "route", cat
    elif find_words:
        cmd["intent"] = "find_homes"
        if cat and re.search(r"near|close|by a|next to|walking distance", low):
            cmd["priorities"].append(f"close_to_{'restaurants' if cat == 'restaurant' else cat}")
    elif re.search(r"\b(next|another|other one|something else|different one|skip)\b", low):
        cmd["intent"] = "next_home"
    elif re.search(r"inside|interior|layout|floor ?plan|walk me through|describe (?:it|the (?:apartment|unit|home|place))|what(?:'s| is) it like", low):
        cmd["intent"] = "inside_home"
    elif re.search(r"neighbou?rhood|\barea\b|around (?:it|here|there)|what(?:'s| is) (?:near|close)|nearby", low):
        cmd["intent"] = "neighborhood"
    elif re.match(r"\s*(what|which|who|how|why|when|where|is|are|does|do|did|can|could|will|would|has|have|any)\b", low) or low.rstrip().endswith("?"):
        cmd["intent"] = "question"
    pm = re.search(r"(?:near|in|around|close to|by)\s+(?:the\s+)?([a-z][a-z .'-]{2,40}?)(?=\s+(?:under|below|for|with|that|which|and i|\$)|[,.;!?]|$)", low)
    if pm:
        cmd["near_place"] = pm.group(1).strip()
    return cmd


def _understand(text: str, st: dict, screen: dict) -> tuple[dict, bool]:
    low = text.lower()
    homes = st.get("homes") or []
    situation = []
    if homes:
        situation.append("Homes just listed: " + ", ".join(f"{i + 1}. {h['name']}" for i, h in enumerate(homes)) + ".")
    cur = next((h["name"] for h in homes if h["id"] == st.get("home")), None) or (screen.get("selected_name") if screen.get("selected_building") else None)
    if cur:
        situation.append(f"Current home: {cur}.")
    if screen.get("unit"):
        situation.append("A unit's floor plan is open.")
    if screen.get("route"):
        situation.append("A route is drawn on the map.")
    cmd = brain.parse(text, " ".join(situation))
    smart = cmd is not None
    rules = _rules(text, st)
    if not smart:
        cmd = rules
    # things a pattern reads more reliably than a small model
    if rules["intent"] in ("repeat", "help") and len(low.split()) <= 5:
        cmd["intent"] = rules["intent"]
    m = re.search(r"(?:\$|under|below|budget(?: of| is)?|max(?:imum)?|up to|less than)\s*\$?\s*([\d,]{3,6})|([\d,]{3,6})\s*(?:dollars|bucks|a month|per month|/ ?mo)", low)
    if m:
        cmd["max_rent"] = int((m.group(1) or m.group(2)).replace(",", ""))
    elif cmd.get("max_rent") and not re.search(r"\d|thousand|hundred|grand|\bk\b", low):
        cmd["max_rent"] = None                       # a budget nobody said
    cmd["needs"] = [n for n in (cmd.get("needs") or []) if re.search(NEEDS[n], low)] or rules["needs"]
    if cmd.get("intent") == "route" and not cmd.get("destination"):
        cmd["destination"] = rules["destination"] or next((c for c, rx in CATS.items() if re.search(rx, low)), None)
    if cmd.get("intent") == "moving" and not cmd.get("from_address"):
        cmd["from_address"] = rules["from_address"]
    # a "place" that is really a quality or a kind of place becomes a priority
    near = (cmd.get("near_place") or "").strip().lower()
    if near:
        cat = next((c for c, rx in CATS.items() if re.search(rx, near)), None)
        named = re.search(r"[A-Z]", cmd.get("near_place") or "") or near in ("midtown", "downtown", "montrose", "the heights", "heights", "galleria", "medical center", "texas medical center", "museum district", "koreatown", "hollywood", "santa monica")
        if cat and not re.search(r"university|center|centre|district|\bst\b|street|avenue|road|blvd", near):
            cmd["priorities"] = list(dict.fromkeys((cmd.get("priorities") or []) + [f"close_to_{'restaurants' if cat == 'restaurant' else cat}"]))
            cmd["near_place"] = None
        elif re.search(GENERIC_PLACES, near) and not named:
            cmd["near_place"] = None
        elif (any(near == h["name"].lower() for h in homes) or near == (cur or "").lower()) and not re.search(r"\b(near|around|close to|next to)\b", low):
            cmd["near_place"] = None
    if re.search(PRIORITY_RULES["safe_streets"], low) and cmd.get("intent") == "find_homes":
        cmd["priorities"] = list(dict.fromkeys((cmd.get("priorities") or []) + ["safe_streets"]))
    # small models add qualities nobody mentioned: keep these only when a matching word was said
    for p, rx in (("new_building", PRIORITY_RULES["new_building"]), ("elevator", PRIORITY_RULES["elevator"]), ("cheap", PRIORITY_RULES["cheap"]),
                  ("quiet", PRIORITY_RULES["quiet"]), ("bright", PRIORITY_RULES["bright"]), ("spacious", PRIORITY_RULES["spacious"]), ("single_story", PRIORITY_RULES["single_story"])):
        if p in (cmd.get("priorities") or []) and not re.search(rx, low):
            cmd["priorities"].remove(p)
    if cmd.get("bedrooms") and not re.search(r"bed|br\b|studio", low):
        cmd["bedrooms"] = None
    if re.search(r"los angeles|\bl\.?a\.?\b", low) and cmd.get("intent") != "moving":
        cmd["city"] = "la"
    elif "houston" in low and cmd.get("intent") != "moving":
        cmd["city"] = "houston"
    else:
        cmd["city"] = None
    return cmd, smart


def _pick_home(cmd: dict, low: str, st: dict) -> str | None:
    homes = st.get("homes") or []
    if not homes:
        return None
    which = str(cmd.get("which_home") or "").strip().lower()
    if re.fullmatch(r"\d", which) and int(which) - 1 < len(homes) and re.search(r"\d|first|second|third|fourth|fifth|one|two|three|number|option", low):
        return homes[int(which) - 1]["id"]
    m = re.search(r"(?:number|option|#)\s*(\d)\b|^\W*(\d)\W*$", low)
    if m and 0 <= int(m.group(1) or m.group(2)) - 1 < len(homes):
        return homes[int(m.group(1) or m.group(2)) - 1]["id"]
    for word, i in ORDINALS.items():
        if re.search(rf"\b{word}\b", low) and -1 <= i < len(homes):
            return homes[i]["id"]
    flat = re.sub(r"[^a-z0-9 ]", "", low)
    for h in homes:
        name = re.sub(r"[^a-z0-9 ]", "", h["name"].lower())
        if len(name) > 4 and (name in flat or (which and (which in name or name in which))):
            return h["id"]
    return None


SUFFIX = {"street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr", "road": "rd", "lane": "ln", "parkway": "pkwy", "place": "pl",
          "court": "ct", "circle": "cir", "terrace": "ter", "highway": "hwy", "north": "n", "south": "s", "east": "e", "west": "w"}
_names: dict[str, list] = {}


def _norm(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return " ".join(SUFFIX.get(w, w) for w in words)


def _name_index() -> list:
    """(key, building id, city) for every building name and street address, longest keys first."""
    if not _names:
        from . import main as api
        keys = []
        for b in api.buildings().values():
            street = b.get("address", "").split(",")[0]
            name = _norm(b["name"])
            short = re.sub(r"^the |\s+(?:apartment homes|apartments|apts|apartment|residences|homes|condominiums|condos|townhomes|lofts)$", "", name).strip()
            for k in {name, short, _norm(street)}:
                if len(k) >= 7 and not k.isdigit():
                    keys.append((k, b["id"], b["city"]))
                    parts = k.split()
                    if parts[-1] in SUFFIX.values() and len(parts) >= 3:      # "404 avondale st" also matches "404 avondale"
                        keys.append((" ".join(parts[:-1]), b["id"], b["city"]))
        keys.sort(key=lambda t: -len(t[0]))
        _names["all"] = keys
    return _names["all"]


def find_named(text: str, city: str | None = None) -> str | None:
    """A building the person named by its name or street address ("404 Avondale", "The Hopkins")."""
    t = f" {_norm(text)} "
    hits = [(k, bid, c) for k, bid, c in _name_index() if f" {k} " in t]
    if not hits:
        return None
    best = len(hits[0][0])
    top = [h for h in hits if len(h[0]) == best]
    return next((bid for _k, bid, c in top if c == city), top[0][1])


# ---------------------------------------------------------------- the guided conversation (the agent leads)

GUIDE_START = "__start__"
GUIDE_STAGES = ("needs", "budget", "area", "pick", "tour", "furniture")
ALL_NEEDS = ["wheelchair", "blind", "deaf", "older"]
GREETING = ("Hi, I'm NavEasy. I'll help you find a home in Houston or Los Angeles and walk you through it, room by room. "
            "First, what help do you need at home? For example: you use a wheelchair, you're blind or have low vision, "
            "you're deaf or hard of hearing, or you're an older adult. Or say none.")
YES = r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|go ahead|do it|of course|sounds good|alright|why not|let'?s do it|definitely)\b"
NO = r"^\W*(?:no|nope|nah|not now|skip|no thanks|don'?t|never ?mind|i'?m (?:good|fine))\b"
NO_NEEDS = r"\b(?:none|no(?: special)? needs?|nothing|i'?m fine|just looking|no disabilit\w*|not really|no help|nobody|doesn'?t apply)\b|^\W*no\W*$"
NO_BUDGET = r"\b(?:no budget|any(?: price| budget)?|doesn'?t matter|not sure|no limit|whatever|flexible|no max\w*|don'?t know)\b"
ANYWHERE = r"\b(?:anywhere|whole city|any ?where|doesn'?t matter|no preference|all over|not sure|don'?t mind|any area|surprise me)\b"
ADDRESS = (r"\b\d{1,6}\s+(?:[nsew]\.?\s+)?[a-z0-9.' -]+?\s(?:st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|way|pl|place|ct|court|pkwy|parkway|hwy|highway)\b"
           r"[^.;!?]*")
SAY_NUM = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "a pair of": 2, "pair of": 2}
FURNITURE = [  # (pattern, planner label); longer names first so "queen bed" isn't also a "bed"
    (r"king(?:[- ]size(?:d)?)? beds?", "king bed"), (r"queen(?:[- ]size(?:d)?)? beds?", "queen bed"),
    (r"(?:full|double)(?:[- ]size(?:d)?)? beds?", "full bed"), (r"(?:twin|single)(?:[- ]size(?:d)?)? beds?", "twin bed"),
    (r"night ?stands?|bedside tables?", "nightstand"), (r"dressers?|chests? of drawers", "dresser"), (r"wardrobes?|armoires?", "wardrobe"),
    (r"sectionals?(?: sofa| couch)?", "sectional"), (r"love ?seats?", "loveseat"), (r"sofas?|couch(?:es)?", "sofa"),
    (r"arm ?chairs?|recliners?|accent chairs?|easy chairs?", "armchair"), (r"coffee tables?", "coffee table"),
    (r"tv stands?|tv|television|entertainment (?:center|unit)", "tv stand"), (r"book ?(?:shelf|shelves|cases?)|shelving units?", "bookshelf"),
    (r"pianos?", "piano"), (r"desks?", "desk"), (r"(?:dining |kitchen )?tables?", "dining table"), (r"\bbeds?\b", "queen bed"),
]


def parse_furniture(text: str) -> list[str]:
    """The pieces someone says they own ("a queen bed, two nightstands and a couch") as planner labels, with repeats."""
    low = " " + re.sub(r"[^a-z0-9' ]", " ", text.lower()) + " "
    out: list[tuple[int, str]] = []
    for rx, label in FURNITURE:
        for m in list(re.finditer(rf"(?:\b(\d|one|two|three|four|a pair of|pair of|an?)\s+)?(?:\w+\s+)?\b(?:{rx})(?=\W)", low)):
            word = (m.group(1) or "one").strip()
            n = int(word) if word.isdigit() else SAY_NUM.get(word, 1)
            out += [(m.start(), label)] * max(1, min(n, 4))
            low = low[:m.start()] + " " * (m.end() - m.start()) + low[m.end():]   # used up
    return [label for _pos, label in sorted(out, key=lambda t: t[0])]


def _words_number(low: str) -> int | None:
    """A budget said as words or bare digits: "two thousand", "1800", "two point five k"."""
    m = re.search(r"\b(\d{1,2}(?:[.,]\d)?)\s*(?:k|thousand|grand)\b", low)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1000)
    m = re.search(r"\b(\d[\d,]{2,5})\b", low)
    if m:
        return int(m.group(1).replace(",", ""))
    units = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
    m = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine)\s+thousand(?:\s+(?:and\s+)?(one|two|three|four|five|six|seven|eight|nine)\s+hundred)?", low)
    if m:
        return units[m.group(1)] * 1000 + (units[m.group(2)] * 100 if m.group(2) else 0)
    m = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine)\s+hundred\b", low)
    if m:
        return units[m.group(1)] * 100
    return None


def _needs_words(st: dict) -> str:
    return "no special needs" if st.get("general") else " and ".join(NEED_NAME[p] for p in st["profiles"])


def _fill(t: str, low: str, cmd: dict, st: dict) -> list[str]:
    """Take whatever the person said that answers the guide's questions, in any order. Returns what was learned."""
    got = []
    stage = st.get("guide")
    if cmd.get("needs"):
        st["profiles"], st["general"], st["needs_known"] = cmd["needs"], False, True
        got.append(_needs_words(st))
    elif stage == "needs" and re.search(NO_NEEDS, low):
        st["profiles"], st["general"], st["needs_known"] = list(ALL_NEEDS), True, True
        got.append("no special needs")
    budget = cmd.get("max_rent") or (_words_number(low) if stage == "budget" else None)
    if budget and budget >= 300:
        st["budget"], st["budget_known"] = budget, True
        got.append(f"up to {budget:,} dollars a month")
    elif stage == "budget" and re.search(NO_BUDGET, low):
        st.pop("budget", None)
        st["budget_known"] = True
        got.append("any budget")
    addr = cmd.get("from_address") or (re.search(ADDRESS, low) and t[re.search(ADDRESS, low).start():].strip(" .;!?"))
    rest = low.replace(addr.lower(), " ") if addr else low      # a city named inside an address isn't where they want to live
    if cmd.get("city") and re.search(r"los angeles|\bl\.?a\.?\b|houston", rest):
        st["city"], st["city_said"] = cmd["city"], True
    if addr and (stage == "area" or re.search(r"\blive|\bliving|moving|current|now\b", low)):
        st["origin"], st["area_known"] = addr, True
        st.pop("near", None)
        got.append(f"somewhere like {addr}")
    elif cmd.get("near_place"):
        place = cmd["near_place"]
        place = place.title() if place == place.lower() else place
        st["near"], st["area_known"] = place, True
        st.pop("origin", None)
        got.append(f"near {place}")
    elif stage == "area" and (re.search(ANYWHERE, low) or (cmd.get("city") and len(low.split()) <= 4)):
        st.pop("near", None)
        st.pop("origin", None)
        st["area_known"] = True
        got.append("anywhere in " + ("Houston" if st["city"] == "houston" else "Los Angeles"))
    return got


def _center(place: str, st: dict):
    from . import agent
    from . import main as api
    c = agent.PLACES.get(place.lower().strip())
    if not c:
        bid = find_named(place, st["city"])
        if bid:
            bb = api.buildings()[bid]
            c = (bb["lat"], bb["lon"])
    if not c:
        from pipeline.relocate import geocode
        g = geocode(place + (", Houston, TX" if st["city"] == "houston" else ", Los Angeles, CA"))
        c = (g["lat"], g["lon"]) if g else None
    return c


def _search(st: dict, actions: list) -> tuple[list[dict], int, str]:
    """Homes for the guide's answers: (homes, how many matched, a lead-in sentence)."""
    from . import main as api
    ps = st["profiles"]
    if st.get("origin"):
        from pipeline import relocate as RL
        r = RL.match({"address": st["origin"]}, ps, None, st.get("budget"), st["city"] if st.get("city_said") else None)
        if r.get("error") or not r.get("candidates"):
            return [], 0, r.get("error") or r.get("message") or "I found no neighborhood like it."
        st["city"] = r.get("target") or st["city"]
        bs = api.buildings()
        homes = []
        for c in r["candidates"]:
            for h in c["homes"][:2]:
                b = bs.get(h["id"])
                if b and not any(x["id"] == b["id"] for x in homes):
                    homes.append({"id": b["id"], "name": f"{b['name']} in {c['name']}", "score": h["score"], "rent": b.get("rent_min"),
                                  "inside": bool(b.get("inside")), "lat": b["lat"], "lon": b["lon"]})
        homes.sort(key=lambda h: -h["score"])
        actions.append({"type": "show_relocation", "request": {"origin": {"address": st["origin"]}, "profiles": ps, "budget": st.get("budget"), "must_haves": []}})
        lead = (f"I compared how close everyday places are to where you live now. These {r['target_name']} neighborhoods come closest: "
                + narrate._join([f"{c['name']}, match {c['match']}" for c in r["candidates"][:3]]) + ". ")
        return homes, len(homes), lead
    center = _center(st["near"], st) if st.get("near") else None
    homes, notes = _rank(st, ps, center, 3.0)
    lead = (" ".join(notes) + " ") if notes else ""
    if st.get("near") and not center:
        lead = f"I couldn't find {st['near']} on the map, so I searched the whole city. " + lead
    return homes, (homes[0]["n"] if homes else 0), lead


def _pitch(st: dict) -> tuple[str, str | None]:
    """The broker's summary of the current home, and the unit it describes."""
    from . import main as api
    ps = st["profiles"]
    b = api.building(st["home"], ",".join(ps), "avg" if st.get("general") else "min")
    uid = _best_unit(st["home"], ps)
    u = None
    if uid:
        try:
            u = api.unit_heatmap(uid, ps[0])
            u = u if u.get("has_graph") else None
        except Exception:  # noqa: BLE001
            u = None
    return narrate.broker_pitch(b, u, ps, st.get("general", False)), (uid if u else None)


def _pick_guided(low: str, cmd: dict, st: dict) -> str | None:
    home = _pick_home(cmd, low, st)
    homes = st.get("homes") or []
    if not home and len(low.split()) <= 5:
        m = re.search(r"\b(one|two|three|four|five)\b", low)
        i = {"one": 0, "two": 1, "three": 2, "four": 3, "five": 4}[m.group(1)] if m else None
        if i is not None and i < len(homes):
            home = homes[i]["id"]
    return home


def _guide(t: str, low: str, cmd: dict, st: dict, actions: list) -> str | None:
    """One step of the conversation the agent leads. Returns what to say, or None to let the ordinary handlers answer."""
    stage = st["guide"]
    if stage in ("needs", "budget", "area"):
        got = _fill(t, low, cmd, st)
        if not got and cmd.get("intent") in ("route", "question", "inside_home", "about_home", "neighborhood") and not re.search(YES + "|" + NO, low):
            return None                                              # a question of its own: answer it, keep the place in the guide
        ack = ("Okay: " + ", ".join(got) + ". ") if got else ""
        if not st.get("needs_known"):
            return ack + ("Sorry, I didn't catch that. " if not got else "") + "What help do you need at home? Say wheelchair, blind or low vision, deaf or hard of hearing, older adult, or none."
        if not st.get("budget_known"):
            st["guide"] = "budget"
            return ack + "What's your monthly budget for rent? Say a number, or say any."
        if not st.get("area_known"):
            st["guide"] = "area"
            return (ack + "And where? Name a neighborhood or a place you need to be near, like the Medical Center or Midtown. "
                    "Or, if you want somewhere like where you live now, tell me your current address.")
        homes, n, lead = _search(st, actions)
        if not homes:
            st["guide"], st["area_known"] = "area", False
            return ack + lead + " " + ("I found no homes for that. Try a wider area, or a higher budget: say anywhere, or a new budget.")
        st["homes"] = [{"id": h["id"], "name": h["name"], "score": h["score"]} for h in homes[:5]]
        st["home"], st["unit"], st["guide"] = None, None, "pick"
        top = homes[:3]
        actions.append({"type": "set_map_view", "center": [sum(h["lon"] for h in top) / len(top), sum(h["lat"] for h in top) / len(top)],
                        "zoom": 13.2, "profiles": st["profiles"], "city": st["city"]})
        actions.append({"type": "highlight_buildings", "ids": [h["id"] for h in homes[:5]]})
        words = ["One", "Two", "Three"]
        for i, h in enumerate(top):     # the map follows the list as it is read out
            actions.append({"type": "set_map_view", "center": [h["lon"], h["lat"]], "zoom": 15.8, "cue": f"{words[i]}: {h['name']}"})
        return ack + lead + narrate.home_list(homes, n, st.get("general", False))
    if stage in ("pick", "tour") and (stage == "pick" or not re.search(YES, low)):
        home = _pick_guided(low, cmd, st)
        if home:
            st["home"], st["unit"] = home, None
            text, uid = _pitch(st)
            st["unit"] = uid
            actions.append({"type": "open_building", "id": home})
            from . import main as api
            b = api.buildings()[home]
            actions.append({"type": "set_map_view", "center": [b["lon"], b["lat"]], "zoom": 17.2})
            if uid:
                st["guide"] = "tour"
                return text + " Shall I walk you through it, starting at the front door?"
            return text + " I can't walk you through this one, because nothing inside is measured. Would you like to hear another? Say one, two or three."
        if stage == "pick" and re.search(NO + r"|none of (?:them|these)|something else|other options|different", low):
            st["guide"], st["area_known"] = "area", False
            return "Okay. Tell me a different area or place to be near, or say anywhere. You can also change the budget: say a new number."
        if stage == "pick":
            return None
    if stage == "tour":
        if re.search(YES + r"|walk me|tour|go on|start|front door|inside", low):
            from . import main as api
            u = api.unit_heatmap(st["unit"], st["profiles"][0])
            n = narrate.unit_narration(u, st["profiles"][0], st["profiles"])
            sec = n["sections"]
            keys = (["layout"] + (["paths"] if "blind" in st["profiles"] else []) + ["rooms", "fixtures"]
                    + (["doors"] if st.get("general") or {"wheelchair", "older"} & set(st["profiles"]) else []) + ["problems"])
            actions.append({"type": "open_unit", "model_id": st["unit"], "profile": st["profiles"][0]})
            st["guide"] = "furniture"
            return (narrate._say_in(" ".join(sec[k] for k in keys if k in sec)) + " Would you like me to plan where your furniture goes? "
                    "Tell me what you own, for example a queen bed, a desk and a sofa. Or say no.")
        if re.search(NO, low):
            st["guide"] = "furniture"
            return "Okay. Would you like me to plan where your furniture goes? Tell me what you own, for example a queen bed, a desk and a sofa. Or say no."
        return None
    if stage == "furniture":
        pieces = parse_furniture(low)
        if pieces:
            return _furnish(pieces, st, actions)
        if re.search(NO, low):
            st["guide"] = "done"
            return "No problem. " + _closing(st)
        if re.search(YES, low):
            return "What furniture do you own? For example: a queen bed, two nightstands, a sofa and a desk."
        return None
    return None


def _furnish(pieces: list[str], st: dict, actions: list) -> str:
    from pipeline.furnish import plan_furnishing

    from . import main as api
    if not st.get("unit"):
        st["guide"] = "done"
        return "I need a home with a measured floor plan to place furniture. Pick one first: say one, two or three."
    ps = st["profiles"]
    plan = plan_furnishing(st["unit"], [{"label": p} for p in pieces], ps, {})
    u = api.unit_heatmap(st["unit"], ps[0])
    said = narrate.plan_narration(plan, u, ["older"] if st.get("general") else ps)
    actions.append({"type": "open_unit", "model_id": st["unit"], "profile": ps[0], "tab": "plan", "plan": plan})
    st["guide"] = "done"
    say = lambda p: narrate.PIECE.get(p, p)  # noqa: E731
    names = narrate._join([say(p) if pieces.count(p) == 1 else f"{pieces.count(p)} {say(p)}s" for p in dict.fromkeys(pieces)])
    return f"Here's where your {names} go{'es' if len(pieces) == 1 else ''}, so you can still get everywhere. " + said["text"] + " " + _closing(st)


def _closing(st: dict) -> str:
    from . import main as api
    b = api.building(st["home"], ",".join(st["profiles"])) if st.get("home") else {}
    check = [x for x in b.get("checklist", []) if not x.startswith("We did not check")][:2]
    return ((("Before you sign: " + " ".join(check) + " ") if check else "")
            + "Ask me anything else about it, like the way to the pharmacy, or say start over to look again.")


# ---------------------------------------------------------------- 2. act

def _rank(st: dict, ps: list[str], center, km: float) -> tuple[list[dict], list[str]]:
    """Homes ranked for the person's needs and the qualities they asked for. Returns (homes, notes for the listener)."""
    from pipeline import locality

    from . import main as api
    city, budget, pri, beds = st["city"], st.get("budget"), st.get("priorities") or [], st.get("bedrooms")
    try:
        cells = api.hexes(city)["by_id"]
    except Exception:  # noqa: BLE001
        cells = {}
    rows = []
    for b in api.buildings().values():
        if b["city"] != city:
            continue
        if center and math.hypot((b["lat"] - center[0]) * 110.57, (b["lon"] - center[1]) * 96.6) > km:
            continue
        if budget and not (b.get("rent_min") and b["rent_min"] <= budget):
            continue
        if beds:
            caps = " ".join((u.get("caption") or "") for u in b["units"]).lower()
            if not (b.get("beds") == beds or re.search(rf"\b{beds}\s*b(?:r|ed)", caps)):
                continue
        base = (sum(b["scores"][p]["score"] for p in ps) / len(ps) if st.get("general") else min(b["scores"][p]["score"] for p in ps)) / 100
        cell = cells.get(b["h3"]) or {}
        streets = cell.get("streets")
        mins = (cell.get("minutes") or {}).get(ps[0], {})
        total, why = base + (0.05 if b.get("inside") else 0), []
        for p in pri:
            if p == "safe_streets":
                e = locality.street_score(streets)
                v = sum(e[q] for q in ps) / len(ps) if e else None
                total += 0.7 * (v if v is not None else 0.25)
                if streets:
                    bit = f"sidewalks on {streets['sidewalk_coverage'] * 100:.0f} percent of nearby streets"
                    if streets.get("signalized_share") is not None:
                        bit += f" and signals at {streets['signalized_share'] * 100:.0f} percent of crossings"
                    why.append(bit)
                else:
                    why.append("no street records nearby, so street safety is unknown")
            elif p.startswith("close_to_"):
                cat = p[9:].replace("restaurants", "restaurant")
                t = mins.get(cat)
                total += 0.6 * (max(0.0, min(1.0, (30 - t) / 25)) if t is not None else 0.0)
                if t is not None:
                    why.append(f"{CAT_WORD[cat]} {'about ' if not cell.get('live') else ''}{t:.0f} minutes away")
            elif p == "cheap":
                r = b.get("rent_min")
                total += 0.6 * (max(0.0, min(1.0, (2500 - r) / 1700)) if r else 0.2)
            elif p == "new_building":
                y = b.get("year_built")
                total += 0.5 * (max(0.0, min(1.0, (y - 1970) / 45)) if y else 0.2)
                if y:
                    why.append(f"built {y}")
            elif p == "elevator":
                total += 0.6 if b.get("elevator") else 0.0
            elif p == "single_story":
                total += 0.6 if b.get("stories") == 1 else 0.0
            elif p == "spacious":
                area = max((u.get("stats") or {}).get("area_m2", 0) for u in b["units"]) if b["units"] else 0
                total += 0.5 * min(1.0, area / 120)
                if area:
                    why.append(f"up to {area * 10.764:,.0f} square feet")
        rows.append((total, b, why))
    rows.sort(key=lambda t: -t[0])
    notes = []
    if "safe_streets" in pri:
        notes.append("I have no crime data. By safe I mean streets that are safer to get around for you: sidewalks, crossings with signals, and lowered curbs.")
    notes += [NO_DATA[p] for p in pri if p in NO_DATA]
    agg = (lambda v: round(sum(v) / len(v))) if st.get("general") else min  # noqa: E731
    out = [{"id": b["id"], "name": b["name"], "score": agg([b["scores"][p]["score"] for p in ps]), "rent": b.get("rent_min"), "inside": bool(b.get("inside")),
            "access": b["components"]["vertical_access"]["why"], "why": why, "lat": b["lat"], "lon": b["lon"], "n": len(rows)} for _t, b, why in rows[:5]]
    return out, notes


def _best_unit(building_id: str, profiles: list[str]) -> str | None:
    from . import main as api
    b = api.buildings().get(building_id) or {}
    best = ((b.get("inside") or {}).get(profiles[0]) or {}).get("best_model")
    return best or next((u["model_id"] for u in b.get("units", []) if u.get("status") == "measured" and u.get("dwelling")), None)


def respond(text: str, state: dict | None, context: dict | None = None) -> dict:
    from . import agent
    from . import main as api
    from . import screen as screen_qa
    st = dict(state or {})
    ctx = context or {}
    screen = dict(ctx.get("screen") or {})
    st.setdefault("profiles", ctx.get("profiles") or ["wheelchair"])
    st.setdefault("city", ctx.get("city") or "houston")
    t = text.strip()
    low = t.lower()
    actions: list = []

    def done(msg: str, remember: bool = True) -> dict:
        if remember:
            st["last"] = msg
        return {"text": msg, "ui_actions": actions, "state": st, "understood_by": "local model " + (brain.model() or "") if smart else "keyword rules"}

    smart = False
    if not t:
        return done("I didn't hear anything. Hold the space bar and speak.", remember=False)
    if t == GUIDE_START or re.fullmatch(r"\W*(?:start over|restart|start again|let'?s start(?: again| over)?|begin again)\W*", low):
        keep = {k: st[k] for k in ("city",) if k in st}
        st.clear()
        st.update(keep, profiles=ctx.get("profiles") or ["wheelchair"], guide="needs")
        st.setdefault("city", ctx.get("city") or "houston")
        return done(GREETING)
    cmd, smart = _understand(t, st, screen)
    intent = cmd.get("intent")

    if intent == "repeat" and st.get("last"):
        return done(st["last"], remember=False)
    if intent == "help" and not st.get("guide") in GUIDE_STAGES:
        return done(HELP)
    if st.get("guide") in GUIDE_STAGES:
        said = _guide(t, low, cmd, st, actions)
        if said is not None:
            return done(said)

    # things said at any time are remembered: needs, city, budget, the qualities wanted
    changed = False
    if cmd["needs"] and set(cmd["needs"]) != set(st["profiles"]):
        st["profiles"], changed = cmd["needs"], True
    if cmd.get("city") and cmd["city"] != st["city"]:
        st["city"], changed = cmd["city"], True
    if cmd.get("max_rent"):
        st["budget"], changed = cmd["max_rent"], True
    if cmd.get("bedrooms"):
        st["bedrooms"], changed = cmd["bedrooms"], True
    if intent == "find_homes":
        # qualities add up over the conversation ("safe", then "and cheaper") unless the person starts over
        fresh = bool(re.search(r"start over|forget (?:that|it)|never ?mind|instead|only\b|new search|something else entirely", low))
        merged = list(dict.fromkeys(([] if fresh else st.get("priorities") or []) + (cmd.get("priorities") or [])))
        if re.search(r"cheaper|less expensive|lower rent", low) and "cheap" not in merged:
            merged.append("cheap")
        changed = changed or merged != (st.get("priorities") or [])
        st["priorities"] = merged
        if fresh:
            for k in ("near", "budget", "bedrooms"):
                if not cmd.get({"near": "near_place", "budget": "max_rent", "bedrooms": "bedrooms"}[k]):
                    st.pop(k, None)
    ps = st["profiles"]
    needs_words = " and ".join(NEED_NAME[p] for p in ps)

    # a home named outright ("show me 404 Avondale Street"), unless they want homes near it
    named = None if re.search(r"\b(near|around|close to|next to|by)\b", low) and intent == "find_homes" else find_named(t, st["city"])
    if named:
        from . import main as api_
        nb = api_.buildings()[named]
        st["city"] = nb["city"]
        st["home"], st["unit"] = named, None
        if not any(h["id"] == named for h in st.get("homes") or []):
            st["homes"] = [{"id": named, "name": nb["name"], "score": min(nb["scores"][p]["score"] for p in st["profiles"])}] + (st.get("homes") or [])[:5]
        said_place = any(re.search(rx, low) for rx in CATS.values())
        if intent in (None, "find_homes", "chitchat", "next_home", "moving") or (intent == "route" and not said_place):
            intent = "about_home"
        actions.append({"type": "highlight_buildings", "ids": [named]})

    # the outside of the home in 3D: its drone model where one exists
    if re.search(r"\b(outside|exterior|drone|from above|aerial view|in 3d|3d model|orbit)\b", low) and not re.search(r"inside", low) and intent != "find_homes":
        hid = st.get("home") or screen.get("selected_building")
        if not hid:
            return done("Which home? Name it, or ask me to find homes first.")
        from . import main as api_
        b_ = api_.buildings()[hid]
        has = any(u["model_id"] in api_._splat_ids() for u in b_["units"])
        actions.append({"type": "open_building", "id": hid})
        actions.append({"type": "open_drone", "id": hid})
        st["home"] = hid
        return done(f"Here is {b_['name']} from outside. " + ("This is its drone model, made from photos taken by a drone circling the home. "
                    if has else "It has no drone model, so this is a 3D block built from aerial heights and photos. ")
                    + "Drag to look around, and zoom far out to go back to the map.")

    # what the person is looking at is part of the conversation: a home they clicked becomes "it"
    home = None if named else _pick_home(cmd, low, st) if intent in ("about_home", "inside_home", "route", "neighborhood", "question", "next_home", None, "chitchat") else None
    if home:
        st["home"], st["unit"] = home, None
    else:
        if screen.get("selected_building") and screen["selected_building"] != st.get("home"):
            st["home"], st["unit"] = screen["selected_building"], None
        if (screen.get("unit") or {}).get("model_id"):
            st["unit"] = screen["unit"]["model_id"]

    # ---- moving
    if intent == "moving":
        if not cmd.get("from_address"):
            return done("What address are you moving from? Say the street address and the city.")
        r = agent._run_tool("match_neighborhoods", {"current_address": cmd["from_address"], "profiles": ps, "budget": st.get("budget")}, actions)
        if r.get("error"):
            return done(r["error"] + " Try the full street address with the city.")
        if not r["neighborhoods"]:
            return done(r.get("message") or "I found no matching neighborhood.")
        st["city"] = "houston" if r["target_city"] == "Houston" else "la"
        st["homes"] = [{"id": h["id"], "name": h["name"], "score": h["score"]} for n in r["neighborhoods"] for h in n["homes"]][:6]
        st["home"], st["unit"] = None, None
        lines = [f"{i + 1}. {n['name']}, match {n['match']}. {n['reason']}" for i, n in enumerate(r["neighborhoods"])]
        return done(f"For {needs_words} needs, these {r['target_city']} neighborhoods have everyday places about as close as where you live now. "
                    + " ".join(lines) + f" The best home there is {st['homes'][0]['name']}. Say tell me about the first one, or ask about the inside or a route.")

    if intent == "next_home" and st.get("homes"):
        ids = [h["id"] for h in st["homes"]]
        st["home"], st["unit"] = ids[(ids.index(st["home"]) + 1) % len(ids)] if st.get("home") in ids else ids[0], None
        intent = "about_home"

    # ---- a route from the current home
    if intent == "route":
        if not st.get("home"):
            return done("Which home should I start from? Ask me to find homes first, or pick one on the map.")
        cat = cmd.get("destination")
        if not cat:
            return done("Where to? I can route to a pharmacy, clinic, grocery, bus or rail stop, hospital, school, park or restaurants.")
        try:
            r = api.routes(st["home"], cat, ps[0])
        except Exception as e:  # noqa: BLE001
            return done(f"I couldn't work out that route: {getattr(e, 'detail', e)}")
        actions.append({"type": "show_route", "building_id": st["home"], "category": cat, "profile": ps[0]})
        return done(narrate.route_narration(r) + " Ask for another place, or about the inside.")

    # ---- inside the current home
    if intent == "inside_home":
        if not (st.get("home") or st.get("unit")):
            return done("Which home? Ask me to find homes first, or pick one on the map.")
        unit_id = st.get("unit") or _best_unit(st["home"], ps)
        if not unit_id:
            return done("No unit in that home has a measured floor plan, so I can't describe the inside. Say next for another home.")
        st["unit"] = unit_id
        n = narrate.unit_narration(api.unit_heatmap(unit_id, ps[0]), ps[0], ps)
        sec = n["sections"]
        actions.append({"type": "open_unit", "model_id": unit_id, "profile": ps[0]})
        topic = cmd.get("topic")
        if not topic:
            topic = ("light" if re.search(r"window|sun|light|bright|view", low) else "fixtures" if re.search(r"counter|sink|vanity|toilet|tub|shower|island|built.?in|how high|height", low) else
                     "doors" if re.search(r"door|wide|width", low) else
                     "problems" if re.search(r"problem|issue|barrier|wrong", low) else "size" if re.search(r"how (?:big|large)|size|square", low) else
                     "paths" if re.search(r"get to|reach|path|directions", low) else None)
        keys = {"light": ["light"], "doors": ["doors"], "fixtures": ["fixtures"], "problems": ["problems"], "size": ["overview", "rooms"], "paths": ["layout", "paths"]}.get(
            topic, ["overview", "layout"] + (["paths"] if "blind" in ps else []) + ["problems"])
        said = " ".join(sec[k] for k in keys if k in sec)
        more = [w for k, w in (("light", "the light"), ("rooms", "room sizes"), ("doors", "doorways"), ("paths", "directions inside"), ("problems", "problems")) if k in sec and k not in keys]
        return done(said + (f" You can ask about {narrate._join(more)}." if more else "") + " Or ask for a route, the neighborhood, or say next.")

    # ---- the neighborhood around the current home
    if intent == "neighborhood" and st.get("home"):
        b = api.building(st["home"], ",".join(ps))
        loc = b.get("locality_detail")
        if not loc:
            return done("I have no neighborhood data around that home.")
        score = min(v["score"] for v in loc["scores"].values())
        near = ", ".join(f"{n['label'].lower()} {n['minutes'][ps[0]]:.0f} minutes" for n in b["nearest"] if n["minutes"].get(ps[0]) is not None)
        s = loc.get("streets") or {}
        streets = (f" Sidewalks are mapped on about {s['sidewalk_coverage'] * 100:.0f} percent of streets"
                   + (f", and {s['signalized_share'] * 100:.0f} percent of crossings have signals." if s.get("signalized_share") is not None else ".")) if s else " Street details aren't recorded for this area."
        actions.append({"type": "set_map_view", "center": [b["lon"], b["lat"]], "zoom": 15.6})
        return done(f"The neighborhood around {b['name']} scores {score} for {needs_words} needs. Times are {'routed' if loc.get('live') else 'estimated'}: {near}.{streets}"
                    " Ask for a route to any of these.")

    # ---- tell me about a home
    if intent == "about_home" and st.get("home"):
        b = api.building(st["home"], ",".join(ps))
        actions.append({"type": "open_building", "id": st["home"]})
        actions.append({"type": "set_map_view", "center": [b["lon"], b["lat"]], "zoom": 17.2})
        actions.append({"type": "highlight_buildings", "ids": [st["home"]]})
        drone = " It has a drone model: say show me the outside." if any(u.get("splat") for u in b["units"]) else ""
        text, uid = _pitch(st)
        st["unit"] = uid
        if uid and st.get("guide") in (None, "done", "pick"):
            st["guide"] = "tour"           # the same broker's next step: offer to walk them through it
            return done(text + drone + " Shall I walk you through it, starting at the front door?")
        return done(text + drone + " Ask what it is like inside, or for a route to the pharmacy, a clinic, the bus, or a grocery.")

    # ---- a question: answer from what is on screen and in the conversation
    if intent in ("question", "chitchat", "about_home", "neighborhood", None) and not (intent == "find_homes"):
        shown = dict(screen)
        if st.get("home") and not shown.get("selected_building"):
            shown["selected_building"] = st["home"]
        if not shown.get("unit"):
            # questions about the inside of the current home are answered from its best measured unit
            uid = st.get("unit") or (_best_unit(shown["selected_building"], ps) if shown.get("selected_building") else None)
            if uid:
                shown["unit"] = {"model_id": uid, "profile": ps[0]}
        found = screen_qa.answer(t, shown, ps, top=8)
        if found:
            worded = brain.answer(t, found["all"]) if smart else None
            return done(worded or found["text"])
        if intent == "chitchat" or not (shown.get("selected_building") or st.get("homes")):
            if re.search(r"\b(hi|hello|hey|good (?:morning|afternoon|evening))\b", low):
                return done("Hello. " + HELP)
            if re.search(r"thank", low):
                return done("You're welcome. Ask me anything else about these homes.", remember=False)
            return done("I can help you find a home and understand it. " + HELP)
        return done("I don't have that in what's on screen or in what I've measured. I can tell you about scores, rent, the building, doorways, rooms, light, the route, "
                    "the neighborhood, and what the colors mean.")

    # ---- find homes
    near, center, km = None, None, 3.0
    if re.search(r"near me|around me|close to me|where i am", low) and ctx.get("location"):
        from pipeline.buildings import city_of
        center, near, km = (ctx["location"]["lat"], ctx["location"]["lon"]), "you", 2.0
        c = city_of(*center)
        if not c:
            return done("You seem to be outside Houston and Los Angeles, the two cities I cover.")
        st["city"] = c
    else:
        place = cmd.get("near_place") or (st.get("near") if intent != "find_homes" or not cmd.get("priorities") else st.get("near"))
        if cmd.get("near_place") is None and intent == "find_homes" and re.search(r"anywhere|whole city|any area|all of", low):
            place = None
        if place:
            c = agent.PLACES.get(place.lower().strip())
            if not c:
                bid = find_named(place, st["city"])
                if bid:
                    bb = api.buildings()[bid]
                    c = (bb["lat"], bb["lon"])
            if not c:
                from pipeline.relocate import geocode
                g = geocode(place + (", Houston, TX" if st["city"] == "houston" else ", Los Angeles, CA"))
                c = (g["lat"], g["lon"]) if g else None
            if c:
                center, near = c, place
                st["near"] = place
            elif cmd.get("near_place"):
                # not a place we can find: search the whole city rather than stop the conversation
                st.pop("near", None)
    homes, notes = _rank(st, ps, center, km)
    if not homes:
        return done(f"I found no scanned homes{' near ' + near if near else ''}{' under ' + format(st['budget'], ',') + ' dollars' if st.get('budget') else ''}"
                    f"{' with ' + str(st['bedrooms']) + ' bedrooms' if st.get('bedrooms') else ''}. Try a higher budget or a wider area.")
    st["homes"] = [{"id": h["id"], "name": h["name"], "score": h["score"]} for h in homes]
    st["home"], st["unit"] = None, None
    actions.append({"type": "set_map_view", "center": [sum(h["lon"] for h in homes[:3]) / len(homes[:3]), sum(h["lat"] for h in homes[:3]) / len(homes[:3])],
                    "zoom": 14 if center else 12.5, "profiles": ps, "city": st["city"]})
    actions.append({"type": "highlight_buildings", "ids": [h["id"] for h in homes]})
    lines = []
    for i, h in enumerate(homes[:3]):
        line = f"{i + 1}. {h['name']}, score {h['score']}. {h['access'][0].upper() + h['access'][1:]}"
        if h["why"]:
            line += "; " + "; ".join(h["why"][:2])
        if h.get("rent"):
            line += f"; from {h['rent']:,} dollars a month"
        lines.append(line + ("." if h["inside"] else ". The inside isn't measured yet."))
    ranked = [PRIORITY_WORD[p] for p in st.get("priorities") or [] if p in PRIORITY_WORD]
    where = f" near {near}" if near else f" in {'Houston' if st['city'] == 'houston' else 'Los Angeles'}"
    budget = f" under {st['budget']:,} dollars" if st.get("budget") else ""
    beds = f" with {st['bedrooms']} bedrooms" if st.get("bedrooms") else ""
    return done(f"For {needs_words} needs{where}{budget}{beds}, I found {homes[0]['n']:,} homes"
                + (f", and ranked them for {narrate._join(ranked)}" if ranked else "") + ". "
                + (" ".join(notes) + " " if notes else "")
                + f"The top {len(lines)}: " + " ".join(lines)
                + " These are estimates from 3D scans and maps, not a certification. Ask about any of them: say the first one, or ask what it is like inside.")
