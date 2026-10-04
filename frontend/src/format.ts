export const available = (v: number | null | undefined) =>
  v !== null && v !== undefined && Number.isFinite(v)
export function value(v: number | null | undefined, dec = 0): string {
  return available(v) ? v!.toFixed(dec) : '—'
}
export function lapTime(ms: number | null | undefined, allowZero = false) {
  if (!available(ms) || ms! < 0 || (ms === 0 && !allowZero)) return '—:——.———'
  const total = Math.round(ms!)
  return `${Math.floor(total / 60000)}:${String(Math.floor(total / 1000) % 60).padStart(2, '0')}.${String(total % 1000).padStart(3, '0')}`
}
export function delta(v: number | null | undefined, dec = 3) {
  return available(v) ? `${v! > 0 ? '+' : ''}${v!.toFixed(dec)}` : '—'
}
export function speed(v: number | null | undefined, imperial = false) {
  return value(available(v) ? v! * (imperial ? 0.621371 : 1) : null)
}
export function tyreStatus(temp: number | null | undefined, cold: number, hot: number) {
  return !available(temp) ? 'unavailable' : temp! < cold ? 'cold' : temp! > hot ? 'hot' : 'in_range'
}
export function interpolate(d: number[], v: Array<number | null>, x: number): number | null {
  if (!d.length || x < d[0] || x > d[d.length - 1]) return null
  let lo = 0,
    hi = d.length - 1
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1
    if (d[m] <= x) lo = m
    else hi = m
  }
  if (v[lo] === null || v[hi] === null) return null
  if (d[hi] === d[lo]) return v[lo]
  return v[lo]! + ((v[hi]! - v[lo]!) * (x - d[lo])) / (d[hi] - d[lo])
}

/* ---------- Units: stored values are km/h, °C, psi, L; shown per setting ---------- */
export type Units = 'metric' | 'imperial'
export const units = {
  speed: (u: Units) => (u === 'imperial' ? 'mph' : 'km/h'),
  temp: (u: Units) => (u === 'imperial' ? '°F' : '°C'),
  pressure: (u: Units) => (u === 'imperial' ? 'psi' : 'bar'),
  fuel: (u: Units) => (u === 'imperial' ? 'gal' : 'L'),
}
const nn = (v: number | null | undefined): v is number => available(v)
export const toTemp = (c: number | null | undefined, u: Units) =>
  nn(c) ? (u === 'imperial' ? c * 1.8 + 32 : c) : null
export const fromTemp = (v: number, u: Units) => (u === 'imperial' ? (v - 32) / 1.8 : v)
export const toPressure = (psi: number | null | undefined, u: Units) =>
  nn(psi) ? (u === 'imperial' ? psi : psi * 0.0689476) : null
export const fromPressure = (v: number, u: Units) => (u === 'imperial' ? v : v / 0.0689476)
export const toFuel = (l: number | null | undefined, u: Units) =>
  nn(l) ? (u === 'imperial' ? l * 0.264172 : l) : null
