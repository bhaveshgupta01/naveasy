import { useEffect, useRef, useState } from 'react'
import { PROFILES, type Problem, type Profile, type Unit } from '../types'
import { worse, zoneAt, type Zone } from '../zones'
import { TourOverlay, type Pose } from './tourOverlay'

// Matterport SDK for Embeds. The key only works on localhost (see the Team Access Kit).
const KEY = import.meta.env.VITE_MATTERPORT_SDK_KEY as string | undefined
const BOOTSTRAP = 'https://static.matterport.com/showcase-sdk/bootstrap/3.0.0-0-g0517b8d76c/sdk.es6.js'

interface Sweep {
  sid: string
  id: string
  position: { x: number; y: number; z: number }
}
/* eslint-disable @typescript-eslint/no-explicit-any */

// Model API coordinates are (east, north, up); the viewer uses (east, up, -north).
const toSdk = (x: number, y: number, z: number) => ({ x, y: z, z: -y })
const NONE: Zone = { level: 'none', title: '', text: '', key: 'none' }

function beep() {
  try {
    const ac = new AudioContext()
    const o = ac.createOscillator()
    const g = ac.createGain()
    o.type = 'square'
    o.frequency.setValueAtTime(880, ac.currentTime)
    o.frequency.setValueAtTime(620, ac.currentTime + 0.13)
    g.gain.value = 0.05
    o.connect(g).connect(ac.destination)
    o.start()
    o.stop(ac.currentTime + 0.28)
    o.onended = () => void ac.close()
  } catch {
    /* no audio */
  }
}

interface Props {
  modelId: string
  unit: Unit | null
  profile: Profile
  onProfile: (p: Profile) => void
  onFloor?: (floor: number) => void
  planned?: boolean // the unit carries a furnishing plan, placed in the scan
}

export default function Tour3D({ modelId, unit, profile, onProfile, onFloor, planned }: Props) {
  const frame = useRef<HTMLIFrameElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const overlay = useRef<TourOverlay | null>(null)
  const sdk = useRef<any>(null)
  const sweeps = useRef<Sweep[]>([])
  const tagIds = useRef<string[]>([])
  const [status, setStatus] = useState(KEY ? 'Loading the 3D scan…' : 'Viewing only: set VITE_MATTERPORT_SDK_KEY in frontend/.env.local to see the heat map and zone alerts in 3D.')
  const [connected, setConnected] = useState(false)
  const [active, setActive] = useState<string | null>(null)
  const [mode, setModeName] = useState('')
  const [zone, setZone] = useState<Zone>(NONE)
  const [hoverZone, setHoverZone] = useState<Zone>(NONE)
  const [showFloor, setShowFloor] = useState(true)
  const [alerts, setAlerts] = useState(true)
  const [sound, setSound] = useState(profile === 'blind')
  const problems: Problem[] = (unit?.problems ?? []).filter((p) => p.severity !== 'low')

  // the latest values, for the SDK callbacks
  const live = useRef({ unit, alerts, sound, onFloor, zone })
  live.current = { unit, alerts, sound, onFloor, zone }
  const settle = useRef<number | undefined>(undefined)

  // Move into a worse zone: alert at once. Move into a better one: wait a moment, so the edge of a zone doesn't flicker.
  const applyZone = (z: Zone) => {
    const cur = live.current.zone
    if (z.key === cur.key) return window.clearTimeout(settle.current)
    const go = () => {
      live.current.zone = z
      setZone(z)
      if (!live.current.alerts || (z.level !== 'red' && z.level !== 'amber')) return
      if (live.current.sound) {
        if (z.level === 'red') beep()
        window.speechSynthesis?.cancel()
        window.speechSynthesis?.speak(new SpeechSynthesisUtterance(`${z.title}. ${z.text}`))
      }
    }
    window.clearTimeout(settle.current)
    if (worse(z.level, cur.level) || cur.level === 'none') go()
    else settle.current = window.setTimeout(go, 700)
  }

  const onPose = (pose: Pose) => {
    overlay.current?.setPose(pose)
    setModeName(pose.mode)
    const u = live.current.unit
    if (!u || u.has_graph === false || pose.mode !== 'mode.inside') return applyZone(NONE)
    const h = pose.position.y - (u.floor_z ?? 0) // standing on this floor? (eyes about 1.5 m up)
    if (h < 0.5 || h > 2.6) return applyZone(NONE)
    applyZone(zoneAt(u, pose.position.x, -pose.position.z))
  }

  const lastHover = useRef('')
  const onPointer = (hit: { position?: { x: number; y: number; z: number }; normal?: { x: number; y: number; z: number } }) => {
    const u = live.current.unit
    const pos = hit?.position
    const floorish = pos && hit.normal && hit.normal.y > 0.7 && u && Math.abs(pos.y - (u.floor_z ?? 0)) < 0.35
    const z = floorish && u && u.has_graph !== false ? zoneAt(u, pos!.x, -pos!.z) : NONE
    overlay.current?.setHover(floorish ? pos! : null, z.level)
    if (z.key !== lastHover.current) {
      lastHover.current = z.key
      setHoverZone(z)
    }
  }

  useEffect(() => {
    if (!KEY || !frame.current) return
    let gone = false
    const el = frame.current
    const subs: { cancel: () => void }[] = []
    const onLoad = async () => {
      try {
        const mod: any = await import(/* @vite-ignore */ BOOTSTRAP)
        const mp = await mod.connect(el)
        if (gone) return mp.disconnect?.()
        sdk.current = mp
        ;(window as unknown as { __tour?: unknown }).__tour = { mp, overlay: overlay.current } // for scripted demos and checks
        subs.push(
          mp.Sweep.data.subscribe({
            onAdded: (_i: string, item: Sweep) => sweeps.current.push(item),
            onCollectionUpdated: () => undefined,
          }),
        )
        subs.push(mp.Camera.pose.subscribe(onPose))
        subs.push(mp.Pointer.intersection.subscribe(onPointer))
        // walking up or down the stairs: show that floor's measurements
        let floorCount = 0
        subs.push(mp.Floor.data.subscribe({ onCollectionUpdated: (c: Record<string, unknown>) => (floorCount = Object.keys(c).length) }))
        subs.push(
          mp.Floor.current.subscribe((f: { sequence?: number }) => {
            const u = live.current.unit
            const s = f?.sequence
            if (!u || s === undefined || s < 0 || floorCount !== u.floors.length || s === u.floor) return
            live.current.onFloor?.(s)
          }),
        )
        setConnected(true)
        setStatus('')
      } catch (e) {
        setStatus(`Couldn't connect to the 3D viewer (${String((e as Error).message ?? e)}). You can still look around.`)
      }
    }
    el.addEventListener('load', onLoad)
    return () => {
      gone = true
      el.removeEventListener('load', onLoad)
      subs.forEach((s) => s?.cancel?.())
      sdk.current?.disconnect?.()
      sdk.current = null
      window.clearTimeout(settle.current)
      window.speechSynthesis?.cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId])

  // the three.js layer over the tour, sized to it
  useEffect(() => {
    if (!canvas.current || !stage.current) return
    const o = new TourOverlay(canvas.current)
    overlay.current = o
    const s = stage.current
    const ro = new ResizeObserver(() => o.resize(s.clientWidth, s.clientHeight))
    ro.observe(s)
    o.resize(s.clientWidth, s.clientHeight)
    return () => {
      ro.disconnect()
      o.dispose()
      overlay.current = null
    }
  }, [])

  // rebuild the 3D layers when the need, floor or furniture changes
  useEffect(() => {
    const o = overlay.current
    if (!o || !unit || unit.has_graph === false) return
    o.setUnit(unit, { heat: showFloor, marks: showFloor, planned })
    live.current.zone = NONE
    setZone(NONE)
  }, [unit, showFloor, planned])

  useEffect(() => setSound(profile === 'blind'), [profile])

  // one Matterport tag per problem for this need, at chest height above its spot on the plan
  useEffect(() => {
    const mp = sdk.current
    if (!mp || !connected || !unit) return
    const z = unit.floor_z ?? 0
    const old = tagIds.current
    tagIds.current = []
    ;(old.length ? mp.Tag.remove(...old) : Promise.resolve())
      .catch(() => undefined)
      .then(() =>
        problems.length
          ? mp.Tag.add(
              ...problems.slice(0, 20).map((p) => ({
                label: p.kind.replace(/_/g, ' '),
                description: p.text,
                anchorPosition: toSdk(p.x, p.y, z + 1.0),
                stemVector: { x: 0, y: 0.3, z: 0 },
                color: p.severity === 'high' ? { r: 0.78, g: 0.16, b: 0.16 } : { r: 0.85, g: 0.54, b: 0 },
              })),
            )
          : [],
      )
      .then((ids: string[]) => (tagIds.current = ids ?? []))
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, unit])

  const flyTo = (p: Problem) => {
    setActive(p.id)
    const mp = sdk.current
    if (!mp || !unit) return
    const t = toSdk(p.x, p.y, (unit.floor_z ?? 0) + 1.0)
    // stand at the nearest scan position that is not on top of the spot, and turn to face it
    const near = sweeps.current
      .map((s) => ({ s, d: Math.hypot(s.position.x - t.x, s.position.z - t.z) + Math.abs(s.position.y - t.y - 0.5) * 2 }))
      .filter((v) => v.d > 0.6)
      .sort((a, b) => a.d - b.d)[0]
    if (!near) return
    const yaw = (Math.atan2(-(t.x - near.s.position.x), -(t.z - near.s.position.z)) * 180) / Math.PI
    mp.Sweep.moveTo(near.s.sid || near.s.id, { rotation: { x: -8, y: yaw }, transition: mp.Sweep.Transition?.FLY ?? 'transition.fly', transitionTime: 1800 }).catch(() => undefined)
  }

  const moveMode = (name: 'DOLLHOUSE' | 'FLOORPLAN' | 'INSIDE') => sdk.current?.Mode.moveTo(sdk.current.Mode.Mode[name]).catch(() => undefined)
  const need = PROFILES.find((p) => p.id === profile)!
  const alerting = alerts && (zone.level === 'red' || zone.level === 'amber')
  const inside = mode === 'mode.inside'

  return (
    <div className="tourwrap">
      <div ref={stage} className="tourstage">
        <iframe
          ref={frame}
          className="tour"
          title="Matterport 3D tour"
          src={`https://my.matterport.com/show?m=${modelId}&play=1&qs=1&brand=0${KEY ? `&applicationKey=${KEY}` : ''}`}
          allow="fullscreen; xr-spatial-tracking"
        />
        <canvas ref={canvas} className="tour-overlay" aria-hidden />
        <div className={`zone-vignette ${alerting ? zone.level : ''}`} aria-hidden />
        {alerts && inside && zone.level !== 'none' && (
          <div className={`zone-hud ${zone.level}`}>
            <strong>
              {zone.level === 'red' ? '⚠ ' : zone.level === 'amber' ? '! ' : '✓ '}
              {zone.title}
            </strong>
            {zone.level !== 'green' && <span>{zone.text}</span>}
          </div>
        )}
        {hoverZone.level !== 'none' && (
          <div className={`zone-hover ${hoverZone.level}`} aria-hidden>
            Pointing at: {hoverZone.title}
          </div>
        )}
      </div>
      <div>
        <div className="seg" role="group" aria-label="Need checked in 3D">
          {PROFILES.map((p) => (
            <button key={p.id} className={profile === p.id ? 'on' : ''} aria-pressed={profile === p.id} onClick={() => onProfile(p.id)}>
              <span className="swatch" style={{ background: p.color }} aria-hidden />
              {p.short}
              {unit?.scores && <strong> {unit.scores[p.id].score}</strong>}
            </button>
          ))}
        </div>
        <p className="muted" role="status">
          {status}
        </p>
        {KEY && (
          <>
            <div className="chips">
              <button onClick={() => moveMode('INSIDE')} aria-pressed={inside}>
                Walk inside
              </button>
              <button onClick={() => moveMode('DOLLHOUSE')} aria-pressed={mode === 'mode.dollhouse'}>
                Dollhouse
              </button>
              <button onClick={() => moveMode('FLOORPLAN')} aria-pressed={mode === 'mode.floorplan'}>
                Top view
              </button>
            </div>
            <div className="row wrap">
              <label className="check">
                <input type="checkbox" checked={showFloor} onChange={(e) => setShowFloor(e.target.checked)} /> Heat map in 3D
              </label>
              <label className="check">
                <input type="checkbox" checked={alerts} onChange={(e) => setAlerts(e.target.checked)} /> Zone alerts
              </label>
              <label className="check">
                <input type="checkbox" checked={sound} onChange={(e) => setSound(e.target.checked)} /> Speak warnings
              </label>
            </div>
            <div className={`zone-card ${inside ? zone.level : 'none'}`} aria-live="polite">
              {inside && zone.level !== 'none' ? (
                <>
                  <strong>{zone.title}</strong>
                  <span>{zone.text}</span>
                </>
              ) : (
                <span className="muted">
                  Walk inside and move around: the screen flashes red where a {need.label.toLowerCase()} resident can't go or is at risk, and amber where it's
                  tight. Point at the floor to check a spot before you step there.
                </span>
              )}
            </div>
          </>
        )}
        {planned && <p className="fine">Showing your furniture plan in the scan, with the heat map worked out for it.</p>}
        <h3>Measured problems</h3>
        <ul className="problems">
          {problems.map((p) => (
            <li key={p.id}>
              <button className={`problem ${p.severity}`} aria-pressed={active === p.id} onClick={() => flyTo(p)} disabled={!connected}>
                <span className="sev">{p.severity}</span> {p.text}
              </button>
            </li>
          ))}
          {!problems.length && <li className="muted">No problems for this need in what the scan can measure.</li>}
        </ul>
        <p className="legend">
          <span>
            <i style={{ background: '#ff2d2d' }} /> can't go / at risk
          </span>
          <span>
            <i style={{ background: '#ffb020' }} /> tight / caution
          </span>
          <span>
            <i style={{ background: '#34c759' }} /> good
          </span>
        </p>
        <p className="fine">3D scan © Matterport. The colors, door widths and alerts come from NavEasy's measurements of this scan's walls and doorways.</p>
      </div>
    </div>
  )
}
