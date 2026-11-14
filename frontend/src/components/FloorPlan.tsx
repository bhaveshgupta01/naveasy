import { useEffect, useRef } from 'react'
import type { Furniture, Unit } from '../types'
import { DOOR, FIXTURE_COLOR, HATCH, HEAT, INK, NEED_COLOR } from '../colors'

interface Props {
  unit: Unit
  size?: number
  focus?: { x: number; y: number } | null
  furniture?: Furniture[] // overrides unit.furniture (e.g. a plan)
  ghost?: Furniture[] // drawn as outlines (e.g. a suggested position)
  compact?: boolean
}

export default function FloorPlan({ unit, size = 520, focus, furniture, ghost, compact }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const c = ref.current
    if (!c) return
    const [x0, y0, x1, y1] = unit.bounds
    const s = Math.min(size / (x1 - x0), size / (y1 - y0))
    const W = Math.round((x1 - x0) * s)
    const H = Math.round((y1 - y0) * s)
    const dpr = window.devicePixelRatio || 1
    c.width = W * dpr
    c.height = H * dpr
    c.style.width = `${W}px`
    c.style.height = `${H}px`
    const ctx = c.getContext('2d')!
    ctx.scale(dpr, dpr)
    const X = (x: number) => (x - x0) * s
    const Y = (y: number) => H - (y - y0) * s // y up in meters, down on canvas
    ctx.clearRect(0, 0, W, H)

    const g = unit.heat
    const cs = g.cell_m * s
    g.values.forEach((row, j) => {
      let i = 0
      while (i < row.length) {
        const v = row[i]
        let k = i + 1
        while (k < row.length && row[k] === v) k++
        if (v > 1) {
          ctx.fillStyle = HEAT[v]
          ctx.fillRect(X(g.origin[0] + i * g.cell_m), Y(g.origin[1] + (j + 1) * g.cell_m), (k - i) * cs + 0.6, cs + 0.6)
        }
        i = k
      }
    })

    // built-ins from the 3D mesh, drawn like solid furniture
    ctx.fillStyle = FIXTURE_COLOR
    ;(unit.fixtures ?? []).forEach(([j, a, b]) =>
      ctx.fillRect(X(g.origin[0] + a * g.cell_m), Y(g.origin[1] + (j + 1) * g.cell_m), (b - a) * cs + 0.6, cs + 0.6),
    )

    // "doesn't work" cells get a hatch, so the map doesn't rely on color alone
    if (!compact) {
      ctx.save()
      ctx.globalAlpha = 0.45
      ctx.strokeStyle = HATCH
      ctx.lineWidth = 1
      const step = Math.max(2, Math.round(0.25 / g.cell_m))
      for (let j = 0; j < g.height; j += step)
        for (let i = 0; i < g.width; i += step)
          if (g.values[j][i] === 2) {
            const px = X(g.origin[0] + i * g.cell_m)
            const py = Y(g.origin[1] + j * g.cell_m)
            ctx.beginPath()
            ctx.moveTo(px, py)
            ctx.lineTo(px + step * cs, py - step * cs)
            ctx.stroke()
          }
      ctx.restore()
    }

    const items = furniture ?? unit.furniture
    items.forEach((f) => {
      const [fx, fy, fw, fd] = f.rect
      ctx.fillStyle = '#c8a97e'
      ctx.strokeStyle = '#5b4020'
      ctx.lineWidth = 1.5
      ctx.fillRect(X(fx), Y(fy + fd), fw * s, fd * s)
      ctx.strokeRect(X(fx), Y(fy + fd), fw * s, fd * s)
      if (!compact) {
        ctx.fillStyle = '#2b1d0a'
        ctx.font = '600 11px system-ui'
        ctx.textAlign = 'center'
        ctx.fillText(f.label, X(fx + fw / 2), Y(fy + fd / 2) + 4, Math.max(30, fw * s - 4))
      }
    })
    ;(ghost ?? []).forEach((f) => {
      const [fx, fy, fw, fd] = f.rect
      ctx.setLineDash([6, 4])
      ctx.strokeStyle = '#1b3a6b'
      ctx.lineWidth = 2.5
      ctx.strokeRect(X(fx), Y(fy + fd), fw * s, fd * s)
      ctx.setLineDash([])
    })

    ctx.strokeStyle = '#15171a'
    ctx.lineWidth = compact ? 1.5 : 3.5
    ctx.lineCap = 'square'
    unit.walls.forEach(([ax, ay, bx, by]) => {
      ctx.beginPath()
      ctx.moveTo(X(ax), Y(ay))
      ctx.lineTo(X(bx), Y(by))
      ctx.stroke()
    })
    if (compact) return

    ctx.strokeStyle = '#3d8fd1'
    ctx.lineWidth = 3
    unit.windows.forEach((w) => {
      const a = (w.angle_deg * Math.PI) / 180
      const dx = (Math.cos(a) * w.width_m) / 2
      const dy = (Math.sin(a) * w.width_m) / 2
      ctx.beginPath()
      ctx.moveTo(X(w.x - dx), Y(w.y - dy))
      ctx.lineTo(X(w.x + dx), Y(w.y + dy))
      ctx.stroke()
    })

    if (unit.profile === 'wheelchair') {
      ctx.strokeStyle = NEED_COLOR.wheelchair
      ctx.lineWidth = 1.5
      ctx.setLineDash([5, 4])
      unit.turning_circles.forEach((t) => {
        ctx.beginPath()
        ctx.arc(X(t.x), Y(t.y), t.r * s, 0, Math.PI * 2)
        ctx.stroke()
      })
      ctx.setLineDash([])
    }
    if (unit.profile === 'blind' || unit.profile === 'older') {
      // the route in the need's color on a white casing, so it reads on any shade of the heat map
      const route = unit.paths.filter((p) => (unit.profile === 'older' ? p.from === 'bed' || p.to === 'kitchen' : p.from === 'entry'))
      for (const [color, width] of [['#ffffff', 5.5], [NEED_COLOR[unit.profile], 2.8]] as const) {
        ctx.strokeStyle = color
        ctx.lineWidth = width
        ctx.lineJoin = 'round'
        route.forEach((p) => {
          ctx.beginPath()
          p.points.forEach(([px, py], i) => (i ? ctx.lineTo(X(px), Y(py)) : ctx.moveTo(X(px), Y(py))))
          ctx.stroke()
        })
      }
    }
    if (unit.profile === 'deaf' && unit.view_points?.seat) {
      const [sx, sy] = unit.view_points.seat
      ctx.fillStyle = NEED_COLOR.deaf
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(X(sx), Y(sy), 6, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.font = '600 11px system-ui'
      ctx.textAlign = 'left'
      ctx.lineWidth = 3
      ctx.strokeText('main seat', X(sx) + 9, Y(sy) + 4)
      ctx.fillStyle = INK
      ctx.fillText('main seat', X(sx) + 9, Y(sy) + 4)
    }

    ctx.textAlign = 'center'
    ctx.font = '12px system-ui'
    // room names with a white outline, readable on every shade
    ctx.fillStyle = '#2f333a'
    ctx.strokeStyle = 'rgba(255,255,255,0.9)'
    ctx.lineWidth = 3
    ctx.lineJoin = 'round'
    unit.rooms.forEach((r) => {
      if (r.area_m2 < 1.5 || r.type === 'Other') return
      ctx.strokeText(r.type, X(r.centroid[0]), Y(r.centroid[1]) + 4)
      ctx.fillText(r.type, X(r.centroid[0]), Y(r.centroid[1]) + 4)
    })

    ctx.font = '700 11.5px system-ui'
    unit.doors
      .filter((d) => d.is_door && !(d.exterior && !d.entry))
      .forEach((d) => {
        const label = `${Math.round(d.est_clear_in)}″${d.entry ? ' entry' : ''}`
        const w = ctx.measureText(label).width + 8
        const px = Math.min(W - w / 2 - 2, Math.max(w / 2 + 2, X(d.x)))
        const py = Math.min(H - 11, Math.max(11, Y(d.y)))
        const c = DOOR[d.status]
        ctx.fillStyle = c.fill
        ctx.strokeStyle = d.status === 'fail' ? INK : '#ffffff'
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.roundRect(px - w / 2, py - 9, w, 18, 5)
        ctx.fill()
        ctx.stroke()
        ctx.fillStyle = c.text
        ctx.fillText(label, px, py + 4)
      })

    if (focus) {
      ctx.strokeStyle = '#111'
      ctx.lineWidth = 3
      ctx.beginPath()
      ctx.arc(X(focus.x), Y(focus.y), 20, 0, Math.PI * 2)
      ctx.stroke()
      ctx.strokeStyle = '#ffd60a'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }
  }, [unit, size, focus, furniture, ghost, compact])

  const narrow = unit.doors.filter((d) => d.is_door && !d.minor && d.status !== 'pass' && !(d.exterior && !d.entry)).length
  return (
    <canvas
      ref={ref}
      className="floorplan"
      role="img"
      aria-label={`Floor plan of ${unit.caption}, ${unit.profile} view. Score ${unit.scores[unit.profile].score} of 100. ${narrow} main doorways narrower than 32 inches. ${unit.problems.length} problems listed below.`}
    />
  )
}
