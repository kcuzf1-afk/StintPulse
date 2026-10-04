import { beforeEach, expect, test } from 'vitest'
import { END_GRACE_MS, RETRY_START_MS, SessionRecorder, pickMime, recDetail, recView, type RecorderInput, type UploadResult } from './recorder'
import type { SourceMode, SourceUser } from './videoSource'

/* ---------- fakes: video source, MediaRecorder, PC app ---------- */

class FakeSource {
  stream: MediaStream | null = null
  uses: Array<[SourceUser, SourceMode]> = []
  released: SourceUser[] = []
  private fns = new Set<() => void>()
  get() {
    return { stream: this.stream }
  }
  subscribe(fn: () => void) {
    this.fns.add(fn)
    return () => this.fns.delete(fn)
  }
  use(user: SourceUser, mode: SourceMode) {
    this.uses.push([user, mode])
  }
  release(user: SourceUser) {
    this.released.push(user)
  }
  setStream(s: MediaStream | null) {
    this.stream = s
    this.fns.forEach((fn) => fn())
  }
}
const stream = (label = 'OBS Virtual Camera') =>
  ({
    getVideoTracks: () => [{ label, getSettings: () => ({ width: 1920, height: 1080, frameRate: 60 }) }],
  }) as unknown as MediaStream

let recorders: FakeRecorder[] = []
class FakeRecorder {
  static isTypeSupported = (t: string) => t.startsWith('video/webm')
  state: 'inactive' | 'recording' = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  timeslice = 0
  constructor(
    public stream: MediaStream,
    public options: Record<string, unknown>,
  ) {
    recorders.push(this)
  }
  start(ms: number) {
    this.timeslice = ms
    this.state = 'recording'
  }
  emit(size: number) {
    this.ondataavailable?.({ data: new Blob([new Uint8Array(size)]) })
  }
  stop() {
    this.state = 'inactive'
    queueMicrotask(() => {
      this.emit(10) // MediaRecorder delivers the last chunk before "stop"
      this.onstop?.()
    })
  }
  /** The recorded track ended (OBS virtual camera stopped). */
  sourceEnded() {
    this.state = 'inactive'
    this.onstop?.()
  }
}

let clock = 1_000_000
let calls: Array<{ path: string; body?: Record<string, unknown> }> = []
let uploads: string[] = []
let uploadResult: UploadResult = { ok: true, status: 200, detail: '' }
let created = 0
let createError: Error | null = null
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve()
}

function makeRecorder(source: FakeSource) {
  return new SessionRecorder({
    source,
    clientId: 'tab-test',
    MediaRecorder: FakeRecorder as never,
    now: () => clock,
    sleep: async () => {},
    request: async <T,>(path: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ path, body })
      if (path === '/recordings') {
        if (createError) throw createError
        created += 1
        // PC clock runs 2.5 s ahead of the browser clock.
        return {
          id: 'r' + created,
          segment: created,
          server_time: (clock + 2500) / 1000,
          audio: { status: 'recording', mode: 'game' },
        } as T
      }
      if (/^\/recordings\/r\d+$/.test(path)) return { status: 'ready', error: null } as T
      return {} as T
    },
    upload: async (path) => {
      uploads.push(path)
      return uploadResult
    },
  })
}
const input = (patch: Partial<RecorderInput> = {}): RecorderInput => ({
  enabled: true,
  quality: 'standard',
  mode: 'camera',
  device: '',
  de: true,
  drive: { sessionId: 's1', active: true },
  pc: true,
  ...patch,
})

beforeEach(() => {
  recorders = []
  calls = []
  uploads = []
  created = 0
  createError = null
  uploadResult = { ok: true, status: 200, detail: '' }
  clock = 1_000_000
})

test('WebM codecs are chosen at runtime, MP4 is never used', () => {
  expect(pickMime(() => true)).toBe('video/webm;codecs=vp8')
  expect(pickMime((t) => t === 'video/webm;codecs=vp9')).toBe('video/webm;codecs=vp9')
  expect(pickMime((t) => t === 'video/webm')).toBe('video/webm')
  expect(pickMime((t) => t.startsWith('video/mp4'))).toBeNull()
  expect(pickMime(undefined)).toBeNull()
})

test('starts with an active drive and a running source, maps time to the PC clock', async () => {
  const source = new FakeSource()
  source.stream = stream()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  expect(rec.status.state).toBe('recording')
  expect(source.uses[0]).toEqual(['record', 'camera'])
  const r = recorders[0]
  expect(r.timeslice).toBe(2000)
  expect(r.options).toMatchObject({ mimeType: 'video/webm;codecs=vp8', videoBitsPerSecond: 6_000_000 })
  expect(calls[0].body).toMatchObject({ session_id: 's1', client_id: 'tab-test', width: 1920, fps: 60 })
  expect(rec.status.audio).toEqual({ status: 'recording', mode: 'game' })
  uploadResult = { ok: true, status: 200, detail: '', body: { audio: { status: 'failed', mode: 'game', error: 'disk' } } }
  r.emit(1000)
  r.emit(2000)
  await flush()
  expect(rec.status.audio).toMatchObject({ status: 'failed', error: 'disk' }) // sound problem reported, video goes on
  expect(rec.status.state).toBe('recording')
  // Chunks in order; the first one carries video time 0 on the PC clock.
  expect(uploads[0]).toBe(`/recordings/r1/chunks/0?started_at=${((clock + 2500) / 1000).toFixed(4)}&clock_offset_ms=2500.00`)
  expect(uploads[1]).toBe('/recordings/r1/chunks/1')
  expect(rec.status.bytes).toBe(3000)
})

test('a pause keeps recording; only a longer stop ends and saves the segment', async () => {
  const source = new FakeSource()
  source.stream = stream()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  rec.update(input({ drive: { sessionId: 's1', active: false } }))
  clock += END_GRACE_MS - 1000
  rec.evaluate()
  expect(rec.status.state).toBe('recording')
  rec.update(input()) // drive continues after a short break
  clock += END_GRACE_MS * 2
  rec.evaluate()
  expect(rec.status.state).toBe('recording')
  rec.update(input({ drive: { sessionId: 's1', active: false } }))
  clock += END_GRACE_MS + 1
  rec.evaluate()
  await flush()
  await flush()
  expect(calls.some((c) => c.path === '/recordings/r1/finish' && c.body?.reason === 'session_end')).toBe(true)
  expect(rec.status.state).toBe('saved')
  expect(uploads).toHaveLength(1) // the last chunk (delivered on stop) was stored before "finish"
})

test('missing source is reported and recording starts once it is there', async () => {
  const source = new FakeSource()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  expect(rec.status).toMatchObject({ state: 'no_source', detail: 'no_stream' })
  expect(recorders).toHaveLength(0)
  source.setStream(stream())
  await flush()
  expect(rec.status.state).toBe('recording')
})

test('source drop-out closes the segment; a new segment starts when it is back', async () => {
  const source = new FakeSource()
  source.stream = stream()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  recorders[0].emit(500)
  recorders[0].sourceEnded()
  source.setStream(null)
  await flush()
  await flush()
  expect(calls.find((c) => c.path === '/recordings/r1/finish')?.body?.reason).toBe('source_lost')
  expect(rec.status.state).toBe('no_source')
  source.setStream(stream())
  await flush()
  expect(rec.status).toMatchObject({ state: 'recording', recordingId: 'r2', segment: 2 })
})

test('a new session closes the old recording and starts one for the new session', async () => {
  const source = new FakeSource()
  source.stream = stream()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  rec.update(input({ drive: { sessionId: 's2', active: true } }))
  await flush()
  await flush()
  expect(calls.find((c) => c.path === '/recordings/r1/finish')?.body?.reason).toBe('session_changed')
  const second = calls.filter((c) => c.path === '/recordings')[1]
  expect(second.body?.session_id).toBe('s2')
  expect(rec.status).toMatchObject({ state: 'recording', sessionId: 's2' })
})

test('full storage stops the recording with a message and does not loop', async () => {
  const source = new FakeSource()
  source.stream = stream()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  uploadResult = { ok: false, status: 507, detail: 'Video storage limit reached (20480 MB).' }
  recorders[0].emit(1000)
  await flush()
  await flush()
  expect(rec.status.state).toBe('failed')
  expect(recDetail(rec.status.detail, true)).toContain('Speicherlimit')
  rec.update(input())
  await flush()
  expect(recorders).toHaveLength(1) // same session: no automatic restart
  rec.retry()
  uploadResult = { ok: true, status: 200, detail: '' }
  await flush()
  expect(recorders).toHaveLength(2) // manual retry after the limit was raised
})

test('unreachable PC app is retried later instead of giving up', async () => {
  const source = new FakeSource()
  source.stream = stream()
  createError = new Error('Failed to fetch')
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  expect(rec.status.state).toBe('failed')
  createError = null
  rec.evaluate()
  await flush()
  expect(recorders).toHaveLength(0)
  clock += RETRY_START_MS + 1
  rec.evaluate()
  await flush()
  expect(rec.status.state).toBe('recording')
})

test('switching off stops and releases the camera; LAN devices never record', async () => {
  const source = new FakeSource()
  source.stream = stream()
  const rec = makeRecorder(source)
  rec.update(input())
  await flush()
  rec.update(input({ enabled: false }))
  await flush()
  await flush()
  expect(calls.find((c) => c.path === '/recordings/r1/finish')?.body?.reason).toBe('disabled')
  expect(source.released).toContain('record')
  const phone = makeRecorder(new FakeSource())
  phone.update(input({ pc: false }))
  expect(phone.status).toMatchObject({ state: 'unsupported', detail: 'pc_only' })
})

test('state labels are distinct', () => {
  const states = ['ready', 'recording', 'no_source', 'saving', 'saved', 'failed'] as const
  const labels = states.map((state) => recView({ state, detail: '', sessionId: null, recordingId: null, segment: null, since: 0, bytes: 0, mime: '' }, 65000).de)
  expect(labels).toEqual([
    'AUFNAHME BEREIT',
    'AUFNAHME LÄUFT 1:05',
    'VIDEOQUELLE FEHLT',
    'AUFNAHME WIRD GESPEICHERT',
    'AUFNAHME GESPEICHERT',
    'AUFNAHME FEHLGESCHLAGEN',
  ])
})
