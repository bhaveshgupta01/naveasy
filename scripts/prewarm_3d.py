"""Pre-download the 3D terrain and aerial tiles around a point so the demo loads instantly.

    python scripts/prewarm_3d.py [lat lon km]      # default: Midtown Houston, 1.5 km

Needs the API running on localhost:8000. Safe to stop and rerun (everything is cached in data/dem and data/tiles).
"""
import math
import sys

import requests

lat, lon, km = (float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else (29.7437, -95.3795, 1.5)
API = "http://localhost:8000"


def tile(la: float, lo: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    return int((lo + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n)


dlat, dlon = km / 110.57, km / (111.32 * math.cos(math.radians(lat)))
for kind, zooms in (("dem", (14, 15, 16, 17)), ("tiles", (15, 16, 17, 18))):
    for z in zooms:
        x0, y0 = tile(lat + dlat, lon - dlon, z)
        x1, y1 = tile(lat - dlat, lon + dlon, z)
        ok = 0
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                ok += requests.get(f"{API}/{kind}/{z}/{x}/{y}.{'png' if kind == 'dem' else 'jpg'}", timeout=120).status_code == 200
        print(kind, z, f"{ok}/{(x1 - x0 + 1) * (y1 - y0 + 1)}", flush=True)
