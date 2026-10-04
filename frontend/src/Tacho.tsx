import type { Sample } from './types'
import { speed, value } from './format'

/** Speed, gear and pedals (the lap time is already in the HUD). */
export default function Tacho({ sample, imperial, de }: { sample: Sample; imperial: boolean; de: boolean }) {
  const gear = sample.channels.gear
  return (
    <div className="onboard-overlay" data-testid="tacho">
      {sample.source === 'demo' && <small className="orange-text">DEMO</small>}
      <b>
        {speed(sample.channels.speed, imperial)} <small>{imperial ? 'mph' : 'km/h'}</small>
      </b>
      <b className="orange-text" data-testid="tacho-gear">
        {gear === 0 ? 'N' : gear === -1 ? 'R' : value(gear)}
      </b>
      <span className="tacho-pedals" aria-label={de ? 'Pedale' : 'Pedals'}>
        <i className="green" style={{ height: Math.round((sample.channels.gas || 0) * 100) + '%' }} />
        <i className="red" style={{ height: Math.round((sample.channels.brake || 0) * 100) + '%' }} />
      </span>
    </div>
  )
}
