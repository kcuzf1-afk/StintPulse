/**
 * Telemetry reception as its own state, separate from the backend link and
 * the game status: values that are no longer updated must never look live.
 */
import type { Frame } from './types'

export type TelemetryKey = 'live' | 'demo' | 'paused' | 'stale' | 'none'
export interface TelemetryView {
  key: TelemetryKey
  de: string
  en: string
  tone: 'green' | 'orange' | 'yellow' | 'muted'
  /** Wall-clock time (ms) of the last new sample; null if none. */
  lastAt: number | null
  /** True when shown values are old (show "Letzter Stand"). */
  frozen: boolean
}

export const STALE_MS = 2500

export function formatClock(ms: number | null | undefined): string {
  if (!ms) return '—'
  const d = new Date(ms)
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((v) => String(v).padStart(2, '0')).join(':')
}

export function telemetryStatus(
  frame: Pick<Frame, 'sample' | 'status' | 'source'>,
  socket: string,
  lastFreshAt: number | null,
  now: number,
): TelemetryView {
  if (!frame.sample)
    return { key: 'none', de: 'KEINE TELEMETRIE', en: 'NO TELEMETRY', tone: 'muted', lastAt: lastFreshAt, frozen: false }
  const stamp = formatClock(lastFreshAt)
  const stale: TelemetryView = {
    key: 'stale',
    de: `LETZTER STAND ${stamp}`,
    en: `LAST VALUES ${stamp}`,
    tone: 'yellow',
    lastAt: lastFreshAt,
    frozen: true,
  }
  if (socket !== 'connected') return stale
  if (frame.status === 'paused')
    return { key: 'paused', de: 'TELEMETRIE PAUSIERT', en: 'TELEMETRY PAUSED', tone: 'yellow', lastAt: lastFreshAt, frozen: true }
  const fresh = lastFreshAt !== null && now - lastFreshAt < STALE_MS
  if (fresh && frame.status === 'demo')
    return { key: 'demo', de: 'DEMO-TELEMETRIE', en: 'DEMO TELEMETRY', tone: 'orange', lastAt: lastFreshAt, frozen: false }
  if (fresh && (frame.status === 'live' || frame.status === 'replay'))
    return { key: 'live', de: 'TELEMETRIE LIVE', en: 'TELEMETRY LIVE', tone: 'green', lastAt: lastFreshAt, frozen: false }
  return stale
}
