"""The apartment engine on a hand-built plan, so the checks are known in advance (no network, no kit)."""
from pipeline.unit import analyze, load_plan, unit_json


def _v(i, x, y):
    return {"id": f"v{i}", "position": {"x": x, "y": y}}


def make_model(bath_door=0.70, entry=0.95, bed_door=0.90, threshold=0.0):
    """A 8 x 6 m flat: living room (left 5 x 6), bedroom (top right 3 x 3.5), bathroom (bottom right 3 x 2.5)."""
    pts = [(0, 0), (5, 0), (8, 0), (8, 2.5), (8, 6), (5, 6), (0, 6), (5, 2.5)]
    V = [_v(i, x, y) for i, (x, y) in enumerate(pts)]

    def edge(i, a, b, openings=()):
        return {"id": f"e{i}", "type": "wall", "thickness": 0.12, "vertices": [{"id": f"v{a}"}, {"id": f"v{b}"}],
                "openings": [{"type": t, "label": None, "width": w, "height": 2.0, "lowerElevation": le, "relativeCenter": c} for t, w, c, le in openings]}

    E = [
        edge(0, 0, 1), edge(1, 1, 2), edge(2, 2, 3), edge(3, 3, 4), edge(4, 4, 5), edge(5, 5, 6),
        edge(6, 6, 0, [("doorway", entry, 0.5, 0.0)]),                    # entry on the west wall of the living room
        edge(7, 1, 7, [("doorway", bath_door, 0.5, threshold)]),          # living <-> bathroom
        edge(8, 7, 5, [("doorway", bed_door, 0.5, 0.0)]),                 # living <-> bedroom
        edge(9, 7, 3),                                                    # bedroom | bathroom
    ]

    def room(i, label, ids):
        return {"id": f"r{i}", "label": None, "classifications": [{"label": label}], "panoLocations": [],
                "boundary": {"edges": [{"id": f"e{j}"} for j in ids]}, "dimensions": {"lowerElevation": 0.0}}

    rooms = [room(0, "Living Room", [0, 7, 8, 5, 6]), room(1, "Bathroom", [1, 2, 9, 7]), room(2, "Bedroom", [9, 3, 4, 8])]
    return {"id": "test", "name": "Test flat", "created": "2026-11-01T00:00:00Z", "geocoordinates": None,
            "floors": [{"id": "f0", "label": "Floor 1", "sequence": 0, "vertices": V, "edges": E, "rooms": rooms}]}


def run(**kw):
    plan = load_plan("test", make_model(**kw))
    return plan, analyze(plan)


def test_plan_geometry():
    plan, _ = run()
    f = plan.floors[0]
    assert sorted(r["type"] for r in f.rooms) == ["Bathroom", "Bedroom", "Living Room"]
    assert abs(sum(r["area_m2"] for r in f.rooms) - 48) < 0.5
    assert plan.entry is not None and plan.entry["room_types"] == ["Living Room"]
    assert len(f.doors) == 3


def test_narrow_bathroom_door_blocks_the_bathroom():
    plan, res = run(bath_door=0.70)                      # 0.70 m scan width -> about 25.6 in clear
    texts = [p["text"] for p in res["problems"] if p["profile"] == "wheelchair"]
    assert any("Bathroom door" in t and "26 in" in t for t in texts)
    assert any("can't reach this bathroom" in t for t in texts)
    assert res["checks"]["wheelchair"]["rooms_reachable"]["value"] < 1
    assert res["scores"]["wheelchair"]["score"] < 80


def test_wide_doors_pass():
    _, res = run(bath_door=0.95)
    assert not [p for p in res["problems"] if p["profile"] == "wheelchair" and p["severity"] == "high"]
    assert res["checks"]["wheelchair"]["rooms_reachable"]["value"] == 1
    assert res["checks"]["wheelchair"]["turning_space"]["value"] == 1
    assert res["scores"]["wheelchair"]["score"] >= 90


def test_tight_door_is_caution_not_blocked():
    _, res = run(bath_door=0.85)                          # 0.80 m clear: under 32 in, but the raw width passes
    assert res["checks"]["wheelchair"]["rooms_reachable"]["value"] == 1
    assert any(p["severity"] == "medium" and "Bathroom door" in p["text"] for p in res["problems"])


def test_narrow_entry_fails_everything():
    _, res = run(entry=0.70)
    assert res["checks"]["wheelchair"]["entry_door"]["value"] == 0
    assert res["checks"]["wheelchair"]["rooms_reachable"]["value"] == 0


def test_threshold_is_flagged_only_when_clearly_raised():
    _, low = run(bath_door=0.95, threshold=0.015)         # inside scan noise
    _, high = run(bath_door=0.95, threshold=0.08)
    assert not [p for p in low["problems"] if p["kind"] == "threshold"]
    assert [p for p in high["problems"] if p["kind"] == "threshold" and p["profile"] == "wheelchair"]


def test_furniture_in_the_doorway_blocks_and_scores_lower():
    plan, empty = run(bath_door=0.95)
    sofa = [{"id": "s", "label": "sofa", "rect": [4.0, 0.8, 0.9, 1.0], "floor": 0}]      # parked in front of the bathroom door
    blocked = analyze(plan, sofa)
    assert blocked["scores"]["wheelchair"]["score"] < empty["scores"]["wheelchair"]["score"]
    assert blocked["checks"]["wheelchair"]["rooms_reachable"]["value"] < 1


def test_all_profiles_scored_and_api_shape():
    plan, res = run()
    assert set(res["scores"]) == {"wheelchair", "blind", "deaf", "older"}
    assert all(0 <= s["score"] <= 100 for s in res["scores"].values())
    for prof in res["scores"]:
        u = unit_json(plan, res, prof)
        assert len(u["heat"]["values"]) == u["heat"]["height"] and len(u["heat"]["values"][0]) == u["heat"]["width"]
        assert {v for row in u["heat"]["values"] for v in row} <= {0, 1, 2, 3, 4}
    assert res["stats"]["bed_to_bathroom_m"] is not None


def test_bigger_chair_needs_wider_doors():
    plan = load_plan("test", make_model(bath_door=0.90))
    assert analyze(plan)["checks"]["wheelchair"]["rooms_reachable"]["value"] == 1
    assert analyze(plan, chair_width=0.80)["checks"]["wheelchair"]["rooms_reachable"]["value"] < 1
