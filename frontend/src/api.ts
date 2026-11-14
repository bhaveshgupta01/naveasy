// Talks to the NavEasy API when VITE_API_BASE is set (e.g. http://localhost:8000),
// otherwise reads the synthetic files in public/mock so the UI works with no backend.
import type { Building, Profile, Score, Unit } from './types'

const API = import.meta.env.VITE_API_BASE as string | undefined

async function get<T>(apiPath: string, mockFile: string): Promise<T> {
  const url = API ? `${API}${apiPath}` : `/mock/${mockFile}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  return res.json() as Promise<T>
}

export function combine(scores: Record<Profile, Score>, profiles: Profile[], how: 'min' | 'avg'): Score {
  const vals = profiles.map((p) => scores[p].score)
  const order = { low: 0, medium: 1, high: 2 } as const
  const conf = profiles.map((p) => scores[p].confidence).sort((a, b) => order[a] - order[b])[0]
  const score = how === 'min' ? Math.min(...vals) : Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)
  return { score, confidence: conf }
}

export async function fetchBuildings(): Promise<Building[]> {
  const data = await get<{ buildings: Building[] }>('/map/buildings?profiles=wheelchair', 'buildings.json')
  return data.buildings
}

export async function fetchUnit(modelId: string, profile: Profile, furnished: boolean): Promise<Unit> {
  if (API) return get<Unit>(`/units/${modelId}/heatmap?profile=${profile}&furnished=${furnished}`, '')
  // Mock file holds both versions; pick one so the shape matches the API.
  const raw = await get<Unit & { heat: Record<string, { empty: Unit['heat']; furnished: Unit['heat'] }> }>(
    '', `unit_${modelId}.json`,
  )
  const byProfile = raw.heat[profile] ?? raw.heat.wheelchair
  return { ...raw, heat: furnished ? byProfile.furnished : byProfile.empty }
}
