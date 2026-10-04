/**
 * Lap-timing HUD: pure presentation logic (no timing engine).
 *
 * Every time shown comes from the backend (game lap clock, official lap and
 * sector times) or, in replay, from the stored lap. This module only decides
 * WHICH value to show and how to colour it.
 */
import type { Frame, LapSummary, Meta, Sample, Timing, TimingReference } from './types'

export type SectorColor = 'gray' | 'green' | 'yellow' | 'violet' | 'red'
export interface HudSector {
  label: string
  ms: number | null
  color: SectorColor
}
export interface HudStatus {
  de: string
  en: string
  tone: 'muted' | 'yellow' | 'red' | 'cyan'
}
export interface HudModel {
  mode: 'live' | 'demo' | 'replay'
  /** Synthetic demo data (live demo or replayed demo lap). */
  synthetic?: boolean
  status: HudStatus | null
  position: number | null
  driver: string | null
  compound: { label: string; full: string; manual: boolean } | null
  timeMs: number | null
  phase: 'running' | 'completed' | 'frozen' | 'none'
  lapNumber: number | null
  sectors: HudSector[]
  sectorsAvailable: boolean
  invalid: boolean
  incomplete: boolean
  reference: { kind: 'personal_best' | 'selected' | 'session_best'; ms: number } | null
  deltaMs: number | null
}

/** TV style: surname only, upper case ("Kimi Antonelli" -> "ANTONELLI"). */
export function surname(driver: string | null | undefined): string {
  const parts = (driver || '').trim().split(/\s+/).filter(Boolean)
  return parts.length ? parts[parts.length - 1].toUpperCase() : ''
}

/** Mirrors backend timing.INVALID_REASONS (derived from game fields). */
export const INVALID_REASONS = ['off_track_inferred', 'penalty']

const ok = (v: number | null | undefined): v is number =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0

/** M:SS.mmm (lap) – missing values as "—". */
export function formatLapTime(ms: number | null | undefined): string {
  if (!ok(ms)) return '—'
  const total = Math.round(ms)
  const minutes = Math.floor(total / 60000)
  const seconds = Math.floor(total / 1000) % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(total % 1000).padStart(3, '0')}`
}

/** Sector: SS.mmm below one minute, otherwise M:SS.mmm. */
export function formatSectorTime(ms: number | null | undefined): string {
  if (!ok(ms)) return '—'
  if (ms >= 60000) return formatLapTime(ms)
  const total = Math.round(ms)
  return `${Math.floor(total / 1000)}.${String(total % 1000).padStart(3, '0')}`
}

export function formatDelta(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—'
  const sign = ms > 0 ? '+' : ms < 0 ? '−' : '±'
  return `${sign}${(Math.abs(Math.round(ms)) / 1000).toFixed(3)}`
}

/**
 * Integer milliseconds as delivered by the game (1 ms resolution). Equal times
 * are not "faster": they count as yellow (no improvement measurable).
 */
export function sectorColor(
  ms: number | null | undefined,
  refMs: number | null | undefined,
  bestMs: number | null | undefined,
  invalid: boolean,
): SectorColor {
  if (!ok(ms)) return 'gray'
  if (invalid) return 'red'
  if (ok(bestMs) && ms < bestMs) return 'violet'
  if (!ok(refMs)) return 'gray'
  return ms < refMs ? 'green' : 'yellow'
}

/**
 * Tyre label: a manual mapping (documented as such) wins; otherwise only the
 * short code the game itself puts in brackets, e.g. "Michelin Soft (MS)".
 * No S/M/H guessing from names.
 */
export function compoundView(raw: string | null | undefined, map: Record<string, string> = {}) {
  const full = (raw || '').trim()
  if (!full) return null
  if (map[full]) return { label: map[full], full, manual: true }
  const code = /\(([A-Za-z0-9+-]{1,4})\)\s*$/.exec(full)
  // label '' = no short code: the HUD shows the full name on its own line.
  return { label: code ? code[1] : '', full, manual: false }
}

function sectors(
  count: number,
  done: Array<number | null>,
  ref: Array<number | null> | undefined,
  best: Array<number | null> | undefined,
  invalid: boolean,
): HudSector[] {
  return Array.from({ length: count }, (_, i) => {
    const ms = ok(done[i]) ? done[i] : null
    return { label: `S${i + 1}`, ms, color: sectorColor(ms, ref?.[i], best?.[i], invalid) }
  })
}

const NO_DATA: Omit<HudModel, 'mode' | 'status'> = {
  position: null,
  driver: null,
  compound: null,
  timeMs: null,
  phase: 'none',
  lapNumber: null,
  sectors: [],
  sectorsAvailable: false,
  invalid: false,
  incomplete: false,
  reference: null,
  deltaMs: null,
}

const STATUS: Record<string, HudStatus> = {
  paused: { de: 'PAUSE', en: 'PAUSED', tone: 'yellow' },
  replay: { de: 'SPIEL-REPLAY', en: 'GAME REPLAY', tone: 'cyan' },
  stale: { de: 'DATEN VERALTET', en: 'DATA STALE', tone: 'yellow' },
  waiting_game: { de: 'WARTE AUF SPIEL', en: 'WAITING FOR GAME', tone: 'muted' },
  waiting_session: { de: 'WARTE AUF FAHRSESSION', en: 'WAITING FOR SESSION', tone: 'muted' },
  not_initialized: { de: 'SESSION LÄDT', en: 'SESSION LOADING', tone: 'muted' },
  access_denied: { de: 'ZUGRIFF VERWEIGERT', en: 'ACCESS DENIED', tone: 'red' },
  decode_error: { de: 'DATENFEHLER', en: 'DATA ERROR', tone: 'red' },
  memory_error: { de: 'SPEICHERFEHLER', en: 'MEMORY ERROR', tone: 'red' },
  capture_failed: { de: 'ERFASSUNG AUS', en: 'CAPTURE STOPPED', tone: 'red' },
  starting: { de: 'STARTET', en: 'STARTING', tone: 'muted' },
}
const DISCONNECTED: HudStatus = { de: 'KEINE VERBINDUNG', en: 'NO CONNECTION', tone: 'red' }

function refView(ref: TimingReference | null | undefined): HudModel['reference'] {
  return ref && ok(ref.duration_ms) ? { kind: ref.kind, ms: ref.duration_ms } : null
}

/** Live/demo HUD from the backend timing block. Never extrapolates a clock. */
export function buildLiveHud(
  frame: Frame,
  socket: string,
  compoundMap: Record<string, string> = {},
): HudModel {
  const mode = frame.source === 'demo' || frame.meta?.source === 'demo' ? 'demo' : 'live'
  if (socket !== 'connected') return { mode, status: DISCONNECTED, ...NO_DATA }
  const t: Timing | null | undefined = frame.timing
  const s: Sample | null = frame.sample
  const meta: Meta | null = frame.meta
  const flowing = frame.status === 'live' || frame.status === 'demo'
  const frozen = frame.status === 'paused'
  const status = flowing ? null : STATUS[frame.status] || { de: frame.status.toUpperCase(), en: frame.status.toUpperCase(), tone: 'muted' as const }
  if (!t || !s || !meta) return { mode, status: status || STATUS.starting, ...NO_DATA }
  const base = {
    mode,
    status,
    position: s.position > 0 ? s.position : null,
    driver: meta.driver || null,
    compound: compoundView(meta.compound, compoundMap),
    sectorsAvailable: t.sectors_known && t.sector_count >= 1,
  } as const
  if (!flowing && !frozen)
    // Stale, waiting, game replay …: no time that looks current.
    return { ...NO_DATA, ...base }
  const last = t.last_lap
  if (last && last.age_ms < t.hold_ms) {
    const invalid = last.invalid_reasons.length > 0
    const ref = refView(last.reference)
    return {
      ...base,
      timeMs: last.duration_ms,
      phase: 'completed',
      lapNumber: last.number,
      sectors: sectors(t.sector_count, last.sectors_ms, last.reference?.sectors_ms, last.best_sectors_ms, invalid),
      invalid,
      incomplete: !last.complete,
      reference: ref,
      deltaMs: ref && last.complete && !invalid ? last.duration_ms - ref.ms : null,
    }
  }
  const invalid = t.invalid_reasons.length > 0
  const done = t.sectors_ms.map((v, i) => (i < t.sector_index ? v : null))
  return {
    ...base,
    timeMs: t.lap_ms,
    phase: frozen ? 'frozen' : 'running',
    lapNumber: t.lap_number,
    sectors: sectors(t.sector_count, done, t.reference?.sectors_ms, t.best_sectors_ms, invalid),
    invalid,
    incomplete: !t.started_at_line,
    reference: refView(t.reference),
    deltaMs: null,
  }
}

function bestSectorsBefore(laps: LapSummary[], lap: LapSummary, count: number) {
  const best: Array<number | null> = Array(count).fill(null)
  for (const l of laps) {
    if (l.id === lap.id || !l.valid || !l.complete || l.number >= lap.number) continue
    if (l.sectors_ms.length !== count) continue
    l.sectors_ms.forEach((v, i) => {
      if (ok(v) && (best[i] === null || v < best[i]!)) best[i] = v
    })
  }
  return best
}

/**
 * Replay HUD: ONLY the replayed lap and its session (stored data). The
 * comparison is session-internal: reference = fastest other valid lap of the
 * replayed session, violet = better than every earlier valid lap.
 */
export function buildReplayHud(
  sample: Sample | null,
  lap: LapSummary | null,
  laps: LapSummary[],
  meta: Meta | null,
  atEnd: boolean,
  compoundMap: Record<string, string> = {},
): HudModel {
  const status: HudStatus = { de: 'WIEDERGABE', en: 'REPLAY', tone: 'cyan' }
  const synthetic = meta?.source === 'demo' || sample?.source === 'demo'
  if (!sample || !lap) return { mode: 'replay', synthetic, status, ...NO_DATA }
  const count = meta?.sector_count || lap.sectors_ms.length
  const invalid = lap.reasons.some((r) => INVALID_REASONS.includes(r))
  const reference = laps
    .filter((l) => l.id !== lap.id && l.valid && l.complete)
    .sort((a, b) => a.duration_ms - b.duration_ms)[0]
  const done = lap.sectors_ms.map((v, i) => (atEnd || i < sample.sector_index ? v : null))
  return {
    mode: 'replay',
    synthetic,
    status,
    position: sample.position > 0 ? sample.position : null,
    driver: meta?.driver || null,
    compound: compoundView(meta?.compound, compoundMap),
    timeMs: atEnd ? lap.duration_ms : sample.lap_ms,
    phase: atEnd ? 'completed' : 'running',
    lapNumber: lap.number,
    sectors: sectors(count, done, reference?.sectors_ms, bestSectorsBefore(laps, lap, count), invalid),
    sectorsAvailable: count >= 1 && lap.sectors_ms.length === count,
    invalid,
    incomplete: !lap.complete,
    reference: reference ? { kind: 'session_best', ms: reference.duration_ms } : null,
    deltaMs:
      atEnd && reference && lap.complete && !invalid ? lap.duration_ms - reference.duration_ms : null,
  }
}

/* ---------- placement on the real picture area ---------- */

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}
/** Picture area of object-fit: contain media inside a box (letterboxing). */
export function contentRect(boxW: number, boxH: number, mediaW?: number, mediaH?: number): Rect {
  if (!mediaW || !mediaH || !boxW || !boxH) return { x: 0, y: 0, w: boxW, h: boxH }
  const scale = Math.min(boxW / mediaW, boxH / mediaH)
  const w = mediaW * scale,
    h = mediaH * scale
  return { x: Math.max(0, (boxW - w) / 2), y: Math.max(0, (boxH - h) / 2), w, h }
}

export interface HudPrefs {
  visible: boolean
  /** Position as fraction (0..1) of the free space inside the picture area. */
  x: number
  y: number
  scale: number
  opacity: number
  locked: boolean
  /** Extra line (lap, reference/delta) and sector times; off = TV look. */
  details: boolean
  /** Compact speed/gear/pedal overlay (bottom left). */
  tacho: boolean
}
export const DEFAULT_HUD: HudPrefs = {
  visible: true,
  x: 1,
  y: 0,
  scale: 1,
  opacity: 0.95,
  locked: false,
  details: false,
  tacho: true,
}
const KEY = 'aceda-lap-hud-v1'
const clamp = (v: unknown, lo: number, hi: number, d: number) =>
  typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d

export function sanitizeHud(raw: Partial<HudPrefs> | null | undefined): HudPrefs {
  const r = raw || {}
  return {
    visible: typeof r.visible === 'boolean' ? r.visible : DEFAULT_HUD.visible,
    x: clamp(r.x, 0, 1, DEFAULT_HUD.x),
    y: clamp(r.y, 0, 1, DEFAULT_HUD.y),
    scale: clamp(r.scale, 0.6, 1.8, DEFAULT_HUD.scale),
    opacity: clamp(r.opacity, 0.3, 1, DEFAULT_HUD.opacity),
    locked: typeof r.locked === 'boolean' ? r.locked : DEFAULT_HUD.locked,
    details: typeof r.details === 'boolean' ? r.details : DEFAULT_HUD.details,
    tacho: typeof r.tacho === 'boolean' ? r.tacho : DEFAULT_HUD.tacho,
  }
}
/** Per device (phone and PC differ), so stored in this browser. */
export function loadHud(): HudPrefs {
  try {
    return sanitizeHud(JSON.parse(localStorage.getItem(KEY) || 'null'))
  } catch {
    return { ...DEFAULT_HUD }
  }
}
export function saveHud(prefs: HudPrefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs))
  } catch {}
}

/** Top-left of the HUD (px) inside the box for the stored fractional position. */
export function hudOffset(rect: Rect, hudW: number, hudH: number, x: number, y: number, pad = 8) {
  const freeW = Math.max(0, rect.w - hudW - 2 * pad),
    freeH = Math.max(0, rect.h - hudH - 2 * pad)
  return { left: rect.x + pad + x * freeW, top: rect.y + pad + y * freeH }
}
/** Inverse of hudOffset for dragging. */
export function hudFraction(rect: Rect, hudW: number, hudH: number, left: number, top: number, pad = 8) {
  const freeW = rect.w - hudW - 2 * pad,
    freeH = rect.h - hudH - 2 * pad
  return {
    x: freeW > 0 ? Math.min(1, Math.max(0, (left - rect.x - pad) / freeW)) : 1,
    y: freeH > 0 ? Math.min(1, Math.max(0, (top - rect.y - pad) / freeH)) : 0,
  }
}
