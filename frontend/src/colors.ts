// One place for the colors that carry meaning. No red or green for places: a heat map shows how well a space fits a
// need, not whether it is good or bad, so fit is one violet scale (deep = fits well, pale = doesn't) and each need
// has its own hue. Checked with the dataviz validator: the fit steps are a single-hue ordinal ramp (monotone, visible
// gaps, pale end >= 2:1 on white); the four need hues stay apart for red-green color blindness in every pairing
// (worst pair dE 8, normal vision 19.4). Pink and gold sit under 3:1 on white, so they always come with a label.

// fit scale, strong colors: the 3D floor
export const FIT = { deep: '#6530b9', mid: '#8c5ee8', pale: '#ba99ff' }
// the same scale as lighter tints for the 2D floor plan, so walls, labels and doors stay readable on top
export const FIT_2D = { deep: '#6f48c3', mid: '#9573de', pale: '#bca1f3' }
export const INK = '#2b1a5e' // dark violet text, for labels on pale fills
export const HATCH = '#3b1d7a' // stripes that mark "doesn't work" cells, so the map never relies on color alone
export const FIXTURE_COLOR = '#a8865c' // counters, islands, toilets, tubs found in Matterport's 3D mesh

// heat grid values: 0 outside, 1 wall / built-in / furniture, 2 doesn't work, 3 tight, 4 works well
export const HEAT = ['rgba(0,0,0,0)', '#c9ccd1', FIT_2D.pale, FIT_2D.mid, FIT_2D.deep]

// door width labels: passes, tight, too narrow (also told apart by lightness and, for too narrow, a light chip)
export const DOOR = {
  pass: { fill: '#3b1d7a', text: '#ffffff' },
  tight: { fill: '#7a55cc', text: '#ffffff' },
  fail: { fill: '#e9e0ff', text: INK },
}

// need identity (map heat layer, swatches, routes inside a home)
export const NEED_COLOR = { wheelchair: '#2a78d6', blind: '#9c3fa6', deaf: '#e87ba4', older: '#c99400' } as const

// Building markers keep their green / orange / red score bands (and yellow when selected): a deliberate choice for
// the map's markers and the lists that match them. The heat maps above don't use these.
export function scoreColor(score: number): string {
  if (score >= 75) return '#1f8a4c'
  if (score >= 50) return '#d98a00'
  return '#c62828'
}
