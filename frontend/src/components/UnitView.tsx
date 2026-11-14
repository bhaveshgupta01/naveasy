import { useEffect, useState } from 'react'
import FloorPlan, { HEAT } from './FloorPlan'
import Tour3D from './Tour3D'
import { fetchFurnishScore, fetchUnit, fetchUnitCustom, planFurnishing, type Meta } from '../api'
import { PROFILES, scoreWord, type FurnishPlan, type FurnishScore, type Furniture, type Problem, type Profile, type Unit } from '../types'

const CHECK_LABEL: Record<string, string> = {
  entry_door: 'Entry door width',
  doors: 'Inside doorways',
  rooms_reachable: 'Main rooms reachable',
  turning_space: '60 in turning space',
  thresholds: 'Thresholds',
  floor_reachable: 'Floor reachable at 36 in',
  no_stairs: 'No stairs inside',
  bed_to_bathroom: 'Bed to bathroom distance',
  entry_to_kitchen: 'Entry to kitchen distance',
  bathroom_space: 'Bathroom size',
  simple_paths: 'Simple paths (few turns)',
  level_changes: 'No level changes',
  clear_paths: 'Paths clear of furniture',
  open_sightlines: 'Open sightlines',
  entry_visible_from_seat: 'Entry visible from the main seat',
  entry_visible_from_kitchen: 'Entry visible from the kitchen',
  daylight: 'Windows in main rooms',
}
const DEFAULT_PIECES = ['queen bed', 'nightstand', 'dresser', 'sofa', 'tv stand', 'dining table', 'desk', 'armchair']

interface Props {
  modelId: string
  profiles: Profile[]
  initialProfile?: Profile
  initialTab?: 'heat' | 'score' | 'plan' | 'tour'
  initialPlan?: FurnishPlan
  autoPlanFrom?: string
  meta: Meta | null
  onClose: () => void
}

export default function UnitView({ modelId, profiles, initialProfile, initialTab, initialPlan, autoPlanFrom, meta, onClose }: Props) {
  const [profile, setProfile] = useState<Profile>(initialProfile ?? profiles[0] ?? 'wheelchair')
  const [furnished, setFurnished] = useState(false)
  const [floor, setFloor] = useState<number | undefined>(undefined)
  const [unit, setUnit] = useState<Unit | null>(null)
  const [tab, setTab] = useState(initialTab ?? 'heat')
  const [focus, setFocus] = useState<Problem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fscore, setFscore] = useState<FurnishScore | null>(null)
  const [ghost, setGhost] = useState<Furniture[] | undefined>()
  const [plan, setPlan] = useState<FurnishPlan | null>(initialPlan ?? null)
  const [planUnit, setPlanUnit] = useState<Unit | null>(null)
  const [showTypical, setShowTypical] = useState(false)
  const [pieces, setPieces] = useState(DEFAULT_PIECES.join(', '))
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setError(null)
    fetchUnit(modelId, profile, furnished, floor)
      .then((u) => {
        setUnit(u)
        if (u.has_graph === false) setError(u.message ?? 'No floor plan for this scan.')
      })
      .catch((e) => setError(String(e.message ?? e)))
  }, [modelId, profile, furnished, floor])

  useEffect(() => {
    if (tab !== 'score' || fscore) return
    fetchFurnishScore(modelId, profiles.length ? profiles : [profile])
      .then((s) => {
        setFscore(s)
        setFurnished(true)
      })
      .catch((e) => setError(String(e.message ?? e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  useEffect(() => {
    if (!plan) return setPlanUnit(null)
    fetchUnitCustom(modelId, profile, showTypical ? plan.typical_placements : plan.placements)
      .then(setPlanUnit)
      .catch((e) => setError(String(e.message ?? e)))
  }, [plan, showTypical, profile, modelId])

  const runPlan = (fromModel?: string) => {
    setBusy(true)
    setError(null)
    planFurnishing({
      target_model_id: modelId,
      profiles: profiles.length ? profiles : [profile],
      ...(fromModel ? { from_model_id: fromModel } : { inventory: pieces.split(',').map((p) => ({ label: p.trim().toLowerCase() })).filter((p) => p.label) }),
    })
      .then(setPlan)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setBusy(false))
  }

  useEffect(() => {
    if (autoPlanFrom && !initialPlan) runPlan(autoPlanFrom)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoPlanFrom])

  const ok = unit && unit.has_graph !== false
  const shown = tab === 'plan' && planUnit ? planUnit : unit
  const score = ok ? shown!.scores[profile] : null
  const color = PROFILES.find((p) => p.id === profile)!.color

  return (
    <div className="unit-overlay" role="dialog" aria-modal="true" aria-label={`Apartment view: ${unit?.caption ?? modelId}`}>
      <div className="unit-card">
        <header className="unit-head">
          <div>
            <h2>{unit?.caption || 'Scanned unit'}</h2>
            <p className="muted">
              Measured from the Matterport 3D scan{unit?.scan_date ? ` of ${unit.scan_date}` : ''}
              {ok ? ` · ${Math.round(unit!.stats.area_m2 * 10.764).toLocaleString()} sq ft · ${unit!.stats.doorways} doorways measured` : ''}
            </p>
          </div>
          <button className="close" onClick={onClose} aria-label="Close apartment view">
            ×
          </button>
        </header>

        <div className="tabs" role="tablist">
          {(
            [
              ['heat', 'Heat map'],
              ['score', 'Score my furniture'],
              ['plan', 'Plan my furniture'],
              ['tour', '3D tour'],
            ] as const
          ).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'tab on' : 'tab'} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>

        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}

        {tab === 'tour' ? (
          <Tour3D modelId={modelId} unit={unit} />
        ) : (
          ok && (
            <div className="unit-body">
              <div className="unit-plan">
                <div className="row wrap">
                  <div className="seg" role="group" aria-label="Need shown on the plan">
                    {PROFILES.map((p) => (
                      <button key={p.id} className={profile === p.id ? 'on' : ''} aria-pressed={profile === p.id} onClick={() => setProfile(p.id)}>
                        <span className="swatch" style={{ background: p.color }} aria-hidden />
                        {p.short} <strong>{shown!.scores[p.id].score}</strong>
                      </button>
                    ))}
                  </div>
                  {unit!.floors.length > 1 && tab === 'heat' && (
                    <label>
                      Floor{' '}
                      <select value={unit!.floor} onChange={(e) => setFloor(Number(e.target.value))}>
                        {unit!.floors.map((f) => (
                          <option key={f.index} value={f.index}>
                            {f.label}
                            {f.index === unit!.entry_floor ? ' (entry)' : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {tab === 'heat' && unit!.has_furniture_scan && (
                    <label className="check">
                      <input type="checkbox" checked={furnished} onChange={(e) => setFurnished(e.target.checked)} /> With the furniture found in the scan
                    </label>
                  )}
                </div>
                <FloorPlan unit={shown!} size={620} focus={focus} ghost={tab === 'score' ? ghost : undefined} />
                <p className="legend">
                  {[4, 3, 2].map((v) => (
                    <span key={v}>
                      <i style={{ background: HEAT[v] }} className={v === 2 ? 'hatch' : ''} /> {shown!.legend[String(v)]}
                    </span>
                  ))}
                  <span>
                    <i style={{ background: '#14532d' }} /> door label: estimated clear width
                  </span>
                </p>
              </div>

              <div className="unit-side">
                <div className="bigscore" style={{ borderColor: color }}>
                  <span className="num">{score!.score}</span>
                  <span>
                    <strong>{scoreWord(score!.score)}</strong>
                    <br />
                    <span className="muted">
                      {PROFILES.find((p) => p.id === profile)!.label} · {score!.confidence} confidence
                    </span>
                  </span>
                </div>

                {tab === 'heat' && (
                  <>
                    <h3>What we measured</h3>
                    <table className="checks">
                      <tbody>
                        {Object.entries(unit!.checks).map(([k, c]) => (
                          <tr key={k}>
                            <th scope="row">{CHECK_LABEL[k] ?? k}</th>
                            <td>
                              <meter min={0} max={1} low={0.5} high={0.8} optimum={1} value={c.value} aria-label={`${Math.round(c.value * 100)} percent`} />
                            </td>
                            <td>{Math.round(c.value * 100)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <h3>
                      {unit!.problems.length} problem{unit!.problems.length === 1 ? '' : 's'} found
                    </h3>
                    <ul className="problems">
                      {unit!.problems.map((p) => (
                        <li key={p.id}>
                          <button
                            className={`problem ${p.severity}`}
                            aria-pressed={focus?.id === p.id}
                            onClick={() => {
                              setFocus(p)
                              if (p.floor !== unit!.floor) setFloor(p.floor)
                            }}
                          >
                            <span className="sev">{p.severity}</span> {p.text}
                          </button>
                        </li>
                      ))}
                      {!unit!.problems.length && <li className="muted">None for this need in what the scan can measure.</li>}
                    </ul>
                    <p className="fine">
                      Door widths are scan widths minus 2 in (scans measure the frame opening). Fixtures such as the toilet, tub and counters are not in the
                      scan geometry. Estimates, not a certification: measure before you sign.
                    </p>
                  </>
                )}

                {tab === 'score' &&
                  (fscore ? (
                    <>
                      <div className="beforeafter">
                        <div>
                          <span className="muted">Empty</span>
                          <strong>{fscore.combined_empty}</strong>
                        </div>
                        <div>
                          <span className="muted">With this furniture</span>
                          <strong>{fscore.combined_furnished}</strong>
                        </div>
                      </div>
                      <p>
                        {fscore.furniture_cost > 0
                          ? `The furniture costs ${fscore.furniture_cost} points.`
                          : 'This layout costs no points: the furniture leaves every checked path clear.'}{' '}
                        {fscore.furniture.length} pieces found by comparing the furnished and furniture-removed scans.
                      </p>
                      <h3>Changes that gain the most</h3>
                      <ul className="problems">
                        {fscore.suggestions.map((s) => (
                          <li key={s.item_id}>
                            <button
                              className="problem medium"
                              onClick={() => setGhost(s.to ? [{ id: 'ghost', label: s.label, rect: s.to as [number, number, number, number] }] : undefined)}
                            >
                              <span className="sev">+{Math.round(s.gain)}</span> {s.text}
                            </button>
                          </li>
                        ))}
                        {!fscore.suggestions.length && <li className="muted">No single move improves the score.</li>}
                      </ul>
                      <p className="fine">{fscore.note}</p>
                    </>
                  ) : (
                    !error && <p className="muted">Finding the furniture…</p>
                  ))}

                {tab === 'plan' && (
                  <>
                    <label htmlFor="pieces">
                      <strong>Furniture to place</strong>
                    </label>
                    <textarea id="pieces" rows={2} value={pieces} onChange={(e) => setPieces(e.target.value)} />
                    <div className="row wrap">
                      <button className="primary" disabled={busy} onClick={() => runPlan()}>
                        {busy ? 'Planning…' : 'Plan this furniture'}
                      </button>
                      {meta?.hero.furnished_unit && meta.hero.furnished_unit !== modelId && (
                        <button disabled={busy} onClick={() => runPlan(meta.hero.furnished_unit)}>
                          Use the furniture from my current home’s scan
                        </button>
                      )}
                    </div>
                    {plan && (
                      <>
                        <div className="beforeafter">
                          <div>
                            <span className="muted">Ordinary layout (middle of 15)</span>
                            <strong>{plan.score_before}</strong>
                          </div>
                          <div>
                            <span className="muted">NavEasy plan</span>
                            <strong>{plan.score_after}</strong>
                          </div>
                          <div>
                            <span className="muted">Empty</span>
                            <strong>{plan.score_empty}</strong>
                          </div>
                        </div>
                        <p>
                          We also tried 15 ordinary layouts of the same pieces (each in a random wall slot that fits). They scored from{' '}
                          <strong>{plan.ordinary_min}</strong> to <strong>{plan.ordinary_max}</strong>; {plan.ordinary_blocked} of 15 lost 10 points or more to
                          blocked paths.
                        </p>
                        <label className="check">
                          <input type="checkbox" checked={showTypical} onChange={(e) => setShowTypical(e.target.checked)} /> Show the middle ordinary layout
                          instead
                        </label>
                        <h3>Why it’s placed this way</h3>
                        <ul className="reasons">
                          {plan.reasons.map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                        {plan.wont_fit.length > 0 && (
                          <>
                            <h3>Won’t fit</h3>
                            <ul className="reasons">
                              {plan.wont_fit.map((w, i) => (
                                <li key={i}>
                                  <strong>{w.label}:</strong> {w.reason}
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        )}
      </div>
    </div>
  )
}
