"""Pull Matterport geometry for every scan in data/buildings.json, nearest to Midtown Houston first.

    python -m pipeline.fetch_geometry [houston|la|all] [limit]

Sequential and cached (data/matterport/<id>.json), so it can be stopped and restarted.
"""
from __future__ import annotations

import json
import math
import sys
import time

from .clients import ROOT, matterport

HERO = (29.7437, -95.3795)  # Midtown Houston


def main(city: str = "houston", limit: int | None = None) -> None:
    rows = json.loads((ROOT / "data" / "buildings.json").read_text())
    rows = [r for r in rows if city == "all" or r["city"] == city]
    rows.sort(key=lambda r: math.hypot(r["lat"] - HERO[0], (r["lon"] - HERO[1]) * 0.87))
    ids = [u["model_id"] for r in rows for u in r["units"]]
    ids = list(dict.fromkeys(ids))[:limit]
    out = ROOT / "data" / "matterport"
    out.mkdir(parents=True, exist_ok=True)
    todo = [m for m in ids if not (out / f"{m}.json").exists() and not (out / f"{m}.err").exists()]
    print(f"{len(ids) - len(todo)} cached, {len(todo)} to fetch", flush=True)

    def one(mid: str) -> bool:
        try:
            matterport.geometry(mid)
            return True
        except Exception as e:  # noqa: BLE001 - record and move on
            (out / f"{mid}.err").write_text(str(e)[:500])
            return False

    from concurrent.futures import ThreadPoolExecutor
    done = fail = 0
    with ThreadPoolExecutor(max_workers=3) as pool:   # 3 polite workers; the client backs off on 429
        for ok in pool.map(one, todo):
            done += ok
            fail += not ok
            if (done + fail) % 100 == 0:
                print(f"{done + fail}/{len(todo)} fetched={done} failed={fail}", flush=True)
    print(f"finished: fetched={done} failed={fail} of {len(todo)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "houston", int(sys.argv[2]) if len(sys.argv) > 2 else None)
