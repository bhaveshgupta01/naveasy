# NavEasy

**Can I live here independently?** NavEasy scores homes, buildings, streets and neighborhoods in
Houston and Los Angeles for four needs (wheelchair, blind or low vision, deaf or hard of hearing,
older adults or low stamina), plans furniture so a person can move through their home alone, and
finds a matching neighborhood when they move cities.

Built at HackPrinceton Fall 2026 on CoStar's "Inside and Out: AI for the Built World" track, using
CoStar data, Matterport 3D scans and Vexcel aerial imagery, plus OpenStreetMap and Valhalla routing.

The core idea: we measure the inside of every scanned home automatically (doorway widths,
thresholds, turning room) from Matterport's wall graph, then connect it to the building
(elevator or walk-up) and to accessible routes to everyday places.

## What works today (Nov 14)

| Feature | Status | Numbers from the real data |
| --- | --- | --- |
| Building scores | Working, both cities | 4,670 buildings: 2,765 apartment communities and 1,905 homes |
| Apartment heat maps (all four needs) | Working for every scan that has a wall graph | 13,771 scans indexed; 6,551 measured inside; 66,000+ doorways |
| Locality heat map | Working; Houston has routed walking times in Midtown and street data inside the loop, the rest is estimated and says so | 18,788 Houston cells, 6,500 LA cells |
| Routes | Working (Valhalla + OpenStreetMap crossings, curbs, signals, benches; slope measured from Vexcel heights; Vexcel aerial photo and crosswalk check per hard spot) | Curb detail only in central Houston |
| Furniture finder | Working on scans with a furniture-removed view; labels confirmed by hand for the hero homes | 3 homes; the lived-in one is 7833 Narcissus St (11 pieces) |
| Furnishing score and planner | Working | Hero plan scores 91; of 15 ordinary layouts of the same pieces, 7 block a path and drop to about 48 |
| Relocation match | Working, LA to Houston and back | |
| Aerial 3D map | Working: Vexcel aerial tiles as the base map and Vexcel surface heights as 3D terrain past zoom 15 (both proxied and cached; the key stays on the server), rotate and tilt, score pillars | Midtown pre-loaded |
| Outside of a home in 3D | Working for every building from Vexcel surface heights + aerial photo; drone Gaussian splat viewer for the homes that have one (checked: renders the home in detail) | 230 buildings with a splat |
| Zoom into a drone model | Homes with a drone model carry a yellow ring and a "Drone 3D" label on the map. Zoom past 18 with one near the middle of the screen and the map hands over to its drone model, starting at the map's tilt; zoom far out of it (or press Escape or "Back to the map") to return. `scripts/prefetch_splats.py` pre-loads the ones near an area | 198 Houston homes |
| Angled photos around a home | Working (Vexcel obliques, four directions) | |
| Matterport 3D tour with problem pins | Working: pins sit on the measured doorways and clicking a problem flies the camera to it | localhost only |
| Guided demo | Working: seven steps that run live (`?panel=story`) | |
| Voice mode | Working, on open-source speech that runs locally: Whisper (`base.en`, via faster-whisper) hears and Piper (`en_US-lessac-medium`) speaks, so nothing said leaves the machine. Hold Space or tap, speak, and the answer is read aloud while the app moves behind it (press V). Checked in a real browser: three spoken questions heard correctly and answered in about 4 s each. Falls back to the browser's speech if the models aren't installed | Not yet tried with a physical microphone |
| Understanding free speech | Working on a local open-source language model through Ollama (`qwen2.5:7b`; `backend/app/brain.py`). It turns what you say into a command (needs, budget, place, qualities such as safe streets or near a bus stop) and words answers from retrieved facts; it never supplies facts. About 4 s per turn on an M-series Mac. Without Ollama it falls back to keyword rules | `ollama pull qwen2.5:7b` |
| Questions about what's on screen | Working: the app sends a snapshot of what is shown (home, unit, route, list, moving results) and the assistant answers from those facts only, or says it isn't on screen (`backend/app/screen.py`) | |
| Spoken home descriptions | Working: layout from the entry door, room sizes, window directions and sun (from Matterport's compass alignment), doorways, problems (`backend/app/narrate.py`) | |
| Agent | Tool-calling Claude agent written but **not yet run with a key**; a rule-based fallback answers without one | Needs `ANTHROPIC_API_KEY` |
| Voice | Browser speech in and out in the chat panel; Gemini Live token endpoint written, **untested** | Needs `GEMINI_API_KEY` |

Known gaps: built-in fixtures (toilet, tub, counters) are not in the scan geometry, so bathroom and kitchen checks are "before fixtures";
building approach (entrance steps, parking) is not checked yet; LA has no street-level data cached; LangChain deepagents and Photon are not wired.

## Repo layout

| Path | What |
| --- | --- |
| `docs/TECHNICAL_DESIGN.md` | The full design: features, scoring formulas, data model, scope |
| `docs/API_CONTRACT.md` | Shapes the frontend and backend agree on |
| `pipeline/buildings.py` | CoStar kit to the building table and building scores |
| `pipeline/fetch_geometry.py` | Pulls and caches Matterport geometry for every scan |
| `pipeline/unit.py` | The apartment engine: wall graph to 5 cm grid to heat maps, checks and scores for four needs |
| `pipeline/score_units.py` | Scores every cached scan and folds the results into the buildings |
| `pipeline/osm.py`, `pipeline/locality.py` | OpenStreetMap cache and the hex heat map |
| `pipeline/routes.py` | Route A to B with stretch colors and hard spots |
| `pipeline/furniture.py`, `pipeline/furnish.py` | Furniture finder (furnished vs furniture-removed panoramas) and the furnishing scorer and planner |
| `pipeline/relocate.py` | Relocation match (between cities or within one) |
| `pipeline/terrain.py` | Vexcel surface heights as terrain tiles; ground height and slope along a route |
| `pipeline/furnished_scan.py` | Finds which scans are furnished (cheap two-panorama check) |
| `tests/` | Engine and scoring tests on a hand-built plan: `python -m pytest tests` |
| `pipeline/splat.py` | Crops Matterport's aerial Gaussian splat to the home and its lot |
| `backend/app/main.py`, `backend/app/agent.py` | FastAPI app and the agent |
| `frontend/` | React + Vite + MapLibre app |

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # fill in your own keys (see the Team Access Kit); unzip the CoStar kit into costar-kit/
```

Build the data once (about 25 minutes, mostly downloads; every step caches and can be rerun):

```bash
python -m pipeline.buildings                 # building table
python -m pipeline.fetch_geometry all        # Matterport geometry, 3 workers
python -m pipeline.score_units               # unit scores + buildings_scored.json
python -m pipeline.osm pois houston && python -m pipeline.osm pois la && python -m pipeline.osm streets
# transit stops (optional): stops.txt from the Houston METRO feed (metro.resourcespace.com/pages/download.php?ref=4835&ext=zip)
# and LA Metro (gitlab.com/LACMTA/gtfs_bus, gtfs_rail) go in data/gtfs/houston_stops.txt and data/gtfs/la_stops.txt
python -m pipeline.locality houston --live && python -m pipeline.locality la
python -m pipeline.furniture fetch <model_id> && python -m pipeline.furniture find <model_id>   # per furnished hero home
```

Speech models (open source, run locally; needs `ffmpeg`). Whisper downloads itself on first use; fetch the Piper voice once:

```bash
mkdir -p data/speech && cd data/speech
B=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium
curl -LO $B/en_US-lessac-medium.onnx && curl -LO $B/en_US-lessac-medium.onnx.json
```

The assistant's understanding uses a local model through Ollama: `ollama pull qwen2.5:7b` (4.7 GB). Set `NAVEASY_LOCAL_LLM` to pick another model, or to `off` for keyword rules only.

Run it:

```bash
./run.sh                                     # API on :8000 and the web app on http://localhost:5173
python scripts/prewarm_3d.py <lat> <lon> <km>   # optional: pre-load 3D tiles for an area you will demo
python -m pytest tests                       # 23 tests, under a second, no network
```

Useful demo links: `?needs=wheelchair&unit=5d9pvDHZMhC` (narrow bathroom door), `?unit=yQsPwmzZscg&tab=plan` (furnishing plan),
`?unit=yBnqSBNHtTb&tab=score` (furniture found in a scan), `?home=p9180689&route=pharmacy` (route), `?panel=move` (relocation),
`?home=p9180689&outside=1` (orbit the building in 3D), `?home=h238672879&outside=1` (drone splat).

## Data and keys: never commit them

- Keys live in `.env` (template: `.env.example`). Each teammate gets their own with `/costar-keys` in the
  `#costar-group` Discord channel.
- The CoStar data kit goes in `costar-kit/`; generated files go in `data/`. Both are gitignored: the data is
  licensed for hackathon use only.
- Run `git status` before every push and make sure none of `.env`, `costar-kit/`, `data/`, `*.zip`, `*.rtf` appear.

## Team workflow

- `main` stays runnable. Work on a branch (`ui/...`, `pipeline/...`, `backend/...`) and open a pull request.
- If you change a response shape, update `docs/API_CONTRACT.md`, `frontend/src/types.ts` and `scripts/make_mock.py`
  in the same pull request.

Imagery © Vexcel Imaging US, Inc. · Map data © OpenStreetMap contributors · 3D content © Matterport.
