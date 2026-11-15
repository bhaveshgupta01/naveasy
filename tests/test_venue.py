"""The venue's public-building mode on a hand-built building and mesh (no network, no kit).

A hall (6 x 6 m), a 1.2 m corridor (8 m long) and a classroom (6 x 6 m) whose floor is 14 cm higher, as in the
Rabinowitz Building scan. The entrance is a 1.8 m double door on the hall's west wall; a 45 cm gap on the hall's
north wall is a scan artefact; the classroom's east door has a 13 cm sill in the scan, with outside unscanned.
"""
import json

import numpy as np

from pipeline import venue
from pipeline.clients import vexcel
from pipeline.unit import unit_json
from test_fixtures import Mesh


def _v(i, x, y):
    return {"id": f"v{i}", "position": {"x": x, "y": y}}


def model(corridor=1.2, class_door=0.95):
    c0, c1 = 3 - corridor / 2, 3 + corridor / 2
    pts = {0: (0, 0), 1: (6, 0), 2: (6, c0), 3: (6, c1), 4: (6, 6), 5: (0, 6),          # hall
           6: (14, c0), 7: (14, c1),                                                      # corridor end
           8: (14, 0), 9: (20, 0), 10: (20, 6), 11: (14, 6)}                              # classroom
    V = [_v(i, x, y) for i, (x, y) in pts.items()]

    def edge(i, a, b, openings=(), kind="wall"):
        return {"id": f"e{i}", "type": kind, "thickness": 0.12, "vertices": [{"id": f"v{a}"}, {"id": f"v{b}"}],
                "openings": [{"type": "doorway", "label": None, "width": w, "height": 2.1, "lowerElevation": le, "relativeCenter": rc}
                             for w, rc, le in openings]}

    E = [edge(0, 0, 1), edge(1, 1, 2), edge(2, 2, 3, kind="invisible"), edge(3, 3, 4), edge(4, 4, 5, [(0.45, 0.3, 0.0)]),
         edge(5, 5, 0, [(1.8, 0.5, 0.0)]),                                                 # entrance, west wall
         edge(6, 2, 6), edge(7, 3, 7),                                                     # corridor walls
         edge(8, 6, 7, [(class_door, 0.5, 0.0)]),                                          # corridor -> classroom door
         edge(9, 8, 6), edge(10, 7, 11), edge(11, 8, 9), edge(12, 9, 10, [(0.95, 0.5, 0.13)]), edge(13, 10, 11)]

    def room(i, cls, label, ids, panos):
        return {"id": f"r{i}", "label": label, "classifications": [{"label": cls}], "panoLocations": [{"id": p} for p in panos],
                "boundary": {"edges": [{"id": f"e{j}"} for j in ids]}, "dimensions": {"lowerElevation": 0.0}}

    rooms = [room(0, "Hallway", "", [0, 1, 2, 3, 4, 5], ["p0"]),
             room(1, "Hallway", "", [2, 6, 8, 7], ["p1"]),
             room(2, "Other", "Classroom", [9, 11, 12, 13, 10, 8], ["p2"])]       # the classifier says Other; a person said Classroom
    panos = [{"id": "p0", "position": {"x": 3, "y": 3, "z": 1.37}}, {"id": "p1", "position": {"x": 10, "y": 3, "z": 1.37}},
             {"id": "p2", "position": {"x": 17, "y": 3, "z": 1.51}}]
    return {"id": "venue_test", "name": "Test hall", "created": "2026-11-14T00:00:00Z", "geocoordinates": None, "panoLocations": panos,
            "floors": [{"id": "f0", "label": "Floor 1", "sequence": 0, "vertices": V, "edges": E, "rooms": rooms}]}


def mesh():
    m = Mesh()
    m.quad(0, 0, 6, 6, 0.0)                     # hall floor
    m.quad(6, 2.4, 14, 3.6, 0.0)                # corridor floor
    m.quad(14, 0, 20, 6, 0.14)                  # classroom floor: 14 cm higher
    return m.arrays()


def build(name="venue_test", **kw):
    return venue.build_plan(name, model(**kw), mesh(), offset=0.0)


def test_classroom_label_comes_from_the_person_not_the_classifier():
    build()
    assert "classroom" in venue.places("venue_test")


def test_artefact_doorway_set_aside():
    plan = build()
    gaps = [d for d in plan.floors[0].doors if d.get("artefact")]
    assert len(gaps) == 1 and gaps[0]["width_m"] == 0.45
    doors = venue.door_widths(model_id="venue_test")
    assert all(x["status"] != "artefact" for x in doors["doors"]) and len(doors["artefacts"]) == 1
    res = venue.analyze_public(plan)
    assert any(p["kind"] == "scan_artefact" and p["severity"] == "low" for p in res["problems"])
    assert not any(p["kind"] == "narrow_door" and "45" in p["text"] for p in res["problems"])


def test_entrance_is_the_wide_door_and_route_reaches_the_classroom():
    plan = build()
    assert plan.entry is not None and plan.entry["width_m"] == 1.8
    r = venue.inside_route("entrance", "classroom", "wheelchair", "venue_test")
    leg = r["legs"][0]
    assert leg["connected"] and 14 < leg["meters"] < 22
    assert any(d["rooms"] == ["Hallway", "Classroom"] or d["rooms"] == ["Classroom", "Hallway"] for d in leg["doors"])
    assert "not yet checked against a tape measure" in r["spoken"]


def test_corridor_width_is_the_narrowest_point():
    build("venue_wide", corridor=1.2)
    wide = venue.inside_route("entrance", "classroom", "walk", "venue_wide")["legs"][0]
    assert 1.0 <= wide["narrowest_m"] <= 1.15          # 1.2 m between wall centers, less the 12 cm walls
    build("venue_narrow", corridor=0.75)
    narrow = venue.inside_route("entrance", "classroom", "wheelchair", "venue_narrow")
    assert not narrow["legs"][0]["connected"]           # under 32 in clear: a wheelchair can't get through
    assert any("no way through" in s for s in narrow["needs_site_visit"])


def test_raised_floor_and_steps_come_from_the_mesh():
    plan = build()
    assert plan.venue["levels"][0] == 0.0 or abs(plan.venue["levels"][0]) < 0.02
    z = plan.venue["raised"]
    assert len(z) == 1 and abs(z[0]["rise_m"] - 0.14) < 0.03 and 25 < z[0]["area_m2"] < 40
    doors = {tuple(d["room_types"]): d for d in plan.floors[0].doors}
    inner = doors.get(("Hallway", "Classroom")) or doors[("Classroom", "Hallway")]
    assert inner["step_source"] == "mesh" and abs(inner["mesh_step_m"] - 0.14) < 0.03
    east = next(d for d in plan.floors[0].doors if d["exterior"] and d["room_types"] == ["Classroom"])
    fact = venue._step_fact(east)
    assert fact and not fact["confirmed"] and "needs a site visit" in fact["says"]


def test_public_analysis_renders_as_a_unit():
    plan = build()
    res = venue.analyze_public(plan)
    u = unit_json(plan, res, "wheelchair")
    vals = np.array(u["heat"]["values"])
    assert (vals == 4).sum() > 1000                     # the hall and corridor are reachable from the entrance
    assert res["score_note"].startswith("Public-building checks")
    assert set(res["scores"]) == {"wheelchair", "blind", "deaf", "older"}


def test_tape_measure_evidence(tmp_path, monkeypatch):
    gt = tmp_path / "princeton.json"
    monkeypatch.setattr(venue, "GROUND_TRUTH", gt)
    assert "not yet checked" in venue.evidence("door_x", 35.4)["says"]
    gt.write_text(json.dumps({"checked_on": "2026-11-15", "items": {"door_x": {"tape_in": 35.0}, "door_y": {"tape_in": 31.0}}}))
    ok = venue.evidence("door_x", 35.4)
    assert ok["checked"] and ok["agrees"] and "2026-11-15" in ok["says"]
    off = venue.evidence("door_y", 34.5)
    assert off["checked"] and not off["agrees"] and "we trust the tape" in off["says"]


def test_vexcel_geofence():
    assert vexcel.region(40.3491, -74.6554) == "princeton"
    assert vexcel.region(29.75, -95.37) == "houston"
    assert vexcel.region(40.71, -74.0) is None
    try:
        vexcel._check("POINT(-74.0 40.71)")
        raise AssertionError("a point outside every region must be refused")
    except vexcel.OutsideRegion:
        pass
