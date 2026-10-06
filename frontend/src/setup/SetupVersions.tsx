/** Setup versions, manual session assignment and the before/after comparison. */
import { useEffect, useState } from 'react'
import { ArrowDownToLine, Link2 } from 'lucide-react'
import { api, download } from '../api'
import type { Session } from '../types'
import { lapTime } from '../format'
import type { Assignment, CompareResponse, CompareSide, SetupVersion } from './types'
import { fmt, secs, shortPath } from './logic'

const date = (iso: string) => new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })

export default function SetupVersions({
  car,
  track,
  sessions,
  refresh,
  includeInvalid,
  issues,
  onError,
}: {
  car: string
  track: string
  sessions: Session[]
  refresh: number
  includeInvalid: boolean
  issues: Record<string, string>
  onError: (e: unknown) => void
}) {
  const [versions, setVersions] = useState<SetupVersion[]>([]),
    [links, setLinks] = useState<Assignment[]>([]),
    [sid, setSid] = useState(''),
    [vid, setVid] = useState(''),
    [note, setNote] = useState(''),
    [a, setA] = useState(''),
    [b, setB] = useState(''),
    [cmp, setCmp] = useState<CompareResponse | null>(null),
    [loading, setLoading] = useState(false)
  const q = `car=${encodeURIComponent(car)}&track=${encodeURIComponent(track)}`
  const load = () =>
    Promise.all([api<SetupVersion[]>('/setup/versions?' + q), api<Assignment[]>('/setup/assignments?' + q)])
      .then(([v, l]) => {
        setVersions(v)
        setLinks(l)
        const exports = v.filter((x) => x.kind === 'export')
        if (exports[0] && !b) {
          setB(exports[0].id)
          setA(exports[0].parent_id || '')
        }
      })
      .catch(onError)
  useEffect(() => {
    setCmp(null)
    load()
  }, [car, track, refresh])

  const name = (id: string) => versions.find((v) => v.id === id)?.name || '—'
  const sessionLabel = (s: Session) =>
    `${date(s.created_at)} · ${s.lap_count} Runden${s.best_ms ? ' · Bestzeit ' + lapTime(s.best_ms) : ''}${s.meta.layout ? ' · ' + s.meta.layout : ''}`

  return (
    <div className="setup-versions" data-testid="setup-versions">
      {versions.length === 0 ? (
        <p className="subtle">Noch keine Versionen. Ein Ausgangssetup wird beim Auswählen gespeichert, ein Export als neue Version.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Art</th>
                <th>Änderungen</th>
                <th>Sessions</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id}>
                  <td>
                    {v.name}
                    <div className="small subtle">{date(v.created_at)}</div>
                    {v.saved_path && (
                      <div className="small green-text" title={v.saved_path}>
                        im AC-Ordner: {shortPath(v.saved_path)}
                      </div>
                    )}
                  </td>
                  <td>{v.kind === 'base' ? `Ausgang (${v.origin})` : 'Export von ' + name(v.parent_id || '')}</td>
                  <td className="small">
                    {v.changes.length
                      ? v.changes.map((c) => `${c.label}: ${fmt(c.old_value, c.unit)} → ${fmt(c.new_value, c.unit)}`).join(' · ')
                      : '—'}
                  </td>
                  <td>{v.sessions || 0}</td>
                  <td>
                    <button onClick={() => download(`/setup/versions/${v.id}/file`, v.name).catch(onError)} title="INI-Datei herunterladen">
                      <ArrowDownToLine size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4>Session einer Version zuordnen (manuell)</h4>
      <p className="small subtle">
        StintPulse kann das im Spiel geladene Setup nicht auslesen. Ordne deshalb selbst zu, mit welcher Version du eine Session gefahren bist.
      </p>
      <div className="setup-inline wrap">
        <select aria-label="Session" value={sid} onChange={(e) => setSid(e.target.value)}>
          <option value="">Session wählen</option>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {sessionLabel(s)}
              {links.find((l) => l.session_id === s.id) ? ' · ' + name(links.find((l) => l.session_id === s.id)!.version_id) : ''}
            </option>
          ))}
        </select>
        <select aria-label="Version" value={vid} onChange={(e) => setVid(e.target.value)}>
          <option value="">Version wählen</option>
          <option value="-">(Zuordnung entfernen)</option>
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <input aria-label="Notiz zur Testfahrt" placeholder="Rückmeldung nach der Testfahrt (optional)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        <button
          disabled={!sid || !vid}
          onClick={() =>
            api(`/setup/assignments/${sid}`, { method: 'PUT', body: JSON.stringify({ version_id: vid === '-' ? null : vid, note }) })
              .then(() => {
                setNote('')
                load()
              })
              .catch(onError)
          }
        >
          <Link2 size={13} /> Zuordnen
        </button>
      </div>

      <h4>Vorher / nachher vergleichen</h4>
      <div className="setup-inline wrap">
        <select aria-label="Version A" value={a} onChange={(e) => setA(e.target.value)}>
          <option value="">Version A</option>
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <select aria-label="Version B" value={b} onChange={(e) => setB(e.target.value)}>
          <option value="">Version B</option>
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <button
          disabled={!a || !b || a === b || loading}
          onClick={() => {
            setLoading(true)
            api<CompareResponse>(`/setup/compare?${q}&a=${a}&b=${b}&include_invalid=${includeInvalid}`)
              .then(setCmp)
              .catch(onError)
              .finally(() => setLoading(false))
          }}
        >
          {loading ? 'Wird verglichen …' : 'Vergleichen'}
        </button>
      </div>
      {cmp && <Comparison cmp={cmp} issues={issues} />}
    </div>
  )
}

function Comparison({ cmp, issues }: { cmp: CompareResponse; issues: Record<string, string> }) {
  const row = (label: string, get: (s: CompareSide) => string) => (
    <tr>
      <th>{label}</th>
      <td>{get(cmp.a)}</td>
      <td>{get(cmp.b)}</td>
    </tr>
  )
  const k = (s: CompareSide) => s.kpis?.kpis
  const phase = (s: CompareSide, p: 'entry' | 'mid' | 'exit') => fmt(k(s)?.phases?.[p].slip_balance, '', 2)
  const tyre = (s: CompareSide, c: 'FL' | 'FR' | 'RL' | 'RR') => {
    const t = k(s)?.tyres?.[c]
    return t ? `${fmt(t.inner, '', 0)}/${fmt(t.middle, '', 0)}/${fmt(t.outer, '', 0)} °C · ${fmt(t.pressure_hot, 'psi', 1)}` : '—'
  }
  const sectors = (s: CompareSide) => (k(s)?.sectors_median_s || []).map((v) => secs(v).replace(' s', '')).join(' / ') || '—'
  return (
    <div className="setup-compare" data-testid="setup-compare">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>A: {cmp.a.version.name}</th>
              <th>B: {cmp.b.version.name}</th>
            </tr>
          </thead>
          <tbody>
            {row('Zugeordnete Sessions (manuell)', (s) => String(s.sessions))}
            {row('Repräsentative Runden', (s) => (s.kpis ? `${s.kpis.used} (${s.kpis.excluded} ausgeschlossen)` : s.error || 'keine'))}
            {row('Rundenzeit Median', (s) => secs(k(s)?.lap_time?.median_s))}
            {row('Beste Runde', (s) => secs(k(s)?.lap_time?.best_s))}
            {row('Konstanz (Std.-Abw.)', (s) => secs(k(s)?.lap_time?.stdev_s))}
            {row('Sektoren Median', sectors)}
            {row('Schlupf v/h Eingang', (s) => phase(s, 'entry'))}
            {row('Schlupf v/h Mitte', (s) => phase(s, 'mid'))}
            {row('Schlupf v/h Ausgang', (s) => phase(s, 'exit'))}
            {(['FL', 'FR', 'RL', 'RR'] as const).map((c) => (
              <tr key={c}>
                <th>Reifen {c} (I/M/A · Druck)</th>
                <td>{tyre(cmp.a, c)}</td>
                <td>{tyre(cmp.b, c)}</td>
              </tr>
            ))}
            {row('Blockieren / Durchdrehen je Runde', (s) => {
              const e = k(s)?.events_per_lap || {}
              return `${fmt((e.wheel_lock_front || 0) + (e.wheel_lock_rear || 0), '', 2)} / ${fmt((e.wheelspin_front || 0) + (e.wheelspin_rear || 0), '', 2)}`
            })}
            {row('Bedingungen', (s) => {
              const c = s.kpis?.conditions
              return c ? `Tank ${c.fuel_start_l?.join('–') ?? '—'} l · Strecke ${c.road_c?.join('–') ?? '—'} °C · ${c.compounds.join(', ') || '—'}` : '—'
            })}
            {row('Rückmeldung vorher', (s) => (s.feedback_before ? [s.feedback_before.text, ...s.feedback_before.items.map((i) => issues[i.issue] || i.issue)].filter(Boolean).join(' · ') : '—'))}
            {row('Notizen nach Testfahrt', (s) => s.notes.join(' · ') || '—')}
          </tbody>
        </table>
      </div>
      <ul className="setup-caveats">
        {cmp.caveats.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
    </div>
  )
}
