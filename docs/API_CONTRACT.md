# NavEasy API contract

The frontend and backend agree on these shapes. The API serves real data built by the pipeline
(see the README); if `data/` hasn't been built it falls back to the synthetic files in `frontend/public/mock/`.

- Base URL (dev): `http://localhost:8000` (override with `VITE_API_BASE` in `frontend/.env.local`).
- `profiles` is a comma list of `wheelchair`, `blind`, `deaf`, `older`. `city` is `houston` or `la`.
- Every score is `{ "score": 0-100, "confidence": "low" | "medium" | "high" }`.
- Several profiles combine by `min` (weakest need decides, default) or `avg`.
- Map coordinates are WGS84 `lat` / `lon`. Floor plans are in **meters, y pointing up**.

## Endpoints

| Method and path | Returns |
| --- | --- |
| `GET /health`, `GET /meta` | Mode, which keys are set, counts per city, hero ids |
| `GET /map/buildings?city&profiles&combine&passing_only&min_score&max_rent` | Building dots with per-profile scores, `combined`, `measured_inside` |
| `GET /map/hexes?city&profiles&combine` | Compact rows `[h3, combined score, confidence 0-2, live 0/1, index of the weakest need]` |
| `GET /map/hexes/{cell}?city` | One cell: scores, minutes to essentials per profile, street measures, `live` |
| `GET /buildings/{id}?profiles` | Building + `components` (value and why), `building_scores`, `inside`, `units[]` with scores and top problems, `nearest[]`, `checklist[]` |
| `GET /units/{model_id}/heatmap?profile&furnished&floor&chair_cm` | Floor plan, doors, rooms, furniture, turning circles, paths, problems, checks, scores and one heat grid |
| `POST /units/heatmap` | The same for a custom furniture layout `{ model_id, profile, furniture[] }` |
| `GET /units/{model_id}/floorplans` | Matterport's own 2D floor plans for any scan, even one without a wall graph: `[{ floor, label, kind: "schematic" or "photo", url }]` |
| `GET /units/{model_id}/floorplan/{name}` | One of those images (PNG, cached) |
| `GET /routes?building_id&category&profile` (or `from_lat, from_lon, to_lat, to_lon`) | Route with colored stretches, hard spots, turn-by-turn steps |
| `GET /aerial?lat&lon&meters` | Vexcel top-down JPEG around a point (cached) |
| `GET /furnish/score?model_id&profiles` | Furniture found in the scan, score empty vs furnished, suggestions |
| `GET /furnish/inventory?model_id` | The pieces found in a furnished scan, snapped to standard sizes |
| `POST /furnish/plan` | Placement plan: `{ target_model_id, profiles, inventory[] or from_model_id, preferences }` |
| `POST /relocate` | Ranked neighborhoods in the other city |
| `POST /agent/chat` | Agent text + UI actions |
| `POST /voice/token` | Short-lived Gemini Live token (see `docs/VOICE.md`; 503 until `GEMINI_API_KEY` is set) |

Heat grid values mean, per profile: `wheelchair` 4 reachable at 36 in, 3 reachable at 32 to 36 in or behind a tight door, 2 not reachable;
`older` 4 within 8 m of the bed, 3 within 15 m, 2 farther or up stairs; `deaf` 4 visible from the main seat, 3 from the kitchen only, 2 neither;
`blind` 4 clear floor, 3 within 30 cm of furniture. Stairs are 2 for `wheelchair`, `older` and `blind`. The response's `legend` carries these labels. `frontend/src/types.ts` has every field.

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
  "heat": { "cell_m": 0.1, "origin": [x0, y0], "width": 86, "height": 72, "values": [[...], ...] },
  "fixtures": [[row, first_col, last_col_plus_1], ...],   // built-ins from the 3D mesh, on the heat grid
  "fixtures_source": "mesh",                             // or null when the mesh wasn't available
  "fixture_items": [{                                     // each built-in on this floor, measured from the mesh
    "id": "fx0_7", "kind": "counter", "floor": 0, "room": "r3", "room_type": "Kitchen", "x": 2.1, "y": 0.4,
    "width_m": 3.71, "depth_m": 0.99, "height_m": 0.902, "width_in": 146, "depth_in": 39, "height_in": 35.5,
    "shape": "straight", "angle_deg": 0.0, "area_m2": 2.6, "polygon": [[x, y], ...], "at_wall": true,
    "possibly_movable": false, "floor_ref": "mesh",
    "measured": "width, depth and height measured from the 3D mesh", "kind_source": "inferred from room, size and height" }],
  "defurnished_id": "teat3htgur5a5tzxus3xz552c"          // Matterport's furniture-removed view, or null
}
```

**Measured built-ins** (`fixture_items`). Each patch of the built-in mask is split into seat-height (under 62 cm) and
counter-height parts and measured: width and depth from its minimum rotated rectangle (L or U shaped counters report
run length by depth, `shape`), and the height of its top above the floor seen in the mesh 10 to 60 cm around it
(`floor_ref: "mesh"`; `"room"` or `"floor"` when no floor was seen there, which is less reliable). Sizes and heights are
measured; `kind` is inferred from room type, size and height: `counter`, `island`, `vanity`, `toilet`, `toilet tank`,
`tub`, `shelf or ledge`, `table`, `counter or appliance`, `low surface`, `low built-in`. In a public building (no
bedroom and no kitchen) level surfaces in classrooms, offices, halls and landings are listed too, as `desk or table`,
`bench or seat`, `fixed seating or bench`, `high table or counter`, `counter` or `raised floor or platform`, most with
`possibly_movable: true`; they are listed only, not added to the blocked floor. Calibration: ceilings measured the same
way come out 0 to 2% under 8, 9 and 10 ft, so heights are good to about an inch.

**Counter and sink heights** (wheelchair `checks.counter_height`, weight 0: reported, not scored; present only when
measured items exist). Against the 2010 ADA Standards: the lowest kitchen counter section at least 30 in wide, 34 in
maximum (804.3, 804.3.2); each bathroom's lowest vanity, 34 in (606.3); desks and tables on a public floor, 34 in
(902.3); counters on a public floor, a 36 in section (904.4.1). Judged by the whole inch said aloud: at or under the
limit passes (1), one inch over is a `low` problem "measure it" (0.5), two or more inches over is a `medium` problem
(0). Problems have `kind: "counter_height"` and ids `height_<item id>`. Only items with `floor_ref: "mesh"` are judged.

**Furniture heights.** `furniture[]` items (from `?furnished=true`) carry `height_m` (top) and `surface_m` (main level
surface: mattress, seat, table top) from the furnished scan's mesh when it is on disk, with `height_source: "mesh"`, or
nulls. Footprints still come from the panoramas.

Built-ins (`fixtures`) come from Matterport's 50k-triangle mesh: level surfaces 30 cm to 1.3 m above the floor in
kitchens, bathrooms, laundry, pantry and utility rooms (counters, islands, vanities, toilets, tubs). They block like
walls (heat value 1), so routes go around them. Stairs inside rooms labeled Staircase are found the same way (level
surfaces climbing at least 50 cm). A scan without a wall graph returns `{ has_graph: false, message, defurnished_id }`.

`heat.values[row][col]` (row 0 = lowest y):

| Value | Meaning | Suggested color |
| --- | --- | --- |
| 0 | Outside the home | transparent |
| 1 | Wall, fixture or furniture | grey |
| 2 | Free floor a 32 in wheelchair can't reach | red, hatched |
| 3 | Reachable at 32 in but not 36 in | yellow |
| 4 | Reachable at 36 in (ADA walkway width) | green |

Built-ins (`fixtures`) are drawn brown. The exact values live in `frontend/src/colors.ts`.

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
- Plan response: `{ target_model_id, profiles, score_before, score_after, placements: [{ item_id, label, room, rect, rotation_deg, facing, why, needs, caution?, transfer_side? }], wont_fit: [{ label, reason }], reasons: [] }`.
  The layout follows each selected need's rules (Technical Design section 8): wheelchair keeps 30 x 48 in beside the bed,
  30 in in front of what you use, turning circles and 36 in ways through; blind keeps every piece 30 cm off the routes from
  the front door and against a wall; deaf puts the sofa in view of the front door with tall pieces off that line; older
  adults get the shortest walk from the bed to the bathroom and a seat near the entry. `why` is one measured sentence per
  piece naming the need it serves (`needs`); `caution` names a rule no spot could keep; `reasons` has one line per need,
  checked on the engine's analysis of the furnished home.

## Relocation

Request `{ origin: { lat, lon } | { address }, profiles, must_haves: [{ category, max_minutes }], budget }`;
response `{ origin, profiles, candidates: [{ h3, name, match, comparison: { pharmacy: [now, there], ... }, homes: [building ids], reason }] }`.

## Agent

`POST /agent/chat` is the one entry point, channel-neutral (text and state in; text, actions and state out), so a
messaging sidecar could call it the same way the web app does.

Request `{ messages: [{ role, content }], context: { channel: "text" | "voice", city, profiles, state, screen, location, request_id } }`.
`state` is whatever the previous response returned (needs, budget, the homes listed, the home and unit being discussed,
the guided conversation's step, and `history` for the Claude paths); send it back unchanged.

Response `{ text, ui_actions: [...], state, mode: "local" | "claude" | "deep", steps?: [...] }`.

- `mode`: `local` answers voice and the guided conversation on this machine (`dialogue.py`); `deep` is the planned
  mode on LangChain deepagents for multi-step home searches (`deep.py`, off with `NAVEASY_DEEP=off`); `claude` is one
  tool-calling Claude agent for other typed questions (`agent.py`).
- `steps`: what the agent did, in order: `{ kind: "plan" | "subagent" | "tool" | "check" | "note", who, text, status }`.
  `who` is `planner`, `building`, `inside`, `outside`, `checker` or `agent`. The last `check` step says how many numbers
  in the answer were found in the tool results.
- The message `__start__` starts the guided conversation (the agent asks what help is needed, then budget and area,
  reads back three homes, tours the chosen one from the entry door and plans the person's furniture).

`GET /agent/progress/{request_id}` returns `{ steps }` for an answer still being worked on (the app polls it).
`GET /agent/modes` says which modes are available.

Each action is one of `set_map_view {center: [lon, lat], zoom, profiles, city}`, `highlight_buildings {ids}`, `open_building {id}`,
`show_route {building_id, category, profile}`, `open_unit {model_id, profile, problem_id, tab, plan}`, `show_relocation {request}`,
`open_drone {id}`. Any action may carry `cue`: text from the answer; when the answer is spoken, the action runs as the
sentence containing the cue starts (the map follows a list as it is read out). Without `cue` it runs at once.
