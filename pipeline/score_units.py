"""Score every scan whose geometry is cached, then fold the results into the building table.

    python -m pipeline.score_units

Writes data/units_index.json (model_id -> summary) and data/buildings_scored.json.
Safe to rerun while pipeline.fetch_geometry is still downloading.
"""
from __future__ import annotations

import json
import re
import sys
import time

from .clients import ROOT
from .unit import PROFILES, analyze, load_plan, summary

DATA = ROOT / "data"
AMENITY = re.compile(r"fitness|pool|club|lobby|lounge|business|courtyard|gym|laundry fac|leasing|conference|game|terrace|"
                     r"concierge|rooftop|theat|yoga|spa\b|dog|pet|grill|kitchen$|mail|cowork|amenit|office|garage|deck|patio|"
                     r"library|media|party|community|exterior|entrance|model unit tour|bbq|sky", re.I)
ORDER = {"low": 0, "medium": 1, "high": 2}


def is_dwelling(s: dict, caption: str | None, kind: str) -> bool:
    types = {r["type"] for r in s.get("rooms", [])}
    if kind == "home":
        return True
    if caption and re.search(r"\d\s*b[rd]|studio|bed|bath", caption, re.I):
        return True
    if caption and AMENITY.search(caption):
        return False
    return "Bathroom" in types and bool(types & {"Bedroom", "Kitchen"})


def main() -> None:
    idx_path = DATA / "units_index.json"
    index = json.loads(idx_path.read_text()) if idx_path.exists() and "--fresh" not in sys.argv else {}
    files = sorted((DATA / "matterport").glob("*.json"))
    t0, n = time.time(), 0
    for f in files:
        mid = f.stem
        if mid in index:
            continue
        try:
            raw = json.loads(f.read_text())
            plan = load_plan(mid, raw)
            res = analyze(plan, layers=False)
            s = summary(plan, res)
            s["defurnished"] = (raw.get("defurnished") or {}).get("id")
            s["panos"] = len(raw.get("panoLocations") or [])
            index[mid] = s
        except Exception as e:  # noqa: BLE001
            index[mid] = {"model_id": mid, "has_graph": False, "error": str(e)[:200]}
        n += 1
        if n % 200 == 0:
            print(f"scored {n} in {time.time() - t0:.0f}s", flush=True)
            idx_path.write_text(json.dumps(index))
    idx_path.write_text(json.dumps(index))

    rows = json.loads((DATA / "buildings.json").read_text())
    n_inside = 0
    for b in rows:
        b["building_scores"] = b["scores"]
        scored = []
        for u in b["units"]:
            s = index.get(u["model_id"])
            if not s:
                u["status"] = "pending"
                continue
            u["status"] = "measured" if s.get("has_graph") else "no_floor_plan"
            u["caption"] = u.get("caption") or s.get("caption")
            u["dwelling"] = is_dwelling(s, u.get("caption"), b["kind"])
            if s.get("has_graph") and s.get("scores"):
                u["scores"] = s["scores"]
                u["stats"] = s["stats"]
                if u["dwelling"]:
                    scored.append((u, s))
        if scored:
            n_inside += 1
            inside = {}
            for p in PROFILES:
                best = max(scored, key=lambda t: t[1]["scores"][p]["score"])
                inside[p] = {"best": best[1]["scores"][p]["score"], "best_model": best[0]["model_id"],
                             "share_pass": round(sum(1 for _, s in scored if s["scores"][p]["score"] >= 70) / len(scored), 2),
                             "confidence": best[1]["scores"][p]["confidence"]}
            b["inside"] = {"units_measured": len(scored), **inside}
            b["scores"] = {}
            for p in PROFILES:
                bs = b["building_scores"][p]
                conf = min(bs["confidence"], inside[p]["confidence"], key=ORDER.get)
                b["scores"][p] = {"score": round(0.5 * bs["score"] + 0.5 * inside[p]["best"]), "confidence": conf}
        else:
            b["inside"] = None
            b["scores"] = {p: {"score": v["score"], "confidence": "low"} for p, v in b["building_scores"].items()}
    (DATA / "buildings_scored.json").write_text(json.dumps(rows))
    graphs = sum(1 for s in index.values() if s.get("has_graph"))
    print(f"units indexed: {len(index)} ({graphs} with a wall graph); buildings with inside scores: {n_inside} of {len(rows)}")


if __name__ == "__main__":
    main()
