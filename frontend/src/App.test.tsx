import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import type { Compare, Frame, LapSummary, Sample, Session, Settings } from './types'

// The chart canvas is not testable in jsdom; everything else of the module stays real.
vi.mock('./TelemetryChart', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./TelemetryChart')>()),
  default: (p: { analysis: boolean }) => <div data-testid={p.analysis ? 'chart-distance' : 'chart-live'} />,
}))

const settings: Settings = {
  language: 'de',
  units: 'metric',
  capture_hz: 60,
  broadcast_hz: 20,
  source: 'ac',
  auto_save: true,
  storage_mb: 2048,
  lan: false,
  port: 8765,
  token_set: false,
  temp_cold: 70,
  temp_hot: 105,
  pressure_low: 20,
  pressure_high: 35,
  steering_lock_deg: null,
  wheelbase_m: null,
  steering_ratio: null,
  steering_yaw_sign: null,
  brake_threshold: 0.08,
  full_throttle: 0.97,
  lock_ratio: 0.65,
  spin_ratio: 1.2,
  slip_angle_deg: 8,
  video_mode: 'none',
  video_url: '',
  video_offset_s: 0,
  visible_widgets: [],
  widget_order: [],
  graph_colors: {},
  reference_lap_id: null,
  start_with_windows: false,
  open_browser: true,
  ai_provider: 'off',
  upload_telemetry: false,
}
class FakeSocket {
  static latest: FakeSocket
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    FakeSocket.latest = this
    setTimeout(() => this.onopen?.(), 0)
  }
  close() {}
  message(f: Frame) {
    this.onmessage?.({ data: JSON.stringify(f) })
  }
  drop() {
    this.onclose?.()
  }
}
const meta = (over: Partial<Frame['meta'] & object> = {}) => ({
  source: 'ac' as const,
  driver: 'Measured Driver',
  car: 'Formula',
  track: 'Test Track',
  layout: 'GP',
  session_type: 0,
  track_length: 4200,
  sector_count: 3,
  max_rpm: 10000,
  compound: 'Medium (M)',
  air_temp: 23,
  road_temp: 31,
  ...over,
})
function makeFrame(v: number, id = 1): Frame {
  const sample: Sample = {
    source: 'ac',
    packet_id: id,
    captured_at: 100 + id,
    status: 2,
    completed_laps: 0,
    lap_ms: 12800,
    last_lap_ms: 0,
    best_lap_ms: 0,
    sector_index: 0,
    last_sector_ms: 0,
    position: 1,
    session_left_ms: 600000,
    lap_pos: 0.1,
    coords: [0, 0, 0],
    in_pit: false,
    tyres_out: 0,
    penalty_s: 0,
    channels: { speed: v, gear: 4, rpm: 8470, gas: 0.6, brake: 0, steer: 0.1, engine_temp: null },
    tyres: [],
  }
  return {
    type: 'telemetry',
    status: 'live',
    source: 'ac',
    connected: true,
    game_connected: true,
    error: null,
    meta: meta(),
    sample,
    session_id: 'session',
    reference_id: null,
    reference_ms: null,
    delta_s: null,
    ghost_pos: null,
    fuel_laps_est: null,
    current_sector_ms: 12000,
    statistics: {},
    tips: [],
    recording: true,
    storage: {},
    tyre_warnings: [],
    performance: { samples: 10, dropped: 0, queue_depth: 0, uptime_s: 1 },
  }
}

/* Stored data for Analysis / Sessions (the game is NOT needed for these). */
const lap = (id: string, number: number, ms: number, over: Partial<LapSummary> = {}): LapSummary => ({
  id,
  session_id: 's1',
  number,
  duration_ms: ms,
  valid: true,
  complete: true,
  reasons: [],
  sectors_ms: [30000, 31000, ms - 61000],
  sector_positions: [0.33, 0.66],
  events: [],
  tips: [],
  ...over,
})
const sessionA: Session = {
  id: 's1',
  created_at: '2026-10-03T10:00:00+00:00',
  meta: meta(),
  lap_count: 3,
  best_ms: 91000,
  favorite: false,
  ended_at: null,
  laps: [lap('a1', 1, 92000), lap('a2', 2, 91000), lap('a3', 3, 93000)],
}
const sessionOther: Session = {
  id: 's2',
  created_at: '2026-10-02T10:00:00+00:00',
  meta: meta({ car: 'Other car' }),
  lap_count: 1,
  best_ms: 95000,
  favorite: false,
  ended_at: null,
  laps: [lap('b1', 1, 95000, { session_id: 's2' })],
}
const compare: Compare = {
  distance: [0, 2100, 4200],
  delta: [0, 0.4, 1],
  line_difference_m: [0, 1, 0],
  lap: { id: 'a3', time: [0, 46, 93], channels: { speed: [100, 200, 150], gas: [1, 0.5, 1] } },
  reference: { id: 'a2', time: [0, 45, 91], channels: { speed: [110, 205, 160], gas: [1, 0.6, 1] } },
  map: { x: [0, 100, 0], z: [0, 50, 0] },
  reference_map: { x: [0, 100, 0], z: [0, 50, 0] },
  summary: { lap_s: 93, reference_s: 91, delta_s: 2 },
  events: [],
  tips: [],
  corners: [],
}

const fetchMock = vi.fn()
const ok = (body: unknown) => ({ ok: true, json: async () => body })
function defaultFetch(path: string, options?: RequestInit) {
  if (path === '/api/settings')
    return ok(
      options?.method === 'PATCH'
        ? { settings: { ...settings, ...JSON.parse(String(options.body)) }, restart_required: false }
        : settings,
    )
  if (path === '/api/map') return ok({ points: [], events: [], sector_positions: [], complete: false })
  if (path.startsWith('/api/sessions?') || path === '/api/sessions') return ok([sessionA, sessionOther])
  if (path === '/api/sessions/s1') return ok(sessionA)
  if (path === '/api/sessions/s2') return ok(sessionOther)
  if (path.startsWith('/api/compare')) return ok([compare])
  return ok({})
}
beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket)
  sessionStorage.clear()
  localStorage.clear()
  history.replaceState(null, '', '#/live')
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (path: string, options?: RequestInit) => defaultFetch(path, options))
  vi.stubGlobal('fetch', fetchMock)
})

test('navigation has exactly the five areas', async () => {
  render(<App />)
  const nav = screen.getByRole('navigation', { name: 'Hauptnavigation' })
  expect(within(nav).getAllByRole('button').map((b) => b.textContent)).toEqual([
    'Live-Dashboard',
    'Analyse',
    'Onboard',
    'Sessions',
    'Einstellungen',
  ])
  expect(within(nav).queryByText('Tablet')).toBeNull()
  expect(within(nav).queryByText('Engineering')).toBeNull()
  expect(within(nav).queryByText('Diagnose')).toBeNull()
})

test('old links lead to the new areas', async () => {
  for (const [hash, expected] of [
    ['#/engineering', '#/live'],
    ['#/live-mode', '#/live'],
    ['#/tablet', '#/live'],
    ['#/diagnostics', '#/settings/advanced'],
  ]) {
    history.replaceState(null, '', hash)
    const { unmount } = render(<App />)
    expect(location.hash).toBe(expected)
    unmount()
  }
  history.replaceState(null, '', '#/diagnostics')
  render(<App />)
  expect(await screen.findByRole('tab', { name: 'Erweitert / Diagnose' })).toHaveAttribute('aria-selected', 'true')
})

test('live dashboard updates without reload and has no coach, video or comparison tools', async () => {
  render(<App />)
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  act(() => FakeSocket.latest.message(makeFrame(181)))
  const dash = screen.getByTestId('live-dashboard')
  expect(within(dash).getByText('181')).toBeInTheDocument()
  expect(within(dash).getByText('Measured Driver')).toBeInTheDocument()
  act(() => FakeSocket.latest.message(makeFrame(207, 2)))
  expect(within(dash).getByText('207')).toBeInTheDocument()
  expect(within(dash).queryByText('181')).toBeNull()
  expect(within(dash).queryByText(/Engineering-Coach/)).toBeNull()
  expect(within(dash).queryByText('Referenzrunde')).toBeNull()
  expect(dash.querySelector('video')).toBeNull()
  expect(screen.getByTestId('chart-live')).toBeInTheDocument()
  fireEvent.click(within(dash).getByRole('button', { name: 'Onboard öffnen' }))
  expect(location.hash).toBe('#/onboard')
  expect(screen.getByTestId('onboard-page')).toBeInTheDocument()
})

test('demo switch changes the backend data source', async () => {
  render(<App />)
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/settings', expect.anything()))
  fireEvent.click(screen.getByRole('button', { name: 'Demo starten' }))
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, opts]) => url === '/api/settings' && opts?.method === 'PATCH' && JSON.parse(opts.body).source === 'demo',
      ),
    ).toBe(true),
  )
})

test('four tyre positions appear with unavailable values', async () => {
  render(<App />)
  for (const wheel of ['FL', 'FR', 'RL', 'RR']) expect(screen.getByTestId('tyre-' + wheel)).toHaveTextContent('nicht verfügbar')
})

test('backend, game and telemetry are separate states', async () => {
  render(<App />)
  await waitFor(() => expect(screen.getByTestId('backend-status')).toHaveTextContent('BACKEND VERBUNDEN'))
  act(() =>
    FakeSocket.latest.message({ ...makeFrame(0), status: 'waiting_game', game_connected: false, connected: false, sample: null, meta: null, session_id: null }),
  )
  expect(screen.getByTestId('game-status')).toHaveTextContent('WARTE AUF SPIEL')
  expect(screen.getByTestId('telemetry-status')).toHaveTextContent('KEINE TELEMETRIE')
  act(() => FakeSocket.latest.message(makeFrame(150)))
  expect(screen.getByTestId('telemetry-status')).toHaveTextContent('TELEMETRIE LIVE')
  expect(screen.getByTestId('game-status')).toHaveTextContent('TELEMETRIE VERBUNDEN')
})

test('values after a backend disconnect are marked as last values, not live', async () => {
  render(<App />)
  await waitFor(() => expect(screen.getByTestId('backend-status')).toHaveTextContent('BACKEND VERBUNDEN'))
  act(() => FakeSocket.latest.message(makeFrame(222)))
  act(() => FakeSocket.latest.drop())
  expect(screen.getByTestId('backend-status')).toHaveTextContent('BACKEND GETRENNT')
  expect(screen.getByTestId('telemetry-status')).toHaveTextContent(/LETZTER STAND \d\d:\d\d:\d\d/)
  expect(screen.getByTestId('stale-notice')).toHaveTextContent('keine aktuellen Live-Daten')
  expect(screen.getByTestId('live-dashboard')).toHaveClass('frozen')
})

test('switching from demo to AC removes demo values from the live view', async () => {
  render(<App />)
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  const demo = makeFrame(199)
  act(() =>
    FakeSocket.latest.message({
      ...demo,
      status: 'demo',
      source: 'demo',
      game_connected: false,
      meta: meta({ source: 'demo', driver: 'Demo Driver' }),
      sample: { ...demo.sample!, source: 'demo' },
      session_id: 'demo-session',
    }),
  )
  expect(screen.getByText('199')).toBeInTheDocument()
  expect(screen.getByTestId('demo-tag')).toHaveTextContent('DEMO')
  act(() =>
    FakeSocket.latest.message({ ...makeFrame(0), status: 'waiting_game', source: 'ac', connected: false, game_connected: false, sample: null, meta: null, session_id: null }),
  )
  expect(screen.queryByText('199')).toBeNull()
  expect(screen.queryByText('Demo Driver')).toBeNull()
  expect(screen.queryByTestId('demo-tag')).toBeNull()
})

test('analysis compares stored laps over distance and only offers compatible laps', async () => {
  history.replaceState(null, '', '#/analysis')
  render(<App />)
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/compare'))).toBe(true))
  // Defaults: fastest valid lap = reference, newest other lap = comparison.
  expect(fetchMock.mock.calls.some(([u]) => u === '/api/compare?lap_ids=a3&reference=a2')).toBe(true)
  expect(await screen.findByTestId('chart-distance')).toBeInTheDocument()
  const summary = screen.getByTestId('analysis-summary')
  expect(summary).toHaveTextContent('Vergleich: Runde 3')
  expect(summary).toHaveTextContent('Referenz: Runde 2')
  expect(summary).toHaveTextContent('Theoretische Bestzeit')
  // Laps of another car are not offered in the lap pickers.
  const ref = screen.getByLabelText('Referenzrunde') as HTMLSelectElement
  expect([...ref.options].map((o) => o.textContent).join(' ')).not.toContain('1:35.000')
  // No live tacho / pedals in the analysis view.
  expect(screen.queryByTestId('driving-strip')).toBeNull()
  expect(screen.getByTestId('sector-compare')).toBeInTheDocument()
  expect(screen.getByTestId('coach-empty')).toBeInTheDocument()
})

test('analysis without stored laps shows a compact hint, not an empty coach card', async () => {
  fetchMock.mockImplementation(async (path: string, options?: RequestInit) =>
    path.startsWith('/api/sessions') ? ok([]) : defaultFetch(path, options),
  )
  history.replaceState(null, '', '#/analysis')
  render(<App />)
  expect(await screen.findByTestId('analysis-hint')).toHaveTextContent('Noch keine gespeicherten Runden')
  expect(screen.queryByTestId('coach')).toBeNull()
})

test('sessions hand the selected laps to analysis', async () => {
  history.replaceState(null, '', '#/sessions')
  render(<App />)
  fireEvent.click((await screen.findAllByRole('checkbox'))[0])
  fireEvent.click(await screen.findByLabelText('Runde 1 auswählen'))
  fireEvent.click(screen.getByLabelText('Runde 3 auswählen'))
  fireEvent.click(screen.getByRole('button', { name: 'In Analyse vergleichen' }))
  expect(location.hash).toBe('#/analysis')
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/api/compare?lap_ids=a3&reference=a1')).toBe(true))
  expect(screen.queryByText('Rundenvergleich')).toBeNull()
})

test('incompatible laps cannot be sent to analysis', async () => {
  history.replaceState(null, '', '#/sessions')
  render(<App />)
  const boxes = await screen.findAllByRole('checkbox')
  // open both sessions: same track, but the second one is another car
  fireEvent.click(boxes[0])
  fireEvent.click(boxes[1])
  await waitFor(() => expect(screen.getAllByLabelText('Runde 1 auswählen')).toHaveLength(2))
  const lapBoxes = screen.getAllByLabelText('Runde 1 auswählen')
  fireEvent.click(lapBoxes[0])
  fireEvent.click(lapBoxes[1])
  expect(screen.getByRole('button', { name: 'In Analyse vergleichen' })).toBeDisabled()
  expect(screen.getByTestId('compare-blocked')).toHaveTextContent('Nicht vergleichbar')
})

test('understandable message instead of "TypeError: Failed to fetch"', async () => {
  fetchMock.mockImplementation(async (path: string) => {
    if (path === '/api/settings') throw new TypeError('Failed to fetch')
    return ok({})
  })
  render(<App />)
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Keine Verbindung zum PC-Programm')
  expect(alert).not.toHaveTextContent('TypeError')
})

test('diagnostics live under settings', async () => {
  const fallback = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (path: string, options?: RequestInit) =>
    path.startsWith('/api/diagnostics')
      ? ok({
          generated_at: '2026-10-03T10:00:00+00:00',
          status: 'waiting_game',
          system: { os: 'Windows-11', windows: true, python: '3.12.10', python_bits: 64, machine: 'AMD64', frozen: false },
          source: { selected: 'ac', active: 'ac' },
          capture_thread: { alive: true, restarts: 0, errors: 0, last_error: null, last_error_at: null, exit_reason: null },
          game: null,
          engine: { analysis_thread_alive: true, samples: 0, dropped: 0, queue_depth: 0, last_sample_age_s: null, last_sample_source: null, analysis_error: null },
          websocket: { clients: 1, last_send_age_s: 0.05 },
          hints: [],
        })
      : fallback(path, options),
  )
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: 'Einstellungen' }))
  fireEvent.click(await screen.findByRole('tab', { name: 'Erweitert / Diagnose' }))
  expect(location.hash).toBe('#/settings/advanced')
  await waitFor(() => expect(screen.getByTestId('diag-game')).toHaveTextContent('WARTE AUF SPIEL'))
  expect(screen.getByText('Erfassungsfrequenz (Hz)')).toBeInTheDocument()
})

test('code screen stops polling so the phone is not flooded with code-less requests', async () => {
  class RejectedSocket {
    onopen: (() => void) | null = null
    onmessage: (() => void) | null = null
    onerror: (() => void) | null = null
    onclose: (() => void) | null = null
    constructor() {
      setTimeout(() => this.onclose?.(), 0)
    }
    close() {}
  }
  vi.stubGlobal('WebSocket', RejectedSocket)
  fetchMock.mockImplementation(async () => ({ ok: false, status: 401, json: async () => ({ detail: 'Access code required' }) }))
  render(<App />)
  await waitFor(() => expect(screen.getByText('Zugriffscode eingeben')).toBeInTheDocument())
  const before = fetchMock.mock.calls.filter(([u]) => u === '/api/map').length
  await new Promise((r) => setTimeout(r, 2300))
  expect(fetchMock.mock.calls.filter(([u]) => u === '/api/map').length).toBe(before)
  const input = screen.getByLabelText('Zugriffscode')
  fireEvent.change(input, { target: { value: '1234 56' } })
  expect(screen.getByRole('button', { name: 'Verbinden' })).toBeDisabled()
  fireEvent.change(input, { target: { value: '1234 5678' } })
  expect(screen.getByRole('button', { name: 'Verbinden' })).toBeEnabled()
}, 10000)

test('an open tab reloads once when the PC app was updated', async () => {
  const reload = vi.fn()
  vi.stubGlobal('location', { ...window.location, hash: '#/live', reload, hostname: 'localhost', host: 'localhost', protocol: 'http:' })
  render(<App />)
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  act(() => FakeSocket.latest.message({ ...makeFrame(100), build: 'aaaa' }))
  act(() => FakeSocket.latest.message({ ...makeFrame(101, 2), build: 'aaaa' }))
  expect(reload).not.toHaveBeenCalled()
  act(() => FakeSocket.latest.message({ ...makeFrame(102, 3), build: 'bbbb' }))
  expect(reload).toHaveBeenCalledTimes(1)
  vi.unstubAllGlobals()
})
