import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import {
  MAX_INTERPOLATION_GAP_S,
  audioLabel,
  audioTimeAt,
  syncSound,
  atLapEnd,
  clampToLap,
  coverageGradient,
  lapTimeline,
  sampleAtWall,
  wallAt,
  type LapVideo,
} from './playback'
import { parseRoute, routeHash } from './routes'
import SessionsView from './SessionsView'
import LapVideoPlayer from './LapVideoPlayer'
import type { LapSummary, Sample, Session, Settings } from './types'

const sample = (t: number, lapMs: number, speed: number, gear: number, extra: Partial<Sample> = {}): Sample =>
  ({
    source: 'ac',
    packet_id: Math.round(t * 1000),
    captured_at: t,
    status: 2,
    completed_laps: 3,
    lap_ms: lapMs,
    last_lap_ms: 0,
    best_lap_ms: 0,
    sector_index: 0,
    last_sector_ms: 0,
    position: 1,
    session_left_ms: null,
    lap_pos: lapMs / 100000,
    coords: [0, 0, 0],
    in_pit: false,
    tyres_out: 0,
    penalty_s: 0,
    channels: { speed, gear, gas: 1, brake: 0 },
    tyres: [],
    ...extra,
  }) as Sample

test('continuous values are interpolated, gear and lap counter are not', () => {
  const s = [sample(100, 0, 100, 3), sample(100.1, 100, 200, 4, { sector_index: 1 })]
  const mid = sampleAtWall(s, 100.025)!
  expect(mid.channels.speed).toBeCloseTo(125)
  expect(mid.lap_ms).toBe(25)
  expect(mid.channels.gear).toBe(3) // never 3.25
  expect(mid.sector_index).toBe(0)
  expect(sampleAtWall(s, 99)!.lap_ms).toBe(0) // before the lap: first sample
  expect(sampleAtWall(s, 101)!.lap_ms).toBe(100) // after: last sample
})

test('a game pause in the lap is held, not bridged', () => {
  // 20 s pause between both samples: the lap clock stands still in the video.
  const s = [sample(100, 5000, 150, 3), sample(120, 5016, 151, 3)]
  expect(MAX_INTERPOLATION_GAP_S).toBeLessThan(1)
  expect(sampleAtWall(s, 110)!.lap_ms).toBe(5000)
})

test('video position and telemetry share one clock incl. offset', () => {
  // Video time 0 = server time 1000; the picture lags 0.2 s behind telemetry.
  expect(wallAt(12.2, 1000, 0.2)).toBeCloseTo(1012)
  const info = {
    video_from_s: 10,
    video_to_s: 100,
    available_from_s: 10,
    available_to_s: 100,
  } as LapVideo
  const t = lapTimeline(info)!
  expect(clampToLap(t, 5)).toBe(10)
  expect(clampToLap(t, 500)).toBe(100)
  expect(atLapEnd(t, 99.999)).toBe(true)
  expect(atLapEnd(t, 99)).toBe(false)
  // Partial video: the missing first 20 % are marked on the timeline.
  const partial = lapTimeline({ ...info, video_from_s: -8, available_from_s: 0, video_to_s: 32, available_to_s: 32 } as LapVideo)!
  expect(coverageGradient(partial)).toContain('var(--video-gap) 0 20%')
  expect(lapTimeline({ ...info, available_from_s: undefined } as LapVideo)).toBeNull()
})

test('sound runs on the same clock as picture and telemetry', () => {
  const sound = { status: 'ready' as const, mode: 'game' as const, started_at: 999.5, duration_ms: 120000 }
  // Video 0 = server 1000; picture lags 0.2 s: sound captured at 1000 + 10 - 0.2.
  expect(audioTimeAt(10, 1000, 0.2, sound)).toBeCloseTo(10.3)
  expect(audioTimeAt(-1, 1000, 0, sound)).toBeNull() // before the sound file
  expect(audioTimeAt(200, 1000, 0, sound)).toBeNull() // after it
  expect(audioTimeAt(10, 1000, 0, { ...sound, status: 'none' })).toBeNull()
  // Small drift: slightly slower/faster; big jump: reposition.
  expect(syncSound(1, 0.005)).toEqual({ seek: false, rate: 1 })
  expect(syncSound(2, 0.03).rate).toBeCloseTo(2 * 0.97)
  expect(syncSound(1, -0.04).rate).toBeCloseTo(1.04)
  expect(syncSound(1, 0.12).rate).toBeCloseTo(0.95) // clamped to 5 %
  expect(syncSound(1, 0.4).seek).toBe(true)
  expect(audioLabel(sound, true).text).toBe('Ton: Spielsound')
  expect(audioLabel({ status: 'none', reason: 'Assetto Corsa (acs.exe) is not running' }, true).text).toBe(
    'Ohne Ton: Assetto Corsa lief nicht',
  )
})

test('the lap video has its own route', () => {
  expect(parseRoute('#/sessions/video/abc-123')).toEqual({ page: 'sessions', video: 'abc-123' })
  expect(routeHash({ page: 'sessions', video: 'abc-123' })).toBe('#/sessions/video/abc-123')
  expect(parseRoute('#/sessions/video/<script>')).toEqual({ page: 'sessions' })
})

/* ---------- UI ---------- */

const settings = { language: 'de', units: 'metric', video_offset_s: 0 } as unknown as Settings
const lap = (id: string, number: number, coverage: NonNullable<LapSummary['video']>['coverage'], ratio = 1): LapSummary => ({
  id,
  session_id: 's1',
  number,
  duration_ms: 90000 + number,
  valid: number !== 3,
  complete: number !== 1,
  reasons: number === 3 ? ['off_track_inferred'] : number === 1 ? ['partial_start'] : [],
  sectors_ms: [30000, 30000, 30000],
  events: [],
  tips: [],
  video: { coverage, recording_id: coverage === 'none' ? null : 'r1', covered_ratio: ratio, audio: number === 2 },
})
const session: Session = {
  id: 's1',
  created_at: '2026-10-04T12:00:00Z',
  ended_at: null,
  meta: { source: 'ac', driver: 'Max Tester', car: 'ks_car', track: 'sepang', layout: 'gp', session_type: 0, track_length: 5500, sector_count: 3, max_rpm: 9000, compound: '', air_temp: null, road_temp: null },
  lap_count: 4,
  best_ms: 90002,
  favorite: false,
  video_count: 1,
  laps: [lap('l1', 1, 'partial', 0.4), lap('l2', 2, 'full'), lap('l3', 3, 'full'), lap('l4', 4, 'pending'), lap('l5', 5, 'none')],
}
const fetchMock = vi.fn()
const ok = (body: unknown) => ({ ok: true, json: async () => body })
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  localStorage.clear()
})

test('sessions show the video status per lap and offer "Onboard ansehen" only with video', async () => {
  fetchMock.mockImplementation(async (path: string) =>
    path.startsWith('/api/sessions?') ? ok([session]) : path === '/api/sessions/s1' ? ok(session) : ok({}),
  )
  const onWatch = vi.fn()
  render(
    <SessionsView activeId={null} settings={settings} onCompare={() => {}} onReplay={() => {}} onReplaySession={() => {}} onWatch={onWatch} />,
  )
  const card = await screen.findByText('sepang')
  expect(screen.getByTitle('Session mit Onboard-Video')).toBeInTheDocument()
  fireEvent.click(card.closest('.session-item')!.querySelector('input')!)
  await waitFor(() => expect(screen.getAllByTestId('video-badge')).toHaveLength(5))
  expect(screen.getAllByTestId('video-badge').map((b) => b.textContent)).toEqual([
    'Video teilweise · 40 %',
    'Video verfügbar · mit Ton',
    'Video verfügbar',
    'Video wird noch gespeichert …',
    ' Kein Video',
  ])
  const buttons = screen.getAllByRole('button', { name: /Onboard ansehen/ })
  expect(buttons).toHaveLength(3) // partial + two full, never "pending" or "none"
  fireEvent.click(screen.getByRole('button', { name: 'Onboard ansehen: Runde 3' }))
  expect(onWatch).toHaveBeenCalledWith(expect.objectContaining({ id: 'l3' }))
})

const playback = (patch: Partial<LapVideo>): LapVideo => ({
  lap_id: 'l3',
  session_id: 's1',
  number: 3,
  duration_ms: 90003,
  complete: true,
  valid: false,
  reasons: ['off_track_inferred'],
  offset_s: 0,
  lap_start: 1010,
  lap_end: 1100,
  coverage: 'full',
  covered_ratio: 1,
  recording: { id: 'r1', session_id: 's1', segment: 1, status: 'ready', mime: 'video/webm', started_at: 1000, duration_ms: 200000, bytes: 1, indexed: true, end_reason: 'session_end', error: null },
  video_from_s: 10,
  video_to_s: 100,
  available_from_s: 10,
  available_to_s: 100,
  segments: [],
  ...patch,
})
function playerFetch(info: LapVideo) {
  fetchMock.mockImplementation(async (path: string) => {
    if (path === '/api/laps/l3/video') return ok(info)
    if (path === '/api/laps/l3') return ok({ session_id: 's1', samples: [sample(1010, 0, 80, 2), sample(1100, 90003, 90, 3)] })
    if (path === '/api/sessions/s1') return ok(session)
    return ok({})
  })
}

test('player: playback label, invalid lap marked, video from the PC app, no live data', async () => {
  playerFetch(playback({}))
  render(<LapVideoPlayer lapId="l3" settings={settings} de onClose={() => {}} persist={() => {}} />)
  expect(await screen.findByTestId('playback-tag')).toHaveTextContent('WIEDERGABE')
  await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
  expect(document.querySelector('video')!.getAttribute('src')).toBe('/api/videos/r1')
  expect(screen.getByText('UNGÜLTIGE RUNDE')).toBeInTheDocument()
  expect(screen.getByTestId('video-coverage')).toHaveTextContent('VIDEO VOLLSTÄNDIG')
  expect(screen.getByRole('combobox', { name: 'Wiedergabegeschwindigkeit' })).toHaveTextContent('0,5×')
  await waitFor(() => expect(screen.getByText(/sepang \/ gp · Runde 3/)).toBeInTheDocument())
})

test('player: sound file is played along when it was recorded', async () => {
  playerFetch(
    playback({
      recording: {
        ...playback({}).recording!,
        audio: { status: 'ready', mode: 'game', started_at: 999.9, duration_ms: 200000, sample_rate: 48000, channels: 2 },
      },
    }),
  )
  const { unmount } = render(<LapVideoPlayer lapId="l3" settings={settings} de onClose={() => {}} persist={() => {}} />)
  expect(await screen.findByTestId('sound-tag')).toHaveTextContent('MIT TON')
  expect(screen.getByTestId('lap-audio').getAttribute('src')).toBe('/api/audio/r1')
  fireEvent.click(screen.getByRole('button', { name: 'Ton ausschalten' }))
  expect(screen.getByRole('button', { name: 'Ton einschalten' })).toBeInTheDocument()
  expect(JSON.parse(localStorage.getItem('aceda-sound-v1')!).muted).toBe(true)
  unmount()
  playerFetch(playback({ recording: { ...playback({}).recording!, audio: { status: 'none', reason: 'Assetto Corsa (acs.exe) is not running' } } }))
  render(<LapVideoPlayer lapId="l3" settings={settings} de onClose={() => {}} persist={() => {}} />)
  expect(await screen.findByTestId('sound-tag')).toHaveTextContent('OHNE TON')
  expect(screen.getByTestId('no-sound')).toHaveAttribute('title', 'Ohne Ton: Assetto Corsa lief nicht')
  expect(screen.queryByTestId('lap-audio')).toBeNull()
})

test('player: partial video and missing video are explained', async () => {
  playerFetch(playback({ coverage: 'partial', covered_ratio: 0.62, video_from_s: -20, available_from_s: 0 }))
  const { unmount } = render(<LapVideoPlayer lapId="l3" settings={settings} de onClose={() => {}} persist={() => {}} />)
  expect(await screen.findByTestId('video-gap')).toHaveTextContent('fehlt das Video')
  expect(screen.getByTestId('video-coverage')).toHaveTextContent('62 %')
  unmount()
  playerFetch(playback({ coverage: 'none', recording: null, video_from_s: undefined, available_from_s: undefined }))
  render(<LapVideoPlayer lapId="l3" settings={settings} de onClose={() => {}} persist={() => {}} />)
  expect(await screen.findByTestId('no-video')).toHaveTextContent('kein Video')
  expect(document.querySelector('video')).toBeNull()
})
