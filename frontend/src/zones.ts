// Zone alerts for the 3D tour: is the spot you are standing on (or pointing at) a problem for this need?
// Uses the same measurements as the 2D heat map: the heat grid, the rooms and the measured problems.
import type { Problem, Profile, Room, Unit } from './types'

export type Level = 'red' | 'amber' | 'green' | 'none'

export interface Zone {
  level: Level
  title: string
  text: string
  key: string // changes when you move into a different zone, so alerts fire once per zone
  problem?: Problem
}

const TITLE: Record<Profile, Record<'red' | 'amber' | 'green', string>> = {
  wheelchair: { red: 'Not reachable in a wheelchair', amber: 'Tight for a wheelchair', green: 'Clear for a wheelchair' },
  blind: { red: 'Hazard for blind or low vision', amber: 'Caution: obstacle or step nearby', green: 'Clear floor' },
  deaf: { red: 'Blind spot: out of sight', amber: 'Partly out of sight', green: 'In view of the main seat' },
  older: { red: 'Hard to reach for an older adult', amber: 'A long way from the bed', green: 'Close to the bed, no steps' },
}

// What a heat value means where you stand, in words (2 problem, 3 caution, 4 good)
const HEAT_TEXT: Record<Profile, Record<2 | 3 | 4, string>> = {
  wheelchair: { 2: "A wheelchair can't get here from the entry.", 3: 'Reachable, but only 32 to 36 in of clear width.', 4: '36 in or more of clear width.' },
  blind: { 2: 'Furniture or a step on a main path.', 3: 'Within 30 cm of furniture.', 4: 'No obstacles measured here.' },
  deaf: {
    2: "Can't be seen from the main seat or the kitchen, so a visitor or a flashing alarm here goes unnoticed.",
    3: 'Seen from the kitchen only, not from the main seat.',
    4: 'In view of the main seat.',
  },
  older: { 2: 'Over 15 m from the bed, or up stairs.', 3: '8 to 15 m from the bed.', 4: 'Within 8 m of the bed, no steps.' },
}

// How close a measured problem has to be before it counts (meters). Room problems count anywhere in their room.
const REACH: Record<string, number> = { narrow_door: 1.1, threshold: 1.0, path_obstacle: 0.9, sightline: 1.3, long_walk: 1.3 }
const ROOM_KINDS = new Set(['unreachable_room', 'no_turning_space'])
const STAIRS = /stair/i

export function heatAt(unit: Unit, x: number, y: number): number {
  const g = unit.heat
  const i = Math.floor((x - g.origin[0]) / g.cell_m)
  const j = Math.floor((y - g.origin[1]) / g.cell_m)
  const at = (ii: number, jj: number) => (jj >= 0 && jj < g.height && ii >= 0 && ii < g.width ? g.values[jj][ii] : 0)
  const v = at(i, j)
  if (v >= 2) return v
  // on a wall line or next to furniture: use the nearest floor cell within 35 cm
  const r = Math.ceil(0.35 / g.cell_m)
  let best = v
  let bestD = Infinity
  for (let dj = -r; dj <= r; dj++)
    for (let di = -r; di <= r; di++) {
      const w = at(i + di, j + dj)
      const d = di * di + dj * dj
      if (w >= 2 && d < bestD) {
        best = w
        bestD = d
      }
    }
  return best
}

function inside(poly: [number, number][], x: number, y: number): boolean {
  let c = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]
    const [xj, yj] = poly[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c
  }
  return c
}

export function roomAt(unit: Unit, x: number, y: number): Room | null {
  return unit.rooms.find((r) => inside(r.polygon, x, y)) ?? null
}

const rank = { none: 0, green: 1, amber: 2, red: 3 } as const
export const worse = (a: Level, b: Level) => rank[a] > rank[b]

// The zone at a point on the unit's floor (model coordinates: x east, y north, meters).
export function zoneAt(unit: Unit, x: number, y: number): Zone {
  const p = unit.profile
  const v = heatAt(unit, x, y)
  const room = roomAt(unit, x, y)
  if (v === 0 && !room) return { level: 'none', title: '', text: '', key: 'none' }
  const where = room && room.type !== 'Other' ? `${room.type}: ` : ''

  // measured problems that apply here, worst first
  const near = unit.problems
    .filter((q) => q.severity !== 'low' && q.floor === unit.floor)
    .filter((q) => {
      if (ROOM_KINDS.has(q.kind)) return !!room && inside(room.polygon, q.x, q.y)
      const reach = REACH[q.kind]
      return reach !== undefined && Math.hypot(q.x - x, q.y - y) <= reach
    })
    .sort((a, b) => (a.severity === b.severity ? Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y) : a.severity === 'high' ? -1 : 1))
  const hit = near[0]

  // stairs are a no-go for a wheelchair and a hazard for the others
  if (room && STAIRS.test(room.type) && p !== 'deaf') {
    const level = p === 'wheelchair' ? 'red' : 'amber'
    const text = p === 'wheelchair' ? "Stairs: a wheelchair can't go this way." : 'Stairs: a fall and trip risk.'
    return { level, title: TITLE[p][level], text, key: `stairs:${room.id}` }
  }

  let level: Level = v === 2 ? 'red' : v === 3 ? 'amber' : 'green'
  if (hit) {
    // right at an obstacle or a step, a blind resident is about to walk into it
    const atIt = p === 'blind' && (hit.kind === 'path_obstacle' || hit.kind === 'threshold') && Math.hypot(hit.x - x, hit.y - y) <= 0.6
    const l: Level = hit.severity === 'high' || atIt ? 'red' : 'amber'
    if (worse(l, level)) level = l
  }
  if (level === 'green') return { level, title: TITLE[p].green, text: `${where}${HEAT_TEXT[p][4]}`, key: `green:${room?.id ?? ''}` }
  // the measured problem explains a zone best; the heat map's meaning is the fallback
  const text = hit ? hit.text : `${where}${HEAT_TEXT[p][v === 2 ? 2 : 3]}`
  return { level, title: TITLE[p][level], text, key: `${level}:${hit?.id ?? room?.id ?? v}`, problem: hit }
}
