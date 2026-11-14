import { useCallback, useEffect, useMemo, useState } from 'react'
import MapView, { scoreColor } from './components/MapView'
import FloorPlan from './components/FloorPlan'
import { combine, fetchBuildings, fetchUnit } from './api'
import { PROFILES, type Building, type Profile, type Unit } from './types'
import './App.css'

// Starter UI: profile menu -> map of homes -> building panel -> apartment heat map.
// See docs/FRONTEND_BRIEF.md for the full set of screens to build.
export default function App() {
  const [all, setAll] = useState<Building[]>([])
  const [profiles, setProfiles] = useState<Profile[]>(['wheelchair'])
  const [how, setHow] = useState<'min' | 'avg'>('min')
  const [passingOnly, setPassingOnly] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [unit, setUnit] = useState<Unit | null>(null)
  const [furnished, setFurnished] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchBuildings().then(setAll).catch((e) => setError(String(e)))
  }, [])

  const buildings = useMemo(() => {
    if (!profiles.length) return []
    return all
      .map((b) => ({ ...b, combined: combine(b.scores, profiles, how) }))
      .filter((b) => !passingOnly || b.combined.score >= 60)
      .sort((a, b) => b.combined.score - a.combined.score)
  }, [all, profiles, how, passingOnly])

  const selected = buildings.find((b) => b.id === selectedId) ?? null

  useEffect(() => {
    if (!selected) return setUnit(null)
    fetchUnit('u_sample_001', profiles[0] ?? 'wheelchair', furnished).then(setUnit).catch((e) => setError(String(e)))
  }, [selected, furnished, profiles])

  const onSelect = useCallback((id: string) => setSelectedId(id), [])
  const toggle = (p: Profile) => setProfiles((ps) => (ps.includes(p) ? ps.filter((x) => x !== p) : [...ps, p]))

  return (
    <div className="app">
      <aside className="sidebar" aria-label="Accessibility needs">
        <h1>NavEasy</h1>
        <p className="muted">Houston · sample data</p>
        <fieldset>
          <legend>My needs</legend>
          {PROFILES.map((p) => (
            <label key={p.id} className="check">
              <input type="checkbox" checked={profiles.includes(p.id)} onChange={() => toggle(p.id)} />
              <span className="swatch" style={{ background: p.color }} aria-hidden />
              {p.label}
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend>Combine several needs</legend>
          <label className="check">
            <input type="radio" name="how" checked={how === 'min'} onChange={() => setHow('min')} /> Weakest need decides
          </label>
          <label className="check">
            <input type="radio" name="how" checked={how === 'avg'} onChange={() => setHow('avg')} /> Average
          </label>
        </fieldset>
        <label className="check">
          <input type="checkbox" checked={passingOnly} onChange={(e) => setPassingOnly(e.target.checked)} /> Only homes
          that pass (60+)
        </label>
        <h2>{buildings.length} homes</h2>
        <ol className="list">
          {buildings.slice(0, 15).map((b) => (
            <li key={b.id}>
              <button onClick={() => setSelectedId(b.id)} aria-pressed={b.id === selectedId}>
                <span className="dot" style={{ background: scoreColor(b.combined!.score) }} aria-hidden />
                {b.name} <strong>{b.combined!.score}</strong>
              </button>
            </li>
          ))}
        </ol>
        {error && <p role="alert" className="error">{error}</p>}
      </aside>

      <main className="main">
        <MapView buildings={buildings} selectedId={selectedId} onSelect={onSelect} />
      </main>

      {selected && (
        <aside className="panel" aria-label={`Details for ${selected.name}`}>
          <button className="close" onClick={() => setSelectedId(null)} aria-label="Close">
            ×
          </button>
          <h2>{selected.name}</h2>
          <p className="muted">
            {selected.stories} stories · {selected.elevator ? 'elevator' : selected.walkup ? 'walk-up' : 'no elevator data'} ·
            built {selected.year_built} · ${selected.rent_min}–${selected.rent_max}
          </p>
          <table className="scores">
            <tbody>
              {PROFILES.map((p) => (
                <tr key={p.id}>
                  <th scope="row">{p.label}</th>
                  <td>{selected.scores[p.id].score}</td>
                  <td className="muted">{selected.scores[p.id].confidence} confidence</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Inside a scanned unit</h3>
          <label className="check">
            <input type="checkbox" checked={furnished} onChange={(e) => setFurnished(e.target.checked)} /> With furniture
          </label>
          {unit && (
            <>
              <FloorPlan unit={unit} />
              <p className="legend">
                <span style={{ background: '#9fdc9f' }} /> 36 in clear <span style={{ background: '#f5cf73' }} /> 32–36 in{' '}
                <span style={{ background: '#f08a8a' }} /> under 32 in
              </p>
              <ul className="problems">
                {unit.problems.map((p) => (
                  <li key={p.id} className={p.severity}>
                    {p.text}
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      )}
    </div>
  )
}
