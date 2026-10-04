import { useEffect, useRef, useMemo } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
  MarkAreaComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Sample, Compare, Settings } from './types'
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
  MarkAreaComponent,
  MarkLineComponent,
  CanvasRenderer,
])

export interface ChannelInfo {
  label: string
  de: string
  unit: string
  color: string
  range?: number[]
}
export const CHANNELS: Record<string, ChannelInfo> = {
  speed: { label: 'Speed', de: 'Geschwindigkeit', unit: 'km/h', color: '#ffcc57' },
  gas: { label: 'Throttle', de: 'Gas', unit: '%', color: '#60e7b0', range: [0, 100] },
  brake: { label: 'Brake', de: 'Bremse', unit: '%', color: '#ff6977', range: [0, 100] },
  gear: { label: 'Gear', de: 'Gang', unit: '', color: '#62d9ef', range: [-1, 8] },
  steer: { label: 'Steering input', de: 'Lenkung (roh)', unit: 'raw', color: '#a1a9ff' },
  rpm: { label: 'RPM', de: 'Drehzahl', unit: 'U/min', color: '#ff964b' },
  clutch: { label: 'Clutch', de: 'Kupplung', unit: '%', color: '#d5a9ff', range: [0, 100] },
  g_lat: { label: 'Lateral G', de: 'Querbeschleunigung', unit: 'g', color: '#62d9ef' },
  g_long: { label: 'Longitudinal G', de: 'Längsbeschleunigung', unit: 'g', color: '#ff964b' },
  g_vert: { label: 'Vertical G', de: 'Vertikalbeschleunigung', unit: 'g', color: '#a1a9ff' },
  fuel: { label: 'Fuel', de: 'Kraftstoff', unit: 'L', color: '#ffcc57' },
  ride_front: { label: 'Ride height front', de: 'Bodenfreiheit vorn', unit: 'm', color: '#62d9ef' },
  ride_rear: { label: 'Ride height rear', de: 'Bodenfreiheit hinten', unit: 'm', color: '#ff964b' },
  delta: { label: 'Delta', de: 'Delta', unit: 's', color: '#ff964b' },
  line_difference_m: { label: 'Line difference', de: 'Linienabstand', unit: 'm', color: '#62d9ef' },
}
for (const c of ['FL', 'FR', 'RL', 'RR'])
  for (const [key, label, de, unit] of [
    ['core', 'Tyre core', 'Reifen Kern', '°C'],
    ['inner', 'Tyre inner', 'Reifen innen', '°C'],
    ['middle', 'Tyre middle', 'Reifen Mitte', '°C'],
    ['outer', 'Tyre outer', 'Reifen außen', '°C'],
    ['pressure', 'Tyre pressure', 'Reifendruck', 'psi'],
    ['wear_raw', 'Tyre wear (raw)', 'Reifenverschleiß (roh)', 'raw'],
    ['slip_raw', 'Wheel slip (raw)', 'Radschlupf (roh)', 'raw'],
    ['angular_speed', 'Wheel speed', 'Raddrehzahl', 'rad/s'],
    ['load', 'Wheel load', 'Radlast', 'N'],
    ['travel', 'Suspension travel', 'Federweg', 'm'],
    ['brake_temp', 'Brake temperature', 'Bremsentemperatur', '°C'],
  ])
    CHANNELS[`${key}_${c}`] = { label: `${label} ${c}`, de: `${de} ${c}`, unit, color: '#62d9ef' }

export const channelLabel = (key: string, de: boolean) =>
  CHANNELS[key] ? (de ? CHANNELS[key].de : CHANNELS[key].label) : key

/** Shown unit and value per the units setting (stored: km/h, °C, psi, L). */
export function channelUnit(key: string, imperial: boolean) {
  const unit = CHANNELS[key]?.unit || ''
  if (key === 'speed') return imperial ? 'mph' : 'km/h'
  if (unit === '°C') return imperial ? '°F' : '°C'
  if (unit === 'psi') return imperial ? 'psi' : 'bar'
  if (key === 'fuel') return imperial ? 'gal' : 'L'
  return unit
}
export function channelValue(key: string, v: number | null | undefined, imperial: boolean) {
  if (v === null || v === undefined || !Number.isFinite(v)) return null
  if (['gas', 'brake', 'clutch'].includes(key)) return v * 100
  const unit = CHANNELS[key]?.unit
  if (key === 'speed') return imperial ? v * 0.621371 : v
  if (unit === '°C') return imperial ? v * 1.8 + 32 : v
  if (unit === 'psi') return imperial ? v : v * 0.0689476
  if (key === 'fuel') return imperial ? v * 0.264172 : v
  return v
}

/** Analysis colours: comparison lap solid orange, reference dashed cyan. */
export const COMPARE_COLOR = '#ff964b'
export const REFERENCE_COLOR = '#62d9ef'

interface Props {
  history: Sample[]
  comparison: Compare[]
  channels: string[]
  settings: Settings | null
  analysis: boolean
  cursor?: (distance: number) => void
  /** Cursor set elsewhere (map, table): drawn as a vertical line. */
  cursorAt?: number | null
  range: [number, number] | null
  height?: number
  de?: boolean
  labels?: { comparison: string; reference: string }
}
export default function TelemetryChart(props: Props) {
  const element = useRef<HTMLDivElement>(null),
    chart = useRef<echarts.EChartsType | null>(null),
    latest = useRef(props)
  latest.current = props
  const axisSignature = useMemo(() => props.channels.join(','), [props.channels])
  useEffect(() => {
    if (!element.current) return
    const c = echarts.init(element.current, undefined, { renderer: 'canvas' })
    chart.current = c
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => c.resize()) : null
    observer?.observe(element.current)
    let frame = 0,
      last = 0,
      priorMode = '',
      priorSignature = ''
    const draw = (now: number) => {
      if (now - last > 200) {
        last = now
        const p = latest.current,
          de = p.de !== false,
          rows = p.channels.length,
          height = element.current!.clientHeight
        const gap = 18,
          bottom = p.analysis ? 40 : 24,
          top = 16,
          rowHeight = Math.max(22, (height - top - bottom - (rows - 1) * gap) / rows)
        const grids = p.channels.map((_, i) => ({
          left: 58,
          right: 18,
          top: top + i * (rowHeight + gap),
          height: rowHeight,
          containLabel: false,
        }))
        const series: Record<string, unknown>[] = []
        const imperial = p.settings?.units === 'imperial'
        const label = (key: string) => channelLabel(key, de)
        p.channels.forEach((key, index) => {
          if (p.analysis && p.comparison.length) {
            const comp = p.comparison[0]
            const values =
              key === 'delta'
                ? comp.delta
                : key === 'line_difference_m'
                  ? comp.line_difference_m
                  : comp.lap.channels[key] || []
            const marks: Record<string, unknown> = {}
            if (p.range)
              marks.markArea = {
                silent: true,
                itemStyle: { color: '#ff964b14' },
                data: [[{ xAxis: p.range[0] }, { xAxis: p.range[1] }]],
              }
            if (p.cursorAt !== null && p.cursorAt !== undefined)
              marks.markLine = {
                silent: true,
                symbol: 'none',
                label: { show: false },
                lineStyle: { color: '#ecf1f8', type: 'solid', width: 1, opacity: 0.6 },
                data: [{ xAxis: p.cursorAt }],
              }
            series.push({
              name: `${p.labels?.comparison || (de ? 'Vergleich' : 'Comparison')} · ${label(key)}`,
              type: 'line',
              xAxisIndex: index,
              yAxisIndex: index,
              data: comp.distance.map((d, i) => [d, channelValue(key, values[i] ?? null, imperial)]),
              showSymbol: false,
              connectNulls: false,
              step: key === 'gear' ? 'end' : false,
              lineStyle: { color: COMPARE_COLOR, width: 2 },
              itemStyle: { color: COMPARE_COLOR },
              animation: false,
              ...marks,
            })
            const vals = comp.reference.channels[key]
            if (vals && key !== 'delta' && key !== 'line_difference_m')
              series.push({
                name: `${p.labels?.reference || (de ? 'Referenz' : 'Reference')} · ${label(key)}`,
                type: 'line',
                xAxisIndex: index,
                yAxisIndex: index,
                data: comp.distance.map((d, i) => [d, channelValue(key, vals[i] ?? null, imperial)]),
                showSymbol: false,
                connectNulls: false,
                lineStyle: { color: REFERENCE_COLOR, type: 'dashed', width: 1.6 },
                itemStyle: { color: REFERENCE_COLOR },
                step: key === 'gear' ? 'end' : false,
                animation: false,
              })
          } else {
            const color = p.settings?.graph_colors?.[key] || CHANNELS[key]?.color || '#62d9ef'
            const newest = p.history.at(-1)?.captured_at || 0
            const h = p.history.filter((s) => s.captured_at >= newest - 30)
            const origin = h.length ? h[0].captured_at : 0
            series.push({
              name: label(key),
              type: 'line',
              xAxisIndex: index,
              yAxisIndex: index,
              data: h.map((s) => [s.captured_at - origin, channelValue(key, s.channels[key] ?? null, imperial)]),
              showSymbol: false,
              connectNulls: false,
              lineStyle: { color, width: 2 },
              areaStyle: { color, opacity: 0.04 },
              step: key === 'gear' ? 'end' : false,
              animation: false,
            })
          }
        })
        const signature = p.channels.join(',') + (de ? 'de' : 'en') + (imperial ? 'i' : 'm')
        const mode = p.analysis ? 'analysis' : 'live'
        const option = {
          animation: false,
          backgroundColor: 'transparent',
          grid: grids,
          xAxis: p.channels.map((_, i) => ({
            type: 'value',
            gridIndex: i,
            min: p.analysis ? 0 : undefined,
            // The axis ends at the lap distance, not at the next round number.
            max: p.analysis && p.comparison.length ? p.comparison[0].distance.at(-1) : undefined,
            axisLine: { lineStyle: { color: '#3a536b' } },
            axisTick: { show: false },
            axisLabel: {
              show: i === rows - 1,
              color: '#9db0c4',
              fontSize: 11,
              formatter: (v: number) => `${Math.round(v)}${p.analysis ? ' m' : ' s'}`,
            },
            splitLine: { lineStyle: { color: '#2a4157', type: 'dashed', opacity: 0.5 } },
            axisPointer: { show: true },
          })),
          yAxis: p.channels.map((key, i) => ({
            type: 'value',
            gridIndex: i,
            min: CHANNELS[key]?.range?.[0],
            max: CHANNELS[key]?.range?.[1],
            scale: true,
            name: `${label(key)}${channelUnit(key, imperial) ? ' · ' + channelUnit(key, imperial) : ''}`,
            nameLocation: 'end',
            nameGap: 6,
            nameTextStyle: { color: '#9db0c4', fontSize: 11, align: 'left' },
            axisLabel: { color: '#9db0c4', fontSize: 11 },
            splitNumber: 2,
            splitLine: { lineStyle: { color: '#2a4157', opacity: 0.5 } },
            axisLine: { show: false },
            axisTick: { show: false },
          })),
          axisPointer: {
            link: [{ xAxisIndex: 'all' }],
            lineStyle: { color: '#ff964b', type: 'dashed' },
          },
          tooltip: {
            trigger: 'axis',
            backgroundColor: '#0e1928f5',
            borderColor: '#31495f',
            textStyle: { color: '#ecf1f8', fontSize: 12 },
            confine: true,
            valueFormatter: (v: number) => (typeof v === 'number' ? v.toFixed(2) : '—'),
          },
          dataZoom: [
            { type: 'inside', xAxisIndex: p.channels.map((_, i) => i), filterMode: 'none' },
            {
              type: 'slider',
              xAxisIndex: p.channels.map((_, i) => i),
              bottom: 2,
              height: 16,
              borderColor: '#23364a',
              backgroundColor: '#0b1522',
              fillerColor: '#62d9ef15',
              handleStyle: { color: '#62d9ef' },
              textStyle: { color: '#9db0c4' },
              show: p.analysis,
            },
          ],
          series,
        }
        const reset = priorMode !== mode || priorSignature !== signature
        c.setOption(option, {
          notMerge: reset,
          lazyUpdate: true,
          replaceMerge: reset ? undefined : ['series'],
        })
        priorMode = mode
        priorSignature = signature
      }
      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    c.on('updateAxisPointer', (event: unknown) => {
      const ev = event as { axesInfo?: Array<{ value: number }> }
      const v = ev.axesInfo?.[0]?.value
      if (v !== undefined && latest.current.analysis) latest.current.cursor?.(v)
    })
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      c.dispose()
      chart.current = null
    }
  }, [])
  useEffect(() => {
    chart.current?.resize()
  }, [axisSignature, props.height])
  return (
    <div
      className="telemetry-chart"
      ref={element}
      role="img"
      aria-label={props.de === false ? 'Synchronised telemetry charts' : 'Synchronisierte Telemetriediagramme'}
      style={{ height: props.height ?? Math.max(240, props.channels.length * 80) }}
    />
  )
}
