import { useEffect, useState } from 'react'
import { API, fetchFloorplans } from '../api'
import type { FloorplanImage } from '../types'

// Matterport's own 2D floor plans of a scan: a schematic (walls, doorways, room areas in m2) and a top-down photo.
// They exist even when the Model API has no wall graph for the scan, so every scanned unit has a 2D view.
export default function MatterportPlan({ modelId }: { modelId: string }) {
  const [plans, setPlans] = useState<FloorplanImage[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState<'schematic' | 'photo'>('schematic')
  const [floor, setFloor] = useState(0)

  useEffect(() => {
    fetchFloorplans(modelId)
      .then((p) => {
        setPlans(p)
        if (p.length && !p.some((x) => x.kind === 'schematic')) setKind('photo')
        if (p.length) setFloor(p[0].floor)
      })
      .catch((e) => setError(String(e.message ?? e)))
  }, [modelId])

  if (error) return <p className="note">Matterport's floor plan isn't available for this scan ({error}). The 3D tour still works.</p>
  if (!plans) return <p className="muted">Loading Matterport's floor plan…</p>
  if (!plans.length) return <p className="note">Matterport has no 2D floor plan for this scan. The 3D tour still works.</p>

  const floors = [...new Map(plans.map((p) => [p.floor, p.label])).entries()]
  const kinds = new Set(plans.filter((p) => p.floor === floor).map((p) => p.kind))
  const img = plans.find((p) => p.floor === floor && p.kind === kind) ?? plans.find((p) => p.floor === floor)!

  return (
    <div className="mp-plan">
      <div className="row wrap">
        <div className="seg" role="group" aria-label="Floor plan style">
          {(['schematic', 'photo'] as const)
            .filter((k) => kinds.has(k))
            .map((k) => (
              <button key={k} className={img.kind === k ? 'on' : ''} aria-pressed={img.kind === k} onClick={() => setKind(k)}>
                {k === 'schematic' ? 'Plan with room areas' : 'Top-down photo'}
              </button>
            ))}
        </div>
        {floors.length > 1 && (
          <label>
            Floor{' '}
            <select value={floor} onChange={(e) => setFloor(Number(e.target.value))}>
              {floors.map(([f, label]) => (
                <option key={f} value={f}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <img
        src={`${API}${img.url}`}
        alt={`Matterport ${img.kind === 'schematic' ? 'schematic floor plan with room areas in square meters' : 'top-down photo floor plan'}, ${img.label}`}
      />
      <p className="fine">
        Floor plan © Matterport, made from the 3D scan. Room areas are in square meters (1 m² ≈ 10.8 sq ft). NavEasy can't measure doorways or
        build a heat map for this scan, because Matterport didn't release its walls and doorways as data.
      </p>
    </div>
  )
}
