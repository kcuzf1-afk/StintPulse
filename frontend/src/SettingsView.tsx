import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { api } from './api'
import type { Diagnostics, Settings, VersionInfo } from './types'
import type { SettingsTab } from './routes'
import { fromPressure, fromTemp, toPressure, toTemp, units, type Units } from './format'
import { friendlyError, logError } from './errors'
import DiagnosticsView from './DiagnosticsView'
import { pickMime, recDetail, recView, type RecStatus } from './recorder'
import { audioLabel } from './playback'

interface VideoStorage {
  dir: string
  default_dir: string
  used_mb: number
  limit_mb: number
  free_mb: number | null
  count: number
  delete_oldest: boolean
}
const gb = (mb: number | null | undefined) =>
  mb === null || mb === undefined ? '—' : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`

const TABS: Array<[SettingsTab, string, string]> = [
  ['general', 'Allgemein', 'General'],
  ['data', 'Daten und Speicherung', 'Data and storage'],
  ['onboard', 'Onboard und HUD', 'Onboard and HUD'],
  ['network', 'Netzwerk', 'Network'],
  ['advanced', 'Erweitert / Diagnose', 'Advanced / diagnostics'],
]

export default function SettingsView({
  settings,
  onSaved,
  tab,
  onTab,
  socketState,
  onRecheck,
  recording,
  onRetryRecording,
}: {
  settings: Settings
  onSaved: (s: Settings) => void
  tab: SettingsTab
  onTab: (t: SettingsTab) => void
  socketState: string
  onRecheck: () => Promise<Diagnostics | null>
  recording?: RecStatus
  onRetryRecording?: () => void
}) {
  const [draft, setDraft] = useState(settings),
    [message, setMessage] = useState<{ text: string; bad: boolean } | null>(null),
    [busy, setBusy] = useState(false),
    [access, setAccess] = useState<{ code: string; lan: boolean; urls: string[] } | null>(null),
    [accessError, setAccessError] = useState('')
  const de = draft.language !== 'en',
    tr = (a: string, b: string) => (de ? a : b)
  const u: Units = draft.units
  useEffect(() => {
    api<{ code: string; lan: boolean; urls: string[] }>('/access-code')
      .then(setAccess)
      .catch(() => setAccess(null))
  }, [])
  const [version, setVersion] = useState<VersionInfo | null>(null)
  useEffect(() => {
    if (tab !== 'general') return
    api<VersionInfo>('/version')
      .then(setVersion)
      .catch(() => setVersion(null))
  }, [tab, settings.update_check])
  const [storage, setStorage] = useState<VideoStorage | null>(null)
  useEffect(() => {
    if (tab !== 'onboard') return
    let active = true
    const load = () =>
      api<VideoStorage>('/recordings/storage')
        .then((s) => active && setStorage(s))
        .catch(() => {})
    load()
    const id = setInterval(load, 5000)
    return () => {
      active = false
      clearInterval(id)
    }
  }, [tab, settings])
  const recordMime =
    typeof MediaRecorder !== 'undefined' ? pickMime((t) => MediaRecorder.isTypeSupported(t)) : null
  async function newCode() {
    try {
      setAccess(await api('/access-code', { method: 'POST' }))
      setAccessError('')
    } catch (e) {
      logError(e, 'settings: access code')
      setAccessError(friendlyError(e, de))
    }
  }
  const set = <K extends keyof Settings>(key: K, val: Settings[K]) => setDraft((d) => ({ ...d, [key]: val }))
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings)
  async function save() {
    setBusy(true)
    setMessage(null)
    try {
      const { token_set, ...data } = draft
      void token_set
      const res = await api<{ settings: Settings; restart_required: boolean }>('/settings', {
        method: 'PATCH',
        body: JSON.stringify(data),
      })
      onSaved(res.settings)
      setDraft(res.settings)
      setMessage({
        text: res.restart_required
          ? tr('Gespeichert. Für LAN oder Port die App neu starten.', 'Saved. Restart the app for LAN or port changes.')
          : tr('Einstellungen gespeichert.', 'Settings saved.'),
        bad: false,
      })
    } catch (e) {
      logError(e, 'settings: save')
      setMessage({ text: friendlyError(e, de) + ' ' + String(e).replace(/^Error:\s*/, ''), bad: true })
    } finally {
      setBusy(false)
    }
  }
  function numeric(key: keyof Settings, label: string, min: number, max: number, step = 1, nullable = false) {
    return (
      <label className="form-field" key={key}>
        <span>{label}</span>
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={(draft[key] ?? '') as string | number}
          onChange={(e) => set(key, (e.target.value === '' && nullable ? null : Number(e.target.value)) as never)}
        />
      </label>
    )
  }
  /** Stored in °C / psi, shown and edited in the chosen units. */
  function unitNumeric(key: 'temp_cold' | 'temp_hot' | 'pressure_low' | 'pressure_high', label: string) {
    const isTemp = key.startsWith('temp')
    const shown = isTemp ? toTemp(draft[key], u) : toPressure(draft[key], u)
    const unit = isTemp ? units.temp(u) : units.pressure(u)
    return (
      <label className="form-field" key={key}>
        <span>
          {label} ({unit})
        </span>
        <input
          type="number"
          step={isTemp ? 1 : u === 'imperial' ? 0.1 : 0.01}
          value={shown === null ? '' : Number(shown.toFixed(isTemp ? 1 : 2))}
          onChange={(e) => {
            const v = Number(e.target.value)
            set(key, isTemp ? fromTemp(v, u) : fromPressure(v, u))
          }}
        />
      </label>
    )
  }
  function toggle(key: keyof Settings, label: string, info?: string) {
    return (
      <label className="toggle-row" key={key}>
        <span>
          {label}
          {info && <small>{info}</small>}
        </span>
        <input type="checkbox" checked={!!draft[key]} onChange={(e) => set(key, e.target.checked as never)} />
      </label>
    )
  }
  return (
    <div className="settings-view">
      <div className="page-heading">
        <h2>{tr('Einstellungen', 'Settings')}</h2>
        <button className="primary" onClick={save} disabled={busy || !dirty}>
          <Save size={15} />
          {dirty ? tr('Speichern', 'Save') : tr('Gespeichert', 'Saved')}
        </button>
      </div>
      <nav className="settings-tabs" role="tablist" aria-label={tr('Einstellungsbereiche', 'Settings sections')}>
        {TABS.map(([id, labelDe, labelEn]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => onTab(id)}>
            {de ? labelDe : labelEn}
          </button>
        ))}
      </nav>
      {message && (
        <div className={'notice' + (message.bad ? ' danger' : '')} role="status">
          {message.text}
        </div>
      )}
      {tab === 'general' && (
        <section className="panel settings-section">
          <div className="form-grid">
            <label className="form-field">
              <span>{tr('Sprache', 'Language')}</span>
              <select value={draft.language} onChange={(e) => set('language', e.target.value as 'de' | 'en')}>
                <option value="de">Deutsch</option>
                <option value="en">English</option>
              </select>
            </label>
            <label className="form-field">
              <span>{tr('Einheiten', 'Units')}</span>
              <select value={draft.units} onChange={(e) => set('units', e.target.value as Units)}>
                <option value="metric">{tr('Metrisch (km/h, °C, bar, L)', 'Metric (km/h, °C, bar, L)')}</option>
                <option value="imperial">{tr('Imperial (mph, °F, psi, gal)', 'Imperial (mph, °F, psi, gal)')}</option>
              </select>
            </label>
            <label className="form-field">
              <span>{tr('Datenquelle', 'Data source')}</span>
              <select value={draft.source} onChange={(e) => set('source', e.target.value as 'ac' | 'demo')}>
                <option value="ac">Assetto Corsa</option>
                <option value="demo">{tr('Demo (synthetische Daten)', 'Demo (synthetic data)')}</option>
              </select>
            </label>
          </div>
          {toggle('open_browser', tr('Dashboard beim Start öffnen', 'Open dashboard on startup'))}
          {toggle(
            'start_with_windows',
            tr('Mit Windows starten', 'Start with Windows'),
            tr('Nur in der Windows-EXE verfügbar.', 'Only available in the Windows EXE.'),
          )}
          {toggle(
            'update_check',
            tr('Auf neue Versionen prüfen', 'Check for new versions'),
            tr(
              'Fragt höchstens alle 6 Stunden bei GitHub nach einer neueren StintPulse-Version. Es werden keine Daten übertragen und nichts automatisch installiert.',
              'Asks GitHub at most every 6 hours for a newer StintPulse release. No data is sent and nothing is installed automatically.',
            ),
          )}
          <p className="subtle small">
            {tr(
              'Alle Daten bleiben auf diesem PC. Kein Cloud-Dienst, kein Telemetrie-Upload; der Coach nutzt lokale Regeln.',
              'All data stays on this PC. No cloud service, no telemetry upload; the coach uses local rules.',
            )}
          </p>
          <div className="about" data-testid="about">
            <img src="/icon.svg" alt="" />
            <div>
              <strong>
                StintPulse {version?.version || ''}
              </strong>
              <span className="subtle small">
                {!version
                  ? '…'
                  : version.update
                    ? tr(`Neue Version ${version.update.version} verfügbar. `, `New version ${version.update.version} available. `)
                    : version.update_check
                      ? version.error
                        ? tr('Update-Prüfung gerade nicht möglich (offline?).', 'Update check not possible right now (offline?).')
                        : tr('Du hast die aktuelle Version.', 'You have the latest version.')
                      : tr('Update-Prüfung aus.', 'Update check off.')}
                {version?.download_url && (
                  <>
                    {' '}
                    <a href={version.update?.url || version.download_url} target="_blank" rel="noreferrer">
                      {tr('Download-Seite', 'Download page')}
                    </a>
                  </>
                )}
              </span>
              <span className="subtle small">
                {tr(
                  'StintPulse ist ein unabhängiges Projekt und nicht mit Kunos Simulazioni verbunden. Assetto Corsa ist eine Marke von Kunos Simulazioni. MIT-Lizenz; Hinweise zu Drittkomponenten liegen dem Programm bei.',
                  'StintPulse is an independent project, not affiliated with Kunos Simulazioni. Assetto Corsa is a trademark of Kunos Simulazioni. MIT license; third-party notices are included.',
                )}
              </span>
            </div>
          </div>
        </section>
      )}
      {tab === 'data' && (
        <section className="panel settings-section">
          {toggle('auto_save', tr('Runden automatisch speichern', 'Save laps automatically'))}
          <div className="toggle-row">
            <span>
              {tr('Referenz für Delta und HUD', 'Reference for delta and HUD')}
              <small>
                {draft.reference_lap_id
                  ? tr('Feste Runde aus der Analyse gewählt.', 'Fixed lap chosen in Analysis.')
                  : tr('Automatisch: persönliche Bestzeit.', 'Automatic: personal best.')}
              </small>
            </span>
            {draft.reference_lap_id && (
              <button onClick={() => set('reference_lap_id', null)}>{tr('Auf Bestzeit zurücksetzen', 'Reset to personal best')}</button>
            )}
          </div>
          <h4>{tr('Reifen-Grenzwerte für Warnungen', 'Tyre limits for warnings')}</h4>
          <div className="form-grid">
            {unitNumeric('temp_cold', tr('Kalt unter', 'Cold below'))}
            {unitNumeric('temp_hot', tr('Heiß über', 'Hot above'))}
            {unitNumeric('pressure_low', tr('Druck niedrig unter', 'Pressure low below'))}
            {unitNumeric('pressure_high', tr('Druck hoch über', 'Pressure high above'))}
          </div>
          <p className="subtle small">
            {tr(
              'Konfigurierbarer Startbereich, kein allgemeines Reifenoptimum. Sicherung und Import findest du unter Sessions.',
              'Configurable starting range, not a universal tyre optimum. Backup and import are under Sessions.',
            )}
          </p>
        </section>
      )}
      {tab === 'onboard' && (
        <section className="panel settings-section">
          <div className="form-grid">
            <label className="form-field">
              <span>{tr('Videoquelle', 'Video source')}</span>
              <select value={draft.video_mode} onChange={(e) => set('video_mode', e.target.value as Settings['video_mode'])}>
                <option value="none">{tr('Keine', 'None')}</option>
                <option value="camera">OBS Virtual Camera / Webcam</option>
                <option value="screen">{tr('Fenster freigeben', 'Share window')}</option>
                <option value="url">{tr('Video-URL', 'Video URL')}</option>
                <option value="file">{tr('Lokale Aufnahme', 'Local recording')}</option>
              </select>
            </label>
            {numeric('video_offset_s', tr('Video-Versatz (Sekunden)', 'Video offset (seconds)'), -86400, 86400, 0.1)}
            {numeric('hud_hold_s', tr('HUD: abgeschlossene Runde anzeigen (Sekunden)', 'HUD: show completed lap (seconds)'), 1, 30, 0.5)}
            <label className="form-field wide">
              <span>{tr('Video-URL (MP4/WebM, direkt abspielbar)', 'Video URL (MP4/WebM, directly playable)')}</span>
              <input type="url" value={draft.video_url} onChange={(e) => set('video_url', e.target.value)} />
            </label>
            <label className="form-field wide">
              <span>{tr('Reifen-Kürzel (optional, manuell): je Zeile „Name im Spiel = Kürzel“', 'Tyre labels (optional, manual): one "game name = label" per line')}</span>
              <textarea
                aria-label={tr('Manuelle Reifenzuordnung', 'Manual tyre mapping')}
                rows={3}
                defaultValue={Object.entries(draft.compound_map || {})
                  .map(([k, v]) => `${k} = ${v}`)
                  .join('\n')}
                placeholder="Semislick (SM) = M"
                onChange={(e) =>
                  set(
                    'compound_map',
                    Object.fromEntries(
                      e.target.value
                        .split('\n')
                        .map((line) => line.split('='))
                        .filter((p) => p.length === 2 && p[0].trim() && p[1].trim())
                        .map(([k, v]) => [k.trim(), v.trim()]),
                    ),
                  )
                }
              />
            </label>
          </div>
          <p className="subtle small">
            {tr(
              'Ohne Zuordnung zeigt das HUD nur das Kürzel, das das Spiel selbst in Klammern liefert; zugeordnete Kürzel sind mit * markiert. HUD-Größe, -Position und Deckkraft stellst du direkt im Onboard ein (gilt pro Gerät). Video-Versatz: Videoposition = Telemetriezeit + Versatz; positiv, wenn das Bild der Telemetrie hinterherläuft (OBS-Verzögerung).',
              'Without a mapping the HUD shows only the code the game puts in brackets; mapped labels are marked with *. HUD size, position and opacity are set directly in Onboard (per device). Video offset: video position = telemetry time + offset; positive when the picture lags behind telemetry (OBS latency).',
            )}
          </p>
        </section>
      )}
      {tab === 'onboard' && (
        <section className="panel settings-section" data-testid="recording-settings">
          <h3>{tr('Onboard-Aufnahme', 'Onboard recording')}</h3>
          {toggle(
            'record_auto',
            tr('Onboard automatisch aufnehmen', 'Record onboard automatically'),
            tr(
              'Startet bei einer aktiven Fahrt, sobald die Videoquelle läuft, und speichert Video und Telemetrie zusammen. Eine Pause beendet die Aufnahme nicht.',
              'Starts during an active drive as soon as the video source runs and stores video together with telemetry. A pause does not end the recording.',
            ),
          )}
          {recording && draft.record_auto && (
            <div className={'record-state tone-' + recView(recording, Date.now()).tone} data-testid="record-state">
              <strong>{de ? recView(recording, Date.now()).de : recView(recording, Date.now()).en}</strong>
              {recording.detail && <span>{recDetail(recording.detail, de)}</span>}
              {recording.state === 'recording' && recording.audio && (
                <span data-testid="record-audio">{audioLabel(recording.audio, de).text}</span>
              )}
              {recording.state === 'failed' && onRetryRecording && (
                <button onClick={onRetryRecording}>{tr('Erneut versuchen', 'Try again')}</button>
              )}
            </div>
          )}
          <div className="form-grid">
            <label className="form-field">
              <span>{tr('Aufnahmequalität (Bitrate)', 'Recording quality (bitrate)')}</span>
              <select
                value={draft.record_quality || 'standard'}
                onChange={(e) => set('record_quality', e.target.value as Settings['record_quality'])}
              >
                <option value="saver">{tr('Sparsam – 3 Mbit/s (≈ 1,3 GB/h)', 'Saver – 3 Mbit/s (≈ 1.3 GB/h)')}</option>
                <option value="standard">{tr('Standard – 6 Mbit/s (≈ 2,7 GB/h)', 'Standard – 6 Mbit/s (≈ 2.7 GB/h)')}</option>
                <option value="high">{tr('Hoch – 12 Mbit/s (≈ 5,4 GB/h)', 'High – 12 Mbit/s (≈ 5.4 GB/h)')}</option>
              </select>
            </label>
            <label className="form-field">
              <span>{tr('Ton aufnehmen', 'Record sound')}</span>
              <select
                value={draft.record_audio || 'game'}
                onChange={(e) => set('record_audio', e.target.value as Settings['record_audio'])}
              >
                <option value="game">{tr('Spielsound (nur Assetto Corsa)', 'Game sound (Assetto Corsa only)')}</option>
                <option value="system">{tr('Gesamter PC-Ton (auch Discord, Musik …)', 'All PC sound (incl. Discord, music …)')}</option>
                <option value="off">{tr('Kein Ton', 'No sound')}</option>
              </select>
            </label>
            {numeric('record_storage_mb', tr('Speicherlimit für Videos (MB)', 'Storage limit for videos (MB)'), 1024, 4000000, 1024)}
            <label className="form-field wide">
              <span>{tr('Speicherordner (leer = Standardordner; nur am PC änderbar)', 'Folder (empty = default folder; changeable on the PC only)')}</span>
              <input
                type="text"
                value={draft.record_dir || ''}
                placeholder={storage?.default_dir || ''}
                onChange={(e) => set('record_dir', e.target.value)}
              />
            </label>
          </div>
          {toggle(
            'record_delete_oldest',
            tr('Älteste Videos automatisch löschen, wenn das Limit erreicht ist', 'Delete the oldest videos automatically when the limit is reached'),
            tr(
              'Aus (Standard): Bei vollem Limit stoppt die Aufnahme mit einer Meldung, es wird nichts gelöscht. Favorisierte Sessions und die laufende Session werden nie automatisch gelöscht.',
              'Off (default): when the limit is full, recording stops with a message and nothing is deleted. Favourite sessions and the running session are never deleted automatically.',
            ),
          )}
          <p className="small" data-testid="video-storage">
            {storage
              ? tr(
                  `Belegt: ${gb(storage.used_mb)} von ${gb(storage.limit_mb)} · ${storage.count} Aufnahmeabschnitte · frei auf dem Laufwerk: ${gb(storage.free_mb)} · Ordner: ${storage.dir}`,
                  `Used: ${gb(storage.used_mb)} of ${gb(storage.limit_mb)} · ${storage.count} recording segments · free on drive: ${gb(storage.free_mb)} · folder: ${storage.dir}`,
                )
              : '…'}
          </p>
          <p className="subtle small">
            {tr(
              `So funktioniert es: Dieser Browser-Tab auf dem PC nimmt das Bild der gewählten Videoquelle (OBS Virtual Camera) auf – ohne HUD – und überträgt es alle 2 Sekunden an das PC-Programm, das es sofort auf die Festplatte schreibt. Der Tab muss während der Fahrt geöffnet bleiben (er darf im Hintergrund liegen); nach dem Schließen des Browsers wird nicht weiter aufgenommen. Auflösung und Bildrate kommen von OBS. Format: WebM (${recordMime || 'in diesem Browser nicht verfügbar'}); nach der Aufnahme wird die Datei ohne Neukodierung für schnelles Spulen indiziert. Ton: Die OBS-Kamera überträgt keinen Ton – das PC-Programm nimmt ihn selbst aus Windows auf (Spielsound = nur Assetto Corsa, ohne Discord/Musik; Windows 10 2004 oder neuer) und speichert ihn als WAV neben dem Video (≈ 0,7 GB/h). Bei der Wiedergabe läuft er synchron zum Bild. Videos sind nicht im Datenbank-Backup enthalten.`,
              `How it works: this browser tab on the PC records the picture of the selected video source (OBS Virtual Camera) – without HUD – and sends it to the PC app every 2 seconds, which writes it to disk at once. Keep the tab open while driving (it may be in the background); nothing is recorded after the browser was closed. Resolution and frame rate come from OBS. Format: WebM (${recordMime || 'not available in this browser'}); afterwards the file is indexed for fast seeking without re-encoding. Sound: the OBS camera carries no sound – the PC app records it itself from Windows (game sound = Assetto Corsa only, without Discord/music; Windows 10 2004 or newer) and stores it as WAV next to the video (≈ 0.7 GB/h). Playback keeps it in sync with the picture. Videos are not part of the database backup.`,
            )}
          </p>
        </section>
      )}
      {tab === 'network' && (
        <section className="panel settings-section">
          {toggle('lan', tr('Zugriff im lokalen Netzwerk (Handy, Tablet)', 'Access in the local network (phone, tablet)'))}
          <div className="form-grid">{numeric('port', 'Port', 1024, 65535)}</div>
          <div className="access-code" data-testid="access-code">
            <span>{tr('Zugriffscode für Handy/Tablet', 'Access code for phone/tablet')}</span>
            {access ? (
              <>
                <b className="mono">
                  {access.code.slice(0, 4)} {access.code.slice(4)}
                </b>
                <button onClick={newCode}>{tr('Neuen Code erzeugen', 'Generate new code')}</button>
                {access.urls.length > 0 && (
                  <small>
                    {tr('Auf dem Handy (gleiches WLAN) öffnen: ', 'Open on the phone (same Wi-Fi): ')}
                    {access.urls.map((url) => (
                      <code key={url}>{url} </code>
                    ))}
                  </small>
                )}
              </>
            ) : (
              <small>{accessError || tr('Der Code wird nur am PC selbst angezeigt.', 'The code is only shown on the PC.')}</small>
            )}
          </div>
          <p className="subtle small">
            {tr(
              'LAN und Port wirken nach einem Neustart der App. Der PC selbst braucht keinen Code. Nach 10 verschiedenen falschen Codes wird ein Gerät 60 s gesperrt. HTTP ist unverschlüsselt; für HTTPS die App mit --cert und --key starten.',
              'LAN and port apply after restarting the app. The PC itself needs no code. After 10 different wrong codes a device is locked for 60 s. HTTP is unencrypted; start with --cert and --key for HTTPS.',
            )}
          </p>
        </section>
      )}
      {tab === 'advanced' && (
        <>
          <section className="panel settings-section">
            <h4>{tr('Erfassung und Speicher', 'Capture and storage')}</h4>
            <div className="form-grid">
              {numeric('capture_hz', tr('Erfassungsfrequenz (Hz)', 'Capture rate (Hz)'), 20, 120)}
              {numeric('broadcast_hz', tr('Live-Übertragung (Hz)', 'Live broadcast (Hz)'), 10, 30)}
              {numeric('storage_mb', tr('Speicherlimit (MiB)', 'Storage limit (MiB)'), 64, 100000)}
            </div>
            <h4>{tr('Erkennungsschwellen und Fahrzeugkalibrierung', 'Detection thresholds and vehicle calibration')}</h4>
            <div className="form-grid">
              {numeric('brake_threshold', tr('Bremsbeginn ab Pedal (0–1)', 'Brake onset from pedal (0–1)'), 0.01, 0.8, 0.01)}
              {numeric('full_throttle', tr('Vollgas ab Pedal (0–1)', 'Full throttle from pedal (0–1)'), 0.8, 1, 0.01)}
              {numeric('lock_ratio', tr('Blockieren: Rad/Fahrzeug unter', 'Locking: wheel/car below'), 0.1, 0.95, 0.01)}
              {numeric('spin_ratio', tr('Durchdrehen: Rad/Fahrzeug über', 'Wheelspin: wheel/car above'), 1.05, 3, 0.01)}
              {numeric('slip_angle_deg', tr('Fahrzeugschlupf (°)', 'Body slip (°)'), 2, 30)}
              {numeric('steering_lock_deg', tr('Lenkradwinkel bei Eingabe 1 (°)', 'Steering wheel angle at input 1 (°)'), 1, 1500, 1, true)}
              {numeric('wheelbase_m', tr('Radstand (m)', 'Wheelbase (m)'), 1, 6, 0.01, true)}
              {numeric('steering_ratio', tr('Lenkübersetzung', 'Steering ratio'), 1, 40, 0.1, true)}
            </div>
            <div className="color-options">
              <span className="subtle small">{tr('Farben der Live-Telemetrie', 'Live telemetry colours')}</span>
              {Object.entries(draft.graph_colors).map(([key, col]) => (
                <label key={key}>
                  <input type="color" value={col} onChange={(e) => set('graph_colors', { ...draft.graph_colors, [key]: e.target.value })} />
                  {key}
                </label>
              ))}
            </div>
          </section>
          <DiagnosticsView de={de} socketState={socketState} onRecheck={onRecheck} />
        </>
      )}
    </div>
  )
}
