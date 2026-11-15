"""Run the three demo requests (the chat panel's starters) through the planned mode once and cache them.

Planned answers take one to two minutes live; a cached run replays its real steps in a few seconds and is
labeled as cached in the app. Delete data/agent_cache/ (or set NAVEASY_DEEP_CACHE=off) to run live again.

    python scripts/cache_deep_runs.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import main  # noqa: E402,F401  (loads .env)
from backend.app import deep  # noqa: E402

STARTERS = [
    "I use a power wheelchair and my wife is hard of hearing. Find us a place near the Medical Center under $2,000.",
    "I'm moving from 1200 W 7th St, Los Angeles to Houston. I use a wheelchair.",
    "My mother is 82 and tires quickly. Find her a home in Midtown.",
]

if __name__ == "__main__":
    for q in STARTERS:
        ctx = {"city": "houston", "profiles": ["wheelchair"], "request_id": f"cache{time.time_ns()}"}
        path = deep.CACHE / f"deep_{deep._key(q, ctx)}.json"
        if path.exists() and "--force" not in sys.argv:
            print("cached already:", q)
            continue
        path.unlink(missing_ok=True)
        t = time.time()
        r = deep.run(q, ctx)
        print(f"{time.time() - t:.0f}s  {len(r['steps'])} steps  {q}\n  {r['text'][:300]}\n")
