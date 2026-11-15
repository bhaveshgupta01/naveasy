import { useEffect, useState } from 'react'
import { aerialUrl, fetchBuilding, fetchCrosswalk, fetchRoute, obliqueUrl } from '../api'
import { CATEGORIES, PROFILES, scoreWord, type BuildingDetail, type Category, type Profile, type Route } from '../types'
import { scoreColor } from '../colors'
import Outside3D from './Outside3D'

const COMPONENT_LABEL: Record<string, string> = {
  vertical_access: 'Getting upstairs',
  approach: 'Approach from the street',
  listing_evidence: 'Listing evidence',
  age: 'Building age',
}
const STATUS_WORD = { green: 'Good', amber: 'Caution', red: 'Barrier', grey: 'Unknown' }

interface Props {
  id: string
  profiles: Profile[]
  route: Route | null
  autoRoute?: { category: Category; profile: Profile } | null
  onRoute: (r: Route | null) => void
  onOpenUnit: (modelId: string) => void
  onClose: () => void
}

export default function BuildingPanel({ id, profiles, route, autoRoute, onRoute, onOpenUnit, onClose }: Props) {
  const [b, setB] = useState<BuildingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [routeProfile, setRouteProfile] = useState<Profile>(profiles[0] ?? 'wheelchair')
  const [loading, setLoading] = useState<Category | null>(null)
  const [spot, setSpot] = useState<number | null>(null)
  const [crosswalk, setCrosswalk] = useState<Record<number, string>>({})
  const [showAll, setShowAll] = useState(false)
  const [bigView, setBigView] = useState<string | null>(null)
  const [outside, setOutside] = useState(() => new URLSearchParams(window.location.search).has('outside'))

  useEffect(() => {
    setB(null)
    setError(null)
    fetchBuilding(id, profiles.length ? profiles : ['wheelchair'])
      .then(setB)
      .catch((e) => setError(String(e.message ?? e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, profiles.join(',')])

  const go = (category: Category, profile = routeProfile) => {
    setLoading(category)
    setSpot(null)
    setCrosswalk({})
    setError(null)
    fetchRoute(id, category, profile)
      .then(onRoute)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(null))
  }

  useEffect(() => {
    if (autoRoute) {
      setRouteProfile(autoRoute.profile)
      go(autoRoute.category, autoRoute.profile)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRoute])

  if (!b)
    return (
      <aside className="panel" aria-label="Home details">
        <button className="close" onClick={onClose} aria-label="Close">
          ×
        </button>
        {error ? <p role="alert" className="error">{error}</p> : <p className="muted">Loading…</p>}
      </aside>
    )

  const units = b.units.filter((u) => showAll || u.dwelling)
  const dwellings = b.units.filter((u) => u.dwelling).length
  return (
    <aside className="panel" aria-label={`Details for ${b.name}`}>
      <button className="close" onClick={onClose} aria-label="Close">
        ×
      </button>
      {b.photo && <img className="photo" src={b.photo} alt={`Photo of ${b.name}`} referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.display = "none")} />}
      <h2>{b.name}</h2>
      <p className="muted">
        {b.address}
        <br />
        {b.kind === 'home' ? 'Home' : b.kind === 'public_building' ? 'Public building' : 'Apartment community'}
        {b.stories ? ` · ${b.stories} ${b.stories === 1 ? 'story' : 'stories'}` : ''}
        {b.year_built ? ` · built ${b.year_built}` : ''}
        {b.rent_min ? ` · from $${b.rent_min.toLocaleString()}/mo` : b.price ? ` · $${b.price.toLocaleString()}` : ''}
      </p>

      <div className="bigscore" style={{ borderColor: scoreColor(b.combined!.score) }}>
        <span className="num">{b.combined!.score}</span>
        <span>
          <strong>{scoreWord(b.combined!.score)}</strong>
          <br />
          <span className="muted">
            {profiles.length > 1 ? 'weakest of your needs' : PROFILES.find((p) => p.id === profiles[0])?.label} · {b.combined!.confidence} confidence
          </span>
        </span>
      </div>
      <p className="fine">
        {b.score_note
          ? b.score_note
          : b.inside
          ? `Half building, half inside: ${b.inside.units_measured} unit${b.inside.units_measured === 1 ? '' : 's'} measured from 3D scans.`
          : 'Building evidence only: no unit here has a measured floor plan yet, so confidence is low.'}
      </p>

      <table className="scores">
        <thead>
          <tr>
            <th scope="col">Need</th>
            <th scope="col">Home</th>
            <th scope="col">Neighborhood</th>
            <th scope="col">Building</th>
            <th scope="col">Best unit inside</th>
          </tr>
        </thead>
        <tbody>
          {PROFILES.map((p) => (
            <tr key={p.id} className={profiles.includes(p.id) ? 'sel' : ''}>
              <th scope="row">
                <span className="swatch" style={{ background: p.color }} aria-hidden /> {p.short}
              </th>
              <td>
                <strong>{b.scores[p.id].score}</strong>
              </td>
              <td>{b.locality_detail?.scores[p.id]?.score ?? '–'}</td>
              <td>{b.building_scores[p.id].score}</td>
              <td>{b.inside ? b.inside[p.id].best : '–'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Around this home</h3>
      <button className="primary" style={{ marginBottom: 8, width: '100%' }} onClick={() => setOutside(true)}>
        Orbit this {b.kind === 'home' ? 'home' : 'building'} in 3D{b.units.some((u) => u.splat) ? ' (drone model available)' : ''}
      </button>
      {outside && <Outside3D lat={b.lat} lon={b.lon} name={b.name} splatSid={b.units.find((u) => u.splat)?.model_id} onClose={() => setOutside(false)} />}
      <div className="around">
        {(['north', 'east', 'south', 'west'] as const)
          .filter((d) => !bigView || bigView === d)
          .map((d) => (
            <figure key={d} className={bigView === d ? 'big' : ''}>
              <img
                src={obliqueUrl(b.lat, b.lon, d)}
                alt={`Aerial photo of ${b.name} looking ${d}`}
                loading="lazy"
                tabIndex={0}
                role="button"
                onClick={() => setBigView(bigView === d ? null : d)}
                onKeyDown={(e) => e.key === 'Enter' && setBigView(bigView === d ? null : d)}
                onError={(e) => ((e.currentTarget.parentElement as HTMLElement).style.display = 'none')}
              />
              <figcaption>Looking {d}</figcaption>
            </figure>
          ))}
      </div>
      <p className="fine">Angled aerial photos © Vexcel Imaging US, Inc. Use them to check the entrance, steps, ramps and parking; we have not scored the approach yet.</p>

      <h3>Building</h3>
      <ul className="components">
        {Object.entries(b.components).map(([k, c]) => (
          <li key={k}>
            <meter min={0} max={1} low={0.45} high={0.8} optimum={1} value={c.value} aria-hidden />
            <span>
              <strong>{COMPONENT_LABEL[k] ?? k}:</strong> {c.why}
            </span>
          </li>
        ))}
      </ul>

      <h3>
        Scanned units <span className="muted">({dwellings})</span>
      </h3>
      <ul className="units">
        {units.map((u) => {
          const s = u.scores ? Math.min(...profiles.map((p) => u.scores![p].score)) : null
          return (
            <li key={u.model_id}>
              <button onClick={() => onOpenUnit(u.model_id)}>
                <span className="dot" style={{ background: s == null ? '#c9ccd1' : scoreColor(s) }} aria-hidden />
                <span className="grow">
                  {u.caption || 'Scan'}
                  {u.furnished_scan ? ' · furniture found' : ''}
                  <br />
                  <span className="muted">
                    {u.status === 'measured'
                      ? u.problems[0]?.text ?? `${u.stats?.doorways ?? 0} doorways measured, no problems for your needs`
                      : u.status === 'pending'
                        ? 'Not measured yet: open it to measure it now'
                        : 'No measured walls: 3D tour and Matterport’s 2D floor plan'}
                  </span>
                </span>
                {s != null && <strong>{s}</strong>}
              </button>
            </li>
          )
        })}
      </ul>
      {b.units.length > dwellings && (
        <button className="link" onClick={() => setShowAll(!showAll)}>
          {showAll ? 'Hide' : 'Show'} {b.units.length - dwellings} scans of shared spaces (lobby, gym, pool)
        </button>
      )}

      <h3>Neighborhood{b.locality_detail ? <span className="muted"> ({b.locality_detail.live ? 'routed walking times' : 'estimated from distance'})</span> : null}</h3>
      {b.locality_detail ? (
        <>
          <p>
            Neighborhood score <strong>{Math.min(...profiles.map((p) => b.locality_detail!.scores[p]?.score ?? 0))}</strong> for your needs: 70% how quickly you reach
            everyday places, 30% the streets themselves.
          </p>
          {b.locality_detail.streets ? (
            <ul className="reasons">
              <li>Sidewalks mapped along about {Math.round((b.locality_detail.streets.sidewalk_coverage ?? 0) * 100)}% of streets</li>
              {b.locality_detail.streets.lowered_kerb_share != null && <li>{Math.round(b.locality_detail.streets.lowered_kerb_share * 100)}% of recorded curbs are lowered or flush</li>}
              {b.locality_detail.streets.signalized_share != null && <li>{Math.round(b.locality_detail.streets.signalized_share * 100)}% of crossings have signals</li>}
              {b.locality_detail.streets.audible_share != null && <li>{Math.round(b.locality_detail.streets.audible_share * 100)}% of recorded signals have sound</li>}
              <li>{b.locality_detail.streets.benches_per_km ?? 0} benches per km of street</li>
            </ul>
          ) : (
            <p className="fine">Street details (sidewalks, curbs, signals) aren’t cached for this area yet, so the street part uses the city median.</p>
          )}
        </>
      ) : (
        <p className="fine">No neighborhood data here.</p>
      )}

      <h3>Getting to everyday places</h3>
      <div className="row wrap">
        <label>
          Route for{' '}
          <select value={routeProfile} onChange={(e) => setRouteProfile(e.target.value as Profile)}>
            {PROFILES.map((p) => (
              <option key={p.id} value={p.id}>
                {p.short}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="chips">
        {CATEGORIES.map((c) => {
          const n = b.nearest.find((x) => x.category === c.id)
          const m = n?.minutes[routeProfile] ?? Object.values(n?.minutes ?? {})[0]
          return (
            <button key={c.id} onClick={() => go(c.id)} aria-pressed={route?.category === c.id} disabled={loading !== null}>
              {loading === c.id ? '…' : c.label}
              {m != null && <span className="muted"> {n!.estimated ? '~' : ''}{Math.round(m)} min</span>}
            </button>
          )
        })}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {route && (
        <section className="route" aria-live="polite">
          <h4>
            {PROFILES.find((p) => p.id === route.profile)!.short} route to {route.destination.name ?? route.category}
          </h4>
          <p>
            <strong>{Math.round(route.minutes)} min</strong> · {route.meters < 1000 ? `${route.meters} m` : `${(route.meters / 1000).toFixed(1)} km`}
            {route.crossings != null ? ` · ${route.crossings} crossings` : ''} · {route.confidence} confidence
          </p>
          {route.elevation && route.elevation.height_m.length > 2 && (
            <figure className="elev">
              <svg viewBox="0 0 300 46" preserveAspectRatio="none" role="img" aria-label={`Height along the route: steepest slope ${route.elevation.max_grade_pct} percent, total climb ${route.elevation.climb_m} meters`}>
                <polyline
                  fill="none"
                  stroke="#1b3a6b"
                  strokeWidth="2"
                  points={route.elevation.height_m
                    .map((h, i, a) => `${(i / (a.length - 1)) * 300},${42 - (h / Math.max(3, ...a)) * 38}`)
                    .join(' ')}
                />
              </svg>
              <figcaption>
                Ground height along the route, from Vexcel’s aerial heights: steepest sustained slope <strong>{route.elevation.max_grade_pct}%</strong> (ADA walkway limit
                5%), total climb {route.elevation.climb_m} m.
              </figcaption>
            </figure>
          )}
          <ol className="stretches">
            {route.stretches.map((s, i) => (
              <li key={i} className={s.status}>
                <span className="tag">{STATUS_WORD[s.status]}</span> {s.why} <span className="muted">({s.meters} m)</span>
              </li>
            ))}
          </ol>
          {route.hard_spots.length > 0 && (
            <>
              <h4>Hard spots</h4>
              <ol className="spots">
                {route.hard_spots.map((h, i) => (
                  <li key={i}>
                    <button
                      onClick={() => {
                        setSpot(spot === i ? null : i)
                        if (h.type !== 'slope' && h.type !== 'no_bench' && !crosswalk[i])
                          fetchCrosswalk(h.lat, h.lon)
                            .then((c) => setCrosswalk((m) => ({ ...m, [i]: c.text })))
                            .catch(() => undefined)
                      }}
                      aria-expanded={spot === i}
                    >
                      <span className={`pin ${h.severity}`}>{i + 1}</span> {h.text}
                    </button>
                    {spot === i && (
                      <figure>
                        <img src={aerialUrl(h.lat, h.lon)} alt="Aerial photo of this spot" />
                        <figcaption>
                          {crosswalk[i] ? `${crosswalk[i]} ` : ''}Imagery © Vexcel Imaging US, Inc.
                        </figcaption>
                      </figure>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
          {route.notes.map((n, i) => (
            <p key={i} className="fine">
              {n}
            </p>
          ))}
          <button className="link" onClick={() => onRoute(null)}>
            Clear route
          </button>
        </section>
      )}

      <h3>Verify before you sign</h3>
      <ul className="reasons">
        {b.checklist.map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>
      {b.url && (
        <p>
          <a href={b.url} target="_blank" rel="noreferrer">
            Open the listing
          </a>
        </p>
      )}
    </aside>
  )
}
