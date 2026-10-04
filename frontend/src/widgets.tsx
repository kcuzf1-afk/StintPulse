/** Shared building blocks. Pages compose them; no page repeats the full set. */
import { useState, type ReactNode } from 'react'
import { ChevronRight, Layers, ShieldCheck } from 'lucide-react'
import TrackMap from './TrackMap'
import type { Compare, Frame, Sample, Settings, Tip, Track } from './types'
import { available, toPressure, toTemp, tyreStatus, units, value, type Units } from './format'
import { formatSectorTime, sectorColor } from './hud'

export function Panel({
  title,
  actions,
  children,
  className = '',
  testId,
}: {
  title: string
  actions?: ReactNode
  children: ReactNode
  className?: string
  testId?: string
}) {
  return (
    <section className={'panel ' + className} data-testid={testId}>
      <div className="panel-title">
        <h3>{title}</h3>
        {actions && <div className="panel-tools">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

export function Kpi({
  label,
  val,
  unit,
  detail,
  color = '',
  testId,
}: {
  label: string
  val: string
  unit?: string
  detail?: string
  color?: string
  testId?: string
}) {
  return (
    <div className="kpi" data-testid={testId}>
      <span className="kpi-label">{label}</span>
      <div className={color}>
        <strong>{val}</strong>
        {unit && <small>{unit}</small>}
      </div>
      <span className="kpi-detail">{detail || ' '}</span>
    </div>
  )
}

/** Compact tyre overview: core temperature, pressure, I/M/O per wheel. */
export function TyreGrid({ sample, settings, de }: { sample: Sample | null; settings: Settings | null; de: boolean }) {
  const u: Units = settings?.units || 'metric'
  const t = (v: number | null | undefined) => value(toTemp(v, u), 0)
  return (
    <div className="tyre-grid compact">
      {['FL', 'FR', 'RL', 'RR'].map((c) => {
        const w = sample?.tyres.find((x) => x.corner === c)
        const status = tyreStatus(w?.core, settings?.temp_cold || 70, settings?.temp_hot || 105)
        return (
          <div key={c} className={'tyre-card ' + status} data-testid={'tyre-' + c}>
            <div className="tyre-card-head">
              <span>{c}</span>
              <small>
                {status === 'in_range'
                  ? de
                    ? 'im Bereich'
                    : 'in range'
                  : status === 'hot'
                    ? de
                      ? 'zu heiß'
                      : 'hot'
                    : status === 'cold'
                      ? de
                        ? 'kalt'
                        : 'cold'
                      : de
                        ? 'nicht verfügbar'
                        : 'not available'}
              </small>
            </div>
            <div className="tyre-temperature">
              <b>{t(w?.core)}</b>
              <small>{units.temp(u)}</small>
            </div>
            <div className="tyre-pressure">
              {value(toPressure(w?.pressure, u), u === 'imperial' ? 1 : 2)} {units.pressure(u)}
            </div>
            <div className="temp-strip" title={de ? 'innen / Mitte / außen' : 'inner / middle / outer'}>
              <span>{t(w?.inner)}</span>
              <span>{t(w?.middle)}</span>
              <span>{t(w?.outer)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Sector times of the current lap from the backend timing block. */
export function SectorTimes({ frame, de }: { frame: Frame; de: boolean }) {
  const tr = (a: string, b: string) => (de ? a : b)
  const t = frame.timing
  if (!t)
    return <p className="compact-empty">{tr('Sektorzeiten erscheinen während der Fahrt.', 'Sector times appear while driving.')}</p>
  if (!t.sectors_known)
    return <p className="compact-empty">{tr('Das Spiel liefert für diese Strecke keine Sektoren.', 'The game provides no sectors for this track.')}</p>
  const invalid = t.invalid_reasons.length > 0
  const last = t.last_lap
  return (
    <table className="sector-times" data-testid="sector-times">
      <thead>
        <tr>
          <th></th>
          <th>{tr('Aktuell', 'Current')}</th>
          <th>{tr('Letzte', 'Last')}</th>
          <th>{t.reference?.kind === 'selected' ? 'REF' : 'PB'}</th>
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: t.sector_count }, (_, i) => {
          const cur = i < t.sector_index ? t.sectors_ms[i] : null
          const color = sectorColor(cur, t.reference?.sectors_ms[i], t.best_sectors_ms[i], invalid)
          return (
            <tr key={i}>
              <th>S{i + 1}</th>
              <td className={'sector-' + color} data-color={color}>
                {cur !== null ? formatSectorTime(cur) : i === t.sector_index ? tr('läuft', 'running') : '—'}
              </td>
              <td>{formatSectorTime(last?.sectors_ms[i])}</td>
              <td>{formatSectorTime(t.reference?.sectors_ms[i])}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export interface Warning {
  level: 'warn' | 'bad'
  text: string
}
/** Relevant warnings only; derived from measured values and configured limits. */
export function vehicleWarnings(frame: Frame, de: boolean): Warning[] {
  const tr = (a: string, b: string) => (de ? a : b)
  const out: Warning[] = []
  for (const w of frame.tyre_warnings || []) {
    if (w.temperature === 'hot') out.push({ level: 'bad', text: tr(`${w.wheel}: Reifen zu heiß`, `${w.wheel}: tyre too hot`) })
    if (w.temperature === 'cold') out.push({ level: 'warn', text: tr(`${w.wheel}: Reifen kalt`, `${w.wheel}: tyre cold`) })
    if (w.pressure === 'high') out.push({ level: 'warn', text: tr(`${w.wheel}: Druck hoch`, `${w.wheel}: pressure high`) })
    if (w.pressure === 'low') out.push({ level: 'warn', text: tr(`${w.wheel}: Druck niedrig`, `${w.wheel}: pressure low`) })
  }
  if (frame.timing?.invalid_reasons.length)
    out.push({ level: 'bad', text: tr('Runde ungültig (Strecke verlassen/Strafe)', 'Lap invalid (off track/penalty)') })
  if (available(frame.fuel_laps_est) && frame.fuel_laps_est! < 2)
    out.push({ level: 'bad', text: tr(`Kraftstoff für ca. ${value(frame.fuel_laps_est, 1)} Runden`, `Fuel for about ${value(frame.fuel_laps_est, 1)} laps`) })
  const dmg = [0, 1, 2, 3, 4].map((i) => frame.sample?.channels['damage_' + i]).filter(available) as number[]
  if (dmg.some((d) => d > 0)) out.push({ level: 'warn', text: tr('Fahrzeugschaden gemeldet (Rohwert > 0)', 'Car damage reported (raw value > 0)') })
  if (frame.sample?.channels.pit_limiter) out.push({ level: 'warn', text: tr('Pit-Limiter aktiv', 'Pit limiter on') })
  return out
}

export function WarningList({ warnings, de }: { warnings: Warning[]; de: boolean }) {
  if (!warnings.length)
    return <p className="compact-empty" data-testid="no-warnings">{de ? 'Keine Warnungen.' : 'No warnings.'}</p>
  return (
    <ul className="warning-list" data-testid="warnings">
      {warnings.map((w, i) => (
        <li key={i} className={w.level}>
          {w.text}
        </li>
      ))}
    </ul>
  )
}

export const MAP_LAYERS: Array<[string, string, string]> = [
  ['heatmap', 'Zeitgewinn/-verlust', 'Time gain/loss'],
  ['corners', 'Kurven*', 'Turns*'],
  ['braking_zone', 'Bremszonen', 'Braking zones'],
  ['full_throttle', 'Vollgas', 'Full throttle'],
  ['shift', 'Schaltpunkte', 'Shifts'],
  ['wheel_lock', 'Blockieren*', 'Locking*'],
  ['wheelspin', 'Wheelspin*', 'Wheelspin*'],
  ['off_track', 'Neben der Strecke*', 'Off track*'],
  ['body_slip', 'Fahrzeugschlupf*', 'Body slip*'],
]

/** Track map with the many layer options folded into one compact menu. */
export function TrackMapPanel({
  title,
  track,
  sample,
  frame,
  comparison,
  comparisonComplete,
  cursor,
  onSelect,
  de,
  defaultLayers,
}: {
  title: string
  track: Track | null
  sample: Sample | null
  frame?: Frame
  comparison?: Compare
  comparisonComplete?: boolean
  cursor?: number
  onSelect?: (m: number) => void
  de: boolean
  defaultLayers: string[]
}) {
  const [layers, setLayers] = useState(defaultLayers)
  const available = MAP_LAYERS.filter(([id]) => comparison || id !== 'heatmap')
  return (
    <Panel
      title={title}
      className="map-panel"
      actions={
        <details className="layer-menu">
          <summary aria-label={de ? 'Kartenebenen' : 'Map layers'}>
            <Layers size={14} /> {de ? 'Ebenen' : 'Layers'}
          </summary>
          <div className="layer-menu-list">
            {available.map(([id, labelDe, labelEn]) => (
              <label key={id}>
                <input
                  type="checkbox"
                  checked={layers.includes(id)}
                  onChange={(e) => setLayers(e.target.checked ? [...layers, id] : layers.filter((l) => l !== id))}
                />
                {de ? labelDe : labelEn}
              </label>
            ))}
            <small>{de ? '* Schätzung' : '* estimate'}</small>
          </div>
        </details>
      }
    >
      <TrackMap
        track={track}
        sample={sample}
        ghost={frame ? frame.ghost_pos : null}
        ghostCoords={frame ? frame.ghost_coords : null}
        comparison={comparison}
        comparisonComplete={comparisonComplete}
        cursor={cursor}
        click={onSelect}
        layers={layers}
        de={de}
      />
    </Panel>
  )
}

/** Measured coaching hints; causes are hypotheses and labelled as such. */
export function Coach({ tips, de, select }: { tips: Tip[]; de: boolean; select?: (m: number) => void }) {
  const tr = (a: string, b: string) => (de ? a : b)
  if (!tips.length)
    return (
      <p className="compact-empty" data-testid="coach-empty">
        {tr(
          'Keine belastbaren Unterschiede gefunden – oder noch keine kompatible Referenz gewählt.',
          'No reliable differences found – or no compatible reference selected yet.',
        )}
      </p>
    )
  return (
    <div className="coach-list" data-testid="coach">
      {tips.slice(0, 6).map((tip, i) => {
        const m = tip.measurement,
          kind = m.kind
        const heading =
          kind === 'brake_early'
            ? `${value(Number(m.difference_m), 1)} m ${tr('früher gebremst', 'earlier braking')}`
            : kind === 'minimum_speed'
              ? `${value(Number(m.difference_kmh), 1)} km/h ${tr('weniger am Minimum', 'lower minimum speed')}`
              : kind === 'throttle_late'
                ? `${value(Number(m.difference_s), 2)} s ${tr('später Vollgas', 'later full throttle')}`
                : kind === 'tyre_hot'
                  ? `${m.wheel} · ${m.peak_c} °C`
                  : `${value(Number(m.difference_s), 3)} s ${tr('Abschnittsverlust', 'section loss')}`
        return (
          <button className="coach-card" key={i} onClick={() => select?.(tip.position_m)}>
            <div className="coach-location">
              <span>
                {tip.corner ? `${tr('Kurve', 'Turn')} ${tip.corner}*` : `${Math.round(tip.position_m)} m`}
              </span>
              <span className="confidence">
                {tr('Sicherheit', 'Confidence')}: {tip.confidence}
              </span>
            </div>
            <strong>{heading}</strong>
            <p>
              <em>{tr('Hypothese: ', 'Hypothesis: ')}</em>
              {tip.cause}
            </p>
            <p className="coach-action">
              <ChevronRight size={12} />
              {tip.action}
            </p>
            <footer>
              {available(tip.potential_s)
                ? `${tr('Gemessener Verlust', 'Measured loss')} ${value(tip.potential_s, 3)} s`
                : tr('Zeitverlust nicht messbar', 'Time loss not measurable')}
            </footer>
          </button>
        )
      })}
      <div className="coach-note">
        <ShieldCheck size={12} />
        {tr('Lokale Regeln · Ursachen sind Hypothesen · *Kurven geschätzt', 'Local rules · causes are hypotheses · *turns estimated')}
      </div>
    </div>
  )
}
