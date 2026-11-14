# NavEasy Technical Design

Nov 14, 2026 · @Bhavesh

NavEasy helps people with disabilities choose where to live in Houston and Los Angeles. It scores localities, routes, buildings and apartments for four accessibility needs; plans furniture so a person can move through their home independently; and finds a matching neighborhood when they relocate.

## 1. Overview

NavEasy answers one question: "Can I live here independently?" It answers at four zoom levels (locality, route, building, apartment) for four needs, then adds two planners: one for furniture, one for moving to a new city.

**The three features:**

| Feature | What the user gets | Main data |
| --- | --- | --- |
| 1. Accessibility heat maps | A heat map per need at four levels: localities (distance to hospitals, restaurants, bus stops, schools and more), routes from A to B, buildings, and the inside of each apartment with its current furniture and rooms | OpenStreetMap, Valhalla routing, Vexcel imagery, CoStar buildings, Matterport floor plans |
| 2. Furnishing planner | For a new apartment: what furniture to use and where to put it, around fixed elements (kitchen, bathroom, walls), so the user can move through the home alone. For a furnished apartment: a current furnishing score and how to improve it | Matterport wall graph, furnished and furniture-removed panoramas, ADA clearances |
| 3. Relocation match | Moving from LA to Houston (or anywhere in reverse): neighborhoods in the new city where the user's essentials are as close and as reachable as at home, with homes that fit | Locality scores from Feature 1, Vexcel look-alike search, CoStar listings |

**The four accessibility profiles** (users can select one or several):

| Profile | Map color | Who |
| --- | --- | --- |
| Wheelchair | Red | Manual and power wheelchair users; also walkers and scooters |
| Blind or low vision | Purple | People who navigate by cane, guide dog, audio or magnification |
| Deaf or hard of hearing | Blue | People who rely on visual information and sightlines |
| Older adults or low stamina | Green | People for whom distance, slopes, stairs and standing time are the barrier |

**Who uses it:** the person with the need, and the family members, occupational therapists and case managers who help them search. Secondary users are CoStar's own customers: property managers and brokers who want to market accessible units honestly, and cities that want to see where accessible housing and accessible streets don't meet.

**Scope:** Houston and Los Angeles, limited to what CoStar scanned:

| Inventory | Houston | Los Angeles |
| --- | --- | --- |
| Apartment communities | 1,348 | 1,417 |
| Home listings (Homes.com) | 1,700 | 420 |

Across both cities there are about 9,400 scanned apartment units and 1,938 scanned homes.

**Success for the hackathon:**

- A judge goes from the city map to a measured doorway inside a real apartment in under 60 seconds.
- Every Houston apartment community has a wheelchair score, with its evidence.
- 5 to 10 hero apartments have full inside heat maps, a furnishing score and a furnishing plan.
- An LA address returns three Houston neighborhoods with explained matches.

**Not in scope:** certifying ADA compliance (we estimate and show evidence; we never guarantee), buildings without a scan, and real-time conditions such as broken elevators or construction.

## 2. Data sources

Six sources, all reachable today. CoStar, Matterport and Vexcel come from the hackathon keys; OpenStreetMap and Valhalla are free and need no key. Everything marked "tested" was called on Nov 13 or 14 with real Houston or LA locations.

| Source | What we use it for | Access | Status |
| --- | --- | --- | --- |
| CoStar commercial properties | 3,339 properties, 2,765 of them apartment communities: location, stories, walk-up flag (known for 2,165), passenger elevators, construction year, parking, photos, links to Matterport scans | Kit file `commercial-property.jsonl` | Tested |
| CoStar apartment listings | 1,747 communities on Apartments.com: rent and units available per floor plan | `apartment-listing.jsonl` | Tested |
| CoStar residential listings | 2,120 Homes.com listings for 1,867 homes: beds, baths, size, stories, elevator and accessibility flags, photos with scene labels (Bathroom, Stairs, Porch) | `residential-listings.jsonl` | Tested |
| CoStar listing features | Feature text for 1,899 listings; "accessible" appears in 90, "handicap" in 37, "ramp" in 25, "wheelchair" in 18 | `residential-listing-features.jsonl` | Tested |
| CoStar tenants and occupancy | Who works in which building; lets a user name their workplace as a destination | `location-occupancy.jsonl`, `company-detail.jsonl` | Tested |
| Matterport Model API | Rooms (type, outline, size), floors, wall graph in meters with walls, doorways, openings and windows (width, height, threshold), panorama positions and 2K/4K images, floor plan images and CAD files, AI description, a furniture-removed view of the same panoramas | GraphQL, `api.matterport.com/api/models/graph`, Basic auth | Tested on 30+ models |
| Matterport MatterPak | Textured mesh and point cloud of the space | Per Matterport's quickstart | Not tested |
| Matterport Showcase SDK | Embed a 3D tour, move the camera, pin tags at 3D positions | JavaScript SDK, key valid on localhost only | Not tested |
| Vexcel imagery API | Parcel and building footprint, ground elevation, top-down and oblique photos at about 7.5 cm, object detection by name (found crosswalks), look-alike search by text, place or image | REST, `api.vexcelgroup.com/v2`, `X-API-KEY` | Tested |
| OpenStreetMap (Overpass API) | Places (hospitals, clinics, pharmacies, schools, restaurants, supermarkets, bus stops, parks), sidewalks, crossings, curbs, tactile paving, audible signals, benches | Public, no key, rate-limited | Tested |
| Valhalla routing (FOSSGIS public server) | Walking routes and walking-time zones by profile: `wheelchair`, `blind` and standard walking | Public, no key, rate-limited | Tested |
| Claude (LLM and vision) | The agent, furniture labeling from panoramas, explanations | Anthropic API | Planned |

**What we found when testing:**

- Midtown Houston, within 1.2 km of one apartment: 874 mapped sidewalk segments, 1,057 crossings, 53 mapped curbs (10 lowered), 5 pharmacies, 6 clinics, 9 schools, 1 hospital.
- Valhalla returned a wheelchair route in Houston (1.31 km, 19.8 minutes against 15.5 walking) and 5, 10 and 15-minute wheelchair zones.
- Vexcel found a striped crosswalk from the air, but not sidewalks or curb ramps under trees.
- Matterport doorway widths came back for every home sampled and about half of apartment units. Object annotations were empty everywhere.
- The furniture-removed view exists for homes and apartment units but not for the office suite we tested. On lived-in homes it cleanly removes tables, chairs, rugs, a piano and wall art, and keeps built-ins such as cabinets and fireplaces.

**How the sources join:**

| From | Key | To |
| --- | --- | --- |
| CoStar property | `PropertyId` plus `MatterportModelId` (kit CSV) | Matterport model |
| CoStar home listing | `ListingId` plus `MatterportModelId` | Matterport model |
| CoStar property or listing | Latitude and longitude | Vexcel (as `POINT(lon lat)`, longitude first), OpenStreetMap and Valhalla (latitude first) |
| Matterport model | Its furniture-removed view id, from `view(type: "matterport.model.defurnished")` | The same panoramas without furniture |

## 3. System architecture

Everything slow (API calls, geometry, routing zones, furniture extraction) runs ahead of time in Python jobs and lands in one feature store. The app and the agent read precomputed scores, and call live services only for a new route or a new furnishing plan.

&#91;embedded content: NavEasy architecture · sources, precompute, store, services, clients\]

| Component | Job | Tech |
| --- | --- | --- |
| Building scores | Join CoStar properties and homes to their Matterport models and Vexcel parcels; compute building-level features | Python, pandas or DuckDB |
| Floor plan grids | Turn each model's wall graph into an occupancy grid at 5 cm; mark walls, doorways, thresholds and room types | Python, shapely, numpy |
| Furniture finder | Compare furnished and furniture-removed panoramas; label each piece; place its footprint on the plan | Python, Pillow, Claude vision |
| Locality hexes | Cover each city with H3 hex cells; for each cell, compute walking-time zones per profile and count reachable places | Python, h3, Valhalla, Overpass |
| Feature store | All tables plus cached images and API responses | DuckDB for the hackathon; PostGIS later |
| API | Serves scores, heat maps, routes, furnishing plans and relocation matches | FastAPI |
| Agent | Turns "I use a power wheelchair and work at the Medical Center" into filters, tool calls and an explained answer | LangChain deepagents with Claude |
| Furnishing solver | Places furniture under hard and soft rules, checks reachability | Python, numpy; optional OR-Tools |
| Web app | Map with profile menu, building panel, route view, apartment heat map, 3D tour | React, MapLibre GL, deck.gl, Matterport Showcase SDK |
| Voice and chat | The same agent over iMessage, WhatsApp or a phone call | Photon 2.0 |

## 4. Accessibility profiles

Each profile is a set of rules at four levels: locality, route, building and apartment. Wheelchair rules are precise and our data covers them well. Blind and deaf rules depend on things our sources barely record, so those scores carry low confidence and say so. We use the [2010 ADA Standards](https://www.access-board.gov/ada/) as design benchmarks; the ADA itself does not apply to private homes.

**Wheelchair (red)**

| Level | What matters | Rule we score against | Data | Confidence |
| --- | --- | --- | --- | --- |
| Apartment | Doors, walkways, turning room, thresholds, space beside fixtures | Door clear width 32 in (815 mm) minimum (404.2.3); walkways 36 in (915 mm) (403.5.1); turning circle 60 in (1525 mm) in bedroom, bathroom and kitchen (304.3.1); clear floor space 30 × 48 in (760 × 1220 mm) beside the bed, toilet and sink (305.3); thresholds ½ in (13 mm) maximum (303) | Matterport doorway widths and thresholds, room outlines, furniture footprints | High for geometry; medium for furniture |
| Building | Getting from the street to the unit door | Elevator, or a ground-floor unit; walk-ups fail for upper floors; ramp slope 1:12 maximum (405.2) | CoStar walk-up flag, elevators, stories; Vexcel entrance and parking images | Medium |
| Route | Continuous, smooth, gentle path | Sidewalk present; running slope 1:20 (5%) maximum and cross slope 1:48 (403.3); lowered curbs at crossings | OpenStreetMap sidewalks, curbs and crossings; Valhalla `wheelchair` profile; Vexcel crosswalks | Medium; good in central areas, thin in suburbs |
| Locality | Daily needs within reach | Essentials reachable by wheelchair route within 15 minutes | Valhalla zones, OpenStreetMap places | Medium |

**Blind or low vision (purple)**

| Level | What matters | Rule we score against | Data | Confidence |
| --- | --- | --- | --- | --- |
| Apartment | Clear, predictable paths with nothing jutting into them | Nothing protruding more than 4 in (100 mm) into a path between 27 and 80 in high (307.2); furniture against walls; few turns from the entrance to the bed, bathroom and kitchen; no level changes | Floor plan grid, furniture footprints, path turn counts | Medium |
| Building | Predictable entry | Elevator, few stairs | CoStar elevator and stories | Low |
| Route | Simple, safe crossings | Few street crossings, signalized crossings, audible signals, tactile paving, few turns | Valhalla `blind` profile; OpenStreetMap crossings. Audible-signal and tactile tags are almost absent in Houston (2 tagged within 1 km of Midtown, both "no") | Low |
| Locality | Transit and essentials without complex routes | Bus or rail stop and essentials reachable with few crossings | OpenStreetMap places; transit stops from Houston METRO and LA Metro GTFS feeds (planned; only 3 bus stops mapped within 1 km of Midtown in OpenStreetMap) | Low to medium |

**Deaf or hard of hearing (blue)**

| Level | What matters | Rule we score against | Data | Confidence |
| --- | --- | --- | --- | --- |
| Apartment | Seeing people, doors and alerts | Open sightlines: share of living area visible from the main seating spot; entry door visible from living room and kitchen; daylight (window openings) | Visibility polygons from the wall graph; window openings | Medium for sightlines |
| Building | Visual entry and alerts | Video intercom, visual alarms | Only if listing text mentions them | Low |
| Route | Visual information on the way | Signalized crossings | OpenStreetMap crossings | Low |
| Locality | Services and community | Essentials, transit, and Deaf community services nearby | OpenStreetMap places (search by name for Deaf services) | Low |

**Older adults or low stamina (green)**

| Level | What matters | Rule we score against | Data | Confidence |
| --- | --- | --- | --- | --- |
| Apartment | Short distances, no stairs, places to sit, room for grab bars | Bed to bathroom distance; stairs inside (Staircase rooms, multiple floors); seats along long paths; toilet clear space for grab bars at 33 to 36 in (609.4); thresholds as trip hazards | Floor plan grid, room types, floors | High |
| Building | Few steps from car to door | Elevator, stories, parking near the entrance | CoStar elevator, stories, parking; Vexcel parking | Medium |
| Route | Short, flat, with rests and shade | Total distance; slope; benches every few hundred meters; tree shade | Valhalla walking at a slower speed (3 km/h); OpenStreetMap benches (11 within 1 km of Midtown); Vexcel tree cover and ground elevation | Medium |
| Locality | Essentials close by | Essentials within 10 minutes at slow walking speed | Valhalla zones, OpenStreetMap places | Medium |

**Personalization:** each profile has defaults a user can tighten. Examples: a power wheelchair needs more turning room than a manual one; a person may walk 5 minutes, not 15; a guide-dog user may care more about quiet routes than tactile paving.

## 5. Feature 1a: locality heat map

Each locality gets a 0 to 100 score per profile: how quickly and safely a person with that need reaches daily essentials, plus how walkable the streets are for them. The map shows these scores on a hexagon grid, colored by the selected profile, and grey where we have no data.

**Grid.** Uber's H3 hexagons: resolution 9 (about 0.1 km² each) for neighborhood zoom, rolled up to resolution 8 (about 0.7 km²) for city zoom. We score only cells within 1.5 km of a scanned building or home; everything else stays grey and is labeled "no scan nearby". This keeps us from inventing data between points.

**Essentials and default targets** (users can reweight or add their own, such as a dialysis center or their workplace):

| Category | OpenStreetMap tags | Good within | Zero score at | Default weight |
| --- | --- | --- | --- | --- |
| Hospital or urgent care | `amenity=hospital`, `healthcare=urgent_care` | 15 min | 45 min | 0.20 |
| Clinic or doctor | `amenity=clinic`, `amenity=doctors` | 10 min | 30 min | 0.15 |
| Pharmacy | `amenity=pharmacy`, `shop=chemist` | 10 min | 30 min | 0.15 |
| Grocery | `shop=supermarket` | 10 min | 30 min | 0.15 |
| Bus or rail stop | `highway=bus_stop`, `public_transport=platform`, plus METRO and LA Metro GTFS stops | 5 min | 15 min | 0.15 |
| Restaurants and cafes | `amenity=restaurant`, `cafe`, `fast_food` (time to the 3rd nearest, for choice) | 10 min | 30 min | 0.08 |
| School | `amenity=school`, `college`, `university` | 15 min | 40 min | 0.07 |
| Park | `leisure=park` | 10 min | 30 min | 0.05 |

Travel times are by profile, not straight-line distance: Valhalla `wheelchair` for red, `blind` for purple, standard walking for blue, and standard walking at 3 km/h for green.

**How a cell is scored.** For profile p, category c and travel time t (minutes) from the cell's center to the nearest place of that category:

```latex
s_{p,c} = \min\left(1,\ \max\left(0,\ \frac{T^{zero}_c - t_{p,c}}{T^{zero}_c - T^{good}_c}\right)\right)
```

The essentials part is the weighted sum of those category scores. The street part, E (0 to 1), measures the streets themselves, weighted by profile:

| Street measure | Source | Matters most for |
| --- | --- | --- |
| Sidewalk coverage (sidewalk length over street length) | OpenStreetMap | Wheelchair, older adults |
| Share of crossings with lowered or flush curbs | OpenStreetMap `kerb` | Wheelchair |
| Crosswalks seen from the air | Vexcel object detection | Wheelchair, blind |
| Signalized crossings, audible signals, tactile paving | OpenStreetMap | Blind |
| Mean slope along the walking network | Vexcel ground elevation | Wheelchair, older adults |
| Benches per km and tree shade | OpenStreetMap, Vexcel tree cover | Older adults |

```latex
L_p = 100 \times \left( 0.7 \sum_c w_c\, s_{p,c} + 0.3\, E_p \right)
```

**Several needs at once.** When the user selects more than one profile, the combined score is the lowest of their scores. A place that is great for a wheelchair but unsafe for a blind user fails a person who is both. The map then switches to a neutral color ramp, so colors never mix. A toggle offers the average for users who prefer it.

**Confidence.** Every cell carries a confidence from data density: how many of its streets have sidewalk and curb tags, and whether transit stops came from GTFS or only OpenStreetMap. Low-confidence cells are drawn with hatching.

**What the user sees:** a heat map per selected profile; hover shows "Pharmacy 8 min, bus stop 4 min, hospital 22 min" with the route behind each. Clicking a cell zooms to the buildings in it.

**Compute plan.** Calling Valhalla once per building and profile (5, 10, 15, 30-minute zones) costs about 3,000 sites × 4 profiles = 12,000 calls for Houston. That is too heavy for the free public server in a weekend. For the hackathon, compute zones for one neighborhood (about 200 sites) and estimate the rest from straight-line distance × 1.3 detour ÷ profile speed, marked as estimates. After the hackathon, run Valhalla ourselves with a Texas map extract.

**Output table:** `locality_score(h3, profile, score, confidence, minutes_by_category, street_measures)`.

## 6. Feature 1b: route from A to B

From a home to any destination, NavEasy draws the route a person with the selected needs should take, colors each stretch by how hard it is for them, and pins the hard spots with an aerial photo. Typical destinations are the nearest pharmacy, clinic or bus stop, a school, or the user's workplace (found by company name in CoStar's tenant data).

**Steps:**

1. Geocode both ends: the home from CoStar coordinates; the destination from OpenStreetMap, CoStar tenants, or a typed address.
2. Ask Valhalla `/route` for a walking route with the profile's settings: `type: wheelchair` (avoids steps), `type: blind`, or standard walking at 3 km/h for older adults. Request two alternatives.
3. Ask Valhalla `/trace_attributes` for the attributes of each stretch along the route: sidewalk or road, crossing, surface, grade. Exact attribute names are to be confirmed against Valhalla's docs.
4. Add what Valhalla lacks: curbs, signals and tactile paving from OpenStreetMap nodes along the line; slope from Vexcel ground elevation sampled every 20 m; crosswalks confirmed by Vexcel detection at each crossing.
5. Score each stretch and each crossing per profile (rules below), then the whole route.
6. Cut a Vexcel aerial photo around each hard spot, with the detection box drawn on it.

**Stretch rules:**

| Condition | Wheelchair | Blind | Deaf | Older adults |
| --- | --- | --- | --- | --- |
| No sidewalk (route runs on the road) | Red | Red | Amber | Red |
| Running slope over 5% | Red (over 8.3%: impassable) | Amber | Green | Red |
| Crossing without a lowered or flush curb | Red pin | Amber pin | Green | Amber pin |
| Crossing of a major road without signals | Amber pin | Red pin | Amber pin | Amber pin |
| No audible signal or tactile paving at a signalized crossing | Green | Amber pin | Green | Green |
| More than 400 m without a bench | Green | Green | Green | Amber |
| Unknown (no tags) | Grey, counted as low confidence | Grey | Grey | Grey |

**Route summary shown to the user:** time and distance for their profile (Valhalla's wheelchair route through Midtown took 19.8 minutes against 15.5 walking for 1.31 km), number of crossings, the hard spots in order, the confidence, and the alternatives if they avoid a hard spot.

**Output:** `route(origin, destination, profile, geometry, minutes, meters, crossings, hard_spots[], stretch_scores[], confidence)`. Routes from each scored home to its nearest essentials are precomputed for the hero neighborhood; any other route runs live in about a second.

## 7. Feature 1c: building and apartment heat maps

The apartment heat map is NavEasy's centerpiece and the part no other product does at scale. We rebuild each scanned home as a 5 cm grid from Matterport's wall graph, add its furniture, and color every cell by whether a person with the selected need can reach it, move through it and turn in it.

### Building score

| Component | Wheelchair | Blind | Deaf | Older adults | Source |
| --- | --- | --- | --- | --- | --- |
| Vertical access: elevator, walk-up, stories, unit floor | 0.40 | 0.20 | 0.05 | 0.35 | CoStar `isWalkup`, elevators, `numStoriesAboveGrade`; unit floor from scan captions such as "16th Floor" |
| Approach: entrance steps or ramp, parking near the door, slope | 0.30 | 0.20 | 0.10 | 0.30 | Vexcel obliques and detection, Matterport exterior panoramas, Vexcel elevation |
| Evidence in the listing: accessibility flags and text, an accessible elevator in photos | 0.15 | 0.20 | 0.35 | 0.15 | CoStar `hasAccessibilityFeatures`, `hasElevator`, feature text, photo scene labels |
| Age: built before 1991 (pre-ADA and pre-Fair Housing design rules) | 0.15 | 0.40 | 0.50 | 0.20 | CoStar construction year (1,882 buildings before 1991) |

An apartment community is scored from all its scanned floor plans: the best plan's score and the share of plans that pass.

### Apartment heat map: pipeline

1. **Geometry.** Query the model's floors: vertices (2D points in meters), wall edges with thickness, openings (doorway, opening, window, divider) with width, height, threshold height and position along the wall, and rooms with type and outline. Units are meters: a scanned unit measured 8.6 × 7.2 m, about 667 sq ft against 638 listed.
2. **Rasterize** at 5 cm per cell. Walls become blocked cells at their real thickness. Doorways and openings cut gaps. Windows stay blocked but are recorded for daylight. Invisible edges (room boundaries without a wall) stay open.
3. **Fixed elements.** Kitchen counters, vanities, toilets and tubs aren't in the wall graph, but they survive in the furniture-removed panoramas. For the hackathon, place them from room-type templates (a 0.6 m counter band along the kitchen's longest walls; a 0.5 × 0.7 m toilet and a 1.5 × 0.75 m tub in the bathroom), corrected by hand on hero units. Later, detect them in the furniture-removed panoramas.
4. **Furniture.** Add footprints from the furniture finder (Feature 2), or none for an empty unit.
5. **Clearance.** A distance transform gives, for every free cell, its distance to the nearest obstacle. Twice that is the local clear width.
6. **Per-profile layers** (below), then a score.

**Thresholds on the grid:**

| Check | Metric value | Source rule |
| --- | --- | --- |
| Door clear width | 0.815 m; widths from the scan are treated as rough openings, so estimated clear width = scan width − 0.05 m | ADA 404.2.3 |
| Walkway width | 0.915 m | ADA 403.5.1 |
| Turning circle | 1.525 m diameter | ADA 304.3.1 |
| Clear floor space at bed, toilet, sink | 0.76 × 1.22 m | ADA 305.3 |
| Threshold | 0.013 m | ADA 303 |
| Protruding objects in a path | 0.10 m between 0.69 and 2.03 m high | ADA 307.2 |

**What each profile's layer computes:**

| Profile | Layer | Method |
| --- | --- | --- |
| Wheelchair | Reachable and passable floor | Inflate obstacles by half the chair width (default 0.35 m, user-set), flood-fill from the entry door. Cells reachable with clear width at least 0.915 m are green, 0.815 to 0.915 m amber, under 0.815 m red. Mark every spot where a 1.525 m circle fits, and check each bedroom, bathroom and kitchen has one. Check 0.76 × 1.22 m clear space beside the bed, toilet and sink. Flag each doorway by estimated clear width and threshold |
| Blind | Predictable paths | Shortest paths (A\*) from the entry to bed, bathroom and kitchen; count turns over 30° after simplifying the path; flag furniture within 0.3 m of a path and any level change |
| Deaf | Sightlines | From the main seat (the sofa, or the living room's center) cast 720 rays against the walls to get the visible area; report the share of the unit visible, and whether the entry door is visible from the living room and kitchen. Count windows per room for daylight |
| Older adults | Effort | Walking distance from bed to bathroom and from entry to kitchen; stairs inside (Staircase rooms, more than one floor); a seat within every 8 m of path; wall length beside the toilet for grab bars |

**Apartment score** for profile p, with checks k weighted by importance (doors and reachability weigh most for wheelchair; turns and protrusions for blind; sightlines for deaf; distance and stairs for older adults):

```latex
A_p = 100 \times \sum_k v_{p,k}\, \mathrm{pass}_k, \qquad \sum_k v_{p,k} = 1
```

where each pass value is 0 to 1 (for example, the share of doors that pass, or the share of floor area reachable).

**What the user sees:** the floor plan colored cell by cell, each doorway labeled with its estimated width ("about 29 in"), turning circles drawn where they fit, and a list of problems. Tapping a problem opens the Matterport 3D tour at the nearest scan position. Scan positions and the wall graph share one coordinate frame, per Matterport's quickstart; mapping a Model API scan id to the Showcase SDK's scan id is still to be confirmed.

**Output tables:** `building_score(building_id, profile, score, components)`, `unit_grid(model_id, floor, png, npz)`, `unit_score(model_id, profile, score, checks, problems[])`.

## 8. Feature 2: furnishing planner

The planner has two modes. For a furnished apartment, it scores the current layout and suggests changes that raise the score. For a new apartment, it places the user's own furniture (or recommended pieces) around what can't move, so they can reach every room alone. Both modes reuse the apartment heat map from Feature 1c as the judge.

&#91;embedded content: Furnishing planner · inputs, solver, heat-map check\]

### Furniture finder (turning a scan into an inventory)

1. For each scan position, download the 2K cube faces of the panorama twice: as scanned, and from the model's furniture-removed view (`view(type: "matterport.model.defurnished")`, same positions).
2. Difference mask: per-pixel color difference in Lab space above a threshold, cleaned with morphology. What remains is movable furniture; built-ins (cabinets, fireplaces) appear in both and drop out. Tested: the removal is clean on lived-in homes.
3. Label each blob: crop it from the furnished image and ask Claude vision to choose from a fixed list (bed by size, sofa, sectional, armchair, dining table, chair, desk, dresser, nightstand, TV stand, bookshelf, rug, piano, lamp, plant), ignoring wall art.
4. Place it on the floor: take the blob's lowest pixels, where it touches the floor. Cast a ray from the scan position (camera about 1.5 m above the floor) through each of those pixels, using the panorama's rotation, and intersect it with the floor plane. The hits outline the footprint. Merge sightings from several scan positions within 0.5 m with the same label.
5. Snap to a standard size for the label (a queen bed is 1.52 × 2.03 m), align to the nearest wall, and show the inventory to the user to correct. Expect 20 to 30 cm error before correction.

### Mode A: current furnishing score and suggestions

- **Score:** the apartment score A\_p computed with the furniture in place, shown next to the same apartment empty. "Your furniture costs 23 points" is the headline.
- **Suggestions:** local search over single changes (shift 10 to 50 cm, rotate 90°, move to another wall, swap rooms, remove, or replace with a smaller piece). Keep the five changes that gain the most, each with its reason: "Move the armchair 40 cm left to open a 36 in path to the bathroom (+12)".

### Mode B: furnish a new apartment

**Inputs:** the target unit's grid with fixed elements, the inventory (from the finder, a typed list, or photos), and the user's profiles and preferences: chair width, transfer side for the bed, "I work from home", "keep my old layout".

**1. Room assignment.** Rules first (bed to a bedroom, never a bathroom or kitchen; dining table next to the kitchen; sofa and TV in the living room), then the LLM for ambiguous pieces and preferences.

**2. Hard rules** (a placement that breaks one is rejected):

| Rule | All profiles | Wheelchair | Blind | Deaf | Older adults |
| --- | --- | --- | --- | --- | --- |
| No overlap with walls, fixed elements or other pieces | Yes |  |  |  |  |
| Door swing zone clear (quarter circle of the door's width) | Yes | Plus 0.915 m approach on the pull side |  |  |  |
| Every room reachable from the entry | Yes | At 0.915 m clear width, in the inflated grid |  |  |  |
| Turning circle in bedroom, bathroom, kitchen |  | 1.525 m |  |  |  |
| Clear space beside bed, toilet, sink |  | 0.76 × 1.22 m on the transfer side |  |  | 0.76 × 1.22 m |
| Nothing protruding into paths |  |  | Over 0.10 m between 0.69 and 2.03 m high is rejected |  |  |
| Tall pieces don't block windows | Yes |  |  |  |  |
| Rugs |  | Not on paths | Not on paths |  | None, they are a trip hazard |

**3. Soft goals** (scored, then maximized): the sofa faces the TV wall at 2 to 3 m; nightstands beside the bed; the desk near a window; chairs around the table; short bed-to-bathroom distance (older adults); the sofa facing the entry and kitchen to keep sightlines (deaf); the same relative layout as the old home, so a memorized map still works (blind); balanced, uncluttered rooms.

**4. Search.** Candidate positions are wall-aligned slots every 10 cm with the piece's back to the wall, plus a free grid for tables, each in 4 rotations. Place the largest pieces first (greedy), then improve with simulated annealing for a few seconds. After each move, recheck reachability with a flood fill on the inflated 5 cm grid (about 28,000 cells for a 70 m² unit, a few milliseconds in numpy). If simulated annealing proves too slow or unstable, OR-Tools CP-SAT is the fallback.

**5. Check and explain.** Run the full heat map on the result. If a hard rule fails, drop or shrink the lowest-value piece and retry. Pieces with no legal spot go on a "won't fit" list with a suggested size that would fit. The LLM writes the reasons: "The bed is on the north wall so you have 1.2 m on your transfer side and a clear path to the bathroom."

**Outputs:** the plan as a floor plan with the heat map; the furnishing score before and after; the won't-fit list with replacement sizes (Amazon product links are optional); Matterport tags pinned at each piece's position in the 3D tour; and, as a bonus, a generated "refurnished" panorama of one room. Matterport lists "refurnish: restage a room" among its own unsolved problems.

**Output tables:** `furniture_item(model_id, label, footprint, confidence, source)`, `furnishing_plan(target_model_id, profile, placements[], score_before, score_after, wont_fit[], reasons[])`.

## 9. Feature 3: relocation match

A person moving from LA to Houston (or the other way) gives their current address and needs. NavEasy builds a "locality fingerprint" of where they live now, then ranks neighborhoods in the new city whose essentials are as close and as reachable for them, with homes available. The ranking runs on the locality scores already computed for Feature 1a, so it returns in under a second.

**Inputs:** the current address (any address, not only scanned ones), the selected profiles, must-haves ("dialysis center within 10 minutes", "bus stop within 5"), and optionally a workplace in the new city, a budget and a home type.

**The fingerprint** (computed live for the origin, precomputed for candidates):

| Part | What it holds | Source |
| --- | --- | --- |
| Essentials | Minutes to the nearest place in each of the 8 categories, per profile; how many are within 15 minutes (choice) | Valhalla zones and routes, OpenStreetMap places |
| Streets | Sidewalk coverage, share of lowered curbs, signalized crossings, benches per km, slope | OpenStreetMap, Vexcel elevation |
| Housing | Typical rent, building type, stories, share of homes with elevators | CoStar apartment listings and properties |
| Look and feel (optional) | Vexcel's image embedding of the blocks around the home | Vexcel look-alike search, which matched an LA courtyard mid-rise to Houston courtyard mid-rises at 0.95 similarity |

**Matching.** Candidates are Houston hex cells with scored, available homes that pass every must-have. For each category c, only a slower trip counts against a candidate; a closer pharmacy is never a penalty:

```latex
d_c = \frac{\max\left(0,\ t^{cand}_{p,c} - t^{home}_{p,c}\right)}{T^{zero}_c - T^{good}_c}
```

```latex
M = 100 \times \left( 0.6\,\left(1 - \sum_c w_c\, \min(1, d_c)\right) + 0.2\, S_{streets} + 0.1\, S_{housing} + 0.1\, S_{look} \right)
```

With several profiles, each category uses the slowest profile's minutes. The street, housing and look similarities run from 0 to 1. Look and feel is a tie-breaker, never the main reason.

**Output: the top 3 to 5 neighborhoods**, each named (OpenStreetMap neighborhood or ZIP code) and shown as:

- A side-by-side map at the same scale: the 15-minute zone around the current home and around the candidate, for the user's profile.
- A comparison table: "Pharmacy: 8 min now, 6 min here. Hospital: 12 min now, 19 min here."
- The three best homes there, with their apartment heat maps and furnishing fit.
- A plain-language reason, plus what's worse than home.

If nothing meets every must-have, it says which must-have failed and shows the closest options.

**Edge cases:** an origin outside our scanned area still works, because its fingerprint uses only OpenStreetMap and Valhalla. Geocoding goes through OpenStreetMap's Nominatim, which allows about one request per second, or the user drops a pin. The reverse direction (Houston to LA) is the same code.

**Output:** `relocation_match(origin, profiles, must_haves, candidates[{h3, name, match, comparison, homes[], reason}])`.

## 10. Agent and voice layer

The agent turns a sentence like "I use a power wheelchair, my wife is hard of hearing, I work at the Medical Center, budget $2,000" into profiles, filters, tool calls and an answer the user can check. It never computes geometry or scores itself; it calls our API for every number and moves the app to show the evidence. That matches what CoStar asked for: AI at the center, reaching into the app, not a chat box bolted on top.

**Framework:** LangChain deepagents in Python, with Claude Opus 5.5 (`claude-opus-5-5`) as the planner and Claude Haiku 5.5 (`claude-haiku-5-5`) for high-volume furniture labeling.

**Sub-agents** (each with its own context and tools):

| Sub-agent | Job | Tools |
| --- | --- | --- |
| Needs interviewer | Turns free text into profiles, preferences and must-haves; asks at most two follow-up questions | `save_profile` |
| Locality scout | Finds neighborhoods and explains their scores | `get_locality_scores`, `get_places_near`, `get_isochrone` |
| Home inspector | Checks buildings and apartments, lists problems with evidence | `get_building_score`, `get_unit_heatmap`, `get_unit_problems` |
| Furnishing designer | Scores current furniture, plans a new layout, explains it | `get_inventory`, `score_furnishing`, `plan_furnishing` |
| Relocation matcher | Builds the fingerprint and ranks matches | `get_fingerprint`, `match_localities` |

**UI actions.** Besides text, the agent returns actions the web app runs: `set_map_view(center, zoom, profiles)`, `highlight_buildings(ids)`, `show_route(route_id)`, `open_unit(model_id, problem_id)`, `open_tour_at(model_id, scan_id)`. The answer "These three homes work for you" arrives with the map already zoomed and the homes lit up.

**Guardrails:**

- Every claim cites its evidence ("bathroom door about 29 in, measured from the scan") and its confidence.
- The words "accessible" or "compliant" never appear as a guarantee; the agent says "meets our wheelchair checks" and lists what it couldn't check.
- If data is missing (a grey cell, no scan), it says so instead of guessing.

**Voice for blind and low-vision users.** A heat map is the wrong interface for a blind user, so the same agent runs over Photon 2.0: iMessage, WhatsApp or a phone call. Answers are short and ordered, with distances in minutes and landmarks, and routes are given turn by turn with each crossing described ("cross Main Street at the signal; no audible signal recorded there"). This also enters Photon's Best Voice Agent prize.

**For deaf and hard-of-hearing users:** everything is visual and text-first. No audio-only content, captions on any video, and chat as the main channel.

**The app itself must be accessible:** a list view next to every map, full keyboard navigation, screen-reader labels, patterns and text alongside color (never color alone), and a high-contrast mode. We test against WCAG 2.2 AA.

## 11. Data model and API

One DuckDB file holds every table for the hackathon; geometry is stored as WKT, and heavy artifacts (grids, images) live on disk with their paths in the tables. The API is a thin FastAPI layer over these tables plus three live calls: routing, furnishing and relocation.

**Tables:**

| Table | Key | Main columns | Filled by |
| --- | --- | --- | --- |
| `building` | `building_id` | kind (apartment community or home), CoStar property or listing id, name, lat, lon, city, h3 cell, stories, walk-up, elevators, year built, rent range | Building scores job |
| `unit` | `model_id` | building id, caption ("2BR, 1BA - 638SF"), floor, area in m², has furniture-removed view, scan date | Floor plan grids job |
| `room` | `model_id`, `room_id` | type, polygon, area, floor | Floor plan grids job |
| `opening` | `model_id`, `edge_id`, `index` | type, width, height, threshold, center point, room on each side | Floor plan grids job |
| `unit_grid` | `model_id`, `floor` | paths to the 5 cm grid (.npz) and its preview image | Floor plan grids job |
| `furniture_item` | `item_id` | model id, label, footprint polygon, height class, confidence, source (finder or user) | Furniture finder job |
| `poi` | OpenStreetMap id | category, name, lat, lon, tags | Locality job |
| `street_measure` | h3 cell | sidewalk coverage, lowered-curb share, signalized crossings, benches per km, mean slope, crosswalks seen | Locality job |
| `locality_score` | h3 cell, profile | score, confidence, minutes by category | Locality job |
| `building_score` | building id, profile | score, components | Building scores job |
| `unit_score` | model id, profile, furnished | score, checks, problems | Floor plan grids job |
| `route` | route id | origin, destination, profile, geometry, minutes, meters, hard spots, stretch scores | Live, cached |
| `furnishing_plan` | plan id | target model, profiles, placements, score before and after, won't-fit list, reasons | Live, cached |
| `user_profile` | session id | profiles, chair width, transfer side, must-haves (kept on the device where possible) | Agent |

**Endpoints:**

| Method and path | Returns |
| --- | --- |
| `GET /map/hexes?city&res&profiles&combine=min` | Hex cells with score and confidence |
| `GET /map/buildings?bbox&profiles&passing_only` | Building dots with score and kind |
| `GET /buildings/{id}?profiles` | Building score, components, units, nearest essentials |
| `GET /units/{model_id}/heatmap?profile&furnished` | Heat map image, doorway labels, turning circles, problems |
| `GET /routes?from&to\|category&profile` | Route with colored stretches and hard spots (with Vexcel image links) |
| `GET /furnish/score?model_id&profiles` | Current furnishing score, empty-unit score, top 5 suggestions |
| `POST /furnish/plan` | Placement plan for a target unit from an inventory and preferences |
| `POST /relocate` | Ranked neighborhoods in the other city with comparisons and homes |
| `POST /agent/chat` | Agent text plus UI actions |

All endpoints take `profiles` as a comma list (`wheelchair,deaf`) and return a confidence with every score.

## 12. Frontend

One web app, built around the map, that zooms from city to doorway. React with Vite; MapLibre GL JS for the base map (free vector tiles, no key); deck.gl layers for hexes (`H3HexagonLayer`), building dots (`ScatterplotLayer`) and routes (`PathLayer`); an HTML canvas for floor-plan heat maps; the Matterport Showcase SDK for the 3D tour.

**Screens, in the order a user meets them:**

| Screen | What's on it | Interactions |
| --- | --- | --- |
| City map | Houston or LA hex heat map for the selected profiles; legend with color and pattern; grey "no scan nearby" cells; hatching for low confidence | Side (burger) menu with four profile checkboxes (red wheelchair, purple blind, blue deaf, green older adults), "combine by weakest" or "average", city switch, search box, agent chat |
| Neighborhood | Building dots colored by score; "only show homes that pass" toggle; list view of the same homes, sorted | Hover for score; click a dot to open the building panel |
| Building panel | Score with its parts (vertical access, approach, listing evidence, age); scanned units with mini heat maps; rent; photos; buttons for routes to pharmacy, clinic, grocery, bus stop, school, hospital, workplace | Click a unit to open the apartment view; click a destination to draw its route |
| Route view | Path colored by stretch; pins on hard spots with a Vexcel photo; minutes, crossings, confidence; alternatives | Toggle profiles to see the route change |
| Apartment view | Floor plan heat map for the chosen profile; doorway labels ("about 29 in"); turning circles; problems list; furnished or empty toggle | Click a problem to open the 3D tour at that spot; "Score my furniture" and "Plan my furniture" tabs |
| Furnishing | Score before and after; suggestions with point gains; the new plan drawn on the floor plan; won't-fit list | Drag a piece and see the heat map update live; accept or reject suggestions |
| Relocation | Origin address and needs; split map with both 15-minute zones at the same scale; comparison table; top neighborhoods and their homes | Click a neighborhood to jump to it on the main map |

**3D tour:** the Showcase SDK embed with our SDK key, which only works on `localhost`, so the demo runs from a laptop. Problems from the heat map become tags at their 3D positions.

**Accessibility of the app itself:** a list view next to every map; full keyboard use; screen-reader labels on every map feature and chart; scores shown as numbers and words, not only colors; patterns for color-blind users; high contrast; reduced motion; voice entry through the agent.

## 13. Pipeline, performance and limits

The bottlenecks are the free public services and panorama downloads, not our code. The hackathon run covers one Houston neighborhood in full and estimates the rest; a full-city run needs our own Valhalla server and an overnight job.

**Precompute jobs and volumes (Houston):**

| Job | Volume | Cost per item | Hackathon plan |
| --- | --- | --- | --- |
| Building table and scores | 1,348 communities + 1,700 home listings | Local; seconds in total | Full city |
| Matterport geometry (floors, rooms, openings) | About 7,100 commercial scans + 1,648 home scans | One GraphQL call, about 1 to 2 s | Hero neighborhood (about 200 scans); full city overnight if rate limits allow |
| Floor plan grids and unit scores | Same as above | About 50 ms each in numpy | Whatever geometry we have |
| Furniture finder | Homes with furniture-removed views | 5 cube faces × 2 views × about 0.49 MB = about 4.9 MB per scan position; a large home with 85 positions is over 400 MB | Hero homes only, one scan position per room (about 15 per home, about 75 MB) |
| OpenStreetMap places and street features | One bounding-box query per city | One Overpass call; public servers returned 502 and timed out during testing | Run once early, cache as GeoJSON, retry on mirrors |
| Walking-time zones | About 3,000 sites × 4 profiles | One Valhalla call, about 1 s; the public server expects light use | Hero neighborhood live; elsewhere straight-line distance × 1.3 ÷ profile speed, marked as estimates |
| Vexcel crosswalk detection | Crossings on hero routes | A few seconds per call; HTTP 429 means back off | Hero routes only |

**Limits and how we respect them:**

- Vexcel: back off exponentially on 429 (1, 2, 4, 8, 16 s), never run calls in parallel, cache every image.
- Matterport: signed image links expire, so store the images, not the links. Model ids and view ids are stable.
- Valhalla (FOSSGIS) and Overpass: public, best-effort services. Keep to about 1 request per second, set a clear user agent, cache everything. After the hackathon, self-host Valhalla with a Texas and California OpenStreetMap extract.
- Nominatim geocoding: at most one request per second.

**Latency targets for the app:**

| Action | Target | How |
| --- | --- | --- |
| Pan or zoom the map | Under 200 ms | Precomputed hex and building tables |
| Open an apartment heat map | Under 1 s | Cached grid and image |
| Draw a new route | Under 2 s | Live Valhalla call plus cached street features |
| Furnishing plan | Under 5 s | Greedy placement plus a short annealing run |
| Relocation match | Under 1 s, plus 2 s for a new origin | Precomputed candidates; live fingerprint for the origin |
| Agent answer | Under 8 s | Tool calls hit cached endpoints |

**Storage:** the CoStar kit is 416 MB unpacked; geometry and grids for the hero neighborhood are under 1 GB; hero panoramas are a few GB. Everything stays on the laptop and out of git.

## 14. Accuracy, safety and ethics

A wrong "yes" can strand a wheelchair user at a door they can't pass, so NavEasy estimates, shows its evidence, and never certifies. Every score carries a confidence, and every claim links to what it was measured from.

**Known measurement limits:**

| Limit | Effect | What we do |
| --- | --- | --- |
| Scan door widths are likely rough openings, not ADA clear width | Doors look 3 to 5 cm wider than they are | Subtract 5 cm and label as "about"; ask Matterport how widths are measured |
| Room types are sometimes wrong (a "2BR" scan had 3 bedrooms) | Wrong room rules applied | Check room types against the listing's bed count and size; let the user relabel |
| Furniture footprints from panoramas are off by 20 to 30 cm | Clearance errors near the limits | Snap to standard sizes; user confirms the inventory; treat borderline paths as amber |
| OpenStreetMap is uneven (good in Midtown, thin in suburbs; almost no audible-signal or tactile tags in Houston) | Route and locality scores weaker for blind users and outer areas | Confidence per cell and route; grey for unknown, never green |
| Data age: scans and flights have dates | Renovations or new furniture since then | Show scan and flight dates |
| Coverage: only buildings CoStar scanned | Many homes, often lower-cost ones, are missing | Grey means "no data", stated on the map, never "inaccessible" |

**User safety:**

- Use "meets our wheelchair checks" rather than "accessible", and list what wasn't checked (entrance hardware, counter heights, smoke alarms).
- Every home page has a "verify before you sign" checklist generated from its own weak spots.
- A "this is wrong" button on every score; reports lower confidence until reviewed.

**Privacy:** disability and health information is sensitive. Profiles stay on the device where possible, are never sold or shared, and only coordinates (never names or conditions) go to routing and map services. CoStar's contact emails and phone numbers are already fake in the kit.

**Fairness:** filtering by accessibility is the user's own choice. We don't rank or describe neighborhoods by who lives there, only by streets, distances and buildings.

**Licensing and keys:**

- CoStar, Matterport and Vexcel data are licensed for hackathon use only. Raw data, images and the kit stay out of the public GitHub repo (`.gitignore` covers `costar-kit/`, `*.zip`, `.env`).
- Keys live in `.env`, never in code, slides or screenshots.
- Attribution on every map and image: "Imagery © Vexcel Imaging US, Inc.", "© OpenStreetMap contributors", Matterport for 3D content.

## 15. Hackathon scope

Everything in this document is in scope, but it ships in two tiers. The MVP must work end to end by Sunday 6 am. Stretch items get built only once the MVP demo has been recorded. Devpost closes at 8:00 am Sunday, Nov 15.

**MVP and stretch, per feature:**

| Feature | MVP (must ship) | Stretch |
| --- | --- | --- |
| 1a Locality heat map | Houston; one neighborhood (Midtown or the Medical Center) computed live for all four profiles; the rest of the city estimated and marked | Live zones citywide; Los Angeles |
| 1b Route A to B | Routes from hero homes to pharmacy, clinic, grocery, bus stop, school and hospital, for wheelchair and blind profiles, with hard spots | Any destination; Vexcel crosswalk check on every crossing |
| 1c Building and apartment heat maps | Building scores for all 1,348 Houston communities; full apartment heat maps for 5 to 10 hero units: wheelchair and older-adult layers, deaf sightlines | Blind path-turn layer; every scan in the hero neighborhood |
| 2 Furnishing planner | Furnishing score and top 3 suggestions for 2 furnished hero homes; a new-apartment plan for 1 empty unit with 6 to 8 pieces | Fully automatic furniture finder; a generated refurnished panorama; drag-to-edit |
| 3 Relocation match | One LA origin returns its top 3 Houston neighborhoods, with a comparison table and homes | Any address; Houston to LA |
| Agent and voice | Text agent: needs to filters, map actions, explanations | Photon voice and iMessage channel |

**Workstreams** (one per teammate; if solo, do them in this order):

1. Data and geometry: building table, Matterport geometry pulls, grids, apartment scores, furniture finder.
2. Map and frontend: map, profile menu, building panel, apartment canvas, Showcase embed.
3. Routing and locality: OpenStreetMap cache, Valhalla routes and zones, hex scores, relocation match.
4. Agent, pitch and demo: deepagents tools over the API, the 2-minute script, the backup video, the Devpost write-up.

**Milestones:**

1. Sat 12 pm: hero neighborhood chosen; building table built; OpenStreetMap cached; geometry pulled for hero scans.
2. Sat 6 pm: apartment heat maps working for 5 hero units; building scores for every Houston community.
3. Sat 10 pm: map with profile toggles, building dots and the building panel; routes from hero homes.
4. Sun 2 am: furnishing score and one new-apartment plan; relocation top 3 for one LA origin.
5. Sun 5 am: agent wired to the app; the full demo path works once.
6. Sun 6 am: backup video recorded. MVP freeze.
7. Sun 7:30 am: Devpost submitted (public repo, video, tracks, Discord usernames).

**Cut lines, in order, if we fall behind:** the generated panorama, then the agent (keep manual filters), then LA, then the 3D tour embed (keep the floor plan), then hex cells (keep building dots). Never cut the apartment heat map; it is the part nobody else has.

**Tracks to enter:** Healthcare (main track), CoStar, Amazon Practical AI, LangChain deepagents, and Photon if voice ships.

## 16. Demo script

One user story carries all three features in two minutes. Numbers in \[brackets\] are placeholders until the build produces the real ones.

1. **0:00, the hook.** "Maria uses a power wheelchair. She's moving from LA to Houston for a job at the Medical Center. On a listing, 'accessible' is a guess: only 68 of 2,120 homes in CoStar's Houston and LA listings even mention it."
2. **0:15, relocation.** Type her LA address and needs. Three Houston neighborhoods appear, with her 15-minute wheelchair zone at home beside each one at the same scale: "Pharmacy \[8\] min now, \[6\] min here."
3. **0:40, locality heat map.** The Houston map with the wheelchair layer. Tick "deaf" for her husband and the map switches to the weakest-of-both view. Zoom into the top neighborhood.
4. **0:55, buildings and route.** Homes that pass light up. Tap one: the wheelchair route to the pharmacy, with one hard spot (a crossing without a lowered curb) shown in Vexcel's aerial photo.
5. **1:15, inside the apartment.** The floor plan heat map: \[24\] doorways measured, the bathroom door at about \[29\] in shown in red, no turning circle in the bathroom. Tap it and the 3D tour flies there.
6. **1:35, furniture.** Her furniture, pulled from her current home's scan, is placed in a unit that passes. The score goes from \[54\] to \[88\]; the piano is on the won't-fit list.
7. **1:50, close.** "From the city to the doorway, and from her old home to her new one: measured, not guessed."

**Questions judges will ask, and our answers:**

| Question | Answer |
| --- | --- |
| How accurate are the door widths? | They come from Matterport's measured wall graph; we subtract 5 cm because they're likely rough openings, show "about", and never certify |
| Doesn't Airbnb already do this? | Airbnb Adapted 3D-scans and reviews a small set of vacation rentals. We measure every scanned home automatically, for four needs, and connect it to the building and the street |
| What about buildings without a scan? | They're grey, labeled "no data", never marked inaccessible. Every new Matterport scan adds a home |
| Why would CoStar care? | Apartments.com and Homes.com could market accessible units with proof, and the furnishing planner is Matterport's own unsolved "refurnish" problem |
| Why are blind and deaf scores weaker? | Public map data rarely records audible signals or tactile paving. We say so, and serve blind users by voice instead of a map |
| What about privacy? | Profiles stay on the device; only coordinates go to map services |

## 17. Risks and open questions

The biggest risk is scope: this document describes three features, four profiles and four zoom levels, and the team keys list one member. The MVP tiers and cut lines in section 15 exist for that reason.

**Risks:**

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Scope too large for the team and time | High | No working demo | Build the apartment heat map first; follow the cut lines; freeze at 6 am |
| Public Overpass or Valhalla servers slow or down (Overpass returned 502 and timed out in testing) | Medium | No places or routes | Cache early; use mirrors; precompute hero routes; keep straight-line estimates as fallback |
| Matterport rate limits or expiring image links | Medium | Missing geometry or panoramas | Pull hero scans first; store images, not links |
| Door widths are rough openings | High | Overstated widths | 5 cm deduction, "about" labels, ask Matterport |
| Furniture finder too slow or noisy | Medium | Weak Feature 2 demo | Semi-manual inventory for hero homes; automate later |
| Showcase SDK key works only on localhost | Certain | Can't deploy the 3D tour | Demo from a laptop; floor plan works anywhere |
| "Accessibility map" is a common hackathon theme | Medium | Judges tune out | Show the measured doorway in the first minute; mention Airbnb first |
| Blind and deaf scores rest on thin data | Certain | Credibility | Label low confidence; voice channel for blind users |

**Open questions** (ask in `#costar-group` or with `/ask-costar`):

- Are Matterport opening widths rough openings or clear widths, and how is the threshold height measured?
- How do Model API scan ids map to Showcase SDK scan ids, so a problem can open the tour at the right spot?
- Do all apartment-unit scans have the furniture-removed view, or only some?
- Are MatterPak meshes and point clouds available for every model with our key?
- What do the building amenity ids in the CoStar kit mean?
- Does the Photon 2.0 beta support live voice calls?
- Which attribute names does Valhalla's `trace_attributes` return for sidewalks, crossings and grade on the public server?
- How many people are on the team, and who owns which workstream?

## 18. Sources

Every figure in this document comes from our own API calls on Nov 13 and 14, 2026, from the CoStar kit, or from these pages:

- [2010 ADA Standards for Accessible Design](https://www.access-board.gov/ada/) (U.S. Access Board): sections 303, 304.3.1, 305.3, 307.2, 308, 403.3, 403.5.1, 404.2.3, 405.2, 609.4
- [Matterport developer docs: Showcase SDK overview](https://matterport.github.io/developer-docs/overview/) and [Model API](https://matterport.github.io/developer-docs/api/)
- [Matterport hackathon quickstart](https://static.matterport.com/hackathon/26.15.35-main-35-g24e0220/assets/quickstart/reference.html): the furniture-removed view and Matterport's list of unsolved problems
- [Matterport hackathon site](https://beta-app.matterport.com/2026-princeton-hackathon/): track brief, splat dataset, Model Playground
- Vexcel API Hackathon Tutorial (PDF, from the hackathon materials): endpoints, layers, rate limits
- CoStar data kit `index.md` (from the hackathon materials): datasets, joins, scope
- [AccessMap](https://tcat.cs.washington.edu/projects/accessmap) (Taskar Center, University of Washington): prior work on accessible routing
- [Airbnb accessibility](https://www.airbnb.com/accessibility?locale=en): Adapted homes, 3D-scanned with doorway widths
- [Apartments.com wheelchair-accessible listings](https://www.apartments.com/merrifield-va/wheelchair-accessible) and [Sunrise Medical's accessible apartment hunting tips](https://www.sunrisemedical.com/livequickie/blog/january-2020/accessible-apartment-hunting-tips): existing accessibility filters
- [OpenStreetMap Overpass API](https://overpass-api.de/) and the [FOSSGIS Valhalla server](https://valhalla1.openstreetmap.de/): places, street features, routes, walking-time zones
- [LangChain deepagents](https://github.com/langchain-ai/deepagents) and [Photon 2.0](https://photon.codes/beta): agent framework and messaging and voice channel

Libraries named but not yet used: H3 (hexagon grid), MapLibre GL JS, deck.gl, FastAPI, DuckDB, shapely, OR-Tools. The app is to be tested against WCAG 2.2 AA.
