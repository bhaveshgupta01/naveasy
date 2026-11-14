import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import type { Building } from '../types'

maplibregl.setWorkerUrl(workerUrl)

// Free vector tiles, no key needed.
const STYLE = 'https://tiles.openfreemap.org/styles/liberty'

interface Props {
  buildings: Building[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function scoreColor(score: number): string {
  if (score >= 75) return '#2e9e5b'
  if (score >= 50) return '#e0a526'
  return '#d9363e'
}

export default function MapView({ buildings, selectedId, onSelect }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const ready = useRef(false)
  const latest = useRef({ buildings, selectedId })
  latest.current = { buildings, selectedId }

  const apply = () => {
    const m = map.current
    const src = m?.getSource('buildings') as maplibregl.GeoJSONSource | undefined
    const { buildings: bs, selectedId: sel } = latest.current
    src?.setData({
      type: 'FeatureCollection',
      features: bs.map((b) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [b.lon, b.lat] },
        properties: { id: b.id, color: scoreColor(b.combined?.score ?? 0), selected: b.id === sel },
      })),
    })
  }

  useEffect(() => {
    if (!el.current || map.current) return
    const m = new maplibregl.Map({ container: el.current, style: STYLE, center: [-95.3795, 29.7437], zoom: 13.5 })
    m.addControl(new maplibregl.NavigationControl(), 'top-right')
    m.on('load', () => {
      m.addSource('buildings', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      m.addLayer({
        id: 'buildings',
        type: 'circle',
        source: 'buildings',
        paint: {
          'circle-radius': ['case', ['get', 'selected'], 11, 7],
          'circle-color': ['get', 'color'],
          'circle-stroke-color': '#111',
          'circle-stroke-width': ['case', ['get', 'selected'], 3, 1],
        },
      })
      m.on('click', 'buildings', (e: maplibregl.MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.id
        if (id) onSelect(String(id))
      })
      m.on('mouseenter', 'buildings', () => (m.getCanvas().style.cursor = 'pointer'))
      m.on('mouseleave', 'buildings', () => (m.getCanvas().style.cursor = ''))
      ready.current = true
      apply()
    })
    map.current = m
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onSelect])

  useEffect(() => {
    if (ready.current) apply()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildings, selectedId])

  return <div ref={el} className="map" role="region" aria-label="Map of homes colored by accessibility score" />
}
