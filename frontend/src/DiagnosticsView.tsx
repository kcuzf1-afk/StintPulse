import { useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { api } from './api'
import type { Diagnostics } from './types'
import { backendStatus, gameStatus } from './status'
import { errorLog, friendlyError, logError, onErrorLog } from './errors'
import { formatClock } from './connection'

const num = (v: number | null | undefined, digits: number, unit: string) =>
  typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) + unit : '—'
const yesNo = (v: boolean | null | undefined, de: boolean) =>
  v === null || v === undefined ? '—' : v ? (de ? 'ja' : 'yes') : de ? 'nein' : 'no'

export default function DiagnosticsView({
  de,
  socketState,
  onRecheck,
}: {
  de: boolean
  socketState: string
  onRecheck: () => Promise<Diagnostics | null>
}) {
  const tr = (a: string, b: string) => (de ? a : b)
  const [data, setData] = useState<Diagnostics | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [perf, setPerf] = useState<{ cpu_percent: number; ram_mb: number; websocket_bytes: number; database_mb: number } | null>(null),
    [log, setLog] = useState(errorLog())
  useEffect(() => onErrorLog(() => setLog(errorLog())), [])
  useEffect(() => {
    let active = true
    const load = () => {
      api<Diagnostics>('/diagnostics')
        .then((d) => {
          if (active) {
            setData(d)
            setError('')
          }
        })
        .catch((e) => {
          if (!active) return
          logError(e, 'diagnostics')
          setError(friendlyError(e, de))
        })
      api<{ cpu_percent: number; ram_mb: number; websocket_bytes: number; database_mb: number }>('/performance')
        .then((p) => active && setPerf(p))
        .catch(() => {})
    }
    load()
    const timer = setInterval(load, 1000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])
  async function recheck() {
    setBusy(true)
    try {
      const d = await onRecheck()
      if (d) setData(d)
    } finally {
      setBusy(false)
    }
  }
  const game = data?.game
  const state = data ? gameStatus({ status: data.status }, 'connected') : null
  const backend = backendStatus(socketState)
  const processText = (s?: string) =>
    s === 'found'
      ? tr('gefunden', 'found')
      : s === 'not_found'
        ? tr('nicht gefunden', 'not found')
        : s === 'unavailable'
          ? tr('Prüfung nicht möglich', 'check not possible')
          : '—'
  return (
    <section className="panel diagnostics">
      <div className="panel-title">
        <h3>{tr('Diagnose', 'Diagnostics')}</h3>
        <button className="primary" onClick={recheck} disabled={busy}>
          <RotateCcw size={13} />
          {busy ? tr('Prüfe …', 'Checking …') : tr('Verbindung erneut prüfen', 'Re-check connection')}
        </button>
      </div>
      {error && <div className="notice danger">{error}</div>}
      <div className="diag-status">
        <span className={'tag ' + backend.tone + '-tag'} data-testid="diag-backend">
          {tr('Browser ↔ Backend: ', 'Browser ↔ backend: ') + (de ? backend.de : backend.en)}
        </span>
        {state && (
          <span className={'tag ' + state.tone + '-tag'} data-testid="diag-game">
            {tr('Spiel: ', 'Game: ') + (de ? state.de : state.en)}
          </span>
        )}
      </div>
      {data?.hints.map((h, i) => (
        <div key={i} className={'notice' + (h.level === 'error' ? ' danger' : '')}>
          {de ? h.de : h.en}
        </div>
      ))}
      {data && (
        <div className="diag-grid">
          <table>
            <caption>{tr('System & Erfassung', 'System & capture')}</caption>
            <tbody>
              <tr>
                <th>{tr('Betriebssystem', 'Operating system')}</th>
                <td>{data.system.os}</td>
              </tr>
              <tr>
                <th>Python</th>
                <td>
                  {data.system.python} · {data.system.python_bits} bit · {data.system.machine}
                  {data.system.frozen ? ' · EXE' : ''}
                </td>
              </tr>
              <tr>
                <th>{tr('Datenquelle', 'Data source')}</th>
                <td>{data.source.active === 'demo' ? 'DEMO' : 'Assetto Corsa (Shared Memory)'}</td>
              </tr>
              <tr>
                <th>{tr('Erfassungs-Thread', 'Capture thread')}</th>
                <td className={data.capture_thread.alive ? 'green-text' : 'red-text'}>
                  {data.capture_thread.alive ? tr('aktiv', 'running') : tr('beendet', 'stopped')}
                  {data.capture_thread.exit_reason ? ' – ' + data.capture_thread.exit_reason : ''}
                </td>
              </tr>
              <tr>
                <th>{tr('Erfassungsfehler', 'Capture errors')}</th>
                <td>
                  {data.capture_thread.errors}
                  {data.capture_thread.last_error ? ' – ' + data.capture_thread.last_error : ''}
                </td>
              </tr>
              <tr>
                <th>{tr('Analyse-Thread', 'Analysis thread')}</th>
                <td>{data.engine.analysis_thread_alive ? tr('aktiv', 'running') : tr('beendet', 'stopped')}</td>
              </tr>
              <tr>
                <th>WebSocket</th>
                <td>
                  {(de ? backend.de : backend.en) +
                    ' · ' +
                    data.websocket.clients +
                    tr(' Client(s)', ' client(s)')}
                </td>
              </tr>
              <tr>
                <th>{tr('Samples verarbeitet', 'Samples processed')}</th>
                <td>
                  {data.engine.samples} · {tr('verworfen', 'dropped')} {data.engine.dropped}
                </td>
              </tr>
            </tbody>
          </table>
          {game && (
            <table>
              <caption>Assetto Corsa</caption>
              <tbody>
                <tr>
                  <th>{tr('Spielprozess (acs.exe)', 'Game process (acs.exe)')}</th>
                  <td>
                    {processText(game.process.status)}
                    {game.process.launcher.length
                      ? ' · Launcher: ' + game.process.launcher.join(', ')
                      : ''}
                    {game.process.error ? ' · ' + game.process.error : ''}
                  </td>
                </tr>
                <tr>
                  <th>{tr('Spielstatus', 'Game status')}</th>
                  <td>{game.game_status.name ?? '—'}</td>
                </tr>
                <tr>
                  <th>Packet-ID Physics / Graphics</th>
                  <td>
                    {game.physics_packet_id ?? '—'} / {game.graphics_packet_id ?? '—'}
                  </td>
                </tr>
                <tr>
                  <th>{tr('Letzte Aktualisierung', 'Last update')}</th>
                  <td>
                    {game.last_update_age_s === null
                      ? '—'
                      : tr('vor ', '') + game.last_update_age_s.toFixed(1) + ' s' + tr('', ' ago')}
                  </td>
                </tr>
                <tr>
                  <th>{tr('Empfangsrate', 'Receive rate')}</th>
                  <td>{game.observed_hz.toFixed(1)} Hz</td>
                </tr>
                <tr>
                  <th>{tr('Letzte Dekodierung', 'Last decode')}</th>
                  <td>{game.last_decode_at ? new Date(game.last_decode_at).toLocaleTimeString() : '—'}</td>
                </tr>
                <tr>
                  <th>{tr('Letzte Fehlermeldung', 'Last error')}</th>
                  <td className={game.last_decode_error || game.state.endsWith('error') || game.state === 'access_denied' ? 'red-text' : ''}>
                    {game.last_decode_error || game.message || '—'}
                    {game.decode_errors ? ` (${game.decode_errors}×)` : ''}
                  </td>
                </tr>
                {!!game.unknown_fields.length && (
                  <tr>
                    <th>{tr('Unbekannte Werte', 'Unknown values')}</th>
                    <td>{game.unknown_fields.join('; ')}</td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
          {game && (
            <table className="diag-pages">
              <caption>{tr('Speicherbereiche', 'Shared memory pages')}</caption>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>{tr('vorhanden', 'present')}</th>
                  <th>{tr('lesbar', 'readable')}</th>
                  <th>{tr('initialisiert', 'initialised')}</th>
                  <th>{tr('nur lesend', 'read-only')}</th>
                  <th>{tr('Bytes erwartet / gelesen', 'Bytes expected / read')}</th>
                  <th>{tr('Fehler', 'Error')}</th>
                </tr>
              </thead>
              <tbody>
                {game.pages.map((p) => (
                  <tr key={p.name}>
                    <td className="mono">{p.name}</td>
                    <td>{yesNo(p.present, de)}</td>
                    <td>{yesNo(p.readable, de)}</td>
                    <td>{yesNo(p.initialized, de)}</td>
                    <td>{yesNo(p.read_only, de)}</td>
                    <td>
                      {p.expected_bytes} / {p.read_bytes}
                    </td>
                    <td>{p.error ? `${p.error} (${p.error_code})` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      <p className="small subtle">
        {tr(
          'Lokale Diagnose: enthält keine Zugriffstoken und keine Speicherinhalte. Ein bestandener Demo-Test ist kein Nachweis einer Spielverbindung.',
          'Local diagnostics: contains no access tokens and no memory contents. A passing demo is no proof of a game connection.',
        )}
      </p>
      <details className="diag-extra">
        <summary>{tr('Leistung und technische Fehlerdetails', 'Performance and technical error details')}</summary>
        <table className="diag-perf">
          <tbody>
            <tr>
              <th>CPU</th>
              <td>{num(perf?.cpu_percent, 1, ' %')}</td>
            </tr>
            <tr>
              <th>RAM</th>
              <td>{num(perf?.ram_mb, 0, ' MB')}</td>
            </tr>
            <tr>
              <th>{tr('Live-Datenvolumen', 'Live data volume')}</th>
              <td>{num(perf?.websocket_bytes === undefined ? undefined : perf.websocket_bytes / 1024 / 1024, 2, ' MB')}</td>
            </tr>
            <tr>
              <th>{tr('Datenbank', 'Database')}</th>
              <td>{num(perf?.database_mb, 1, ' MB')}</td>
            </tr>
          </tbody>
        </table>
        <h4>{tr('Letzte Fehlermeldungen im Browser', 'Recent browser errors')}</h4>
        {log.length ? (
          <ul className="error-log" data-testid="error-log">
            {log.map((e, i) => (
              <li key={i}>
                <span className="mono">{formatClock(e.at)}</span> {e.context && <b>{e.context}</b>} <code>{e.detail}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="compact-empty">{tr('Keine Fehler protokolliert.', 'No errors logged.')}</p>
        )}
      </details>
    </section>
  )
}
