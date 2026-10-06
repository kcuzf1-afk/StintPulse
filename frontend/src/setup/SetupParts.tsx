/** Building blocks of the setup assistant (Analyse → Setup). */
import { useState } from 'react'
import { Check, KeyRound, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../api'
import type { AiStatus, Evidence, KpiResult, Recommendation, SetupParam } from './types'
import { STATUS_HINT, STATUS_LABEL, confidenceTag, fmt, rangeText, secs, statusTag, verdictTag } from './logic'

/* ---------------------------------------------------------- provider */

export function AiProviderPanel({ ai, onChanged, onError }: { ai: AiStatus; onChanged: () => void; onError: (e: unknown) => void }) {
  const [key, setKey] = useState(''),
    [model, setModel] = useState(ai.model),
    [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      onChanged()
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }
  const keyText = ai.key.set
    ? ai.key.source === 'env'
      ? 'aus Umgebungsvariable ANTHROPIC_API_KEY'
      : ai.key.protection === 'dpapi'
        ? 'gespeichert (mit Windows-Benutzerkonto verschlüsselt)'
        : 'gespeichert (nur durch Dateirechte geschützt)'
    : ai.key.unreadable
      ? 'nicht lesbar - bitte neu eingeben'
      : 'fehlt'
  return (
    <div className="setup-provider" data-testid="ai-provider">
      {!ai.pc && <p className="notice compact-hint">Anbieter, Modell und Schlüssel lassen sich nur direkt am PC ändern.</p>}
      <div className="form-grid">
        <label className="form-field">
          KI-Anbieter
          <select
            aria-label="KI-Anbieter"
            value={ai.provider}
            disabled={!ai.pc || busy}
            onChange={(e) => run(() => api('/settings', { method: 'PATCH', body: JSON.stringify({ ai_provider: e.target.value }) }))}
          >
            <option value="off">Aus (nur regelbasiert)</option>
            <option value="anthropic">Anthropic Claude</option>
          </select>
        </label>
        <label className="form-field">
          Modell
          <span className="setup-inline">
            <input aria-label="Modell" value={model} disabled={!ai.pc || busy} onChange={(e) => setModel(e.target.value.trim())} />
            <button
              disabled={!ai.pc || busy || model === ai.model || !model}
              onClick={() => run(() => api('/settings', { method: 'PATCH', body: JSON.stringify({ ai_model: model }) }))}
            >
              Übernehmen
            </button>
          </span>
          <small className="subtle">Standard: {ai.default_model}</small>
        </label>
        <label className="form-field wide">
          API-Schlüssel: <b className={ai.key.set ? 'green-text' : 'orange-text'}>{keyText}</b>
          <span className="setup-inline">
            <input
              type="password"
              autoComplete="off"
              aria-label="API-Schlüssel"
              placeholder={ai.key.set ? 'Neuen Schlüssel eingeben, um ihn zu ersetzen' : 'sk-ant-…'}
              value={key}
              disabled={!ai.pc || busy}
              onChange={(e) => setKey(e.target.value)}
            />
            <button
              disabled={!ai.pc || busy || key.length < 20}
              onClick={() =>
                run(async () => {
                  await api('/setup/ai-key', { method: 'PUT', body: JSON.stringify({ key }) })
                  setKey('')
                })
              }
            >
              <KeyRound size={13} /> Speichern
            </button>
            {ai.key.source === 'stored' && (
              <button disabled={!ai.pc || busy} onClick={() => run(() => api('/setup/ai-key', { method: 'DELETE' }))} title="Gespeicherten Schlüssel löschen">
                <Trash2 size={13} />
              </button>
            )}
          </span>
          <small className="subtle">
            Der Schlüssel bleibt im PC-Programm: er wird nie angezeigt, nicht in den Einstellungen gespeichert und nicht protokolliert. Ein
            kostenpflichtiger API-Zugang bei Anthropic ist nötig. Lehnt das Modell eine Anfrage ab, beantwortet Anthropic sie automatisch mit einem
            Ersatzmodell (Server-Fallback).
          </small>
        </label>
      </div>
    </div>
  )
}

export function Disclosure({ ai, onAccept, onPreview, busy }: { ai: AiStatus; onAccept: () => void; onPreview: () => void; busy: boolean }) {
  const p = ai.providers[ai.provider === 'off' ? 'anthropic' : ai.provider]
  return (
    <section className="panel setup-disclosure" data-testid="ai-disclosure">
      <div className="panel-title">
        <h3>
          <ShieldCheck size={15} /> Welche Daten gehen an den KI-Anbieter?
        </h3>
      </div>
      <p>
        Anbieter: <b>{p?.label}</b> · Modell <b>{ai.model}</b> · Ziel <span className="mono">{p?.endpoint}</span>
      </p>
      <div className="setup-two">
        <div>
          <h4>Übertragen wird (nur beim Klick auf „Setup analysieren“)</h4>
          <ul>
            <li>Fahrzeug- und Streckenname, dein Ziel und deine Rückmeldung</li>
            <li>Setup-Werte des Ausgangssetups mit Name, Grenzen und Schrittweite</li>
            <li>Zusammengefasste Kennwerte der gewählten Runden (Medianwerte, Ereignisse pro Runde)</li>
            <li>Bedingungen: Tankfüllung, Luft-/Streckentemperatur, Reifenmischung</li>
          </ul>
        </div>
        <div>
          <h4>Nicht übertragen wird</h4>
          <ul>
            <li>Fahrername, Session- und Runden-IDs</li>
            <li>Rohtelemetrie (einzelne Messpunkte), Videos und Ton</li>
            <li>Andere Sessions, Einstellungen oder Dateien</li>
          </ul>
        </div>
      </div>
      <p className="small subtle">
        Es gelten die Datenschutzbestimmungen des Anbieters:{' '}
        <a href={p?.privacy} target="_blank" rel="noreferrer">
          {p?.privacy}
        </a>
        . Die genaue Anfrage kannst du vor jedem Senden in der Vorschau ansehen.
      </p>
      <div className="button-row">
        <button onClick={onPreview} disabled={busy}>
          Vorschau der Daten
        </button>
        <button className="primary" onClick={onAccept} disabled={busy || !ai.pc}>
          <Check size={13} /> Verstanden, KI-Analyse erlauben
        </button>
        {!ai.pc && <span className="small subtle">Bestätigung nur am PC möglich.</span>}
      </div>
    </section>
  )
}

/* --------------------------------------------------------- parameters */

export function ParamTable({
  params,
  onConfirm,
  showValues,
}: {
  params: SetupParam[]
  onConfirm?: (param: string, kind: string | null) => void
  showValues: boolean
}) {
  const [filter, setFilter] = useState<'all' | 'issues'>('all')
  const ok = (p: SetupParam) => (showValues ? p.exportable : p.status === 'abgeleitet' || p.status === 'bestaetigt')
  const rows = filter === 'all' ? params : params.filter((p) => !ok(p))
  return (
    <div>
      <div className="button-row setup-filter">
        <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>
          Alle ({params.length})
        </button>
        <button className={filter === 'issues' ? 'active' : ''} onClick={() => setFilter('issues')}>
          {showValues ? 'Nicht exportierbar' : 'Nicht bestätigt'} ({params.filter((p) => !ok(p)).length})
        </button>
      </div>
      <div className="table-scroll setup-params">
        <table>
          <thead>
            <tr>
              <th>Parameter</th>
              {showValues && <th>Wert</th>}
              <th>Bereich (Spiel)</th>
              <th>Datei-Kodierung</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.name} data-testid={'param-' + p.name}>
                <td>
                  {p.label}
                  <div className="mono subtle small">{p.name}</div>
                </td>
                {showValues && (
                  <td>
                    {p.value !== undefined && p.value !== null ? fmt(p.value, p.unit) : p.raw !== null ? <span className="subtle">Datei: {p.raw}</span> : '—'}
                    {p.click !== undefined && <div className="subtle small">Klick {p.click}</div>}
                  </td>
                )}
                <td>{rangeText(p)}</td>
                <td>
                  {p.encoding === 'clicks' ? 'VALUE = Klick ab MIN' : p.encoding === 'tenths' ? 'VALUE = Wert × 10' : p.encoding === 'value' ? 'VALUE = Wert' : '—'}
                </td>
                <td>
                  <span className={'tag ' + statusTag(p.status)} title={p.reason || STATUS_HINT[p.status]}>
                    {STATUS_LABEL[p.status]}
                  </span>
                  {p.status === 'mehrdeutig' && onConfirm && p.raw !== null && (
                    <div className="setup-confirm">
                      <small>Datei VALUE={p.raw} zeigt im Spiel:</small>
                      {p.options
                        .filter((o) => o.display !== null)
                        .map((o) => (
                          <button key={o.kind} onClick={() => onConfirm(p.name, o.kind)} title={o.label}>
                            {fmt(o.display, p.unit)}
                          </button>
                        ))}
                    </div>
                  )}
                  {p.status === 'bestaetigt' && onConfirm && (
                    <button className="link-button" onClick={() => onConfirm(p.name, null)}>
                      Bestätigung zurücknehmen
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {onConfirm && params.some((p) => p.status === 'mehrdeutig') && (
        <p className="small subtle">
          Mehrdeutig: Bitte nur bestätigen, wenn du den Wert im Setup-Menü des Spiels abgelesen hast. Ohne Bestätigung wird der Parameter nie
          exportiert.
        </p>
      )}
    </div>
  )
}

/* ------------------------------------------------------- measurements */

const PHASE_LABEL = { entry: 'Kurveneingang', mid: 'Kurvenmitte', exit: 'Kurvenausgang' } as const
const EVENT_LABEL: Record<string, string> = {
  wheel_lock_front: 'Blockieren vorne',
  wheel_lock_rear: 'Blockieren hinten',
  wheelspin_front: 'Durchdrehen vorne',
  wheelspin_rear: 'Durchdrehen hinten',
  body_slip: 'Rutscher (Schwimmwinkel)',
  countersteer_est: 'Gegenlenken (geschätzt)',
  understeer_est: 'Untersteuern (geschätzt)',
  oversteer_est: 'Übersteuern (geschätzt)',
  unstable_braking: 'Unruhiges Bremsen',
  throttle_correction: 'Gaskorrekturen',
  tyre_overheat: 'Reifen überhitzt',
}

export function DataUsed({ kpi }: { kpi: KpiResult }) {
  const missing = kpi.availability.filter((a) => !a.available)
  return (
    <div data-testid="setup-data-used">
      <p>
        <b>{kpi.used}</b> repräsentative Runde(n) ausgewertet, <b>{kpi.excluded}</b> ausgeschlossen.
      </p>
      {kpi.warnings.map((w) => (
        <div key={w} className="notice compact-hint">
          {w}
        </div>
      ))}
      {kpi.laps.some((l) => l.used && l.notes?.length) && (
        <ul className="setup-excluded">
          {kpi.laps
            .filter((l) => l.used && l.notes?.length)
            .map((l) => (
              <li key={l.id}>
                Runde {l.number} einbezogen: {l.notes!.join(', ')}
              </li>
            ))}
        </ul>
      )}
      {kpi.laps.some((l) => !l.used) && (
        <ul className="setup-excluded">
          {kpi.laps
            .filter((l) => !l.used)
            .map((l) => (
              <li key={l.id}>
                Runde {l.number}: {l.reasons.join(', ')}
              </li>
            ))}
        </ul>
      )}
      <div className="setup-chips" aria-label="Datenverfügbarkeit">
        {kpi.availability.map((a) => (
          <span key={a.key} className={'tag ' + (a.available ? 'green-tag' : 'muted-tag')} title={`${Math.round(a.ratio * 100)} % der Messpunkte`}>
            {a.available ? '✓' : '✕'} {a.label}
          </span>
        ))}
      </div>
      {missing.length > 0 && <p className="small subtle">Fehlende Kanäle werden nicht geschätzt; betroffene Aussagen sind als „nicht messbar“ markiert.</p>}
      {kpi.notes.map((n) => (
        <p key={n} className="small subtle">
          {n}
        </p>
      ))}
    </div>
  )
}

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return <p className="subtle">Keine Rückmeldung angegeben - es gibt nichts abzugleichen.</p>
  return (
    <ul className="setup-evidence" data-testid="setup-evidence">
      {evidence.map((e) => (
        <li key={e.item}>
          <span className={'tag ' + verdictTag(e.verdict)}>{e.verdict}</span> <b>{e.label}</b>
          {e.details.map((d) => (
            <div key={d} className="small subtle">
              {d}
            </div>
          ))}
        </li>
      ))}
    </ul>
  )
}

export function KpiTables({ kpi }: { kpi: KpiResult }) {
  const k = kpi.kpis
  const events = Object.entries(k.events_per_lap || {})
  return (
    <div className="setup-kpis">
      {k.lap_time && (
        <p>
          Rundenzeit Median <b>{secs(k.lap_time.median_s)}</b> · Beste {secs(k.lap_time.best_s)} · Streuung {secs(k.lap_time.spread_s)} ·
          Höchstgeschwindigkeit {fmt(k.top_speed_kmh, 'km/h', 0)}
        </p>
      )}
      {k.phases && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Phase (Indizien)</th>
                <th title="Mittlerer Radschlupf vorne / hinten, nur ohne starkes Bremsen oder Gas">Schlupf v/h</th>
                <th title="Winkel zwischen Fahrzeuglängsachse und Fahrtrichtung">Schwimmwinkel</th>
                <th title="Lenkeinschlag (−1…1) je g Querbeschleunigung - nur relativ vergleichbar">Lenkung je g</th>
              </tr>
            </thead>
            <tbody>
              {(['entry', 'mid', 'exit'] as const).map((p) => (
                <tr key={p}>
                  <td>{PHASE_LABEL[p]}</td>
                  <td>{fmt(k.phases![p].slip_balance, '', 2)}</td>
                  <td>{fmt(k.phases![p].body_slip_deg, '°', 2)}</td>
                  <td>{fmt(k.phases![p].steer_per_g, '', 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {k.tyres && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Reifen</th>
                <th>Innen / Mitte / Außen</th>
                <th>Kern</th>
                <th>Druck (2. Rundenhälfte)</th>
                <th>Verschleiß je Runde (roh)</th>
              </tr>
            </thead>
            <tbody>
              {(['FL', 'FR', 'RL', 'RR'] as const).map((c) => {
                const t = k.tyres![c]
                return (
                  <tr key={c}>
                    <td>{c}</td>
                    <td>
                      {fmt(t.inner, '', 1)} / {fmt(t.middle, '', 1)} / {fmt(t.outer, '', 1)} °C
                    </td>
                    <td>{fmt(t.core, '°C', 1)}</td>
                    <td>{fmt(t.pressure_hot, 'psi', 1)}</td>
                    <td>{fmt(t.wear_per_lap, '', 3)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {events.length > 0 && (
        <p className="small">
          Ereignisse pro Runde:{' '}
          {events.map(([key, v], i) => (
            <span key={key}>
              {i > 0 && ' · '}
              {EVENT_LABEL[key] || key} {fmt(v, '', 2)}
            </span>
          ))}
        </p>
      )}
      <p className="small subtle">
        Bedingungen: Tank {kpi.conditions.fuel_start_l ? kpi.conditions.fuel_start_l.join('–') + ' l' : '—'} · Luft{' '}
        {kpi.conditions.air_c ? kpi.conditions.air_c.join('–') + ' °C' : '—'} · Strecke {kpi.conditions.road_c ? kpi.conditions.road_c.join('–') + ' °C' : '—'} ·
        Reifen {kpi.conditions.compounds.join(', ') || '—'}
      </p>
    </div>
  )
}

/* ------------------------------------------------------ recommendation */

export function RecommendationCard({ rec, selected, onToggle }: { rec: Recommendation; selected: boolean; onToggle: () => void }) {
  return (
    <article className={'setup-rec' + (selected ? ' selected' : '')} data-testid={'rec-' + rec.parameter}>
      <header>
        <span className="setup-rec-prio">{rec.priority}</span>
        <div>
          <strong>{rec.label}</strong> <span className="mono subtle small">{rec.parameter}</span>
          <div className="setup-rec-value">
            {rec.exportable ? (
              <>
                {fmt(rec.old_value, rec.unit)} → <b>{fmt(rec.new_value, rec.unit)}</b>
              </>
            ) : (
              <>
                Dateiwert {rec.old_raw} → {fmt(rec.new_value)} <span className="orange-text">(Zielwert unbestätigt)</span>
              </>
            )}
          </div>
          <div className="mono small subtle">{rec.ini}</div>
        </div>
        <span className={'tag ' + confidenceTag(rec.confidence)} title={rec.confidence_limited ? 'Konfidenz durch die Datenlage begrenzt' : ''}>
          Konfidenz {rec.confidence}
          {rec.confidence_limited ? '*' : ''}
        </span>
      </header>
      <dl>
        <dt>Beobachtung</dt>
        <dd>{rec.observation}</dd>
        <dt>Mögliche Ursache</dt>
        <dd>{rec.possible_cause}</dd>
        <dt>Vorgeschlagener Test</dt>
        <dd>{rec.test}</dd>
        <dt>Begründung</dt>
        <dd>{rec.reasoning}</dd>
        <dt>Erwartete Wirkung</dt>
        <dd>{rec.expected_effect}</dd>
        <dt>Nachteile</dt>
        <dd>{rec.tradeoffs}</dd>
      </dl>
      <footer>
        {rec.exportable ? (
          <label className="setup-take">
            <input type="checkbox" checked={selected} onChange={onToggle} aria-label={'Übernehmen: ' + rec.label} /> Für den Export übernehmen
          </label>
        ) : (
          <span className="orange-text small">Nicht exportierbar: {rec.export_block}</span>
        )}
      </footer>
    </article>
  )
}
