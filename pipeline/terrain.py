"""Vexcel surface heights as map tiles, and ground heights along a line.

Tiles are 256 px, Mapbox terrain-RGB encoded, cached in data/dem/<z>/<x>/<y>.png. Zoom 16 is about 2 m per pixel.
The surface model includes roofs and tree tops, so ground_along() takes the low points near each sample
(the street or sidewalk beside the canopy) before measuring slope.
"""
from __future__ import annotations

import io
import math
import threading
from functools import lru_cache

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from .clients import ROOT, vexcel

DEM = ROOT / "data" / "dem"
_LOCK = threading.Lock()      # Vexcel asks for one request at a time


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z
    lon = lambda i: i / n * 360 - 180  # noqa: E731
    lat = lambda j: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * j / n))))  # noqa: E731
    return lon(x), lat(y + 1), lon(x + 1), lat(y)


def tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def _encode(height_m: np.ndarray) -> bytes:
    v = np.clip(np.round((height_m + 10000) * 10), 0, 2 ** 24 - 1).astype(np.uint32)
    rgb = np.dstack([(v >> 16) & 255, (v >> 8) & 255, v & 255]).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    return buf.getvalue()


def decode(png: bytes) -> np.ndarray:
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int64)
    return (-10000 + (a[..., 0] * 65536 + a[..., 1] * 256 + a[..., 2]) * 0.1).astype(np.float32)


def build_dem_tile(z: int, x: int, y: int) -> bytes | None:
    path = DEM / str(z) / str(x) / f"{y}.png"
    none = path.with_suffix(".none")
    if path.exists():
        return path.read_bytes()
    if none.exists():
        return None
    if z < 16:
        # Vexcel refuses boxes this large, so a wide tile is its four finer tiles shrunk and joined
        kids = [[build_dem_tile(z + 1, 2 * x + i, 2 * y + j) for i in (0, 1)] for j in (0, 1)]
        if not any(k for row in kids for k in row):
            return None
        fill = next(k for row in kids for k in row if k)
        big = np.zeros((512, 512), np.float32)
        for j in (0, 1):
            for i in (0, 1):
                big[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256] = decode(kids[j][i] or fill)
        body = _encode(big.reshape(256, 2, 256, 2).max(axis=(1, 3)))     # keep the tallest point so buildings don't melt
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body
    import tifffile
    w, s, e, n = tile_bounds(z, x, y)
    with _LOCK:
        if path.exists():
            return path.read_bytes()
        r = vexcel._get("dsm/extract", wkt=f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))", srid=4326, crop="bounding_box",
                        **{"destination-dimensions": "256,256"})
    path.parent.mkdir(parents=True, exist_ok=True)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        if r.status_code in (400, 404):
            none.write_text(str(r.status_code))
        return None
    a = tifffile.imread(io.BytesIO(r.content)).astype("float32")
    if a.shape != (256, 256):
        a = np.asarray(Image.fromarray(a).resize((256, 256), Image.BILINEAR))
    bad = ~np.isfinite(a) | (a < -500)
    a = np.where(bad, float(np.nanmedian(np.where(bad, np.nan, a))) if (~bad).any() else 0.0, a)
    body = _encode(a)
    path.write_bytes(body)
    return body


@lru_cache(maxsize=256)
def _ground_tile(z: int, x: int, y: int) -> np.ndarray | None:
    """A tile with roofs and tree tops pressed down to the low ground around them (about 14 m window at zoom 16)."""
    body = build_dem_tile(z, x, y)
    if body is None:
        return None
    return ndi.percentile_filter(decode(body), 8, size=7)


def ground_along(coords: list[list[float]], step_m: float = 10.0, z: int = 16) -> dict | None:
    """Ground height every step_m along a [[lon, lat], ...] line, and the steepest sustained slope.
    Returns None where there is no height data."""
    if len(coords) < 2:
        return None
    pts, dists, d = [], [], 0.0
    for (lo1, la1), (lo2, la2) in zip(coords, coords[1:]):
        seg = math.hypot((lo2 - lo1) * 111_320 * math.cos(math.radians(la1)), (la2 - la1) * 110_570)
        n = max(1, int(seg / step_m))
        for i in range(n):
            t = i / n
            pts.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
            dists.append(d + seg * t)
        d += seg
    pts.append((coords[-1][1], coords[-1][0]))
    dists.append(d)
    hs = []
    for la, lo in pts:
        fx, fy = tile_xy(la, lo, z)
        t = _ground_tile(z, int(fx), int(fy))
        if t is None:
            return None
        hs.append(float(t[min(255, int((fy % 1) * 256)), min(255, int((fx % 1) * 256))]))
    h = ndi.median_filter(np.array(hs), size=5, mode="nearest")
    h = ndi.uniform_filter1d(h, size=3, mode="nearest")
    dist = np.array(dists)
    grades = np.zeros(len(h))
    win = max(1, int(round(30 / step_m)))            # slope over 30 m, so one noisy sample can't make a hill
    for i in range(len(h) - win):
        run = dist[i + win] - dist[i]
        if run > 5:
            grades[i] = (h[i + win] - h[i]) / run * 100
    return {"dist_m": [round(float(v)) for v in dist], "height_m": [round(float(v - h.min()), 1) for v in h],
            "grade_pct": [round(float(v), 1) for v in grades], "max_grade_pct": round(float(np.abs(grades).max()), 1),
            "climb_m": round(float(np.clip(np.diff(h), 0, None).sum()), 1), "step_m": step_m}
