/**
 * Onboard video of ONE stored lap with the existing HUD, fed exclusively by
 * that lap's stored telemetry (never by a live drive running at the same
 * time). The HUD follows the real video position on every frame.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowLeft, Eye, EyeOff, Maximize, Pause, Play, Repeat, SkipBack, SkipForward, Volume2, VolumeX } from 'lucide-react'
import { api, audioUrl, videoUrl } from './api'
import { friendlyError, logError } from './errors'
import { buildReplayHud, contentRect, formatLapTime, loadHud, saveHud, type HudPrefs, type Rect } from './hud'
import LapTimingHUD from './LapTimingHUD'
import Tacho from './Tacho'
import {
  atLapEnd,
  audioLabel,
  audioTimeAt,
  clampToLap,
  coverageGradient,
  lapTimeline,
  sampleAtWall,
  syncSound,
  wallAt,
  type AudioInfo,
  type LapVideo,
} from './playback'
import type { Sample, Session, Settings } from './types'

export const RATES = [0.25, 0.5, 1, 2]
const SKIP_S = 5

/** Sound settings of the player (per device). */
interface SoundPrefs {
  muted: boolean
  volume: number
}
const SOUND_KEY = 'aceda-sound-v1'
function loadSound(): SoundPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(SOUND_KEY) || 'null') as Partial<SoundPrefs> | null
    return {
      muted: typeof raw?.muted === 'boolean' ? raw.muted : false,
      volume: typeof raw?.volume === 'number' && raw.volume >= 0 && raw.volume <= 1 ? raw.volume : 0.8,
    }
  } catch {
    return { muted: false, volume: 0.8 }
  }
}

const clock = (s: number) => {
  const v = Math.max(0, s)
  return `${Math.floor(v / 60)}:${(v % 60).toFixed(1).padStart(4, '0')}`
}

export default function LapVideoPlayer({
  lapId,
  settings,
  de,
  onClose,
  persist,
}: {
  lapId: string
  settings: Settings | null
  de: boolean
  onClose: () => void
  persist: (patch: Partial<Settings>) => void
}) {
  const tr = (a: string, b: string) => (de ? a : b)
  const [info, setInfo] = useState<LapVideo | null>(null),
    [samples, setSamples] = useState<Sample[]>([]),
    [session, setSession] = useState<Session | null>(null),
    [error, setError] = useState(''),
    [videoError, setVideoError] = useState(''),
    [pos, setPos] = useState(0),
    [playing, setPlaying] = useState(false),
    [rate, setRate] = useState(1),
    [loop, setLoop] = useState(false),
    [prefs, setPrefsState] = useState<HudPrefs>(loadHud),
    [rect, setRect] = useState<Rect>({ x: 0, y: 0, w: 0, h: 0 }),
    [sound, setSoundState] = useState<SoundPrefs>(loadSound),
    [soundBlocked, setSoundBlockedState] = useState(false)
  const video = useRef<HTMLVideoElement>(null),
    audio = useRef<HTMLAudioElement>(null),
    box = useRef<HTMLDivElement>(null),
    shell = useRef<HTMLDivElement>(null),
    loopRef = useRef(loop),
    soundRef = useRef<{ info: AudioInfo | null; startedAt: number | null; offset: number; muted: boolean }>({
      info: null,
      startedAt: null,
      offset: 0,
      muted: false,
    })
  loopRef.current = loop
  // Browser refused audible playback (no user gesture yet): ask once, no retry loop.
  const blockedRef = useRef(false)
  const setSoundBlocked = (v: boolean) => {
    blockedRef.current = v
    setSoundBlockedState(v)
  }
  const setSound = (patch: Partial<SoundPrefs>) =>
    setSoundState((p) => {
      const next = { ...p, ...patch }
      try {
        localStorage.setItem(SOUND_KEY, JSON.stringify(next))
      } catch {}
      return next
    })
  const setPrefs = (patch: Partial<HudPrefs>) =>
    setPrefsState((p) => {
      const next = { ...p, ...patch }
      saveHud(next)
      return next
    })
  const offset = settings?.video_offset_s ?? 0

  // Video metadata of the lap (polled while the segment is still being saved).
  useEffect(() => {
    let active = true,
      timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const next = await api<LapVideo>(`/laps/${lapId}/video`)
        if (!active) return
        setInfo(next)
        if (next.coverage === 'pending') timer = setTimeout(load, 2000)
      } catch (e) {
        logError(e, 'lap video')
        if (active) setError(friendlyError(e, de))
      }
    }
    load()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [lapId, offset])
  // Stored telemetry of exactly this lap and its session.
  useEffect(() => {
    let active = true
    setSamples([])
    setSession(null)
    api<{ samples: Sample[]; session_id: string }>(`/laps/${lapId}`)
      .then(async (lap) => {
        const s = await api<Session>(`/sessions/${lap.session_id}`)
        if (!active) return
        setSamples([...lap.samples].sort((a, b) => a.captured_at - b.captured_at))
        setSession(s)
      })
      .catch((e) => {
        logError(e, 'lap video telemetry')
        if (active) setError(friendlyError(e, de))
      })
    return () => {
      active = false
    }
  }, [lapId])

  const timeline = lapTimeline(info)
  const rec = info?.recording || null
  const from = timeline?.from ?? 0
  const startedAt = rec?.started_at ?? null
  const soundInfo = rec?.audio?.status === 'ready' ? rec.audio : null
  soundRef.current = { info: soundInfo, startedAt, offset: info?.offset_s ?? offset, muted: sound.muted }
  useEffect(() => {
    if (audio.current) audio.current.volume = sound.volume
  }, [sound.volume, !!soundInfo])

  // Follow the real playback position; stop at the end of the lap.
  useEffect(() => {
    if (!timeline) return
    let raf = 0
    const tick = () => {
      const v = video.current
      if (v) {
        if (!v.paused && atLapEnd(timeline, v.currentTime)) {
          if (loopRef.current) v.currentTime = timeline.availableFrom
          else {
            v.pause()
            v.currentTime = timeline.availableTo
          }
        }
        const t = v.currentTime
        setPos((p) => (Math.abs(p - t) > 0.0005 ? t : p))
        // The sound is a separate file on the same clock: follow the picture.
        const a = audio.current,
          snd = soundRef.current
        if (a && snd.info && snd.startedAt !== null) {
          const target = audioTimeAt(t, snd.startedAt, snd.offset, snd.info)
          if (v.paused || v.seeking || target === null || snd.muted) {
            if (!a.paused) a.pause()
          } else if (a.paused) {
            if (blockedRef.current) return void (raf = requestAnimationFrame(tick))
            a.currentTime = target
            a.playbackRate = v.playbackRate
            a.play().then(
              () => setSoundBlocked(false),
              () => setSoundBlocked(true),
            )
          } else {
            const step = syncSound(v.playbackRate, a.currentTime - target)
            if (step.seek) a.currentTime = target
            else if (Math.abs(a.playbackRate - step.rate) > 0.001) a.playbackRate = step.rate
          }
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [timeline?.availableFrom, timeline?.availableTo])

  const measure = useCallback(() => {
    const b = box.current
    if (!b) return
    const next = contentRect(b.clientWidth, b.clientHeight, video.current?.videoWidth, video.current?.videoHeight)
    setRect((r) => (Math.abs(r.x - next.x) + Math.abs(r.y - next.y) + Math.abs(r.w - next.w) + Math.abs(r.h - next.h) < 1 ? r : next))
  }, [])
  useLayoutEffect(() => {
    measure()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    if (box.current) ro?.observe(box.current)
    document.addEventListener('fullscreenchange', measure)
    return () => {
      ro?.disconnect()
      document.removeEventListener('fullscreenchange', measure)
    }
  }, [measure, !!timeline])

  const seek = (v: number) => {
    if (video.current && timeline) video.current.currentTime = clampToLap(timeline, v)
  }
  const toggle = () => {
    const v = video.current
    if (!v || !timeline) return
    if (v.paused) {
      if (atLapEnd(timeline, v.currentTime)) v.currentTime = timeline.availableFrom
      v.play().catch(() => {})
    } else v.pause()
  }
  const onKey = (e: KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === 'SELECT') return
    if (e.key === ' ') toggle()
    else if (e.key === 'ArrowLeft') seek((video.current?.currentTime || 0) - (e.shiftKey ? 1 : SKIP_S))
    else if (e.key === 'ArrowRight') seek((video.current?.currentTime || 0) + (e.shiftKey ? 1 : SKIP_S))
    else return
    e.preventDefault()
  }

  const wall = startedAt !== null ? wallAt(pos, startedAt, info?.offset_s ?? offset) : null
  const sample = wall !== null ? sampleAtWall(samples, wall) : null
  const lap = session?.laps?.find((l) => l.id === lapId) || null
  const atEnd = wall !== null && info?.lap_end != null && wall >= info.lap_end - 0.001
  const hud = buildReplayHud(sample, lap, session?.laps || [], session?.meta || null, atEnd, settings?.compound_map)
  const lapMs = atEnd ? info?.duration_ms ?? null : sample?.lap_ms ?? null
  const meta = session?.meta
  const invalid = !!info && info.complete && !info.valid
  const span = timeline ? timeline.to - timeline.from : 0

  return (
    <div className="lap-player" ref={shell} tabIndex={-1} onKeyDown={onKey} data-testid="lap-player">
      <div className="lap-player-head">
        <button onClick={onClose}>
          <ArrowLeft size={14} />
          {tr('Zurück zu Sessions', 'Back to sessions')}
        </button>
        <span className="tag cyan-tag" data-testid="playback-tag">
          {tr('WIEDERGABE', 'PLAYBACK')}
        </span>
        <div className="lap-player-title">
          <strong>
            {meta ? `${meta.track}${meta.layout ? ' / ' + meta.layout : ''}` : '…'} · {tr('Runde', 'Lap')}{' '}
            {info?.number ?? lap?.number ?? '–'}
          </strong>
          <small>
            {meta ? `${meta.car} · ${meta.driver}` : ''}
            {session ? ` · ${new Date(session.created_at).toLocaleString()}` : ''}
          </small>
        </div>
        {meta?.source === 'demo' && <span className="tag orange-tag">DEMO</span>}
        {invalid && (
          <span className="tag red-tag" title={info!.reasons.join(', ')}>
            {tr('UNGÜLTIGE RUNDE', 'INVALID LAP')}
          </span>
        )}
        {info && !info.complete && (
          <span className="tag yellow-tag" title={info.reasons.join(', ')}>
            {tr('UNVOLLSTÄNDIG · KEINE GEZEITETE RUNDE', 'PARTIAL · NOT A TIMED LAP')}
          </span>
        )}
        {rec && info && (info.coverage === 'full' || info.coverage === 'partial') && (
          <span className={'tag ' + (soundInfo ? 'green-tag' : 'muted-tag')} data-testid="sound-tag" title={audioLabel(rec.audio, de).text}>
            {soundInfo ? tr('MIT TON', 'WITH SOUND') : tr('OHNE TON', 'NO SOUND')}
          </span>
        )}
        {info && (
          <span className={'tag ' + (info.coverage === 'full' ? 'green-tag' : 'yellow-tag')} data-testid="video-coverage">
            {info.coverage === 'full'
              ? tr('VIDEO VOLLSTÄNDIG', 'FULL VIDEO')
              : info.coverage === 'partial'
                ? tr(`VIDEO TEILWEISE · ${Math.round((info.covered_ratio || 0) * 100)} %`, `PARTIAL VIDEO · ${Math.round((info.covered_ratio || 0) * 100)} %`)
                : info.coverage === 'pending'
                  ? tr('VIDEO WIRD GESPEICHERT', 'VIDEO BEING SAVED')
                  : tr('KEIN VIDEO', 'NO VIDEO')}
          </span>
        )}
      </div>
      {error && (
        <div className="notice danger" role="alert">
          {error}
        </div>
      )}
      {info?.coverage === 'none' && (
        <div className="empty-state" data-testid="no-video">
          {tr(
            'Für diese Runde gibt es kein Video. Es wird nur aufgenommen, wenn „Onboard automatisch aufnehmen“ aktiv ist und die Videoquelle lief. Telemetrie und Analyse dieser Runde sind unverändert verfügbar.',
            'There is no video for this lap. Video is only recorded when "Record onboard automatically" is on and the video source was running. Telemetry and analysis of this lap remain available.',
          )}
        </div>
      )}
      {info?.coverage === 'pending' && (
        <div className="notice" role="status">
          {tr('Das Video dieser Runde wird noch gespeichert …', 'The video of this lap is still being saved …')}
        </div>
      )}
      {timeline && rec && (
        <>
          <div className="onboard-video lap-player-video" ref={box}>
            <video
              ref={video}
              src={videoUrl(rec.id)}
              muted
              playsInline
              preload="auto"
              onLoadedMetadata={() => {
                measure()
                // Start exactly at the beginning of the selected lap.
                seek(timeline.availableFrom)
                video.current?.play().catch(() => {})
              }}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onError={() =>
                setVideoError(
                  tr(
                    'Das Video konnte nicht geladen werden (Datei fehlt oder wird von diesem Browser nicht unterstützt).',
                    'The video could not be loaded (file missing or not supported by this browser).',
                  ),
                )
              }
            />
            {prefs.visible && (
              <LapTimingHUD model={hud} prefs={prefs} rect={rect} de={de} onMove={(p) => setPrefs(p)} />
            )}
            {prefs.visible && prefs.tacho && sample && (
              <Tacho sample={sample} imperial={settings?.units === 'imperial'} de={de} />
            )}
            {videoError && (
              <div className="video-error" role="alert">
                {videoError}
              </div>
            )}
            {soundInfo && (
              <audio ref={audio} src={audioUrl(rec.id)} preload="auto" data-testid="lap-audio" />
            )}
            <span
              hidden
              data-testid="lap-sync"
              data-video-s={pos.toFixed(3)}
              data-wall={wall?.toFixed(3)}
              data-lap-ms={lapMs ?? ''}
              data-gear={sample?.channels.gear ?? ''}
            />
          </div>
          <div className="lap-player-controls panel" data-testid="lap-controls">
            <button
              className="primary"
              onClick={toggle}
              aria-label={playing ? tr('Pause', 'Pause') : tr('Abspielen', 'Play')}
            >
              {playing ? <Pause size={15} /> : <Play size={15} />}
            </button>
            <button aria-label={tr(`${SKIP_S} s zurück`, `Back ${SKIP_S} s`)} onClick={() => seek(pos - SKIP_S)}>
              <SkipBack size={14} />
            </button>
            <button aria-label={tr(`${SKIP_S} s vor`, `Forward ${SKIP_S} s`)} onClick={() => seek(pos + SKIP_S)}>
              <SkipForward size={14} />
            </button>
            <input
              type="range"
              className="lap-timeline"
              aria-label={tr('Zeit in der Runde', 'Time in lap')}
              data-testid="lap-timeline"
              min={0}
              max={span}
              step={0.01}
              value={Math.min(span, Math.max(0, pos - from))}
              style={{ background: coverageGradient(timeline) }}
              onChange={(e) => seek(from + Number(e.target.value))}
            />
            <span className="mono lap-clock" data-testid="lap-clock" title={tr('Rundenzeit (Spieluhr)', 'Lap time (game clock)')}>
              {formatLapTime(lapMs)} / {formatLapTime(info?.duration_ms)}
            </span>
            <span className="mono subtle small" title={tr('Videozeit ab Rundenbeginn', 'Video time from lap start')}>
              {clock(pos - from)} / {clock(span)}
            </span>
            <select
              aria-label={tr('Wiedergabegeschwindigkeit', 'Playback speed')}
              value={rate}
              onChange={(e) => {
                const r = Number(e.target.value)
                setRate(r)
                if (video.current) video.current.playbackRate = r
              }}
            >
              {RATES.map((r) => (
                <option key={r} value={r}>
                  {String(r).replace('.', de ? ',' : '.')}×
                </option>
              ))}
            </select>
            <button aria-pressed={loop} title={tr('Runde wiederholen', 'Repeat lap')} onClick={() => setLoop(!loop)}>
              <Repeat size={14} />
            </button>
            <button
              aria-pressed={prefs.visible}
              title={tr('HUD ein/aus', 'HUD on/off')}
              onClick={() => setPrefs({ visible: !prefs.visible })}
            >
              {prefs.visible ? <Eye size={14} /> : <EyeOff size={14} />} HUD
            </button>
            {soundInfo ? (
              <span className="lap-sound">
                <button
                  aria-pressed={!sound.muted}
                  aria-label={sound.muted ? tr('Ton einschalten', 'Sound on') : tr('Ton ausschalten', 'Sound off')}
                  title={audioLabel(soundInfo, de).text}
                  onClick={() => {
                    setSound({ muted: !sound.muted })
                    setSoundBlocked(false)
                  }}
                >
                  {sound.muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
                </button>
                <input
                  type="range"
                  aria-label={tr('Lautstärke', 'Volume')}
                  min={0}
                  max={1}
                  step={0.05}
                  value={sound.volume}
                  onChange={(e) => setSound({ volume: Number(e.target.value), muted: false })}
                />
                {soundBlocked && !sound.muted && (
                  <button
                    className="primary"
                    onClick={() => audio.current?.play().then(() => setSoundBlocked(false), () => {})}
                  >
                    {tr('Ton aktivieren', 'Enable sound')}
                  </button>
                )}
              </span>
            ) : (
              <span className="subtle small" data-testid="no-sound" title={audioLabel(rec.audio, de).text}>
                <VolumeX size={13} /> {tr('ohne Ton', 'no sound')}
              </span>
            )}
            <button aria-label={tr('Vollbild', 'Fullscreen')} onClick={() => shell.current?.requestFullscreen?.()}>
              <Maximize size={14} />
            </button>
            <span className="lap-sync-offset" title={tr('Video-Versatz: positiv = Bild läuft der Telemetrie hinterher', 'Video offset: positive = picture lags behind telemetry')}>
              {tr('Versatz', 'Offset')} {offset.toFixed(2)} s
              <button aria-label={tr('Versatz verringern', 'Decrease offset')} onClick={() => persist({ video_offset_s: Math.round((offset - 0.05) * 100) / 100 })}>
                −
              </button>
              <button aria-label={tr('Versatz erhöhen', 'Increase offset')} onClick={() => persist({ video_offset_s: Math.round((offset + 0.05) * 100) / 100 })}>
                +
              </button>
            </span>
          </div>
          {info!.coverage === 'partial' && (
            <p className="notice compact-hint" data-testid="video-gap">
              {tr(
                'Für einen Teil dieser Runde fehlt das Video (Quelle ausgefallen oder Aufnahme später gestartet). Rot markierte Bereiche der Zeitleiste sind nicht abspielbar; die Telemetrie ist vollständig.',
                'Part of this lap has no video (source dropped out or recording started later). Red parts of the timeline cannot be played; telemetry is complete.',
              )}
            </p>
          )}
          <p className="subtle small">
            {tr(
              'Leertaste: Abspielen/Pause · ←/→: 5 s (mit Umschalt 1 s). Die Wiedergabe endet am Rundenende. HUD und Tacho zeigen ausschließlich die gespeicherten Daten dieser Runde.',
              'Space: play/pause · ←/→: 5 s (Shift: 1 s). Playback stops at the end of the lap. HUD and speed overlay show only the stored data of this lap.',
            )}
          </p>
        </>
      )}
    </div>
  )
}
