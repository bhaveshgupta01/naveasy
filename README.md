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

## Repo layout

| Path | What |
| --- | --- |
| `docs/TECHNICAL_DESIGN.md` | The full design: features, scoring formulas, data model, scope |
| `docs/API_CONTRACT.md` | Shapes the frontend and backend agree on |
| `docs/FRONTEND_BRIEF.md` | Screens to build, accessibility rules, how to work with GPT |
| `docs/VOICE.md` | Gemini Live voice plan (free tier) |
| `frontend/` | React + Vite + MapLibre app; runs on sample data with no keys |
| `frontend/public/mock/` | **Synthetic** sample data (no licensed data) |
| `backend/` | FastAPI app serving the contract (sample data for now) |
| `pipeline/` | Clients for Matterport, Vexcel, OpenStreetMap and Valhalla; `floorplan.py` turns a real scan into a heat map |
| `scripts/make_mock.py` | Regenerates the sample data |

## Quick start

Interface only (no keys needed):

```bash
cd frontend && npm install && npm run dev
```

Backend and pipeline:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # fill in your own keys (see the Team Access Kit)
uvicorn backend.app.main:app --reload --port 8000
python -m pipeline.floorplan <matterport_model_id>   # writes data/units/<id>.json and .png
python scripts/make_mock.py                          # regenerate sample data
```

Point the frontend at the backend with `VITE_API_BASE=http://localhost:8000` in `frontend/.env.local`.

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
