/**
 * Analysis: compare a lap with a reference lap from stored sessions, over the
 * lap distance in metres. Works without the game (stored data only).
 */
import { useEffect, useMemo, useState } from 'react'
import { ArrowDownToLine, Database, X } from 'lucide-react'
import TelemetryChart, { CHANNELS, COMPARE_COLOR, REFERENCE_COLOR, channelLabel, channelUnit, channelValue } from './TelemetryChart'
import type { Compare, LapSummary, Meta, Session, Settings } from './types'
import { api, download } from './api'
import { delta, interpolate, lapTime, value } from './format'
import { formatSectorTime } from './hud'
import { friendlyError, logError } from './errors'
import { Coach, Panel, TrackMapPanel } from './widgets'

export type CatalogLap = LapSummary & { meta: Meta; created_at: string }

/** Laps are only comparable within one data source, car, track and layout. */
export const groupKey = (m: Pick<Meta, 'source' | 'car' | 'track' | 'layout'>) =>
  JSON.stringify([m.source, m.car, m.track, m.layout])

export function defaultPair(laps: CatalogLap[], requested: string[] = []) {
  const pool = requested.length ? laps.filter((l) => requested.includes(l.id)) : laps
  const complete = pool.filter((l) => l.complete)
  const ref = [...complete.filter((l) => l.valid)].sort((a, b) => a.duration_ms - b.duration_ms)[0] || complete[0]
  const others = complete.filter((l) => l.id !== ref?.id)
  const cmp = requested.length ? others[0] : others.at(-1)
  return { ref: ref?.id || '', cmp: cmp?.id || '' }
}

/** Sum of the best valid sectors of the combination; null if not computable. */
export function theoreticalBest(laps: CatalogLap[]): { ms: number; sectors: number[] } | null {
  const valid = laps.filter((l) => l.valid && l.complete && l.sectors_ms.length > 0)
  if (!valid.length) return null
  const count = valid[0].sectors_ms.length
  const best: Array<number | null> = Array(count).fill(null)
  for (const l of valid) {
    if (l.sectors_ms.length !== count) continue
    l.sectors_ms.forEach((v, i) => {
      if (v !== null && v > 0 && (best[i] === null || v < best[i]!)) best[i] = v
    })
  }
  if (best.some((v) => v === null)) return null
  return { ms: best.reduce((a, b) => a! + b!, 0)!, sectors: best as number[] }
}

const BASE_CHANNELS = ['speed', 'gas', 'brake', 'gear', 'delta']

export default function AnalysisView({
  settings,
  de,
  liveMeta,
  request,
  onRequestDone,
  persist,
  onSessions,
}: {
  settings: Settings | null
  de: boolean
  liveMeta: Meta | null
  request: string[] | null
  onRequestDone: () => void
  persist: (patch: Partial<Settings>) => void
  onSessions: () => void
}) {
  const tr = (a: string, b: string) => (de ? a : b)
  const imperial = settings?.units === 'imperial'
  const [sessions, setSessions] = useState<Session[] | null>(null),
    [group, setGroup] = useState(''),
    [laps, setLaps] = useState<CatalogLap[]>([]),
    [refId, setRefId] = useState(''),
    [cmpId, setCmpId] = useState(''),
    [comparison, setComparison] = useState<Compare | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(''),
    [channels, setChannels] = useState(BASE_CHANNELS),
    [cursor, setCursor] = useState<number | null>(null),
    [range, setRange] = useState<[number, number] | null>(null)

  const fail = (e: unknown, context: string) => {
    logError(e, context)
    setError(friendlyError(e, de))
  }

  // 1) Stored sessions (independent of the game connection).
  useEffect(() => {
    api<Session[]>('/sessions')
      .then(setSessions)
      .catch((e) => {
        setSessions([])
        fail(e, 'analysis: sessions')
      })
  }, [])

  const groups = useMemo(() => {
    const map = new Map<string, { meta: Meta; sessions: Session[] }>()
    for (const s of sessions || []) {
      const key = groupKey(s.meta)
      if (!map.has(key)) map.set(key, { meta: s.meta, sessions: [] })
      map.get(key)!.sessions.push(s)
    }
    return map
  }, [sessions])

  // Laps handed over from Sessions: kept until the matching laps are loaded.
  const [pending, setPending] = useState<string[]>([]),
    [reload, setReload] = useState(0)

  // 2) Choose the combination: from Sessions request, else the live car/track, else newest.
  useEffect(() => {
    if (!sessions || !sessions.length) return
    if (request?.length) {
      const wanted = request
      onRequestDone()
      const findGroup = async () => {
        for (const s of sessions) {
          const detail = await api<Session>('/sessions/' + s.id)
          if (detail.laps?.some((l) => wanted.includes(l.id))) return groupKey(detail.meta)
        }
        return ''
      }
      findGroup()
        .then((key) => {
          if (!key) return
          setPending(wanted)
          setGroup(key)
          setReload((r) => r + 1)
        })
        .catch((e) => fail(e, 'analysis: request'))
      return
    }
    if (group && groups.has(group)) return
    const live = liveMeta ? groupKey(liveMeta) : ''
    setGroup(groups.has(live) ? live : groupKey(sessions[0].meta))
  }, [sessions, request])

  // 3) All laps of the combination across its sessions.
  useEffect(() => {
    const entry = groups.get(group)
    if (!entry) return
    let active = true
    setLoading(true)
    Promise.all(entry.sessions.map((s) => api<Session>('/sessions/' + s.id)))
      .then((details) => {
        if (!active) return
        const all = details
          .flatMap((d) => (d.laps || []).map((l) => ({ ...l, meta: d.meta, created_at: d.created_at })))
          .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.number - b.number)
        setLaps(all)
        const pair = defaultPair(all, pending)
        setRefId(pair.ref)
        setCmpId(pair.cmp)
        setPending([])
      })
      .catch((e) => fail(e, 'analysis: laps'))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [group, groups, reload])

  // 4) Distance-based comparison from the backend.
  useEffect(() => {
    setComparison(null)
    setCursor(null)
    setRange(null)
    if (!refId || !cmpId || refId === cmpId) return
    let active = true
    setError('')
    api<Compare[]>(`/compare?lap_ids=${cmpId}&reference=${refId}`)
      .then((data) => active && setComparison(data[0] || null))
      .catch((e) => active && fail(e, 'analysis: compare'))
    return () => {
      active = false
    }
  }, [refId, cmpId])

  const ref = laps.find((l) => l.id === refId),
    cmp = laps.find((l) => l.id === cmpId)
  const theo = theoreticalBest(laps)
  const sectorCount = Math.max(ref?.sectors_ms.length || 0, cmp?.sectors_ms.length || 0)
  const groupLabel = (m: Meta) => `${m.car} · ${m.track}${m.layout ? ' / ' + m.layout : ''}${m.source === 'demo' ? ' · DEMO' : ''}`
  const lapLabel = (l: CatalogLap) =>
    `${tr('Runde', 'Lap')} ${l.number} · ${lapTime(l.duration_ms)} · ${new Date(l.created_at).toLocaleDateString()}` +
    (!l.complete ? tr(' · unvollständig', ' · partial') : !l.valid ? tr(' · ungültig*', ' · invalid*') : '')
  const select = (m: number) => {
    setCursor(m)
    setRange([Math.max(0, m - 120), m + 160])
  }
  const pickable = laps.filter((l) => l.complete)

  let hint: { text: string; action?: { label: string; run: () => void } } | null = null
  if (sessions && !sessions.length)
    hint = {
      text: tr('Noch keine gespeicherten Runden. Eine Runde fahren oder die Demo starten.', 'No stored laps yet. Drive a lap or start the demo.'),
    }
  else if (sessions && group && !loading && pickable.length < 2)
    hint = {
      text: tr('Für diese Kombination gibt es weniger als zwei vollständige Runden.', 'This combination has fewer than two complete laps.'),
      action: { label: tr('Andere Session wählen', 'Choose another session'), run: onSessions },
    }
  else if (sessions && !refId) hint = { text: tr('Referenzrunde auswählen.', 'Select a reference lap.') }
  else if (sessions && !cmpId) hint = { text: tr('Vergleichsrunde auswählen.', 'Select a comparison lap.') }

  return (
    <div className="analysis-page" data-testid="analysis-page">
      <section className="panel analysis-select">
        <label>
          {tr('Fahrzeug · Strecke', 'Car · track')}
          <select aria-label={tr('Kombination', 'Combination')} value={group} onChange={(e) => setGroup(e.target.value)}>
            {!group && <option value="">—</option>}
            {[...groups.entries()].map(([key, g]) => (
              <option key={key} value={key}>
                {groupLabel(g.meta)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="legend-swatch ref" /> {tr('Referenzrunde', 'Reference lap')}
          <select aria-label={tr('Referenzrunde', 'Reference lap')} value={refId} onChange={(e) => setRefId(e.target.value)}>
            <option value="">{tr('Referenz wählen', 'Choose reference')}</option>
            {pickable.map((l) => (
              <option key={l.id} value={l.id}>
                {lapLabel(l)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="legend-swatch cmp" /> {tr('Vergleichsrunde', 'Comparison lap')}
          <select aria-label={tr('Vergleichsrunde', 'Comparison lap')} value={cmpId} onChange={(e) => setCmpId(e.target.value)}>
            <option value="">{tr('Runde wählen', 'Choose lap')}</option>
            {pickable
              .filter((l) => l.id !== refId)
              .map((l) => (
                <option key={l.id} value={l.id}>
                  {lapLabel(l)}
                </option>
              ))}
          </select>
        </label>
        <div className="button-row">
          <button onClick={onSessions}>
            <Database size={13} />
            {tr('Sessions', 'Sessions')}
          </button>
          <button
            disabled={!refId}
            title={tr('Diese Referenz auch für Delta und HUD während der Fahrt verwenden', 'Also use this reference for live delta and HUD')}
            onClick={() => persist({ reference_lap_id: refId })}
          >
            {settings?.reference_lap_id === refId && refId ? tr('Live-Referenz ✓', 'Live reference ✓') : tr('Als Live-Referenz', 'Use as live reference')}
          </button>
          {settings?.reference_lap_id && (
            <button onClick={() => persist({ reference_lap_id: null })}>{tr('Live: persönliche Bestzeit', 'Live: personal best')}</button>
          )}
          {cmpId && (
            <button
              onClick={() =>
                download('/laps/' + cmpId + '/export?format=csv', 'lap.csv').catch((e) => fail(e, 'analysis: csv'))
              }
            >
              <ArrowDownToLine size={13} />
              CSV
            </button>
          )}
        </div>
      </section>
      {error && (
        <div className="notice danger" role="alert">
          {error}
          <button aria-label="OK" onClick={() => setError('')}>
            <X size={13} />
          </button>
        </div>
      )}
      {hint && (
        <div className="notice compact-hint" data-testid="analysis-hint">
          <span>{hint.text}</span>
          {hint.action && <button onClick={hint.action.run}>{hint.action.label}</button>}
        </div>
      )}
      {ref && cmp && (
        <div className="analysis-summary" data-testid="analysis-summary">
          <span>
            <i className="legend-line cmp" /> {tr('Vergleich', 'Comparison')}: {tr('Runde', 'Lap')} {cmp.number} · <b>{lapTime(cmp.duration_ms)}</b>
          </span>
          <span>
            <i className="legend-line ref" /> {tr('Referenz', 'Reference')}: {tr('Runde', 'Lap')} {ref.number} · <b>{lapTime(ref.duration_ms)}</b>
          </span>
          <span className={cmp.duration_ms - ref.duration_ms <= 0 ? 'green-text' : 'orange-text'}>
            Δ <b>{delta((cmp.duration_ms - ref.duration_ms) / 1000)} s</b>
          </span>
          <span title={tr('Summe der besten Sektoren aller gültigen Runden dieser Kombination', 'Sum of the best sectors of all valid laps of this combination')}>
            {tr('Theoretische Bestzeit', 'Theoretical best')}: <b>{theo ? lapTime(theo.ms) : tr('nicht berechenbar', 'not computable')}</b>
          </span>
        </div>
      )}
      {comparison && ref && cmp && (
        <div className="analysis-grid">
          <Panel
            title={tr('Telemetrievergleich über die Rundendistanz', 'Telemetry comparison over lap distance')}
            className="analysis-chart"
            actions={
              <select
                aria-label={tr('Kanal hinzufügen', 'Add channel')}
                value=""
                onChange={(e) => e.target.value && !channels.includes(e.target.value) && setChannels([...channels, e.target.value])}
              >
                <option value="">+ {tr('Kanal', 'Channel')}</option>
                {Object.keys({ ...CHANNELS, ...comparison.lap.channels })
                  .filter((k) => !channels.includes(k) && k in CHANNELS)
                  .map((k) => (
                    <option key={k} value={k}>
                      {channelLabel(k, de)}
                    </option>
                  ))}
              </select>
            }
          >
            <div className="channel-pills">
              {channels.map((k) => (
                <button key={k} onClick={() => channels.length > 1 && setChannels(channels.filter((c) => c !== k))} title={tr('Entfernen', 'Remove')}>
                  {channelLabel(k, de)} <X size={10} />
                </button>
              ))}
              {range && (
                <button className="active" onClick={() => setRange(null)}>
                  {tr('Abschnitt', 'Section')} {Math.round(range[0])}–{Math.round(range[1])} m <X size={10} />
                </button>
              )}
            </div>
            <TelemetryChart
              history={[]}
              comparison={[comparison]}
              channels={channels}
              settings={settings}
              analysis
              cursor={setCursor}
              cursorAt={cursor}
              range={range}
              height={Math.max(300, channels.length * 92)}
              de={de}
              labels={{ comparison: `${tr('Runde', 'Lap')} ${cmp.number}`, reference: `REF ${tr('Runde', 'Lap')} ${ref.number}` }}
            />
            {cursor !== null && (
              <div className="cursor-values" data-testid="cursor-values">
                <b>{Math.round(cursor)} m</b>
                {channels.map((k) => {
                  const lapVals = k === 'delta' ? comparison.delta : comparison.lap.channels[k] || []
                  const a = channelValue(k, interpolate(comparison.distance, lapVals, cursor), imperial)
                  const b =
                    k === 'delta' ? null : channelValue(k, interpolate(comparison.distance, comparison.reference.channels[k] || [], cursor), imperial)
                  return (
                    <span key={k}>
                      {channelLabel(k, de)}: <b style={{ color: COMPARE_COLOR }}>{value(a, k === 'delta' ? 3 : 1)}</b>
                      {k !== 'delta' && (
                        <>
                          {' / '}
                          <b style={{ color: REFERENCE_COLOR }}>{value(b, 1)}</b>
                        </>
                      )}{' '}
                      {channelUnit(k, imperial)}
                    </span>
                  )
                })}
              </div>
            )}
          </Panel>
          <TrackMapPanel
            title={tr('Strecke · Zeitgewinn und -verlust', 'Track · time gain and loss')}
            track={{
              points: [],
              sector_positions: cmp.sector_positions || [],
              events: comparison.events,
              complete: cmp.complete,
              source: cmp.meta.source,
              length_m: comparison.distance.at(-1),
            }}
            sample={null}
            comparison={comparison}
            comparisonComplete={cmp.complete}
            cursor={cursor ?? undefined}
            onSelect={select}
            de={de}
            defaultLayers={['heatmap', 'corners']}
          />
          <Panel title={tr('Sektorvergleich', 'Sector comparison')} className="analysis-sectors">
            {sectorCount ? (
              <table className="sector-times" data-testid="sector-compare">
                <thead>
                  <tr>
                    <th></th>
                    <th>{tr('Vergleich', 'Comparison')}</th>
                    <th>{tr('Referenz', 'Reference')}</th>
                    <th>Δ</th>
                    <th>{tr('Bester', 'Best')}</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: sectorCount }, (_, i) => {
                    const a = cmp.sectors_ms[i],
                      b = ref.sectors_ms[i]
                    const d = a !== null && a !== undefined && b !== null && b !== undefined ? (a - b) / 1000 : null
                    return (
                      <tr key={i}>
                        <th>S{i + 1}</th>
                        <td>{formatSectorTime(a)}</td>
                        <td>{formatSectorTime(b)}</td>
                        <td className={d === null ? '' : d <= 0 ? 'green-text' : 'orange-text'}>{d === null ? '—' : delta(d)}</td>
                        <td className="violet-text">{formatSectorTime(theo?.sectors[i])}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            ) : (
              <p className="compact-empty">{tr('Für diese Runden sind keine Sektorzeiten gespeichert.', 'No sector times stored for these laps.')}</p>
            )}
          </Panel>
          <Panel title={tr('Engineering-Coach', 'Engineering coach')} className="analysis-coach">
            <Coach tips={comparison.tips} de={de} select={select} />
          </Panel>
        </div>
      )}
      {comparison && comparison.corners.length > 0 && (
        <details className="panel corner-analysis">
          <summary>{tr('Kurven und Bremszonen im Detail (Schätzungen)', 'Corners and braking zones in detail (estimates)')}</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{tr('Kurve*', 'Turn*')}</th>
                  <th>{tr('Zeit / Ref.', 'Time / ref')}</th>
                  <th>{tr('Verlust', 'Loss')}</th>
                  <th>{tr('Minimum / Ref.', 'Minimum / ref')}</th>
                  <th>{tr('Bremsbeginn / Ref.', 'Brake start / ref')}</th>
                  <th>{tr('Vollgas ab', 'Full throttle at')}</th>
                </tr>
              </thead>
              <tbody>
                {comparison.corners.map((c) => (
                  <tr key={c.corner} onClick={() => { setCursor(c.apex_m); setRange([c.start_m, c.end_m]) }}>
                    <td>{c.corner}</td>
                    <td>
                      {value(c.lap.time_s, 3)} / {value(c.reference.time_s, 3)} s
                    </td>
                    <td className={(c.loss_s || 0) > 0 ? 'orange-text' : 'green-text'}>{delta(c.loss_s)} s</td>
                    <td>
                      {value(channelValue('speed', c.lap.minimum_kmh, imperial))} / {value(channelValue('speed', c.reference.minimum_kmh, imperial))}{' '}
                      {imperial ? 'mph' : 'km/h'}
                    </td>
                    <td>
                      {value(c.lap.brake_start_m)} / {value(c.reference.brake_start_m)} m
                    </td>
                    <td>{value(c.lap.full_throttle_m)} m</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small subtle">
            {tr('* Kurvennummern und Apex sind Schätzungen aus Geschwindigkeitsminima.', '* Turn numbers and apex are estimates from speed minima.')}
          </p>
        </details>
      )}
    </div>
  )
}
