import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  Video,
  Maximize,
  PictureInPicture2,
  Monitor,
  Upload,
  Square,
  Camera,
  RotateCcw,
  Smartphone,
  Timer,
} from 'lucide-react'
import type { Settings, Sample } from './types'
import LapTimingHUD from './LapTimingHUD'
import Tacho from './Tacho'
import {
  contentRect,
  DEFAULT_HUD,
  loadHud,
  saveHud,
  type HudModel,
  type HudPrefs,
  type Rect,
} from './hud'
import { onboardSocket } from './api'
import { videoSource, type SourceState, type VideoSource } from './videoSource'
import {
  isPcHost,
  OnboardSender,
  OnboardViewer,
  type RelayDeps,
  type Transport,
  type ViewerState,
} from './relay'

const webrtc = () => typeof RTCPeerConnection !== 'undefined'
const relayDeps = (): RelayDeps => ({
  open: onboardSocket,
  peer: (config) => new RTCPeerConnection(config),
})
interface Props {
  settings: Settings | null
  sample: Sample | null
  replayTime?: number
  replaying?: boolean
  /** Persist the chosen camera (label) for the next start. */
  onDevice?: (label: string) => void
  /** Test hook: WebRTC transport (defaults to browser WebSocket/RTCPeerConnection). */
  relay?: RelayDeps
  /** Test hook: hostname deciding PC (sender) vs. phone/tablet (viewer). */
  hostname?: string
  /** Lap-timing HUD content (live, demo or replay); null = no HUD. */
  hud?: HudModel | null
  /** Video connection state, separate from telemetry (header display). */
  onVideoState?: (state: { live: boolean; label: string }) => void
  /** Test hook: the shared onboard stream (defaults to the app-wide one). */
  source?: VideoSource
}
export default function Onboard({
  settings,
  sample,
  replayTime,
  replaying,
  onDevice,
  relay,
  hostname = location.hostname,
  hud = null,
  onVideoState,
  source = videoSource,
}: Props) {
  const video = useRef<HTMLVideoElement>(null),
    container = useRef<HTMLDivElement>(null),
    videoBox = useRef<HTMLDivElement>(null)
  // The stream belongs to the app (shared with the recording), not to this
  // page: leaving Onboard does not stop a running recording.
  const [src, setSrc] = useState<SourceState>(() => source.get())
  useEffect(() => {
    setSrc(source.get())
    return source.subscribe(setSrc)
  }, [source])
  const [hudPrefs, setHudPrefsState] = useState<HudPrefs>(loadHud),
    [hudPanel, setHudPanel] = useState(false),
    [rect, setRect] = useState<Rect>({ x: 0, y: 0, w: 0, h: 0 }),
    [pipNote, setPipNote] = useState('')
  const setHudPrefs = (patch: Partial<HudPrefs>) =>
    setHudPrefsState((p) => {
      const next = { ...p, ...patch }
      saveHud(next)
      return next
    })
  const [file, setFile] = useState(''),
    [localError, setError] = useState('')
  const mode = settings?.video_mode || 'none'
  const de = settings?.language !== 'en'
  const tr = (a: string, b: string) => (de ? a : b)
  // The PC captures (OBS camera / window) and sends; LAN devices receive it.
  const liveMode = mode === 'camera' || mode === 'screen'
  const viewer = liveMode && !isPcHost(hostname)
  const sender = useRef<OnboardSender | null>(null)
  const [viewers, setViewers] = useState(0),
    [viewerState, setViewerState] = useState<ViewerState>('connecting'),
    [remoteLive, setRemoteLive] = useState(false),
    [transport, setTransport] = useState<Transport>('webrtc')
  const frame = useRef<HTMLImageElement>(null),
    frameUrl = useRef<string | null>(null)
  const showFrames = viewer && transport === 'frames'
  const capturing = !viewer && liveMode && !!src.stream
  const cameraLabel = liveMode ? src.label : ''
  const cameras = src.cameras
  const error = (liveMode && !viewer ? src.error : '') || localError
  useEffect(() => {
    if (viewer || !liveMode || (!relay && !webrtc())) return
    const s = new OnboardSender(relay || relayDeps())
    s.onViewers = setViewers
    sender.current = s
    return () => {
      s.stop()
      sender.current = null
      setViewers(0)
    }
  }, [viewer, liveMode])
  useEffect(() => {
    sender.current?.setStream(capturing ? src.stream : null, capturing ? video.current : null)
  }, [capturing, src.stream, viewer, liveMode])
  useEffect(() => {
    if (!viewer) return
    if (!relay && !webrtc()) {
      setError(tr('Dieser Browser unterstützt kein WebRTC.', 'This browser does not support WebRTC.'))
      return
    }
    const v = new OnboardViewer(
      relay || relayDeps(),
      (remote) => {
        if (video.current) {
          video.current.srcObject = remote
          if (remote) video.current.play().catch(() => {})
        }
        if (v.transport === 'webrtc') setRemoteLive(!!remote)
      },
      setViewerState,
    )
    v.onTransport = setTransport
    v.onFrame = (blob) => {
      const img = frame.current
      if (!img) return
      const previous = frameUrl.current
      frameUrl.current = blob ? URL.createObjectURL(blob) : null
      if (frameUrl.current) img.src = frameUrl.current
      else img.removeAttribute('src')
      if (previous) URL.revokeObjectURL(previous)
      setRemoteLive(!!blob)
    }
    return () => {
      v.stop()
      setRemoteLive(false)
      if (frameUrl.current) URL.revokeObjectURL(frameUrl.current)
      frameUrl.current = null
    }
  }, [viewer])
  useEffect(
    () => () => {
      if (file) URL.revokeObjectURL(file)
    },
    [file],
  )
  // Declare the need for the shared camera/window stream; released on leave
  // (the stream keeps running while the recording still needs it).
  useEffect(() => {
    setError('')
    if (viewer || !liveMode) source.release('view')
    else source.use('view', mode, settings?.video_device || '', de)
  }, [mode, viewer, source])
  useEffect(() => () => source.release('view'), [source])
  useEffect(() => {
    const v = video.current
    if (!v || viewer || !liveMode) return
    if (v.srcObject !== src.stream) {
      v.srcObject = src.stream
      if (src.stream) v.play().catch(() => {})
    }
  }, [src.stream, viewer, liveMode])
  const startCamera = (preferred?: string) => source.startCamera(preferred)
  useEffect(() => {
    if (video.current && mode === 'file' && replayTime !== undefined) {
      const target = replayTime + (settings?.video_offset_s || 0)
      if (Math.abs(video.current.currentTime - target) > 0.2)
        video.current.currentTime = Math.max(0, target)
      if (replaying) video.current.play().catch(() => {})
      else video.current.pause()
    }
  }, [replayTime, replaying, mode, settings?.video_offset_s])
  const capture = () => source.captureScreen()
  const stopStream = () => source.stopStream()
  const hasVideo =
    (mode === 'url' && !!settings?.video_url) ||
    (mode === 'file' && !!file) ||
    (viewer ? remoteLive : liveMode && capturing)
  const viewerText: Record<ViewerState, string> = {
    connecting: tr('Verbinde mit dem PC …', 'Connecting to the PC …'),
    waiting: tr(
      'Warte auf das Bild vom PC: Dort das Dashboard mit Live Onboard geöffnet lassen und in OBS die virtuelle Kamera starten.',
      'Waiting for the PC: keep the dashboard with Live onboard open there and start the OBS virtual camera.',
    ),
    negotiating: tr('Baue Videoverbindung zum PC auf …', 'Setting up the video link to the PC …'),
    live: '',
    failed: tr(
      'Direktverbindung zum PC fehlgeschlagen. Sind Handy und PC im selben WLAN/Netz? Neuer Versuch läuft …',
      'Direct link to the PC failed. Are phone and PC on the same network? Retrying …',
    ),
    denied: tr(
      'Zugriffscode ungültig oder geändert. Seite neu laden und den aktuellen Code eingeben.',
      'Access code invalid or changed. Reload and enter the current code.',
    ),
  }
  // Picture area inside the box (object-fit: contain → letterboxing).
  const measure = useCallback(() => {
    const box = videoBox.current
    if (!box) return
    const media = showFrames
      ? { w: frame.current?.naturalWidth, h: frame.current?.naturalHeight }
      : { w: video.current?.videoWidth, h: video.current?.videoHeight }
    const next = contentRect(box.clientWidth, box.clientHeight, media.w, media.h)
    setRect((r) =>
      Math.abs(r.x - next.x) < 0.5 &&
      Math.abs(r.y - next.y) < 0.5 &&
      Math.abs(r.w - next.w) < 0.5 &&
      Math.abs(r.h - next.h) < 0.5
        ? r
        : next,
    )
  }, [showFrames])
  useLayoutEffect(() => {
    measure()
    const box = videoBox.current,
      v = video.current
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    if (box) ro?.observe(box)
    v?.addEventListener('loadedmetadata', measure)
    v?.addEventListener('resize', measure)
    window.addEventListener('resize', measure)
    document.addEventListener('fullscreenchange', measure)
    return () => {
      ro?.disconnect()
      v?.removeEventListener('loadedmetadata', measure)
      v?.removeEventListener('resize', measure)
      window.removeEventListener('resize', measure)
      document.removeEventListener('fullscreenchange', measure)
    }
  }, [measure, hasVideo])

  /**
   * Picture-in-picture WITH the HUD needs Document Picture-in-Picture (Chrome/
   * Edge desktop): the whole picture box (video + DOM overlay) moves into the
   * PiP window. Standard video PiP shows only the video – said so explicitly.
   */
  async function pictureInPicture() {
    setPipNote('')
    const dpip = (
      window as unknown as {
        documentPictureInPicture?: { requestWindow: (o: object) => Promise<Window> }
      }
    ).documentPictureInPicture
    const box = videoBox.current
    if (dpip && box) {
      try {
        const pip = await dpip.requestWindow({ width: 640, height: 360 })
        for (const sheet of Array.from(document.styleSheets)) {
          try {
            const style = pip.document.createElement('style')
            style.textContent = Array.from(sheet.cssRules)
              .map((r) => r.cssText)
              .join('\n')
            pip.document.head.appendChild(style)
          } catch {
            if (sheet.href) {
              const link = pip.document.createElement('link')
              link.rel = 'stylesheet'
              link.href = sheet.href
              pip.document.head.appendChild(link)
            }
          }
        }
        pip.document.body.className = 'pip-body'
        const parent = box.parentElement
        pip.document.body.append(box)
        video.current?.play().catch(() => {})
        const resize = () => measure()
        pip.addEventListener('resize', resize)
        pip.addEventListener('pagehide', () => {
          parent?.prepend(box)
          video.current?.play().catch(() => {})
          measure()
        })
        measure()
        return
      } catch (e) {
        setPipNote(String(e))
      }
    }
    if (showFrames || !video.current?.requestPictureInPicture) {
      setPipNote(
        tr(
          'Bild-in-Bild ist in diesem Browser nicht verfügbar.',
          'Picture-in-picture is not available in this browser.',
        ),
      )
      return
    }
    try {
      await video.current.requestPictureInPicture()
      setPipNote(
        tr(
          'Bild-in-Bild zeigt in diesem Browser nur das Video – ohne Rundenzeit-HUD (Browser-Einschränkung).',
          'Picture-in-picture shows only the video in this browser – without the lap timing HUD (browser limitation).',
        ),
      )
    } catch (e) {
      setError(String(e))
    }
  }

  const showHud = !!hud && hudPrefs.visible && hasVideo
  const videoLabel = hasVideo
    ? viewer
      ? showFrames
        ? tr('PC-Bild über PC-Server', 'PC video via PC server')
        : tr('PC-Bild per WebRTC', 'PC video via WebRTC')
      : mode === 'camera' && cameraLabel
        ? cameraLabel
        : tr('Video verbunden', 'Video connected')
    : mode === 'none'
      ? tr('Keine Videoquelle gewählt', 'No video source selected')
      : tr('Kein Video', 'No video')
  useEffect(() => {
    onVideoState?.({ live: hasVideo, label: videoLabel })
  }, [hasVideo, videoLabel])
  useEffect(() => () => onVideoState?.({ live: false, label: '' }), [])
  return (
    <div className="onboard-shell" ref={container}>
      <div className="onboard-video" ref={videoBox}>
        <video
          ref={video}
          src={mode === 'file' ? file : mode === 'url' ? settings?.video_url : undefined}
          autoPlay={mode !== 'file'}
          muted
          playsInline
          controls={mode === 'file'}
          onError={() =>
            setError(
              'Video could not be loaded. Use a direct browser-compatible video URL; see docs/ONBOARD.md.',
            )
          }
          style={{ display: hasVideo && !showFrames ? 'block' : 'none' }}
        />
        {viewer && (
          <img
            ref={frame}
            className="onboard-frame"
            alt={tr('Live-Bild vom PC', 'Live picture from the PC')}
            style={{ display: showFrames && remoteLive ? 'block' : 'none' }}
            onLoad={measure}
          />
        )}
        {showHud && (
          <LapTimingHUD
            model={hud!}
            prefs={hudPrefs}
            rect={rect}
            de={de}
            onMove={(pos) => setHudPrefs(pos)}
          />
        )}
        {!hasVideo && (
          <div className="onboard-empty">
            <Video size={28} />
            <strong>Live onboard</strong>
            {viewer && <p data-testid="viewer-state">{viewerText[viewerState]}</p>}
            {!liveMode && (
              <p>
                {tr('Videoquelle in den Einstellungen wählen.', 'Choose a video source in Settings.')}
              </p>
            )}
            {mode === 'camera' && !viewer && (
              <>
                <p>
                  {tr(
                    'In OBS „Virtuelle Kamera starten“. Die App wählt „OBS Virtual Camera“ automatisch.',
                    'Click "Start Virtual Camera" in OBS. The app selects "OBS Virtual Camera" automatically.',
                  )}
                </p>
                <button onClick={() => startCamera()}>
                  <Camera size={14} />
                  {tr('Kamera verbinden', 'Connect camera')}
                </button>
              </>
            )}
            {mode === 'screen' && !viewer && (
              <button onClick={capture}>
                <Monitor size={14} />
                {tr('Fenster freigeben', 'Share window')}
              </button>
            )}
            {mode === 'file' && (
              <label className="button">
                <Upload size={14} />
                {tr('Aufnahme öffnen', 'Open recording')}
                <input
                  hidden
                  type="file"
                  accept="video/*"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) {
                      if (file) URL.revokeObjectURL(file)
                      setFile(URL.createObjectURL(f))
                    }
                  }}
                />
              </label>
            )}
            <small>
              {tr(
                'Telemetrie läuft unabhängig vom Video.',
                'Telemetry runs independently of video.',
              )}
            </small>
          </div>
        )}
        {hasVideo && hudPrefs.tacho && sample && (
          <Tacho sample={sample} imperial={settings?.units === 'imperial'} de={de} />
        )}
        {error && (
          <div className="video-error" role="alert">
            {error}
          </div>
        )}
      </div>
      <div className="onboard-controls">
        <span>
          <i className={`dot ${hasVideo ? 'green' : 'muted-dot'}`} />
          {hasVideo
            ? viewer
              ? showFrames
                ? tr('PC-BILD LIVE · ÜBER PC-SERVER', 'PC VIDEO LIVE · VIA PC SERVER')
                : tr('PC-BILD LIVE · WEBRTC', 'PC VIDEO LIVE · WEBRTC')
              : mode === 'camera' && cameraLabel
                ? cameraLabel.toUpperCase()
                : tr('VIDEO VERBUNDEN', 'VIDEO CONNECTED')
            : tr('KEIN VIDEO', 'NO VIDEO')}
          {!viewer && liveMode && viewers > 0 && (
            <span
              className="tag cyan-tag"
              data-testid="viewer-count"
              title={tr('Geräte, die dieses Bild sehen', 'Devices watching this video')}
            >
              <Smartphone size={11} /> {viewers}
            </span>
          )}
        </span>
        <div>
          {mode === 'camera' && !viewer && cameras.length > 1 && (
            <select
              aria-label={tr('Kamera', 'Camera')}
              value={cameraLabel}
              onChange={(e) => {
                onDevice?.(e.target.value)
                startCamera(e.target.value)
              }}
            >
              {!cameraLabel && <option value="">—</option>}
              {cameras.map((c, i) => (
                <option key={c.deviceId || i} value={c.label}>
                  {c.label || `Camera ${i + 1}`}
                </option>
              ))}
            </select>
          )}
          {mode === 'camera' && !viewer && (
            <button
              title={tr('Erneut verbinden', 'Reconnect')}
              onClick={() => startCamera(cameraLabel || undefined)}
            >
              <RotateCcw size={14} />
            </button>
          )}
          {mode === 'screen' && !viewer && (
            <button title="Capture window" onClick={capturing ? stopStream : capture}>
              {capturing ? <Square size={14} /> : <Monitor size={14} />}
            </button>
          )}
          <button
            onClick={() => setHudPrefs({ visible: !hudPrefs.visible })}
            aria-pressed={hudPrefs.visible}
            title={tr('Rundenzeit-HUD ein/aus', 'Lap timing HUD on/off')}
          >
            HUD
          </button>
          <button
            onClick={() => setHudPrefs({ tacho: !hudPrefs.tacho })}
            aria-pressed={hudPrefs.tacho}
            title={tr('Tempo, Gang und Pedale einblenden', 'Show speed, gear and pedals')}
          >
            {tr('Tacho', 'Speed')}
          </button>
          <button
            onClick={() => setHudPanel(!hudPanel)}
            aria-pressed={hudPanel}
            aria-label={tr('Rundenzeit-HUD', 'Lap timing HUD')}
            title={tr('Rundenzeit-HUD einstellen', 'Lap timing HUD settings')}
          >
            <Timer size={15} />
          </button>
          <button
            title={tr('Bild-in-Bild', 'Picture in picture')}
            aria-label={tr('Bild-in-Bild', 'Picture in picture')}
            onClick={pictureInPicture}
          >
            <PictureInPicture2 size={15} />
          </button>
          <button
            title="Fullscreen onboard"
            aria-label={tr('Onboard-Vollbild', 'Onboard fullscreen')}
            // The shared container holds video AND HUD, so both go fullscreen.
            onClick={() => container.current?.requestFullscreen?.()}
          >
            <Maximize size={15} />
          </button>
        </div>
      </div>
      {pipNote && (
        <div className="notice" role="status">
          {pipNote}
          <button onClick={() => setPipNote('')} aria-label="OK">
            ✕
          </button>
        </div>
      )}
      {hudPanel && (
        <div className="hud-panel" role="dialog" aria-label={tr('Rundenzeit-HUD', 'Lap timing HUD')}>
          <strong>{tr('Rundenzeit-HUD', 'Lap timing HUD')}</strong>
          <label>
            {tr('Anzeigen', 'Show')}
            <input
              type="checkbox"
              checked={hudPrefs.visible}
              onChange={(e) => setHudPrefs({ visible: e.target.checked })}
            />
          </label>
          <label>
            {tr('Größe', 'Size')}
            <input
              type="range"
              aria-label={tr('HUD-Größe', 'HUD size')}
              min={0.6}
              max={1.8}
              step={0.05}
              value={hudPrefs.scale}
              onChange={(e) => setHudPrefs({ scale: Number(e.target.value) })}
            />
          </label>
          <label>
            {tr('Deckkraft', 'Opacity')}
            <input
              type="range"
              aria-label={tr('HUD-Deckkraft', 'HUD opacity')}
              min={0.3}
              max={1}
              step={0.05}
              value={hudPrefs.opacity}
              onChange={(e) => setHudPrefs({ opacity: Number(e.target.value) })}
            />
          </label>
          <label>
            {tr('Position sperren', 'Lock position')}
            <input
              type="checkbox"
              checked={hudPrefs.locked}
              onChange={(e) => setHudPrefs({ locked: e.target.checked })}
            />
          </label>
          <label>
            {tr('Details (Runde, Referenz, Sektorzeiten)', 'Details (lap, reference, sector times)')}
            <input
              type="checkbox"
              checked={hudPrefs.details}
              onChange={(e) => setHudPrefs({ details: e.target.checked })}
            />
          </label>
          <button onClick={() => setHudPrefs({ x: DEFAULT_HUD.x, y: DEFAULT_HUD.y })}>
            {tr('Position zurücksetzen', 'Reset position')}
          </button>
          <small>
            {tr(
              'Verschieben: am Griff ⠿ links neben dem HUD ziehen (Maus oder Finger). Die Einstellungen gelten für dieses Gerät.',
              'Move: drag the grip ⠿ left of the HUD (mouse or finger). Settings apply to this device.',
            )}
          </small>
        </div>
      )}
    </div>
  )
}
