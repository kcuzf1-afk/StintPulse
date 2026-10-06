/** Pure helpers of the setup assistant (unit-tested in setup.test.ts). */
import type { AiStatus, Evidence, ParamStatus, Recommendation, SetupParam } from './types'
import type { LapSummary, Session } from '../types'

export const STATUS_LABEL: Record<ParamStatus, string> = {
  abgeleitet: 'abgeleitet',
  bestaetigt: 'bestätigt',
  mehrdeutig: 'mehrdeutig',
  widerspruechlich: 'widersprüchlich',
  unbestaetigt: 'unbestätigt',
  nicht_unterstuetzt: 'nicht unterstützt',
}

export const STATUS_HINT: Record<ParamStatus, string> = {
  abgeleitet: 'Grenzen aus den Fahrzeugdaten, Kodierung eindeutig aus gespeicherten Setups abgeleitet - exportierbar',
  bestaetigt: 'Kodierung von dir anhand der Anzeige im Spiel bestätigt - exportierbar',
  mehrdeutig: 'Gespeicherte Werte passen zu mehreren Kodierungen - bitte bestätigen, sonst kein Export',
  widerspruechlich: 'Gespeicherte Werte passen nicht zu den Grenzen - kein Export',
  unbestaetigt: 'Grenzen oder Kodierung unbekannt - kein Export',
  nicht_unterstuetzt: 'Nicht änderbar (z. B. Getriebe-Übersetzungen oder nicht beschrieben)',
}

export function statusTag(status: ParamStatus) {
  return status === 'abgeleitet' || status === 'bestaetigt'
    ? 'green-tag'
    : status === 'mehrdeutig'
      ? 'yellow-tag'
      : status === 'widerspruechlich'
        ? 'red-tag'
        : 'muted-tag'
}

export function verdictTag(verdict: Evidence['verdict']) {
  return verdict === 'stützt' ? 'green-tag' : verdict === 'widerspricht eher' ? 'orange-tag' : 'muted-tag'
}

export function confidenceTag(c: Recommendation['confidence']) {
  return c === 'hoch' ? 'green-tag' : c === 'mittel' ? 'cyan-tag' : 'muted-tag'
}

/** Number in the game's unit; trailing zeros trimmed, never invented. */
export function fmt(value: number | null | undefined, unit = '', digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const text = Number(value.toFixed(digits)).toLocaleString('de-DE', { maximumFractionDigits: digits })
  return unit ? `${text} ${unit}` : text
}

/** Seconds with a fixed number of decimals (lap and sector times). */
export function secs(value: number | null | undefined, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits }) + ' s'
}

/** Last two path parts (track folder / file) - the full path is in the tooltip. */
export function shortPath(path: string) {
  return path.split(/[\\/]/).slice(-2).join(' / ')
}

export function rangeText(p: SetupParam) {
  if (p.min === undefined || p.max === undefined) return '—'
  return `${fmt(p.min)} … ${fmt(p.max)}${p.step ? ` · Schritt ${fmt(p.step)}` : ''}`
}

export function aiStateText(ai: AiStatus | null): string {
  if (!ai) return 'Status wird geladen …'
  switch (ai.state) {
    case 'off':
      return 'KI ist ausgeschaltet - die regelbasierte Analyse steht zur Verfügung.'
    case 'sdk_missing':
      return 'Das Anthropic-SDK fehlt in dieser Programmversion.'
    case 'no_key':
      return 'Kein API-Schlüssel hinterlegt - am PC unter „KI-Anbieter“ eintragen.'
    case 'key_unreadable':
      return 'Der gespeicherte API-Schlüssel ist auf diesem PC nicht lesbar - bitte neu eingeben.'
    case 'consent':
      return 'Vor der ersten Nutzung: Datenübertragung ansehen und bestätigen.'
    case 'ready':
      return `KI bereit: ${ai.providers[ai.provider]?.label || ai.provider} · ${ai.model}`
  }
}

/** Default lap choice: complete laps of the newest session, at most 20. */
export function defaultLaps(sessions: Session[]): string[] {
  const newest = [...sessions].sort((a, b) => b.created_at.localeCompare(a.created_at))
  for (const s of newest) {
    const laps = (s.laps || []).filter((l: LapSummary) => l.complete)
    if (laps.length) return laps.slice(-20).map((l) => l.id)
  }
  return []
}

export function comboKey(car: string, track: string) {
  return JSON.stringify([car, track])
}

/** Sessions grouped by car and track folder (setups are stored per track, not layout). */
export function combos(sessions: Session[]) {
  const map = new Map<string, { car: string; track: string; sessions: Session[]; demo: boolean }>()
  for (const s of sessions) {
    const key = comboKey(s.meta.car, s.meta.track)
    if (!s.meta.car || !s.meta.track) continue
    if (!map.has(key)) map.set(key, { car: s.meta.car, track: s.meta.track, sessions: [], demo: s.meta.source === 'demo' })
    map.get(key)!.sessions.push(s)
  }
  return map
}

export function exportable(changes: Recommendation[]) {
  return changes.filter((c) => c.exportable)
}

export async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}
