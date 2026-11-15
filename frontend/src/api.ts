// All data comes from the NavEasy API (backend/app/main.py). Default: http://localhost:8000.
import type { AgentStep, Building, BuildingDetail, Category, City, FloorplanImage, FurnishPlan, FurnishScore, Furniture, Profile, Relocation, Route, Score, UiAction, Unit } from './types'

// Dev: the API runs on :8000. A build made with VITE_API_BASE= (empty) is served by the API itself (one address, so one
// public link works for the app and the data), and calls its own origin.
const BASE = import.meta.env.VITE_API_BASE as string | undefined
export const API = BASE === undefined ? 'http://localhost:8000' : BASE || window.location.origin

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`)
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Request failed (${res.status})`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Request failed (${res.status})`)
  return res.json() as Promise<T>
}

export function combine(scores: Record<Profile, Score>, profiles: Profile[], how: 'min' | 'avg'): Score {
  const vals = profiles.map((p) => scores[p].score)
  const order = { low: 0, medium: 1, high: 2 } as const
  const conf = profiles.map((p) => scores[p].confidence).sort((a, b) => order[a] - order[b])[0]
  const score = how === 'min' ? Math.min(...vals) : Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)
  return { score, confidence: conf }
}

export interface Meta {
  mode: string
  cities: Record<City, { buildings: number; apartment_communities: number; homes: number; measured_inside: number; scans: number }>
  units_measured: number
  doorways_measured: number
  hero: { problem_unit?: string; furnished_unit?: string; plan_target?: string; la_origin?: string }
}

export type HexRow = [string, number, number, number, number, number[]] // h3, score, confidence 0..2, live, index of the weakest need, each need's score

export const fetchMeta = () => get<Meta>('/meta')
export const fetchBuildings = (city: City) => get<{ buildings: Building[] }>(`/map/buildings?city=${city}`).then((d) => d.buildings)
export const fetchHexes = (city: City, profiles: Profile[], how: string) =>
  get<{ cells: HexRow[] }>(`/map/hexes?city=${city}&profiles=${profiles.join(',')}&combine=${how}`).then((d) => d.cells)
export const fetchHex = (city: City, cell: string) =>
  get<{ h3: string; live: boolean; minutes: Record<Profile, Record<string, number>>; scores: Record<Profile, Score>; streets: Record<string, number | null> | null }>(
    `/map/hexes/${cell}?city=${city}`,
  )
export const fetchBuilding = (id: string, profiles: Profile[]) => get<BuildingDetail>(`/buildings/${id}?profiles=${profiles.join(',')}`)
export const fetchUnit = (modelId: string, profile: Profile, furnished: boolean, floor?: number) =>
  get<Unit>(`/units/${modelId}/heatmap?profile=${profile}&furnished=${furnished}${floor == null ? '' : `&floor=${floor}`}`)
export const fetchUnitCustom = (modelId: string, profile: Profile, furniture: Furniture[]) =>
  post<Unit>('/units/heatmap', { model_id: modelId, profile, furniture })
export const fetchRoute = (buildingId: string, category: Category, profile: Profile) =>
  get<Route>(`/routes?building_id=${buildingId}&category=${category}&profile=${profile}`)
export const fetchFurnishScore = (modelId: string, profiles: Profile[]) => get<FurnishScore>(`/furnish/score?model_id=${modelId}&profiles=${profiles.join(',')}`)
export const planFurnishing = (body: { target_model_id: string; profiles: Profile[]; inventory?: { label: string }[]; from_model_id?: string }) =>
  post<FurnishPlan>('/furnish/plan', body)
export const relocate = (body: Record<string, unknown>) => post<Relocation>('/relocate', body)
export const chat = (messages: { role: string; content: string }[], context: Record<string, unknown>) =>
  post<{ text: string; ui_actions: UiAction[]; state?: Record<string, unknown>; steps?: AgentStep[]; mode?: string }>('/agent/chat', { messages, context })
export const fetchProgress = (rid: string) => get<{ steps: AgentStep[] }>(`/agent/progress/${rid}`).then((d) => d.steps)
export const aerialUrl = (lat: number, lon: number) => `${API}/aerial?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}&meters=30`
export const obliqueUrl = (lat: number, lon: number, direction: string) => `${API}/oblique?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}&direction=${direction}`
export const fetchCrosswalk = (lat: number, lon: number) => get<{ seen: boolean; text: string }>(`/crosswalk?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}`)
export const fetchNarration = (modelId: string, profile: Profile) => get<{ title: string; paragraphs: string[] }>(`/units/${modelId}/narration?profile=${profile}`)
export const TILE_URL = `${API}/tiles/{z}/{x}/{y}.jpg`

export const fetchFloorplans = (modelId: string) => get<FloorplanImage[]>(`/units/${modelId}/floorplans`)
