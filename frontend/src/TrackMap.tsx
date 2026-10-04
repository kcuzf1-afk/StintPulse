import { useMemo } from 'react'
import type { Track, Compare, Sample } from './types'
import { interpolate } from './format'

interface Props {
  track: Track | null
  sample: Sample | null
  ghost: number | null
  ghostCoords?: [number, number] | null
  comparison?: Compare
  /** True when the comparison lap is a complete lap (closable at S/F). */
  comparisonComplete?: boolean
  cursor?: number
  click?: (m: number) => void
  layers: string[]
  liveCoords?: boolean
  de?: boolean
}

type Point = [number, number, number]

/**
 * A recorded lap ends a few metres before the line it started on. The gap is
 * closed ONLY for a complete lap and only when it is no larger than the normal
 * point spacing – i.e. the car really drove it. Anything else stays open and
 * is labelled as still being recorded (no artificial straight line).
 */
export function closesAtLine(points: Point[], complete: boolean): boolean {
  if (!complete || points.length < 20) return false
  const step: number[] = []
  for (let i = 1; i < points.length; i++)
    step.push(Math.hypot(points[i][1] - points[i - 1][1], points[i][2] - points[i - 1][2]))
  step.sort((a, b) => a - b)
  const median = step[Math.floor(step.length / 2)] || 0
  const first = points[0],
    last = points[points.length - 1]
  const gap = Math.hypot(last[1] - first[1], last[2] - first[2])
  const coverage = last[0] - first[0]
  return coverage > 0.95 && gap <= Math.max(25, median * 4)
}

/**
 * Splits the trace where the car has not driven yet (provisional map) or where
 * the recording has a hole, so no straight shortcut is drawn across the infield.
 */
export function traceSegments(points: Point[]): Point[][] {
  if (points.length < 2) return points.length ? [points] : []
  const step: number[] = []
  for (let i = 1; i < points.length; i++)
    step.push(Math.hypot(points[i][1] - points[i - 1][1], points[i][2] - points[i - 1][2]))
  const median = [...step].sort((a, b) => a - b)[Math.floor(step.length / 2)] || 0
  const out: Point[][] = [[points[0]]]
  for (let i = 1; i < points.length; i++) {
    if (points[i][0] - points[i - 1][0] > 0.012 || step[i - 1] > Math.max(30, median * 5)) out.push([])
    out[out.length - 1].push(points[i])
  }
  return out
}

const W = 600,
  H = 340,
  PAD = 34

export default function TrackMap({
  track,
  sample,
  ghost,
  ghostCoords,
  comparison,
  comparisonComplete = false,
  cursor,
  click,
  layers,
  liveCoords = true,
  de = true,
}: Props) {
  const tr = (a: string, b: string) => (de ? a : b)
  const geometry = useMemo(() => {
    let points: Point[] = track?.points || []
    if (comparison)
      points = comparison.distance.flatMap((d, i) =>
        comparison.map.x[i] === null || comparison.map.z[i] === null
          ? []
          : [[d / (comparison.distance.at(-1) || 1), comparison.map.x[i]!, comparison.map.z[i]!] as Point],
      )
    if (points.length < 2) return null
    const xs = points.map((p) => p[1]),
      zs = points.map((p) => p[2])
    const minX = Math.min(...xs),
      maxX = Math.max(...xs),
      minZ = Math.min(...zs),
      maxZ = Math.max(...zs)
    // Whole geometry with a margin that leaves room for markers and labels.
    const scale = Math.min((W - 2 * PAD) / Math.max(1, maxX - minX), (H - 2 * PAD) / Math.max(1, maxZ - minZ))
    const tx = (x: number) => W / 2 + (x - (maxX + minX) / 2) * scale,
      tz = (z: number) => H / 2 + (z - (maxZ + minZ) / 2) * scale
    const ds = points.map((p) => p[0])
    const pos = (u: number) => [tx(interpolate(ds, xs, u) ?? xs[0]), tz(interpolate(ds, zs, u) ?? zs[0])]
    const complete = comparison ? comparisonComplete : !!track?.complete
    return { points, tx, tz, pos, closed: closesAtLine(points, complete), complete }
  }, [track, comparison, comparisonComplete])

  if (!geometry)
    return (
      <div className="map-empty" data-testid="map-empty">
        <strong>{tr('Strecke wird aufgezeichnet', 'Recording track')}</strong>
        <p>
          {sample
            ? tr('Die Karte entsteht aus den gefahrenen Positionen.', 'The map is built from driven positions.')
            : tr('Noch keine Positionsdaten. Fahrt starten oder Demo aktivieren.', 'No position data yet. Start driving or enable the demo.')}
        </p>
      </div>
    )
  const { points, tx, tz, pos, closed, complete } = geometry
  const length = comparison?.distance.at(-1) || track?.length_m || 4200
  const cursorX = cursor !== undefined && comparison ? interpolate(comparison.distance, comparison.map.x, cursor) : null
  const cursorZ = cursor !== undefined && comparison ? interpolate(comparison.distance, comparison.map.z, cursor) : null
  const vehicle =
    cursor !== undefined && comparison
      ? cursorX === null || cursorZ === null
        ? null
        : [tx(cursorX), tz(cursorZ)]
      : sample && liveCoords
        ? [tx(sample.coords[0]), tz(sample.coords[2])]
        : sample
          ? pos(sample.lap_pos)
          : null
  let ref = ghostCoords ? [tx(ghostCoords[0]), tz(ghostCoords[1])] : ghost !== null ? pos(ghost) : null
  if (comparison && cursor !== undefined && comparison.reference_map) {
    const elapsed = interpolate(comparison.distance, comparison.lap.time, cursor)
    const indices = comparison.reference.time.flatMap((t, i) => (t === null ? [] : [i]))
    const times = indices.map((i) => comparison.reference.time[i]!),
      dist = indices.map((i) => comparison.distance[i])
    const refDistance = elapsed === null ? null : interpolate(times, dist, Math.min(elapsed, times.at(-1) || 0))
    const x = refDistance === null ? null : interpolate(comparison.distance, comparison.reference_map.x, refDistance)
    const z = refDistance === null ? null : interpolate(comparison.distance, comparison.reference_map.z, refDistance)
    ref = x === null || z === null ? null : [tx(x), tz(z)]
  }
  const events = comparison?.events || track?.events || []
  const segments = traceSegments(points)
  if (closed) segments[segments.length - 1] = [...segments[segments.length - 1], points[0]]
  const paths = segments.map((seg) => seg.map((p) => `${tx(p[1])},${tz(p[2])}`).join(' '))
  const heat = !!comparison && layers.includes('heatmap')
  const color = (u: number) => {
    if (!comparison) return '#62d9ef'
    const idx = Math.max(1, Math.round(u * (comparison.delta.length - 1)))
    if (comparison.delta[idx] === null || comparison.delta[idx - 1] === null) return '#52677d'
    const diff = comparison.delta[idx]! - comparison.delta[idx - 1]!
    return diff < -0.002 ? '#60e7b0' : diff < 0.003 ? '#ffcc57' : diff < 0.008 ? '#ff964b' : '#ff6977'
  }
  const sectorPositions = track?.sector_positions || []
  const [sfX, sfY] = pos(0)
  return (
    <div className="map-container" data-closed={closed ? 'true' : 'false'}>
      <svg viewBox={`0 0 ${W} ${H}`} aria-label={tr('Streckenkarte', 'Track map')} role="img">
        {paths.map((path, i) => (
          <g key={'base' + i}>
            <polyline points={path} fill="none" stroke="#62d9ef14" strokeWidth="16" strokeLinecap="round" strokeLinejoin="round" />
            <polyline points={path} fill="none" stroke="#37516a" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
          </g>
        ))}
        {comparison?.reference_map && (
          <polyline
            points={comparison.distance
              .flatMap((_, i) =>
                comparison.reference_map!.x[i] === null || comparison.reference_map!.z[i] === null
                  ? []
                  : [`${tx(comparison.reference_map!.x[i]!)},${tz(comparison.reference_map!.z[i]!)}`],
              )
              .join(' ')}
            fill="none"
            stroke="#62d9ef"
            strokeWidth="1.4"
            strokeDasharray="3 3"
            opacity=".7"
          />
        )}
        {segments.flatMap((seg, k) =>
          seg.slice(1).map((p, i) => (
          <line
            key={k + '-' + i}
            x1={tx(seg[i][1])}
            y1={tz(seg[i][2])}
            x2={tx(p[1])}
            y2={tz(p[2])}
            stroke={heat ? color(p[0]) : '#62d9ef'}
            strokeWidth="2.6"
            strokeLinecap="round"
            onClick={() => click?.(p[0] * length)}
          />
          )),
        )}
        {sectorPositions.map((u, i) => {
          const [x, y] = pos(u)
          return (
            <g key={i} className="map-sector">
              <circle cx={x} cy={y} r="4.5" fill="#09111c" stroke="#ecf1f8" strokeWidth="1.5" />
              <text x={x + 8} y={y - 7} fill="#ecf1f8" fontSize="11" fontWeight="700">
                S{i + 2}
              </text>
            </g>
          )
        })}
        {events
          .filter((e) => e.type === 'apex' && layers.includes('corners'))
          .map((e, i) => {
            const [x, y] = pos(e.distance_m / length)
            return (
              <g key={'apex' + i} onClick={() => click?.(e.distance_m)} className="map-marker">
                <circle cx={x} cy={y} r="9" fill="#142338" stroke="#ffcc57" />
                <text x={x} y={y + 3.5} fill="#ffcc57" fontSize="9" textAnchor="middle">
                  {e.corner}
                </text>
              </g>
            )
          })}
        {events
          .filter((e) => layers.includes(e.type))
          .map((e, i) => {
            const [x, y] = pos(e.distance_m / length)
            return (
              <circle
                key={'event' + i}
                cx={x}
                cy={y}
                r="4"
                fill={e.type === 'braking_zone' ? '#ff6977' : e.type === 'full_throttle' ? '#60e7b0' : e.type === 'shift' ? '#a1a9ff' : '#ff964b'}
                onClick={() => click?.(e.distance_m)}
              >
                <title>
                  {e.type} · {Math.round(e.distance_m)} m{e.estimated ? tr(' · Schätzung', ' · estimate') : ''}
                </title>
              </circle>
            )
          })}
        <g className="map-start" data-testid="map-start">
          <rect x={sfX - 3} y={sfY - 11} width="6" height="22" fill="#ecf1f8" />
          <rect x={sfX - 3} y={sfY - 11} width="3" height="5.5" fill="#09111c" />
          <rect x={sfX} y={sfY - 5.5} width="3" height="5.5" fill="#09111c" />
          <rect x={sfX - 3} y={sfY} width="3" height="5.5" fill="#09111c" />
          <rect x={sfX} y={sfY + 5.5} width="3" height="5.5" fill="#09111c" />
          <text x={sfX + 8} y={sfY + 4} fill="#ecf1f8" fontSize="11" fontWeight="700">
            {tr('Start/Ziel', 'Start/Finish')}
          </text>
        </g>
        {ref && <circle cx={ref[0]} cy={ref[1]} r="6" fill="#09111c" stroke="#62d9ef" strokeWidth="2" />}
        {vehicle && (
          <g data-testid="map-vehicle">
            <circle cx={vehicle[0]} cy={vehicle[1]} r="13" fill="#ff964b26" />
            <circle cx={vehicle[0]} cy={vehicle[1]} r="6" fill="#ff964b" stroke="#fff" strokeWidth="1.5" />
          </g>
        )}
      </svg>
      <div className="map-legend">
        <span>
          <i className="dot orange" />
          {comparison ? tr('Vergleich (Cursor)', 'Comparison (cursor)') : tr('Fahrzeug', 'Car')}
        </span>
        {(ref || comparison?.reference_map) && (
          <span>
            <i className="dot cyan" />
            {tr('Referenz', 'Reference')}
          </span>
        )}
        {heat && (
          <span className="map-heat-legend">
            <i className="dot green" />
            {tr('Gewinn', 'Gain')} <i className="dot red" />
            {tr('Verlust', 'Loss')}
          </span>
        )}
        <span className={complete && closed ? 'subtle' : 'yellow-text'} data-testid="map-state">
          {complete && closed
            ? tr('Vollständige Runde', 'Complete lap')
            : tr('Strecke wird aufgezeichnet', 'Recording track') +
              (!comparison && track?.coverage !== undefined ? ` · ${Math.floor(track.coverage * 100)} %` : '')}
        </span>
      </div>
    </div>
  )
}
