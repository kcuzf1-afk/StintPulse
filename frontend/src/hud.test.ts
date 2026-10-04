import { beforeEach, expect, test } from 'vitest'
import {
  buildLiveHud,
  buildReplayHud,
  compoundView,
  contentRect,
  DEFAULT_HUD,
  formatDelta,
  formatLapTime,
  formatSectorTime,
  hudFraction,
  hudOffset,
  loadHud,
  sanitizeHud,
  saveHud,
  sectorColor,
  surname,
} from './hud'
import type { Frame, LapSummary, Meta, Sample, Timing } from './types'

const meta: Meta = {
  source: 'ac',
  driver: 'Kimi Antonelli',
  car: 'formula',
  track: 'monza',
  layout: 'gp',
  session_type: 0,
  track_length: 5793,
  sector_count: 3,
  max_rpm: 12000,
  compound: 'Pirelli Soft (S)',
  air_temp: 24,
  road_temp: 33,
}
const sample = (over: Partial<Sample> = {}): Sample => ({
  source: 'ac',
  packet_id: 1,
  captured_at: 1,
  status: 2,
  completed_laps: 4,
  lap_ms: 45123,
  last_lap_ms: 92064,
  best_lap_ms: 91000,
  sector_index: 1,
  last_sector_ms: 30100,
  position: 1,
  session_left_ms: null,
  lap_pos: 0.4,
  coords: [0, 0, 0],
  in_pit: false,
  tyres_out: 0,
  penalty_s: 0,
  channels: {},
  tyres: [],
  ...over,
})
const reference = { id: 'pb', kind: 'personal_best' as const, number: 2, duration_ms: 92500, sectors_ms: [30500, 31000, 31000] }
const timing = (over: Partial<Timing> = {}): Timing => ({
  lap_number: 5,
  lap_ms: 45123,
  game_status: 2,
  sector_index: 1,
  sector_count: 3,
  sectors_known: true,
  sectors_ms: [30100, null, null],
  started_at_line: true,
  reasons: [],
  invalid_reasons: [],
  last_lap: null,
  reference,
  best_sectors_ms: [30200, 30900, 30800],
  hold_ms: 5000,
  resolution_ms: 1,
  ...over,
})
const frame = (over: Partial<Frame> = {}): Frame =>
  ({
    type: 'telemetry',
    status: 'live',
    source: 'ac',
    connected: true,
    error: null,
    meta,
    sample: sample(),
    timing: timing(),
    session_id: 's',
    ...over,
  }) as Frame

test('TV style shows the surname in capitals', () => {
  expect(surname('Kimi Antonelli')).toBe('ANTONELLI')
  expect(surname('  Max   Mustermann ')).toBe('MUSTERMANN')
  expect(surname('Demo')).toBe('DEMO')
  expect(surname('')).toBe('')
  expect(surname(null)).toBe('')
})

test('lap and sector time formatting', () => {
  expect(formatLapTime(92064)).toBe('1:32.064')
  expect(formatLapTime(0)).toBe('0:00.000')
  expect(formatLapTime(5_999)).toBe('0:05.999')
  expect(formatLapTime(3_600_001)).toBe('60:00.001')
  expect(formatLapTime(null)).toBe('—')
  expect(formatLapTime(-5)).toBe('—')
  expect(formatLapTime(Number.NaN)).toBe('—')
  expect(formatSectorTime(30123)).toBe('30.123')
  expect(formatSectorTime(61000)).toBe('1:01.000')
  expect(formatSectorTime(undefined)).toBe('—')
  expect(formatDelta(-312)).toBe('−0.312')
  expect(formatDelta(1500)).toBe('+1.500')
  expect(formatDelta(0)).toBe('±0.000')
})

test('sector colours incl. missing reference values', () => {
  expect(sectorColor(null, 30000, 29000, false)).toBe('gray') // not completed
  expect(sectorColor(30000, null, null, false)).toBe('gray') // no comparison data
  expect(sectorColor(29999, 30000, 29500, false)).toBe('green')
  expect(sectorColor(30001, 30000, 29500, false)).toBe('yellow')
  expect(sectorColor(30000, 30000, 29500, false)).toBe('yellow') // equal is not faster (1 ms resolution)
  expect(sectorColor(29400, 30000, 29500, false)).toBe('violet') // new personal sector best
  expect(sectorColor(29400, null, 29500, false)).toBe('violet')
  expect(sectorColor(29400, 30000, 29500, true)).toBe('red') // invalid lap wins
})

test('tyre label only from the game code or a manual mapping', () => {
  expect(compoundView('Pirelli Soft (S)')).toEqual({ label: 'S', full: 'Pirelli Soft (S)', manual: false })
  expect(compoundView('Street 90s')).toEqual({ label: '', full: 'Street 90s', manual: false })
  expect(compoundView('Street 90s', { 'Street 90s': 'ST' })).toEqual({ label: 'ST', full: 'Street 90s', manual: true })
  expect(compoundView('')).toBeNull()
})

test('running lap: live game time, completed sectors coloured, others grey', () => {
  const hud = buildLiveHud(frame({ timing: timing({ best_sectors_ms: [30000, 30900, 30800] }) }), 'connected')
  expect(hud.mode).toBe('live')
  expect(hud.status).toBeNull()
  expect(hud.timeMs).toBe(45123)
  expect(hud.phase).toBe('running')
  expect(hud.driver).toBe('Kimi Antonelli')
  expect(hud.position).toBe(1)
  expect(hud.compound?.label).toBe('S')
  expect(hud.sectors.map((s) => s.color)).toEqual(['green', 'gray', 'gray'])
  expect(hud.reference).toEqual({ kind: 'personal_best', ms: 92500 })
})

test('completed lap is held for hold_ms, then the new lap runs', () => {
  const last = {
    number: 4,
    duration_ms: 92064,
    sectors_ms: [30100, 31200, 30764],
    valid: true,
    complete: true,
    reasons: [],
    invalid_reasons: [],
    reference,
    best_sectors_ms: [30200, 30900, 30800],
    age_ms: 1200,
  }
  const held = buildLiveHud(
    frame({ timing: timing({ lap_number: 5, lap_ms: 1200, sector_index: 0, sectors_ms: [null, null, null], last_lap: last }) }),
    'connected',
  )
  expect(held.phase).toBe('completed')
  expect(held.timeMs).toBe(92064)
  expect(held.lapNumber).toBe(4)
  expect(held.sectors.map((s) => s.color)).toEqual(['violet', 'yellow', 'violet'])
  expect(held.deltaMs).toBe(92064 - 92500)
  const after = buildLiveHud(
    frame({ timing: timing({ lap_number: 5, lap_ms: 5200, sector_index: 0, sectors_ms: [null, null, null], last_lap: { ...last, age_ms: 5200 } }) }),
    'connected',
  )
  expect(after.phase).toBe('running')
  expect(after.timeMs).toBe(5200)
  expect(after.lapNumber).toBe(5)
  expect(after.sectors.every((s) => s.color === 'gray')).toBe(true)
})

test('invalid lap is red; partial first lap is marked, not red', () => {
  const bad = buildLiveHud(frame({ timing: timing({ invalid_reasons: ['off_track_inferred'], reasons: ['off_track_inferred'] }) }), 'connected')
  expect(bad.invalid).toBe(true)
  expect(bad.sectors[0].color).toBe('red')
  const partial = buildLiveHud(frame({ timing: timing({ started_at_line: false, reasons: ['partial_start'] }) }), 'connected')
  expect(partial.incomplete).toBe(true)
  expect(partial.invalid).toBe(false)
})

test('missing sectors are not invented', () => {
  const hud = buildLiveHud(frame({ timing: timing({ sectors_known: false, sector_count: 1 }) }), 'connected')
  expect(hud.sectorsAvailable).toBe(false)
})

test('pause freezes, stale / disconnected show no running time', () => {
  const paused = buildLiveHud(frame({ status: 'paused' }), 'connected')
  expect(paused.phase).toBe('frozen')
  expect(paused.status?.de).toBe('PAUSE')
  expect(paused.timeMs).toBe(45123)
  const stale = buildLiveHud(frame({ status: 'stale' }), 'connected')
  expect(stale.timeMs).toBeNull()
  expect(stale.status?.de).toBe('DATEN VERALTET')
  const offline = buildLiveHud(frame(), 'disconnected')
  expect(offline.timeMs).toBeNull()
  expect(offline.status?.de).toBe('KEINE VERBINDUNG')
})

test('demo is labelled; after switching to AC no demo value remains', () => {
  const demo = buildLiveHud(
    frame({ status: 'demo', source: 'demo', meta: { ...meta, source: 'demo', driver: 'Demo Driver' } }),
    'connected',
  )
  expect(demo.mode).toBe('demo')
  expect(demo.driver).toBe('Demo Driver')
  // Backend after the switch: no sample, no meta, no timing.
  const waiting = buildLiveHud(frame({ status: 'waiting_game', source: 'ac', meta: null, sample: null, timing: null }), 'connected')
  expect(waiting.mode).toBe('live')
  expect(waiting.driver).toBeNull()
  expect(waiting.timeMs).toBeNull()
  expect(waiting.sectors).toEqual([])
  expect(waiting.status?.de).toBe('WARTE AUF SPIEL')
})

const lapSummary = (over: Partial<LapSummary>): LapSummary => ({
  id: 'x',
  session_id: 's',
  number: 1,
  duration_ms: 93000,
  valid: true,
  complete: true,
  reasons: [],
  sectors_ms: [31000, 31000, 31000],
  events: [],
  tips: [],
  ...over,
})

test('replay uses only the replayed lap and its session', () => {
  const laps = [
    lapSummary({ id: 'a', number: 1, duration_ms: 93000, sectors_ms: [31000, 31000, 31000] }),
    lapSummary({ id: 'b', number: 2, duration_ms: 92000, sectors_ms: [30500, 30800, 30700] }),
    lapSummary({ id: 'c', number: 3, duration_ms: 92500, sectors_ms: [30400, 31200, 30900] }),
  ]
  const mid = buildReplayHud(sample({ lap_ms: 40000, sector_index: 1, position: 3 }), laps[2], laps, meta, false)
  expect(mid.mode).toBe('replay')
  expect(mid.synthetic).toBe(false)
  expect(buildReplayHud(sample({ source: 'demo' }), laps[2], laps, { ...meta, source: 'demo' }, false).synthetic).toBe(true)
  expect(mid.timeMs).toBe(40000)
  expect(mid.position).toBe(3)
  expect(mid.reference).toEqual({ kind: 'session_best', ms: 92000 })
  expect(mid.sectors.map((s) => s.color)).toEqual(['violet', 'gray', 'gray'])
  const end = buildReplayHud(sample({ lap_ms: 92500, sector_index: 2 }), laps[2], laps, meta, true)
  expect(end.phase).toBe('completed')
  expect(end.timeMs).toBe(92500)
  expect(end.sectors.map((s) => s.color)).toEqual(['violet', 'yellow', 'yellow'])
  expect(end.deltaMs).toBe(500)
})

test('letterboxed picture area and stored position', () => {
  const box = contentRect(1000, 1000, 1920, 1080)
  for (const [k, v] of Object.entries({ x: 0, y: 218.75, w: 1000, h: 562.5 }))
    expect(box[k as keyof typeof box]).toBeCloseTo(v, 6)
  expect(contentRect(400, 300, 0, 0)).toEqual({ x: 0, y: 0, w: 400, h: 300 })
  const rect = contentRect(1600, 900, 1280, 720)
  const top = hudOffset(rect, 250, 120, 1, 0)
  expect(top).toEqual({ left: 1600 - 8 - 250, top: 8 })
  expect(hudFraction(rect, 250, 120, top.left, top.top)).toEqual({ x: 1, y: 0 })
  expect(hudFraction(rect, 250, 120, 99999, -50)).toEqual({ x: 1, y: 0 })
})

beforeEach(() => localStorage.clear())
test('HUD prefs persist per device and are sanitised', () => {
  expect(loadHud()).toEqual(DEFAULT_HUD)
  saveHud({ ...DEFAULT_HUD, x: 0.2, y: 0.7, scale: 1.4, opacity: 0.5, locked: true })
  expect(loadHud()).toEqual({ visible: true, x: 0.2, y: 0.7, scale: 1.4, opacity: 0.5, locked: true, details: false, tacho: true })
  expect(sanitizeHud({ scale: 99, opacity: -1, x: Number.NaN })).toMatchObject({ scale: 1.8, opacity: 0.3, x: 1 })
  localStorage.setItem('aceda-lap-hud-v1', '{broken')
  expect(loadHud()).toEqual(DEFAULT_HUD)
})
