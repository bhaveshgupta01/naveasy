"""Pre-download and crop the drone models (aerial Gaussian splats) of homes near a point, so zooming into
them on the map opens instantly. Each full file is about 550 MB to download; the cropped copy kept is ~75 MB.

    python scripts/prefetch_splats.py [lat lon km]      # default: 6 km around Midtown Houston
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import splat  # noqa: E402

lat, lon, km = (float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else (29.7437, -95.3795, 6.0)
ids = splat.splat_ids()
rows = json.loads((ROOT / "data" / "buildings.json").read_text())
todo = []
for b in rows:
    d = math.hypot((b["lat"] - lat) * 110.57, (b["lon"] - lon) * 111.32 * math.cos(math.radians(lat)))
    sid = next((u["model_id"] for u in b["units"] if u["model_id"] in ids), None)
    if sid and d <= km:
        todo.append((d, sid, b["name"]))
todo.sort()
print(f"{len(todo)} drone models within {km} km", flush=True)
for i, (d, sid, name) in enumerate(todo, 1):
    try:
        path, n = splat.fetch(sid)
        print(f"{i}/{len(todo)} {name} ({d:.1f} km): {'cached' if not n else f'{n:,} gaussians'}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"{i}/{len(todo)} {name}: failed {str(e)[:120]}", flush=True)
