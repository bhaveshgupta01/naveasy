# Ground truth: the HackPrinceton venue with a tape measure

The building we are standing in is the Julis Romo Rabinowitz Building on Washington Road (Matterport scans
`P3S6VUTP3qB` and `R1FX6EmdcDt`, "Princeton - Rabinowitz Hall"). NavEasy measures it from the scan
(`pipeline/venue.py`). Before we show any of these numbers to judges, we check them against the real building.
This is also our first ground truth: the first time an engine number is compared with a tape.

Until a number is checked, the app and the assistant say "from the scan, not yet checked against a tape measure".
Once `ground_truth/princeton.json` has a tape value, they say "checked with a tape measure on <date>", and if the
scan is off by more than 1.5 in they say so and use the tape.

## Where things are

- The scan covers two floors that Matterport labels **floor 2** (the lower one, the main hall) and **floor 3**
  (about 4 m higher, with the classroom and two stair landings). Matterport's "floor 1" is a stub nobody stood on.
- Compass sides come from Matterport's alignment of the scan (model north is about 22 degrees west of true
  north). They are approximate: use them to find the door, not as a bearing.
- **Entrances are our assumption.** We take the widest doors out of the hall on floor 2 (the glazed doors on the
  south-west side) and the doors on the north-east side of floor 3. Note which doors people actually use.

## Procedure

1. Doors: open the door to 90 degrees. Measure the **clear width** from the face of the open door (or its stop) to
   the stop on the other jamb, at about waist height. For a double door, measure with both leaves open, and also
   one leaf alone if one leaf is usually kept shut. Write inches to the nearest half inch.
2. Corridors: walk the route (entrance to the stairs on floor 2; top of the stairs to the classroom on floor 3) and
   measure wall to wall, or wall to a fixed object, at the tightest point you find. Ignore movable chairs.
3. Steps and the raised floor: measure the height of any sill or step with the tape held vertical against the
   higher edge. For the raised part of floor 3, find where it meets the rest of the floor: is it a step or a ramp?
   If a ramp, how long?
4. Photograph each item with the tape in the shot (no people in frame).
5. Enter the values in `ground_truth/princeton.json` (`tape_in`, plus `checked_on` as YYYY-MM-DD and who measured)
   and run `python -m pipeline.venue` to rebuild the venue record. Commit the JSON: it holds only our own numbers.

## The sheet

The engine's door width is the scan's opening minus 2 in (scans measure the frame, not the clear space).
ADA 2010 limits for reference: 32 in clear at a door (404.2.3), 36 in along a route (403.5.1), 60 in turning circle (304.3.1).

| # | Key in princeton.json | What and where | Engine says | Tape |
|---|---|---|---|---|
| 1 | `door_6cz05t9cxmem7zmuudgi59hmc_0` | Entrance (assumed), south-west side of floor 2, between the hall and the vestibule | 67.0 in (170 cm) clear | |
| 2 | `door_fd1trm6r30c9df32fi3wd3gcd_0` | The second door of the same entrance | 67.0 in (170 cm) clear | |
| 3 | `door_07r29adiz63ewf88w743g94xc_0` | Door on the west side of floor 2 | 63.4 in (161 cm) clear | |
| 4 | `door_ttu12npbd8epayqgd3ubk7hwc_0` | Doors on the north-east side of floor 3. **The two processings of the scan disagree**: 27.6 in in `P3S6VUTP3qB`, 34.5 in in `R1FX6EmdcDt`. The scan's sill is 13 cm above the main floor of floor 3 | 27.6 in (70 cm) or 34.5 in (88 cm); step 13 cm | |
| 5 | `raised_2` | The raised part of floor 3 on the north-east side (about 64 m2), measured on the 3D mesh. The doors in row 4 open onto it, flush with it | 14 cm (5.6 in) higher than the rest; step or ramp unknown | |
| 6 | `door_ez8z2gwmulw_0` | Classroom to the north landing, floor 3 | 64.0 in (163 cm) clear | |
| 7 | `door_twboqwt4y6f_0` | Classroom to the south landing, floor 3 | 49.1 in (125 cm) clear | |
| 8 | `corridor_entrance_classroom_1` | Narrowest corridor from the entrance to the foot of the stairs, floor 2 | 49 in (125 cm) | |
| 9 | `door_ad9d7cu6wrn5tce0szc1htbmb_0` | Opening on the south side of floor 3 that the scan says is only 1.81 m tall: what is it? | 42.7 in (108 cm) | |
| 10 | `door_hfr74i7dnm8hhcne1xdzwsm7c_0` | A 49 cm gap in the wall on the north-east side of floor 3, set aside as "not a real door": what is there? | not a door | |
| 11 | `elevator` | Is there an elevator between floor 2 and floor 3? Where? Its door's clear width | not in the scan | |
| 12 | `corridor_entrance_classroom_2` | Narrowest point from the top of the stairs to the classroom, floor 3 | 156 in (395 cm): open floor | |

## What the scan already told us

- **The location.** Matterport's coordinates (40.34909, -74.65547) fall inside OpenStreetMap's footprint of the
  Julis Romo Rabinowitz Building (way 1086964029). The other candidate, 40.3497 -74.6521, is among Mudd Library,
  the Computer Science Building and Sherrerd Hall, about 300 m east.
- **The two scans are one capture.** They share all 66 scan positions to within 1.4 cm, so one frame; the labels
  (Classroom, Landing) are only in `P3S6VUTP3qB`, and the 3D mesh is only downloadable for `R1FX6EmdcDt`.
- **The "13 cm steps" at outside doors.** The scan put sills about 13 cm above the floor at several doors on floor 3.
  The 3D mesh shows why: that side of floor 3 really is about 14 cm higher than the rest, and those doors are flush
  with it. So the step is not at the doors but wherever the two levels meet (row 5).
- **Set aside.** Doorways under 50 cm wide (two in `P3S6VUTP3qB`, four in `R1FX6EmdcDt`) and one doorway drawn
  on floor 3 whose sill is at floor 2's height.
- **Stairs.** The main flight is on floor 2, found in the mesh (about 8 m2 of treads), and one scan position stands
  on it halfway up (3.4 m). The scan shows no elevator.

## Results

Fill in after the walk: date, who measured, and for each row the tape value and the difference.
