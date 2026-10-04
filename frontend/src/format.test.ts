import { describe, it, expect } from 'vitest'
import { lapTime, interpolate, tyreStatus, value, speed, delta } from './format'
describe('measured data formatting', () => {
  it('preserves unavailable data', () => {
    expect(value(null)).toBe('—')
    expect(value(NaN)).toBe('—')
    expect(lapTime(null)).toBe('—:——.———')
  })
  it('rounds lap times with carry', () => {
    expect(lapTime(59999.8)).toBe('1:00.000')
    expect(lapTime(91823)).toBe('1:31.823')
    expect(lapTime(0, true)).toBe('0:00.000')
    expect(lapTime(0)).toBe('—:——.———')
  })
  it('converts speed without fake defaults', () => {
    expect(speed(100, true)).toBe('62')
    expect(speed(null, true)).toBe('—')
    expect(delta(0.15)).toBe('+0.150')
  })
  it('interpolates distance and preserves gaps', () => {
    expect(interpolate([0, 10, 20], [0, 2, 5], 5)).toBe(1)
    expect(interpolate([0, 10], [null, 2], 5)).toBeNull()
    expect(interpolate([0, 10], [1, 2], 15)).toBeNull()
  })
  it('uses configured tyre thresholds', () => {
    expect(tyreStatus(110, 70, 105)).toBe('hot')
    expect(tyreStatus(110, 70, 115)).toBe('in_range')
    expect(tyreStatus(null, 70, 105)).toBe('unavailable')
  })
})
