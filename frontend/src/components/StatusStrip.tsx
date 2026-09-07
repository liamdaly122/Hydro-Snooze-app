import { Card } from './Card'
import { formatWatts } from '../domain'
import type { DeviceState } from '../types'

import { STAGE_LABEL } from '../types'

const POWER_LABEL: Record<DeviceState['power'], string> = {
  on: 'On',
  off: 'Off',
  unknown: 'unknown',
}

/**
 * Shows what is known and says "unknown" for the rest. The unit cannot be read
 * over infrared, so a confident-looking value the app has not confirmed would be
 * a lie, and at 3am a lie here is worse than a blank.
 */
export function StatusStrip({
  state,
  onMute,
}: {
  state: DeviceState
  onMute?: () => void
}) {
  const watts = formatWatts(state.observed_power_w)
  const activity = state.inferred_activity === 'unknown' ? null : state.inferred_activity

  return (
    <Card label="Status">
      <div className="status">
        <Cell label="Unit" value={POWER_LABEL[state.power]} unknown={state.power === 'unknown'} />
        <Cell
          label="Stage"
          value={state.current_stage ? STAGE_LABEL[state.current_stage] : 'Not running'}
          unknown={false}
        />
        <Cell
          label="Draw"
          value={watts}
          sub={activity}
          unknown={state.observed_power_w === null}
        />
      </div>
      {state.last_error && <p className="status__error">{state.last_error}</p>}

      {/*
        A setup action, not a nightly one. The unit remembers whether it is
        muted, so this is a toggle you press once and never think about again.
      */}
      {onMute && (
        <button
          type="button"
          className="status__link"
          disabled={state.power !== 'on'}
          onClick={onMute}
        >
          Toggle the unit's beep
        </button>
      )}
    </Card>
  )
}

function Cell({
  label,
  value,
  unknown,
  sub,
}: {
  label: string
  value: string
  unknown: boolean
  sub?: string | null
}) {
  return (
    <div className="status__cell">
      <div className="status__key">{label}</div>
      <div className={`status__val${unknown ? ' status__val--unknown' : ''}`} title={value}>
        {value}
      </div>
      {sub && <div className="status__sub">{sub}</div>}
    </div>
  )
}
