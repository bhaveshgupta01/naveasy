"""The guided conversation: the agent leads (needs, budget, area, a numbered list, a tour, furniture), and every
number it says comes from the data it was handed."""
import pytest

from backend.app import brain, dialogue, narrate
from backend.app import main as api
from pipeline.unit import analyze, load_plan, unit_json

from test_unit_engine import make_model

BANNED = ("accessible", "compliant", "compliance")


def _unit(monkeypatch):
    monkeypatch.setattr(narrate, "scan_orientation", lambda _id: {})
    plan = load_plan("test", make_model(bath_door=0.95))
    u = unit_json(plan, analyze(plan), "wheelchair")
    u["has_graph"] = True
    return u


def test_furniture_is_heard_as_planner_pieces():
    assert dialogue.parse_furniture("a queen bed, two nightstands and a couch") == ["queen bed", "nightstand", "nightstand", "sofa"]
    assert dialogue.parse_furniture("I have a desk, a bookcase, a TV and a kitchen table") == ["desk", "bookshelf", "tv stand", "dining table"]
    assert dialogue.parse_furniture("just my bed and a coffee table") == ["queen bed", "coffee table"]
    assert dialogue.parse_furniture("no thanks") == []


def test_budgets_said_out_loud():
    assert dialogue._words_number("two thousand") == 2000
    assert dialogue._words_number("about 1,900 a month") == 1900
    assert dialogue._words_number("2.5k") == 2500
    assert dialogue._words_number("whatever") is None


def test_plan_is_described_from_each_rooms_door(monkeypatch):
    u = _unit(monkeypatch)
    bedroom = next(r for r in u["rooms"] if r["type"] == "Bedroom")    # x 5..8, y 2.5..6; its door is on the x = 5 wall
    plan = {"placements": [
        {"label": "queen bed", "room": bedroom["id"], "rect": [5.8, 3.95, 1.52, 2.03], "facing": [0, -1]},   # back to the north wall
        {"label": "desk", "room": bedroom["id"], "rect": [7.3, 2.7, 0.6, 1.2], "facing": [-1, 0]}],          # back to the east wall
        "wont_fit": [{"label": "piano", "reason": "No spot for a 1.50 x 0.65 m piano keeps every room reachable and the doors clear."}],
        "score_after": 91, "ordinary_min": 48, "ordinary_max": 80, "reasons": [], "problems": []}
    said = narrate.plan_narration(plan, u)["text"]
    assert "In the bedroom, coming in through its door." in said
    assert "Your queen bed goes against the left wall" in said          # walking in facing east, north is on the left
    assert "Your desk goes against the far wall" in said
    assert "piano won't fit" in said and "scores 91" in said and "48 to 80" in said


def test_broker_pitch_states_the_weakness_and_what_to_verify(monkeypatch):
    u = _unit(monkeypatch)
    b = {"name": "Test Lofts", "address": "1 Main St, Houston, TX 770021234", "rent_min": 1500, "rent_max": 2100, "walkup": True, "stories": 3,
         "combined": {"score": 62, "confidence": "medium"},
         "components": {"vertical_access": {"value": 0.2, "why": "walk-up, 3 stories"}, "age": {"value": 0.0, "why": "built 1978, before 1991"}},
         "nearest": [{"category": "pharmacy", "label": "Pharmacy", "minutes": {"wheelchair": 7.6}, "estimated": False}],
         "checklist": ["Ask which floor the unit is on.", "We did not check: grab bars."]}
    said = narrate.broker_pitch(b, u, ["wheelchair"])
    assert "1,500 dollars a month" in said and "up to 2,100" in said and "Houston 77002" in said
    assert "pharmacy 8 minutes" in said
    assert "The main weakness: the building is a walk-up" in said
    assert "Before you sign: Ask which floor" in said and "grab bars" not in said
    assert not any(w in said.lower() for w in BANNED)


def test_home_list_asks_for_a_number():
    said = narrate.home_list([{"name": "A", "rent": 1200, "score": 80, "inside": True}, {"name": "B", "rent": None, "score": 70, "inside": False}], 12)
    assert said.startswith("I found 12 homes. Here are the best 2. One: A, from 1,200 dollars a month, scores 80.")
    assert "Two: B, rent not listed, scores 70, inside not measured yet." in said and said.endswith("one or two?")


@pytest.fixture
def offline(monkeypatch):
    """The conversation with every data lookup replaced, and no language model, so it runs anywhere."""
    monkeypatch.setattr(brain, "parse", lambda *a, **k: None)
    homes = [{"id": f"b{i}", "name": n, "score": s, "rent": r, "inside": True, "lat": 29.74, "lon": -95.38, "n": 3}
             for i, (n, s, r) in enumerate([("The Rice", 84, 1600), ("Calais Midtown", 82, 1400), ("Elan Med Center", 75, 1750)])]
    seen = {}

    def search(st, actions):
        seen["search"] = dict(st)
        return homes, 3, ""
    monkeypatch.setattr(dialogue, "_search", search)
    monkeypatch.setattr(dialogue, "_pitch", lambda st: (f"Pitch for {st['home']}.", "u1"))
    monkeypatch.setattr(api, "buildings", lambda: {h["id"]: {"lon": h["lon"], "lat": h["lat"]} for h in homes})
    monkeypatch.setattr(api, "building", lambda *a, **k: {"checklist": ["Ask which floor the unit is on."]})
    monkeypatch.setattr(api, "unit_heatmap", lambda *a, **k: {"model_id": "u1"})
    monkeypatch.setattr(narrate, "unit_narration", lambda *a, **k: {"sections": {"layout": "You come in through a door about 36 in wide.", "problems": "Nothing to report."}})
    import pipeline.furnish as F
    monkeypatch.setattr(F, "plan_furnishing", lambda mid, inv, ps, prefs: {"placements": [], "wont_fit": [], "score_after": 88, "reasons": [], "problems": [], "inventory": inv})
    monkeypatch.setattr(narrate, "plan_narration", lambda plan, u, ps=None: {"text": f"Placed {len(plan['inventory'])} pieces."})
    return seen


def _talk(lines):
    st, out = {}, []
    for t in lines:
        r = dialogue.respond(t, st, {"city": "houston", "profiles": ["wheelchair"], "channel": "voice"})
        st = r["state"]
        out.append(r)
    return out, st


def test_the_agent_leads_a_blind_listener_to_a_furnished_home(offline):
    out, st = _talk(["__start__", "I'm blind", "about 1800", "near Midtown", "two", "yes", "a queen bed, a nightstand and a sofa"])
    said = [r["text"] for r in out]
    assert "what help do you need" in said[0].lower()
    assert "budget" in said[1].lower() and "blind or low vision" in said[1]
    assert "where" in said[2].lower() and "1,800" in said[2]
    assert "One: The Rice" in said[3] and "Two: Calais Midtown" in said[3] and said[3].endswith("one, two or three?")
    assert offline["search"]["profiles"] == ["blind"] and offline["search"]["budget"] == 1800 and offline["search"]["near"] == "Midtown"
    assert "Pitch for b1." in said[4] and "front door" in said[4]
    assert "36 inches wide" in said[5] and "furniture" in said[5]
    assert "Placed 3 pieces." in said[6] and "Before you sign: Ask which floor" in said[6]
    assert st["guide"] == "done"
    assert not any(w in t.lower() for t in said for w in BANNED)
    kinds = [[a["type"] for a in r["ui_actions"]] for r in out]
    assert "highlight_buildings" in kinds[3] and "open_building" in kinds[4] and "open_unit" in kinds[5]
    plan_action = next(a for a in out[6]["ui_actions"] if a["type"] == "open_unit")
    assert plan_action["tab"] == "plan" and plan_action["plan"]["score_after"] == 88
    # the map follows the list as it is read: one cued fly-to per home, each cue found in the spoken text
    cues = [a["cue"] for a in out[3]["ui_actions"] if a.get("cue")]
    assert cues == ["One: The Rice", "Two: Calais Midtown", "Three: Elan Med Center"] and all(c in said[3] for c in cues)


def test_everything_said_at_once_skips_the_questions(offline):
    out, st = _talk(["__start__", "I use a wheelchair, my budget is $2,000 and I want to be near Midtown"])
    assert "One: The Rice" in out[1]["text"] and st["guide"] == "pick"
    assert offline["search"]["budget"] == 2000 and offline["search"]["profiles"] == ["wheelchair"]


def test_no_particular_need_and_no_budget(offline):
    out, st = _talk(["__start__", "none", "any", "anywhere"])
    assert st["general"] and "budget" not in offline["search"] and st["guide"] == "pick"
    assert "no special needs" in out[1]["text"] and "any budget" in out[2]["text"]


def test_a_question_mid_guide_is_answered_not_swallowed(offline):
    st = {"guide": "budget", "profiles": ["wheelchair"], "needs_known": True, "city": "houston"}
    cmd = dialogue._rules("how wide is the bathroom door?", st)
    assert dialogue._guide("how wide is the bathroom door?", "how wide is the bathroom door?", cmd, st, []) is None
    assert st["guide"] == "budget"


@pytest.fixture
def venue_stub(monkeypatch):
    """The venue's measurements replaced by small fixed answers, so the conversation can be checked offline."""
    import pipeline.venue as V
    calls = []
    monkeypatch.setattr(brain, "parse", lambda *a, **k: None)
    monkeypatch.setattr(dialogue, "find_named", lambda *a, **k: None)
    monkeypatch.setattr(V, "venue_summary", lambda *a, **k: {"lat": 40.349, "lon": -74.655})
    monkeypatch.setattr(V, "inside_route", lambda a, b, p, *x, **k: calls.append(("route", a, b)) or {"spoken": f"From the {a} to the {b}: go straight about 2 meters."})
    monkeypatch.setattr(V, "door_widths", lambda near=None, **k: calls.append(("doors", near)) or {"doors": [
        {"floor_label": "Floor 3", "rooms": ["Classroom", "Landing"], "clear_in": 64.0, "status": "pass", "entrance": False, "evidence": {"checked": False}},
        {"floor_label": "Floor 2", "rooms": ["Hallway", "Other"], "clear_in": 67.0, "status": "pass", "entrance": True, "evidence": {"checked": False}}], "artefacts": [{}]})
    monkeypatch.setattr(V, "stairs", lambda *a, **k: calls.append(("stairs",)) or {"floors": [{"label": "Floor 2", "height_m": 0.0}, {"label": "Floor 3", "height_m": 3.97}],
                                                                                 "stairs": [{"floor": "Floor 2", "area_m2": 7.8}], "elevator": "not in the scan; needs a site visit", "raised_parts": []})
    monkeypatch.setattr(V, "outside_route", lambda origin, p: calls.append(("outside", origin)) or {"from": "Princeton station (the Dinky)", "to": "Julis Romo Rabinowitz Building",
                        "profile": p, "minutes": 19.1, "meters": 1201, "crossings": 2, "hard_spots": ["Slope of about 11% for 44 m."], "notes": [], "caveat": "Which door is step-free needs a site visit.",
                        "route": {"stretches": []}})
    return calls


def test_the_venue_answers_from_its_measurements(venue_stub):
    out, st = _talk(["How do I get from the entrance to the classroom in a wheelchair?", "How wide is the classroom door?", "Where are the stairs?", "How do I get here from the Dinky?"])
    said = [r["text"] for r in out]
    assert ("route", "entrance", "classroom") in venue_stub and "go straight about 2 meters" in said[0]
    assert ("doors", "classroom") in venue_stub and "about 64 inches clear" in said[1] and "not yet checked against a tape measure" in said[1]
    assert "Elevator: not in the scan; needs a site visit." in said[2]
    assert ("outside", "dinky") in venue_stub and "about 19 minutes" in said[3] and "11 percent for 44 meters" in said[3]
    assert any(a["type"] == "draw_route" for a in out[3]["ui_actions"]) and st["home"] == "princeton_rabinowitz"
    assert not any(w in t.lower() for t in said for w in BANNED)


def test_units_are_spoken_in_words():
    assert narrate.speakable("about 1 meters, a step of 14 cm, 44 m of slope, 64 m2") == "about 1 meter, a step of 14 centimeters, 44 meters of slope, 64 square meters"


def test_the_3d_tour_walks_along_with_the_voice(offline, monkeypatch):
    walk = {"steps": [{"text": "We start at the front door, about 36 inches wide, facing in.", "x": 0.9, "y": 3.0, "look": [2.9, 3.0]},
                      {"text": "About 4 steps ahead, the bedroom, about 11 by 10 feet.", "x": 6.5, "y": 4.2, "look": [8.0, 6.0]},
                      {"text": "That's the whole floor.", "x": None, "y": None, "look": None}]}
    monkeypatch.setattr(narrate, "walk_tour", lambda *a, **k: walk)
    out, st = _talk(["__start__", "I'm blind", "any", "near Midtown", "one", "yes"])
    r = out[-1]
    assert r["text"].startswith("We start at the front door") and "furniture" in r["text"]
    opened = next(a for a in r["ui_actions"] if a["type"] == "open_unit")
    assert opened["tab"] == "tour"
    moves = [a for a in r["ui_actions"] if a["type"] == "tour_goto"]
    assert [(m["x"], m["y"]) for m in moves] == [(0.9, 3.0), (6.5, 4.2)]           # no move for the closing line
    assert all(m["cue"].lower() in r["text"].lower() for m in moves)               # each move runs as its sentence is spoken
