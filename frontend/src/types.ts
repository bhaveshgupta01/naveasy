// Shapes from docs/API_CONTRACT.md. Keep in sync with backend/app/main.py.

export type Profile = 'wheelchair' | 'blind' | 'deaf' | 'older'
export type Confidence = 'low' | 'medium' | 'high'

export interface Score {
  score: number // 0..100
  confidence: Confidence
}

export interface Building {
  id: string
  kind: 'apartment_community' | 'home'
  name: string
  lat: number
  lon: number
  stories: number
  elevator: boolean
  walkup: boolean | null
  year_built: number
  rent_min: number
  rent_max: number
  units_scanned: number
  scores: Record<Profile, Score>
  combined?: Score
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
}

export interface Room {
  id: string
  type: string
  polygon: [number, number][]
}

export interface Problem {
  id: string
  severity: 'high' | 'medium' | 'low'
  profile: Profile
  x: number
  y: number
  text: string
}

// values[row][col]: 0 outside, 1 blocked, 2 red, 3 amber, 4 green
export interface HeatGrid {
  cell_m: number
  origin: [number, number]
  width: number
  height: number
  values: number[][]
}

export interface Unit {
  model_id: string
  caption: string
  units: 'meters'
  bounds: [number, number, number, number]
  walls: [number, number, number, number][]
  doors: Door[]
  rooms: Room[]
  problems: Problem[]
  heat: HeatGrid
}

export const PROFILES: { id: Profile; label: string; color: string }[] = [
  { id: 'wheelchair', label: 'Wheelchair', color: '#d9363e' },
  { id: 'blind', label: 'Blind or low vision', color: '#7b4fd6' },
  { id: 'deaf', label: 'Deaf or hard of hearing', color: '#2f6fd6' },
  { id: 'older', label: 'Older adults, low stamina', color: '#2e9e5b' },
]
