import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import { OnboardSender, OnboardViewer, isPcHost, type RelayDeps, type ViewerState } from './relay'
import Onboard from './Onboard'
import type { Settings } from './types'

/* In-memory signaling hub with the same routing rules as backend/ac_agent/relay.py */
class FakeWS {
  readyState = 0
  id = ''
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  constructor(
    public hub: Hub,
    public role: string,
  ) {}
  bufferedAmount = 0
  send(raw: string | Blob) {
    if (typeof raw === 'string') this.hub.receive(this, raw)
    else this.hub.frame(this, raw)
  }
  close(code = 1000) {
    if (this.readyState === 3) return
    this.readyState = 3
    this.hub.leave(this)
    this.onclose?.({ code })
  }
}
class Hub {
  sender: FakeWS | null = null
  live = false
  viewers = new Map<string, FakeWS>()
  frameViewers = new Set<string>()
  n = 0
  deny = false
  counts() {
    if (this.sender)
      this.deliver(this.sender, {
        type: 'viewers',
        count: this.viewers.size,
        frames: this.frameViewers.size,
        active: this.live,
      })
  }
  frame(ws: FakeWS, blob: Blob) {
    if (ws !== this.sender || !this.live) return
    this.frameViewers.forEach((id) => {
      const v = this.viewers.get(id)
      if (v) queueMicrotask(() => v.readyState === 1 && v.onmessage?.({ data: blob as unknown as string }))
    })
  }
  deliver(ws: FakeWS, m: object) {
    queueMicrotask(() => ws.readyState === 1 && ws.onmessage?.({ data: JSON.stringify(m) }))
  }
  open = (role: string) => {
    const ws = new FakeWS(this, role)
    queueMicrotask(() => {
      if (this.deny) return ws.close(1008)
      ws.readyState = 1
      ws.id = role + ++this.n
      ws.onopen?.()
      if (role === 'sender') {
        this.sender = ws
        this.live = false
        this.deliver(ws, { type: 'welcome', id: ws.id })
      } else {
        this.viewers.set(ws.id, ws)
        this.deliver(ws, { type: 'welcome', id: ws.id, sender: this.live })
      }
      this.counts()
    })
    return ws as unknown as WebSocket
  }
  receive(ws: FakeWS, raw: string) {
    const m = JSON.parse(raw)
    if (ws.role === 'sender') {
      if (m.type === 'online' || m.type === 'offline') {
        this.live = m.type === 'online'
        this.viewers.forEach((v) => this.deliver(v, { type: 'sender-' + m.type }))
        this.counts()
      } else {
        const target = this.viewers.get(m.to)
        if (target) this.deliver(target, { ...m, from: ws.id, to: undefined })
      }
    } else if (m.type === 'frames') {
      if (m.on) this.frameViewers.add(ws.id)
      else this.frameViewers.delete(ws.id)
      this.counts()
    } else if (this.sender) this.deliver(this.sender, { ...m, from: ws.id })
  }
  leave(ws: FakeWS) {
    if (ws === this.sender) {
      this.sender = null
      this.live = false
      this.viewers.forEach((v) => this.deliver(v, { type: 'sender-offline' }))
    } else if (this.viewers.delete(ws.id) && this.sender) {
      this.frameViewers.delete(ws.id)
      this.deliver(this.sender, { type: 'viewer-left', from: ws.id })
      this.counts()
    }
  }
}

/* Fake RTCPeerConnection: sender offer + viewer answer => viewer receives the stream. */
const offers = new Map<string, FakePeer>()
const answers = new Map<string, FakePeer>()
let seq = 0
class FakePeer {
  static all: FakePeer[] = []
  stream: MediaStream | null = null
  localDescription: RTCSessionDescriptionInit | null = null
  remote: RTCSessionDescriptionInit | null = null
  connectionState = 'new'
  candidates: unknown[] = []
  ontrack: ((e: { streams: MediaStream[] }) => void) | null = null
  onicecandidate: ((e: { candidate: { toJSON: () => object } | null }) => void) | null = null
  onconnectionstatechange: (() => void) | null = null
  constructor() {
    FakePeer.all.push(this)
  }
  addTrack(_: unknown, stream: MediaStream) {
    this.stream = stream
  }
  async createOffer() {
    const sdp = 'offer-' + ++seq
    offers.set(sdp, this)
    return { type: 'offer' as const, sdp }
  }
  async createAnswer() {
    const sdp = 'answer-' + this.remote!.sdp
    answers.set(sdp, this)
    return { type: 'answer' as const, sdp }
  }
  async setLocalDescription(d: RTCSessionDescriptionInit) {
    this.localDescription = d
    this.onicecandidate?.({ candidate: { toJSON: () => ({ candidate: 'host ' + d.sdp }) } })
  }
  static blocked = false
  async setRemoteDescription(d: RTCSessionDescriptionInit) {
    this.remote = d
    if (d.type === 'answer' && !FakePeer.blocked) {
      const viewer = answers.get(d.sdp!)!
      const sender = offers.get(viewer.remote!.sdp!)!
      viewer.connectionState = 'connected'
      viewer.ontrack?.({ streams: [sender.stream!] })
    }
  }
  async addIceCandidate(c: unknown) {
    this.candidates.push(c)
  }
  close() {
    this.connectionState = 'closed'
  }
  fail() {
    this.connectionState = 'failed'
    this.onconnectionstatechange?.()
  }
}
const fakeStream = (id: string) =>
  ({ id, getTracks: () => [{ kind: 'video', id }] }) as unknown as MediaStream

let hub: Hub
let deps: RelayDeps
beforeEach(() => {
  hub = new Hub()
  FakePeer.all = []
  FakePeer.blocked = false
  deps = {
    open: hub.open as RelayDeps['open'],
    peer: () => new FakePeer() as unknown as RTCPeerConnection,
    retryMs: 10,
  }
})
const jpeg = (id: string) => ({ id, size: 1000, type: 'image/jpeg' }) as unknown as Blob

function watch() {
  const seen: { stream: MediaStream | null; state: ViewerState; history: ViewerState[] } = {
    stream: null,
    state: 'connecting',
    history: [],
  }
  const v = new OnboardViewer(
    deps,
    (s) => (seen.stream = s),
    (s) => {
      seen.state = s
      seen.history.push(s)
    },
  )
  return { v, seen }
}

test('phone receives the PC camera stream peer-to-peer', async () => {
  const sender = new OnboardSender(deps)
  const counts: number[] = []
  sender.onViewers = (n) => counts.push(n)
  const cam = fakeStream('obs')
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(cam)
  const { seen } = watch()
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  expect(seen.stream).toBe(cam)
  expect(counts.at(-1)).toBe(1)
  // ICE candidates were exchanged in both directions, no STUN/TURN involved.
  expect(FakePeer.all.every((p) => p.candidates.length > 0)).toBe(true)
})

test('viewer waits for the PC, then connects when the camera starts', async () => {
  const sender = new OnboardSender(deps)
  const { seen } = watch()
  await vi.waitFor(() => expect(seen.state).toBe('waiting'))
  expect(seen.stream).toBeNull()
  sender.setStream(fakeStream('obs'))
  await vi.waitFor(() => expect(seen.state).toBe('live'))
})

test('camera stop, camera switch and PC tab closing', async () => {
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('one'))
  const { seen } = watch()
  await vi.waitFor(() => expect(seen.stream?.id).toBe('one'))
  sender.setStream(null)
  await vi.waitFor(() => expect(seen.state).toBe('waiting'))
  expect(seen.stream).toBeNull()
  sender.setStream(fakeStream('two'))
  await vi.waitFor(() => expect(seen.stream?.id).toBe('two'))
  sender.stop()
  await vi.waitFor(() => expect(seen.state).toBe('waiting'))
  expect(seen.stream).toBeNull()
})

test('wrong access code is reported as denied', async () => {
  hub.deny = true
  const { v, seen } = watch()
  await vi.waitFor(() => expect(seen.state).toBe('denied'))
  v.stop()
})

test('failed LAN connection is reported and retried', async () => {
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'))
  const { seen } = watch()
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  const viewerPeer = FakePeer.all.find((p) => p.remote?.type === 'offer')!
  viewerPeer.fail()
  expect(seen.state).toBe('failed')
  expect(seen.stream).toBeNull()
  await vi.waitFor(() => expect(seen.state).toBe('live'))
})

test('connection that stays disconnected is rebuilt automatically', async () => {
  deps.disconnectMs = 20
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'))
  const { seen } = watch()
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  const first = FakePeer.all.find((p) => p.remote?.type === 'offer')!
  first.connectionState = 'disconnected'
  first.onconnectionstatechange?.()
  const before = seen.history.length
  await vi.waitFor(() => expect(seen.history.slice(before)).toContain('failed'))
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  expect(FakePeer.all.filter((p) => p.remote?.type === 'offer').length).toBe(2)
})

test('short disconnect that recovers keeps the stream', async () => {
  deps.disconnectMs = 50
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'))
  const { seen } = watch()
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  const peer = FakePeer.all.find((p) => p.remote?.type === 'offer')!
  peer.connectionState = 'disconnected'
  peer.onconnectionstatechange?.()
  peer.connectionState = 'connected'
  peer.onconnectionstatechange?.()
  await new Promise((r) => setTimeout(r, 80))
  expect(seen.state).toBe('live')
  expect(seen.stream?.id).toBe('obs')
})

test('pc host detection', () => {
  expect(isPcHost('localhost')).toBe(true)
  expect(isPcHost('127.0.0.1')).toBe(true)
  expect(isPcHost('192.168.178.49')).toBe(false)
})

const camera = {
  language: 'de',
  units: 'metric',
  video_mode: 'camera',
  video_url: '',
  video_device: '',
  video_offset_s: 0,
} as unknown as Settings

test('Onboard on the phone shows the PC video instead of opening a local camera', async () => {
  const getUserMedia = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia, enumerateDevices: vi.fn(async () => []) },
  })
  Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', {
    configurable: true,
    writable: true,
    value: null,
  })
  HTMLMediaElement.prototype.play = vi.fn(async () => {})
  render(<Onboard settings={camera} sample={null} relay={deps} hostname="192.168.178.49" />)
  await waitFor(() => expect(screen.getByTestId('viewer-state')).toHaveTextContent('Warte auf das Bild vom PC'))
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'))
  await waitFor(() => expect(screen.getByText('PC-BILD LIVE · WEBRTC')).toBeInTheDocument())
  expect((document.querySelector('video') as HTMLVideoElement).srcObject).toMatchObject({ id: 'obs' })
  expect(getUserMedia).not.toHaveBeenCalled() // the phone never opens its own camera
  expect(screen.queryByText('Kamera verbinden')).not.toBeInTheDocument()
})

test('firewall blocks WebRTC: phone falls back to frames via the PC server', async () => {
  FakePeer.blocked = true // UDP between the browsers never connects
  let n = 0
  deps.fallbackMs = 30
  deps.frameMs = 5
  deps.encode = async () => jpeg('frame-' + ++n)
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'), {} as HTMLVideoElement)
  const { v, seen } = watch()
  const frames: Array<Blob | null> = []
  const transports: string[] = []
  v.onFrame = (f) => frames.push(f)
  v.onTransport = (t) => transports.push(t)
  await vi.waitFor(() => expect(transports).toEqual(['frames']))
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  await vi.waitFor(() => expect(frames.filter(Boolean).length).toBeGreaterThan(2))
  expect(hub.frameViewers.size).toBe(1)
  // PC stops the camera: frames stop, phone shows waiting again.
  sender.setStream(null)
  await vi.waitFor(() => expect(seen.state).toBe('waiting'))
  v.stop()
  sender.stop()
})

test('PC encodes frames only while a phone needs them', async () => {
  const encode = vi.fn(async () => jpeg('x'))
  deps.encode = encode
  deps.frameMs = 5
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'), {} as HTMLVideoElement)
  const { seen } = watch() // WebRTC works: no frames requested
  await vi.waitFor(() => expect(seen.state).toBe('live'))
  await new Promise((r) => setTimeout(r, 40))
  expect(encode).not.toHaveBeenCalled()
  sender.stop()
})

test('slow link: frames are skipped instead of queued', async () => {
  const encode = vi.fn(async () => jpeg('x'))
  deps.encode = encode
  deps.frameMs = 5
  deps.fallbackMs = 10
  FakePeer.blocked = true
  const sender = new OnboardSender(deps)
  await vi.waitFor(() => expect(hub.sender).not.toBeNull())
  sender.setStream(fakeStream('obs'), {} as HTMLVideoElement)
  watch()
  await vi.waitFor(() => expect(encode).toHaveBeenCalled())
  hub.sender!.bufferedAmount = 5_000_000
  const calls = encode.mock.calls.length
  await new Promise((r) => setTimeout(r, 40))
  expect(encode.mock.calls.length).toBeLessThanOrEqual(calls + 1)
  sender.stop()
})
