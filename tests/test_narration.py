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


def test_relative_directions():
    assert narrate._relative((0, 1), (0, 5)) == "straight ahead"
    assert narrate._relative((0, 1), (-5, 0)) == "to your left"
    assert narrate._relative((0, 1), (5, 0)) == "to your right"
    assert narrate._relative((0, 1), (0, -5)) == "behind you"
