import { expect, test } from 'vitest'
import { backendStatus, gameStatus, statusHint } from './status'

test('a working browser-backend link is never a game connection', () => {
  expect(backendStatus('connected').de).toBe('BACKEND VERBUNDEN')
  for (const status of ['waiting_game', 'waiting_session', 'not_initialized', 'stale', 'demo'])
    expect(gameStatus({ status }, 'connected').game).toBe(false)
  expect(gameStatus({ status: 'live' }, 'connected').game).toBe(true)
})

test('every backend state has a distinct label', () => {
  const expected: Record<string, string> = {
    demo: 'DEMO AKTIV',
    waiting_game: 'WARTE AUF SPIEL',
    waiting_session: 'SPIEL GEFUNDEN, WARTE AUF FAHRSESSION',
    not_initialized: 'SPEICHER GEFUNDEN, NICHT INITIALISIERT',
    live: 'TELEMETRIE VERBUNDEN',
    paused: 'SPIEL PAUSIERT',
    replay: 'REPLAY',
    stale: 'DATEN VERALTET',
    access_denied: 'ZUGRIFF VERWEIGERT',
    decode_error: 'DEKODIERUNGSFEHLER',
    capture_failed: 'ERFASSUNG AUSGEFALLEN',
  }
  for (const [status, label] of Object.entries(expected))
    expect(gameStatus({ status }, 'connected').de).toBe(label)
  expect(new Set(Object.values(expected)).size).toBe(Object.keys(expected).length)
})

test('without backend the last game state is not shown', () => {
  const view = gameStatus({ status: 'live' }, 'disconnected')
  expect(view.game).toBe(false)
  expect(view.de).toBe('SPIELSTATUS UNBEKANNT')
  expect(backendStatus('disconnected').tone).toBe('red')
})

test('hints only where action is needed', () => {
  expect(statusHint('live', true)).toBeNull()
  expect(statusHint('demo', true)).toBeNull()
  expect(statusHint('waiting_game', true)).toContain('Launcher')
})
