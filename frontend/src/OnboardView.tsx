/** Onboard: large video with lap-timing HUD. Extra data only in an optional side panel. */
import { useState } from 'react'
import { PanelRightClose, PanelRightOpen, Pause, Play, RotateCcw, X } from 'lucide-react'
import Onboard from './Onboard'
import type { Frame, LapSummary, Meta, Sample, Settings } from './types'
import type { HudModel } from './hud'
import { formatSectorTime } from './hud'
import { lapTime, toFuel, units, value, type Units } from './format'
import type { TelemetryView } from './connection'
import { SectorTimes, TyreGrid } from './widgets'

export interface ReplayState {
  samples: Sample[]
  meta: Meta
  duration: number
  lap: LapSummary
  laps: LapSummary[]
}

export default function OnboardView({
  frame,
  settings,
  sample,
  hud,
  telemetry,
  video,
  onVideoState,
  persist,
  de,
  replay,
  replayTime,
  replaying,
  videoBase,
  setReplayTime,
  setReplaying,
  exitReplay,
}: {
  frame: Frame
  settings: Settings | null
  sample: Sample | null
  hud: HudModel | null
  telemetry: TelemetryView
  video: { live: boolean; label: string }
  onVideoState: (v: { live: boolean; label: string }) => void
  persist: (patch: Partial<Settings>) => void
  de: boolean
  replay: ReplayState | null
  replayTime: number
  replaying: boolean
  videoBase: number
  setReplayTime: (t: number) => void
  setReplaying: (v: boolean) => void
  exitReplay: () => void
}) {
  const tr = (a: string, b: string) => (de ? a : b)
  const [side, setSide] = useState(false)
  const u: Units = settings?.units || 'metric'
  return (
    <div className={'onboard-page' + (side ? ' with-side' : '')} data-testid="onboard-page">
      <div className="onboard-toolbar">
        <label>
          {tr('Videoquelle', 'Video source')}
          <select
            aria-label={tr('Videoquelle', 'Video source')}
            value={settings?.video_mode || 'none'}
            onChange={(e) => persist({ video_mode: e.target.value as Settings['video_mode'] })}
          >
            <option value="none">{tr('Keine', 'None')}</option>
            <option value="camera">OBS Virtual Camera / Webcam</option>
            <option value="screen">{tr('Fenster freigeben', 'Share window')}</option>
            <option value="url">{tr('Video-URL', 'Video URL')}</option>
            <option value="file">{tr('Lokale Aufnahme', 'Local recording')}</option>
          </select>
        </label>
        <span className="state-pill" data-testid="video-state">
          <i className={'dot ' + (video.live ? 'green' : 'muted-dot')} />
          {tr('Video: ', 'Video: ')}
          {video.label || tr('kein Video', 'no video')}
        </span>
        <span className="state-pill" data-testid="telemetry-state">
          <i className={'dot ' + (replay ? 'cyan' : telemetry.tone === 'muted' ? 'muted-dot' : telemetry.tone)} />
          {replay ? tr('Telemetrie: Wiedergabe', 'Telemetry: replay') : de ? telemetry.de : telemetry.en}
        </span>
        <button className="side-toggle" aria-pressed={side} onClick={() => setSide(!side)}>
          {side ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
          {tr('Infos', 'Info')}
        </button>
      </div>
      {replay && (
        <div className="replay-controls panel" data-testid="replay-controls">
          <button
            className="primary"
            aria-label={replaying ? tr('Wiedergabe pausieren', 'Pause replay') : tr('Wiedergabe starten', 'Play replay')}
            onClick={() => setReplaying(!replaying)}
          >
            {replaying ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <button aria-label={tr('Wiedergabe neu starten', 'Restart replay')} onClick={() => setReplayTime(0)}>
            <RotateCcw size={14} />
          </button>
          <input
            aria-label={tr('Wiedergabezeit', 'Replay time')}
            type="range"
            min={0}
            max={replay.duration}
            step={0.01}
            value={replayTime}
            onChange={(e) => setReplayTime(Number(e.target.value))}
          />
          <span className="mono">
            {lapTime(replayTime * 1000, true)} / {lapTime(replay.duration * 1000)}
          </span>
          <span className={'tag ' + (replay.meta.source === 'demo' ? 'orange-tag' : 'cyan-tag')}>
            {replay.meta.source === 'demo' ? 'DEMO-' : ''}
            {tr('WIEDERGABE', 'REPLAY')} · {tr('Runde', 'Lap')} {replay.lap.number}
          </span>
          <button onClick={exitReplay}>
            <X size={13} />
            {tr('Wiedergabe beenden', 'Exit replay')}
          </button>
        </div>
      )}
      <div className="onboard-layout">
        <div className="onboard-main">
          <Onboard
            settings={settings}
            sample={sample}
            replayTime={replay ? replayTime + videoBase : undefined}
            replaying={replaying}
            onDevice={(label) => persist({ video_device: label })}
            hud={hud}
            onVideoState={onVideoState}
          />
        </div>
        {side && (
          <aside className="onboard-side panel" data-testid="onboard-side">
            <h3>{tr('Sektoren', 'Sectors')}</h3>
            {replay ? (
              <table className="sector-times">
                <tbody>
                  {replay.lap.sectors_ms.map((v, i) => (
                    <tr key={i}>
                      <th>S{i + 1}</th>
                      <td>{formatSectorTime(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <SectorTimes frame={frame} de={de} />
            )}
            <h3>{tr('Reifen', 'Tyres')}</h3>
            <TyreGrid sample={sample} settings={settings} de={de} />
            <h3>{tr('Kraftstoff', 'Fuel')}</h3>
            <p className="side-value">
              {value(toFuel(sample?.channels.fuel, u), 1)} {units.fuel(u)}
              {!replay && frame.fuel_laps_est !== null && (
                <small>
                  {' '}
                  · ≈ {value(frame.fuel_laps_est, 1)} {tr('Runden', 'laps')}
                </small>
              )}
            </p>
          </aside>
        )}
      </div>
    </div>
  )
}
