import type { Frame } from './types'

export type Tone = 'green' | 'orange' | 'cyan' | 'yellow' | 'red' | 'muted'
export interface StatusView {
  key: string
  de: string
  en: string
  tone: Tone
  /** True only for real Assetto Corsa telemetry, never for demo data. */
  game: boolean
}

// Backend game/data-source states (engine.game_status()).
const STATES: Record<string, Omit<StatusView, 'key'>> = {
  demo: { de: 'DEMO AKTIV', en: 'DEMO ACTIVE', tone: 'orange', game: false },
  live: { de: 'TELEMETRIE VERBUNDEN', en: 'TELEMETRY CONNECTED', tone: 'green', game: true },
  paused: { de: 'SPIEL PAUSIERT', en: 'GAME PAUSED', tone: 'yellow', game: true },
  replay: { de: 'REPLAY', en: 'REPLAY', tone: 'cyan', game: true },
  stale: { de: 'DATEN VERALTET', en: 'DATA STALE', tone: 'yellow', game: false },
  waiting_game: { de: 'WARTE AUF SPIEL', en: 'WAITING FOR GAME', tone: 'muted', game: false },
  waiting_session: {
    de: 'SPIEL GEFUNDEN, WARTE AUF FAHRSESSION',
    en: 'GAME FOUND, WAITING FOR SESSION',
    tone: 'muted',
    game: false,
  },
  not_initialized: {
    de: 'SPEICHER GEFUNDEN, NICHT INITIALISIERT',
    en: 'MEMORY FOUND, NOT INITIALISED',
    tone: 'muted',
    game: false,
  },
  access_denied: { de: 'ZUGRIFF VERWEIGERT', en: 'ACCESS DENIED', tone: 'red', game: false },
  decode_error: { de: 'DEKODIERUNGSFEHLER', en: 'DECODING ERROR', tone: 'red', game: false },
  memory_error: { de: 'SPEICHERFEHLER', en: 'MEMORY ERROR', tone: 'red', game: false },
  capture_failed: {
    de: 'ERFASSUNG AUSGEFALLEN',
    en: 'CAPTURE STOPPED',
    tone: 'red',
    game: false,
  },
  unsupported_platform: {
    de: 'NUR UNTER WINDOWS',
    en: 'WINDOWS ONLY',
    tone: 'red',
    game: false,
  },
  starting: { de: 'STARTET …', en: 'STARTING …', tone: 'muted', game: false },
}

/** Game/data-source state. Independent of the browser↔backend WebSocket. */
export function gameStatus(frame: Pick<Frame, 'status'>, socket: string): StatusView {
  if (socket !== 'connected')
    // Without the backend nothing is known about the game; never show old state.
    return { key: 'unknown', de: 'SPIELSTATUS UNBEKANNT', en: 'GAME STATUS UNKNOWN', tone: 'muted', game: false }
  const known = STATES[frame.status]
  return known
    ? { key: frame.status, ...known }
    : { key: frame.status, de: frame.status.toUpperCase(), en: frame.status.toUpperCase(), tone: 'muted', game: false }
}

/** Browser ↔ backend link only. Never implies a game connection. */
export function backendStatus(socket: string): StatusView {
  return socket === 'connected'
    ? { key: 'connected', de: 'BACKEND VERBUNDEN', en: 'BACKEND CONNECTED', tone: 'green', game: false }
    : socket === 'connecting'
      ? { key: 'connecting', de: 'BACKEND VERBINDET …', en: 'BACKEND CONNECTING …', tone: 'yellow', game: false }
      : { key: 'disconnected', de: 'BACKEND GETRENNT', en: 'BACKEND DISCONNECTED', tone: 'red', game: false }
}

/** Short instruction for the dashboard notice; null when nothing is needed. */
export function statusHint(status: string, de: boolean): string | null {
  const t = (a: string, b: string) => (de ? a : b)
  switch (status) {
    case 'waiting_game':
      return t(
        'Assetto Corsa (Steam oder Content Manager) starten und auf die Strecke fahren. Ein laufender Launcher allein reicht nicht.',
        'Start Assetto Corsa (Steam or Content Manager) and go on track. A running launcher alone is not enough.',
      )
    case 'waiting_session':
      return t('acs.exe läuft – warte, bis du im Auto sitzt.', 'acs.exe is running – waiting until you are in the car.')
    case 'not_initialized':
      return t('Speicher gefunden, die Session lädt noch.', 'Memory found, the session is still loading.')
    case 'stale':
      return t(
        'Das Spiel liefert keine neuen Daten. Spiel minimiert, hängt oder beendet?',
        'The game delivers no new data. Minimised, frozen or closed?',
      )
    case 'access_denied':
    case 'decode_error':
    case 'memory_error':
    case 'capture_failed':
    case 'unsupported_platform':
      return t('Details und Lösung in der Diagnose.', 'See Diagnostics for details and a fix.')
    default:
      return null
  }
}
