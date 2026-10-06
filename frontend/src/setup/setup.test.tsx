import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import { parseRoute, routeHash } from '../routes'
import type { Session } from '../types'
import SetupAssistant from './SetupAssistant'
import { aiStateText, combos, defaultLaps, fmt, statusTag } from './logic'
import type { AiStatus, SetupAnalysis } from './types'

const ai = (over: Partial<AiStatus> = {}): AiStatus => ({
  provider: 'off',
  model: 'claude-opus-5-5',
  state: 'off',
  ready: false,
  key: { set: false, source: null, unreadable: false, protection: 'dpapi' },
  sdk: true,
  consent: false,
  disclosure_version: 1,
  providers: { anthropic: { label: 'Anthropic (Claude)', endpoint: 'api.anthropic.com', privacy: 'https://www.anthropic.com/legal/privacy' } },
  default_model: 'claude-opus-5-5',
  pc: true,
  ...over,
})

const meta = { source: 'ac' as const, driver: 'D', car: 'f1_car', track: 'monza', layout: '', session_type: 0, track_length: 5000, sector_count: 3, max_rpm: 0, compound: 'Soft', air_temp: 20, road_temp: 30 }
const lap = (id: string, n: number, over = {}) => ({
  id,
  session_id: 's1',
  number: n,
  duration_ms: 90000 + n,
  valid: true,
  complete: true,
  reasons: [],
  sectors_ms: [],
  events: [],
  tips: [],
  ...over,
})
const session: Session = { id: 's1', created_at: '2026-10-01T10:00:00Z', meta, lap_count: 3, best_ms: 90001, favorite: false, ended_at: null }

const kpi = {
  laps: [
    { id: 'l1', session_id: 's1', number: 1, duration_ms: 90001, used: false, reasons: ['Boxengasse (Out-/Inlap)'] },
    { id: 'l2', session_id: 's1', number: 2, duration_ms: 90002, used: true, reasons: [] },
  ],
  used: 1,
  excluded: 1,
  warnings: ['Nur 1 repräsentative Runde(n) - Kennwerte sind unsicher (empfohlen: mindestens 3)'],
  notes: [],
  availability: [
    { key: 'speed', label: 'Geschwindigkeit', ratio: 1, available: true },
    { key: 'wheel_slip', label: 'Radschlupf je Rad', ratio: 0, available: false },
  ],
  kpis: {},
  conditions: { fuel_start_l: [20, 20], air_c: null, road_c: null, compounds: ['Soft'], sessions: 1 },
}
const analysis = (over: Partial<SetupAnalysis> = {}): SetupAnalysis => ({
  id: 'a1',
  created_at: '2026-10-06T10:00:00Z',
  car: 'f1_car',
  track: 'monza',
  base_version_id: 'v1',
  lap_ids: ['l1', 'l2'],
  goal: 'race',
  feedback: { items: [], text: '', goal_text: '' },
  provider: 'rules',
  model: null,
  status: 'rules',
  kpis: { kpis: kpi as never, evidence: [{ item: 0, issue: 'understeer', phase: 'mid', label: 'Untersteuern · Kurvenmitte', verdict: 'nicht messbar', details: ['Radschlupf hier nicht auswertbar'] }] },
  sent: null,
  result: {
    summary: 'Regelbasierte Ersatzanalyse (keine KI): Test.',
    observations: [],
    changes: [
      {
        parameter: 'ARB_FRONT', label: 'ARB Front', tab: 'SUSPENSION', unit: '', old_value: 40000, new_value: 37500, old_raw: '8', new_raw: '7',
        ini: '[ARB_FRONT] VALUE=8 → VALUE=7', encoding: 'clicks', exportable: true, export_block: null, priority: 1, confidence: 'niedrig',
        confidence_limited: true, observation: 'Rückmeldung: Untersteuern', possible_cause: 'Vorderachse', test: 'Eine Stufe', reasoning: 'Regel',
        expected_effect: 'Mehr Grip vorne', tradeoffs: 'Mehr Wanken',
      },
      {
        parameter: 'FUEL', label: 'Fuel', tab: '', unit: '', old_value: 30, new_value: 35, old_raw: '30', new_raw: null,
        ini: '[FUEL] VALUE=30 (Zielwert unbestätigt)', encoding: null, exportable: false, export_block: 'Grenzen/Kodierung unbestätigt - nicht exportierbar',
        priority: 2, confidence: 'niedrig', confidence_limited: false, observation: 'o', possible_cause: 'c', test: 't', reasoning: 'r', expected_effect: 'e', tradeoffs: 'x',
      },
    ],
    dropped: [{ parameter: 'FANTASY', label: 'FANTASY', new_value: 3, reason: 'Parameter existiert für dieses Fahrzeug nicht (erfunden?)' }],
    driving_tips: [],
    uncertainties: [],
    validation_notes: [],
  },
  error: null,
  ...over,
})

let routes: Record<string, unknown>
const fetchMock = vi.fn()
beforeEach(() => {
  routes = {
    'GET /api/setup/status': {
      ai: ai(),
      ac: { install_dir: 'C:/AC', setups_dir: 'C:/setups', setups_found: true },
      goals: { qualifying: 'Qualifying', race: 'Rennen', consistency: 'Konstanz', custom: 'Individuell' },
      issues: { understeer: 'Untersteuern', oversteer: 'Übersteuern' },
      phases: { entry: 'Kurveneingang', mid: 'Kurvenmitte' },
    },
    'GET /api/sessions': [session],
    'GET /api/sessions/s1': { ...session, laps: [lap('l1', 1), lap('l2', 2), lap('l3', 3, { complete: false })] },
    'GET /api/setup/car': {
      car: 'f1_car', track: 'monza', spec: { kind: 'missing', packed: true, game_found: true }, observed_files: 2,
      counts: { unbestaetigt: 40 }, params: [], saved_setups: [{ folder: 'generic', name: 'last.ini', modified: 1790000000, bytes: 900 }],
    },
    'POST /api/setup/base': {
      version: { id: 'v1', name: 'last.ini' }, spec: { kind: 'missing' }, preserved: { sections: 50, internal: 3, duplicates: [] },
      params: [{ name: 'ARB_FRONT', label: 'ARB Front', tab: '', unit: '', status: 'unbestaetigt', reason: 'x', exportable: false, in_setup: true, raw: '8', stored: 8, encoding: null, options: [], observed: 2 }],
    },
    'GET /api/setup/versions': [],
    'GET /api/setup/assignments': [],
    'POST /api/setup/analyze': analysis(),
  }
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    const key = `${options?.method || 'GET'} ${url.split('?')[0]}`
    if (!(key in routes)) return new Response(JSON.stringify({ detail: 'not mocked ' + key }), { status: 404 })
    const body = routes[key]
    if (body instanceof Response) return body
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)
})

test('route #/analysis/setup opens the setup tab', () => {
  expect(parseRoute('#/analysis/setup')).toEqual({ page: 'analysis', analysis: 'setup' })
  expect(routeHash({ page: 'analysis', analysis: 'setup' })).toBe('#/analysis/setup')
  expect(parseRoute('#/analysis')).toEqual({ page: 'analysis' })
})

test('helpers: formatting, AI state texts, default laps, car/track groups', () => {
  expect(fmt(-2.5, '°')).toBe('-2,5 °')
  expect(fmt(null)).toBe('—')
  expect(statusTag('abgeleitet')).toBe('green-tag')
  expect(statusTag('mehrdeutig')).toBe('yellow-tag')
  expect(aiStateText(ai())).toMatch(/ausgeschaltet/)
  expect(aiStateText(ai({ state: 'no_key' }))).toMatch(/Kein API-Schlüssel/)
  expect(aiStateText(ai({ state: 'ready', provider: 'anthropic' }))).toBe('KI bereit: Anthropic (Claude) · claude-opus-5-5')
  const detailed = { ...session, laps: [lap('l1', 1), lap('l2', 2, { complete: false })] } as Session
  expect(defaultLaps([detailed])).toEqual(['l1'])
  expect([...combos([session]).values()][0]).toMatchObject({ car: 'f1_car', track: 'monza', demo: false })
})

test('packed car: honest notice, AI disabled without provider, rule-based analysis shows labelled result', async () => {
  render(<SetupAssistant />)
  expect(await screen.findByText(/Die Fahrzeugdaten sind gepackt/)).toBeInTheDocument()
  expect(screen.getByText(/KI ist ausgeschaltet/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Ausgangssetup'), { target: { value: 'generic/last.ini' } })
  expect(await screen.findByTestId('base-info')).toHaveTextContent('0 von 1 Parametern exportierbar')
  expect(screen.getByTestId('base-info')).toHaveTextContent('3 interne Einträge')
  expect(screen.getByTestId('analyze-ai')).toBeDisabled()
  const rules = screen.getByTestId('analyze-rules')
  await waitFor(() => expect(rules).toBeEnabled())
  fireEvent.click(rules)
  const result = await screen.findByTestId('setup-result')
  expect(within(result).getByText('REGELBASIERT · KEINE KI')).toBeInTheDocument()
  expect(within(result).getByText(/Runde 1: Boxengasse/)).toBeInTheDocument()
  expect(within(result).getByText('nicht messbar')).toBeInTheDocument()
  // Exportable card is preselected; the unconfirmed one cannot be selected.
  expect(within(screen.getByTestId('rec-ARB_FRONT')).getByRole('checkbox')).toBeChecked()
  expect(within(screen.getByTestId('rec-FUEL')).queryByRole('checkbox')).toBeNull()
  expect(within(screen.getByTestId('rec-FUEL')).getByText(/Nicht exportierbar/)).toBeInTheDocument()
  expect(screen.getByTestId('setup-before-after')).toHaveTextContent('VALUE=8 → 7')
  expect(screen.getByTestId('setup-dropped')).toHaveTextContent('erfunden')
  const body = JSON.parse(fetchMock.mock.calls.find((c) => c[0] === '/api/setup/analyze')![1].body)
  expect(body).toMatchObject({ mode: 'rules', base_version_id: 'v1', lap_ids: ['l1', 'l2'], goal: 'race' })
})

test('AI error is shown honestly with a rule-based fallback offer', async () => {
  routes['GET /api/setup/status'] = { ...(routes['GET /api/setup/status'] as object), ai: ai({ provider: 'anthropic', state: 'ready', ready: true, consent: true, key: { set: true, source: 'stored', unreadable: false, protection: 'dpapi' } }) }
  routes['POST /api/setup/analyze'] = analysis({ status: 'error', provider: 'anthropic', error: 'Keine Verbindung zum KI-Anbieter (Internet/Firewall?)', result: { error_code: 'connection' } as never })
  render(<SetupAssistant />)
  await screen.findByRole('option', { name: /last\.ini/ })
  fireEvent.change(screen.getByLabelText('Ausgangssetup'), { target: { value: 'generic/last.ini' } })
  await screen.findByTestId('base-info')
  const button = screen.getByTestId('analyze-ai')
  await waitFor(() => expect(button).toBeEnabled())
  fireEvent.click(button)
  const alert = await screen.findByTestId('setup-ai-error')
  expect(alert).toHaveTextContent('Keine Verbindung zum KI-Anbieter')
  expect(alert).toHaveTextContent('Es wurde keine Empfehlung erzeugt')
  expect(within(alert).getByRole('button', { name: 'Regelbasierte Ersatzanalyse' })).toBeInTheDocument()
  expect(screen.queryByTestId('rec-ARB_FRONT')).toBeNull()
})

test('first use shows the data disclosure before AI analysis is possible', async () => {
  routes['GET /api/setup/status'] = { ...(routes['GET /api/setup/status'] as object), ai: ai({ provider: 'anthropic', state: 'consent', key: { set: true, source: 'stored', unreadable: false, protection: 'dpapi' } }) }
  render(<SetupAssistant />)
  const disclosure = await screen.findByTestId('ai-disclosure')
  expect(disclosure).toHaveTextContent('api.anthropic.com')
  expect(disclosure).toHaveTextContent('Rohtelemetrie (einzelne Messpunkte), Videos und Ton')
  expect(screen.getByTestId('analyze-ai')).toBeDisabled()
})
