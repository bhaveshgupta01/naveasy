from pipeline import locality
from pipeline.buildings import age_component, city_of, score_row, vertical_access
from pipeline.routes import decode6
from pipeline.terrain import _encode, decode, tile_bounds, tile_xy

import numpy as np


def test_city_of():
    assert city_of(29.74, -95.38) == "houston"
    assert city_of(34.05, -118.25) == "la"
    assert city_of(40.7, -74.0) is None


def test_vertical_access():
    assert vertical_access(9, False, 5)[0] == 1.0
    assert vertical_access(1, None, 0)[0] == 1.0
    assert vertical_access(3, True, 0)[0] < 0.5                    # walk-up: only the ground floor is step-free
    assert vertical_access(None, None, 0)[2] is False               # unknown stays unknown


def test_age_and_score_row():
    assert age_component(2013)[0] == 1.0 and age_component(1965)[0] < 0.5 and age_component(None)[0] == 0.5
    good, bad = score_row(1.0, 0.5, 0.3, 0.2, 1.0, True), score_row(0.3, 0.5, 0.3, 0.2, 0.3, True)
    assert good["wheelchair"]["score"] > bad["wheelchair"]["score"]
    assert score_row(1, 1, 1, 1, 1, False)["wheelchair"]["confidence"] == "low"


def test_locality_minutes_score():
    near = {c: 1.0 for c in locality.CATEGORIES}
    far = {c: 120.0 for c in locality.CATEGORIES}
    assert abs(locality.score_from_minutes(near) - 1.0) < 1e-6
    assert locality.score_from_minutes(far) == 0.0
    assert abs(sum(w for _, _, w, _ in locality.CATEGORIES.values()) - 1.0) < 1e-6


def test_polyline_decode():
    # Valhalla precision-6 encoding of (38.5, -120.2) then (40.7, -120.95)
    def enc(vals):
        out, prev = "", 0
        res = []
        for v in vals:
            res.append(v)
        return res
    pts = [(38.5, -120.2), (40.7, -120.95)]
    s, plat, plon = "", 0, 0
    for lat, lon in pts:
        for cur, prev in ((round(lat * 1e6), plat), (round(lon * 1e6), plon)):
            d = cur - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                s += chr((0x20 | (d & 0x1F)) + 63)
                d >>= 5
            s += chr(d + 63)
        plat, plon = round(lat * 1e6), round(lon * 1e6)
    assert decode6(s) == [[-120.2, 38.5], [-120.95, 40.7]]


def test_terrain_tile_math_and_encoding():
    w, s, e, n = tile_bounds(16, 15404, 27092)
    assert w < e and s < n
    fx, fy = tile_xy((s + n) / 2, (w + e) / 2, 16)
    assert int(fx) == 15404 and int(fy) == 27092
    h = np.array([[0.0, 12.3], [41.7, 119.1]], dtype=np.float32)
    assert np.allclose(decode(_encode(h)), h, atol=0.06)
