# NavEasy API contract

The frontend and backend agree on these shapes. Today every endpoint returns the
**synthetic** files in `frontend/public/mock/` (made by `scripts/make_mock.py`);
real data will arrive in the same shapes, so UI code doesn't change.

- Base URL (dev): `http://localhost:8000` — set `VITE_API_BASE` in `frontend/.env.local`.
  Without it, the frontend reads `/mock/*.json` directly.
- `profiles` is a comma list of `wheelchair`, `blind`, `deaf`, `older`.
- Every score is `{ "score": 0-100, "confidence": "low" | "medium" | "high" }`.
- Several profiles combine by `min` (weakest need decides, default) or `avg`.
- Map coordinates are WGS84 `lat` / `lon`. Floor plans are in **meters, y pointing up**.

## Endpoints

| Method and path | Returns | Mock file |
| --- | --- | --- |
| `GET /health` | `{ ok, mode }` | – |
| `GET /map/buildings?profiles&combine&passing_only&min_score` | Building dots with per-profile scores and `combined` | `buildings.json` |
| `GET /map/hexes?profiles&combine` | H3 res-9 cells with scores, `combined`, and minutes to essentials per profile | `hexes.json` |
| `GET /buildings/{id}` | One building + `components`, `units[]` (Matterport model ids), `nearest[]` essentials | `buildings.json` |
| `GET /units/{model_id}/heatmap?profile&furnished` | Floor plan, doors, rooms, problems and one heat grid | `unit_u_sample_001.json` |
| `GET /routes?building_id&category&profile` | Route with colored stretches and hard spots | `route_b000_pharmacy_wheelchair.json` |
| `GET /furnish/score?model_id&profiles` | Current furnishing score + suggestions | `furnish_score_u_sample_001.json` |
| `POST /furnish/plan` | Placement plan for a target unit | `furnish_plan_u_sample_001.json` |
| `POST /relocate` | Ranked neighborhoods in the other city | `relocate_sample.json` |
| `POST /agent/chat` | Agent text + UI actions | `agent_reply_sample.json` |
| `POST /voice/token` | Short-lived Gemini Live token (see `docs/VOICE.md`) | – (503 until `GEMINI_API_KEY` is set) |

## Building

```json
{
  "id": "b000", "kind": "apartment_community", "name": "Sample Lofts 1",
  "lat": 29.7437, "lon": -95.3795, "stories": 4, "elevator": true, "walkup": false,
  "year_built": 1998, "rent_min": 1350, "rent_max": 2500, "units_scanned": 3,
  "scores": { "wheelchair": {"score": 82, "confidence": "high"}, "blind": {...}, "deaf": {...}, "older": {...} },
  "combined": { "score": 61, "confidence": "low" }
}
```

## Hex cell

```json
{ "h3": "8944...", "scores": { "wheelchair": {...} }, "combined": {...},
  "minutes": { "wheelchair": { "hospital": 14.2, "clinic": 8.1, "pharmacy": 6.8, "grocery": 9.0,
                               "transit": 4.1, "restaurant": 5.3, "school": 10.4, "park": 7.7 } } }
```

## Unit (apartment) heat map

```json
{
  "model_id": "u_sample_001", "caption": "2BR, 1BA - 667SF", "units": "meters",
  "bounds": [x0, y0, x1, y1],
  "walls":  [[x1, y1, x2, y2], ...],            // door gaps already cut out
  "doors":  [{ "id": "...", "x": 4.5, "y": 4.8, "angle_deg": 0, "width_m": 0.71,
               "est_clear_in": 26.0, "threshold_m": 0.0, "kind": "doorway", "status": "fail" }],
  "windows": [{ "x": 0, "y": 1.8, "angle_deg": 90, "width_m": 1.2 }],
  "rooms":  [{ "id": "r4", "type": "Bathroom", "polygon": [[x, y], ...] }],
  "furniture": [{ "id": "f1", "label": "sofa", "rect": [x, y, w, d], "room": "r1" }],
  "turning_circles": [{ "x": 2.6, "y": 1.2, "r": 0.76 }],
  "problems": [{ "id": "p1", "severity": "high", "profile": "wheelchair", "x": 4.5, "y": 4.8,
                 "text": "Bathroom door about 26 in clear; a wheelchair needs 32 in." }],
  "heat": { "cell_m": 0.1, "origin": [x0, y0], "width": 86, "height": 72, "values": [[...], ...] }
}
```

`heat.values[row][col]` (row 0 = lowest y):

| Value | Meaning | Suggested color |
| --- | --- | --- |
| 0 | Outside the home | transparent |
| 1 | Wall, fixture or furniture | grey |
| 2 | Free floor a 32 in wheelchair can't reach | red |
| 3 | Reachable at 32 in but not 36 in | amber |
| 4 | Reachable at 36 in (ADA walkway width) | green |

Door `status`: `pass` (≥ 32 in estimated clear width), `tight` (30–32 in), `fail` (< 30 in).
Estimated clear width = scanned opening − 5 cm, because scans look like rough openings.

## Route

```json
{ "profile": "wheelchair", "minutes": 19.8, "meters": 1310, "crossings": 6, "confidence": "medium",
  "stretches": [{ "coords": [[lon, lat], ...], "status": "green" | "amber" | "red" | "grey", "why": "..." }],
  "hard_spots": [{ "lon": -95.37, "lat": 29.74, "type": "no_lowered_curb", "text": "...", "image_url": null }] }
```

## Furnishing

- Score: `{ model_id, profiles, score_empty, score_furnished, suggestions: [{ item_id, action: "move"|"remove"|"replace"|"rotate", to?, gain, text }] }`
- Plan request: `{ target_model_id, profiles, inventory: [{ label, w, d }], preferences: { transfer_side, works_from_home, keep_layout } }`
- Plan response: `{ target_model_id, profiles, score_before, score_after, placements: [{ item_id, label, room, rect, rotation_deg }], wont_fit: [{ label, reason }], reasons: [] }`

## Relocation

Request `{ origin: { lat, lon } | { address }, profiles, must_haves: [{ category, max_minutes }], budget }`;
response `{ origin, profiles, candidates: [{ h3, name, match, comparison: { pharmacy: [now, there], ... }, homes: [building ids], reason }] }`.

## Agent

Request `{ messages: [{ role, content }], session_id }`; response `{ text, ui_actions: [...] }` where each action is one of:
`set_map_view {center: [lon, lat], zoom, profiles}`, `highlight_buildings {ids}`, `show_route {route_id}`,
`open_unit {model_id, problem_id}`, `open_tour_at {model_id, scan_id}`.
