import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Onboard from './Onboard'
import { pickCamera, cameraError } from './camera'
import type { Settings } from './types'

const base = {
  language: 'de',
  units: 'metric',
  video_mode: 'camera',
  video_url: '',
  video_device: '',
  video_offset_s: 0,
} as unknown as Settings

class FakeTrack {
  label: string
  stopped = false
  onended: (() => void) | null = null
  constructor(label: string) {
    this.label = label
  }
  stop() {
    this.stopped = true
  }
}
function fakeStream(label: string) {
  const track = new FakeTrack(label)
  return { track, stream: { getTracks: () => [track], getVideoTracks: () => [track] } }
}

let devices: Array<{ kind: string; deviceId: string; label: string }>
let granted: boolean
let listeners: Array<() => void>
let opened: Array<{ constraints: MediaStreamConstraints; track: FakeTrack }>
let failNext: string | null

beforeEach(() => {
  granted = true
  listeners = []
  opened = []
  failNext = null
  devices = [
    { kind: 'audioinput', deviceId: 'mic', label: 'Microphone' },
    { kind: 'videoinput', deviceId: 'cam1', label: 'Integrated Webcam' },
    { kind: 'videoinput', deviceId: 'obs', label: 'OBS Virtual Camera' },
  ]
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      enumerateDevices: vi.fn(async () =>
        devices.map((d) => ({ ...d, label: granted ? d.label : '' })),
      ),
      getUserMedia: vi.fn(async (constraints: MediaStreamConstraints) => {
        if (failNext) {
          const name = failNext
          failNext = null
          throw Object.assign(new Error(name), { name })
        }
        const cams = devices.filter((d) => d.kind === 'videoinput')
        if (!cams.length) throw Object.assign(new Error('none'), { name: 'NotFoundError' })
        granted = true
        const id = (constraints.video as { deviceId?: { exact: string } })?.deviceId?.exact
        const device = cams.find((d) => d.deviceId === id) || cams[0]
        const { track, stream } = fakeStream(device.label)
        opened.push({ constraints, track })
        return stream
      }),
      addEventListener: (_: string, fn: () => void) => listeners.push(fn),
      removeEventListener: (_: string, fn: () => void) => {
        listeners = listeners.filter((l) => l !== fn)
      },
    },
  })
  Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', {
    configurable: true,
    writable: true,
    value: null,
  })
  HTMLMediaElement.prototype.play = vi.fn(async () => {})
})
afterEach(() => vi.restoreAllMocks())

test('OBS Virtual Camera is selected automatically and shown as live onboard', async () => {
  render(<Onboard settings={base} sample={null} />)
  await waitFor(() => expect(screen.getByText('OBS VIRTUAL CAMERA')).toBeInTheDocument())
  const last = opened.at(-1)!
  expect((last.constraints.video as { deviceId: { exact: string } }).deviceId.exact).toBe('obs')
  expect(last.constraints.audio).toBe(false)
  expect(document.querySelector('video')!.style.display).toBe('block')
})

test('camera names are requested once when permission was not granted yet', async () => {
  granted = false
  render(<Onboard settings={base} sample={null} />)
  await waitFor(() => expect(screen.getByText('OBS VIRTUAL CAMERA')).toBeInTheDocument())
  expect(opened).toHaveLength(2)
  expect(opened[0].track.stopped).toBe(true) // permission probe released
  expect(opened[1].track.stopped).toBe(false)
})

test('saved camera wins and switching cameras persists the choice', async () => {
  const onDevice = vi.fn()
  render(
    <Onboard
      settings={{ ...base, video_device: 'Integrated Webcam' }}
      sample={null}
      onDevice={onDevice}
    />,
  )
  await waitFor(() => expect(screen.getByText('INTEGRATED WEBCAM')).toBeInTheDocument())
  fireEvent.change(screen.getByLabelText('Kamera'), { target: { value: 'OBS Virtual Camera' } })
  await waitFor(() => expect(screen.getByText('OBS VIRTUAL CAMERA')).toBeInTheDocument())
  expect(onDevice).toHaveBeenCalledWith('OBS Virtual Camera')
  expect(opened.filter((o) => !o.track.stopped)).toHaveLength(1) // exactly one stream
})

test('missing OBS camera explains what to do and reconnects on devicechange', async () => {
  devices = devices.filter((d) => d.kind !== 'videoinput')
  render(<Onboard settings={base} sample={null} />)
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent('Virtuelle Kamera starten'),
  )
  devices.push({ kind: 'videoinput', deviceId: 'obs', label: 'OBS Virtual Camera' })
  await act(async () => listeners.forEach((l) => l()))
  await waitFor(() => expect(screen.getByText('OBS VIRTUAL CAMERA')).toBeInTheDocument())
})

test('stopped virtual camera is reported, not shown as connected', async () => {
  render(<Onboard settings={base} sample={null} />)
  await waitFor(() => expect(screen.getByText('OBS VIRTUAL CAMERA')).toBeInTheDocument())
  await act(async () => opened.at(-1)!.track.onended?.())
  expect(screen.getByText('KEIN VIDEO')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Kamera getrennt')
})

test('busy camera and stream release on mode change', async () => {
  failNext = 'NotReadableError'
  const { rerender } = render(<Onboard settings={base} sample={null} />)
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('belegt'))
  fireEvent.click(screen.getByTitle('Erneut verbinden'))
  await waitFor(() => expect(screen.getByText('OBS VIRTUAL CAMERA')).toBeInTheDocument())
  rerender(<Onboard settings={{ ...base, video_mode: 'none' }} sample={null} />)
  expect(opened.every((o) => o.track.stopped)).toBe(true)
})

test('camera helpers', () => {
  const cams = [
    { deviceId: 'a', label: 'Webcam' },
    { deviceId: 'b', label: 'OBS-Virtual-Camera' },
  ]
  expect(pickCamera(cams, '')!.deviceId).toBe('b')
  expect(pickCamera(cams, 'Webcam')!.deviceId).toBe('a')
  expect(pickCamera(cams, 'gone')!.deviceId).toBe('b')
  expect(pickCamera([], '')).toBeNull()
  expect(cameraError({ name: 'NotAllowedError' }, true)).toContain('verweigert')
})
