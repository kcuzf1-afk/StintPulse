/**
 * Automatic onboard recording in the PC browser.
 *
 * Records the shared onboard stream (videoSource.ts) with MediaRecorder while
 * a drive is active and sends it in 2-second chunks to the PC app, which
 * appends them to a file at once (nothing long is kept in memory) and indexes
 * the file afterwards so it can be seeked. Recording needs this browser tab to
 * stay open; it never continues after the tab or browser was closed.
 *
 * One recording "segment" = one continuous MediaRecorder run. A session gets a
 * new segment when the video source dropped out and came back; the gap stays
 * visible (laps across it have only partial video).
 */
import type { SourceMode, SourceUser } from './videoSource'
import type { AudioInfo } from './playback'

export type Quality = 'saver' | 'standard' | 'high'
export type RecState = 'off' | 'unsupported' | 'ready' | 'no_source' | 'recording' | 'saving' | 'saved' | 'failed'

/** Bitrate only: resolution and frame rate come from the source (OBS output). */
export const QUALITY: Record<Quality, { bitrate: number }> = {
  saver: { bitrate: 3_000_000 },
  standard: { bitrate: 6_000_000 },
  high: { bitrate: 12_000_000 },
}
/**
 * WebM only: the PC app indexes WebM for seeking. MediaRecorder MP4 output
 * was tested in Chromium and reports a wrong duration and cannot be seeked,
 * so it is not used as a fallback.
 */
export const MIME_PREFERENCE = ['video/webm;codecs=vp8', 'video/webm;codecs=vp9', 'video/webm']
export const CHUNK_MS = 2000
export const KEYFRAME_MS = 2000
/** Game status not "driving" for this long ends the segment (menus, loading). */
export const END_GRACE_MS = 15000
const MAX_QUEUED_BYTES = 256 * 1024 ** 2
const RETRIES = 5
/** A failed start (PC app unreachable, other tab recording) is retried after this. */
export const RETRY_START_MS = 10000
const storageProblem = (message: string) => /limit|disk|space|speicher/i.test(message)

export function pickMime(isSupported: ((type: string) => boolean) | undefined): string | null {
  if (!isSupported) return null
  return MIME_PREFERENCE.find((t) => isSupported(t)) || null
}

export interface RecStatus {
  state: RecState
  /** Reason code (no_source, pc_only, unsupported_browser, storage, …) or a backend message. */
  detail: string
  sessionId: string | null
  recordingId: string | null
  segment: number | null
  /** Epoch ms of the segment start (for the elapsed time display). */
  since: number | null
  bytes: number
  mime: string
  /** Sound captured by the PC app for the current segment. */
  audio?: AudioInfo | null
}

export interface Drive {
  sessionId: string | null
  /** Live, paused or demo drive (a pause is NOT the end of a session). */
  active: boolean
}
export interface RecorderInput {
  enabled: boolean
  quality: Quality
  mode: SourceMode
  device: string
  de: boolean
  drive: Drive
  /** Only the PC (localhost) records; LAN devices only watch. */
  pc: boolean
}

interface SourceLike {
  get(): { stream: MediaStream | null }
  subscribe(fn: () => void): () => void
  use(user: SourceUser, mode: SourceMode, device?: string, de?: boolean): void
  release(user: SourceUser): void
}
type MediaRecorderCtor = {
  new (stream: MediaStream, options?: MediaRecorderOptions & Record<string, unknown>): MediaRecorder
  isTypeSupported(type: string): boolean
}
export interface UploadResult {
  ok: boolean
  status: number
  detail: string
  body?: unknown
}
export interface RecorderDeps {
  source: SourceLike
  /** JSON request to /api (throws on HTTP errors). */
  request: <T>(path: string, init?: RequestInit) => Promise<T>
  /** POST raw bytes to /api; never throws. */
  upload: (path: string, body: Blob) => Promise<UploadResult>
  MediaRecorder?: MediaRecorderCtor
  /** Epoch milliseconds (sub-millisecond resolution). */
  now: () => number
  sleep?: (ms: number) => Promise<void>
  beacon?: (url: string, body: string) => boolean
  clientId?: string
}

interface Segment {
  id: string | null
  sessionId: string
  stream: MediaStream
  recorder: MediaRecorder | null
  seq: number
  queue: Blob[]
  queued: number
  pumping: Promise<void> | null
  startedAt: number
  clockOffsetMs: number
  stopping: boolean
  /** Chunks can no longer be stored (storage full, upload failed). */
  broken: string
}

const idle = (): RecStatus => ({
  state: 'off',
  detail: '',
  sessionId: null,
  recordingId: null,
  segment: null,
  since: null,
  bytes: 0,
  mime: '',
})

export class SessionRecorder {
  status: RecStatus = idle()
  private input: RecorderInput | null = null
  private seg: Segment | null = null
  private starting = false
  private blocked: string | null = null
  private retryAt = 0
  private inactiveSince: number | null = null
  private listeners = new Set<(s: RecStatus) => void>()
  private readonly clientId: string
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private deps: RecorderDeps) {
    this.clientId = deps.clientId || Math.random().toString(36).slice(2) + Date.now().toString(36)
    this.sleep = deps.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)))
    deps.source.subscribe(() => this.evaluate())
  }

  subscribe(fn: (s: RecStatus) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
  private set(patch: Partial<RecStatus>) {
    this.status = { ...this.status, ...patch }
    this.listeners.forEach((fn) => fn(this.status))
  }
  get recording(): boolean {
    return !!this.seg && !this.seg.stopping
  }

  update(input: RecorderInput) {
    const before = this.input
    this.input = input
    if (!before || before.enabled !== input.enabled || before.quality !== input.quality) this.blocked = null
    this.evaluate()
  }
  /** Manual retry after a failure (e.g. storage limit raised). */
  retry() {
    this.blocked = null
    this.retryAt = 0
    this.evaluate()
  }

  evaluate() {
    const inp = this.input
    const { source } = this.deps
    if (!inp || !inp.enabled) {
      if (this.seg) this.stop('disabled')
      source.release('record')
      if (!this.seg) this.set({ ...idle(), state: 'off' })
      return
    }
    if (!inp.pc || !this.deps.MediaRecorder || !pickMime(this.deps.MediaRecorder.isTypeSupported)) {
      source.release('record')
      this.set({ state: 'unsupported', detail: !inp.pc ? 'pc_only' : 'unsupported_browser' })
      return
    }
    if (inp.mode !== 'camera' && inp.mode !== 'screen') {
      if (this.seg) this.stop('source_changed')
      source.release('record')
      if (!this.seg) this.set({ state: 'no_source', detail: 'mode' })
      return
    }
    source.use('record', inp.mode, inp.device, inp.de)
    const stream = source.get().stream
    const seg = this.seg
    if (seg && !seg.stopping) {
      if (seg.sessionId !== inp.drive.sessionId && inp.drive.sessionId) return void this.stop('session_changed')
      if (seg.stream !== stream) return void this.stop('source_lost')
    }
    const active = inp.drive.active && !!inp.drive.sessionId
    if (!active) {
      if (seg && !seg.stopping) {
        this.inactiveSince ??= this.deps.now()
        if (this.deps.now() - this.inactiveSince >= END_GRACE_MS) this.stop('session_end')
      } else if (!this.seg && !['saved', 'failed', 'saving'].includes(this.status.state))
        this.set({ state: 'ready', detail: '' })
      return
    }
    this.inactiveSince = null
    if (this.seg || this.starting) return
    if (!stream) return void this.set({ state: 'no_source', detail: 'no_stream' })
    if (this.blocked === inp.drive.sessionId || this.deps.now() < this.retryAt) return
    this.start(inp.drive.sessionId!, stream, inp)
  }

  private async start(sessionId: string, stream: MediaStream, inp: RecorderInput) {
    const MR = this.deps.MediaRecorder!
    const mime = pickMime(MR.isTypeSupported)!
    const track = stream.getVideoTracks()[0]
    const settings = (track?.getSettings?.() || {}) as MediaTrackSettings
    this.starting = true
    try {
      const sent = this.deps.now()
      const created = await this.deps.request<{ id: string; segment: number; server_time: number; audio?: AudioInfo }>('/recordings', {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          client_id: this.clientId,
          mime,
          quality: inp.quality,
          bitrate: QUALITY[inp.quality].bitrate,
          width: settings.width,
          height: settings.height,
          fps: settings.frameRate,
        }),
      })
      const received = this.deps.now()
      // Server clock minus browser clock, measured around one request.
      const clockOffsetMs = created.server_time * 1000 - (sent + received) / 2
      const now = this.input
      if (!now?.enabled || now.drive.sessionId !== sessionId || this.deps.source.get().stream !== stream) {
        await this.deps.request(`/recordings/${created.id}/finish`, {
          method: 'POST',
          body: JSON.stringify({ reason: 'cancelled' }),
        })
        return
      }
      const recorder = new MR(stream, {
        mimeType: mime,
        videoBitsPerSecond: QUALITY[inp.quality].bitrate,
        // Keyframe every 2 s: precise seeking (ignored by older browsers).
        videoKeyFrameIntervalDuration: KEYFRAME_MS,
      })
      const seg: Segment = {
        id: created.id,
        sessionId,
        stream,
        recorder,
        seq: 0,
        queue: [],
        queued: 0,
        pumping: null,
        startedAt: 0,
        clockOffsetMs,
        stopping: false,
        broken: '',
      }
      recorder.ondataavailable = (e: BlobEvent) => {
        if (!e.data || !e.data.size || seg.broken) return
        seg.queue.push(e.data)
        seg.queued += e.data.size
        if (seg.queued > MAX_QUEUED_BYTES) {
          seg.broken = 'upload_too_slow'
          this.fail(seg, 'upload_too_slow')
          return
        }
        this.pump(seg)
      }
      recorder.onerror = () => this.fail(seg, 'recorder_error')
      // Not requested by us: the source track ended (OBS camera stopped).
      recorder.onstop = () => {
        if (!seg.stopping) this.stop('source_lost')
      }
      this.seg = seg
      recorder.start(CHUNK_MS)
      // Video time 0 = frames from this moment on; converted to the server clock.
      seg.startedAt = this.deps.now()
      this.set({
        state: 'recording',
        detail: '',
        sessionId,
        recordingId: created.id,
        segment: created.segment,
        since: seg.startedAt,
        bytes: 0,
        mime,
        audio: created.audio || null,
      })
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      // Full storage needs the user; anything else is retried automatically.
      if (storageProblem(message)) this.blocked = sessionId
      else this.retryAt = this.deps.now() + RETRY_START_MS
      this.set({ state: 'failed', detail: message, sessionId })
    } finally {
      this.starting = false
    }
  }

  private pump(seg: Segment) {
    if (seg.pumping) return seg.pumping
    seg.pumping = (async () => {
      while (seg.queue.length && !seg.broken) {
        const blob = seg.queue[0]
        const first = seg.seq === 0
        const query = first
          ? `?started_at=${((seg.startedAt + seg.clockOffsetMs) / 1000).toFixed(4)}&clock_offset_ms=${seg.clockOffsetMs.toFixed(2)}`
          : ''
        let result: UploadResult | null = null
        for (let attempt = 0; attempt < RETRIES; attempt++) {
          result = await this.deps.upload(`/recordings/${seg.id}/chunks/${seg.seq}${query}`, blob)
          if (result.ok || (result.status >= 400 && result.status < 500) || result.status === 507) break
          await this.sleep(500 * 2 ** attempt) // PC app busy or briefly unreachable
        }
        if (!result?.ok) {
          const storage = result?.status === 507
          seg.broken = storage ? 'storage' : 'upload_failed'
          this.fail(seg, result?.detail || seg.broken, storage)
          break
        }
        seg.queue.shift()
        seg.queued -= blob.size
        seg.seq += 1
        if (this.seg === seg) {
          const audio = (result.body as { audio?: AudioInfo } | undefined)?.audio
          this.set({
            bytes: this.status.bytes + blob.size,
            ...(audio && JSON.stringify(audio) !== JSON.stringify(this.status.audio) ? { audio } : {}),
          })
        }
      }
      seg.pumping = null
    })()
    return seg.pumping
  }

  /** Storage full or uploads impossible: end the segment, keep telemetry running. */
  private fail(seg: Segment, detail: string, block = true) {
    if (block) this.blocked = seg.sessionId
    this.finishSegment(seg, 'failed', detail)
  }

  stop(reason: string) {
    const seg = this.seg
    if (!seg || seg.stopping) return
    this.finishSegment(seg, reason)
  }

  private async finishSegment(seg: Segment, reason: string, failure = '') {
    if (seg.stopping) return
    seg.stopping = true
    this.inactiveSince = null
    if (!failure) this.set({ state: 'saving', detail: reason })
    else this.set({ state: 'failed', detail: failure })
    const recorder = seg.recorder
    if (recorder && recorder.state !== 'inactive') {
      await new Promise<void>((resolve) => {
        recorder.onstop = () => resolve()
        try {
          recorder.stop() // delivers the last chunk before "stop"
        } catch {
          resolve()
        }
      })
    }
    while (seg.pumping) await seg.pumping
    if (this.seg === seg) this.seg = null
    try {
      await this.deps.request(`/recordings/${seg.id}/finish`, {
        method: 'POST',
        body: JSON.stringify({ reason, duration_ms: Math.round(this.deps.now() - seg.startedAt) }),
      })
      if (failure) return this.evaluate()
      // Indexing happens in the PC app; wait until it reports the result.
      for (let i = 0; i < 600; i++) {
        const r = await this.deps.request<{ status: string; error: string | null }>(`/recordings/${seg.id}`)
        if (r.status === 'ready' || r.status === 'failed') {
          if (!this.seg)
            this.set(
              r.status === 'ready'
                ? { state: 'saved', detail: reason, recordingId: seg.id }
                : { state: 'failed', detail: r.error || 'finalize_failed' },
            )
          break
        }
        await this.sleep(1000)
      }
    } catch (e) {
      if (!this.seg) this.set({ state: 'failed', detail: e instanceof Error ? e.message : String(e) })
    }
    this.evaluate()
  }

  /** Tab is being closed: tell the PC app (the last chunk may be lost). */
  pageHide() {
    const seg = this.seg
    if (!seg?.id || !this.deps.beacon) return
    this.deps.beacon(`/api/recordings/${seg.id}/finish`, JSON.stringify({ reason: 'page_closed' }))
  }
}

/* ---------- presentation ---------- */

export interface RecView {
  de: string
  en: string
  tone: 'muted' | 'green' | 'red' | 'yellow' | 'cyan'
}
const DETAILS: Record<string, [string, string]> = {
  pc_only: ['Aufnahme nur im Browser auf dem PC (localhost).', 'Recording only in the browser on the PC (localhost).'],
  unsupported_browser: [
    'Dieser Browser kann kein WebM aufnehmen. Chrome oder Edge verwenden.',
    'This browser cannot record WebM. Use Chrome or Edge.',
  ],
  mode: [
    'Als Videoquelle „OBS Virtual Camera / Webcam“ oder „Fenster freigeben“ wählen.',
    'Select "OBS Virtual Camera / Webcam" or "Share window" as video source.',
  ],
  no_stream: [
    'Keine Videoquelle: In OBS die virtuelle Kamera starten. Telemetrie wird weiter aufgezeichnet.',
    'No video source: start the OBS virtual camera. Telemetry keeps recording.',
  ],
  storage: ['Speicherlimit oder Festplatte voll.', 'Storage limit reached or disk full.'],
  upload_failed: [
    'Videodaten konnten nicht an das PC-Programm übertragen werden.',
    'Video data could not be sent to the PC app.',
  ],
  upload_too_slow: ['Speichern zu langsam – Aufnahme abgebrochen.', 'Saving too slow – recording stopped.'],
  recorder_error: ['Der Browser hat die Aufnahme abgebrochen.', 'The browser stopped the recording.'],
}

export function recDetail(detail: string, de: boolean): string {
  const known = DETAILS[detail]
  if (known) return de ? known[0] : known[1]
  if (/limit/i.test(detail)) return de ? 'Video-Speicherlimit erreicht. Limit erhöhen oder Videos löschen.' : detail
  if (/disk/i.test(detail)) return de ? 'Zu wenig freier Speicherplatz für das Video.' : detail
  if (/another browser tab/i.test(detail))
    return de ? 'Ein anderer Browser-Tab nimmt bereits auf.' : 'Another browser tab is already recording.'
  return detail
}

export function recView(status: RecStatus, now: number): RecView {
  switch (status.state) {
    case 'recording': {
      const s = status.since !== null ? Math.max(0, Math.floor((now - status.since) / 1000)) : 0
      const clock = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
      return { de: `AUFNAHME LÄUFT ${clock}`, en: `RECORDING ${clock}`, tone: 'red' }
    }
    case 'ready':
      return { de: 'AUFNAHME BEREIT', en: 'RECORDING READY', tone: 'muted' }
    case 'no_source':
      return { de: 'VIDEOQUELLE FEHLT', en: 'NO VIDEO SOURCE', tone: 'yellow' }
    case 'saving':
      return { de: 'AUFNAHME WIRD GESPEICHERT', en: 'SAVING RECORDING', tone: 'cyan' }
    case 'saved':
      return { de: 'AUFNAHME GESPEICHERT', en: 'RECORDING SAVED', tone: 'green' }
    case 'failed':
      return { de: 'AUFNAHME FEHLGESCHLAGEN', en: 'RECORDING FAILED', tone: 'red' }
    case 'unsupported':
      return { de: 'AUFNAHME NICHT MÖGLICH', en: 'RECORDING UNAVAILABLE', tone: 'muted' }
    default:
      return { de: 'AUFNAHME AUS', en: 'RECORDING OFF', tone: 'muted' }
  }
}
