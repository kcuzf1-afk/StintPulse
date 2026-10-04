import { expect, test } from 'vitest'
import { parseRoute, routeHash } from './routes'
import { formatClock, telemetryStatus, STALE_MS } from './connection'
import { friendlyError } from './errors'
import { closesAtLine, traceSegments } from './TrackMap'
import { defaultPair, groupKey, theoreticalBest, type CatalogLap } from './AnalysisView'
import { channelUnit, channelValue } from './TelemetryChart'
import type { Meta, Sample } from './types'

test('routes: five areas, old names as aliases', () => {
  expect(parseRoute('')).toEqual({ page: 'live' })
  expect(parseRoute('#/engineering')).toEqual({ page: 'live' })
  expect(parseRoute('#/live-mode')).toEqual({ page: 'live' })
  expect(parseRoute('#/tablet')).toEqual({ page: 'live' })
  expect(parseRoute('#/analysis')).toEqual({ page: 'analysis' })
  expect(parseRoute('#/diagnostics')).toEqual({ page: 'settings', tab: 'advanced' })
  expect(parseRoute('#/settings/network')).toEqual({ page: 'settings', tab: 'network' })
  expect(parseRoute('#/settings/diagnose')).toEqual({ page: 'settings', tab: 'advanced' })
  expect(parseRoute('#/nonsense')).toEqual({ page: 'live' })
  expect(routeHash({ page: 'settings', tab: 'data' })).toBe('#/settings/data')
  expect(routeHash({ page: 'onboard' })).toBe('#/onboard')
})

const sample = { packet_id: 1 } as Sample
test('telemetry: fresh, paused, stale and none are distinct', () => {
  const now = 1_000_000
  expect(telemetryStatus({ sample: null, status: 'live', source: 'ac' }, 'connected', null, now).key).toBe('none')
  expect(telemetryStatus({ sample, status: 'live', source: 'ac' }, 'connected', now - 500, now).key).toBe('live')
  expect(telemetryStatus({ sample, status: 'demo', source: 'demo' }, 'connected', now - 500, now).key).toBe('demo')
  expect(telemetryStatus({ sample, status: 'paused', source: 'ac' }, 'connected', now - 9000, now).key).toBe('paused')
  const old = telemetryStatus({ sample, status: 'live', source: 'ac' }, 'connected', now - STALE_MS - 1, now)
  expect(old.key).toBe('stale')
  expect(old.frozen).toBe(true)
  const cut = telemetryStatus({ sample, status: 'live', source: 'ac' }, 'disconnected', now - 100, now)
  expect(cut.key).toBe('stale')
  expect(cut.de).toMatch(/^LETZTER STAND \d\d:\d\d:\d\d$/)
  expect(formatClock(null)).toBe('—')
})

test('errors: understandable message with an action', () => {
  expect(friendlyError(new TypeError('Failed to fetch'), true)).toContain('Keine Verbindung zum PC-Programm')
  expect(friendlyError('Error: Select laps from the same source, car, track and layout', true)).toContain('nicht vergleichbar')
  expect(friendlyError('Error: HTTP 500', true)).toContain('Erweitert / Diagnose')
  expect(friendlyError(new TypeError('Failed to fetch'), true)).not.toContain('TypeError')
})

const ring = (n: number, gapPoints = 1, radius = 300): Array<[number, number, number]> =>
  Array.from({ length: n - gapPoints }, (_, i) => {
    const a = (i / n) * 2 * Math.PI
    return [i / n, Math.cos(a) * radius, Math.sin(a) * radius]
  })
test('map closes only a complete lap with a small gap at the line', () => {
  expect(closesAtLine(ring(200, 1), true)).toBe(true) // last metres before S/F
  expect(closesAtLine(ring(200, 1), false)).toBe(false) // incomplete lap: never closed
  expect(closesAtLine(ring(200, 40), true)).toBe(false) // big gap: not invented
  expect(closesAtLine(ring(10, 1), true)).toBe(false) // too few points
})
test('provisional map: undriven part is a gap, never a straight shortcut', () => {
  const full = ring(400, 0)
  expect(traceSegments(full)).toHaveLength(1)
  // Joined at 50 %, now at 30 % of the next lap: 30..50 % not driven yet.
  const partial = full.filter((p) => p[0] < 0.3 || p[0] >= 0.5)
  const parts = traceSegments(partial)
  expect(parts).toHaveLength(2)
  expect(parts[0].at(-1)![0]).toBeLessThan(0.3)
  expect(parts[1][0][0]).toBeGreaterThanOrEqual(0.5)
})

const meta = (car = 'F'): Meta =>
  ({ source: 'ac', car, track: 'T', layout: 'GP', driver: 'D', session_type: 0, track_length: 1000, sector_count: 3, max_rpm: 0, compound: '', air_temp: null, road_temp: null }) as Meta
const lap = (id: string, number: number, ms: number, over: Partial<CatalogLap> = {}): CatalogLap => ({
  id,
  session_id: 's',
  number,
  duration_ms: ms,
  valid: true,
  complete: true,
  reasons: [],
  sectors_ms: [30000, 30000, ms - 60000],
  events: [],
  tips: [],
  meta: meta(),
  created_at: '2026-10-03',
  ...over,
})
test('analysis helpers: comparable groups, default pair, theoretical best', () => {
  expect(groupKey(meta('A'))).not.toBe(groupKey(meta('B')))
  const laps = [
    lap('1', 1, 92000),
    lap('2', 2, 91000),
    lap('3', 3, 90500, { valid: false }),
    lap('4', 4, 93000),
    lap('5', 5, 99000, { complete: false }),
  ]
  expect(defaultPair(laps)).toEqual({ ref: '2', cmp: '4' })
  expect(defaultPair(laps, ['4', '1'])).toEqual({ ref: '1', cmp: '4' })
  const best = theoreticalBest([
    lap('a', 1, 92000, { sectors_ms: [30500, 30000, 31500] }),
    lap('b', 2, 91800, { sectors_ms: [30000, 30400, 31400] }),
    lap('c', 3, 85000, { valid: false, sectors_ms: [20000, 20000, 45000] }),
  ])
  expect(best).toEqual({ ms: 30000 + 30000 + 31400, sectors: [30000, 30000, 31400] })
  expect(theoreticalBest([lap('x', 1, 90000, { sectors_ms: [30000, null, 30000] })])).toBeNull()
})

test('chart units follow the units setting', () => {
  expect(channelUnit('speed', true)).toBe('mph')
  expect(channelUnit('pressure_FL', false)).toBe('bar')
  expect(channelUnit('core_FL', true)).toBe('°F')
  expect(channelValue('gas', 0.5, false)).toBe(50)
  expect(channelValue('pressure_FL', 29.0, false)).toBeCloseTo(2.0, 2)
  expect(channelValue('core_FL', 100, true)).toBe(212)
  expect(channelValue('speed', null, false)).toBeNull()
})
