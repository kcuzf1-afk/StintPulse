import { useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import {
  formatDelta,
  formatLapTime,
  formatSectorTime,
  hudFraction,
  surname,
  hudOffset,
  type HudModel,
  type HudPrefs,
  type Rect,
} from './hud'

const BASE_WIDTH = 236

/**
 * Lap-timing overlay: DOM layer above the onboard picture (never burnt into
 * video frames). Only the grip takes pointer input; the rest lets clicks reach
 * the video controls underneath.
 */
export default function LapTimingHUD({
  model,
  prefs,
  rect,
  de,
  onMove,
}: {
  model: HudModel
  prefs: HudPrefs
  rect: Rect
  de: boolean
  onMove: (pos: { x: number; y: number }) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: BASE_WIDTH, h: 110 })
  const [drag, setDrag] = useState<{ left: number; top: number } | null>(null)
  const start = useRef<{ px: number; py: number; left: number; top: number } | null>(null)
  const tr = (a: string, b: string) => (de ? a : b)
  // Never wider than ~62 % of the picture (phones), never bigger than chosen.
  const scale = Math.max(0.45, Math.min(prefs.scale, (rect.w * 0.62) / BASE_WIDTH || prefs.scale))
  useLayoutEffect(() => {
    const el = box.current
    if (el) setSize({ w: el.offsetWidth * scale, h: el.offsetHeight * scale })
  }, [scale, model.sectors.length, model.status?.de, model.reference, model.mode, prefs.details])
  const placed = drag || hudOffset(rect, size.w, size.h, prefs.x, prefs.y)

  function down(e: ReactPointerEvent) {
    if (prefs.locked) return
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    start.current = { px: e.clientX, py: e.clientY, left: placed.left, top: placed.top }
    setDrag({ left: placed.left, top: placed.top })
  }
  function move(e: ReactPointerEvent) {
    if (!start.current) return
    const left = start.current.left + e.clientX - start.current.px
    const top = start.current.top + e.clientY - start.current.py
    setDrag({ left, top })
  }
  function up() {
    if (!start.current || !drag) return
    start.current = null
    onMove(hudFraction(rect, size.w, size.h, drag.left, drag.top))
    setDrag(null)
  }

  const refLabel =
    model.reference?.kind === 'selected'
      ? 'REF'
      : model.reference?.kind === 'session_best'
        ? tr('S-PB', 'S-PB')
        : 'PB'
  const refTitle =
    model.reference?.kind === 'selected'
      ? tr('Ausgewählte Referenzrunde', 'Selected reference lap')
      : model.reference?.kind === 'session_best'
        ? tr('Schnellste gültige Runde der wiedergegebenen Session', 'Fastest valid lap of the replayed session')
        : tr('Persönliche Bestzeit (gleiches Auto, Strecke, Layout)', 'Personal best (same car, track, layout)')

  const name = surname(model.driver)
  const tyre = model.compound?.label || ''
  const tyreClass = /^[SMHIW]$/.test(tyre.toUpperCase()) ? 'tyre-' + tyre.toUpperCase() : 'tyre-other'
  const timeText = formatLapTime(model.timeMs)

  return (
    <div
      ref={box}
      className={`lap-hud f1 mode-${model.mode} phase-${model.phase}${model.invalid ? ' invalid' : ''}${prefs.locked ? ' locked' : ''}${drag ? ' dragging' : ''}`}
      data-testid="lap-hud"
      role="group"
      aria-label={tr('Rundenzeit', 'Lap timing')}
      style={{
        left: placed.left,
        top: placed.top,
        opacity: prefs.opacity,
        transform: `scale(${scale})`,
        width: BASE_WIDTH,
      }}
    >
      <div className="f1-head">
        <span className="f1-pos" title={tr('Position (laut Spiel)', 'Position (from the game)')}>
          {model.position ?? '–'}
        </span>
        {model.mode !== 'live' && (
          <span className={'f1-tag ' + (model.synthetic || model.mode === 'demo' ? 'demo' : 'replay')}>
            {model.mode === 'demo' ? 'DEMO' : model.synthetic ? 'DEMO-REPLAY' : 'REPLAY'}
          </span>
        )}
        <span className="f1-name" title={model.driver || undefined}>
          {name || '—'}
        </span>
        <span
          className={'f1-tyre ' + tyreClass}
          title={
            model.compound
              ? model.compound.full +
                (model.compound.manual ? tr(' · manuell zugeordnet', ' · manually mapped') : '')
              : tr('Reifenmischung nicht verfügbar', 'Tyre compound not available')
          }
        >
          {tyre ? tyre + (model.compound?.manual ? '*' : '') : ''}
        </span>
      </div>
      <div className="f1-time" data-testid="lap-hud-time" aria-label={timeText}>
        {[...timeText].map((c, i) => (
          <span key={i} className={/[0-9]/.test(c) ? 'd' : 'p'}>
            {c}
          </span>
        ))}
      </div>
      {model.status && (
        <div className={'f1-status tone-' + model.status.tone} data-testid="lap-hud-status">
          {de ? model.status.de : model.status.en}
        </div>
      )}
      {prefs.details && (
        <div className="f1-details" data-testid="lap-hud-details">
          <span>{model.lapNumber ? tr('RUNDE ', 'LAP ') + model.lapNumber : ''}</span>
          {model.invalid && <span className="bad">{tr('UNGÜLTIG', 'INVALID')}</span>}
          {!model.invalid && model.incomplete && (
            <span className="warn" title={tr('Runde nicht ab Start/Ziel erfasst', 'Lap not captured from the line')}>
              {tr('UNVOLLST.', 'PARTIAL')}
            </span>
          )}
          {model.phase === 'completed' && model.deltaMs !== null ? (
            <span className={model.deltaMs < 0 ? 'good' : 'warn'} title={refTitle}>
              {refLabel} {formatDelta(model.deltaMs)}
            </span>
          ) : (
            <span title={refTitle}>
              {refLabel} {model.reference ? formatLapTime(model.reference.ms) : '—'}
            </span>
          )}
          {model.compound && !model.compound.label && (
            <span title={model.compound.full}>{model.compound.full}</span>
          )}
        </div>
      )}
      {model.sectorsAvailable ? (
        <div className="f1-sectors" style={{ gridTemplateColumns: `repeat(${model.sectors.length}, 1fr)` }}>
          {model.sectors.map((s) => (
            <div
              key={s.label}
              className={'f1-sector ' + s.color}
              data-color={s.color}
              title={s.ms !== null ? `${s.label} ${formatSectorTime(s.ms)}` : s.label}
            >
              <span>{s.label}</span>
              {prefs.details && <b>{s.ms !== null ? formatSectorTime(s.ms) : ''}</b>}
            </div>
          ))}
        </div>
      ) : (
        <div className="f1-sectors none">{tr('SEKTOREN N. V.', 'NO SECTORS')}</div>
      )}
      {!prefs.locked && (
        <button
          type="button"
          className="lap-hud-grip"
          aria-label={tr('Rundenzeit-HUD verschieben', 'Move lap timing HUD')}
          title={tr('Ziehen zum Verschieben', 'Drag to move')}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
        >
          ⠿
        </button>
      )}
    </div>
  )
}
