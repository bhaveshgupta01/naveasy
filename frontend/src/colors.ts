// One place for the colors that carry meaning.

// Apartment floor-plan heat map (2D; the 3D tour floor uses the same red / yellow / green): values are
// 0 outside, 1 wall / furniture, 2 problem (red), 3 caution (yellow), 4 good (green). Built-ins are brown.
// Problem cells also get a hatch, so the plan never relies on color alone.
export const HEAT = ['rgba(0,0,0,0)', '#c9ccd1', '#f29b9b', '#f6d27a', '#a8dfae']
export const FIXTURE_COLOR = '#a8865c' // counters, islands, toilets, tubs found in Matterport's 3D mesh

// door width labels on the plan: passes, tight, too narrow
export const DOOR = {
  pass: { fill: '#14532d', text: '#ffffff' },
  tight: { fill: '#8a5a00', text: '#ffffff' },
  fail: { fill: '#a01616', text: '#ffffff' },
}

// Each need's identity color on the neighborhood map, swatches and legends: no red or green there, so a neighborhood
// is shaded by need, not judged. Checked with the dataviz validator: these four stay apart for red-green color
// blindness in every pairing (worst pair dE 8, normal vision 19.4). Pink and gold sit under 3:1 on white, so they
// always come with a label.
export const NEED_COLOR = { wheelchair: '#2a78d6', blind: '#9c3fa6', deaf: '#e87ba4', older: '#c99400' } as const

// Building markers keep their green / orange / red score bands (and yellow when selected), as do the lists that
// match them.
export function scoreColor(score: number): string {
  if (score >= 75) return '#1f8a4c'
  if (score >= 50) return '#d98a00'
  return '#c62828'
}
