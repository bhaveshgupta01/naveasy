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
| Scans without measured walls | Clickable: Matterport's own 2D floor plan (schematic with room areas, or a top-down photo) and the 3D tour; the app says plainly that there is no heat map | 7,220 of 13,771 scans |
| Matterport 3D tour with the heat map inside it | Working: the chosen need's heat map is drawn on the scan's floor with door widths, too-narrow door gates, turning circles, main paths and furniture (or your furniture plan), moving with Matterport's camera; walls hide what is behind them. Walking inside, the screen edges pulse red where that need can't go or is at risk and amber where it's tight, with the reason on screen; pointing at the floor previews a spot before you step there; blind mode speaks each warning. Problem pins fly the camera to each problem. A furniture plan is shown in Matterport's furniture-removed view of the scan ("Furniture removed" switch) | localhost only (SDK key). The embed SDK can't add objects to Matterport's own scene, so this is a camera-synced overlay (`frontend/src/components/tourOverlay.ts`, `frontend/src/zones.ts`) |
| Guided demo | Working: seven steps that run live (`?panel=story`) | |
| Speak button (voice) | Working: a small speak button docked beside the chat button (press V). The agent leads: it asks what help you need, then budget and area (or your current address, for somewhere like where you live now), reads back three homes and asks for one, two or three, gives a broker's summary (rent, layout, what was measured inside, minutes to places, the main weakness, what to verify before signing), tours the home from the entry door, then places your own furniture and says where each piece goes from each room's doorway. The map and panels stay visible and move as it talks (the map flies to each home as it is read out); a small caption shows only the last thing heard and said. Speech is open source and local: Whisper (`base.en`, faster-whisper) hears and Piper (`en_US-lessac-medium`) speaks. Checked in a headless browser and by script; about 1 s a turn with keyword rules, 4 s with the local model | Not yet tried with a physical microphone |
| Understanding free speech | Working on a local open-source language model through Ollama (`qwen2.5:7b`; `backend/app/brain.py`). It turns what you say into a command (needs, budget, place, qualities such as safe streets or near a bus stop) and words answers from retrieved facts; it never supplies facts. About 4 s per turn on an M-series Mac. Without Ollama it falls back to keyword rules | `ollama pull qwen2.5:7b` |
| Questions about what's on screen | Working: the app sends a snapshot of what is shown (home, unit, route, list, moving results) and the assistant answers from those facts only, or says it isn't on screen (`backend/app/screen.py`) | |
| Spoken home descriptions | Working: layout from the entry door, room sizes, window directions and sun (from Matterport's compass alignment), doorways, problems (`backend/app/narrate.py`) | |
| Agent | Working, three paths behind one entry point (`POST /agent/chat`): voice and the guided conversation run locally; typed multi-step home searches run as a planned LangChain deepagents run (a planner with a to-do list and four subagents: building from CoStar, inside from Matterport, outside from Vexcel and routes, and a checker that re-runs failing findings and marks the rest "needs a site visit"); other typed questions go to one Claude agent (`claude-opus-5-5`) with seven tools. The chat shows the plan, each subagent's task and every tool call, live and under the answer, and every number in an answer is matched against the tool results | Single agent about 12 s; planned run about 85 s live (the three demo requests are cached: `python scripts/cache_deep_runs.py`); `NAVEASY_DEEP=off` turns the planned mode off |
| Gemini Live | Token endpoint works; a Live session with `gemini-3.8-live` (v1alpha) answered in 0.7 s from Python. Not wired into the browser; local speech stays the default (`docs/VOICE.md`) | `GEMINI_API_KEY` (or `Gemini`) |

Built-ins (counters, kitchen islands, vanities, toilets, tubs) and the stairs themselves come from Matterport's 3D mesh, fetched
the first time a home is opened (`python -m pipeline.fixtures <model_id>`; `python -m pipeline.score_units --meshes` rescores the
homes whose mesh is cached). In a furnished scan a kitchen table can be counted as a built-in.

Colors: the apartment floor-plan heat map (2D and the 3D tour floor) is red / yellow / green with brown built-ins, and
problem cells are hatched. The neighborhood map shades each need in its own hue, never red or green (wheelchair blue,
blind plum, deaf pink, older adults gold; checked for color-blind separation). Building markers keep green / orange /
red score bands (75+, 50-74, under 50; yellow when selected), and the "Only homes scoring 75 or more" filter matches
the green band. All values are in `frontend/src/colors.ts`.

Known gaps: building approach (entrance steps, parking) is not checked yet; LA has no street-level data cached; Photon is not wired.

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
| `pipeline/fixtures.py` | Built-ins (counters, islands, toilets, tubs) and stairs from Matterport's 3D mesh |
| `pipeline/furnished_scan.py` | Finds which scans are furnished (cheap two-panorama check) |
| `tests/` | Engine and scoring tests on a hand-built plan: `python -m pytest tests` |
| `pipeline/splat.py` | Crops Matterport's aerial Gaussian splat to the home and its lot |
| `backend/app/main.py`, `backend/app/agent.py`, `backend/app/deep.py`, `backend/app/dialogue.py` | FastAPI app; the agent's router and Claude agent; the planned deepagents mode; the local, agent-led conversation |
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
python -m pytest tests                       # under two seconds, no network
```

Useful demo links: `?needs=wheelchair&unit=5d9pvDHZMhC` (narrow bathroom door), `?unit=yQsPwmzZscg&tab=plan` (furnishing plan),
`?unit=yBnqSBNHtTb&tab=score` (furniture found in a scan), `?home=p9180689&route=pharmacy` (route), `?panel=move` (relocation),
`?home=p9180689&outside=1` (orbit the building in 3D), `?home=h238672879&outside=1` (drone splat),
`?unit=5d9pvDHZMhC&tab=tour` (zone alerts in the 3D tour: walk to the bathroom door), `?unit=TAJdsi5EGmq&tab=tour&needs=blind` (furnished home, blind).

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
