/**
 * Lap video playback: maps the video position to the stored telemetry.
 *
 * One clock for both (see backend recordings.py): a recording knows the server
 * wall time of its video time 0 (started_at); every telemetry sample carries
 * captured_at on that clock. With the video offset (picture lags telemetry):
 *   video_s = captured_at - started_at + offset_s
 * The HUD is derived from the ACTUAL video position on every frame, so it stays
 * in sync after seeking, pausing and at any playback speed. A game pause inside
 * a lap is in the video, too: the lap clock simply stands still there.
 */
import type { Sample } from './types'

export type Coverage = 'full' | 'partial' | 'none' | 'pending'
/** Sound recorded by the PC app (Windows) with a video segment. */
export interface AudioInfo {
  status: 'recording' | 'ready' | 'failed' | 'none' | 'off'
  mode?: 'game' | 'system' | 'test' | 'off'
  started_at?: number | null
  duration_ms?: number | null
  sample_rate?: number
  channels?: number
  reason?: string | null
  error?: string | null
  interrupted?: boolean
}
export interface RecordingInfo {
  id: string
  session_id: string
  segment: number
  status: 'recording' | 'finalizing' | 'ready' | 'failed'
  mime: string
  started_at: number | null
  duration_ms: number | null
  bytes: number
  indexed: boolean
  end_reason: string | null
  error: string | null
  width?: number | null
  height?: number | null
  codec?: string | null
  audio?: AudioInfo
}
export interface LapVideo {
  lap_id: string
  session_id: string
  number: number
  duration_ms: number
  complete: boolean
  valid: boolean
  reasons: string[]
  offset_s: number
  lap_start: number | null
  lap_end: number | null
  coverage: Coverage
  covered_ratio?: number | null
  recording: RecordingInfo | null
  video_from_s?: number
  video_to_s?: number
  available_from_s?: number
  available_to_s?: number
  segments: RecordingInfo[]
}

/** Server wall clock (s) of the telemetry shown at video position v. */
export const wallAt = (v: number, startedAt: number, offset: number) => startedAt + v - offset

/** Values that must never be interpolated (a gear 3.5 does not exist). */
const DISCRETE = new Set([
  'gear',
  'pit_limiter',
  'drs',
  'drs_available',
  'abs_active',
  'tc_active',
  'ai_controlled',
])
/** Longer holes (game pause, lost frames) are held, not bridged. */
export const MAX_INTERPOLATION_GAP_S = 0.25

function lerp(a: number | null | undefined, b: number | null | undefined, f: number) {
  if (typeof a !== 'number' || !Number.isFinite(a)) return a ?? null
  if (typeof b !== 'number' || !Number.isFinite(b)) return a
  return a + (b - a) * f
}

/** Index of the last sample with captured_at <= wall (-1 before the first). */
export function indexAt(samples: Sample[], wall: number): number {
  let lo = -1,
    hi = samples.length
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (samples[mid].captured_at <= wall) lo = mid
    else hi = mid
  }
  return lo
}

/**
 * Telemetry at a wall-clock instant: continuous channels linear between the
 * two neighbouring samples, discrete values (gear, sector, lap counter …) from
 * the sample before. Outside the lap the first/last sample is held.
 */
export function sampleAtWall(samples: Sample[], wall: number): Sample | null {
  if (!samples.length) return null
  const i = indexAt(samples, wall)
  if (i < 0) return samples[0]
  if (i >= samples.length - 1) return samples[samples.length - 1]
  const a = samples[i],
    b = samples[i + 1]
  const dt = b.captured_at - a.captured_at
  if (!(dt > 0) || dt > MAX_INTERPOLATION_GAP_S) return a
  const f = Math.min(1, Math.max(0, (wall - a.captured_at) / dt))
  const channels: Record<string, number | null> = {}
  for (const [key, value] of Object.entries(a.channels))
    channels[key] = DISCRETE.has(key) ? value : (lerp(value, b.channels[key], f) as number | null)
  return {
    ...a,
    captured_at: wall,
    lap_ms: Math.round(lerp(a.lap_ms, b.lap_ms, f) as number),
    lap_pos: lerp(a.lap_pos, b.lap_pos, f) as number,
    channels,
  }
}

export interface Timeline {
  /** Video seconds of lap start/end (may lie outside the video when partial). */
  from: number
  to: number
  /** Part of the lap that really is in the video. */
  availableFrom: number
  availableTo: number
}

export function lapTimeline(info: LapVideo | null): Timeline | null {
  if (!info || info.video_from_s === undefined || info.video_to_s === undefined) return null
  if (info.available_from_s === undefined || info.available_to_s === undefined) return null
  if (!(info.available_to_s > info.available_from_s)) return null
  return {
    from: info.video_from_s,
    to: info.video_to_s,
    availableFrom: info.available_from_s,
    availableTo: info.available_to_s,
  }
}

/** Clamp a requested video position to the recorded part of the lap. */
export const clampToLap = (t: Timeline, v: number) => Math.min(t.availableTo, Math.max(t.availableFrom, v))

/** End of the selected lap reached (playback stops here unless repeating). */
export const atLapEnd = (t: Timeline, v: number) => v >= t.availableTo - 0.002

/**
 * Position in the sound file for video position v (null: no sound there).
 * Sound and telemetry are both on the server clock; the picture lags by the
 * video offset, so the sound of the picture at v was captured at
 * startedAt + v - offset.
 */
export function audioTimeAt(v: number, videoStartedAt: number, offset: number, audio: AudioInfo | null | undefined) {
  if (!audio || audio.status !== 'ready' || audio.started_at == null || !audio.duration_ms) return null
  const t = videoStartedAt + v - offset - audio.started_at
  return t >= 0 && t <= audio.duration_ms / 1000 ? t : null
}

export const HARD_SYNC_S = 0.15
export const SYNC_DEADBAND_S = 0.015
/**
 * Keep the separate sound element on the video: small drift is corrected by a
 * slightly different speed (max ±5 %, inaudible with pitch correction), larger
 * jumps (seeking) by repositioning. drift > 0 = sound ahead of the picture.
 */
export function syncSound(videoRate: number, drift: number): { seek: boolean; rate: number } {
  const size = Math.abs(drift)
  if (size > HARD_SYNC_S) return { seek: true, rate: videoRate }
  if (size < SYNC_DEADBAND_S) return { seek: false, rate: videoRate }
  return { seek: false, rate: videoRate * (1 - Math.max(-0.05, Math.min(0.05, drift))) }
}

export function audioLabel(audio: AudioInfo | null | undefined, de: boolean): { text: string; ok: boolean } {
  const t = (a: string, b: string) => (de ? a : b)
  const source =
    audio?.mode === 'game'
      ? t('Spielsound', 'game sound')
      : audio?.mode === 'system'
        ? t('PC-Ton', 'PC sound')
        : t('Testton', 'test tone')
  if (!audio || audio.status === 'off') return { text: t('Ohne Ton (Ton aus)', 'No sound (sound off)'), ok: false }
  if (audio.status === 'recording') return { text: t('Ton: ', 'Sound: ') + source + t(' wird aufgenommen', ' recording'), ok: true }
  if (audio.status === 'ready') return { text: t('Ton: ', 'Sound: ') + source, ok: true }
  if (audio.status === 'none')
    return {
      text: /not running/i.test(audio.reason || '')
        ? t('Ohne Ton: Assetto Corsa lief nicht', 'No sound: Assetto Corsa was not running')
        : t('Ohne Ton', 'No sound') + (audio.reason ? ` (${audio.reason})` : ''),
      ok: false,
    }
  return { text: t('Ton fehlgeschlagen', 'Sound failed') + (audio.error ? `: ${audio.error}` : ''), ok: false }
}

/** CSS gradient marking the parts of the lap without video on the timeline. */
export function coverageGradient(t: Timeline): string {
  const span = Math.max(1e-6, t.to - t.from)
  const a = Math.max(0, Math.min(100, ((t.availableFrom - t.from) / span) * 100))
  const b = Math.max(0, Math.min(100, ((t.availableTo - t.from) / span) * 100))
  const gap = 'var(--video-gap)'
  const ok = 'var(--video-ok)'
  return `linear-gradient(90deg, ${gap} 0 ${a}%, ${ok} ${a}% ${b}%, ${gap} ${b}% 100%)`
}
