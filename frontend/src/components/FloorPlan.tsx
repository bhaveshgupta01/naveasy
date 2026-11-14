import { useEffect, useRef } from 'react'
import type { Unit } from '../types'

// values: 0 outside, 1 blocked, 2 red, 3 amber, 4 green
const HEAT = ['transparent', '#cfcfcf', '#f08a8a', '#f5cf73', '#9fdc9f']

export default function FloorPlan({ unit, size = 420 }: { unit: Unit; size?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const c = ref.current
    if (!c) return
    const [x0, y0, x1, y1] = unit.bounds
    const pad = 16
    const s = Math.min((size - 2 * pad) / (x1 - x0), (size - 2 * pad) / (y1 - y0))
    const W = Math.round((x1 - x0) * s + 2 * pad)
    const H = Math.round((y1 - y0) * s + 2 * pad)
    c.width = W
    c.height = H
    const ctx = c.getContext('2d')!
    const X = (x: number) => pad + (x - x0) * s
    const Y = (y: number) => H - pad - (y - y0) * s // y up in meters, down on canvas
    ctx.clearRect(0, 0, W, H)

    const g = unit.heat
    g.values.forEach((row, j) =>
      row.forEach((v, i) => {
        if (v === 0) return
        ctx.fillStyle = HEAT[v]
        const gx = g.origin[0] + i * g.cell_m
        const gy = g.origin[1] + (j + 1) * g.cell_m
        ctx.fillRect(X(gx), Y(gy), g.cell_m * s + 0.5, g.cell_m * s + 0.5)
      }),
    )

    ctx.strokeStyle = '#111'
    ctx.lineWidth = 3
    unit.walls.forEach(([ax, ay, bx, by]) => {
      ctx.beginPath()
      ctx.moveTo(X(ax), Y(ay))
      ctx.lineTo(X(bx), Y(by))
      ctx.stroke()
    })

    ctx.font = '11px system-ui'
    ctx.fillStyle = '#444'
    unit.rooms.forEach((r) => {
      if (!r.polygon.length) return
      const cx = r.polygon.reduce((a, p) => a + p[0], 0) / r.polygon.length
      const cy = r.polygon.reduce((a, p) => a + p[1], 0) / r.polygon.length
      ctx.fillText(r.type, X(cx) - 20, Y(cy))
    })

    ctx.font = 'bold 11px system-ui'
    unit.doors
      .filter((d) => d.kind === 'doorway')
      .forEach((d) => {
        ctx.fillStyle = d.status === 'pass' ? '#1b6b3a' : d.status === 'tight' ? '#8a5a00' : '#a11'
        ctx.fillText(`${Math.round(d.est_clear_in)}"`, X(d.x) + 4, Y(d.y) - 4)
      })
  }, [unit, size])

  return (
    <canvas
      ref={ref}
      className="floorplan"
      role="img"
      aria-label={`Floor plan of ${unit.caption}: ${unit.doors.filter((d) => d.status !== 'pass').length} doorways narrower than 32 inches`}
    />
  )
}
