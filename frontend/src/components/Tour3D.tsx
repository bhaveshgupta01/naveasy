import { useEffect, useRef, useState } from 'react'
import type { Problem, Unit } from '../types'

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

export default function Tour3D({ modelId, unit }: { modelId: string; unit: Unit | null }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const sdk = useRef<any>(null)
  const sweeps = useRef<Sweep[]>([])
  const [status, setStatus] = useState(KEY ? 'Loading the 3D scan…' : 'Viewing only: set VITE_MATTERPORT_SDK_KEY in frontend/.env.local to jump to problems.')
  const [active, setActive] = useState<string | null>(null)
  const problems: Problem[] = (unit as (Unit & { all_problems?: Problem[] }) | null)?.all_problems?.filter((p) => p.severity !== 'low') ?? unit?.problems ?? []

  useEffect(() => {
    if (!KEY || !frame.current) return
    let gone = false
    const el = frame.current
    const onLoad = async () => {
      try {
        const mod: any = await import(/* @vite-ignore */ BOOTSTRAP)
        const mp = await mod.connect(el)
        if (gone) return mp.disconnect?.()
        sdk.current = mp
        mp.Sweep.data.subscribe({
          onAdded: (_i: string, item: Sweep) => sweeps.current.push(item),
          onCollectionUpdated: () => undefined,
        })
        setStatus('Connected. Pick a problem to fly to it, or look around.')
      } catch (e) {
        setStatus(`Couldn't connect to the 3D viewer (${String((e as Error).message ?? e)}). You can still look around.`)
      }
    }
    el.addEventListener('load', onLoad)
    return () => {
      gone = true
      el.removeEventListener('load', onLoad)
      sdk.current?.disconnect?.()
      sdk.current = null
    }
  }, [modelId])

  // one tag per problem, at chest height above its spot on the plan
  useEffect(() => {
    const mp = sdk.current
    if (!mp || !unit || status.startsWith('Loading')) return
    const z = unit.floor_z ?? 0
    mp.Tag.add(
      ...problems.slice(0, 20).map((p) => ({
        label: p.kind.replace(/_/g, ' '),
        description: p.text,
        anchorPosition: toSdk(p.x, p.y, z + 1.0),
        stemVector: { x: 0, y: 0.3, z: 0 },
        color: p.severity === 'high' ? { r: 0.78, g: 0.16, b: 0.16 } : { r: 0.85, g: 0.54, b: 0 },
      })),
    ).catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, unit?.model_id])

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

  const mode = (name: 'DOLLHOUSE' | 'FLOORPLAN' | 'INSIDE') => sdk.current?.Mode.moveTo(sdk.current.Mode.Mode[name]).catch(() => undefined)

  return (
    <div className="tourwrap">
      <iframe
        ref={frame}
        className="tour"
        title="Matterport 3D tour"
        src={`https://my.matterport.com/show?m=${modelId}&play=1&qs=1&brand=0${KEY ? `&applicationKey=${KEY}` : ''}`}
        allow="fullscreen; xr-spatial-tracking"
      />
      <div>
        <p className="muted" role="status">
          {status}
        </p>
        {KEY && (
          <div className="chips">
            <button onClick={() => mode('DOLLHOUSE')}>Dollhouse</button>
            <button onClick={() => mode('FLOORPLAN')}>Top view</button>
            <button onClick={() => mode('INSIDE')}>Walk inside</button>
          </div>
        )}
        <h3>Measured problems</h3>
        <ul className="problems">
          {problems.map((p) => (
            <li key={p.id}>
              <button className={`problem ${p.severity}`} aria-pressed={active === p.id} onClick={() => flyTo(p)} disabled={!KEY}>
                <span className="sev">{p.profile}</span> {p.text}
              </button>
            </li>
          ))}
          {!problems.length && <li className="muted">No problems measured in this scan.</li>}
        </ul>
        <p className="fine">3D scan © Matterport. Each problem is pinned in the scan where it was measured.</p>
      </div>
    </div>
  )
}
