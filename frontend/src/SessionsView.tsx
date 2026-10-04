import { useState, useEffect } from 'react'
import {
  Search,
  Star,
  Download,
  Upload,
  Trash2,
  GitCompareArrows,
  Archive,
  Play,
  Clapperboard,
  VideoOff,
} from 'lucide-react'
import { api, download } from './api'
import { friendlyError, logError } from './errors'
import { groupKey } from './AnalysisView'
import { lapTime, value } from './format'
import type { Session, LapSummary, Settings } from './types'
interface Props {
  activeId: string | null
  settings: Settings | null
  /** Hand the selected laps to the Analysis page (comparison happens there). */
  onCompare: (lapIds: string[]) => void
  onReplay: (lap: LapSummary) => void
  onReplaySession: (laps: LapSummary[]) => void
  /** Play the recorded onboard video of a lap (with its stored telemetry HUD). */
  onWatch: (lap: LapSummary) => void
}
export default function SessionsView({
  activeId,
  settings,
  onCompare,
  onReplay,
  onReplaySession,
  onWatch,
}: Props) {
  const [sessions, setSessions] = useState<Session[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [lapSelection, setLapSelection] = useState<string[]>([]),
    [details, setDetails] = useState<Session[]>([]),
    [q, setQ] = useState(''),
    [source, setSource] = useState(''),
    [error, setError] = useState('')
  const de = settings?.language !== 'en',
    tr = (a: string, b: string) => (de ? a : b)
  const fail = (e: unknown, context: string) => {
    logError(e, context)
    setError(friendlyError(e, de))
  }
  async function load() {
    try {
      setSessions(
        await api<Session[]>(
          `/sessions?q=${encodeURIComponent(q)}${source ? '&source=' + source : ''}`,
        ),
      )
    } catch (e) {
      fail(e, 'sessions')
    }
  }
  useEffect(() => {
    const id = setTimeout(load, 200)
    return () => clearTimeout(id)
  }, [q, source])
  useEffect(() => {
    let active = true
    setLapSelection([])
    Promise.all(selected.map((s) => api<Session>('/sessions/' + s)))
      .then((s) => {
        if (active) setDetails(s)
      })
      .catch((e) => fail(e, 'sessions: details'))
    return () => {
      active = false
    }
  }, [selected])
  const laps = details.flatMap((s) => s.laps || []),
    chosen = laps.filter((l) => lapSelection.includes(l.id))
  // Comparable only within one data source, car, track and layout.
  const chosenGroups = new Set(
    chosen.map((l) => groupKey(details.find((d) => d.id === l.session_id)!.meta)),
  )
  const compareBlocked =
    chosen.length !== 2
      ? tr('Zwei Runden auswählen.', 'Select two laps.')
      : chosen.some((l) => !l.complete)
        ? tr('Nur vollständige Runden sind vergleichbar.', 'Only complete laps can be compared.')
        : chosenGroups.size > 1
          ? tr(
              'Nicht vergleichbar: anderes Fahrzeug, andere Strecke, anderes Layout oder Demo/echt gemischt.',
              'Not comparable: different car, track, layout or demo/real mixed.',
            )
          : ''
  /** Video status of a lap; "Onboard ansehen" whenever (some) video exists. */
  function videoCell(l: LapSummary) {
    const v = l.video?.coverage || 'none'
    const watch = (
      <button
        className="watch-button"
        onClick={() => onWatch(l)}
        aria-label={tr(`Onboard ansehen: Runde ${l.number}`, `Watch onboard: lap ${l.number}`)}
      >
        <Clapperboard size={13} />
        {tr('Onboard ansehen', 'Watch onboard')}
      </button>
    )
    if (v === 'full')
      return (
        <div className="video-cell">
          {watch}
          <small className="green-text" data-testid="video-badge">
            {tr('Video verfügbar', 'Video available')}
            {l.video?.audio ? tr(' · mit Ton', ' · with sound') : ''}
          </small>
        </div>
      )
    if (v === 'partial')
      return (
        <div className="video-cell">
          {watch}
          <small className="yellow-text" data-testid="video-badge" title={tr('Ein Teil der Runde hat kein Video', 'Part of the lap has no video')}>
            {tr('Video teilweise', 'Partial video')} · {Math.round((l.video?.covered_ratio || 0) * 100)} %
          </small>
        </div>
      )
    if (v === 'pending')
      return (
        <small className="cyan-text" data-testid="video-badge">
          {tr('Video wird noch gespeichert …', 'Video is being saved …')}
        </small>
      )
    return (
      <small className="subtle" data-testid="video-badge" title={tr('Kein Video für diese Runde', 'No video for this lap')}>
        <VideoOff size={12} /> {tr('Kein Video', 'No video')}
      </small>
    )
  }
  async function deleteVideos(s: Session) {
    if (!confirm(tr('Alle Videos dieser Session löschen? Telemetrie und Runden bleiben erhalten.', "Delete all videos of this session? Telemetry and laps are kept.")))
      return
    try {
      await api('/sessions/' + s.id + '/videos', { method: 'DELETE' })
      await load()
      setSelected([...selected])
    } catch (e) {
      fail(e, 'sessions: videos')
    }
  }
  async function exportData(path: string, name: string) {
    try {
      await download(path, name)
    } catch (e) {
      fail(e, 'sessions')
    }
  }
  async function importFile(file: File, backup = false) {
    try {
      const binary = backup && file.name.toLowerCase().endsWith('.sqlite')
      if (file.size > (binary ? 2 * 1024 ** 3 : 64 * 1024 ** 2))
        throw new Error(binary ? 'Maximum backup size: 2 GiB' : 'Maximum JSON import size: 64 MiB')
      await api(backup ? '/restore' : '/import', {
        method: 'POST',
        body: binary ? file : await file.text(),
        headers: binary ? { 'Content-Type': 'application/octet-stream' } : undefined,
      })
      await load()
    } catch (e) {
      fail(e, 'sessions')
    }
  }
  return (
    <div className="sessions-view">
      <div className="page-heading">
        <div>
          <h2>{tr('Gespeicherte Fahrten', 'Recorded sessions')}</h2>
        </div>
        <div className="button-row">
          <label className="button">
            <Upload size={14} />
            {tr('Session importieren', 'Import session')}
            <input
              hidden
              type="file"
              accept=".json"
              onChange={(e) => {
                if (e.target.files?.[0]) importFile(e.target.files[0])
                e.target.value = ''
              }}
            />
          </label>
          <button onClick={() => exportData('/backup', 'stintpulse-backup.sqlite')}>
            <Archive size={14} />
            Backup
          </button>
          <label className="button">
            {tr('Backup laden', 'Restore backup')}
            <input
              hidden
              type="file"
              accept=".sqlite,.json"
              onChange={(e) => {
                if (e.target.files?.[0]) importFile(e.target.files[0], true)
                e.target.value = ''
              }}
            />
          </label>
        </div>
      </div>
      {error && (
        <div className="notice danger" role="alert">
          {error}
        </div>
      )}
      <div className="archive-tools">
        <label className="search">
          <Search size={16} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={tr('Fahrer, Fahrzeug oder Strecke suchen', 'Search driver, car or track')}
          />
        </label>
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">{tr('Alle Quellen', 'All sources')}</option>
          <option value="ac">Assetto Corsa</option>
          <option value="demo">Demo</option>
        </select>
        <button onClick={load}>{tr('Aktualisieren', 'Refresh')}</button>
      </div>
      <div className="archive-layout">
        <section className="panel session-list">
          <h3>{tr('Sessions · bis zu zwei öffnen', 'Sessions · open up to two')}</h3>
          {sessions.length === 0 && (
            <p className="subtle">
              {tr(
                'Noch keine gespeicherten Sessions. Demo starten oder AC fahren.',
                'No sessions saved. Start Demo or drive in AC.',
              )}
            </p>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={'session-item ' + (selected.includes(s.id) ? 'selected' : '')}
            >
              <label className="session-select">
                <input
                  type="checkbox"
                  checked={selected.includes(s.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, s.id].slice(-2)
                        : selected.filter((id) => id !== s.id),
                    )
                  }
                />
                <div>
                  <strong>
                    {s.meta.track}
                    <small> / {s.meta.layout || 'base'}</small>
                  </strong>
                  <span>
                    {s.meta.car} · {s.meta.driver}
                  </span>
                  <small>
                    {new Date(s.created_at).toLocaleString()} · {s.lap_count} {tr('Runden', 'laps')}
                  </small>
                </div>
              </label>
              <div className="session-actions">
                <span className={'tag ' + (s.meta.source === 'demo' ? 'orange-tag' : 'cyan-tag')}>
                  {s.meta.source === 'demo' ? 'DEMO' : 'AC'}
                </span>
                {!!s.video_count && (
                  <span className="tag green-tag" title={tr('Session mit Onboard-Video', 'Session with onboard video')}>
                    <Clapperboard size={11} /> VIDEO
                  </span>
                )}
                <b title={tr('Beste gültige Runde', 'Best valid lap')}>{lapTime(s.best_ms)}</b>
                <div>
                  <button
                    title={tr('Favorit', 'Favorite')}
                    onClick={async () => {
                      try {
                        await api('/sessions/' + s.id, {
                          method: 'PATCH',
                          body: JSON.stringify({ favorite: !s.favorite }),
                        })
                        load()
                      } catch (e) {
                        fail(e, 'sessions')
                      }
                    }}
                  >
                    <Star size={13} fill={s.favorite ? '#ffcc57' : 'none'} />
                  </button>
                  <button
                    title={tr('Session exportieren', 'Export session')}
                    onClick={() => exportData('/sessions/' + s.id + '/export', 'ac-session.json')}
                  >
                    <Download size={13} />
                  </button>
                  <button
                    title={tr('Session löschen', 'Delete session')}
                    disabled={s.id === activeId}
                    onClick={async () => {
                      if (
                        confirm(
                          s.video_count
                            ? tr(
                                'Diese Session, alle Runden und ihre Onboard-Videos löschen?',
                                'Delete this session, all its laps and its onboard videos?',
                              )
                            : tr('Diese Session und alle Runden löschen?', 'Delete this session and all its laps?'),
                        )
                      ) {
                        try {
                          await api('/sessions/' + s.id, { method: 'DELETE' })
                          setSelected(selected.filter((id) => id !== s.id))
                          load()
                        } catch (e) {
                          fail(e, 'sessions')
                        }
                      }
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                  {!!s.video_count && (
                    <button
                      title={tr('Videos dieser Session löschen', 'Delete videos of this session')}
                      aria-label={tr('Videos dieser Session löschen', 'Delete videos of this session')}
                      disabled={s.id === activeId}
                      onClick={() => deleteVideos(s)}
                    >
                      <VideoOff size={13} />
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </section>
        <section className="panel lap-list">
          <div className="panel-title">
            <h3>{tr('Runden', 'Laps')}</h3>
            <button
              className="primary"
              disabled={!!compareBlocked}
              title={compareBlocked || undefined}
              onClick={() => onCompare(chosen.map((l) => l.id))}
            >
              <GitCompareArrows size={14} />
              {tr('In Analyse vergleichen', 'Compare in Analysis')}
            </button>
          </div>
          {chosen.length > 0 && compareBlocked && (
            <p className="compact-empty" data-testid="compare-blocked">
              {compareBlocked}
            </p>
          )}
          {details.length === 0 ? (
            <div className="empty-state">
              {tr('Wähle eine Session, um Runden zu öffnen.', 'Select a session to open its laps.')}
            </div>
          ) : (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th></th>
                      <th>{tr('Runde', 'Lap')}</th>
                      <th>{tr('Zeit', 'Time')}</th>
                      <th>S1</th>
                      <th>S2</th>
                      <th>S3</th>
                      <th>Status</th>
                      <th>Video</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {laps.map((l) => (
                      <tr
                        key={l.id}
                        className={chosen.some((c) => c.id === l.id) ? 'selected' : ''}
                      >
                        <td>
                          <input
                            aria-label={tr(`Runde ${l.number} auswählen`, `Select lap ${l.number}`)}
                            type="checkbox"
                            checked={lapSelection.includes(l.id)}
                            onChange={(e) =>
                              setLapSelection(
                                e.target.checked
                                  ? [...lapSelection, l.id].slice(-2)
                                  : lapSelection.filter((id) => id !== l.id),
                              )
                            }
                          />
                        </td>
                        <td>{String(l.number).padStart(2, '0')}</td>
                        <td className="mono">{lapTime(l.duration_ms)}</td>
                        {[0, 1, 2].map((i) => (
                          <td key={i}>
                            {value(
                              l.sectors_ms[i] === undefined || l.sectors_ms[i] === null
                                ? null
                                : l.sectors_ms[i]! / 1000,
                              3,
                            )}
                          </td>
                        ))}
                        <td title={l.reasons.join(', ')}>
                          <span className={l.valid ? 'green-text' : 'yellow-text'}>
                            {l.valid
                              ? tr('GÜLTIG*', 'VALID*')
                              : l.complete
                                ? tr('UNGÜLTIG*', 'INVALID*')
                                : tr('UNVOLLSTÄNDIG', 'PARTIAL')}
                          </span>
                        </td>
                        <td>{videoCell(l)}</td>
                        <td>
                          <div className="button-row">
                            <button
                              title={tr('Telemetrie im Onboard wiedergeben (ohne Video)', 'Replay telemetry in Onboard (no video)')}
                              aria-label={tr(`Runde ${l.number} wiedergeben`, `Replay lap ${l.number}`)}
                              onClick={() => onReplay(l)}
                            >
                              <Play size={13} />
                            </button>
                            <button
                              title={tr('CSV exportieren', 'Export CSV')}
                              onClick={() =>
                                exportData(
                                  '/laps/' + l.id + '/export?format=csv',
                                  `lap-${l.number}.csv`,
                                )
                              }
                            >
                              <Download size={13} />
                            </button>
                            <button
                              title={tr('JSON exportieren', 'Export JSON')}
                              onClick={() =>
                                exportData(
                                  '/laps/' + l.id + '/export?format=json',
                                  `lap-${l.number}.json`,
                                )
                              }
                            >
                              JSON
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button
                disabled={!details[0]?.laps?.some((l) => l.complete)}
                onClick={() => onReplaySession(details[0]?.laps || [])}
              >
                <Play size={13} />
                {tr('Session im Onboard wiedergeben', 'Replay session in Onboard')}
              </button>
              <p className="subtle small">
                {tr(
                  'Video: nur für Fahrten mit aktivierter Aufnahme (Einstellungen → Onboard und HUD). „Teilweise“ = für einen Teil der Runde fehlt das Video; ältere Sessions haben kein Video.',
                  'Video: only for drives with recording enabled (Settings → Onboard and HUD). "Partial" = part of the lap has no video; older sessions have no video.',
                )}
              </p>
              <p className="subtle small">
                *{' '}
                {tr(
                  'Gültigkeit aus Off-Track-, Pit- und Strafdaten abgeleitet. Original-AC hat kein eindeutiges Shared-Memory-Gültigkeitsflag.',
                  'Validity is inferred from off-track, pit and penalty data. Original AC has no shared-memory lap-validity flag.',
                )}
              </p>
              <p className="subtle small">
                {tr(
                  'Zwei Runden auswählen (auch aus zwei Sessions). Fahrzeug, Strecke, Layout und Datenquelle müssen übereinstimmen; die schnellere gültige Runde wird Referenz.',
                  'Select two laps (also from two sessions). Car, track, layout and source must match; the faster valid lap becomes the reference.',
                )}
              </p>
              <details className="stint-details">
                <summary>
                  {tr('Stint, Reifenentwicklung & Kraftstoff', 'Stint, tyre evolution & fuel')}
                </summary>
                {details.map((s) => (
                  <div key={s.id}>
                    <p className="subtle small">
                      {s.meta.track} · {tr('Ausreißer', 'Outliers')}:{' '}
                      {s.statistics?.outliers?.length || 0} · σ last 5:{' '}
                      {value(s.statistics?.last_five_std_s, 3)} s ·{' '}
                      {tr('Kraftstoffkorrelation', 'Fuel correlation')}:{' '}
                      {value(s.statistics?.fuel_effect?.seconds_per_litre, 3)} s/L (
                      {tr('Schätzung, ab 8 Runden', 'estimate, at least 8 laps')})
                    </p>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>{tr('Runde', 'Lap')}</th>
                            <th>{tr('Zeit', 'Time')}</th>
                            <th>{tr('Kraftstoff L', 'Fuel L')}</th>
                            <th>FL °C</th>
                            <th>FR °C</th>
                            <th>RL °C</th>
                            <th>RR °C</th>
                          </tr>
                        </thead>
                        <tbody>
                          {s.statistics?.stint?.map((row, i) => (
                            <tr key={i}>
                              <td>{row.lap}</td>
                              <td>{value(row.time_s, 3)}</td>
                              <td>{value(row.fuel_l, 1)}</td>
                              {['FL', 'FR', 'RL', 'RR'].map((c) => (
                                <td key={c}>{value(row.tyres[c], 1)}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </details>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
