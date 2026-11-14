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
