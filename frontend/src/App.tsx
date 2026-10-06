import { Suspense, lazy, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { BarChart3, Database, LayoutDashboard, Maximize, Settings2, ShieldCheck, Video, Wifi, X } from 'lucide-react'
import type { Diagnostics, Frame, LapSummary, Sample, Session, Settings, Track, VersionInfo } from './types'
import { api, saveToken, uploadBytes, websocket } from './api'
import { backendStatus, gameStatus } from './status'
import { telemetryStatus } from './connection'
import { friendlyError, logError } from './errors'
import { buildLiveHud, buildReplayHud, type HudModel } from './hud'
import { parseRoute, routeHash, type Page, type Route, type SettingsTab } from './routes'
import LiveDashboard from './LiveDashboard'
import AnalysisView from './AnalysisView'
// Loaded on demand: the setup assistant is only needed under Analyse → Setup.
const SetupAssistant = lazy(() => import('./setup/SetupAssistant'))
import OnboardView, { type ReplayState } from './OnboardView'
import SessionsView from './SessionsView'
import SettingsView from './SettingsView'
import LapVideoPlayer from './LapVideoPlayer'
import { SessionRecorder, recDetail, recView, type RecStatus } from './recorder'
import { videoSource } from './videoSource'
import { isPcHost } from './relay'

const EMPTY: Frame = {
  type: 'telemetry',
  status: 'starting',
  connected: false,
  error: null,
  meta: null,
  sample: null,
  session_id: null,
  reference_id: null,
  reference_ms: null,
  delta_s: null,
  ghost_pos: null,
  fuel_laps_est: null,
  current_sector_ms: null,
  statistics: {},
  tips: [],
  recording: false,
  storage: {},
  tyre_warnings: [],
  performance: { samples: 0, dropped: 0, queue_depth: 0, uptime_s: 0 },
}
// Dashboard build reported by the backend when this page first connected.
let loadedBuild = ''
let reloading = false

/**
 * App-wide onboard recorder: lives as long as this tab, independent of the
 * page shown, so navigating never interrupts a recording.
 */
const recorder = new SessionRecorder({
  source: videoSource,
  request: api,
  upload: uploadBytes,
  MediaRecorder:
    typeof MediaRecorder !== 'undefined'
      ? (MediaRecorder as unknown as ConstructorParameters<typeof SessionRecorder>[0]['MediaRecorder'])
      : undefined,
  now: () => performance.timeOrigin + performance.now(),
  beacon: (url, body) => !!navigator.sendBeacon?.(url, new Blob([body], { type: 'text/plain' })),
})
const DRIVING = ['live', 'paused', 'demo']

function useRoute(): [Route, (r: Route) => void] {
  const read = () => parseRoute(location.hash)
  const [route, setRoute] = useState<Route>(read)
  useEffect(() => {
    // Old links (#/engineering, #/tablet, #/diagnostics …) become canonical routes.
    const canonical = routeHash(read())
    if (location.hash !== canonical) history.replaceState(null, '', canonical)
    const onHash = () => {
      const next = read()
      const hash = routeHash(next)
      if (location.hash !== hash) history.replaceState(null, '', hash)
      setRoute(next)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const navigate = useCallback((r: Route) => {
    const hash = routeHash(r)
    if (location.hash !== hash) history.pushState(null, '', hash)
    setRoute(r)
  }, [])
  return [route, navigate]
}

export default function App() {
  const [frame, setFrame] = useState<Frame>(EMPTY),
    [settings, setSettings] = useState<Settings | null>(null),
    [socketState, setSocketState] = useState('connecting'),
    [track, setTrack] = useState<Track | null>(null),
    [error, setError] = useState(''),
    [tokenInput, setTokenInput] = useState(''),
    [locked, setLocked] = useState(false),
    [connectionKey, setConnectionKey] = useState(0),
    [route, navigate] = useRoute(),
    [analysisRequest, setAnalysisRequest] = useState<string[] | null>(null),
    [video, setVideo] = useState({ live: false, label: '' }),
    [lastFreshAt, setLastFreshAt] = useState<number | null>(null),
    [now, setNow] = useState(Date.now()),
    [rec, setRec] = useState<RecStatus>(recorder.status),
    [update, setUpdate] = useState<VersionInfo['update']>(null),
    [updateHidden, setUpdateHidden] = useState(false)
  const [replay, setReplay] = useState<ReplayState | null>(null),
    [replayTime, setReplayTime] = useState(0),
    [replaying, setReplaying] = useState(false),
    [videoBase, setVideoBase] = useState(0)
  const playlist = useRef<LapSummary[]>([]),
    videoEpoch = useRef<number | null>(null)
  const history_ = useRef<Sample[]>([]),
    lastPacket = useRef<number | null>(null),
    lastSession = useRef<string | null>(null),
    lastSource = useRef<string | null | undefined>(undefined)
  const de = settings?.language !== 'en',
    tr = useCallback((a: string, b: string) => (de ? a : b), [de])
  const showError = (e: unknown, context: string) => {
    logError(e, context)
    setError(friendlyError(e, de))
  }

  // Clock for "last values" detection.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  // New release on GitHub? (only if a download repository is configured)
  useEffect(() => {
    if (!settings?.update_check) return
    api<VersionInfo>('/version')
      .then((v) => setUpdate(v.update))
      .catch(() => {})
  }, [settings?.update_check, connectionKey])

  // Automatic onboard recording (PC browser only; see recorder.ts).
  useEffect(() => recorder.subscribe(setRec), [])
  const driving = socketState === 'connected' && !!frame.session_id && DRIVING.includes(frame.status)
  useEffect(() => {
    if (!settings) return
    recorder.update({
      enabled: !!settings.record_auto,
      quality: settings.record_quality || 'standard',
      mode: settings.video_mode,
      device: settings.video_device || '',
      de,
      drive: { sessionId: frame.session_id, active: driving },
      pc: isPcHost(location.hostname),
    })
  }, [settings, frame.session_id, driving, de, now])
  useEffect(() => {
    const hide = () => recorder.pageHide()
    const leave = (e: BeforeUnloadEvent) => {
      // The recording needs this tab: ask before closing it.
      if (recorder.recording && !reloading) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('pagehide', hide)
    window.addEventListener('beforeunload', leave)
    return () => {
      window.removeEventListener('pagehide', hide)
      window.removeEventListener('beforeunload', leave)
    }
  }, [])

  useEffect(() => {
    let active = true,
      ws: WebSocket | null = null,
      timer: ReturnType<typeof setTimeout>,
      backoff = 500
    const connect = () => {
      if (!active) return
      ws = websocket()
      setSocketState('connecting')
      ws.onopen = () => {
        backoff = 500
        setSocketState('connected')
        setLocked(false)
      }
      ws.onmessage = (e) => {
        try {
          const next = JSON.parse(e.data) as Frame
          if (!active) return
          // The PC app was updated while this tab stayed open: reload once.
          if (next.build) {
            if (!loadedBuild) loadedBuild = next.build
            else if (next.build !== loadedBuild) {
              reloading = true
              location.reload()
              return
            }
          }
          // New session, source switch (demo <-> AC) or empty live view: drop old live values.
          if (next.session_id !== lastSession.current || next.source !== lastSource.current || !next.sample) {
            history_.current = []
            lastPacket.current = null
            lastSession.current = next.session_id
            lastSource.current = next.source
          }
          if (next.sample && next.sample.packet_id !== lastPacket.current) {
            history_.current.push(next.sample)
            if (history_.current.length > 1200) history_.current.splice(0, history_.current.length - 1200)
            lastPacket.current = next.sample.packet_id
            setLastFreshAt(Date.now())
          }
          setFrame(next)
        } catch (err) {
          logError(err, 'telemetry frame')
        }
      }
      ws.onerror = () => setSocketState('disconnected')
      ws.onclose = () => {
        setSocketState('disconnected')
        if (active) {
          timer = setTimeout(connect, backoff)
          backoff = Math.min(10000, backoff * 2)
        }
      }
    }
    connect()
    api<Settings>('/settings')
      .then((s) => active && setSettings(s))
      .catch((e) => {
        if (!active) return
        if (/Access code|Too many wrong/.test(String(e))) setLocked(true)
        else showError(e, 'settings')
      })
    return () => {
      active = false
      clearTimeout(timer)
      ws?.close()
    }
  }, [connectionKey])

  // Live track map (only while the live dashboard needs it).
  useEffect(() => {
    if (locked || route.page !== 'live') return
    let active = true
    const load = () =>
      api<Track>('/map')
        .then((t) => active && setTrack(t))
        .catch(() => {})
    load()
    const timer = setInterval(load, 1500)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [connectionKey, locked, route.page])

  // Replay of stored laps (Onboard).
  useEffect(() => {
    if (!replay || !replaying) return
    let raf = 0,
      last = performance.now()
    const tick = (t: number) => {
      const dt = (t - last) / 1000
      last = t
      setReplayTime((rt) => {
        if (rt + dt >= replay.duration) {
          setReplaying(false)
          return replay.duration
        }
        return rt + dt
      })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [replay, replaying])
  const replaySample = (() => {
    if (!replay) return null
    let lo = 0,
      hi = replay.samples.length - 1
    if (replayTime >= replay.samples[hi].lap_ms / 1000) return replay.samples[hi]
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (replay.samples[mid].lap_ms / 1000 <= replayTime) lo = mid
      else hi = mid
    }
    return replay.samples[lo]
  })()
  function exitReplay() {
    setReplay(null)
    setReplaying(false)
    playlist.current = []
  }
  async function onReplay(lap: LapSummary, autoplay = false, preserve = false) {
    if (!preserve) {
      playlist.current = []
      videoEpoch.current = null
    }
    try {
      const [detail, s] = await Promise.all([
        api<{ samples: Sample[]; duration_ms: number }>('/laps/' + lap.id),
        api<Session>('/sessions/' + lap.session_id),
      ])
      setReplay({
        samples: detail.samples,
        meta: s.meta,
        duration: detail.duration_ms / 1000,
        lap: (s.laps || []).find((l) => l.id === lap.id) || lap,
        laps: s.laps || [],
      })
      const origin = detail.samples[0].captured_at - detail.samples[0].lap_ms / 1000
      if (preserve && videoEpoch.current === null) videoEpoch.current = origin
      setVideoBase(videoEpoch.current === null ? 0 : origin - videoEpoch.current)
      setReplayTime(detail.samples[0].lap_ms / 1000)
      setReplaying(autoplay)
      navigate({ page: 'onboard' })
    } catch (e) {
      showError(e, 'replay')
    }
  }
  function replaySession(laps: LapSummary[]) {
    const full = laps.filter((l) => l.complete)
    if (!full.length) return
    playlist.current = full.slice(1)
    videoEpoch.current = null
    onReplay(full[0], true, true)
  }
  useEffect(() => {
    if (replay && !replaying && replayTime >= replay.duration && playlist.current.length) {
      onReplay(playlist.current.shift()!, true, true)
    }
  }, [replay, replaying, replayTime])
  // Replay belongs to Onboard; leaving the page ends it.
  useEffect(() => {
    if (route.page !== 'onboard' && replay) exitReplay()
  }, [route.page])

  async function persist(patch: Partial<Settings>) {
    try {
      const result = await api<{ settings: Settings }>('/settings', { method: 'PATCH', body: JSON.stringify(patch) })
      setSettings(result.settings)
    } catch (e) {
      showError(e, 'settings')
    }
  }
  async function recheckConnection(): Promise<Diagnostics | null> {
    try {
      return await api<Diagnostics>('/diagnostics/recheck', { method: 'POST' })
    } catch (e) {
      showError(e, 'recheck')
      return null
    }
  }

  const telemetry = telemetryStatus(frame, socketState, lastFreshAt, now)
  const hudModel: HudModel | null = replay
    ? buildReplayHud(replaySample, replay.lap, replay.laps, replay.meta, replayTime >= replay.duration, settings?.compound_map)
    : buildLiveHud(frame, socketState, settings?.compound_map)
  const backend = backendStatus(socketState),
    game = gameStatus(frame, socketState)
  const nav: Array<{ page: Page; label: string; icon: ReactNode }> = [
    { page: 'live', label: tr('Live-Dashboard', 'Live dashboard'), icon: <LayoutDashboard size={18} /> },
    { page: 'analysis', label: tr('Analyse', 'Analysis'), icon: <BarChart3 size={18} /> },
    { page: 'onboard', label: 'Onboard', icon: <Video size={18} /> },
    { page: 'sessions', label: 'Sessions', icon: <Database size={18} /> },
    { page: 'settings', label: tr('Einstellungen', 'Settings'), icon: <Settings2 size={18} /> },
  ]
  const go = (page: Page, tab?: SettingsTab) => navigate(page === 'settings' ? { page, tab: tab || 'general' } : { page })

  return (
    <div className={'app page-' + route.page}>
      <aside className="sidebar">
        <a
          className="brand"
          href="#/live"
          onClick={(e) => {
            e.preventDefault()
            go('live')
          }}
        >
          <img className="brand-logo" src="/icon.svg" alt="" />
          <div>
            STINTPULSE<small>{tr('TELEMETRIE', 'TELEMETRY')}</small>
          </div>
        </a>
        <nav aria-label={tr('Hauptnavigation', 'Main navigation')}>
          {nav.map((n) => (
            <button
              key={n.page}
              onClick={() => go(n.page)}
              className={route.page === n.page ? 'active' : ''}
              aria-current={route.page === n.page ? 'page' : undefined}
              // Narrow screens show icons only: keep the name for screen readers.
              aria-label={n.label}
              title={n.label}
            >
              {n.icon}
              <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-status">
            <ShieldCheck size={16} />
            <div>
              {tr('Lokal', 'Local')}
              <small>{tr('Deine Daten bleiben auf diesem PC', 'Your data stays on this PC')}</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <strong className="page-title">{nav.find((n) => n.page === route.page)?.label}</strong>
          <div className="status-pills" aria-label={tr('Verbindungsstatus', 'Connection status')}>
            <span className="connection" title={tr('Browser ↔ PC-Programm', 'Browser ↔ PC app')} data-testid="backend-status">
              <i className={'dot ' + backend.tone} />
              {de ? backend.de : backend.en}
            </span>
            <span className="connection" title="Assetto Corsa" data-testid="game-status">
              <i className={'dot ' + (game.tone === 'muted' ? 'muted-dot' : game.tone)} />
              {de ? game.de : game.en}
            </span>
            <span className="connection" title={tr('Empfang von Telemetriedaten', 'Telemetry reception')} data-testid="telemetry-status">
              <i className={'dot ' + (telemetry.tone === 'muted' ? 'muted-dot' : telemetry.tone)} />
              {de ? telemetry.de : telemetry.en}
            </span>
            {settings?.record_auto && rec.state !== 'off' && (
              <button
                className={'connection record-pill tone-' + recView(rec, now).tone}
                title={recDetail(rec.detail, de) || tr('Onboard-Aufnahme', 'Onboard recording')}
                data-testid="record-status"
                data-state={rec.state}
                onClick={() => go('settings', 'onboard')}
              >
                <i className={'dot ' + (recView(rec, now).tone === 'muted' ? 'muted-dot' : recView(rec, now).tone)} />
                {de ? recView(rec, now).de : recView(rec, now).en}
              </button>
            )}
            {route.page === 'onboard' && (
              <span className="connection" title={tr('Videoverbindung (unabhängig von Telemetrie)', 'Video connection (independent of telemetry)')} data-testid="video-status">
                <i className={'dot ' + (video.live ? 'green' : 'muted-dot')} />
                {video.live ? tr('VIDEO AKTIV', 'VIDEO ACTIVE') : tr('KEIN VIDEO', 'NO VIDEO')}
              </span>
            )}
          </div>
          <div className="topbar-actions">
            <span className="lan-mode">
              <Wifi size={14} />
              {settings?.lan ? 'LAN' : tr('NUR PC', 'PC ONLY')}
            </span>
            <button
              aria-label={tr('Vollbild', 'Fullscreen')}
              onClick={() => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.())}
            >
              <Maximize size={16} />
            </button>
          </div>
        </header>
        <main>
          {update && !updateHidden && (
            <div className="notice update-notice" role="status" data-testid="update-notice">
              <span>
                {tr(`StintPulse ${update.version} ist verfügbar.`, `StintPulse ${update.version} is available.`)}{' '}
                <a href={update.url} target="_blank" rel="noreferrer">
                  {tr('Zum Download', 'Download')}
                </a>
              </span>
              <button aria-label="OK" onClick={() => setUpdateHidden(true)}>
                <X size={14} />
              </button>
            </div>
          )}
          {error && (
            <div className="notice danger" role="alert">
              <span>{error}</span>
              <button aria-label="OK" onClick={() => setError('')}>
                <X size={14} />
              </button>
            </div>
          )}
          {frame.error && route.page === 'live' && (
            <div className="notice danger">
              <span>{tr('Das Programm meldet einen Fehler. Details unter Einstellungen → Erweitert / Diagnose.', 'The app reports an error. Details under Settings → Advanced / Diagnostics.')}</span>
              <button onClick={() => go('settings', 'advanced')}>{tr('Diagnose', 'Diagnostics')}</button>
            </div>
          )}
          {locked && (
            <div className="token-gate panel">
              <h3>{tr('Zugriffscode eingeben', 'Enter access code')}</h3>
              <p className="small subtle">
                {tr('Den 8-stelligen Code zeigt der PC unter Einstellungen → Netzwerk.', 'The PC shows the 8-digit code under Settings → Network.')}
              </p>
              <input
                aria-label={tr('Zugriffscode', 'Access code')}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={9}
                placeholder="1234 5678"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value.replace(/[^0-9 ]/g, ''))}
              />
              <button
                className="primary"
                disabled={tokenInput.replace(/\D/g, '').length !== 8}
                onClick={() => {
                  saveToken(tokenInput.replace(/\D/g, ''))
                  setConnectionKey((k) => k + 1)
                  setError('')
                }}
              >
                {tr('Verbinden', 'Connect')}
              </button>
            </div>
          )}
          {route.page === 'live' && (
            <LiveDashboard
              frame={frame}
              settings={settings}
              telemetry={telemetry}
              history={history_.current}
              track={track}
              de={de}
              onOpenOnboard={() => go('onboard')}
              onToggleDemo={() => persist({ source: settings?.source === 'demo' ? 'ac' : 'demo' })}
              onRecheck={() => recheckConnection()}
              onDiagnostics={() => go('settings', 'advanced')}
            />
          )}
          {route.page === 'analysis' && (
            <nav className="settings-tabs analysis-tabs" role="tablist" aria-label={tr('Analysebereiche', 'Analysis sections')}>
              <button
                role="tab"
                aria-selected={route.analysis !== 'setup'}
                className={route.analysis !== 'setup' ? 'active' : ''}
                onClick={() => navigate({ page: 'analysis' })}
              >
                {tr('Vergleich', 'Comparison')}
              </button>
              <button
                role="tab"
                aria-selected={route.analysis === 'setup'}
                className={route.analysis === 'setup' ? 'active' : ''}
                onClick={() => navigate({ page: 'analysis', analysis: 'setup' })}
              >
                {tr('Setup', 'Setup')}
              </button>
            </nav>
          )}
          {route.page === 'analysis' && route.analysis === 'setup' && (
            <Suspense fallback={<div className="notice compact-hint">{tr('Lade Setup-Assistent …', 'Loading setup assistant …')}</div>}>
              <SetupAssistant liveCar={frame.meta ? { car: frame.meta.car, track: frame.meta.track } : null} />
            </Suspense>
          )}
          {route.page === 'analysis' && route.analysis !== 'setup' && (
            <AnalysisView
              settings={settings}
              de={de}
              liveMeta={frame.meta}
              request={analysisRequest}
              onRequestDone={() => setAnalysisRequest(null)}
              persist={persist}
              onSessions={() => go('sessions')}
            />
          )}
          {route.page === 'onboard' && (
            <OnboardView
              frame={frame}
              settings={settings}
              sample={replaySample || frame.sample}
              hud={hudModel}
              telemetry={telemetry}
              video={video}
              onVideoState={setVideo}
              persist={persist}
              de={de}
              replay={replay}
              replayTime={replayTime}
              replaying={replaying}
              videoBase={videoBase}
              setReplayTime={setReplayTime}
              setReplaying={setReplaying}
              exitReplay={exitReplay}
            />
          )}
          {route.page === 'sessions' && (
            // The list stays mounted (selection kept) while a lap video plays.
            <div hidden={!!route.video}>
              <SessionsView
                activeId={frame.session_id}
                settings={settings}
                onCompare={(ids) => {
                  setAnalysisRequest(ids)
                  go('analysis')
                }}
                onReplay={onReplay}
                onReplaySession={replaySession}
                onWatch={(lap) => navigate({ page: 'sessions', video: lap.id })}
              />
            </div>
          )}
          {route.page === 'sessions' && route.video && (
            <LapVideoPlayer
              key={route.video}
              lapId={route.video}
              settings={settings}
              de={de}
              onClose={() => navigate({ page: 'sessions' })}
              persist={persist}
            />
          )}
          {route.page === 'settings' &&
            (settings ? (
              <SettingsView
                key={settings.language + settings.units}
                settings={settings}
                onSaved={(s) => {
                  setSettings(s)
                  setConnectionKey((k) => k + 1)
                }}
                tab={route.tab || 'general'}
                onTab={(tab) => go('settings', tab)}
                socketState={socketState}
                onRecheck={recheckConnection}
                recording={rec}
                onRetryRecording={() => recorder.retry()}
              />
            ) : (
              <div className="notice compact-hint">{tr('Verbinde mit dem PC-Programm …', 'Connecting to the PC app …')}</div>
            ))}
        </main>
      </div>
    </div>
  )
}
