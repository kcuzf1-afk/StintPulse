/**
 * The ONE onboard MediaStream of this browser tab (OBS Virtual Camera,
 * webcam or a shared window). Live view and recording use the same stream,
 * so neither interrupts the other and switching pages keeps it running.
 * The stream is released when no user (view, record) needs it any more.
 */
import { cameraError, needsPermission, pickCamera, videoInputs, type CameraInfo } from './camera'

export type SourceMode = 'none' | 'url' | 'file' | 'screen' | 'camera'
export type SourceUser = 'view' | 'record'

export interface SourceState {
  stream: MediaStream | null
  /** Camera/window label of the running stream. */
  label: string
  cameras: CameraInfo[]
  /** Localized, actionable error; empty when fine. */
  error: string
  mode: SourceMode
}

const EMPTY: SourceState = { stream: null, label: '', cameras: [], error: '', mode: 'none' }

export class VideoSource {
  private state: SourceState = { ...EMPTY }
  private listeners = new Set<(s: SourceState) => void>()
  private users = new Map<SourceUser, { device: string }>()
  private starting = false
  private de = true
  private watching = false
  private readonly onDeviceChange = async () => {
    const media = navigator.mediaDevices
    try {
      this.set({ cameras: videoInputs(await media.enumerateDevices()) })
    } catch {}
    // OBS virtual camera started again or a webcam was plugged in.
    if (!this.state.stream && this.state.mode === 'camera' && this.users.size) this.startCamera()
  }

  get(): SourceState {
    return this.state
  }
  subscribe(fn: (s: SourceState) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
  private set(patch: Partial<SourceState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((fn) => fn(this.state))
  }
  private t(a: string, b: string) {
    return this.de ? a : b
  }

  /** Declare a need for the source; starts the camera when required. */
  use(user: SourceUser, mode: SourceMode, device = '', de = true) {
    this.de = de
    this.users.set(user, { device })
    if (mode !== this.state.mode) {
      this.stopStream()
      this.set({ mode, error: '' })
    }
    this.watchDevices()
    if (mode === 'camera' && !this.state.stream && !this.state.error) this.startCamera(device)
  }
  release(user: SourceUser) {
    // Only a real change notifies (listeners may call release again).
    if (!this.users.delete(user) || this.users.size) return
    this.stopStream()
    this.unwatchDevices()
    this.state = { ...EMPTY }
    this.listeners.forEach((fn) => fn(this.state))
  }
  private watchDevices() {
    const media = navigator.mediaDevices
    const wanted = this.state.mode === 'camera' && this.users.size > 0
    if (wanted && !this.watching && media?.addEventListener) {
      media.addEventListener('devicechange', this.onDeviceChange)
      this.watching = true
    } else if (!wanted) this.unwatchDevices()
  }
  private unwatchDevices() {
    if (!this.watching) return
    navigator.mediaDevices?.removeEventListener?.('devicechange', this.onDeviceChange)
    this.watching = false
  }

  stopStream() {
    this.state.stream?.getTracks().forEach((t) => t.stop())
    if (this.state.stream || this.state.label) this.set({ stream: null, label: '' })
  }

  async startCamera(preferred?: string) {
    const media = navigator.mediaDevices
    if (this.starting) return
    this.starting = true
    const wanted = preferred ?? [...this.users.values()].find((u) => u.device)?.device ?? ''
    try {
      if (!media?.getUserMedia || !media.enumerateDevices)
        throw Object.assign(new Error('getUserMedia unavailable'), { name: 'SecurityError' })
      let list = videoInputs(await media.enumerateDevices())
      if (needsPermission(list)) {
        // Device names are only visible after camera access was allowed once.
        const probe = await media.getUserMedia({ video: true, audio: false })
        probe.getTracks().forEach((t) => t.stop())
        list = videoInputs(await media.enumerateDevices())
      }
      this.set({ cameras: list })
      const chosen = pickCamera(list, wanted)
      if (!chosen) throw Object.assign(new Error('no camera'), { name: 'NotFoundError' })
      this.stopStream()
      const next = await media.getUserMedia({
        video: {
          deviceId: chosen.deviceId ? { exact: chosen.deviceId } : undefined,
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          frameRate: { ideal: 60 },
        },
        audio: false,
      })
      if (!this.users.size || this.state.mode !== 'camera') {
        next.getTracks().forEach((t) => t.stop()) // nobody needs it any more
        return
      }
      const track = next.getVideoTracks()[0]
      this.set({ stream: next, label: track?.label || chosen.label, error: '' })
      if (track)
        track.onended = () => {
          if (this.state.stream !== next) return
          this.set({
            stream: null,
            label: '',
            error: this.t(
              'Kamera getrennt (OBS virtuelle Kamera gestoppt?). Sobald sie in OBS wieder läuft, verbindet die App automatisch – sonst „Erneut verbinden“.',
              'Camera disconnected (OBS virtual camera stopped?). The app reconnects when it runs again, otherwise click "Reconnect".',
            ),
          })
        }
    } catch (e) {
      this.set({ error: cameraError(e, this.de) })
    } finally {
      this.starting = false
    }
  }

  /** Share a window/screen (needs a click: browsers require a user gesture). */
  async captureScreen() {
    try {
      if (!navigator.mediaDevices?.getDisplayMedia)
        throw new Error('Screen capture requires localhost or HTTPS and a compatible browser.')
      const media = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 30 }, audio: false })
      this.stopStream()
      const track = media.getVideoTracks()[0]
      this.set({ stream: media, label: track?.label || this.t('Fenster', 'Window'), error: '' })
      if (track)
        track.onended = () => {
          if (this.state.stream === media) this.set({ stream: null, label: '' })
        }
    } catch (e) {
      this.set({ error: String(e) })
    }
  }

  /** Errors that are not about the source itself (e.g. a video file). */
  setError(error: string) {
    this.set({ error })
  }
}

export const videoSource = new VideoSource()
