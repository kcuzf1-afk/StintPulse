/** Live dashboard: the current drive at a glance (former Engineering + Live mode). */
import { Gauge, Play, RotateCcw, Stethoscope, Video } from 'lucide-react'
import TelemetryChart from './TelemetryChart'
import type { Frame, Sample, Settings, Track } from './types'
import { available, delta, lapTime, speed, toFuel, units, value, type Units } from './format'
import { formatClock, type TelemetryView } from './connection'
import { statusHint } from './status'
import { Kpi, Panel, SectorTimes, TrackMapPanel, TyreGrid, vehicleWarnings, WarningList } from './widgets'

export const SESSION_TYPES_DE = ['Training', 'Qualifying', 'Rennen', 'Hotlap', 'Zeitfahren', 'Drift', 'Drag']
export const SESSION_TYPES_EN = ['Practice', 'Qualifying', 'Race', 'Hotlap', 'Time attack', 'Drift', 'Drag']

export default function LiveDashboard({
  frame,
  settings,
  telemetry,
  history,
  track,
  de,
  onOpenOnboard,
  onToggleDemo,
  onRecheck,
  onDiagnostics,
}: {
  frame: Frame
  settings: Settings | null
  telemetry: TelemetryView
  history: Sample[]
  track: Track | null
  de: boolean
  onOpenOnboard: () => void
  onToggleDemo: () => void
  onRecheck: () => void
  onDiagnostics: () => void
}) {
  const tr = (a: string, b: string) => (de ? a : b)
  const u: Units = settings?.units || 'metric'
  const imperial = u === 'imperial'
  const s = frame.sample,
    meta = frame.meta,
    ch = s?.channels || {}
  const demo = frame.source === 'demo' || meta?.source === 'demo'
  const hint = statusHint(frame.status, de)
  const warnings = vehicleWarnings(frame, de)
  const best = frame.personal_best_ms ?? (frame.statistics.best_s ? frame.statistics.best_s * 1000 : null)
  const sessionType = meta ? (de ? SESSION_TYPES_DE : SESSION_TYPES_EN)[meta.session_type] : undefined
  const rpmShare = available(ch.rpm) && meta?.max_rpm ? ch.rpm! / meta.max_rpm : 0
  return (
    <div className={'live-dashboard' + (telemetry.frozen ? ' frozen' : '')} data-testid="live-dashboard">
      <section className="session-strip" data-testid="session-strip">
        <div className="session-facts">
          <strong>{meta?.driver || tr('Kein Fahrer', 'No driver')}</strong>
          <span>{meta?.car || '—'}</span>
          <span>
            {meta?.track || tr('Keine Strecke', 'No track')}
            {meta?.layout ? ' / ' + meta.layout : ''}
          </span>
          <span>{sessionType || '—'}</span>
          {demo && <span className="tag orange-tag" data-testid="demo-tag">DEMO · {tr('synthetische Daten', 'synthetic data')}</span>}
          {frame.recording && <span className="tag red-tag">{tr('AUFZEICHNUNG', 'RECORDING')}</span>}
        </div>
        <div className="session-actions-row">
          <button onClick={onOpenOnboard}>
            <Video size={14} />
            {tr('Onboard öffnen', 'Open onboard')}
          </button>
          <button className={settings?.source === 'demo' ? 'primary' : ''} onClick={onToggleDemo}>
            <Play size={13} />
            {settings?.source === 'demo' ? tr('AC verbinden', 'Connect AC') : tr('Demo starten', 'Start demo')}
          </button>
        </div>
      </section>
      {telemetry.frozen && s && (
        <div className="notice stale-notice" role="status" data-testid="stale-notice">
          {telemetry.key === 'paused'
            ? tr(`Spiel pausiert – Werte vom ${formatClock(telemetry.lastAt)}.`, `Game paused – values from ${formatClock(telemetry.lastAt)}.`)
            : tr(
                `Letzter Stand ${formatClock(telemetry.lastAt)} – keine aktuellen Live-Daten.`,
                `Last values ${formatClock(telemetry.lastAt)} – no current live data.`,
              )}
        </div>
      )}
      {hint && (
        <div className={'notice' + (['access_denied', 'decode_error', 'memory_error', 'capture_failed'].includes(frame.status) ? ' danger' : '')} data-testid="status-hint">
          <span>
            {hint}
            {frame.source_message ? ' (' + frame.source_message + ')' : ''}
          </span>
          <span className="button-row">
            {settings?.source !== 'demo' && (
              <button onClick={onRecheck}>
                <RotateCcw size={13} />
                {tr('Verbindung erneut prüfen', 'Re-check connection')}
              </button>
            )}
            <button onClick={onDiagnostics}>
              <Stethoscope size={13} /> {tr('Diagnose', 'Diagnostics')}
            </button>
          </span>
        </div>
      )}
      <div className="kpi-row">
        <Kpi
          testId="kpi-current"
          label={tr('Aktuelle Runde', 'Current lap')}
          val={lapTime(s?.lap_ms, true)}
          detail={s ? tr('Runde ', 'Lap ') + (s.completed_laps + 1) : '—'}
        />
        <Kpi label={tr('Persönliche Bestzeit', 'Personal best')} val={lapTime(best)} color="violet-text" detail={tr('gültige Runden', 'valid laps')} />
        <Kpi label={tr('Letzte Runde', 'Last lap')} val={lapTime(s?.last_lap_ms)} detail={tr('offizielle Zeit', 'official time')} />
        <Kpi
          label={tr('Delta zur Referenz', 'Delta to reference')}
          val={delta(frame.delta_s)}
          unit="s"
          color={frame.delta_s === null ? '' : frame.delta_s <= 0 ? 'green-text' : 'orange-text'}
          detail={frame.reference_ms ? `REF ${lapTime(frame.reference_ms)}` : tr('keine Referenz', 'no reference')}
        />
        <Kpi
          label={tr('Kraftstoff', 'Fuel')}
          val={value(toFuel(ch.fuel, u), 1)}
          unit={units.fuel(u)}
          detail={
            available(frame.fuel_laps_est)
              ? tr(`≈ ${value(frame.fuel_laps_est, 1)} Runden`, `≈ ${value(frame.fuel_laps_est, 1)} laps`)
              : tr('Reichweite: nicht verfügbar', 'Range: not available')
          }
        />
      </div>
      <div className="driving-strip" data-testid="driving-strip">
        <div className="speed-readout">
          <Gauge size={18} />
          <b>{speed(ch.speed, imperial)}</b>
          <small>{units.speed(u)}</small>
        </div>
        <div className="gear-readout">
          <span>{tr('Gang', 'Gear')}</span>
          <b>{ch.gear === 0 ? 'N' : ch.gear === -1 ? 'R' : value(ch.gear)}</b>
        </div>
        <div className="rpm-readout">
          <span>
            {tr('Drehzahl', 'RPM')} <b>{value(ch.rpm)}</b>
          </span>
          <div className="rpm-bars">
            {Array.from({ length: 20 }, (_, i) => (
              <i key={i} className={rpmShare > i / 20 ? (i > 15 ? 'lit red' : i > 11 ? 'lit orange' : 'lit cyan') : ''} />
            ))}
          </div>
        </div>
        {(
          [
            ['gas', tr('Gas', 'Throttle'), 'green'],
            ['brake', tr('Bremse', 'Brake'), 'red'],
          ] as const
        ).map(([key, label, col]) => (
          <div className="pedal" key={key}>
            <span>
              {label} <b>{value(available(ch[key]) ? ch[key]! * 100 : null)} %</b>
            </span>
            <div>
              <i className={col} style={{ width: Math.max(0, Math.min(100, (ch[key] || 0) * 100)) + '%' }} />
            </div>
          </div>
        ))}
      </div>
      <div className="live-grid">
        <div className="live-map">
          <TrackMapPanel
            title={tr('Streckenkarte', 'Track map')}
            track={track}
            sample={s}
            frame={frame}
            de={de}
            defaultLayers={['corners']}
          />
        </div>
        <Panel title={tr('Reifen', 'Tyres')} className="live-tyres">
          <TyreGrid sample={s} settings={settings} de={de} />
        </Panel>
        <Panel title={tr('Sektoren', 'Sectors')} className="live-sectors">
          <SectorTimes frame={frame} de={de} />
        </Panel>
        <Panel title={tr('Warnungen', 'Warnings')} className="live-warnings">
          <WarningList warnings={warnings} de={de} />
        </Panel>
      </div>
      <Panel title={tr('Live-Telemetrie · letzte 30 Sekunden', 'Live telemetry · last 30 seconds')} className="live-telemetry">
        <TelemetryChart
          history={history}
          comparison={[]}
          channels={['speed', 'gas', 'brake']}
          settings={settings}
          analysis={false}
          range={null}
          height={210}
          de={de}
        />
      </Panel>
      <details className="panel vehicle-details" data-testid="vehicle-details">
        <summary>{tr('Weitere Fahrzeugdaten', 'More vehicle data')}</summary>
        <div className="vehicle-status">
          <div>
            <span>{tr('Querbeschl. / Längsbeschl.', 'Lateral / longitudinal G')}</span>
            <b>
              {value(ch.g_lat, 2)} / {value(ch.g_long, 2)} g
            </b>
          </div>
          <div>
            <span>{tr('Lenkung (Rohwert)', 'Steering (raw)')}</span>
            <b>{value(ch.steer, 2)}</b>
          </div>
          <div>
            <span>{tr('Kupplung', 'Clutch')}</span>
            <b>{value(available(ch.clutch) ? ch.clutch! * 100 : null)} %</b>
          </div>
          <div>
            <span>{tr('ABS / TC (Rohwert)', 'ABS / TC (raw)')}</span>
            <b>
              {value(ch.abs_raw, 2)} / {value(ch.tc_raw, 2)}
            </b>
          </div>
          <div>
            <span>{tr('Bremsbalance vorn', 'Brake bias front')}</span>
            <b>{value(available(ch.brake_bias) ? ch.brake_bias! * 100 : null, 1)} %</b>
          </div>
          <div>
            <span>{tr('Bodenfreiheit vorn / hinten', 'Ride height front / rear')}</span>
            <b>
              {value(available(ch.ride_front) ? ch.ride_front! * 1000 : null, 1)} / {value(available(ch.ride_rear) ? ch.ride_rear! * 1000 : null, 1)} mm
            </b>
          </div>
          <div>
            <span>{tr('Schaden (Rohwerte 0–4)', 'Damage (raw 0–4)')}</span>
            <b>{Array.from({ length: 5 }, (_, i) => value(ch['damage_' + i])).join(' / ')}</b>
          </div>
          <div>
            <span>{tr('Luft / Strecke', 'Air / track')}</span>
            <b>
              {value(meta?.air_temp === null || meta?.air_temp === undefined ? null : imperial ? meta.air_temp * 1.8 + 32 : meta.air_temp)} /{' '}
              {value(meta?.road_temp === null || meta?.road_temp === undefined ? null : imperial ? meta.road_temp * 1.8 + 32 : meta.road_temp)} {units.temp(u)}
            </b>
          </div>
          <div>
            <span>{tr('Motortemperatur', 'Engine temperature')}</span>
            <b>{tr('nicht verfügbar', 'not available')}</b>
          </div>
        </div>
      </details>
    </div>
  )
}
