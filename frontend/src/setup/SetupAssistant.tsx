/**
 * Analyse → Setup: improve an existing Assetto Corsa setup from stored laps
 * and the driver's feedback. Limits and encodings are checked in the backend;
 * recommendations are applied one by one and exported as a NEW file.
 */
import { useEffect, useMemo, useState } from 'react'
import { ArrowDownToLine, Bot, FolderInput, Plus, Save, Upload, Wrench, X } from 'lucide-react'
import { api, download } from '../api'
import type { Session } from '../types'
import { lapTime } from '../format'
import { friendlyError, logError } from '../errors'
import { Panel } from '../widgets'
import type { BaseResponse, CarInfo, ExportResponse, FeedbackItem, ParamStatus, Preview, SetupAnalysis, SetupStatus } from './types'
import { STATUS_HINT, STATUS_LABEL, aiStateText, comboKey, combos, defaultLaps, exportable, fileToBase64, fmt, statusTag } from './logic'
import { AiProviderPanel, DataUsed, Disclosure, EvidenceList, KpiTables, ParamTable, RecommendationCard } from './SetupParts'
import SetupVersions from './SetupVersions'

const MAX_LAPS = 20

function message(e: unknown) {
  const text = e instanceof Error ? e.message : String(e)
  return /Failed to fetch|NetworkError|Load failed/i.test(text) ? friendlyError(e, true) : text
}

export default function SetupAssistant({ liveCar }: { liveCar?: { car: string; track: string } | null }) {
  const [status, setStatus] = useState<SetupStatus | null>(null),
    [sessions, setSessions] = useState<Session[] | null>(null),
    [combo, setCombo] = useState(''),
    [car, setCar] = useState<CarInfo | null>(null),
    [details, setDetails] = useState<Session[]>([]),
    [baseKey, setBaseKey] = useState(''),
    [base, setBase] = useState<BaseResponse | null>(null),
    [lapIds, setLapIds] = useState<string[]>([]),
    [includeInvalid, setIncludeInvalid] = useState(false),
    [goal, setGoal] = useState('race'),
    [goalText, setGoalText] = useState(''),
    [items, setItems] = useState<FeedbackItem[]>([]),
    [draft, setDraft] = useState<FeedbackItem>({ issue: 'understeer', phase: 'entry', corners: '', severity: 2 }),
    [text, setText] = useState(''),
    [preview, setPreview] = useState<Preview | null>(null),
    [analysis, setAnalysis] = useState<SetupAnalysis | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [exportName, setExportName] = useState(''),
    [exported, setExported] = useState<ExportResponse | null>(null),
    [saveFolder, setSaveFolder] = useState<'track' | 'generic'>('track'),
    [saved, setSaved] = useState(''),
    [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [providerOpen, setProviderOpen] = useState(false),
    [refresh, setRefresh] = useState(0)

  const fail = (e: unknown, context: string) => {
    logError(e, 'setup: ' + context)
    setError(message(e))
  }
  const loadStatus = () =>
    api<SetupStatus>('/setup/status')
      .then(setStatus)
      .catch((e) => fail(e, 'status'))

  useEffect(() => {
    loadStatus()
    api<Session[]>('/sessions')
      .then(setSessions)
      .catch((e) => {
        setSessions([])
        fail(e, 'sessions')
      })
  }, [])

  const groups = useMemo(() => combos(sessions || []), [sessions])
  useEffect(() => {
    if (!sessions?.length || (combo && groups.has(combo))) return
    // Prefer the car/track being driven, else the newest real combination with laps.
    const live = liveCar ? comboKey(liveCar.car, liveCar.track) : ''
    const withLaps = [...groups.entries()].filter(([, g]) => g.sessions.some((s) => s.lap_count > 0))
    const pick = withLaps.find(([, g]) => !g.demo) || withLaps[0]
    setCombo(groups.has(live) && withLaps.some(([k]) => k === live) ? live : pick ? pick[0] : groups.keys().next().value || '')
  }, [groups])

  const group = groups.get(combo)
  const loadCar = () => {
    if (!group) return Promise.resolve()
    return api<CarInfo>(`/setup/car?car=${encodeURIComponent(group.car)}&track=${encodeURIComponent(group.track)}`)
      .then(setCar)
      .catch((e) => fail(e, 'car'))
  }
  useEffect(() => {
    setCar(null)
    setBase(null)
    setBaseKey('')
    setAnalysis(null)
    setPreview(null)
    setExported(null)
    setDetails([])
    if (!group) return
    loadCar()
    Promise.all(group.sessions.map((s) => api<Session>('/sessions/' + s.id)))
      .then((d) => {
        setDetails(d)
        setLapIds(defaultLaps(d))
      })
      .catch((e) => fail(e, 'laps'))
  }, [combo])

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label)
    setError('')
    try {
      await fn()
    } catch (e) {
      fail(e, label)
    } finally {
      setBusy('')
    }
  }

  const chooseBase = (key: string) => {
    setBaseKey(key)
    setAnalysis(null)
    setExported(null)
    if (!key || !group) return setBase(null)
    const [folder, ...rest] = key.split('/')
    run('base', async () => {
      setBase(
        await api<BaseResponse>('/setup/base', {
          method: 'POST',
          body: JSON.stringify({ car: group.car, track: group.track, folder, name: rest.join('/') }),
        }),
      )
      setRefresh((r) => r + 1)
    })
  }
  const uploadBase = (file: File) =>
    group &&
    run('base', async () => {
      setBaseKey('upload')
      setBase(
        await api<BaseResponse>('/setup/base', {
          method: 'POST',
          body: JSON.stringify({ car: group.car, track: group.track, upload: true, name: file.name, content_b64: await fileToBase64(file) }),
        }),
      )
      setRefresh((r) => r + 1)
    })
  const importSpec = (file: File) =>
    group &&
    run('spec', async () => {
      await api('/setup/spec', { method: 'POST', body: JSON.stringify({ car: group.car, content_b64: await fileToBase64(file) }) })
      await loadCar()
      if (baseKey && baseKey !== 'upload') chooseBase(baseKey)
    })
  const confirm = (param: string, encoding: string | null) =>
    group &&
    run('confirm', async () => {
      await api('/setup/confirm', { method: 'PUT', body: JSON.stringify({ car: group.car, param, encoding }) })
      await loadCar()
      if (baseKey && baseKey !== 'upload') chooseBase(baseKey)
    })

  const request = () => ({
    car: group!.car,
    track: group!.track,
    base_version_id: base?.version.id,
    lap_ids: lapIds,
    include_invalid: includeInvalid,
    goal,
    goal_text: goalText,
    feedback: { items, text },
  })
  const showPreview = () =>
    run('preview', async () => {
      setPreview(await api<Preview>('/setup/preview', { method: 'POST', body: JSON.stringify(request()) }))
    })
  const analyze = (mode: 'ai' | 'rules') =>
    run('analyze', async () => {
      setExported(null)
      setSaved('')
      const a = await api<SetupAnalysis>('/setup/analyze', { method: 'POST', body: JSON.stringify({ ...request(), mode }) })
      setAnalysis(a)
      setSelected(exportable(a.result.changes || []).map((c) => c.parameter))
      setExportName('')
    })
  const acceptConsent = () =>
    run('consent', async () => {
      await api('/settings', { method: 'PATCH', body: JSON.stringify({ ai_consent: status!.ai.disclosure_version }) })
      await loadStatus()
    })
  const doExport = () =>
    run('export', async () => {
      const r = await api<ExportResponse>('/setup/export', {
        method: 'POST',
        body: JSON.stringify({ analysis_id: analysis!.id, parameters: selected, ...(exportName.trim() ? { name: exportName.trim() } : {}) }),
      })
      setExported(r)
      setSaved('')
      setRefresh((x) => x + 1)
      await download(`/setup/versions/${r.version.id}/file`, r.file_name)
    })
  const saveToAc = () =>
    run('save', async () => {
      const r = await api<{ path: string }>(`/setup/versions/${exported!.version.id}/save`, {
        method: 'POST',
        body: JSON.stringify({ folder: saveFolder, name: exported!.file_name }),
      })
      setSaved(r.path)
      setRefresh((x) => x + 1)
    })

  const ai = status?.ai
  const lapsBySession = details
    .map((s) => ({ s, laps: (s.laps || []).filter((l) => l.complete) }))
    .filter((x) => x.laps.length)
    .sort((a, b) => b.s.created_at.localeCompare(a.s.created_at))
  const toggleLap = (id: string) =>
    setLapIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : ids.length >= MAX_LAPS ? ids : [...ids, id]))
  const ready = !!(group && base && lapIds.length)
  const changes = analysis?.result.changes || []
  const chosen = changes.filter((c) => selected.includes(c.parameter))

  if (sessions && !sessions.length)
    return <div className="notice compact-hint">Noch keine gespeicherten Runden. Erst ein paar Runden fahren, dann hier das Setup analysieren.</div>

  return (
    <div className="setup-page" data-testid="setup-page">
      <div className="analysis-summary setup-status">
        <span>
          <Bot size={14} /> {aiStateText(ai || null)}
        </span>
        {status && (
          <span className={status.ac.install_dir ? '' : 'orange-text'}>
            AC: {status.ac.install_dir ? 'Installation gefunden' : 'Installation nicht gefunden'} · Setups: {status.ac.setups_found ? 'Ordner gefunden' : 'Ordner fehlt'}
          </span>
        )}
        <button onClick={() => setProviderOpen((o) => !o)}>{providerOpen ? 'KI-Anbieter schließen' : 'KI-Anbieter'}</button>
      </div>
      {error && (
        <div className="notice danger" role="alert">
          {error}
          <button aria-label="OK" onClick={() => setError('')}>
            <X size={13} />
          </button>
        </div>
      )}
      {providerOpen && ai && (
        <Panel title="KI-Anbieter und Zugangsdaten">
          <AiProviderPanel ai={ai} onChanged={loadStatus} onError={(e) => fail(e, 'provider')} />
        </Panel>
      )}

      <div className="setup-grid">
        <Panel title="1 · Fahrzeug und Strecke" className="setup-step">
          <select aria-label="Fahrzeug und Strecke" value={combo} onChange={(e) => setCombo(e.target.value)}>
            {[...groups.entries()].map(([key, g]) => (
              <option key={key} value={key}>
                {g.car} · {g.track}
                {g.demo ? ' · DEMO' : ''}
              </option>
            ))}
          </select>
          {car && (
            <div className="setup-spec" data-testid="setup-spec">
              {car.spec.kind === 'game' && <p className="green-text small">Einstellgrenzen aus den Fahrzeugdaten (data/setup.ini).</p>}
              {car.spec.kind === 'import' && <p className="green-text small">Einstellgrenzen aus importierter setup.ini.</p>}
              {(car.spec.kind === 'missing' || car.spec.kind === 'error') && (
                <div className="notice compact-hint">
                  <span>
                    {car.spec.kind === 'error'
                      ? 'Die setup.ini des Fahrzeugs ist nicht lesbar: ' + car.spec.error
                      : car.spec.packed
                        ? 'Die Fahrzeugdaten sind gepackt (data.acd). Grenzen und Kodierung sind unbekannt, deshalb kann nichts exportiert werden. Analyse und Empfehlungen funktionieren trotzdem.'
                        : car.spec.game_found
                          ? 'Für dieses Fahrzeug wurden keine Setup-Grenzen gefunden.'
                          : 'Assetto Corsa wurde nicht gefunden.'}{' '}
                    Abhilfe: die setup.ini des Fahrzeugs importieren, z. B. nach „Daten entpacken“ in Content Manager (nur wenn der Fahrzeugautor das erlaubt).
                  </span>
                  <label className="button">
                    <FolderInput size={13} /> setup.ini importieren
                    <input type="file" accept=".ini" hidden onChange={(e) => e.target.files?.[0] && importSpec(e.target.files[0])} />
                  </label>
                </div>
              )}
              <div className="setup-chips">
                {(Object.entries(car.counts) as Array<[ParamStatus, number]>).map(([k, n]) => (
                  <span key={k} className={'tag ' + statusTag(k)} title={STATUS_HINT[k]}>
                    {n} {STATUS_LABEL[k]}
                  </span>
                ))}
              </div>
              {car.spec.kind === 'import' && (
                <button
                  className="link-button"
                  onClick={() => run('spec', async () => {
                    await api(`/setup/spec?car=${encodeURIComponent(car.car)}`, { method: 'DELETE' })
                    await loadCar()
                  })}
                >
                  Importierte setup.ini entfernen
                </button>
              )}
            </div>
          )}
        </Panel>

        <Panel title="2 · Ausgangssetup" className="setup-step">
          <div className="setup-inline wrap">
            <select aria-label="Ausgangssetup" value={baseKey === 'upload' ? '' : baseKey} onChange={(e) => chooseBase(e.target.value)} disabled={!car}>
              <option value="">{car?.saved_setups.length ? 'Gespeichertes Setup wählen' : 'Keine gespeicherten Setups gefunden'}</option>
              {car?.saved_setups.map((s) => (
                <option key={s.folder + '/' + s.name} value={s.folder + '/' + s.name}>
                  {s.folder === 'generic' ? 'generic' : 'Strecke'} / {s.name} · {new Date(s.modified * 1000).toLocaleDateString('de-DE')}
                </option>
              ))}
            </select>
            <label className="button">
              <Upload size={13} /> INI importieren
              <input type="file" accept=".ini" hidden onChange={(e) => e.target.files?.[0] && uploadBase(e.target.files[0])} />
            </label>
          </div>
          {busy === 'base' && <p className="subtle">Setup wird gelesen …</p>}
          {base && (
            <p className="small" data-testid="base-info">
              <b>{base.version.name}</b> gespeichert als Ausgangsversion (bleibt unverändert). {base.params.filter((p) => p.exportable).length} von{' '}
              {base.params.filter((p) => p.in_setup).length} Parametern exportierbar
              {base.preserved.internal ? ` · ${base.preserved.internal} interne Einträge (z. B. CSP-Skripte) werden unverändert übernommen` : ''}
              {base.preserved.duplicates.length ? ` · doppelt und gesperrt: ${base.preserved.duplicates.join(', ')}` : ''}.
            </p>
          )}
          {base && (
            <details className="setup-details">
              <summary>Parameter, Grenzen und Kodierung ({base.params.length})</summary>
              <ParamTable params={base.params} showValues onConfirm={confirm} />
            </details>
          )}
          {!base && car && car.params.length > 0 && (
            <details className="setup-details">
              <summary>Bekannte Parameter des Fahrzeugs ({car.params.length})</summary>
              <ParamTable params={car.params} showValues={false} />
            </details>
          )}
        </Panel>

        <Panel title="3 · Runden" className="setup-step">
          <p className="small subtle">
            Mehrere gleichmäßige Runden wählen (höchstens {MAX_LAPS}). Out-/Inlaps, ungültige Runden, Unfälle, Datenlücken und Zeitausreißer werden bei
            der Analyse sichtbar ausgeschlossen.
          </p>
          <div className="setup-laps">
            {lapsBySession.map(({ s, laps }) => (
              <div key={s.id}>
                <div className="small subtle">
                  {new Date(s.created_at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })} · {s.meta.layout || s.meta.track} ·{' '}
                  {s.meta.compound || 'Reifen ?'}
                </div>
                <div className="setup-lap-list">
                  {laps.map((l) => (
                    <label key={l.id} className={'setup-lap' + (lapIds.includes(l.id) ? ' on' : '') + (l.valid ? '' : ' invalid')}>
                      <input type="checkbox" checked={lapIds.includes(l.id)} onChange={() => toggleLap(l.id)} aria-label={`Runde ${l.number}`} />
                      R{l.number} {lapTime(l.duration_ms)}
                      {!l.valid && ' *'}
                    </label>
                  ))}
                </div>
              </div>
            ))}
            {!lapsBySession.length && <p className="subtle">Keine vollständigen Runden für diese Kombination.</p>}
          </div>
          <p className="small subtle">
            {lapIds.length} ausgewählt · * = ungültig (Streckenbegrenzung)
          </p>
          <label className="setup-take">
            <input type="checkbox" aria-label="Ungültige Runden einbeziehen" checked={includeInvalid} onChange={(e) => setIncludeInvalid(e.target.checked)} />
            Ungültige Runden (Streckenbegrenzung) trotzdem einbeziehen - bleiben markiert; Box, Unfall, Dreher und Ausreißer bleiben ausgeschlossen
          </label>
        </Panel>

        <Panel title="4 · Ziel und Fahrverhalten" className="setup-step">
          <div className="setup-chips" role="radiogroup" aria-label="Ziel">
            {Object.entries(status?.goals || {}).map(([k, label]) => (
              <button key={k} role="radio" aria-checked={goal === k} className={goal === k ? 'active' : ''} onClick={() => setGoal(k)}>
                {label}
              </button>
            ))}
          </div>
          {goal === 'custom' && (
            <input aria-label="Eigenes Ziel" placeholder="Eigenes Ziel beschreiben" value={goalText} maxLength={300} onChange={(e) => setGoalText(e.target.value)} />
          )}
          <h4>Was stört dich?</h4>
          <div className="setup-inline wrap">
            <select aria-label="Problem" value={draft.issue} onChange={(e) => setDraft({ ...draft, issue: e.target.value })}>
              {Object.entries(status?.issues || {}).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <select aria-label="Wo" value={draft.phase} onChange={(e) => setDraft({ ...draft, phase: e.target.value })}>
              {Object.entries(status?.phases || {}).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <input aria-label="Kurven" placeholder="Kurven (optional)" value={draft.corners} maxLength={80} onChange={(e) => setDraft({ ...draft, corners: e.target.value })} />
            <select aria-label="Stärke" value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: Number(e.target.value) as 1 | 2 | 3 })}>
              <option value={1}>leicht</option>
              <option value={2}>deutlich</option>
              <option value={3}>stark</option>
            </select>
            <button disabled={items.length >= 8} onClick={() => setItems([...items, draft])}>
              <Plus size={13} /> Hinzufügen
            </button>
          </div>
          <ul className="setup-feedback">
            {items.map((it, i) => (
              <li key={i}>
                {status?.issues[it.issue]} · {status?.phases[it.phase]}
                {it.corners ? ` · ${it.corners}` : ''} · {['', 'leicht', 'deutlich', 'stark'][it.severity]}
                <button aria-label="Entfernen" onClick={() => setItems(items.filter((_, j) => j !== i))}>
                  <X size={12} />
                </button>
              </li>
            ))}
          </ul>
          <textarea
            aria-label="Beschreibung"
            placeholder="Freitext, z. B. „In schnellen Kurven schiebt das Auto, beim Anbremsen wird das Heck leicht“"
            maxLength={1000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </Panel>
      </div>

      {ai && ai.provider !== 'off' && !ai.consent && <Disclosure ai={ai} onAccept={acceptConsent} onPreview={showPreview} busy={!!busy || !ready} />}

      <div className="setup-actions">
        <button className="primary" disabled={!ready || !!busy || !ai?.ready} onClick={() => analyze('ai')} data-testid="analyze-ai">
          <Wrench size={14} /> {busy === 'analyze' ? 'Analysiere …' : 'Setup analysieren (KI)'}
        </button>
        <button disabled={!ready || !!busy} onClick={() => analyze('rules')} data-testid="analyze-rules">
          Regelbasiert analysieren (ohne KI)
        </button>
        <button disabled={!ready || !!busy} onClick={showPreview}>
          Daten-Vorschau
        </button>
        {!ready && <span className="small subtle">Ausgangssetup und mindestens eine Runde wählen.</span>}
        {ready && ai && !ai.ready && <span className="small orange-text">{aiStateText(ai)}</span>}
      </div>

      {preview && (
        <Panel
          title="Datenvorschau - genau das würde an die KI gesendet"
          actions={
            <button aria-label="Schließen" onClick={() => setPreview(null)}>
              <X size={13} />
            </button>
          }
        >
          <p className="small">
            Anbieter: {preview.provider.label || 'keiner eingestellt'} {preview.provider.endpoint ? `(${preview.provider.endpoint})` : ''} · Modell{' '}
            {preview.provider.model}
          </p>
          <pre className="setup-json" data-testid="setup-preview">
            {JSON.stringify(preview.request, null, 2)}
          </pre>
        </Panel>
      )}

      {analysis && (
        <section className="setup-result" data-testid="setup-result">
          <div className="analysis-summary">
            {analysis.status === 'rules' && <span className="tag yellow-tag">REGELBASIERT · KEINE KI</span>}
            {analysis.status === 'ok' && (
              <span className="tag cyan-tag">
                KI-ANALYSE · {analysis.model}
              </span>
            )}
            {analysis.status === 'error' && <span className="tag red-tag">KI NICHT VERFÜGBAR</span>}
            <span className="small subtle">{new Date(analysis.created_at).toLocaleString('de-DE')}</span>
          </div>
          {analysis.status === 'error' && (
            <div className="notice danger" role="alert" data-testid="setup-ai-error">
              <span>
                Die KI-Analyse ist fehlgeschlagen: {analysis.error}. Es wurde keine Empfehlung erzeugt.
              </span>
              <button className="button" onClick={() => analyze('rules')}>
                Regelbasierte Ersatzanalyse
              </button>
            </div>
          )}
          <div className="setup-grid">
            <Panel title="Datengrundlage" className="setup-step">
              <DataUsed kpi={analysis.kpis.kpis} />
            </Panel>
            <Panel title="Messung und Rückmeldung" className="setup-step">
              <EvidenceList evidence={analysis.kpis.evidence} />
              <details className="setup-details">
                <summary>Kennwerte</summary>
                <KpiTables kpi={analysis.kpis.kpis} />
              </details>
            </Panel>
          </div>
          {analysis.status !== 'error' && (
            <>
              <Panel title="Einschätzung">
                <p>{analysis.result.summary}</p>
                {analysis.result.observations.length > 0 && (
                  <ul>
                    {analysis.result.observations.map((o, i) => (
                      <li key={i}>
                        {o.text} <span className="small subtle">({o.basis === 'messung' ? 'Messung' : o.basis === 'rueckmeldung' ? 'Rückmeldung' : 'Messung + Rückmeldung'}, Sicherheit {o.certainty})</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
              <h3 className="setup-heading">Empfehlungen ({changes.length} von höchstens 3) - einzeln übernehmen</h3>
              {changes.length === 0 && <p className="subtle">Keine Änderung empfohlen. Gründe siehe Unsicherheiten.</p>}
              <div className="setup-recs">
                {changes.map((c) => (
                  <RecommendationCard
                    key={c.parameter}
                    rec={c}
                    selected={selected.includes(c.parameter)}
                    onToggle={() => setSelected((s) => (s.includes(c.parameter) ? s.filter((x) => x !== c.parameter) : [...s, c.parameter]))}
                  />
                ))}
              </div>
              {(analysis.result.driving_tips.length > 0 || analysis.result.uncertainties.length > 0) && (
                <div className="setup-grid">
                  {analysis.result.driving_tips.length > 0 && (
                    <Panel title="Fahrtechnik-Hinweise" className="setup-step">
                      <ul>
                        {analysis.result.driving_tips.map((t) => (
                          <li key={t}>{t}</li>
                        ))}
                      </ul>
                    </Panel>
                  )}
                  {analysis.result.uncertainties.length > 0 && (
                    <Panel title="Unsicherheiten" className="setup-step">
                      <ul>
                        {analysis.result.uncertainties.map((t) => (
                          <li key={t}>{t}</li>
                        ))}
                      </ul>
                    </Panel>
                  )}
                </div>
              )}
              {(analysis.result.dropped.length > 0 || analysis.result.validation_notes.length > 0) && (
                <details className="setup-details" data-testid="setup-dropped">
                  <summary>
                    Prüfung: {analysis.result.dropped.length} Vorschläge verworfen · {analysis.result.validation_notes.length} Hinweise
                  </summary>
                  <ul>
                    {analysis.result.dropped.map((d, i) => (
                      <li key={i}>
                        {d.label} ({d.parameter}) → {fmt(d.new_value)}: {d.reason}
                      </li>
                    ))}
                    {analysis.result.validation_notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </details>
              )}

              <Panel title="Export als neue Setup-Datei">
                {chosen.length === 0 ? (
                  <p className="subtle">Keine exportierbare Änderung ausgewählt.</p>
                ) : (
                  <>
                    <div className="table-scroll">
                      <table data-testid="setup-before-after">
                        <thead>
                          <tr>
                            <th>Parameter</th>
                            <th>Vorher</th>
                            <th>Nachher</th>
                            <th>INI</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(exported?.before_after || chosen).map((c) => (
                            <tr key={c.parameter}>
                              <td>{c.label}</td>
                              <td>{fmt(c.old_value, c.unit)}</td>
                              <td>
                                <b>{fmt(c.new_value, c.unit)}</b>
                              </td>
                              <td className="mono small">
                                VALUE={c.old_raw} → {c.new_raw}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="setup-inline wrap">
                      <input aria-label="Dateiname" placeholder={`${base?.version.name.replace(/\.ini$/i, '') || 'setup'} stintpulse`} value={exportName} maxLength={60} onChange={(e) => setExportName(e.target.value)} />
                      <button className="primary" disabled={!!busy || !!exported} onClick={doExport} data-testid="setup-export">
                        <ArrowDownToLine size={13} /> Exportieren und herunterladen
                      </button>
                    </div>
                    <p className="small subtle">
                      Alle anderen Einträge der Ausgangsdatei bleiben byte-genau erhalten. Das Ausgangssetup wird nie überschrieben.
                    </p>
                  </>
                )}
                {exported && (
                  <div className="setup-saved" data-testid="setup-exported">
                    <p className="green-text">
                      Version „{exported.file_name}“ gespeichert und heruntergeladen.{' '}
                      <button className="link-button" onClick={() => download(`/setup/versions/${exported.version.id}/file`, exported.file_name).catch((e) => fail(e, 'download'))}>
                        Erneut herunterladen
                      </button>
                    </p>
                    {status?.ai.pc ? (
                      <div className="setup-inline wrap">
                        <span className="small">Optional in den AC-Setupordner speichern:</span>
                        <select aria-label="Zielordner" value={saveFolder} onChange={(e) => setSaveFolder(e.target.value as 'track' | 'generic')}>
                          <option value="track">Strecke ({group?.track})</option>
                          <option value="generic">generic</option>
                        </select>
                        <button disabled={!!busy || !!saved} onClick={saveToAc}>
                          <Save size={13} /> Speichern
                        </button>
                      </div>
                    ) : (
                      <p className="small subtle">Speichern in den AC-Setupordner ist nur direkt am PC möglich.</p>
                    )}
                    {saved && <p className="small green-text">Gespeichert: {saved}</p>}
                    <p className="small subtle">
                      Das Setup wird nicht automatisch im Spiel geladen. Im Spiel unter Setup → Laden auswählen, dann ein paar Runden fahren und die Session
                      unten dieser Version zuordnen.
                    </p>
                  </div>
                )}
              </Panel>
            </>
          )}
        </section>
      )}

      {group && (
        <Panel title="Versionen und Vergleich">
          <SetupVersions
            car={group.car}
            track={group.track}
            sessions={group.sessions}
            refresh={refresh}
            includeInvalid={includeInvalid}
            issues={status?.issues || {}}
            onError={(e) => fail(e, 'versions')}
          />
        </Panel>
      )}
    </div>
  )
}
