# Table models: two homes, four overlays each

Two homes printed as open-top boxes, each with four drop-in floor mats, one per need. Put a mat into its box and
the table shows what the app's heat map shows for that need. Put the two homes side by side to compare them.
Sized for a Bambu Lab A1 mini (180 mm bed), one color.

    python scripts/print3d.py --estimate                 # the quick set (default)
    python scripts/print3d.py --preset demo --estimate   # the app's demo homes, larger

Files go to `output/print3d/<preset>/` (not committed), about 10 s per preset. `--scale`, `--wall` and `--homes`
override the preset; `--separate` keeps boxes and mats on separate plates (for a spool change between them).

## The two sets

| | **Quick set** (`small`, default) | **Demo set** (`demo`) |
| --- | --- | --- |
| Homes | **Playa Palms 1BR** (151 N Normandie Ave, LA) and **833 W 84th St 1BR** (LA): the same size (7.7 x 4.8 m and 7.8 x 4.8 m), the same furniture | **7833 Narcissus St** (the furnished home, its real furniture) and **Drewery Place 2BR** (2850 Fannin St, Narcissus's furniture placed by the planner) |
| Scores (wheelchair / blind / deaf / older) | Playa 44 / 94 / 90 / 98; W 84th 100 / 100 / 100 / 99 | Narcissus 37 / 89 / 73 / 66; Drewery 91 / 73 / 90 / 91 |
| Scale, walls | 1:120, 12 mm | 1:95, 20 mm |
| Box size | 76 x 67 x 14 mm each | 120 x 143 x 22 and 178 x 164 x 22 mm |
| Mat size | about 62 x 39 mm, one piece each | 109 x 114 (Narcissus, 4 pieces each) and 164 x 136 mm |
| Plates | **2** | 10 (each part fills a plate) |
| Print time (estimate) | **about 1.6 h** | about 8 h |
| Filament | about 56 g | about 300 g |
| Mat labels | print only: a word, or the need's letter (W, B, D, O) with the key on each box | print and braille (Narcissus's wheelchair mat: print only) |

The quick set is the smallest that still reads easily. At 1:150 (`--scale 150`) everything fits one plate (about
1.1 h), but rooms hold only a few cells of the net, most mats are down to a letter, and Playa's blind mat has no label. Its furniture is a light set (bed, sofa,
TV stand, dining table), placed by the planner for all four needs. Eight pieces would cover half of a 37 m2 home.

The times come from `scripts/print_time.py`, which slices each plate and adds up the paths at the A1 mini's
"0.20mm Standard" PLA speeds, plus 5 minutes per plate to start. It is not a slicer: Bambu Studio's number can
differ by up to about a third.

## Files

| File | What | Print |
| --- | --- | --- |
| `plate1_parts.stl`, `plate2_parts.stl`, ... | The parts packed onto 180 x 180 mm plates for one color (quick set: plate 1 holds both boxes and Playa's mats, plate 2 the 84th St mats) | One print per plate, as exported |
| `<home>/house_furnished.stl` | Base, walls (door gaps open, windows as notches), built-ins where the scan has them, and the furniture: the box | Upright, open top up |
| `<home>/house.stl` + `<home>/furniture.stl` | The same box split in two, sharing one origin: load both as **parts of one object** to print the furniture in a second color | |
| `<home>/overlay_<need>.stl` | One mat: wheelchair, blind, deaf, older | Flat, marks up |
| `<home>_sheet.png`, `<home>/preview_*.png` | Top views of the box and each mat, colored like the app | |

Every STL is watertight (checked by reading each file back; the method was tested at scales from 1:70 to 1:150). Each mat clears every
wall, built-in and piece of furniture by 0.5 mm per side, checked on the exported files. No supports are needed.

- **Settings:** PLA, 0.4 mm nozzle, 0.2 mm layers. Keep the mats at 0.2 mm so the domes and text hold their shape.
  Use a brim on the mats if corners lift.
- **Fit:** if a mat binds, turn on elephant-foot compensation (about 0.15 mm), or regenerate with a larger `CLEAR`.
- **Bed:** the demo set's Drewery box is 178 mm long, inside the A1 mini's 180 mm. If the slicer says it doesn't fit,
  use `--preset demo --scale 100`.

## One color

The code is in the shapes, not the colors, so a one-color set still works: net against solid against stripes, the
raised marks, and the label on each mat read by touch and by shadow. What is lost is telling the four mats apart at a
glance (each is labeled) and how strongly the net stands out against a floor of the same color. In order of effort:

1. **Two spools, no AMS:** generate with `--separate`, print the box plate in one color and the mat plates in
   another. White boxes with dark mats (or the reverse) make the net and the furniture holes stand out.
2. **One spool:** color the box floors with a dark marker or paint before use. Every hole in a mat then shows dark,
   so the problem areas (the net) stand out on every mat.
3. **Short on time (demo set):** print both boxes and both wheelchair mats first (the starkest contrast), then the
   blind mats, then deaf and older adults.

## Reading a mat

The same code on all four mats:

| Mat surface | Means |
| --- | --- |
| Solid | Good for this need |
| Solid with raised diagonal stripes | Caution |
| Open net (the floor shows through) | Problem |
| A hole the shape of a piece | Furniture or a built-in stands there |

Raised marks, per need:

| Mat | Marks |
| --- | --- |
| Wheelchair | Rings: a 5 ft turning circle fits. Tall bar across a doorway: too narrow for a chair (under 32 in clear). Low bar: tight (just under 32 in) |
| Blind | Double ridges: the tactile guide lines from the front door to the bed, bathroom and kitchen. A square of 9 domes: furniture within 30 cm of that path |
| Deaf | Tall post: the main seat. Short post: the kitchen. Ring: the front door. Dashed line: the front door is in view from the seat (none drawn when it isn't) |
| Older adults | Ridge: the night walk from the bed to the bathroom. Solid floor is within 8 m of the bed, stripes 8 to 15 m, net beyond |

A row of three domes at a doorway is a raised step or threshold, on every mat for a need where a step matters
(wheelchair, blind, older adults). Each mat has a label and a fin to lift it out. Where the word has no room, the
mat carries its letter, and the box's front strip carries the key: W wheelchair, B blind, D deaf, O older.

## What each home shows

**Playa Palms (quick set).** Wheelchair: the living area is solid with one turning circle; the bedroom and bathroom
are net, behind barrier bars at a 29 in and a 27 in door. Older adults: a 1.2 m night walk from the bed to the
bathroom. Deaf: the front door is in view from the sofa (dashed line).

**833 W 84th St (quick set).** Wheelchair: solid everywhere, three turning circles (doors 33 to 39 in). Older adults:
a 6.6 m night walk, five times Playa's. So each home wins on something. Deaf: the front door is in view from the sofa.

**Narcissus (demo set).** Wheelchair: nearly all net. Only the space just inside the front door is solid, and there
are barrier bars at the bathroom (26 in), kitchen (29 in) and a bedroom (30 in) door, plus a low bar at another
bedroom door (30 in). Blind: guide ridges from the front door to the bed, with warning domes where the sofa and an
armchair crowd the path. Deaf: the living room is solid, the front door is in view from the sofa (dashed line), and
the kitchen is striped (seen from the kitchen only).

**Drewery (demo set).** Wheelchair: solid almost everywhere, five turning circles, the main bedroom with its bathroom
and closet striped (32 to 36 in on the way in), one low bar (31 in), and step domes at three bedroom doors (the scan
shows 4 to 8 cm). Deaf: the bedrooms are net (out of sight of the seat), and the front door is not in view from the
sofa. Older: a 2 m night path.

## Limits

- **No braille in the quick set:** at 1:120 no mat has open floor long enough for a braille word. The demo set has it.
- **No built-ins in the quick set:** the two LA scans have no 3D mesh on disk, so kitchens and bathrooms show as open
  floor. `python -m pipeline.fixtures <model_id>` fetches the mesh (a Matterport API call); regenerate afterwards.
- **Narcissus mats come in 4 pieces.** Its scan records no opening between some areas: dining sits off the living
  room, and the hallway and bottom bedrooms have no recorded opening to it. Each piece sits boxed in by its own
  walls and has its own lift fin.
- **Narcissus has no night path.** The engine finds no walk from the main bedroom to the bathroom in that scan
  (the same missing opening), so its older-adults mat shows only the distance-from-bed bands.
- **Not to scale:** walls are 12 or 20 mm tall so you can see in and lift the mats. Walls print at least 1.6 mm thick.
  Furniture heights are one standard height per kind, since the scan's tops are noisy.
- **Furniture** is the planner's layout for all four needs at once. The app can also plan per need, and those
  layouts differ.
- **Braille** uses standard cell spacing with 1.5 mm dots, at the small end of what FDM prints well. Check it by touch
  before relying on it.
