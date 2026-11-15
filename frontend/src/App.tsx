import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import MapView, { CITY_VIEW, type MapTarget } from './components/MapView'
import { scoreColor } from './colors'
import BuildingPanel from './components/BuildingPanel'
import UnitView from './components/UnitView'
import RelocatePanel from './components/RelocatePanel'
import ChatPanel, { type Msg } from './components/ChatPanel'
import SpeakDock, { type Reply } from './components/SpeakDock'
import Outside3D from './components/Outside3D'
import { chat, combine, fetchBuildings, fetchHex, fetchHexes, fetchMeta, fetchProgress, relocate, type HexRow, type Meta } from './api'
import { CATEGORIES, PROFILES, scoreWord, type Building, type Category, type City, type FurnishPlan, type Profile, type Relocation, type Route, type UiAction } from './types'
import './App.css'

type Tab = 'homes' | 'move' | 'ask' | 'story'
interface OpenUnit {
  modelId: string
  profile?: Profile
  tab?: 'heat' | 'score' | 'plan' | 'tour'
  plan?: FurnishPlan
  autoPlanFrom?: string
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [city, setCity] = useState<City>('houston')
  const [all, setAll] = useState<Building[]>([])
  const [hexes, setHexes] = useState<HexRow[]>([])
  const [profiles, setProfiles] = useState<Profile[]>(['wheelchair'])
  const [how, setHow] = useState<'min' | 'avg'>('min')
  const [passingOnly, setPassingOnly] = useState(false)
  const [measuredOnly, setMeasuredOnly] = useState(false)
  const [maxRent, setMaxRent] = useState('')
  const [showHexes, setShowHexes] = useState(true)
  const [heatView, setHeatView] = useState<'all' | Profile>('all') // with several needs: all together, or one need's heat map
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [highlighted, setHighlighted] = useState<string[]>([])
  const [openUnit, setOpenUnit] = useState<OpenUnit | null>(null)
  const [route, setRoute] = useState<Route | null>(null)
  const [autoRoute, setAutoRoute] = useState<{ category: Category; profile: Profile } | null>(null)
  const [target, setTarget] = useState<MapTarget | null>(null)
  const [tab, setTab] = useState<Tab>('homes')
  const [relocation, setRelocation] = useState<Relocation | null>(null)
  const [hexInfo, setHexInfo] = useState<{ cell: string; text: string[]; live: boolean; score: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [contrast, setContrast] = useState(false)
  const [step, setStep] = useState(-1)
  const startVoice = useRef<(() => void) | null>(null)
  const [drone, setDrone] = useState<{ b: Building; pitch: number } | null>(null) // the map handed over to a home's drone model
  const allRef = useRef<Building[]>([])
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [liveSteps, setLiveSteps] = useState<NonNullable<Msg['steps']>>([])
  const convo = useRef<{ state: Record<string, unknown>; location?: { lat: number; lon: number } }>({ state: {} })
  const [aerial, setAerial] = useState(true)
  const [threeD, setThreeD] = useState(true)

  // which needs the heat layer shows: all selected needs, or just the one picked in the legend
  const heatProfiles = heatView !== 'all' && profiles.includes(heatView) ? [heatView] : profiles
  const fly = useCallback((center: [number, number], zoom: number) => setTarget({ center, zoom, n: Date.now() }), [])

  // Deep links for demos and sharing: ?city=houston&needs=wheelchair,deaf&home=p123&unit=<model>&tab=plan&route=pharmacy&panel=move
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const needs = (q.get('needs') ?? '').split(',').filter((p): p is Profile => PROFILES.some((x) => x.id === p))
    if (q.get('city') === 'la' || q.get('city') === 'princeton') switchCity(q.get('city') as City)
    if (needs.length) setProfiles(needs)
    if (q.get('home')) setSelectedId(q.get('home'))
    if (q.get('unit')) setOpenUnit({ modelId: q.get('unit')!, tab: (q.get('tab') as OpenUnit['tab']) ?? undefined })
    if (q.get('route') && q.get('home')) setAutoRoute({ category: q.get('route') as Category, profile: needs[0] ?? 'wheelchair' })
    if (q.get('panel')) setTab(q.get('panel') as Tab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetchMeta().then(setMeta).catch(() => setError('The NavEasy API is not running. Start it with: uvicorn backend.app.main:app --port 8000'))
  }, [])
  useEffect(() => {
    let current = true // a slower answer for the city we just left must not overwrite this one
    setAll([])
    fetchBuildings(city)
      .then((b) => current && setAll(b))
      .catch((e) => current && setError(String(e.message ?? e)))
    return () => {
      current = false
    }
  }, [city])
  useEffect(() => {
    if (!profiles.length) return setHexes([])
    fetchHexes(city, heatProfiles, how).then(setHexes).catch(() => setHexes([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city, profiles, how, heatView])

  allRef.current = all
  const buildings = useMemo(() => {
    if (!profiles.length) return []
    const rent = Number(maxRent) || 0
    return all
      .map((b) => ({ ...b, combined: combine(b.scores, profiles, how) }))
      .filter((b) => (!passingOnly || b.combined.score >= 75) && (!measuredOnly || b.measured_inside) && (!rent || (b.rent_min != null && b.rent_min <= rent)))
      .sort((a, b) => b.combined.score - a.combined.score || Number(b.measured_inside) - Number(a.measured_inside))
  }, [all, profiles, how, passingOnly, measuredOnly, maxRent])

  function switchCity(c: City) {
    setCity(c)
    setSelectedId(null)
    setRoute(null)
    setHighlighted([])
    setHexInfo(null)
    fly(CITY_VIEW[c].center, CITY_VIEW[c].zoom)
  }

  const select = useCallback(
    (id: string | null) => {
      setSelectedId(id)
      setRoute(null)
      setAutoRoute(null)
      const b = id && all.find((x) => x.id === id)
      if (b) fly([b.lon, b.lat], 15.5)
    },
    [all, fly],
  )

  const onHex = useCallback(
    (cell: string) => {
      fetchHex(city, cell)
        .then((c) => {
          const p = profiles[0] ?? 'wheelchair'
          const rows = CATEGORIES.filter((k) => c.minutes[p][k.id] != null).map((k) => `${k.label} ${Math.round(c.minutes[p][k.id])} min`)
          setHexInfo({ cell, text: rows, live: c.live, score: Math.min(...profiles.map((q) => c.scores[q].score)) })
        })
        .catch(() => setHexInfo(null))
    },
    [city, profiles],
  )

  const runActions = useCallback(
    (actions: UiAction[]) => {
      for (const a of actions) {
        if (a.type === 'set_map_view') {
          if (a.city && a.city !== city) setCity(a.city)
          if (a.profiles?.length) setProfiles(a.profiles)
          fly(a.center, a.zoom)
        } else if (a.type === 'highlight_buildings') setHighlighted(a.ids)
        else if (a.type === 'open_building') setSelectedId(a.id)
        else if (a.type === 'open_unit') setOpenUnit({ modelId: a.model_id, profile: a.profile, tab: a.tab as OpenUnit['tab'], plan: a.plan })
        else if (a.type === 'show_route') {
          setSelectedId(a.building_id)
          setAutoRoute({ category: a.category, profile: a.profile })
        } else if (a.type === 'draw_route') {
          if (a.building_id) setSelectedId(a.building_id)
          setAutoRoute(null)
          setRoute(a.route)
        } else if (a.type === 'open_drone') {
          const b = allRef.current.find((x) => x.id === a.id)
          if (b) setDrone({ b, pitch: 50 })
        } else if (a.type === 'show_relocation') {
          setTab('move')
          relocate(a.request).then(setRelocation).catch(() => undefined)
        }
      }
    },
    [city, fly],
  )

  const hero = meta?.hero as (Meta['hero'] & { problem_building?: string; splat_home?: string }) | undefined
  const story: { title: string; text: string; run: () => void }[] = [
    {
      title: 'Where should she live?',
      text: 'Her LA address is measured for a wheelchair, then matched to Houston neighborhoods where daily places are as close.',
      run: () => {
        setProfiles(['wheelchair'])
        setOpenUnit(null)
        setSelectedId(null)
        relocate({ origin: { address: hero?.la_origin }, profiles: ['wheelchair'] })
          .then((r) => {
            setRelocation(r)
            if (r.target !== city) switchCity(r.target)
            setHighlighted(r.candidates.flatMap((c) => c.homes.map((h) => h.id)))
            if (r.candidates[1]) fly([r.candidates[1].lon, r.candidates[1].lat], 13.6)
          })
          .catch((e) => setError(String(e.message ?? e)))
      },
    },
    {
      title: 'The neighborhood, in 3D',
      text: 'Midtown from Vexcel aerial photos and measured heights. Each pillar is a home; taller and greener means a better score.',
      run: () => {
        setOpenUnit(null)
        setSelectedId(null)
        setAerial(true)
        setThreeD(true)
        fly([-95.3799, 29.7436], 16.3)
      },
    },
    {
      title: 'A home, and the way to the pharmacy',
      text: 'The Hopkins: building evidence, angled aerial photos, and a wheelchair route with its slope and crossings.',
      run: () => {
        setOpenUnit(null)
        if (hero?.problem_building) {
          setSelectedId(hero.problem_building)
          setAutoRoute({ category: 'pharmacy', profile: 'wheelchair' })
        }
      },
    },
    {
      title: 'Inside: the doorway that fails',
      text: 'Every doorway measured from the Matterport scan. The bathroom door is about 29 in, so a wheelchair can’t get in.',
      run: () => hero?.problem_unit && setOpenUnit({ modelId: hero.problem_unit, profile: 'wheelchair', tab: 'heat' }),
    },
    {
      title: 'See it in the 3D scan',
      text: 'The same problems pinned inside Matterport’s 3D tour.',
      run: () => hero?.problem_unit && setOpenUnit({ modelId: hero.problem_unit, profile: 'wheelchair', tab: 'tour' }),
    },
    {
      title: 'Furniture found in a lived-in home',
      text: 'Pieces recovered by comparing the furnished and furniture-removed scans, with what they cost in points.',
      run: () => hero?.furnished_unit && setOpenUnit({ modelId: hero.furnished_unit, profile: 'wheelchair', tab: 'score' }),
    },
    {
      title: 'Her furniture, planned into a unit that works',
      text: 'The same pieces placed so every room stays reachable, against a typical layout.',
      run: () => hero?.plan_target && setOpenUnit({ modelId: hero.plan_target, profile: 'wheelchair', tab: 'plan', autoPlanFrom: hero.furnished_unit }),
    },
  ]

  // One conversation shared by the chat panel and the speak button. The assistant's state (needs, the homes it listed,
  // the home being discussed) rides along so follow-ups work; its UI actions move the app. Typed answers move the app
  // at once; spoken ones hand their actions to the speak button, which runs each as its sentence is spoken.
  const ask = useCallback(
    async (text: string, channel: 'text' | 'voice' = 'text'): Promise<Reply & { steps?: Msg['steps'] }> => {
      const opening = text === '__start__'
      if (!opening) setMsgs((m) => [...m, { role: 'user', content: text }])
      if (channel === 'voice' && !convo.current.location)
        navigator.geolocation?.getCurrentPosition(
          (p) => (convo.current.location = { lat: p.coords.latitude, lon: p.coords.longitude }),
          () => undefined,
          { maximumAge: 60000, timeout: 8000 },
        )
      // the work in progress (plan, subagents, tool calls) is shown while we wait
      const rid = `r${Date.now()}${Math.random().toString(36).slice(2, 7)}`
      setLiveSteps([])
      const poll = window.setInterval(() => void fetchProgress(rid).then(setLiveSteps).catch(() => undefined), 800)
      try {
        const r = await chat([{ role: 'user', content: text }], { city, profiles, channel, request_id: rid, state: convo.current.state, location: convo.current.location, screen: screenRef.current }).finally(() => {
          window.clearInterval(poll)
          setLiveSteps([])
        })
        convo.current.state = r.state ?? convo.current.state
        setMsgs((m) => [...m, { role: 'assistant', content: r.text, steps: r.steps, mode: r.mode }])
        if (channel === 'text') runActions(r.ui_actions ?? [])
        return { text: r.text, ui_actions: r.ui_actions ?? [], steps: r.steps }
      } catch (e) {
        const msg = `Sorry, that failed: ${String((e as Error).message ?? e)}`
        setMsgs((m) => [...m, { role: 'assistant', content: msg }])
        return { text: msg, ui_actions: [] }
      }
    },
    [city, profiles, runActions],
  )
  const askVoice = useCallback((text: string) => ask(text, 'voice'), [ask])
  const askText = useCallback((text: string) => ask(text, 'text').then((r) => r.text), [ask])

  // What is on screen right now, so the assistant can answer questions about it ("how wide is that door?")
  const screenRef = useRef<Record<string, unknown>>({})
  screenRef.current = {
    city,
    selected_building: selectedId,
    unit: openUnit ? { model_id: openUnit.modelId, profile: openUnit.profile ?? profiles[0] } : null,
    route: route && selectedId ? { building_id: selectedId, category: route.category, profile: route.profile } : null,
    relocation: relocation ? { target: relocation.target_name, candidates: relocation.candidates.map((c) => ({ name: c.name, match: c.match, reason: c.reason, comparison: c.comparison })) } : null,
    hex: hexInfo,
    list: { count: buildings.length, top: buildings.slice(0, 5).map((b) => ({ name: b.name, score: b.combined!.score })) },
  }

  const toggle = (p: Profile) => setProfiles((ps) => (ps.includes(p) ? ps.filter((x) => x !== p) : [...ps, p]))
  const hexColors = heatProfiles.map((p) => PROFILES.find((x) => x.id === p)!.color)
  const hexColor = hexColors[0] ?? '#3f4a5a'
  const stats = meta?.cities?.[city]
  const pins = relocation && relocation.target === city ? relocation.candidates.map((c, i) => ({ lon: c.lon, lat: c.lat, label: String(i + 1) })) : []

  return (
    <div className={contrast ? 'app contrast' : 'app'}>
      <a className="skip" href="#homes-list">
        Skip to the list of homes
      </a>
      <aside className="sidebar">
        <header>
          <h1>
            Nav<span>Easy</span>
          </h1>
          <p className="tagline">Can I live here independently?</p>
          <button className="voice-open" onClick={() => startVoice.current?.()}>
            🎙 Talk to NavEasy <span>it asks what you need and walks you through homes, no screen needed (press V)</span>
          </button>
        </header>

        <div className="seg" role="group" aria-label="City">
          <button className={city === 'houston' ? 'on' : ''} aria-pressed={city === 'houston'} onClick={() => switchCity('houston')}>
            Houston
          </button>
          <button className={city === 'la' ? 'on' : ''} aria-pressed={city === 'la'} onClick={() => switchCity('la')}>
            Los Angeles
          </button>
          <button className={city === 'princeton' ? 'on' : ''} aria-pressed={city === 'princeton'} onClick={() => switchCity('princeton')} title="The HackPrinceton venue">
            Princeton
          </button>
        </div>

        <fieldset>
          <legend>My needs</legend>
          {PROFILES.map((p) => (
            <label key={p.id} className="check">
              <input type="checkbox" checked={profiles.includes(p.id)} onChange={() => toggle(p.id)} />
              <span className="swatch" style={{ background: p.color }} aria-hidden />
              {p.label}
            </label>
          ))}
          {profiles.length > 1 && (
            <div className="row" role="radiogroup" aria-label="Combine several needs">
              <label className="check">
                <input type="radio" name="how" checked={how === 'min'} onChange={() => setHow('min')} /> Weakest need decides
              </label>
              <label className="check">
                <input type="radio" name="how" checked={how === 'avg'} onChange={() => setHow('avg')} /> Average
              </label>
            </div>
          )}
        </fieldset>

        <div className="tabs" role="tablist">
          {(
            [
              ['homes', 'Homes'],
              ['move', 'Moving'],
              ['ask', 'Ask'],
              ['story', 'Guided demo'],
            ] as const
          ).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'tab on' : 'tab'} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>

        {tab === 'homes' && (
          <>
            <div className="filters">
              <label className="check">
                <input type="checkbox" checked={passingOnly} onChange={(e) => setPassingOnly(e.target.checked)} /> Only homes scoring 75 or more
              </label>
              <label className="check">
                <input type="checkbox" checked={measuredOnly} onChange={(e) => setMeasuredOnly(e.target.checked)} /> Only homes measured inside
              </label>
              <label className="check">
                <input type="checkbox" checked={showHexes} onChange={(e) => setShowHexes(e.target.checked)} /> Neighborhood heat map
              </label>
              <label className="check">
                Max rent $
                <input className="small" inputMode="numeric" value={maxRent} onChange={(e) => setMaxRent(e.target.value.replace(/\D/g, ''))} aria-label="Maximum monthly rent" />
              </label>
            </div>
            <h2 id="homes-list" tabIndex={-1}>
              {buildings.length.toLocaleString()} homes
              {stats && <span className="muted"> · {stats.measured_inside.toLocaleString()} measured inside</span>}
            </h2>
            <ol className="list">
              {buildings.slice(0, 60).map((b) => (
                <li key={b.id}>
                  <button onClick={() => select(b.id)} aria-pressed={b.id === selectedId}>
                    <span className="dot" style={{ background: scoreColor(b.combined!.score), opacity: b.measured_inside ? 1 : 0.35 }} aria-hidden />
                    <span className="grow">
                      {b.name}
                      <br />
                      <span className="muted">
                        {scoreWord(b.combined!.score)}
                        {b.measured_inside ? '' : ' · building only'}
                        {b.locality_scores ? ` · neighborhood ${combine(b.locality_scores, profiles, how).score}` : ''}
                        {b.rent_min ? ` · from $${b.rent_min.toLocaleString()}` : ''}
                      </span>
                    </span>
                    <strong>{b.combined!.score}</strong>
                  </button>
                </li>
              ))}
            </ol>
            {buildings.length > 60 && <p className="muted">Showing the top 60. Zoom the map or add filters to see others.</p>}
          </>
        )}
        {tab === 'move' && (
          <RelocatePanel
            profiles={profiles}
            defaultAddress={meta?.hero.la_origin}
            result={relocation}
            onResult={(r) => {
              setRelocation(r)
              if (r && r.target !== city) switchCity(r.target)
              if (r?.candidates.length) setHighlighted(r.candidates.flatMap((c) => c.homes.map((h) => h.id)))
            }}
            onGo={(c, t) => {
              if (t !== city) setCity(t)
              fly([c.lon, c.lat], 14.2)
            }}
            onOpenHome={(id, t) => {
              if (t !== city) setCity(t)
              setSelectedId(id)
            }}
          />
        )}
        {tab === 'story' && (
          <div className="story">
            <p className="muted">
              Maria uses a power wheelchair and is moving from Los Angeles to Houston. Each step runs live on the real data; nothing is pre-recorded.
            </p>
            <ol>
              {story.map((s, i) => (
                <li key={s.title}>
                  <button
                    aria-current={step === i ? 'step' : undefined}
                    onClick={() => {
                      setStep(i)
                      s.run()
                    }}
                  >
                    <span>
                      <span className="n">{i + 1}.</span> <strong>{s.title}</strong>
                    </span>
                    <span className="muted">{s.text}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        )}
        {tab === 'ask' && <ChatPanel msgs={msgs} ask={askText} live={liveSteps} onVoice={() => startVoice.current?.()} />}

        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <footer>
          <label className="check">
            <input type="checkbox" checked={contrast} onChange={(e) => setContrast(e.target.checked)} /> High contrast
          </label>
          <p className="fine">
            Estimates from 3D scans and public maps, never a certification. Grey or faint means no data, not “inaccessible”.
            {meta?.doorways_measured ? ` ${meta.doorways_measured.toLocaleString()} doorways measured in ${meta.units_measured.toLocaleString()} scans.` : ''}
          </p>
        </footer>
      </aside>

      <main className="main">
        <MapView
          buildings={buildings}
          hexes={hexes}
          hexColor={hexColor}
          hexColors={hexColors}
          showHexes={showHexes}
          selectedId={selectedId}
          highlighted={highlighted}
          route={route}
          target={target}
          aerial={aerial}
          threeD={threeD}
          pins={pins}
          onSelect={select}
          onHex={onHex}
          droneOpen={!!drone}
          onDrone={(b, pitch) => {
            setDrone({ b, pitch })
            setSelectedId(b.id)
          }}
        />
        {drone && (
          <Outside3D
            key={drone.b.id}
            inline
            lat={drone.b.lat}
            lon={drone.b.lon}
            name={drone.b.name}
            splatSid={drone.b.splat_sid}
            pitch={drone.pitch}
            onClose={() => setDrone(null)}
          />
        )}
        <div className="map-modes">
          <div className="seg" role="group" aria-label="Base map">
            <button className={aerial ? 'on' : ''} aria-pressed={aerial} onClick={() => setAerial(true)}>
              Aerial
            </button>
            <button className={!aerial ? 'on' : ''} aria-pressed={!aerial} onClick={() => setAerial(false)}>
              Streets
            </button>
          </div>
          <div className="seg" role="group" aria-label="Map view">
            <button className={threeD ? 'on' : ''} aria-pressed={threeD} onClick={() => setThreeD(true)}>
              3D
            </button>
            <button className={!threeD ? 'on' : ''} aria-pressed={!threeD} onClick={() => setThreeD(false)}>
              2D
            </button>
          </div>
        </div>
        <div className="map-legend" aria-label="Map legend">
          <strong>{profiles.length === 1 ? PROFILES.find((p) => p.id === profiles[0])!.label : profiles.length ? `${profiles.length} needs, ${how === 'min' ? 'weakest decides' : 'average'}` : 'Pick a need'}</strong>
          {profiles.length > 1 && (
            <div className="heat-pick" role="group" aria-label="Which heat map to show">
              <button className={heatProfiles.length > 1 ? 'on' : ''} aria-pressed={heatProfiles.length > 1} onClick={() => setHeatView('all')}>
                All {profiles.length}
              </button>
              {profiles.map((p) => {
                const info = PROFILES.find((x) => x.id === p)!
                return (
                  <button key={p} className={heatProfiles.length === 1 && heatProfiles[0] === p ? 'on' : ''} aria-pressed={heatProfiles.length === 1 && heatProfiles[0] === p} onClick={() => setHeatView(p)}>
                    <i className="dot" style={{ background: info.color }} /> {info.short}
                  </button>
                )
              })}
            </div>
          )}
          {heatProfiles.length > 1 ? (
            <>
              <span>
                Neighboring cells take turns between your needs. Each cell is stronger in its need’s color where that need scores higher there. Pick one need above for
                its full map.
              </span>
              <span className="heat-keys">
                {heatProfiles.map((p) => {
                  const info = PROFILES.find((x) => x.id === p)!
                  return (
                    <span key={p}>
                      <i className="dot" style={{ background: info.color }} /> {info.short}
                    </span>
                  )
                })}
              </span>
            </>
          ) : (
            <>
              <span className="ramp" style={{ background: `linear-gradient(90deg, ${hexColor}10, ${hexColor}cc)` }} />
              <span className="ramp-labels">
                <span>neighborhood: low</span>
                <span>high</span>
              </span>
            </>
          )}
          <span>
            <i className="dot" style={{ background: scoreColor(80) }} /> 75+ <i className="dot" style={{ background: scoreColor(60) }} /> 50–74{' '}
            <i className="dot" style={{ background: scoreColor(30) }} /> under 50
          </span>
          <span>
            <i className="dot" style={{ background: '#555' }} /> measured inside <i className="dot hollow" /> building only
          </span>
          <span>
            <i className="dot ring-drone" /> drone 3D model: zoom in on it to fly around the home
          </span>
          {threeD && <span className="muted">3D: zoom in and the city rises from Vexcel’s measured heights; each home is a pillar, taller = higher score</span>}
        </div>
        {hexInfo && (
          <div className="hex-info" role="status">
            <button className="close" onClick={() => setHexInfo(null)} aria-label="Close">
              ×
            </button>
            <strong>Neighborhood score {hexInfo.score}</strong>
            <span className="muted"> · {hexInfo.live ? 'routed walking times' : 'estimated from distance'}</span>
            <br />
            {hexInfo.text.join(' · ')}
          </div>
        )}
      </main>

      {selectedId && (
        <BuildingPanel
          id={selectedId}
          profiles={profiles.length ? profiles : ['wheelchair']}
          route={route}
          autoRoute={autoRoute}
          onRoute={setRoute}
          onOpenUnit={(modelId) => setOpenUnit({ modelId })}
          onClose={() => select(null)}
        />
      )}
      {openUnit && (
        <UnitView
          key={openUnit.modelId + (openUnit.tab ?? '')}
          modelId={openUnit.modelId}
          profiles={profiles}
          initialProfile={openUnit.profile}
          initialTab={openUnit.tab}
          initialPlan={openUnit.plan}
          autoPlanFrom={openUnit.autoPlanFrom}
          meta={meta}
          onClose={() => setOpenUnit(null)}
        />
      )}
      <SpeakDock ask={askVoice} runActions={runActions} onChat={() => setTab('ask')} chatOpen={tab === 'ask'} startRef={startVoice} />
    </div>
  )
}
