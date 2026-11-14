// Draws NavEasy's measurements inside the Matterport 3D tour: the heat map on the floor, door gates with their widths,
// turning circles, the main paths, furniture and pulsing problem rings.
//
// The SDK for Embeds can't add objects to Matterport's own scene, so this is a transparent three.js canvas over the
// tour that copies Matterport's camera on every pose update (about 22 a second). The walls from the scan's wall graph
// are drawn invisibly into the depth buffer first, so anything behind a wall stays hidden, while doorways
// (cut out of the walls) stay open to look through. Outside walls only hide things from the inside, like the scan
// itself: they were scanned from inside only, so the dollhouse view looks straight through them.
import * as THREE from 'three'
import type { Profile, Unit } from '../types'
import { roomAt, type Level } from '../zones'
import { DOOR, FIT, HATCH, INK, NEED_COLOR } from '../colors'

export interface Pose {
  position: { x: number; y: number; z: number }
  rotation: { x: number; y: number } // degrees: pitch, yaw
  projection: ArrayLike<number> // 4 x 4, row-major
  mode: string
}

// Model API coordinates are (east, north, up); the viewer uses (east, up, -north).
const V = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y)

// heat values 2 doesn't work, 3 tight, 4 works well, on the violet fit scale, as [r, g, b, alpha]
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number]
const CELL: Record<number, [number, number, number, number]> = {
  2: [...rgb(FIT.pale), 120],
  3: [...rgb(FIT.mid), 115],
  4: [...rgb(FIT.deep), 95],
}
const STRIPE = rgb(HATCH)
// the hover ring: the zone alerts keep red and amber (they flash on screen); a spot that works shows light violet
export const LEVEL_COLOR: Record<Level, string> = { red: '#ff2d2d', amber: '#ffb020', green: '#d9c8ff', none: '#ffffff' }
const PATH_COLOR: Partial<Record<Profile, string>> = { blind: NEED_COLOR.blind, older: NEED_COLOR.older }

// pale with ink stripes: "doesn't work", the same look as those cells on the floor
function stripes(): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const ctx = c.getContext('2d')!
  ctx.fillStyle = FIT.pale
  ctx.fillRect(0, 0, 64, 64)
  ctx.strokeStyle = HATCH
  ctx.lineWidth = 9
  for (let k = -64; k < 128; k += 24) {
    ctx.beginPath()
    ctx.moveTo(k, 64)
    ctx.lineTo(k + 64, 0)
    ctx.stroke()
  }
  const t = new THREE.CanvasTexture(c)
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  t.colorSpace = THREE.SRGBColorSpace
  return t
}
const WALL_H = 2.5

// Labels keep the same size on screen at any distance, like Matterport's own tags.
function label(text: string, color: string, scale = 1, ink = '#fff'): THREE.Sprite {
  const c = document.createElement('canvas')
  const ctx = c.getContext('2d')!
  ctx.font = '700 44px system-ui, sans-serif'
  const w = Math.ceil(ctx.measureText(text).width) + 36
  c.width = w
  c.height = 68
  ctx.font = '700 44px system-ui, sans-serif'
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.roundRect(0, 0, w, 68, 16)
  ctx.fill()
  ctx.fillStyle = ink
  ctx.textBaseline = 'middle'
  ctx.fillText(text, 18, 36)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthWrite: false, sizeAttenuation: false }))
  s.scale.set((w / 68) * 0.06 * scale, 0.06 * scale, 1)
  s.renderOrder = 5
  return s
}

function disposeTree(o: THREE.Object3D) {
  o.traverse((n) => {
    const m = n as THREE.Mesh
    m.geometry?.dispose()
    const mats = (Array.isArray(m.material) ? m.material : [m.material]).filter(Boolean) as THREE.Material[]
    mats.forEach((mat) => {
      ;(mat as THREE.MeshBasicMaterial).map?.dispose()
      mat.dispose()
    })
  })
}

export class TourOverlay {
  private renderer: THREE.WebGLRenderer
  private scene = new THREE.Scene()
  private camera = new THREE.PerspectiveCamera()
  private content = new THREE.Group()
  private pulses: { mesh: THREE.Mesh; phase: number }[] = []
  private hover: THREE.Mesh
  private havePose = false
  private raf = 0
  private t0 = performance.now()
  visible = true

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true })
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
    this.renderer.setClearColor(0x000000, 0)
    this.camera.matrixAutoUpdate = true
    this.scene.add(this.content)
    this.hover = new THREE.Mesh(
      new THREE.RingGeometry(0.22, 0.34, 48).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: '#fff', transparent: true, opacity: 0.95, depthWrite: false, side: THREE.DoubleSide }),
    )
    this.hover.visible = false
    this.hover.renderOrder = 6
    this.scene.add(this.hover)
    const loop = () => {
      this.render()
      this.raf = requestAnimationFrame(loop)
    }
    loop()
  }

  resize(w: number, h: number) {
    this.renderer.setSize(w, h, false)
  }

  setPose(p: Pose) {
    this.camera.position.set(p.position.x, p.position.y, p.position.z)
    this.camera.rotation.set((p.rotation.x * Math.PI) / 180, (p.rotation.y * Math.PI) / 180, 0, 'YXZ')
    const m = p.projection
    this.camera.projectionMatrix.set(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11], m[12], m[13], m[14], m[15])
    this.camera.projectionMatrixInverse.copy(this.camera.projectionMatrix).invert()
    this.havePose = true
  }

  // where the pointer is on the floor (viewer coordinates), colored by the zone it is over
  setHover(at: { x: number; y: number; z: number } | null, level: Level) {
    this.hover.visible = !!at && level !== 'none'
    if (!at) return
    this.hover.position.set(at.x, at.y + 0.03, at.z)
    ;(this.hover.material as THREE.MeshBasicMaterial).color.set(LEVEL_COLOR[level])
  }

  setUnit(unit: Unit, opts: { heat: boolean; marks: boolean; planned?: boolean }) {
    disposeTree(this.content)
    this.content.clear()
    this.pulses = []
    const z0 = unit.floor_z ?? 0
    const p = unit.profile

    // walls: depth only, so what is behind them stays hidden (doorways are already cut out). A wall between two rooms
    // hides from both sides; an outside wall is a one-sided plane facing in, so from outside (the dollhouse) it hides nothing.
    const occluder = new THREE.MeshBasicMaterial({ colorWrite: false })
    for (const [ax, ay, bx, by] of unit.walls) {
      const len = Math.hypot(bx - ax, by - ay)
      if (len < 0.05) continue
      const ang = Math.atan2(by - ay, bx - ax)
      // which sides of the wall are inside the home: probe 30 cm out on each side, at a few points along it
      const nx = Math.sin(ang)
      const ny = -Math.cos(ang) // right of a -> b
      let right = 0
      let left = 0
      for (const u of [0.25, 0.5, 0.75]) {
        const px = ax + (bx - ax) * u
        const py = ay + (by - ay) * u
        if (roomAt(unit, px + nx * 0.3, py + ny * 0.3)) right++
        if (roomAt(unit, px - nx * 0.3, py - ny * 0.3)) left++
      }
      const both = right > 0 && left > 0
      const wall = new THREE.Mesh(both ? new THREE.BoxGeometry(len + 0.04, WALL_H, 0.14) : new THREE.PlaneGeometry(len + 0.04, WALL_H), occluder)
      wall.position.copy(V((ax + bx) / 2, (ay + by) / 2, z0 + WALL_H / 2))
      // a plane faces local +z, which this rotation turns to the right of a -> b; turn it around if the home is on the left
      wall.rotation.y = both || right >= left ? ang : ang + Math.PI
      wall.renderOrder = -1
      this.content.add(wall)
    }

    if (opts.heat) {
      // the heat map as a texture on the floor; "doesn't work" cells get ink stripes
      const g = unit.heat
      const c = document.createElement('canvas')
      c.width = g.width
      c.height = g.height
      const ctx = c.getContext('2d')!
      const img = ctx.createImageData(g.width, g.height)
      const stripe = Math.max(2, Math.round(0.12 / g.cell_m))
      for (let j = 0; j < g.height; j++)
        for (let i = 0; i < g.width; i++) {
          const v = g.values[j][i]
          const col = CELL[v]
          if (!col) continue
          const k = ((g.height - 1 - j) * g.width + i) * 4 // canvas row 0 is the north edge
          const band = v === 2 && Math.floor((i + j) / stripe) % 2 === 0
          img.data[k] = band ? STRIPE[0] : col[0]
          img.data[k + 1] = band ? STRIPE[1] : col[1]
          img.data[k + 2] = band ? STRIPE[2] : col[2]
          img.data[k + 3] = band ? 150 : col[3]
        }
      ctx.putImageData(img, 0, 0)
      const tex = new THREE.CanvasTexture(c)
      tex.magFilter = THREE.NearestFilter
      tex.minFilter = THREE.LinearFilter
      tex.colorSpace = THREE.SRGBColorSpace
      const W = g.width * g.cell_m
      const H = g.height * g.cell_m
      const floor = new THREE.Mesh(
        new THREE.PlaneGeometry(W, H).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }),
      )
      floor.position.copy(V(g.origin[0] + W / 2, g.origin[1] + H / 2, z0 + 0.02))
      this.content.add(floor)
    }

    if (opts.marks) {
      // door gates: a colored curtain in each doorway and its clear width (wheelchair), or a slim frame (others)
      for (const d of unit.doors) {
        if (!d.is_door || (d.exterior && !d.entry)) continue
        const chip = DOOR[d.status]
        if (p === 'wheelchair' && d.status !== 'pass') {
          // a curtain in the doorway: striped when the chair can't pass, mid violet when it's tight
          const fail = d.status === 'fail'
          const tex = fail ? stripes() : null
          tex?.repeat.set(d.width_m * 3, 6)
          const gate = new THREE.Mesh(
            new THREE.PlaneGeometry(d.width_m, 2.0),
            new THREE.MeshBasicMaterial({ color: fail ? '#ffffff' : FIT.mid, map: tex, transparent: true, opacity: fail ? 0.5 : 0.32, depthWrite: false, side: THREE.DoubleSide }),
          )
          gate.position.copy(V(d.x, d.y, z0 + 1.0))
          gate.rotation.y = (d.angle_deg * Math.PI) / 180
          this.content.add(gate)
        }
        if (p === 'wheelchair' && !d.minor) {
          const tag = label(`${Math.round(d.est_clear_in)}″${d.status === 'fail' ? ' too narrow' : d.status === 'tight' ? ' tight' : ''}`, chip.fill, 1, chip.text)
          tag.position.copy(V(d.x, d.y, z0 + 2.15))
          this.content.add(tag)
        }
      }
      if (p === 'wheelchair')
        for (const t of unit.turning_circles) {
          const ring = new THREE.Mesh(
            new THREE.RingGeometry(t.r - 0.05, t.r, 64).rotateX(-Math.PI / 2),
            new THREE.MeshBasicMaterial({ color: NEED_COLOR.wheelchair, transparent: true, opacity: 0.9, depthWrite: false, side: THREE.DoubleSide }),
          )
          ring.position.copy(V(t.x, t.y, z0 + 0.04))
          this.content.add(ring)
        }
      const pc = PATH_COLOR[p]
      if (pc)
        for (const path of unit.paths.filter((q) => (p === 'older' ? q.from === 'bed' || q.to === 'kitchen' : q.from === 'entry'))) {
          if (path.points.length < 2) continue
          const pts = path.points.map(([x, y]) => V(x, y, z0 + 0.06))
          // the need's color on a white casing, so the route reads on every shade of the floor
          const curve = new THREE.CatmullRomCurve3(pts, false, 'centripetal')
          const n = Math.min(400, pts.length * 3)
          const casing = new THREE.Mesh(new THREE.TubeGeometry(curve, n, 0.055, 6), new THREE.MeshBasicMaterial({ color: '#ffffff', depthWrite: false }))
          const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, n, 0.035, 6), new THREE.MeshBasicMaterial({ color: pc, depthWrite: false }))
          tube.renderOrder = 2
          this.content.add(casing, tube)
        }
      if (p === 'deaf' && unit.view_points?.seat) {
        const [sx, sy] = unit.view_points.seat
        const seat = new THREE.Mesh(
          new THREE.CylinderGeometry(0.28, 0.28, 0.04, 32),
          new THREE.MeshBasicMaterial({ color: NEED_COLOR.deaf, transparent: true, opacity: 0.85, depthWrite: false }),
        )
        seat.position.copy(V(sx, sy, z0 + 0.05))
        this.content.add(seat)
        const tag = label('main seat', NEED_COLOR.deaf, 0.8, INK)
        tag.position.copy(V(sx, sy, z0 + 1.2))
        this.content.add(tag)
      }
      // problems pulse where they were measured
      for (const q of unit.problems.filter((x) => x.severity !== 'low' && x.floor === unit.floor)) {
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(0.3, 0.42, 48).rotateX(-Math.PI / 2),
          new THREE.MeshBasicMaterial({ color: q.severity === 'high' ? '#ffffff' : '#e0d4ff', transparent: true, depthWrite: false, side: THREE.DoubleSide }),
        )
        ring.position.copy(V(q.x, q.y, z0 + 0.05))
        this.content.add(ring)
        this.pulses.push({ mesh: ring, phase: (q.x * 7 + q.y * 3) % 1 })
      }
    }

    // furniture found in the scan is already there to see, so it gets an outline; planned pieces are solid boxes
    for (const f of unit.furniture) {
      const [fx, fy, fw, fd] = f.rect
      const h = f.tall ? 1.8 : 0.75
      const geo = new THREE.BoxGeometry(fw, h, fd)
      const box = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: '#c8a97e', transparent: true, opacity: opts.planned ? 0.6 : 0, depthWrite: false }))
      box.position.copy(V(fx + fw / 2, fy + fd / 2, z0 + h / 2))
      box.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: '#5b4020' })))
      this.content.add(box)
      const tag = label(f.label, '#5b4020', 0.6)
      tag.position.set(0, h / 2 + 0.15, 0)
      box.add(tag)
    }
  }

  private render() {
    if (!this.havePose || !this.visible) {
      this.renderer.clear()
      return
    }
    const t = (performance.now() - this.t0) / 1000
    for (const { mesh, phase } of this.pulses) {
      const k = (t * 0.8 + phase) % 1 // one pulse every 1.25 s
      mesh.scale.setScalar(0.6 + k * 1.6)
      ;(mesh.material as THREE.MeshBasicMaterial).opacity = 0.9 * (1 - k)
    }
    this.renderer.render(this.scene, this.camera)
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    disposeTree(this.scene)
    this.renderer.dispose()
  }
}
