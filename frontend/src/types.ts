// Shapes from docs/API_CONTRACT.md. Keep in sync with backend/app/main.py.
import { NEED_COLOR } from './colors'

export type Profile = 'wheelchair' | 'blind' | 'deaf' | 'older'
export type Confidence = 'low' | 'medium' | 'high'
export type City = 'houston' | 'la' | 'princeton' // princeton: the HackPrinceton venue only (pipeline/venue.py)
export type Category = 'hospital' | 'clinic' | 'pharmacy' | 'grocery' | 'transit' | 'restaurant' | 'school' | 'park'

export interface Score {
  score: number // 0..100
  confidence: Confidence
}

export interface Building {
  id: string
  kind: 'apartment_community' | 'home' | 'public_building'
  score_note?: string | null // public buildings: what the score measures
  name: string
  address?: string
  city: City
  lat: number
  lon: number
  stories: number | null
  elevator: boolean
  walkup: boolean | null
  year_built: number | null
  rent_min: number | null
  rent_max: number | null
  price?: number | null
  beds?: number | null
  baths?: number | null
  sqft?: number | null
  units_scanned: number
  measured_inside: boolean
  splat?: boolean
  splat_sid?: string | null // Matterport model with an aerial drone model (Gaussian splat)
  locality_scores?: Record<Profile, Score> | null
  scores: Record<Profile, Score>
  combined?: Score
}

export interface UnitRef {
  model_id: string
  caption: string | null
  status: 'measured' | 'no_floor_plan' | 'pending'
  dwelling: boolean
  scores: Record<Profile, Score> | null
  stats: UnitStats | null
  furnished_scan: boolean
  splat?: boolean
  problems: { kind: string; severity: string; profile: Profile; text: string }[]
}

export interface BuildingDetail extends Building {
  photo?: string | null
  url?: string | null
  total_units?: number | null
  units_available?: number | null
  components: Record<string, { value: number; why: string }>
  building_scores: Record<Profile, Score>
  inside: (Record<Profile, { best: number; best_model: string; share_pass: number }> & { units_measured: number }) | null
  units: UnitRef[]
  nearest: { category: Category; label: string; minutes: Partial<Record<Profile, number>>; estimated: boolean }[]
  locality_detail: { scores: Partial<Record<Profile, Score>>; streets: Record<string, number | null> | null; live: boolean } | null
  checklist: string[]
}

export interface Door {
  id: string
  x: number
  y: number
  angle_deg: number
  width_m: number
  est_clear_in: number
  threshold_m: number
  kind: 'doorway' | 'opening'
  status: 'pass' | 'tight' | 'fail'
  is_door: boolean
  entry: boolean
  minor: boolean
  exterior: boolean
}

export interface Room {
  id: string
  type: string
  polygon: [number, number][]
  centroid: [number, number]
  area_m2: number
}

export interface Problem {
  id: string
  kind: string
  severity: 'high' | 'medium' | 'low'
  profile: Profile
  floor: number
  x: number
  y: number
  text: string
}

export interface Furniture {
  id: string
  label: string
  rect: [number, number, number, number]
  floor?: number
  room?: string
  tall?: boolean
}

// values[row][col]: 0 outside, 1 blocked, 2 problem, 3 caution, 4 good (row 0 = lowest y)
export interface HeatGrid {
  cell_m: number
  origin: [number, number]
  width: number
  height: number
  values: number[][]
}

export interface UnitStats {
  doorways: number
  doorways_pass: number
  narrowest_main_door_in: number | null
  reach36_share: number
  bed_to_bathroom_m: number | null
  visible_share: number
  floors: number
  area_m2: number
  entry_found: boolean
}

export interface Unit {
  model_id: string
  caption: string
  has_graph: boolean
  message?: string
  profile: Profile
  furnished: boolean
  floor: number
  floors: { index: number; label: string }[]
  entry_floor: number
  scan_date: string | null
  floor_z: number
  bounds: [number, number, number, number]
  walls: [number, number, number, number][]
  doors: Door[]
  windows: { x: number; y: number; angle_deg: number; width_m: number }[]
  rooms: Room[]
  furniture: Furniture[]
  turning_circles: { x: number; y: number; r: number }[]
  paths: { from: string; to: string; meters: number; turns: number; points: [number, number][] }[]
  view_points?: { seat: [number, number] | null; kitchen: [number, number] | null }
  problems: Problem[]
  scores: Record<Profile, Score>
  checks: Record<string, { weight: number; value: number }>
  stats: UnitStats
  legend: Record<string, string>
  heat: HeatGrid
  has_furniture_scan?: boolean
  tour_url?: string
  fixtures?: [number, number, number][] // built-ins from the 3D mesh: [row, first col, last col + 1] on the heat grid
  fixtures_source?: 'mesh' | null
  defurnished_id?: string | null // Matterport's furniture-removed view of this scan (same scan positions)
}

// Matterport's own 2D floor plan images, available even for scans without a wall graph
export interface FloorplanImage {
  floor: number
  label: string
  kind: 'schematic' | 'photo'
  url: string
}

export interface Route {
  id: string
  profile: Profile
  category: Category | null
  destination: { lat: number; lon: number; name: string | null }
  origin: { lat: number; lon: number }
  minutes: number
  meters: number
  crossings: number | null
  crossings_with_curb_data: number
  confidence: Confidence
  stretches: { coords: [number, number][]; status: 'green' | 'amber' | 'red' | 'grey'; why: string; meters: number }[]
  hard_spots: { lon: number; lat: number; type: string; severity: string; text: string }[]
  steps: { text: string; meters: number }[]
  notes: string[]
  unknown_share: number
  elevation: { dist_m: number[]; height_m: number[]; max_grade_pct: number; climb_m: number } | null
}

export interface FurnishScore {
  model_id: string
  score_empty: Record<Profile, Score>
  score_furnished: Record<Profile, Score>
  combined_empty: number
  combined_furnished: number
  furniture_cost: number
  furniture: Furniture[]
  suggestions: { item_id: string; label: string; action: string; to: number[] | null; gain: number; text: string }[]
  note: string
}

export interface FurnishPlan {
  target_model_id: string
  score_empty: number
  score_before: number
  score_after: number
  before_label: string
  ordinary_scores: number[]
  ordinary_min: number
  ordinary_max: number
  ordinary_blocked: number
  placements: Furniture[]
  typical_placements: Furniture[]
  wont_fit: { label: string; reason: string }[]
  reasons: string[]
  inventory?: { label: string; w: number; d: number }[]
}

export interface Relocation {
  origin: { address?: string; name?: string; lat: number; lon: number; city: City; minutes: Record<string, number>; live: boolean }
  target: City
  target_name: string
  candidates: {
    h3: string
    name: string
    lat: number
    lon: number
    match: number
    comparison: Record<string, [number, number]>
    minutes_estimated: boolean
    homes: { id: string; name: string; score: number; rent_min: number | null; measured_inside: boolean }[]
    reason: string
  }[]
  message?: string
  note: string
}

export type UiAction =
  | { type: 'set_map_view'; center: [number, number]; zoom: number; profiles?: Profile[]; city?: City }
  | { type: 'highlight_buildings'; ids: string[] }
  | { type: 'open_building'; id: string }
  | { type: 'open_unit'; model_id: string; profile?: Profile; problem_id?: string; tab?: string; plan?: FurnishPlan }
  | { type: 'show_route'; building_id: string; category: Category; profile: Profile }
  | { type: 'show_relocation'; request: Record<string, unknown> }
  | { type: 'open_drone'; id: string }

// each need's identity color (no red or green: see colors.ts)
export const PROFILES: { id: Profile; label: string; short: string; color: string }[] = [
  { id: 'wheelchair', label: 'Wheelchair', short: 'Wheelchair', color: NEED_COLOR.wheelchair },
  { id: 'blind', label: 'Blind or low vision', short: 'Blind', color: NEED_COLOR.blind },
  { id: 'deaf', label: 'Deaf or hard of hearing', short: 'Deaf', color: NEED_COLOR.deaf },
  { id: 'older', label: 'Older adults, low stamina', short: 'Older adults', color: NEED_COLOR.older },
]

export const CATEGORIES: { id: Category; label: string }[] = [
  { id: 'pharmacy', label: 'Pharmacy' },
  { id: 'clinic', label: 'Clinic' },
  { id: 'grocery', label: 'Grocery' },
  { id: 'transit', label: 'Bus stop' },
  { id: 'hospital', label: 'Hospital' },
  { id: 'school', label: 'School' },
  { id: 'park', label: 'Park' },
  { id: 'restaurant', label: 'Restaurants' },
]

export function scoreWord(score: number): string {
  if (score >= 75) return 'meets most checks'
  if (score >= 50) return 'some barriers'
  return 'major barriers'
}
