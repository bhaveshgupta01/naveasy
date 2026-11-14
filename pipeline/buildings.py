"""CoStar kit -> data/buildings.json (the building table, Technical Design sections 7 and 11).

    python -m pipeline.buildings

One row per apartment community (commercial property with propertyApartment) and per home
(residential listing, deduplicated by ResidentialListingId). Each row carries its Matterport
model ids with captions, and a building-level score per profile with its components.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict

import h3

from .clients import ROOT

KIT = ROOT / "costar-kit"
OUT = ROOT / "data"
PROFILES = ("wheelchair", "blind", "deaf", "older")

# Section 7 weights: vertical access, approach, listing evidence, age
WEIGHTS = {
    "wheelchair": (0.40, 0.30, 0.15, 0.15),
    "blind": (0.20, 0.20, 0.20, 0.40),
    "deaf": (0.05, 0.10, 0.35, 0.50),
    "older": (0.35, 0.30, 0.15, 0.20),
}
BASE_CONF = {"wheelchair": "medium", "blind": "low", "deaf": "low", "older": "medium"}
ACCESS_WORDS = re.compile(r"accessib|handicap|wheelchair|\bramp|grab bar|roll-in|ada\b|elevator|zero.?step|no.?step", re.I)
DEAF_WORDS = re.compile(r"video intercom|visual alarm|strobe|doorbell camera|video doorbell", re.I)


def city_of(lat: float, lon: float) -> str | None:
    if 28.8 < lat < 30.8 and -96.6 < lon < -94.3:
        return "houston"
    if 33.2 < lat < 34.9 and -119.4 < lon < -117.0:
        return "la"
    return None


def jsonl(name: str):
    with open(KIT / "data" / f"{name}.jsonl") as f:
        for line in f:
            yield json.loads(line)


def vertical_access(stories, walkup, elevators, home=False) -> tuple[float, str, bool]:
    """Share of the building a person who can't use stairs can reach. Returns (value, why, known)."""
    s = stories or 0
    if elevators:
        return 1.0, f"{elevators} passenger elevator{'s' if elevators > 1 else ''}", True
    if s == 1:
        return 1.0, "single story", True
    if home:
        if not s:
            return 0.5, "stories not recorded", False
        return (0.45 if s == 2 else 0.25), f"{s} stories, no elevator recorded", True
    if walkup is True:
        return round(max(0.2, 1 / s), 2) if s else 0.4, f"walk-up{f', {s} stories' if s else ''}: only ground-floor units are step-free", True
    if walkup is False:
        return 0.85, "not a walk-up (elevator count not recorded)", True
    if s >= 5:
        return 0.7, f"{s} stories; elevator likely but not recorded", False
    if not s:
        return 0.5, "stories and elevator not recorded", False
    return round(max(0.3, 1 / s), 2), f"{s} stories, elevator not recorded", False


def age_component(year) -> tuple[float, str]:
    if not year:
        return 0.5, "construction year not recorded"
    if year >= 1991:
        return 1.0, f"built {year}, after the 1991 Fair Housing design rules"
    return 0.3, f"built {year}, before the 1991 Fair Housing design rules"


def score_row(vert, approach, evidence, deaf_evidence, age, known: bool) -> dict:
    out = {}
    for p in PROFILES:
        w = WEIGHTS[p]
        ev = deaf_evidence if p == "deaf" else evidence
        val = w[0] * vert + w[1] * approach + w[2] * ev + w[3] * age
        conf = BASE_CONF[p] if known else "low"
        out[p] = {"score": round(100 * val), "confidence": conf}
    return out


def build() -> list[dict]:
    models_by_prop = defaultdict(list)
    with open(KIT / "commercial-properties.csv") as f:
        for r in csv.DictReader(f):
            if r["MatterportModelId"]:
                models_by_prop[int(r["PropertyId"])].append(r["MatterportModelId"])
    models_by_listing = defaultdict(list)
    with open(KIT / "residential-listings.csv") as f:
        for r in csv.DictReader(f):
            if r["MatterportModelId"]:
                models_by_listing[int(r["ListingId"])].append(r["MatterportModelId"])

    rents = {}
    for a in jsonl("apartment-listing"):
        ms = [m for m in a.get("apartmentModels") or [] if m.get("minRent")]
        if ms:
            rents[int(a["propertyId"])] = {
                "rent_min": int(min(m["minRent"] for m in ms)),
                "rent_max": int(max(m.get("maxRent") or m["minRent"] for m in ms)),
                "units_available": sum(m.get("numUnitsAvailable") or 0 for m in ms),
                "photo": (a.get("primaryPhoto") or {}).get("attachmentUri"),
            }

    feature_text = {}
    for f_ in jsonl("residential-listing-features"):
        feature_text[int(f_["id"])] = json.dumps(f_)

    rows = []
    for p in jsonl("commercial-property"):
        pa = p.get("propertyApartment")
        addr = p.get("primaryAddress") or {}
        lat, lon = addr.get("latitude"), addr.get("longitude")
        if not pa or lat is None:
            continue
        city = city_of(lat, lon)
        if not city:
            continue
        pid = int(p["id"])
        captions = {}
        def take(atts):
            for a in atts or []:
                if a.get("videoKey") and a.get("caption"):
                    captions.setdefault(a["videoKey"], a["caption"])
        take(p.get("attachments"))
        for m in pa.get("models") or []:
            take(m.get("attachments"))
            for u in m.get("units") or []:
                take(u.get("attachments"))
        for u in pa.get("units") or []:
            take(u.get("attachments"))
        model_ids = list(dict.fromkeys(models_by_prop.get(pid, [])))
        elevators = sum(e.get("numElevators") or 0 for e in pa.get("elevators") or [] if e.get("elevatorType") == 1)
        years = [e["year"] for e in p.get("constructionEvents") or [] if e.get("eventType") == 2 and e.get("year")]
        year = min(years) if years else None
        stories = p.get("numStoriesAboveGrade")
        walkup = pa.get("isWalkup")
        vert, vwhy, known = vertical_access(stories, walkup, elevators)
        age, awhy = age_component(year)
        rent = rents.get(pid, {})
        street = " ".join(str(addr.get(k)) for k in ("streetNumber", "streetDirection", "streetName", "streetType") if addr.get(k))
        rows.append({
            "id": f"p{pid}", "kind": "apartment_community", "costar_id": pid,
            "name": p.get("buildingName") or street, "address": f"{street}, {addr.get('city')}, {addr.get('state')} {addr.get('postcode') or ''}".strip(),
            "city": city, "lat": lat, "lon": lon, "h3": h3.latlng_to_cell(lat, lon, 9),
            "stories": stories, "elevator": elevators > 0, "elevators": elevators, "walkup": walkup, "year_built": year,
            "total_units": pa.get("totalNumUnits"),
            "rent_min": rent.get("rent_min"), "rent_max": rent.get("rent_max"), "units_available": rent.get("units_available"),
            "photo": rent.get("photo") or (p.get("primaryPhoto") or {}).get("attachmentUri"),
            "url": p.get("propertyWebsiteUrl"),
            "units": [{"model_id": m, "caption": captions.get(m)} for m in model_ids],
            "units_scanned": len(model_ids),
            "components": {
                "vertical_access": {"value": vert, "why": vwhy},
                "approach": {"value": 0.5, "why": "entrance and parking not checked yet"},
                "listing_evidence": {"value": 0.3, "why": "no accessibility statement in the listing data"},
                "age": {"value": age, "why": awhy},
            },
            "scores": score_row(vert, 0.5, 0.3, 0.2, age, known and year is not None),
        })

    seen = set()
    for l in jsonl("residential-listings"):
        lid = int(l["id"])
        group = l.get("residentialListingId") or lid
        prop = l.get("property") or {}
        addr = prop.get("address") or {}
        lat, lon = addr.get("latitude"), addr.get("longitude")
        model_ids = list(dict.fromkeys(models_by_listing.get(lid, [])))
        if lat is None or group in seen or not model_ids:
            continue
        city = city_of(lat, lon)
        if not city:
            continue
        seen.add(group)
        reso = l.get("resoValues") or {}
        struct = reso.get("structure") or {}
        stories = struct.get("stories")
        stories = int(stories) if stories else None
        year = prop.get("yearBuilt") or struct.get("yearBuilt")
        has_elev = bool(reso.get("hasElevator"))
        text = (l.get("remarks") or "") + " " + feature_text.get(lid, "")
        hits = sorted({m.group(0).lower() for m in ACCESS_WORDS.finditer(text)})
        flag = bool(reso.get("hasAccessibilityFeatures"))
        evidence = 1.0 if flag else (0.7 if hits else 0.2)
        ewhy = ("listing flags accessibility features" if flag else
                f"listing text mentions: {', '.join(hits[:4])}" if hits else "listing doesn't mention accessibility")
        deaf_ev = 0.8 if DEAF_WORDS.search(text) else 0.2
        vert, vwhy, known = vertical_access(stories, None, 1 if has_elev else 0, home=True)
        if has_elev:
            vwhy = "listing reports an elevator"
        age, awhy = age_component(year)
        rental = l.get("transactionType") == 2
        price = l.get("currentPrice")
        neighborhood = next((a.get("name") for a in addr.get("areas") or [] if a.get("enterpriseEntityType") == 5 and a.get("name")), None)
        rows.append({
            "id": f"h{lid}", "kind": "home", "costar_id": lid,
            "name": addr.get("fullStreetAddress") or f"Home {lid}",
            "address": f"{addr.get('fullStreetAddress') or ''}, {addr.get('city')}, {addr.get('state')} {addr.get('zip') or ''}".strip(", "),
            "city": city, "lat": lat, "lon": lon, "h3": h3.latlng_to_cell(lat, lon, 9), "neighborhood": neighborhood,
            "stories": stories, "elevator": has_elev, "elevators": 1 if has_elev else 0, "walkup": None, "year_built": year,
            "beds": prop.get("bedsMax"), "baths": prop.get("bathsMax"), "sqft": prop.get("sqftMax"),
            "rent_min": int(price) if rental and price else None, "rent_max": int(price) if rental and price else None,
            "price": int(price) if price and not rental else None,
            "photo": next((m.get("uri") for m in l.get("media") or [] if "imagescdn" in (m.get("uri") or "") and not m.get("videoKey")), None),
            "url": f"https://www.homes.com/property/{prop['url']}" if prop.get("url") else None,
            "units": [{"model_id": m, "caption": " · ".join(x for x in (
                f"{prop.get('bedsMax')}BR" if prop.get("bedsMax") else None,
                f"{prop.get('bathsMax')}BA" if prop.get("bathsMax") else None,
                f"{prop.get('sqftMax'):,}SF" if prop.get("sqftMax") else None) if x)} for m in model_ids],
            "units_scanned": len(model_ids),
            "components": {
                "vertical_access": {"value": vert, "why": vwhy},
                "approach": {"value": 0.5, "why": "entrance and parking not checked yet"},
                "listing_evidence": {"value": evidence, "why": ewhy},
                "age": {"value": age, "why": awhy},
            },
            "scores": score_row(vert, 0.5, evidence, deaf_ev, age, known and year is not None),
        })
    return rows


if __name__ == "__main__":
    rows = build()
    OUT.mkdir(exist_ok=True)
    (OUT / "buildings.json").write_text(json.dumps(rows))
    from collections import Counter
    print(Counter((r["city"], r["kind"]) for r in rows))
    print("models:", Counter(r["city"] for r in rows for _ in r["units"]))
    hou = [r for r in rows if r["city"] == "houston"]
    print("houston wheelchair score buckets:", sorted(Counter(r["scores"]["wheelchair"]["score"] // 10 * 10 for r in hou).items()))
