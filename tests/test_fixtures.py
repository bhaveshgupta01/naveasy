"""Built-ins and stairs from a 3D mesh, on a hand-built plan and a hand-built mesh (no network)."""
import numpy as np

from pipeline import fixtures
from pipeline.unit import CELL, analyze, load_plan, unit_json
from test_unit_engine import make_model


def model():
    """The test flat with the living room relabeled Kitchen and the bedroom relabeled Staircase (3 x 3.5 m)."""
    m = make_model(bath_door=0.95)
    labels = {"Living Room": "Kitchen", "Bedroom": "Staircase"}
    for r in m["floors"][0]["rooms"]:
        r["classifications"][0]["label"] = labels.get(r["classifications"][0]["label"], r["classifications"][0]["label"])
    return m


class Mesh:
    def __init__(self):
        self.v, self.f = [], []

    def quad(self, x0, y0, x1, y1, z, up=True):
        """A level rectangle; up=False winds it to face down (a ceiling, the underside of an overhang)."""
        i = len(self.v)
        self.v += [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]
        self.f += [[i, i + 1, i + 2], [i, i + 2, i + 3]] if up else [[i, i + 2, i + 1], [i, i + 3, i + 2]]

    def arrays(self):
        return np.array(self.v, dtype=np.float32), np.array(self.f)


def cells(f, x0, y0, x1, y1):
    r0, c0 = f.rc(x0, y0)
    r1, c1 = f.rc(x1, y1)
    return slice(r0, r1), slice(c0, c1)


def build():
    plan = load_plan("test", model())
    f = plan.floors[0]
    m = Mesh()
    m.quad(0.3, 0.3, 2.3, 0.9, 0.9)                 # kitchen counter along the south wall
    m.quad(2.0, 3.0, 3.0, 4.0, 0.9, up=False)       # something facing down at counter height: not a counter top
    m.quad(0.0, 0.0, 5.0, 6.0, 2.5, up=False)       # the kitchen ceiling
    for k in range(5):                              # a flight of five treads climbing north in the staircase
        m.quad(5.5, 3.0 + 0.3 * k, 6.5, 3.3 + 0.3 * k, 0.18 * (k + 1))
    m.quad(6.8, 4.5, 7.8, 5.5, 0.45)                # a flat seat in the staircase room: not stairs
    v, fc = m.arrays()
    for i, mask in fixtures.masks(plan, v, fc).items():
        plan.floors[i].fixture_mask = mask
    for i, mask in fixtures.stairs(plan, v, fc).items():
        plan.floors[i].stairs_mask = mask
    plan.fixtures = "mesh"
    return plan, f


def test_counter_found_only_where_it_faces_up_in_a_wet_room():
    plan, f = build()
    fix = f.fixture_mask
    assert fix[cells(f, 0.5, 0.4, 2.1, 0.8)].mean() > 0.9           # the counter top
    assert not fix[cells(f, 2.1, 3.1, 2.9, 3.9)].any()                # facing down: ignored
    assert fix.sum() * CELL * CELL < 1.5                               # the ceiling is not a fixture


def test_stairs_found_but_not_a_flat_seat():
    plan, f = build()
    st = f.stairs_mask
    assert st[cells(f, 5.6, 3.1, 6.4, 4.3)].mean() > 0.8
    assert not st[cells(f, 6.9, 4.6, 7.7, 5.4)].any()


def test_counters_block_and_stairs_are_red():
    plan, f = build()
    res = analyze(plan)
    heat = res["floors"][0]["heat"]
    r, c = f.rc(1.3, 0.6)
    assert heat["wheelchair"][r, c] == 1 and heat["older"][r, c] == 1      # a counter is not floor you can use
    r, c = f.rc(6.0, 3.6)
    assert heat["blind"][r, c] == 2 and heat["wheelchair"][r, c] == 2 and heat["older"][r, c] == 2
    u = unit_json(plan, res, "blind")
    assert u["fixtures_source"] == "mesh" and u["fixtures"]


def test_no_mesh_means_no_fixtures():
    plan = load_plan("test", model())
    assert plan.fixtures is None and plan.floors[0].fixture_mask is None
    u = unit_json(plan, analyze(plan), "wheelchair")
    assert u["fixtures"] == [] and u["fixtures_source"] is None
    assert u["fixture_items"] == [] and "counter_height" not in u["checks"]


# ---------------------------------------------------------------- each built-in measured on its own

def build_measured(counter_z=0.91, labels=None):
    """The test flat with a floor in the mesh, a 2.0 x 0.6 m counter along the kitchen's south wall at counter_z,
    and a 0.40 x 0.45 m toilet seat at 0.42 m in the bathroom."""
    m_ = make_model(bath_door=0.95)
    for r in m_["floors"][0]["rooms"]:
        lab = r["classifications"][0]["label"]
        r["classifications"][0]["label"] = (labels or {"Living Room": "Kitchen"}).get(lab, lab)
    plan = load_plan("test", m_)
    m = Mesh()
    m.quad(0.0, 0.0, 8.0, 6.0, 0.0)                 # the floor
    m.quad(0.3, 0.15, 2.3, 0.75, counter_z)         # kitchen counter, its back 9 cm off the wall
    m.quad(7.2, 0.3, 7.6, 0.75, 0.42)               # toilet seat
    v, fc = m.arrays()
    fm = fixtures.masks(plan, v, fc)
    for i, mask in fm.items():
        plan.floors[i].fixture_mask = mask
    for i, its in fixtures.items(plan, v, fc, fm).items():
        plan.floors[i].fixture_items = its
    plan.fixtures = "mesh"
    return plan, plan.floors[0]


def test_counter_is_measured_and_a_high_one_is_a_reach_problem():
    plan, f = build_measured(counter_z=0.91)
    counter = next(it for it in f.fixture_items if it["room_type"] == "Kitchen")
    assert counter["kind"] == "counter" and counter["at_wall"]
    assert abs(counter["width_m"] - 2.0) <= 0.1 and abs(counter["depth_m"] - 0.6) <= 0.1
    assert 35.3 <= counter["height_in"] <= 36.3 and counter["floor_ref"] == "mesh"
    assert "measured from the 3D mesh" in counter["measured"] and "inferred" in counter["kind_source"]
    res = analyze(plan)
    hits = [p for p in res["problems"] if p["kind"] == "counter_height"]
    assert len(hits) == 1 and hits[0]["severity"] == "medium" and hits[0]["profile"] == "wheelchair"
    assert "about 36 in high" in hits[0]["text"] and "804.3.2" in hits[0]["text"]
    assert res["checks"]["wheelchair"]["counter_height"] == {"weight": 0.0, "value": 0.0}
    u = unit_json(plan, res, "wheelchair")
    assert any(it["kind"] == "counter" for it in u["fixture_items"])


def test_a_34_inch_counter_is_not_a_problem_and_scores_do_not_move():
    plan, _f = build_measured(counter_z=0.85)
    res = analyze(plan)
    assert not [p for p in res["problems"] if p["kind"] == "counter_height"]
    assert res["checks"]["wheelchair"]["counter_height"]["value"] == 1.0
    for fl in plan.floors:
        fl.fixture_items = None                      # the same home without measured items scores the same
    assert analyze(plan)["scores"] == res["scores"]


def test_toilet_sized_patch_is_a_toilet():
    _plan, f = build_measured()
    toilet = next(it for it in f.fixture_items if it["room_type"] == "Bathroom")
    assert toilet["kind"] == "toilet" and not toilet["possibly_movable"]
    assert 15.5 <= toilet["height_in"] <= 17.5


def test_public_building_lists_desks_but_does_not_block_them():
    plan, f = build_measured(counter_z=0.74, labels={"Living Room": "Classroom", "Bedroom": "Office"})
    assert fixtures.is_public(plan)
    desk = next(it for it in f.fixture_items if it["room_type"] == "Classroom")
    assert desk["kind"] == "desk or table" and desk["possibly_movable"]
    r, c = f.rc(1.3, 0.45)
    assert not f.fixture_mask[r, c]                  # listed, but the heat map doesn't treat it as built in
    assert 28.5 <= desk["height_in"] <= 29.8


def test_furniture_heights_from_the_mesh():
    plan, _f = build_measured()
    m = Mesh()
    m.quad(0.0, 0.0, 8.0, 6.0, 0.0)
    m.quad(5.6, 3.4, 7.1, 5.4, 0.6)                 # a mattress top in the bedroom
    m.quad(5.6, 5.3, 7.1, 5.4, 1.1)                 # a thin headboard
    v, fc = m.arrays()
    out = fixtures.furniture_heights(plan, v, fc, [{"id": "bed", "floor": 0, "rect": [5.5, 3.3, 1.7, 2.2]},
                                                   {"id": "rug", "floor": 0, "rect": [1.0, 2.0, 1.0, 1.0]}])
    assert abs(out["bed"]["surface_m"] - 0.6) < 0.02 and out["bed"]["height_m"] >= 0.6
    assert "rug" not in out                          # nothing level seen above the floor there: no height
