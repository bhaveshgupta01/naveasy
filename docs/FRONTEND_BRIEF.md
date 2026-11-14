# Frontend brief (for whoever builds the interface)

You can build the whole interface without keys or a backend: `frontend/` reads the
synthetic files in `frontend/public/mock/`. When the backend is ready, set
`VITE_API_BASE=http://localhost:8000` in `frontend/.env.local` and nothing else changes.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

## What already works (starter)

- `src/App.tsx`: profile menu (4 needs, multi-select), "weakest need decides" vs "average",
  "only homes that pass", a ranked list, the map, and a building panel.
- `src/components/MapView.tsx`: MapLibre map (free OpenFreeMap tiles) with building dots colored by score.
- `src/components/FloorPlan.tsx`: canvas floor plan with the heat grid, walls, room names and doorway widths.
- `src/types.ts`: the data types. Treat it and `docs/API_CONTRACT.md` as the source of truth.

## Screens to build (in priority order)

1. **City map polish:** hex heat layer from `hexes.json` (deck.gl `H3HexagonLayer`), legend, grey "no data"
   cells, hatching for low confidence, profile colors: wheelchair `#d9363e`, blind `#7b4fd6`, deaf `#2f6fd6`,
   older adults `#2e9e5b`. With several needs selected, switch to one neutral ramp (never mix hues).
2. **Building panel:** score breakdown (`components`), units list with mini floor plans, rent, photos placeholder,
   buttons "Route to pharmacy / clinic / grocery / bus stop / school / hospital".
3. **Route view:** draw `route_*.json` stretches as colored lines; pins on `hard_spots`; minutes, crossings,
   confidence.
4. **Apartment view:** bigger floor plan, profile switch, furnished/empty toggle, problems list; clicking a
   problem highlights it on the plan (later: opens the 3D tour).
5. **Furnishing tab:** score before/after, suggestion cards with point gains, plan drawn on the floor plan,
   won't-fit list (`furnish_*.json`).
6. **Relocation screen:** LA address input, split map, comparison table, top neighborhoods (`relocate_sample.json`).
7. **Agent chat + voice button:** chat panel that applies `ui_actions` from `agent_reply_sample.json`;
   a mic button wired later to Gemini Live (`docs/VOICE.md`).
8. **3D tour (later):** Matterport Showcase SDK embed. Needs `VITE_MATTERPORT_SDK_KEY` in
   `frontend/.env.local`; the key only works on `localhost`.

## Non-negotiable accessibility rules (we're an accessibility product)

- Every map has a list view of the same items; everything works with the keyboard.
- Never use color alone: show the number and a word ("82, meets wheelchair checks"), add patterns for low confidence.
- Screen-reader labels on map features, charts and the floor plan canvas (`aria-label` summary).
- Visible focus rings, high contrast, respect `prefers-reduced-motion`.
- Wording: "meets our wheelchair checks", never "accessible" or "compliant" as a guarantee.

## Working with ChatGPT

- Paste `docs/API_CONTRACT.md`, `src/types.ts` and the one file you're changing; ask for one component at a time.
- Keep shapes from the contract; if you need a new field, add it to `types.ts` and tell the backend person.
- Don't put API keys in frontend code (only the Matterport SDK key, via `.env.local`).
- Commit on a branch (`ui/<feature>`) and open a pull request into `main`.

## Demo beats the UI must support (2 minutes)

1. LA address → three Houston neighborhoods (relocation).
2. Houston map, wheelchair layer; add "deaf" → weakest-need view; zoom into Midtown.
3. Tap a home → wheelchair route to a pharmacy with one hard spot.
4. Open the apartment → doorways measured, the narrow bathroom door in red.
5. Furniture → score before/after, won't-fit list.
