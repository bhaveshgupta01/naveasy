"""Which path answers, and the checks every answer passes before anyone hears it (offline: no model is called)."""
from backend.app import agent, deep


def test_voice_and_the_guided_conversation_stay_local(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(deep, "enabled", lambda: True)
    assert agent.route("find me a place near Midtown under $2,000, I use a wheelchair", {"channel": "voice"}) == "local"
    assert agent.route("__start__", {}) == "local"
    assert agent.route("2000", {"state": {"guide": "budget"}}) == "local"


def test_multi_step_searches_are_planned_and_questions_go_to_one_agent(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(deep, "enabled", lambda: True)
    assert agent.route("I use a power wheelchair. Find us a place near the Medical Center under $2,000.", {}) == "deep"
    assert agent.route("I'm moving from 1200 W 7th St, Los Angeles to Houston. I use a wheelchair.", {}) == "deep"
    assert agent.route("How wide is the bathroom door?", {}) == "claude"
    monkeypatch.setattr(deep, "enabled", lambda: False)                  # NAVEASY_DEEP=off
    assert agent.route("I use a power wheelchair. Find us a place near the Medical Center under $2,000.", {}) == "claude"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert agent.route("How wide is the bathroom door?", {}) == "local"


def test_numbers_in_an_answer_must_come_from_a_tool():
    evidence = ['{"rent_from": 1884, "minutes": 13.8, "est_clear_in": 32.6, "area_m2": 70.0, "share": 0.86}']
    ok = agent.audit_numbers("Rent from 1,884 dollars, the hospital is 14 minutes away, the door is about 33 inches, 86 percent reachable.", evidence)
    assert ok["unsupported"] == []
    bad = agent.audit_numbers("Rent from 1,650 dollars and the door is 41 inches.", evidence)
    assert bad["unsupported"] == ["1650", "41"]


def test_never_accessible_or_compliant():
    out = agent.guard("This unit is fully accessible and ADA compliant.")
    assert "accessible" not in out.lower() and "compliant" not in out.lower()


def test_the_planned_run_ends_on_one_view():
    acts = [{"type": "set_map_view", "center": [0, 0], "zoom": 13}, {"type": "highlight_buildings", "ids": ["a", "b"]},
            {"type": "open_building", "id": "a"}, {"type": "open_unit", "model_id": "m1"}, {"type": "open_building", "id": "b"},
            {"type": "show_route", "building_id": "a", "category": "pharmacy", "profile": "wheelchair"},
            {"type": "show_route", "building_id": "a", "category": "grocery", "profile": "wheelchair"}]
    out = deep.curate(acts)
    assert [a["type"] for a in out] == ["set_map_view", "highlight_buildings", "open_building", "show_route"]
    assert out[2]["id"] == "a" and out[3]["category"] == "grocery"
