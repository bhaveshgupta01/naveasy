import { useState } from 'react'
import { relocate } from '../api'
import { CATEGORIES, type Profile, type Relocation } from '../types'
import { scoreColor } from './MapView'

interface Props {
  profiles: Profile[]
  defaultAddress?: string
  result: Relocation | null
  onResult: (r: Relocation | null) => void
  onGo: (c: Relocation['candidates'][number], target: Relocation['target']) => void
  onOpenHome: (id: string, target: Relocation['target']) => void
}

const LABEL: Record<string, string> = Object.fromEntries(CATEGORIES.map((c) => [c.id, c.label]))

export default function RelocatePanel({ profiles, defaultAddress, result, onResult, onGo, onOpenHome }: Props) {
  const [address, setAddress] = useState(defaultAddress ?? '')
  const [budget, setBudget] = useState('')
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    relocate({ origin: { address }, profiles: profiles.length ? profiles : ['wheelchair'], budget: budget ? Number(budget) : null, target: target || null })
      .then(onResult)
      .catch((err) => setError(String(err.message ?? err)))
      .finally(() => setBusy(false))
  }

  return (
    <div className="relocate">
      <form onSubmit={run}>
        <label htmlFor="addr">Where do you live now? (any address in Los Angeles or Houston)</label>
        <input id="addr" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="1200 W 7th St, Los Angeles, CA" required />
        <label htmlFor="target">Moving to</label>
        <select id="target" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">The other city</option>
          <option value="houston">Houston (another neighborhood if I live there)</option>
          <option value="la">Los Angeles (another neighborhood if I live there)</option>
        </select>
        <label htmlFor="budget">Monthly budget (optional)</label>
        <input id="budget" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value.replace(/\D/g, ''))} placeholder="2000" />
        <button className="primary" disabled={busy}>
          {busy ? 'Measuring your neighborhood…' : 'Find matching neighborhoods'}
        </button>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {result && (
        <div aria-live="polite">
          {result.message && <p className="error">{result.message}</p>}
          <p className="muted">
            Neighborhoods in {result.target_name} where everyday places are as close as at {result.origin.address ?? 'your address'}, for your needs. Minutes at
            home are {result.origin.live ? 'routed for your profile' : 'estimated'}.
          </p>
          <ol className="cands">
            {result.candidates.map((c) => (
              <li key={c.h3}>
                <button className="cand-head" onClick={() => onGo(c, result.target)}>
                  <span className="match" style={{ background: scoreColor(c.match) }}>
                    {c.match}
                  </span>
                  <strong>{c.name}</strong>
                  <span className="muted">match · show on map</span>
                </button>
                <p>{c.reason}</p>
                <table className="compare">
                  <thead>
                    <tr>
                      <th scope="col">Minutes to</th>
                      <th scope="col">Now</th>
                      <th scope="col">Here{c.minutes_estimated ? ' (est.)' : ''}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(c.comparison).map(([k, [now, there]]) => (
                      <tr key={k}>
                        <th scope="row">{LABEL[k] ?? k}</th>
                        <td>{Math.round(now)}</td>
                        <td className={there <= now + 1 ? 'better' : there > now + 5 ? 'worse' : ''}>
                          {Math.round(there)} {there < now - 1 ? '▼ closer' : there > now + 2 ? '▲ farther' : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <ul className="list">
                  {c.homes.map((h) => (
                    <li key={h.id}>
                      <button onClick={() => onOpenHome(h.id, result.target)}>
                        <span className="dot" style={{ background: scoreColor(h.score) }} aria-hidden />
                        <span className="grow">
                          {h.name}
                          {h.rent_min ? <span className="muted"> from ${h.rent_min.toLocaleString()}</span> : null}
                        </span>
                        <strong>{h.score}</strong>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
          <p className="fine">{result.note}</p>
        </div>
      )}
    </div>
  )
}
