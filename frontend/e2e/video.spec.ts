/**
 * Recording → storage → lap linkage → playback, in a real browser.
 * Video source: Chromium's synthetic test camera (--use-fake-device-for-media-stream).
 * Telemetry: the demo source in time-lapse (AC_AGENT_DEMO_RATE, set by
 * scripts/browser_tests.py), i.e. test data separate from any real drive.
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

interface Sample {
  captured_at: number
  lap_ms: number
}
interface LapVideo {
  lap_id: string
  number: number
  duration_ms: number
  coverage: string
  lap_start: number
  lap_end: number
  offset_s: number
  video_from_s: number
  video_to_s: number
  available_from_s: number
  available_to_s: number
  recording: { id: string; started_at: number; duration_ms: number; segment: number }
}

const json = async (request: APIRequestContext, path: string) => (await request.get(path)).json()

/** Lap clock at a wall-clock instant, interpolated like the player does. */
function lapMsAt(samples: Sample[], wall: number) {
  const s = [...samples].sort((a, b) => a.captured_at - b.captured_at)
  let i = s.findIndex((x) => x.captured_at > wall) - 1
  if (i < 0) i = wall < s[0].captured_at ? 0 : s.length - 1
  const a = s[i],
    b = s[i + 1]
  if (!b || b.captured_at - a.captured_at > 0.25) return a.lap_ms
  return a.lap_ms + ((b.lap_ms - a.lap_ms) * (wall - a.captured_at)) / (b.captured_at - a.captured_at)
}

async function setRecording(page: Page, on: boolean) {
  await page.getByRole('button', { name: 'Einstellungen', exact: true }).click()
  await page.getByRole('tab', { name: 'Onboard und HUD' }).click()
  const box = page.getByLabel(/Onboard automatisch aufnehmen/)
  if ((await box.isChecked()) !== on) await box.click()
  await page.getByRole('button', { name: 'Speichern' }).click()
  await expect(page.getByRole('button', { name: 'Gespeichert' })).toBeVisible()
}

test('a missing video source is shown while telemetry keeps running', async ({ page, request }) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw Object.assign(new Error('no camera'), { name: 'NotFoundError' })
    }
  })
  await request.patch('/api/settings', { data: { video_mode: 'camera', record_auto: true } })
  try {
    await page.goto('/#/live')
    await expect(page.getByTestId('record-status')).toHaveText(/VIDEOQUELLE FEHLT/, { timeout: 15000 })
    await expect(page.getByTestId('telemetry-status')).toHaveText(/DEMO-TELEMETRIE/)
  } finally {
    await request.patch('/api/settings', { data: { video_mode: 'none', record_auto: false } })
  }
})

test('a drive is recorded, laps are linked and a lap plays with its stored HUD', async ({ page, request }) => {
  test.setTimeout(300000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await request.patch('/api/settings', {
    data: { video_mode: 'camera', record_auto: false, record_quality: 'saver', video_offset_s: 0 },
  })
  try {
    await page.goto('/#/live')
    await setRecording(page, true)
    const pill = page.getByTestId('record-status')
    await expect(pill).toHaveAttribute('data-state', 'recording', { timeout: 20000 })
    const sid = (await json(request, '/api/live')).session_id as string

    // 6. Navigating through all areas does not interrupt the recording.
    for (const name of ['Analyse', 'Onboard', 'Sessions', 'Einstellungen', 'Live-Dashboard']) {
      await page.getByRole('button', { name, exact: true }).click()
      await page.waitForTimeout(1200)
      await expect(pill).toHaveAttribute('data-state', 'recording')
    }
    // Only this run's segment (a retry may find an earlier one in the session).
    const during = (await json(request, '/api/recordings?session_id=' + sid)).filter(
      (r: { status: string }) => r.status === 'recording',
    )
    expect(during).toHaveLength(1)
    expect(during[0].chunks).toBeGreaterThan(2)
    const mine = async () =>
      (await json(request, '/api/recordings?session_id=' + sid)).find((r: { id: string }) => r.id === during[0].id)

    // Three laps finished while recording: the first one began before the
    // recording, so at least two consecutive laps are fully on video.
    await expect
      .poll(
        async () => {
          const s = await json(request, '/api/sessions/' + sid)
          return s.laps.filter((l: { complete: boolean; video: { coverage: string } }) => l.complete && l.video.coverage === 'pending').length
        },
        { timeout: 150000, intervals: [2000] },
      )
      .toBeGreaterThanOrEqual(3)
    await setRecording(page, false)

    // 1. A real, finished, indexed local video file.
    await expect
      .poll(async () => (await mine()).status, { timeout: 60000 })
      .toBe('ready')
    const rec = await mine()
    expect(rec.indexed).toBe(true)
    expect(rec.end_reason).toBe('disabled') // one continuous segment, ended by switching off
    expect(rec.duration_ms).toBeGreaterThan(25000)
    const range = await request.get('/api/videos/' + rec.id, { headers: { Range: 'bytes=0-1023' } })
    expect(range.status()).toBe(206)
    // Sound of the segment (test tone from the PC app, AC_AGENT_TEST_AUDIO).
    expect(rec.audio).toMatchObject({ status: 'ready', mode: 'test', sample_rate: 48000 })
    expect(rec.audio.started_at).toBeLessThanOrEqual(rec.started_at + 0.05)
    expect((await request.get('/api/audio/' + rec.id, { headers: { Range: 'bytes=0-1023' } })).status()).toBe(206)

    // 2. Two consecutive laps on consecutive sections of the same video.
    const session = await json(request, '/api/sessions/' + sid)
    const full = session.laps.filter((l: { video: { coverage: string } }) => l.video.coverage === 'full')
    const pairIndex = full.findIndex((l: { number: number }, i: number) => full[i + 1]?.number === l.number + 1)
    expect(pairIndex).toBeGreaterThanOrEqual(0)
    const va: LapVideo = await json(request, `/api/laps/${full[pairIndex].id}/video`)
    const vb: LapVideo = await json(request, `/api/laps/${full[pairIndex + 1].id}/video`)
    expect(va.recording.id).toBe(rec.id)
    expect(vb.recording.id).toBe(rec.id)
    expect(vb.video_from_s).toBeGreaterThan(va.video_from_s)
    expect(Math.abs(vb.video_from_s - va.video_to_s)).toBeLessThan(0.05)

    // 3. "Onboard ansehen" starts at the selected lap.
    await page.getByRole('button', { name: 'Sessions', exact: true }).click()
    const card = page.locator('.session-item').filter({ hasText: 'Engineering Circuit' }).first()
    await card.locator('input[type=checkbox]').check()
    await page.getByRole('button', { name: `Onboard ansehen: Runde ${vb.number}` }).click()
    await expect(page.getByTestId('playback-tag')).toHaveText('WIEDERGABE')
    await expect(page.getByTestId('sound-tag')).toHaveText('MIT TON')
    await expect(page.locator('.lap-hud')).toContainText('WIEDERGABE')
    const sync = page.getByTestId('lap-sync')
    const videoPos = async () => Number(await sync.getAttribute('data-video-s'))
    // Video position and HUD value from the same rendered frame.
    const read = () =>
      sync.evaluate((el) => [Number((el as HTMLElement).dataset.videoS), Number((el as HTMLElement).dataset.lapMs)])
    await expect.poll(videoPos, { timeout: 15000 }).toBeGreaterThanOrEqual(vb.available_from_s - 0.01)
    expect(await videoPos()).toBeLessThan(vb.available_from_s + 1.5)
    expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.videoWidth)).toBeGreaterThan(0)

    // 5. HUD follows the video after seeking (paused) …
    await page.getByRole('button', { name: 'Pause' }).click()
    const samples: Sample[] = (await json(request, `/api/laps/${vb.lap_id}`)).samples
    const timeline = page.getByTestId('lap-timeline')
    for (const fraction of [0.6, 0.25]) {
      await timeline.evaluate((el, f) => {
        const input = el as HTMLInputElement
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
        setter.call(input, String(Number(input.max) * f))
        input.dispatchEvent(new Event('input', { bubbles: true }))
      }, fraction)
      await page.waitForTimeout(600)
      const [v, shown] = await read()
      const target = vb.video_from_s + (vb.video_to_s - vb.video_from_s) * fraction
      expect(Math.abs(v - target)).toBeLessThan(0.1)
      const wall = rec.started_at + v - vb.offset_s
      // Same instant on one clock; demo time-lapse makes 1 ms wall = several ms lap clock.
      expect(Math.abs(shown - lapMsAt(samples, wall))).toBeLessThan(60)
    }

    // … and at 2× speed until the lap ends (4.).
    await page.getByRole('combobox', { name: 'Wiedergabegeschwindigkeit' }).selectOption('2')
    await page.getByRole('button', { name: 'Abspielen' }).click()
    await page.waitForTimeout(1500)
    const [v, shown] = await read()
    expect(v).toBeGreaterThan(vb.video_from_s + (vb.video_to_s - vb.video_from_s) * 0.25 + 2)
    // Sound follows the picture (one clock, same offset), also at 2×.
    // Sound follows the picture (settles within moments, also on slow machines).
    const soundDrift = async () => {
      const m = await page.evaluate(() => {
        const video = document.querySelector('video')!,
          sound = document.querySelector('audio')!
        return { v: video.currentTime, a: sound.currentTime, paused: sound.paused, rate: sound.playbackRate }
      })
      if (m.paused || m.rate < 1.8) return 99
      return Math.abs(m.a - (rec.started_at + m.v - vb.offset_s - rec.audio.started_at))
    }
    await expect.poll(soundDrift, { timeout: 5000, intervals: [200] }).toBeLessThan(0.1)
    const wall = rec.started_at + v - vb.offset_s
    expect(Math.abs(shown - lapMsAt(samples, wall))).toBeLessThan(60)
    await expect(page.getByRole('button', { name: 'Abspielen' })).toBeVisible({ timeout: 30000 })
    expect(Math.abs((await videoPos()) - vb.available_to_s)).toBeLessThan(0.05)
    await expect.poll(() => page.evaluate(() => document.querySelector('audio')!.paused)).toBe(true)
    await expect(page.getByTestId('lap-clock')).toContainText(
      new Date(vb.duration_ms).toISOString().slice(14, 23).replace(/^0/, ''),
    )
    await page.getByRole('button', { name: 'Zurück zu Sessions' }).click()
    await expect(page.locator('.lap-list')).toBeVisible()
    expect(errors).toEqual([])
  } finally {
    await request.patch('/api/settings', { data: { video_mode: 'none', record_auto: false } })
  }
})
