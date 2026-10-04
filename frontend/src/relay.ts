/**
 * Live onboard video PC -> phone/tablet.
 *
 * Primary: WebRTC peer-to-peer inside the LAN. The PC dashboard (sender)
 * publishes its OBS camera / window stream; the backend only relays signaling
 * messages over /ws/onboard (protected by the access code). No STUN/TURN.
 *
 * Fallback: if no direct connection comes up within `fallbackMs` (typical
 * causes: Windows firewall blocks the browser's UDP on a "Public" network, or
 * the phone browser offers no usable candidates), the viewer asks for JPEG
 * frames. The PC encodes frames only while such a viewer exists and sends them
 * over the same WebSocket; the server forwards the newest frame.
 */

export type Role = 'sender' | 'viewer'
export type Transport = 'webrtc' | 'frames'
export interface RelayDeps {
  open: (role: Role) => WebSocket
  peer: (config: RTCConfiguration) => RTCPeerConnection
  /** Encode the current video picture as JPEG (fallback transport). */
  encode?: (video: HTMLVideoElement) => Promise<Blob | null>
  retryMs?: number
  disconnectMs?: number
  fallbackMs?: number
  frameMs?: number
  staleMs?: number
}
interface Signal {
  type: string
  from?: string
  to?: string
  id?: string
  count?: number
  frames?: number
  active?: boolean
  on?: boolean
  sender?: boolean
  sdp?: RTCSessionDescriptionInit
  candidate?: RTCIceCandidateInit | null
}
const LAN_ONLY: RTCConfiguration = { iceServers: [] }
const OPEN = 1
const MAX_BUFFERED = 1_500_000

abstract class Endpoint {
  protected ws: WebSocket | null = null
  protected stopped = false
  private timer: ReturnType<typeof setTimeout> | undefined
  private chain: Promise<void> = Promise.resolve()
  constructor(
    protected deps: RelayDeps,
    private role: Role,
  ) {}
  protected connect() {
    if (this.stopped) return
    const ws = this.deps.open(this.role)
    this.ws = ws
    ws.onopen = () => this.opened()
    ws.onmessage = (e: MessageEvent) => {
      if (typeof e.data !== 'string') return this.binary(e.data as Blob)
      let message: Signal
      try {
        message = JSON.parse(e.data)
      } catch {
        return
      }
      // Messages are handled strictly in order (ICE must follow its offer/answer).
      this.chain = this.chain.then(() => this.handle(message)).catch(() => {})
    }
    ws.onclose = (e: CloseEvent) => {
      if (this.ws !== ws) return
      this.closed(e?.code)
      if (!this.stopped)
        this.timer = setTimeout(() => this.connect(), this.deps.retryMs ?? 2000)
    }
  }
  protected send(message: Signal) {
    if (this.ws && this.ws.readyState === OPEN) this.ws.send(JSON.stringify(message))
  }
  protected binary(_: Blob) {}
  protected abstract opened(): void
  protected abstract closed(code?: number): void
  protected abstract handle(message: Signal): Promise<void>
  stop() {
    this.stopped = true
    clearTimeout(this.timer)
    const ws = this.ws
    this.ws = null
    this.closed()
    ws?.close()
  }
}

function plain(d: RTCSessionDescription | RTCSessionDescriptionInit | null) {
  return d ? { type: d.type, sdp: d.sdp } : undefined
}

/** ICE candidates may arrive before the matching description; buffer them. */
class IceBuffer {
  private pending: RTCIceCandidateInit[] = []
  ready = false
  async add(pc: RTCPeerConnection | null | undefined, candidate: RTCIceCandidateInit) {
    if (pc && this.ready) await pc.addIceCandidate(candidate).catch(() => {})
    else this.pending.push(candidate)
  }
  async flush(pc: RTCPeerConnection) {
    this.ready = true
    const queued = this.pending
    this.pending = []
    for (const c of queued) await pc.addIceCandidate(c).catch(() => {})
  }
  reset() {
    this.ready = false
    this.pending = []
  }
}

/** Default JPEG encoder: video picture -> canvas (max 1280 px wide) -> JPEG. */
export function canvasEncoder(): (video: HTMLVideoElement) => Promise<Blob | null> {
  const canvas = document.createElement('canvas')
  return (video) =>
    new Promise((resolve) => {
      const w = video.videoWidth,
        h = video.videoHeight
      if (!w || !h) return resolve(null)
      const scale = Math.min(1, 1280 / w)
      canvas.width = Math.round(w * scale)
      canvas.height = Math.round(h * scale)
      const g = canvas.getContext('2d')
      if (!g) return resolve(null)
      g.drawImage(video, 0, 0, canvas.width, canvas.height)
      canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.7)
    })
}

export class OnboardSender extends Endpoint {
  private peers = new Map<string, RTCPeerConnection>()
  private ice = new Map<string, IceBuffer>()
  private stream: MediaStream | null = null
  private video: HTMLVideoElement | null = null
  private frameViewers = 0
  private active = false
  private frameTimer: ReturnType<typeof setInterval> | undefined
  private encoding = false
  onViewers: (count: number) => void = () => {}
  constructor(deps: RelayDeps) {
    super(deps, 'sender')
    this.connect()
  }
  /** New/changed/removed local stream: viewers renegotiate automatically. */
  setStream(stream: MediaStream | null, video: HTMLVideoElement | null = null) {
    this.video = video
    if (stream === this.stream) return this.frames()
    this.stream = stream
    this.closePeers()
    this.send({ type: stream ? 'online' : 'offline' })
    this.frames()
  }
  protected opened() {
    if (this.stream) this.send({ type: 'online' })
  }
  protected closed() {
    this.closePeers()
    this.active = false
    this.frames()
  }
  stop() {
    super.stop()
    clearInterval(this.frameTimer)
    this.frameTimer = undefined
  }
  private closePeers() {
    this.peers.forEach((pc) => pc.close())
    this.peers.clear()
    this.ice.clear()
  }
  /** Encode JPEG frames only while this tab is active and someone needs them. */
  private frames() {
    const needed =
      !this.stopped && this.active && this.frameViewers > 0 && !!this.stream && !!this.video
    if (needed && !this.frameTimer)
      this.frameTimer = setInterval(() => this.pushFrame(), this.deps.frameMs ?? 66)
    else if (!needed && this.frameTimer) {
      clearInterval(this.frameTimer)
      this.frameTimer = undefined
    }
  }
  private async pushFrame() {
    const ws = this.ws
    if (this.encoding || !ws || ws.readyState !== OPEN || !this.video) return
    if (ws.bufferedAmount > MAX_BUFFERED) return // slow link: skip, never queue
    this.encoding = true
    try {
      const encode = this.deps.encode || (this.deps.encode = canvasEncoder())
      const blob = await encode(this.video)
      if (blob && this.ws === ws && ws.readyState === OPEN) ws.send(blob)
    } finally {
      this.encoding = false
    }
  }
  protected async handle(m: Signal) {
    const pc = m.from ? this.peers.get(m.from) : undefined
    switch (m.type) {
      case 'viewers':
        this.onViewers(m.count || 0)
        this.frameViewers = m.frames || 0
        this.active = !!m.active
        this.frames()
        break
      case 'request':
        if (this.stream && m.from) await this.offer(m.from)
        break
      case 'answer':
        if (pc && m.sdp && m.from) {
          await pc.setRemoteDescription(m.sdp)
          await this.ice.get(m.from)?.flush(pc)
        }
        break
      case 'ice':
        if (m.candidate && m.from) await this.ice.get(m.from)?.add(pc, m.candidate)
        break
      case 'viewer-left':
        if (pc && m.from) {
          pc.close()
          this.peers.delete(m.from)
          this.ice.delete(m.from)
        }
        break
    }
  }
  private async offer(id: string) {
    const stream = this.stream!
    this.peers.get(id)?.close()
    const pc = this.deps.peer(LAN_ONLY)
    this.peers.set(id, pc)
    this.ice.set(id, new IceBuffer())
    stream.getTracks().forEach((track) => pc.addTrack(track, stream))
    pc.onicecandidate = (e) =>
      this.send({ type: 'ice', to: id, candidate: e.candidate ? e.candidate.toJSON() : null })
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    this.send({ type: 'offer', to: id, sdp: plain(pc.localDescription) || offer })
  }
}

export type ViewerState = 'connecting' | 'waiting' | 'negotiating' | 'live' | 'failed' | 'denied'

export class OnboardViewer extends Endpoint {
  private pc: RTCPeerConnection | null = null
  private ice = new IceBuffer()
  private retry: ReturnType<typeof setTimeout> | undefined
  private fallback: ReturnType<typeof setTimeout> | undefined
  private stale: ReturnType<typeof setTimeout> | undefined
  private live = false
  transport: Transport = 'webrtc'
  onFrame: (frame: Blob | null) => void = () => {}
  onTransport: (transport: Transport) => void = () => {}
  constructor(
    deps: RelayDeps,
    private onStream: (stream: MediaStream | null) => void,
    private onState: (state: ViewerState) => void,
  ) {
    super(deps, 'viewer')
    this.setState('connecting')
    this.connect()
  }
  private setState(state: ViewerState) {
    this.live = state === 'live'
    if (this.live) clearTimeout(this.fallback)
    this.onState(state)
  }
  protected opened() {}
  protected closed(code?: number) {
    this.drop()
    clearTimeout(this.fallback)
    this.fallback = undefined
    if (!this.stopped) this.setState(code === 1008 ? 'denied' : 'connecting')
  }
  private drop() {
    clearTimeout(this.retry)
    clearTimeout(this.stale)
    this.pc?.close()
    this.pc = null
    this.ice.reset()
    this.onStream(null)
    this.onFrame(null)
  }
  private request() {
    if (this.transport === 'frames') {
      this.send({ type: 'frames', on: true })
      this.setState('waiting')
      return
    }
    this.setState('negotiating')
    this.send({ type: 'request' })
    // No direct picture in time: switch to frames over the server.
    if (!this.fallback)
      this.fallback = setTimeout(() => {
        this.fallback = undefined
        if (!this.live && !this.stopped) this.useFrames()
      }, this.deps.fallbackMs ?? 6000)
  }
  private useFrames() {
    this.transport = 'frames'
    this.onTransport('frames')
    this.drop()
    this.request()
  }
  protected binary(frame: Blob) {
    if (this.transport !== 'frames') return
    this.onFrame(frame)
    if (!this.live) this.setState('live')
    clearTimeout(this.stale)
    this.stale = setTimeout(() => {
      this.onFrame(null)
      this.setState('waiting')
    }, this.deps.staleMs ?? 3000)
  }
  protected async handle(m: Signal) {
    switch (m.type) {
      case 'welcome':
        if (m.sender || this.transport === 'frames') this.request()
        else this.setState('waiting')
        break
      case 'sender-online':
        this.drop()
        this.request()
        break
      case 'sender-offline':
        this.drop()
        this.setState('waiting')
        break
      case 'offer': {
        if (this.transport === 'frames') break
        // Candidates that raced ahead of this offer belong to it: keep them.
        const early = this.ice
        this.ice = new IceBuffer()
        this.drop()
        this.ice = early
        this.ice.ready = false
        const pc = this.deps.peer(LAN_ONLY)
        this.pc = pc
        pc.ontrack = (e) => {
          const stream = e.streams[0]
          if (stream) {
            this.onStream(stream)
            this.setState('live')
          }
        }
        pc.onicecandidate = (e) =>
          this.send({ type: 'ice', candidate: e.candidate ? e.candidate.toJSON() : null })
        const reconnect = () => {
          this.drop()
          this.setState('failed')
          this.retry = setTimeout(() => this.request(), this.deps.retryMs ?? 2000)
        }
        pc.onconnectionstatechange = () => {
          if (this.pc !== pc) return
          clearTimeout(this.retry)
          if (pc.connectionState === 'failed') reconnect()
          else if (pc.connectionState === 'disconnected')
            // Often transient (Wi-Fi hiccup); rebuild if it does not recover.
            this.retry = setTimeout(() => {
              if (this.pc === pc && pc.connectionState !== 'connected') reconnect()
            }, this.deps.disconnectMs ?? 3000)
        }
        if (m.sdp) await pc.setRemoteDescription(m.sdp)
        await this.ice.flush(pc)
        const answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        this.send({ type: 'answer', sdp: plain(pc.localDescription) || answer })
        break
      }
      case 'ice':
        if (m.candidate && this.transport === 'webrtc') await this.ice.add(this.pc, m.candidate)
        break
    }
  }
}

export const isPcHost = (hostname: string) =>
  ['localhost', '127.0.0.1', '::1', '[::1]'].includes(hostname)
