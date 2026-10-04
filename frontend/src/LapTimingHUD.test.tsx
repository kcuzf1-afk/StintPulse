import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Onboard from './Onboard'
import { buildLiveHud, DEFAULT_HUD, loadHud, type HudModel } from './hud'
import type { Settings } from './types'

const settings = {
  language: 'de',
  units: 'metric',
  video_mode: 'url',
  video_url: 'http://192.168.178.49:9000/onboard.mp4',
  video_offset_s: 0,
} as unknown as Settings

const model: HudModel = {
  mode: 'live',
  status: null,
  position: 1,
  driver: 'Kimi Antonelli',
  compound: { label: 'S', full: 'Pirelli Soft (S)', manual: false },
  timeMs: 92064,
  phase: 'completed',
  lapNumber: 4,
  sectors: [
    { label: 'S1', ms: 30100, color: 'violet' },
    { label: 'S2', ms: 31200, color: 'green' },
    { label: 'S3', ms: 30764, color: 'yellow' },
  ],
  sectorsAvailable: true,
  invalid: false,
  incomplete: false,
  reference: { kind: 'personal_best', ms: 92500 },
  deltaMs: -436,
}

let restore: Array<() => void> = []
function stubProp(proto: object, key: string, value: unknown) {
  const original = Object.getOwnPropertyDescriptor(proto, key)
  Object.defineProperty(proto, key, { configurable: true, get: () => value })
  restore.push(() => {
    if (original) Object.defineProperty(proto, key, original)
    else delete (proto as Record<string, unknown>)[key]
  })
}
beforeEach(() => {
  localStorage.clear()
  // A 1600×900 picture box (jsdom has no layout).
  stubProp(HTMLElement.prototype, 'clientWidth', 1600)
  stubProp(HTMLElement.prototype, 'clientHeight', 900)
  HTMLMediaElement.prototype.play = vi.fn(async () => {})
})
afterEach(() => {
  restore.forEach((r) => r())
  restore = []
  vi.unstubAllGlobals()
})

test('HUD renders the TV look: position, surname, tyre, time and coloured sectors', () => {
  render(<Onboard settings={settings} sample={null} hud={model} />)
  const hud = screen.getByTestId('lap-hud')
  expect(hud.querySelector('.f1-pos')).toHaveTextContent('1')
  expect(hud.querySelector('.f1-name')).toHaveTextContent('ANTONELLI')
  expect(hud.querySelector('.f1-tyre')).toHaveTextContent('S')
  expect(hud.querySelector('.f1-tyre')).toHaveClass('tyre-S')
  expect(screen.getByTestId('lap-hud-time')).toHaveTextContent('1:32.064')
  // Equal-width digit cells: the running time cannot jitter.
  expect(screen.getByTestId('lap-hud-time').querySelectorAll('.d')).toHaveLength(6)
  // TV look by default: no extra line and no sector times.
  expect(screen.queryByTestId('lap-hud-details')).toBeNull()
  expect(hud).not.toHaveTextContent('30.100')
  expect([...hud.querySelectorAll('[data-color]')].map((e) => e.getAttribute('data-color'))).toEqual([
    'violet',
    'green',
    'yellow',
  ])
  // Default position: top right inside the picture.
  expect(hud.style.top).toBe('8px')
})

test('details switch adds lap, reference delta and sector times', () => {
  render(<Onboard settings={settings} sample={null} hud={model} />)
  fireEvent.click(screen.getByRole('button', { name: 'Rundenzeit-HUD' }))
  fireEvent.click(screen.getByLabelText('Details (Runde, Referenz, Sektorzeiten)'))
  expect(screen.getByTestId('lap-hud-details')).toHaveTextContent('RUNDE 4')
  expect(screen.getByTestId('lap-hud-details')).toHaveTextContent('PB −0.436')
  expect(screen.getByTestId('lap-hud')).toHaveTextContent('30.100')
  expect(loadHud().details).toBe(true)
})

test('a tyre without single-letter game code is shown neutral, never guessed', () => {
  render(
    <Onboard
      settings={settings}
      sample={null}
      hud={{ ...model, compound: { label: 'SM', full: 'Semislick (SM)', manual: false } }}
    />,
  )
  expect(document.querySelector('.f1-tyre')).toHaveClass('tyre-other')
  expect(document.querySelector('.f1-tyre')).toHaveTextContent('SM')
})

test('fullscreen is requested for the container holding video AND HUD', () => {
  const requested: Element[] = []
  HTMLElement.prototype.requestFullscreen = vi.fn(function (this: Element) {
    requested.push(this)
    return Promise.resolve()
  })
  render(<Onboard settings={settings} sample={null} hud={model} />)
  fireEvent.click(screen.getByRole('button', { name: 'Onboard-Vollbild' }))
  expect(requested).toHaveLength(1)
  expect(requested[0].querySelector('video')).not.toBeNull()
  expect(requested[0].querySelector('[data-testid=lap-hud]')).not.toBeNull()
})

test('drag with the grip stores the position; lock removes the grip', () => {
  render(<Onboard settings={settings} sample={null} hud={model} />)
  const grip = screen.getByRole('button', { name: 'Rundenzeit-HUD verschieben' })
  fireEvent.pointerDown(grip, { clientX: 1500, clientY: 30, pointerId: 1 })
  fireEvent.pointerMove(grip, { clientX: 700, clientY: 430, pointerId: 1 })
  fireEvent.pointerUp(grip, { clientX: 700, clientY: 430, pointerId: 1 })
  const saved = loadHud()
  expect(saved.x).toBeGreaterThan(0.4)
  expect(saved.x).toBeLessThan(0.6)
  expect(saved.y).toBeGreaterThan(0.4)
  fireEvent.click(screen.getByRole('button', { name: 'Rundenzeit-HUD' }))
  fireEvent.click(screen.getByLabelText('Position sperren'))
  expect(screen.queryByRole('button', { name: 'Rundenzeit-HUD verschieben' })).toBeNull()
  expect(loadHud().locked).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Position zurücksetzen' }))
  expect(loadHud()).toMatchObject({ x: DEFAULT_HUD.x, y: DEFAULT_HUD.y })
})

test('size, opacity and visibility persist for the next start', () => {
  const { unmount } = render(<Onboard settings={settings} sample={null} hud={model} />)
  fireEvent.click(screen.getByRole('button', { name: 'Rundenzeit-HUD' }))
  fireEvent.change(screen.getByLabelText('HUD-Größe'), { target: { value: '1.4' } })
  fireEvent.change(screen.getByLabelText('HUD-Deckkraft'), { target: { value: '0.5' } })
  unmount()
  render(<Onboard settings={settings} sample={null} hud={model} />)
  const hud = screen.getByTestId('lap-hud')
  expect(hud.style.opacity).toBe('0.5')
  expect(hud.style.transform).toBe('scale(1.4)')
  fireEvent.click(screen.getByRole('button', { name: 'Rundenzeit-HUD' }))
  fireEvent.click(screen.getByLabelText('Anzeigen'))
  expect(screen.queryByTestId('lap-hud')).toBeNull()
  expect(loadHud().visible).toBe(false)
})

test('HUD is not shown without a video source; the rest stays usable', () => {
  render(<Onboard settings={{ ...settings, video_mode: 'none' }} sample={null} hud={model} />)
  expect(screen.queryByTestId('lap-hud')).toBeNull()
  expect(screen.getByText('Videoquelle in den Einstellungen wählen.')).toBeInTheDocument()
})

test('switching from demo to AC removes demo data from the HUD', () => {
  const demo = { ...model, mode: 'demo' as const, driver: 'Demo Driver' }
  const { rerender } = render(<Onboard settings={settings} sample={null} hud={demo} />)
  expect(screen.getByTestId('lap-hud')).toHaveTextContent('DEMO')
  const waiting = buildLiveHud(
    { status: 'waiting_game', source: 'ac', meta: null, sample: null, timing: null } as never,
    'connected',
  )
  rerender(<Onboard settings={settings} sample={null} hud={waiting} />)
  const hud = screen.getByTestId('lap-hud')
  expect(hud).not.toHaveTextContent('DEMO')
  expect(hud).not.toHaveTextContent('Demo Driver'.toUpperCase())
  expect(screen.getByTestId('lap-hud-status')).toHaveTextContent('WARTE AUF SPIEL')
  expect(screen.getByTestId('lap-hud-time')).toHaveTextContent('—')
})

test('Document Picture-in-Picture takes video and HUD together', async () => {
  const pipDoc = document.implementation.createHTMLDocument('pip')
  const listeners: Record<string, () => void> = {}
  const pipWindow = { document: pipDoc, addEventListener: (k: string, f: () => void) => (listeners[k] = f) }
  vi.stubGlobal('documentPictureInPicture', { requestWindow: vi.fn(async () => pipWindow) })
  render(<Onboard settings={settings} sample={null} hud={model} />)
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Bild-in-Bild' }))
  })
  expect(pipDoc.body.querySelector('video')).not.toBeNull()
  expect(pipDoc.body.querySelector('[data-testid=lap-hud]')).not.toBeNull()
  act(() => listeners.pagehide())
  expect(document.querySelector('.onboard-shell [data-testid=lap-hud]')).not.toBeNull()
})

test('without Document PiP the video-only limitation is stated', async () => {
  HTMLVideoElement.prototype.requestPictureInPicture = vi.fn(async () => ({}) as PictureInPictureWindow)
  render(<Onboard settings={settings} sample={null} hud={model} />)
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Bild-in-Bild' }))
  })
  expect(screen.getByRole('status')).toHaveTextContent('ohne Rundenzeit-HUD')
})
