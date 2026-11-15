"""The furnishing planner places the same furniture differently for each need (Technical Design section 8).

The flat: an 11 x 6 m version of the test flat (living room on the west with the front door, a 6 x 3.5 m bedroom and
a bathroom to the east), with a short wall inside the living room that hides its south-west corner from the front door.
"""
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from backend.app import narrate
from pipeline import furnish as F
from pipeline.unit import analyze, item_polygon, load_plan, unit_json

from test_unit_engine import _v, make_model

INV = [{"label": x} for x in ("queen bed", "nightstand", "sofa", "tv stand", "coffee table", "desk", "bookshelf")]


def _plan():
    m = make_model(bath_door=0.95, width=11.0)
    fl = m["floors"][0]
    fl["vertices"] += [_v(20, 0.0, 1.8), _v(21, 2.2, 1.8)]
    fl["edges"].append({"id": "e20", "type": "wall", "thickness": 0.12, "vertices": [{"id": "v20"}, {"id": "v21"}], "openings": []})
    return load_plan("test", m)


@pytest.fixture(scope="module")
def plans():
    plan = _plan()
    return plan, {need: F.plan_furnishing(plan, INV, [need], {}) for need in ("wheelchair", "blind", "deaf", "older")}


def _free(plan, placed):
    f = plan.floors[0]
    furn = np.zeros(f.shape, bool)
    for p in placed:
        furn[F._rect_mask(f, p["rect"])] = True
    return f, (f.room_idx >= 0) & ~f.wall_mask & ~furn


def _piece(r, label):
    return next((p for p in r["placements"] if p["label"] == label), None)


def test_the_corner_behind_the_short_wall_cant_see_the_front_door():
    plan = _plan()
    needs = F._Needs(plan, plan.floors[0], ["deaf"], analyze(plan, layers=False))
    assert not needs.sees_entry((1.0, 0.6)) and needs.sees_entry((1.0, 4.5))


def test_deaf_puts_the_sofa_where_the_front_door_is_in_view(plans):
    plan, r = plans
    after = analyze(plan, r["deaf"]["placements"], layers=False)
    assert after["checks"]["deaf"]["entry_visible_from_seat"]["value"] == 1
    sofa = _piece(r["deaf"], "sofa")
    assert sofa["rect"][1] > 1.8 and "front door" in sofa["why"] and "deaf" in sofa["needs"]
    assert any(x.startswith("Deaf: the front door can be seen from the sofa") for x in r["deaf"]["reasons"])


def test_blind_keeps_every_piece_off_the_routes_from_the_door(plans):
    plan, r = plans
    blind = r["blind"]
    after = analyze(plan, blind["placements"], layers=False)
    assert after["checks"]["blind"]["clear_paths"]["value"] == 1
    routes = [LineString(p["points"]) for p in analyze(plan, layers=False)["paths"] if len(p["points"]) > 1]
    ct = _piece(blind, "coffee table")
    assert ct is None and any(w["label"] == "coffee table" for w in blind["wont_fit"]) or \
        min(line.distance(Polygon(item_polygon(ct))) for line in routes) >= F.ROUTE_CLEAR_M
    assert all(p.get("facing") for p in blind["placements"])                     # everything against a wall
    assert not any(p.get("caution") for p in blind["placements"])


def test_wheelchair_bed_leaves_30_by_48_inches_on_one_side(plans):
    plan, r = plans
    wc = r["wheelchair"]
    f, free = _free(plan, wc["placements"])
    bed = _piece(wc, "queen bed")
    sides, t0, t1 = F._bed_geometry(bed["rect"], bed["facing"])
    assert max(F._side_clearance(free, f, bed["rect"], s, t0, t1) for s in sides) >= F.TRANSFER_M
    assert "inches clear" in bed["why"] and bed["transfer_side"] in sides


def test_older_bed_has_the_shortest_walk_to_the_bathroom(plans):
    plan, r = plans
    walk = {need: analyze(plan, res["placements"], layers=False)["stats"]["bed_to_bathroom_m"] for need, res in r.items()}
    assert walk["older"] < min(walk[n] for n in ("wheelchair", "blind", "deaf")) - 1.0
    assert "bathroom door" in _piece(r["older"], "queen bed")["why"]


def test_the_why_is_spoken_after_where_each_piece_goes(plans):
    plan, r = plans
    u = unit_json(plan, analyze(plan), "deaf")
    said = narrate.plan_narration(r["deaf"], u)["text"]
    assert "Your sofa goes" in said and ": it faces the front door" in said or ": it has a clear view of the front door" in said
