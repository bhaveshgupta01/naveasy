import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { cellToBoundary, cellToLocalIj, latLngToCell } from 'h3-js'
import { API, TILE_URL, type HexRow } from '../api'
import type { Feature, FeatureCollection } from 'geojson'
import type { Building, Route } from '../types'
import { scoreColor } from '../colors'

maplibregl.setWorkerUrl(workerUrl)

// Free vector tiles, no key needed.
const STYLE = 'https://tiles.openfreemap.org/styles/positron'
export const CITY_VIEW = {
  houston: { center: [-95.3795, 29.7437] as [number, number], zoom: 12.6 },
  la: { center: [-118.27, 34.05] as [number, number], zoom: 11.5 },
  princeton: { center: [-74.655448, 40.3491177] as [number, number], zoom: 17 }, // the venue: Julis Romo Rabinowitz Building
}
const ROUTE_COLOR = { green: '#1f8a4c', amber: '#d98a00', red: '#c62828', grey: '#8a8f98' }

export interface MapTarget {
  center: [number, number]
  zoom: number
  n: number // bump to re-trigger
}

interface Props {
  buildings: Building[]
  hexes: HexRow[]
  hexColor: string
  hexColors: string[] // one per need shown; with several, each cell is split into one part per need
  showHexes: boolean
  selectedId: string | null
  highlighted: string[]
  route: Route | null
  target: MapTarget | null
  aerial: boolean
  threeD: boolean
  pins: { lon: number; lat: number; label: string }[]
  onSelect: (id: string) => void
  onHex: (cell: string, lngLat: [number, number]) => void
  onDrone?: (b: Building, pitch: number) => void // zoomed in on a home that has a drone model
  droneOpen?: boolean
}

// Past this zoom, a home with a drone model in the middle of the screen takes over the map view.
export const DRONE_ZOOM = 18


// Which need a cell shows when several are selected: a regular repeating pattern over the hexagon grid
// (stripes for 2, the classic three-coloring for 3, a 2 x 2 tile for 4).
const origins = new Map<string, string>()
function needTurn(h: string, n: number): number {
  try {
    const [lat, lng] = cellToBoundary(h)[0]
    const key = lng < -100 ? 'la' : 'houston'
    let origin = origins.get(key)
    if (!origin) {
      origin = key === 'la' ? latLngToCell(34.05, -118.25, 9) : latLngToCell(29.76, -95.37, 9)
      origins.set(key, origin)
    }
    void lat
    const { i, j } = cellToLocalIj(origin, h)
    const mod = (a: number, m: number) => ((a % m) + m) % m
    if (n === 2) return mod(i + j, 2)
    if (n === 3) return mod(i + j, 3)
    return mod(i, 2) + 2 * mod(j, 2)
  } catch {
    let x = 0
    for (let c = 0; c < h.length; c++) x = (x * 31 + h.charCodeAt(c)) >>> 0
    return x % n
  }
}

const fc = (features: Feature[]): FeatureCollection => ({ type: 'FeatureCollection', features })

export default function MapView(props: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const ready = useRef(false)
  const latest = useRef(props)
  latest.current = props
  const hexCache = useRef(new Map<string, number[][]>())
  const droneShown = useRef<string | null>(null) // the home just handed over, so it doesn't re-trigger until you zoom out
  const baseLayers = useRef<string[]>([])

  const src = (id: string) => map.current?.getSource(id) as maplibregl.GeoJSONSource | undefined

  const applyBuildings = () => {
    const { buildings, selectedId, highlighted } = latest.current
    const hi = new Set(highlighted)
    src('buildings')?.setData(
      fc(
        buildings.map((b) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [b.lon, b.lat] },
          properties: {
            id: b.id,
            color: scoreColor(b.combined?.score ?? 0),
            selected: b.id === selectedId,
            hi: hi.has(b.id),
            measured: b.measured_inside,
            sort: (b.combined?.score ?? 0) + (hi.has(b.id) ? 200 : 0),
          },
        })),
      ),
    )
    src('drones')?.setData(
      fc(
        buildings
          .filter((b) => b.splat_sid)
          .map((b) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [b.lon, b.lat] }, properties: { id: b.id, name: b.name } })),
      ),
    )
  }

  // Zoomed in on a home that has a drone model: hand the view over to it (and back when you zoom out of it).
  const checkDrone = () => {
    const m = map.current
    const { buildings, onDrone, droneOpen } = latest.current
    if (!m || !onDrone || droneOpen) return
    const z = m.getZoom()
    if (z < DRONE_ZOOM - 0.5) droneShown.current = null
    if (z < DRONE_ZOOM) return
    const c = m.getCanvas()
    const mid = { x: c.clientWidth / 2, y: c.clientHeight / 2 }
    const reach = Math.min(c.clientWidth, c.clientHeight) * 0.3
    let best: { b: Building; d: number } | null = null
    for (const b of buildings) {
      if (!b.splat_sid) continue
      const pt = m.project([b.lon, b.lat])
      const d = Math.hypot(pt.x - mid.x, pt.y - mid.y)
      if (d < reach && (!best || d < best.d)) best = { b, d }
    }
    if (!best || droneShown.current === best.b.id) return
    droneShown.current = best.b.id
    const b = best.b
    m.easeTo({ center: [b.lon, b.lat], zoom: Math.max(z, DRONE_ZOOM + 0.6), duration: 650 })
    window.setTimeout(() => latest.current.onDrone?.(b, m.getPitch()), 700)
  }

  // In 3D each home is a pillar: color = score band, height = score. Below zoom 14 the flat dots show instead.
  const applyPillars = () => {
    const { buildings, selectedId, highlighted, threeD } = latest.current
    const hi = new Set(highlighted)
    const m = map.current
    const show = threeD && !!m && m.getZoom() >= 14
    // only the homes on screen (plus a margin), so a city of 3,000 pillars never has to be rebuilt at once
    const bb = m?.getBounds()
    const pad = 0.004
    const near = show && bb ? buildings.filter((b) => b.lon > bb.getWest() - pad && b.lon < bb.getEast() + pad && b.lat > bb.getSouth() - pad && b.lat < bb.getNorth() + pad).slice(0, 400) : []
    src('pillars')?.setData(
      fc(
        show
          ? near.map((b) => {
              const r = b.id === selectedId || hi.has(b.id) ? 11 : 6
              const ring: number[][] = []
              for (let i = 0; i <= 12; i++) {
                const a = (i / 12) * Math.PI * 2
                ring.push([b.lon + (Math.cos(a) * r) / (111320 * Math.cos((b.lat * Math.PI) / 180)), b.lat + (Math.sin(a) * r) / 110570])
              }
              const score = b.combined?.score ?? 0
              return {
                type: 'Feature',
                geometry: { type: 'Polygon', coordinates: [ring] },
                properties: { id: b.id, color: b.id === selectedId || hi.has(b.id) ? '#ffd60a' : scoreColor(score), height: 25 + score * 0.7, opacity: b.measured_inside ? 1 : 0.4 },
              } as Feature
            })
          : [],
      ),
    )
  }

  const applyMode = () => {
    const m = map.current
    if (!m) return
    const { aerial, threeD } = latest.current
    m.setLayoutProperty('vexcel', 'visibility', aerial ? 'visible' : 'none')
    baseLayers.current.forEach((id) => m.getLayer(id) && m.setLayoutProperty(id, 'visibility', aerial ? 'none' : 'visible'))
    if (m.getLayer('3d-buildings')) m.setLayoutProperty('3d-buildings', 'visibility', threeD && !aerial ? 'visible' : 'none')
    m.setLayerZoomRange('buildings', 0, threeD ? 14 : 24)
    m.setLayerZoomRange('buildings-halo', 0, threeD ? 14 : 24)
    m.easeTo({ pitch: threeD ? 52 : 0, bearing: threeD ? -18 : 0, duration: 900 })
    applyTerrain()
    applyPillars()
  }

  // Staged loading: the flat aerial photo shows first; measured heights switch on only once you are close
  // (zoom 15+) and the map has stopped moving, so zooming and dragging never wait on 3D.
  const applyTerrain = () => {
    const m = map.current
    if (!m) return
    const want = latest.current.threeD && m.getZoom() >= 15
    if (want !== !!m.getTerrain()) m.setTerrain(want ? { source: 'dem', exaggeration: 1 } : null)
  }

  const applyHexes = () => {
    const { hexes, showHexes, hexColor, hexColors } = latest.current
    const m = map.current
    if (!m) return
    src('hexes')?.setData(
      fc(
        showHexes
          ? hexes.flatMap(([h, score, conf, , , each]): Feature[] => {
              let ring = hexCache.current.get(h)
              if (!ring) {
                ring = cellToBoundary(h, true)
                hexCache.current.set(h, ring)
              }
              // Several needs at once: neighboring cells take turns, one need each, in a regular pattern. A cell shows
              // its need's own color at that need's own score, so both heat maps stay readable, interleaved.
              const n = hexColors.length
              if (n > 1 && each) {
                const k = needTurn(h, n)
                return [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: { h, score: each[k], conf, color: hexColors[k] } }]
              }
              return [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: { h, score, conf, color: hexColor } }]
            })
          : [],
      ),
    )
    if (m.getLayer('hexes')) m.setPaintProperty('hexes', 'fill-color', ['get', 'color'])
  }

  const applyRoute = () => {
    const { route, pins } = latest.current
    src('route')?.setData(
      fc(
        (route?.stretches ?? []).map((s) => ({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: s.coords },
          properties: { color: ROUTE_COLOR[s.status], dashed: s.status === 'grey' },
        })),
      ),
    )
    const spots: Feature[] = (route?.hard_spots ?? []).map((h, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [h.lon, h.lat] },
      properties: { color: h.severity === 'red' ? '#c62828' : '#d98a00', label: String(i + 1) },
    }))
    if (route)
      spots.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [route.destination.lon, route.destination.lat] },
        properties: { color: '#1b3a6b', label: '★' },
      })
    pins.forEach((p) => spots.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { color: '#1b3a6b', label: p.label } }))
    src('spots')?.setData(fc(spots))
    if (route && map.current) {
      const b = new maplibregl.LngLatBounds()
      route.stretches.forEach((s) => s.coords.forEach((c) => b.extend(c as [number, number])))
      map.current.fitBounds(b, { padding: 90, maxZoom: 18, duration: 800, pitch: latest.current.threeD ? 50 : 0 })
    }
  }

  useEffect(() => {
    if (!el.current || map.current) return
    const m = new maplibregl.Map({ container: el.current, style: STYLE, ...CITY_VIEW.houston, attributionControl: false, maxPitch: 70 })
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')
    m.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: 'Imagery © Vexcel Imaging US, Inc. · 3D © Matterport · CoStar' }))
    m.on('load', () => {
      const empty = fc([])
      for (const id of ['hexes', 'route', 'buildings', 'spots', 'pillars', 'drones']) m.addSource(id, { type: 'geojson', data: empty })
      // Vexcel aerial imagery (about 7.5 cm per pixel in the urban layer), served through our API so the key stays private
      m.addSource('vexcel', { type: 'raster', tiles: [TILE_URL], tileSize: 400, minzoom: 10, maxzoom: 20, attribution: 'Imagery © Vexcel Imaging US, Inc.' })
      // Vexcel surface heights as terrain: past zoom 14 the ground, roofs and trees on screen rise into 3D,
      // and more loads as you drag (tiles are cut and cached by our API)
      m.addSource('dem', { type: 'raster-dem', tiles: [`${API}/dem/{z}/{x}/{y}.png`], tileSize: 256, minzoom: 15, maxzoom: 16, encoding: 'mapbox' })
      // street-map layers that sit under the aerial photo: switched off while it covers them, so they cost nothing
      baseLayers.current = m.getStyle().layers.filter((l) => l.type !== 'symbol' && l.type !== 'background').map((l) => l.id)
      const firstSymbol = m.getStyle().layers.find((l) => l.type === 'symbol')?.id
      m.addLayer({ id: 'vexcel', type: 'raster', source: 'vexcel', layout: { visibility: 'none' } }, firstSymbol)
      const vector = Object.entries(m.getStyle().sources).find(([, v]) => v.type === 'vector')?.[0]
      if (vector)
        m.addLayer({
          id: '3d-buildings',
          type: 'fill-extrusion',
          source: vector,
          'source-layer': 'building',
          minzoom: 13.5,
          layout: { visibility: 'none' },
          paint: { 'fill-extrusion-color': '#e3e6ea', 'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 6], 'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0], 'fill-extrusion-opacity': 0.8 },
        })
      m.addLayer({
        id: 'hexes',
        type: 'fill',
        source: 'hexes',
        maxzoom: 15,
        paint: {
          'fill-color': ['get', 'color'],
          // lighter = lower score; low-confidence cells are fainter still
          // fades out at street level so the aerial photo and 3D buildings stay readable
          'fill-opacity': [
            'interpolate',
            ['linear'],
            ['zoom'],
            13.5,
            ['*', ['interpolate', ['linear'], ['get', 'score'], 0, 0.04, 40, 0.18, 70, 0.45, 100, 0.72], ['match', ['get', 'conf'], 0, 0.75, 1]],
            15.5,
            ['*', ['interpolate', ['linear'], ['get', 'score'], 0, 0.0, 40, 0.03, 70, 0.07, 100, 0.12], ['match', ['get', 'conf'], 0, 0.75, 1]],
          ],
          'fill-outline-color': 'rgba(255,255,255,0.25)',
        },
      })
      m.addLayer({ id: 'route-casing', type: 'line', source: 'route', paint: { 'line-color': '#fff', 'line-width': 9 }, layout: { 'line-cap': 'round', 'line-join': 'round' } })
      m.addLayer({ id: 'route', type: 'line', source: 'route', filter: ['!', ['get', 'dashed']], paint: { 'line-color': ['get', 'color'], 'line-width': 5.5 }, layout: { 'line-cap': 'round' } })
      m.addLayer({ id: 'route-unknown', type: 'line', source: 'route', filter: ['get', 'dashed'], paint: { 'line-color': ['get', 'color'], 'line-width': 5, 'line-dasharray': [1, 1.2] } })
      m.addLayer({
        id: 'buildings-halo',
        type: 'circle',
        source: 'buildings',
        filter: ['any', ['get', 'hi'], ['get', 'selected']],
        paint: { 'circle-radius': 17, 'circle-color': '#ffd60a', 'circle-opacity': 0.55, 'circle-stroke-color': '#111', 'circle-stroke-width': 1.5 },
      })
      m.addLayer({
        id: 'buildings',
        type: 'circle',
        source: 'buildings',
        layout: { 'circle-sort-key': ['get', 'sort'] },
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2.2, 12, 4.5, 15, 8.5],
          'circle-color': ['get', 'color'],
          // homes measured from a scan are solid; building-only estimates are hollow
          'circle-opacity': ['case', ['get', 'measured'], 0.95, 0.25],
          'circle-stroke-color': ['case', ['get', 'measured'], '#fff', ['get', 'color']],
          'circle-stroke-width': ['case', ['get', 'selected'], 3, 1.2],
        },
      })
      m.addLayer({
        id: 'pillars',
        type: 'fill-extrusion',
        source: 'pillars',
        minzoom: 14,
        paint: { 'fill-extrusion-color': ['get', 'color'], 'fill-extrusion-height': ['get', 'height'], 'fill-extrusion-opacity': 0.9 },
      })
      m.addLayer({ id: 'spots', type: 'circle', source: 'spots', paint: { 'circle-radius': 11, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } })
      m.addLayer({
        id: 'spots-label',
        type: 'symbol',
        source: 'spots',
        layout: { 'text-field': ['get', 'label'], 'text-size': 12, 'text-font': ['Noto Sans Bold'], 'text-allow-overlap': true },
        paint: { 'text-color': '#fff' },
      })
      // homes with a drone model get a ring and a label, so you know zooming in there opens it
      m.addLayer({
        id: 'drones-ring',
        type: 'circle',
        source: 'drones',
        minzoom: 11.5, // visible as soon as the map comes up, so you can see which homes open in 3D
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 11.5, 6, 14, 9, 18, 22],
          'circle-color': 'rgba(0,0,0,0)',
          'circle-stroke-color': '#ffd60a',
          'circle-stroke-width': 2.5,
        },
      })
      m.addLayer({
        id: 'drones-label',
        type: 'symbol',
        source: 'drones',
        minzoom: 15,
        layout: {
          'text-field': ['step', ['zoom'], 'Drone 3D', 16.5, ['concat', ['get', 'name'], '\nDrone 3D: zoom in']],
          'text-size': 12,
          'text-font': ['Noto Sans Bold'],
          'text-offset': [0, 1.9],
          'text-anchor': 'top',
          'text-allow-overlap': false,
        },
        paint: { 'text-color': '#ffd60a', 'text-halo-color': '#111', 'text-halo-width': 1.6 },
      })
      m.on('click', (e) => {
        const hit = m.queryRenderedFeatures(e.point, { layers: ['pillars', 'buildings'] })[0]
        if (hit?.properties?.id) return latest.current.onSelect(String(hit.properties.id))
        const hex = m.queryRenderedFeatures(e.point, { layers: ['hexes'] })[0]
        if (hex?.properties?.h) latest.current.onHex(String(hex.properties.h), [e.lngLat.lng, e.lngLat.lat])
      })
      m.on('mouseenter', 'buildings', () => (m.getCanvas().style.cursor = 'pointer'))
      m.on('mouseleave', 'buildings', () => (m.getCanvas().style.cursor = ''))
      let idle: ReturnType<typeof setTimeout> | undefined
      m.on('moveend', () => {
        checkDrone()
        clearTimeout(idle)
        idle = setTimeout(() => {
          applyTerrain()
          applyPillars()
        }, 250)
      })
      ready.current = true
      applyMode()
      applyHexes()
      applyBuildings()
      applyRoute()
    })
    map.current = m
    ;(window as unknown as { __map?: maplibregl.Map }).__map = m // handy for debugging and scripted demos
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (ready.current) applyBuildings()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.buildings, props.selectedId, props.highlighted])
  useEffect(() => {
    if (ready.current) applyMode()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.aerial, props.threeD])
  useEffect(() => {
    if (ready.current) applyPillars()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.buildings, props.selectedId, props.highlighted])
  useEffect(() => {
    if (ready.current) applyHexes()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.hexes, props.showHexes, props.hexColor, props.hexColors])
  useEffect(() => {
    if (ready.current) applyRoute()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.route, props.pins])
  // coming back from a drone model: step back out a little so the map is in view again
  const wasOpen = useRef(false)
  useEffect(() => {
    const m = map.current
    if (wasOpen.current && !props.droneOpen && m) {
      // the scroll that zoomed out of the drone model shouldn't carry on zooming the map out to the whole city
      // (trackpads keep sending scroll for a moment), so map zoom waits until the scrolling has stopped
      m.scrollZoom.disable()
      const until = Date.now() + 1200 // at least this long, and until the scroll has been quiet for 0.7 s
      let quiet = window.setTimeout(release, 1200)
      function release() {
        if (Date.now() < until) return void (quiet = window.setTimeout(release, until - Date.now()))
        window.removeEventListener('wheel', onWheel, true)
        m!.scrollZoom.enable()
      }
      function onWheel() {
        window.clearTimeout(quiet)
        quiet = window.setTimeout(release, 700)
      }
      window.addEventListener('wheel', onWheel, { capture: true, passive: true })
      if (m.getZoom() > DRONE_ZOOM - 0.6) m.easeTo({ zoom: DRONE_ZOOM - 1.2, duration: 700 })
    }
    wasOpen.current = !!props.droneOpen
  }, [props.droneOpen])
  useEffect(() => {
    if (props.target) map.current?.flyTo({ center: props.target.center, zoom: props.target.zoom, duration: 1200, pitch: latest.current.threeD ? 52 : 0 })
  }, [props.target])

  const turn = (deg: number) => map.current?.easeTo({ bearing: map.current.getBearing() + deg, duration: 500 })
  const tilt = (deg: number) => map.current?.easeTo({ pitch: Math.max(0, Math.min(70, map.current.getPitch() + deg)), duration: 400 })

  return (
    <>
      <div ref={el} className="map" role="region" aria-label="Map of neighborhoods and homes colored by score. The same homes are listed in the sidebar." />
      <div className="map-turn" role="group" aria-label="Rotate and tilt the map">
        <button onClick={() => turn(-45)} aria-label="Rotate left" title="Rotate left">
          ⟲
        </button>
        <button onClick={() => turn(45)} aria-label="Rotate right" title="Rotate right">
          ⟳
        </button>
        <button onClick={() => tilt(12)} aria-label="Tilt more" title="Tilt more">
          ⤒
        </button>
        <button onClick={() => tilt(-12)} aria-label="Tilt less" title="Tilt less">
          ⤓
        </button>
        <button onClick={() => map.current?.easeTo({ bearing: 0, duration: 500 })} aria-label="Face north" title="Face north">
          N
        </button>
        <span>or right-drag / Ctrl-drag</span>
      </div>
    </>
  )
}
