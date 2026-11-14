"""Which scans are furnished? Sample two scan positions per home at low resolution and measure how much of the
picture changes between the scan and its furniture-removed view.

    python -m pipeline.furnished_scan [houston|la] [max homes]

Writes data/furnished_index.json: model_id -> share of pixels that changed (0 = empty, over ~0.06 = lived-in).
"""
from __future__ import annotations

import io
import json
import sys

import numpy as np
import requests
from PIL import Image
from scipy import ndimage as ndi

from .clients import ROOT, matterport

DATA = ROOT / "data"


def changed_share(model_id: str, view_id: str) -> float | None:
    a, b = matterport.panoramas(model_id, "low"), matterport.panoramas(view_id, "low")
    bi = {p["id"]: p for p in b}
    a = [p for p in a if p["id"] in bi and p["skybox"]["status"] == "available"]
    if not a:
        return None
    picks = [a[len(a) // 3], a[2 * len(a) // 3]] if len(a) > 2 else a[:1]
    shares = []
    for p in picks:
        for face in (1, 3):
            im = []
            for src in (p, bi[p["id"]]):
                r = requests.get(src["skybox"]["children"][face], timeout=60)
                im.append(ndi.uniform_filter(np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB").resize((192, 192)), dtype=np.float32), (5, 5, 1)))
            diff = np.abs(im[0] - im[1]).sum(-1)[96:]          # lower half: where furniture stands
            shares.append(float((diff > 30).mean()))
    return round(float(np.mean(shares)), 3)


def main(city: str = "houston", limit: int = 60) -> None:
    idx = json.loads((DATA / "units_index.json").read_text())
    rows = json.loads((DATA / "buildings_scored.json").read_text())
    path = DATA / "furnished_index.json"
    out = json.loads(path.read_text()) if path.exists() else {}
    n = 0
    for b in rows:
        if b["city"] != city or b["kind"] != "home":
            continue
        for u in b["units"]:
            s = idx.get(u["model_id"]) or {}
            if u["model_id"] in out or not (s.get("has_graph") and s.get("defurnished") and s["stats"]["entry_found"] and 10 <= s.get("panos", 0) <= 30):
                continue
            try:
                out[u["model_id"]] = changed_share(u["model_id"], s["defurnished"])
            except Exception as e:  # noqa: BLE001
                out[u["model_id"]] = None
                print("skip", u["model_id"], str(e)[:80], flush=True)
            n += 1
            if n % 10 == 0:
                path.write_text(json.dumps(out))
                print(n, "checked", flush=True)
            if n >= limit:
                break
        if n >= limit:
            break
    path.write_text(json.dumps(out))
    top = sorted(((v, k) for k, v in out.items() if v), reverse=True)[:12]
    print("most furnished:", top)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "houston", int(sys.argv[2]) if len(sys.argv) > 2 else 60)
