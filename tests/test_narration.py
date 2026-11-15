"""The spoken description is built only from measured geometry, and the conversation keeps its thread."""
from backend.app import narrate
from pipeline.unit import analyze, load_plan, unit_json

from test_unit_engine import make_model


def _unit(monkeypatch, offset=0.0, **kw):
    monkeypatch.setattr(narrate, "scan_orientation", lambda _id: {"offset_deg": offset} if offset is not None else {})
    model = make_model(**kw)
    # a 2 m window on the bedroom's east wall (edge 3 runs up the x = 8 side)
    model["floors"][0]["edges"][3]["openings"] = [{"type": "window", "label": None, "width": 2.0, "height": 1.2, "lowerElevation": 0.9, "relativeCenter": 0.5}]
    plan = load_plan("test", model)
    u = unit_json(plan, analyze(plan), "wheelchair")
    u["has_graph"] = True
    return u


def test_layout_is_described_from_the_entry(monkeypatch):
    n = narrate.unit_narration(_unit(monkeypatch), "wheelchair")
    s = n["sections"]
    assert "517 square feet" in s["overview"] and "a bedroom" in s["overview"]
    assert "about 35 inches wide" in s["layout"] and "living room" in s["layout"]
    assert "bedroom" in s["layout"] and "bathroom" in s["layout"]
    assert "To the bathroom" in s["paths"]


def test_window_direction_and_sun_follow_the_compass(monkeypatch):
    east = narrate.unit_narration(_unit(monkeypatch, offset=0.0), "wheelchair")["sections"]["light"]
    assert "facing east" in east and "morning sun" in east
    turned = narrate.unit_narration(_unit(monkeypatch, offset=90.0), "wheelchair")["sections"]["light"]
    assert "facing south" in turned and "most of the day" in turned


def test_no_compass_means_no_sun_claim(monkeypatch):
    light = narrate.unit_narration(_unit(monkeypatch, offset=None), "wheelchair")["sections"]["light"]
    assert "window" in light and "sun" not in light.split("The scan isn't aligned")[0]
    assert "can't say which way" in light


def test_problems_are_spoken_in_words(monkeypatch):
    s = narrate.unit_narration(_unit(monkeypatch, bath_door=0.70), "wheelchair")["sections"]
    assert "inches clear" in s["problems"] and " in clear" not in s["problems"]
    assert "narrowest is about 26 inches" in s["doors"]


def _item(kind, room_type, h_m, w_m, d_m, room="r9", **kw):
    return {"id": "fx0_1", "kind": kind, "floor": 0, "room": room, "room_type": room_type, "x": 1.0, "y": 1.0,
            "width_m": w_m, "depth_m": d_m, "height_m": h_m, "width_in": round(w_m / 0.0254), "depth_in": round(d_m / 0.0254),
            "height_in": round(h_m / 0.0254, 1), "area_m2": round(w_m * d_m, 2), "shape": "straight", "possibly_movable": False, **kw}


def test_built_ins_are_spoken_with_measured_heights(monkeypatch):
    u = _unit(monkeypatch)
    u["fixture_items"] = [_item("counter", "Kitchen", 0.914, 2.4, 0.62), _item("toilet", "Bathroom", 0.43, 0.4, 0.45, room="r1"),
                          _item("toilet tank", "Bathroom", 0.76, 0.5, 0.22, room="r1")]
    s = narrate.unit_narration(u, "wheelchair")["sections"]
    assert "counter about 36 inches high, 8 feet long" in s["fixtures"] and "seat about 17 inches high" in s["fixtures"]
    assert "tank" not in s["fixtures"] and "34 inches or lower" in s["fixtures"]
    assert "measured from the scan's 3D mesh" in s["caveat"] and "Grab bars" in s["caveat"]


def test_no_mesh_says_fixtures_were_not_measured(monkeypatch):
    s = narrate.unit_narration(_unit(monkeypatch), "wheelchair")["sections"]
    assert "fixtures" not in s and "aren't measured" in s["caveat"]
    assert narrate.spoken_inches("about 36 in high; 34 in or lower (1.4 in)") == "about 36 inches high; 34 inches or lower (1.4 inches)"


def test_relative_directions():
    assert narrate._relative((0, 1), (0, 5)) == "straight ahead"
    assert narrate._relative((0, 1), (-5, 0)) == "to your left"
    assert narrate._relative((0, 1), (5, 0)) == "to your right"
    assert narrate._relative((0, 1), (0, -5)) == "behind you"


def test_walk_starts_inside_the_front_door_and_visits_every_room(monkeypatch):
    w = narrate.walk_tour(_unit(monkeypatch, bath_door=0.95), "wheelchair")
    steps = w["steps"]
    assert "front door" in steps[0]["text"] and "living room" in steps[0]["text"]
    assert abs(steps[0]["x"] - 0.9) < 0.2 and abs(steps[0]["y"] - 3.0) < 0.2       # just inside the west-wall entry, facing east
    assert steps[0]["look"][0] > steps[0]["x"]
    said = " ".join(s["text"] for s in steps)
    assert "bedroom" in said and "bathroom" in said and steps[-1]["text"].startswith("That's the whole floor")
    assert all(s["x"] is not None and s["look"] for s in steps[:-1])


def test_walk_warns_in_the_room_a_problem_belongs_to(monkeypatch):
    steps = narrate.walk_tour(_unit(monkeypatch, bath_door=0.70), "wheelchair")["steps"]
    bath = next(s["text"] for s in steps if "the bathroom" in s["text"])
    assert "Bathroom door" in bath and "26 inches clear" in bath
    assert not any("Bathroom door" in s["text"] for s in steps if "the bedroom" in s["text"])
