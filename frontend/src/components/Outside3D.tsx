import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { API } from '../api'

interface Props {
  lat: number
  lon: number
  name: string
  splatSid?: string | null
  onClose: () => void
}

interface Surface {
  width_m: number
  depth_m: number
  cols: number
  rows: number
  max_m: number
  heights: string
  texture: string
}

// The outside of a home as a 3D model you can orbit.
//  - "Block": Vexcel surface heights draped with the Vexcel aerial photo. Works for every building.
//  - "Drone model": Matterport's aerial Gaussian splat, where one exists (230 homes). Much finer detail.
export default function Outside3D({ lat, lon, name, splatSid, onClose }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const [mode, setMode] = useState<'block' | 'splat'>(splatSid ? 'splat' : 'block')
  const [status, setStatus] = useState('Loading…')
  const [spin, setSpin] = useState(true)
  const spinRef = useRef(spin)
  spinRef.current = spin

  useEffect(() => {
    const el = host.current
    if (!el) return
    let dispose = () => {}
    let dead = false
    setStatus('Loading…')

    if (mode === 'block') {
      const renderer = new THREE.WebGLRenderer({ antialias: true })
      renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
      renderer.setSize(el.clientWidth, el.clientHeight)
      el.appendChild(renderer.domElement)
      const scene = new THREE.Scene()
      scene.background = new THREE.Color('#dfe8f2')
      const camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 1, 2000)
      camera.position.set(-95, 85, 110)
      const controls = new OrbitControls(camera, renderer.domElement)
      controls.target.set(0, 8, 0)
      controls.maxPolarAngle = Math.PI / 2.05
      controls.minDistance = 25
      controls.maxDistance = 400
      controls.enableDamping = true
      let raf = 0
      const tick = () => {
        controls.autoRotate = spinRef.current && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
        controls.autoRotateSpeed = 0.9
        controls.update()
        renderer.render(scene, camera)
        raf = requestAnimationFrame(tick)
      }
      fetch(`${API}/surface?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('No aerial height data here'))))
        .then((s: Surface) => {
          if (dead) return
          const bin = atob(s.heights)
          const h = new Uint16Array(bin.length / 2)
          for (let i = 0; i < h.length; i++) h[i] = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)
          const geo = new THREE.PlaneGeometry(s.width_m, s.depth_m, s.cols - 1, s.rows - 1)
          geo.rotateX(-Math.PI / 2) // lay it flat: x east, z south, y up; row 0 (north) is at -z
          const pos = geo.attributes.position
          for (let i = 0; i < pos.count; i++) pos.setY(i, h[i] / 100)
          geo.computeVertexNormals()
          const tex = new THREE.TextureLoader().load(`${API}${s.texture}`, () => setStatus(''))
          tex.colorSpace = THREE.SRGBColorSpace
          tex.anisotropy = 8
          scene.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex })))
          // a pin over the home itself
          const pin = new THREE.Mesh(new THREE.ConeGeometry(2.2, 7, 16), new THREE.MeshBasicMaterial({ color: '#ffd60a' }))
          pin.rotation.x = Math.PI
          pin.position.set(0, h[Math.floor(s.rows / 2) * s.cols + Math.floor(s.cols / 2)] / 100 + 9, 0)
          scene.add(pin)
          tick()
        })
        .catch((e) => setStatus(String(e.message ?? e)))
      const onResize = () => {
        camera.aspect = el.clientWidth / el.clientHeight
        camera.updateProjectionMatrix()
        renderer.setSize(el.clientWidth, el.clientHeight)
      }
      window.addEventListener('resize', onResize)
      dispose = () => {
        cancelAnimationFrame(raf)
        window.removeEventListener('resize', onResize)
        controls.dispose()
        renderer.dispose()
        renderer.domElement.remove()
      }
    } else if (splatSid) {
      let viewer: { dispose?: () => Promise<void> | void; start: () => void } | null = null
      import('@mkkellogg/gaussian-splats-3d')
        .then((GS) => {
          if (dead) return
          const v = new GS.Viewer({
            rootElement: el,
            cameraUp: [0, 0, 1],
            initialCameraPosition: [-32, -32, 22],
            initialCameraLookAt: [0, 0, 2],
            sharedMemoryForWorkers: false,
            dynamicScene: false,
          })
          viewer = v
          return v
            .addSplatScene(`${API}/splats/${splatSid}.ply`, { format: GS.SceneFormat.Ply, showLoadingUI: true, splatAlphaRemovalThreshold: 8 })
            .then(() => {
              if (dead) return
              v.start()
              setStatus('')
            })
        })
        .catch((e) => setStatus(`Couldn't load the drone model (${String(e.message ?? e)}). Try the Block view.`))
      dispose = () => {
        try {
          void viewer?.dispose?.()
        } catch {
          /* viewer already gone */
        }
        el.replaceChildren()
      }
    }
    return () => {
      dead = true
      dispose()
    }
  }, [mode, lat, lon, splatSid])

  return (
    <div className="unit-overlay" role="dialog" aria-modal="true" aria-label={`3D view of the outside of ${name}`}>
      <div className="unit-card">
        <header className="unit-head">
          <div>
            <h2>{name}: outside in 3D</h2>
            <p className="muted">Drag to orbit, scroll to zoom. Use it to check the entrance, steps, driveway and the way from the street.</p>
          </div>
          <button className="close" onClick={onClose} aria-label="Close 3D view">
            ×
          </button>
        </header>
        <div className="row wrap" style={{ margin: '10px 0' }}>
          <div className="seg" role="group" aria-label="3D model">
            {splatSid && (
              <button className={mode === 'splat' ? 'on' : ''} aria-pressed={mode === 'splat'} onClick={() => setMode('splat')}>
                Drone model (Matterport splat)
              </button>
            )}
            <button className={mode === 'block' ? 'on' : ''} aria-pressed={mode === 'block'} onClick={() => setMode('block')}>
              Block (Vexcel heights + aerial photo)
            </button>
          </div>
          {mode === 'block' && (
            <label className="check">
              <input type="checkbox" checked={spin} onChange={(e) => setSpin(e.target.checked)} /> Turn slowly
            </label>
          )}
          <span className="muted" role="status">
            {status}
          </span>
        </div>
        <div ref={host} className="outside3d" />
        <p className="fine">
          {mode === 'block'
            ? 'Roof, tree and ground heights are measured from the air by Vexcel (about 7.5 cm per pixel); walls are stretched from the top-down photo, so read them for shape, not detail. © Vexcel Imaging US, Inc.'
            : 'Built by Matterport from drone photos of this home; cropped to the home and its lot. © Matterport.'}
        </p>
      </div>
    </div>
  )
}
